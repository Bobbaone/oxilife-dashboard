import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import sqlite3
import time
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from email.message import EmailMessage
from typing import Any

from app.reports import generate_weekly_report

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", "/app/data/oxilife.db"))
TASMOTA_BASE_URL = os.getenv("TASMOTA_BASE_URL", "").rstrip("/")
TASMOTA_DISPLAY_NAME = os.getenv("TASMOTA_DISPLAY_NAME", "AtomV5").strip() or "Tasmota"
STATUS_PATH = os.getenv("TASMOTA_STATUS_PATH", "/cm?cmnd=Status%2010")
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "wasserwerte")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
ALERT_COOLDOWN_SECONDS = max(60, int(os.getenv("ALERT_COOLDOWN_SECONDS", "3600")))
WEATHER_REFRESH_SECONDS = max(300, int(os.getenv("WEATHER_REFRESH_SECONDS", "900")))
WEATHER_POSTAL_CODE = os.getenv("WEATHER_POSTAL_CODE", "").strip()
REPORT_DIR = DB_PATH.parent / "reports"
COMMANDS = {
    "1": os.getenv("FILTER_SPEED_COMMAND_1", ""),
    "2": os.getenv("FILTER_SPEED_COMMAND_2", ""),
    "3": os.getenv("FILTER_SPEED_COMMAND_3", ""),
    "backwash": os.getenv("BACKWASH_COMMAND", ""),
}
latest: dict[str, Any] = {"online": False, "updated_at": None, "raw": {}, "error": "Noch keine Daten empfangen"}
poll_lock = asyncio.Lock()
weather_lock = asyncio.Lock()
weather_cache: dict[str, Any] = {"fetched_at": 0, "data": None, "error": None}
last_report_week: tuple[int, int] | None = None


class DatapointUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="", max_length=40)
    visible: bool = False
    sort_order: int = Field(default=0, ge=-100000, le=100000)
    logging: bool = True
    chart: bool = False
    widget_type: str = Field(default="value", pattern="^(value|gauge|status|text|levels)$")
    scale: float = Field(default=1.0, ge=-1000000, le=1000000, allow_inf_nan=False)
    decimals: int = Field(default=2, ge=0, le=8)
    min_value: float | None = Field(default=None, allow_inf_nan=False)
    max_value: float | None = Field(default=None, allow_inf_nan=False)
    warning_low: float | None = Field(default=None, allow_inf_nan=False)
    warning_high: float | None = Field(default=None, allow_inf_nan=False)
    alert_low: bool = False


class PasswordChange(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[^\s]+$")
    password: str = Field(min_length=10, max_length=200)


class WeatherUpdate(BaseModel):
    postal_code: str = Field(pattern=r"^\d{5}$")


class ConnectionUpdate(BaseModel):
    tasmota_name: str = Field(min_length=1, max_length=80)
    oxilife_datapoint_id: int | None = Field(default=None, ge=1)
    oxilife_timeout_seconds: int = Field(default=30, ge=10, le=3600)


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with db() as conn:
        # The legacy table is deliberately retained for installations upgrading in place.
        conn.execute("""CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, ph REAL, rx REAL,
            temperature REAL, hydrolysis REAL, filter_speed TEXT, filter_start TEXT,
            filter_end TEXT, online INTEGER NOT NULL, raw_json TEXT NOT NULL)""")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS datapoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                data_type TEXT NOT NULL DEFAULT 'text',
                unit TEXT NOT NULL DEFAULT '',
                visible INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                logging INTEGER NOT NULL DEFAULT 1,
                chart INTEGER NOT NULL DEFAULT 0,
                widget_type TEXT NOT NULL DEFAULT 'value',
                scale REAL NOT NULL DEFAULT 1.0,
                decimals INTEGER NOT NULL DEFAULT 2,
                min_value REAL,
                max_value REAL,
                warning_low REAL,
                warning_high REAL,
                last_value_text TEXT,
                last_value_num REAL,
                last_seen INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datapoint_id INTEGER NOT NULL REFERENCES datapoints(id) ON DELETE CASCADE,
                ts INTEGER NOT NULL,
                value_text TEXT,
                value_num REAL
            );
            CREATE TABLE IF NOT EXISTS poll_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                online INTEGER NOT NULL, raw_json TEXT NOT NULL, error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_readings_point_ts ON readings(datapoint_id, ts);
            CREATE INDEX IF NOT EXISTS idx_poll_events_ts ON poll_events(ts);
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(datapoints)")}
        if "alert_low" not in columns:
            conn.execute("ALTER TABLE datapoints ADD COLUMN alert_low INTEGER NOT NULL DEFAULT 0")
        if "alert_active" not in columns:
            conn.execute("ALTER TABLE datapoints ADD COLUMN alert_active INTEGER NOT NULL DEFAULT 0")
        if "last_alert_at" not in columns:
            conn.execute("ALTER TABLE datapoints ADD COLUMN last_alert_at INTEGER")
        if "auto_configured" not in columns:
            conn.execute("ALTER TABLE datapoints ADD COLUMN auto_configured INTEGER NOT NULL DEFAULT 0")
        initial_hash = hash_password(ADMIN_PASSWORD)
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('admin_username',?)", (ADMIN_USER,))
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('admin_password_hash',?)", (initial_hash,))
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('password_change_required','1')")
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('session_token',?)", (secrets.token_urlsafe(32),))
        if setting(conn, "filtration_names_v2") != "1":
            conn.execute("UPDATE datapoints SET name='Betriebsart Filter',unit='',updated_at=? WHERE lower(path) LIKE '%neopool.filtration.mode'",
                         (int(time.time()),))
            conn.execute("UPDATE datapoints SET name='Filterpumpe',unit='',widget_type='status',decimals=0,updated_at=? WHERE lower(path) LIKE '%neopool.filtration.state'",
                         (int(time.time()),))
            conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('filtration_names_v2','1')")
        # Upgrade only untouched legacy rows. Explicit admin choices remain authoritative.
        for row in conn.execute("SELECT * FROM datapoints").fetchall():
            legacy_name = row["path"].replace(".", " › ").replace("_", " ")
            untouched = (row["name"] == legacy_name and row["unit"] == "" and row["widget_type"] == "value"
                         and row["scale"] == 1.0 and row["decimals"] == 2)
            if untouched:
                defaults = datapoint_defaults(row["path"], row["data_type"], row["last_value_num"])
                conn.execute("""UPDATE datapoints SET name=?,unit=?,widget_type=?,scale=?,decimals=?,updated_at=?
                                WHERE id=?""",
                             (defaults["name"], defaults["unit"], defaults["widget_type"],
                              defaults["scale"], defaults["decimals"], int(time.time()), row["id"]))
                conn.execute("UPDATE datapoints SET auto_configured=1 WHERE id=?", (row["id"],))
            elif row["name"] in {"State", "Mode", "Speed", "Conductivity"} and "neopool" in row["path"].lower():
                defaults = datapoint_defaults(row["path"], row["data_type"], row["last_value_num"])
                conn.execute("""UPDATE datapoints SET name=?,unit=?,widget_type=?,scale=?,decimals=?,updated_at=?
                                WHERE id=?""",
                             (defaults["name"], defaults["unit"], defaults["widget_type"],
                              defaults["scale"], defaults["decimals"], int(time.time()), row["id"]))
        # Add safe pool defaults once where no limits exist yet. Existing limits remain authoritative.
        if setting(conn, "quality_limits_v1") != "1":
            for row in conn.execute("""SELECT * FROM datapoints WHERE min_value IS NULL AND max_value IS NULL
                                     AND warning_low IS NULL AND warning_high IS NULL""").fetchall():
                limits = datapoint_quality_defaults(row["path"])
                if limits:
                    conn.execute("""UPDATE datapoints SET min_value=?,max_value=?,warning_low=?,warning_high=?,
                                    alert_low=?,updated_at=? WHERE id=?""",
                                 (limits["min_value"], limits["max_value"], limits["warning_low"],
                                  limits["warning_high"], int(limits.get("alert_low", False)),
                                  int(time.time()), row["id"]))
            conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('quality_limits_v1','1')")
        if setting(conn, "binary_tank_switch_v1") != "1":
            # Remove the percentage defaults from the short-lived tank-level implementation.
            conn.execute("""UPDATE datapoints SET unit='',widget_type='status',min_value=NULL,max_value=NULL,
                            warning_low=NULL,warning_high=NULL,alert_low=0,updated_at=?
                            WHERE (lower(path) LIKE '%ph%tank%' OR lower(path) LIKE '%chlor%tank%')
                            AND min_value=10 AND max_value=100 AND warning_low=25 AND warning_high=100""",
                         (int(time.time()),))
            conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('binary_tank_switch_v1','1')")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def weather_location() -> dict[str, Any] | None:
    with db() as conn:
        postal_code = setting(conn, "weather_postal_code")
        latitude = setting(conn, "weather_latitude")
        longitude = setting(conn, "weather_longitude")
        city = setting(conn, "weather_city")
    if not all((postal_code, latitude, longitude, city)):
        return None
    return {"postal_code": postal_code, "latitude": float(latitude), "longitude": float(longitude), "city": city}


def connection_config(conn: sqlite3.Connection) -> dict[str, Any]:
    point_id = setting(conn, "oxilife_datapoint_id")
    return {
        "tasmota_name": setting(conn, "tasmota_name", TASMOTA_DISPLAY_NAME),
        "oxilife_datapoint_id": int(point_id) if point_id.isdigit() else None,
        "oxilife_timeout_seconds": int(setting(conn, "oxilife_timeout_seconds", "30")),
    }


async def resolve_postal_code(postal_code: str) -> dict[str, Any]:
    # Zippopotam is purpose-built for postal codes and avoids fuzzy city-name matching.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"https://api.zippopotam.us/de/{postal_code}")
            if response.status_code == 200:
                payload = response.json()
                places = payload.get("places", [])
                if places:
                    place = places[0]
                    return {"postal_code": postal_code, "latitude": float(place["latitude"]),
                            "longitude": float(place["longitude"]), "city": place["place name"]}
            # OpenStreetMap remains the fallback for postal codes absent from Zippopotam.
            response = await client.get("https://nominatim.openstreetmap.org/search", params={
                "postalcode": postal_code, "country": "Deutschland", "format": "jsonv2",
                "limit": 1, "addressdetails": 1,
            }, headers={"User-Agent": "Oxilife-Dashboard/1.0"})
            response.raise_for_status()
            results = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ortsauflösung fehlgeschlagen: {exc}") from exc
    item = (results or [None])[0]
    if not item:
        raise HTTPException(status_code=404, detail="Zu dieser PLZ wurde kein Ort gefunden")
    address = item.get("address", {})
    city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
    if not city:
        city = item.get("display_name", postal_code).split(",")[1].strip() if "," in item.get("display_name", "") else postal_code
    return {"postal_code": postal_code, "latitude": float(item["lat"]), "longitude": float(item["lon"]), "city": city}


def save_weather_location(location: dict[str, Any]) -> None:
    with db() as conn:
        for key, value in location.items():
            conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (f"weather_{key}", str(value)))


async def current_weather(force: bool = False) -> dict[str, Any]:
    location = weather_location()
    if not location and len(WEATHER_POSTAL_CODE) == 5 and WEATHER_POSTAL_CODE.isdigit():
        try:
            location = await resolve_postal_code(WEATHER_POSTAL_CODE)
            save_weather_location(location)
        except HTTPException as exc:
            return {"enabled": True, "postal_code": WEATHER_POSTAL_CODE, "stale": True, "error": str(exc.detail)}
    if not location:
        return {"enabled": False}
    now = int(time.time())
    async with weather_lock:
        if not force and weather_cache["data"] and now - weather_cache["fetched_at"] < WEATHER_REFRESH_SECONDS:
            return weather_cache["data"]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("https://api.open-meteo.com/v1/forecast", params={
                    "latitude": location["latitude"], "longitude": location["longitude"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                    "timezone": "auto", "forecast_days": 1,
                })
                response.raise_for_status()
                payload = response.json()
            current = payload.get("current", {})
            units = payload.get("current_units", {})
            data = {"enabled": True, **location, "updated_at": now, "stale": False,
                    "temperature": current.get("temperature_2m"), "temperature_unit": units.get("temperature_2m", "°C"),
                    "apparent_temperature": current.get("apparent_temperature"),
                    "humidity": current.get("relative_humidity_2m"), "weather_code": current.get("weather_code"),
                    "wind_speed": current.get("wind_speed_10m"), "wind_unit": units.get("wind_speed_10m", "km/h"),
                    "attribution": "Open-Meteo"}
            weather_cache.update(fetched_at=now, data=data, error=None)
            return data
        except Exception as exc:
            weather_cache["error"] = str(exc)
            if weather_cache["data"]:
                return {**weather_cache["data"], "stale": True, "error": str(exc)}
            return {"enabled": True, **location, "stale": True, "error": f"Wetterdaten nicht verfügbar: {exc}"}


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            result.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            result.update(flatten(value, f"{prefix}[{index}]"))
    elif prefix:
        result[prefix] = data
    return result


def value_parts(value: Any) -> tuple[str, str | None, float | None]:
    if value is None:
        return "null", None, None
    if isinstance(value, bool):
        return "boolean", "true" if value else "false", 1.0 if value else 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number", str(value), float(value)
    if isinstance(value, str):
        return "text", value, None
    return "text", json.dumps(value, ensure_ascii=False), None


def _words(value: str) -> str:
    value = re.sub(r"\[(\d+)\]", r" \1", value)
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return re.sub(r"[_\-]+", " ", value).strip()


def datapoint_defaults(path: str, data_type: str, numeric_value: float | None = None) -> dict[str, Any]:
    """Return useful presentation defaults without tying ingestion to fixed sensors."""
    leaf = re.split(r"\.|(?=\[)", path)[-1]
    words = _words(leaf)
    key = re.sub(r"[^a-z0-9]", "", words.lower())
    full = re.sub(r"[^a-z0-9]", "", path.lower())

    names = {
        "ph": "pH-Wert", "orp": "Redox", "rx": "Redox", "redox": "Redox",
        "chlorine": "Chlor", "chlor": "Chlor", "cl": "Chlor",
        "temperature": "Temperatur", "temp": "Temperatur", "humidity": "Luftfeuchtigkeit",
        "pressure": "Luftdruck", "voltage": "Spannung", "current": "Stromstärke",
        "power": "Leistung", "energy": "Energie", "frequency": "Frequenz",
        "speed": "Geschwindigkeit", "rpm": "Drehzahl", "level": "Füllstand",
        "filllevel": "Füllstand", "tanklevel": "Füllstand", "time": "Zeit",
    }
    name = names.get(key, words or path)
    path_key = path.lower()
    neopool_names = {
        "neopool.ph.data": "pH-Wert",
        "neopool.ph.min": "pH Minimum",
        "neopool.ph.max": "pH Maximum",
        "neopool.redox.data": "Redox",
        "neopool.redox.setpoint": "Redox-Sollwert",
        "neopool.hydrolysis.data": "Hydrolyse-Leistung",
        "neopool.hydrolysis.setpoint": "Hydrolyse-Sollwert",
        "neopool.hydrolysis.state": "Zustand Hydrolyse",
        "neopool.filtration.state": "Filterpumpe",
        "neopool.filtration.speed": "Geschwindigkeit Pumpe",
        "neopool.filtration.mode": "Betriebsart Filter",
        "neopool.conductivity": "Leitfähigkeit",
        "neopool.temperature": "Wassertemperatur",
    }
    for suffix, candidate in neopool_names.items():
        if suffix in path_key:
            name = candidate
            break
    if "neopool.ph.tank" in path_key:
        name = "pH-Tank"
    elif "neopool" in path_key and "chlor" in path_key and "tank" in path_key:
        name = "Chlor-Tank"
    if key in {"level", "filllevel", "tanklevel"}:
        if "ph" in full:
            name = "pH-Füllstand"
        elif any(token in full for token in ("chlor", "cltank")):
            name = "Chlor-Füllstand"

    unit = ""
    unit_rules = (
        (("temperature", "temp"), "°C"), (("humidity",), "%"),
        (("pressure",), "hPa"), (("voltage",), "V"), (("current",), "A"),
        (("power",), "W"), (("energy",), "kWh"), (("frequency",), "Hz"),
        (("rpm",), "U/min"), (("level", "filllevel", "tanklevel", "percent"), "%"),
        (("orp", "redox", "rx"), "mV"), (("chlorine", "chlor"), "mg/l"),
        (("conductivity",), "%"),
    )
    for keys, candidate in unit_rules:
        if key in keys:
            unit = candidate
            break
    if "neopool.ph." in path_key:
        unit = "pH"
    elif "neopool.redox." in path_key:
        unit = "mV"
    elif "neopool.conductivity" in path_key:
        unit = "%"
    elif "neopool.temperature" in path_key:
        unit = "°C"

    decimals = 0 if data_type in {"boolean", "text"} else 2
    if key in {"temperature", "temp", "humidity", "pressure", "voltage", "current", "power", "energy", "frequency"}:
        decimals = 1
    scale = 1.0
    magnitude = abs(numeric_value) if numeric_value is not None else 0
    if (key == "ph" or "neopool.ph.data" in path_key) and magnitude > 14:
        scale = 0.1 if magnitude <= 140 else 0.01
    elif key in {"temperature", "temp"} and magnitude > 100:
        scale = 0.1
    elif key in {"chlorine", "chlor", "cl"} and magnitude > 20:
        scale = 0.01
    if "neopool.ph." in path_key:
        decimals = 2
    elif "neopool.redox." in path_key or "neopool.conductivity" in path_key:
        decimals = 0
    elif "neopool.temperature" in path_key:
        decimals = 1

    widget_type = "status" if data_type == "boolean" else "text" if data_type == "text" else "gauge"
    if key in {"speed", "filterspeed", "pumpspeed"}:
        widget_type = "levels"
        name = "Geschwindigkeit Pumpe"
    if "neopool.hydrolysis.state" in path_key:
        widget_type = "text"
    elif "neopool.filtration.state" in path_key:
        widget_type = "status"
        decimals = 0
    elif "neopool" in path_key and "tank" in path_key and ("ph" in path_key or "chlor" in path_key):
        widget_type = "status"
        unit = ""
        decimals = 0
    return {"name": name, "unit": unit, "widget_type": widget_type, "scale": scale, "decimals": decimals}


def datapoint_quality_defaults(path: str) -> dict[str, float | bool | None] | None:
    """Return conservative pool limits in the displayed unit for real measurements."""
    key = re.sub(r"[^a-z0-9]", "", path.lower())
    limits: tuple[float | None, float | None, float | None, float | None] | None = None
    alert_low = False

    if "neopoolphdata" in key or key.endswith("ph"):
        limits = (6.8, 7.6, 7.0, 7.4)
    elif any(word in key for word in ("neopoolredoxdata", "orpdata", "redoxdata")) or key.endswith(("redox", "orp", "rx")):
        limits = (550.0, 850.0, 650.0, 750.0)
    elif ("chlor" in key or "chlorine" in key) and not any(word in key for word in ("setpoint", "target", "min", "max")):
        limits = (0.3, 3.0, 0.5, 1.5)
    elif ("neopooltemperature" in key or key.endswith(("temperature", "temp"))) and not any(word in key for word in ("setpoint", "target")):
        limits = (10.0, 35.0, 20.0, 30.0)

    if limits is None:
        return None
    minimum, maximum, warning_low, warning_high = limits
    return {"min_value": minimum, "max_value": maximum, "warning_low": warning_low,
            "warning_high": warning_high, "alert_low": alert_low}


def display_name(path: str) -> str:
    return datapoint_defaults(path, "text")["name"]


def point_dict(row: sqlite3.Row, include_path: bool = True) -> dict[str, Any]:
    value: Any = row["last_value_num"] if row["data_type"] in ("number", "boolean") else row["last_value_text"]
    item = {
        "id": row["id"], "name": row["name"], "data_type": row["data_type"], "unit": row["unit"],
        "visible": bool(row["visible"]), "sort_order": row["sort_order"], "logging": bool(row["logging"]),
        "chart": bool(row["chart"]), "widget_type": row["widget_type"], "scale": row["scale"],
        "decimals": row["decimals"], "min_value": row["min_value"], "max_value": row["max_value"],
        "warning_low": row["warning_low"], "warning_high": row["warning_high"],
        "alert_low": bool(row["alert_low"]), "semantic": datapoint_semantic(row["path"]),
        "importance": datapoint_importance(row["path"]),
        "value": value, "raw_value": row["last_value_text"], "last_seen": row["last_seen"],
    }
    if include_path:
        item["path"] = row["path"]
    return item


def datapoint_semantic(path: str) -> str | None:
    lowered = path.lower()
    if "neopool" in lowered and "tank" in lowered and ("ph" in lowered or "chlor" in lowered):
        return "tank_switch"
    mappings = {
        "neopool.ph.data": "ph_data",
        "neopool.redox.data": "redox_data",
        "neopool.hydrolysis.data": "hydrolysis_data",
        "neopool.hydrolysis.state": "hydrolysis_state",
        "neopool.temperature": "water_temperature",
    }
    for fragment, semantic in mappings.items():
        if fragment in lowered:
            return semantic
    if "neopool.filtration.mode" in lowered:
        return "filtration_mode"
    if "neopool.filtration.state" in lowered:
        return "filtration_state"
    if "neopool.filtration.speed" in lowered:
        return "filtration_speed"
    return None


def datapoint_importance(path: str) -> int:
    lowered = path.lower()
    priorities = (
        ("neopool.ph.data", 0), ("neopool.redox.data", 1), ("neopool.hydrolysis.data", 2),
        ("neopool.temperature", 3), ("neopool.filtration.state", 4), ("neopool.filtration.mode", 5),
        ("neopool.filtration.speed", 6), ("neopool.ph.state", 7), ("neopool.ph.tank", 8),
        ("neopool.hydrolysis.state", 9), ("neopool.hydrolysis.low", 10),
        ("neopool.hydrolysis.fl1", 11), ("neopool.chlorine.data", 12),
        ("neopool.conductivity", 13), ("neopool.ionization.data", 14),
    )
    for fragment, priority in priorities:
        if fragment in lowered:
            return priority
    if ".modules." in lowered:
        return 200
    if ".connection." in lowered or ".powerunit." in lowered:
        return 300
    return 100


def neopool_alarms(payload: Any) -> list[dict[str, str]]:
    """Translate documented NeoPool status fields into user-facing plant alarms."""
    if not isinstance(payload, dict):
        return []
    pool = payload.get("NeoPool")
    if not isinstance(pool, dict):
        for value in payload.values():
            alarms = neopool_alarms(value)
            if alarms or isinstance(value, dict) and "NeoPool" in value:
                return alarms
        return []
    result: list[dict[str, str]] = []
    ph = pool.get("pH", {}) if isinstance(pool.get("pH"), dict) else {}
    ph_code = int(ph.get("State", 0) or 0)
    ph_messages = {
        1: "pH-Wert deutlich zu hoch", 2: "pH-Wert deutlich zu niedrig",
        3: "Maximale Laufzeit der pH-Pumpe überschritten", 4: "pH-Wert über dem Sollwert",
        5: "pH-Wert unter dem Sollwert", 6: "pH-Tank leer",
    }
    if ph_code in ph_messages:
        result.append({"code": f"AL{ph_code}", "title": "pH-Alarm", "detail": ph_messages[ph_code],
                       "severity": "warning" if ph_code in (4, 5) else "critical"})
    hydro = pool.get("Hydrolysis", {}) if isinstance(pool.get("Hydrolysis"), dict) else {}
    if str(hydro.get("State", "")).upper() == "FLOW" or int(hydro.get("FL1", 0) or 0) == 1:
        result.append({"code": "FLOW", "title": "Hydrolyse-Alarm",
                       "detail": "Kein Wasserdurchfluss an der Hydrolysezelle", "severity": "critical"})
    if int(hydro.get("Low", 0) or 0) == 1:
        result.append({"code": "LOW", "title": "Hydrolyse-Alarm",
                       "detail": "Hydrolyse erreicht den Sollwert nicht", "severity": "warning"})
    return result


def ingest(payload: dict[str, Any], now: int) -> list[tuple[int, str, float, str]]:
    flat = flatten(payload)
    alerts: list[tuple[int, str, float, str]] = []
    with db() as conn:
        for position, (path, value) in enumerate(flat.items()):
            data_type, text_value, num_value = value_parts(value)
            defaults = datapoint_defaults(path, data_type, num_value)
            limits = datapoint_quality_defaults(path) or {}
            conn.execute("""INSERT INTO datapoints
                (path,name,data_type,unit,sort_order,widget_type,scale,decimals,min_value,max_value,warning_low,warning_high,
                 alert_low,auto_configured,last_value_text,last_value_num,last_seen,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                data_type=excluded.data_type,last_value_text=excluded.last_value_text,
                last_value_num=excluded.last_value_num,last_seen=excluded.last_seen,updated_at=excluded.updated_at,
                name=CASE WHEN datapoints.auto_configured=1 THEN excluded.name ELSE datapoints.name END,
                unit=CASE WHEN datapoints.auto_configured=1 THEN excluded.unit ELSE datapoints.unit END,
                widget_type=CASE WHEN datapoints.auto_configured=1 THEN excluded.widget_type ELSE datapoints.widget_type END,
                decimals=CASE WHEN datapoints.auto_configured=1 THEN excluded.decimals ELSE datapoints.decimals END,
                scale=CASE WHEN datapoints.auto_configured=1 THEN excluded.scale ELSE datapoints.scale END,
                min_value=CASE WHEN datapoints.auto_configured=1 THEN excluded.min_value ELSE datapoints.min_value END,
                max_value=CASE WHEN datapoints.auto_configured=1 THEN excluded.max_value ELSE datapoints.max_value END,
                warning_low=CASE WHEN datapoints.auto_configured=1 THEN excluded.warning_low ELSE datapoints.warning_low END,
                warning_high=CASE WHEN datapoints.auto_configured=1 THEN excluded.warning_high ELSE datapoints.warning_high END,
                alert_low=CASE WHEN datapoints.auto_configured=1 THEN excluded.alert_low ELSE datapoints.alert_low END""",
                (path, defaults["name"], data_type, defaults["unit"], position, defaults["widget_type"],
                 defaults["scale"], defaults["decimals"], limits.get("min_value"), limits.get("max_value"),
                 limits.get("warning_low"), limits.get("warning_high"), int(limits.get("alert_low", False)),
                 text_value, num_value, now, now, now))
            point = conn.execute("SELECT * FROM datapoints WHERE path=?", (path,)).fetchone()
            if point["logging"]:
                conn.execute("INSERT INTO readings(datapoint_id,ts,value_text,value_num) VALUES(?,?,?,?)",
                             (point["id"], now, text_value, num_value))
            low = (point["alert_low"] and num_value is not None and point["warning_low"] is not None
                   and num_value * point["scale"] < point["warning_low"])
            if low:
                due = not point["alert_active"] or not point["last_alert_at"] or now - point["last_alert_at"] >= ALERT_COOLDOWN_SECONDS
                conn.execute("UPDATE datapoints SET alert_active=1 WHERE id=?", (point["id"],))
                if due:
                    scaled = num_value * point["scale"]
                    alerts.append((point["id"], point["name"], scaled, point["unit"]))
            elif point["alert_active"]:
                conn.execute("UPDATE datapoints SET alert_active=0 WHERE id=?", (point["id"],))
        conn.execute("INSERT INTO poll_events(ts,online,raw_json,error) VALUES(?,1,?,NULL)",
                     (now, json.dumps(payload, ensure_ascii=False)))
    return alerts


def send_low_alert(name: str, value: float, unit: str) -> None:
    if not all((SMTP_HOST, SMTP_FROM, ALERT_EMAIL_TO)):
        raise RuntimeError("SMTP_HOST, SMTP_FROM oder ALERT_EMAIL_TO fehlt")
    rendered = f"{value:g}{(' ' + unit) if unit else ''}"
    message = EmailMessage()
    message["Subject"] = f"Oxilife-Warnung: {name} niedrig"
    message["From"] = SMTP_FROM
    message["To"] = ALERT_EMAIL_TO
    message.set_content(f"Achtung. Füllstand {name} {rendered} niedrig.")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as client:
        client.starttls()
        if SMTP_USER:
            client.login(SMTP_USER, SMTP_PASSWORD)
        client.send_message(message)


async def _poll_once() -> None:
    now = int(time.time())
    if not TASMOTA_BASE_URL:
        latest.update(online=False, updated_at=now, error="TASMOTA_BASE_URL fehlt")
        return
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(TASMOTA_BASE_URL + STATUS_PATH)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Tasmota-Antwort ist kein JSON-Objekt")
        alerts = ingest(payload, now)
        alert_error = None
        for point_id, name, value, unit in alerts:
            try:
                await asyncio.to_thread(send_low_alert, name, value, unit)
                with db() as conn:
                    conn.execute("UPDATE datapoints SET last_alert_at=? WHERE id=?", (now, point_id))
            except Exception as alert_exc:
                alert_error = f"E-Mail-Warnung fehlgeschlagen: {alert_exc}"
        latest.update(online=True, updated_at=now, raw=payload, error=alert_error)
    except Exception as exc:
        message = str(exc)
        latest.update(online=False, updated_at=now, error=message)
        with db() as conn:
            conn.execute("INSERT INTO poll_events(ts,online,raw_json,error) VALUES(?,0,'{}',?)", (now, message))


async def poll_once() -> None:
    async with poll_lock:
        await _poll_once()


async def poller() -> None:
    global last_report_week
    while True:
        await poll_once()
        current_week = tuple(datetime.now().astimezone().isocalendar()[:2])
        if current_week != last_report_week:
            try:
                await asyncio.to_thread(ensure_weekly_report)
            except Exception as exc:
                latest["error"] = f"Wochenbericht fehlgeschlagen: {exc}"
            last_report_week = current_week
        await asyncio.sleep(POLL_SECONDS)


def ensure_weekly_report() -> Path:
    now = datetime.now().astimezone()
    this_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = this_monday - timedelta(days=7), this_monday
    iso = start.isocalendar()
    output = REPORT_DIR / f"wochenbericht_{iso.year}_KW{iso.week:02d}.pdf"
    if not output.exists():
        generate_weekly_report(DB_PATH, output, int(start.timestamp()), int(end.timestamp()))
    return output


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(poller())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Oxilife Dashboard", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=False)


def valid_admin_session(request: Request) -> bool:
    if not request.session.get("admin") or not request.session.get("auth_token"):
        return False
    with db() as conn:
        expected = setting(conn, "session_token")
    return bool(expected and hmac.compare_digest(str(request.session["auth_token"]), expected))


def require_admin(request: Request) -> None:
    if not valid_admin_session(request):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    with db() as conn:
        change_required = setting(conn, "password_change_required", "1") == "1"
    request.session["password_change_required"] = change_required
    if change_required:
        raise HTTPException(status_code=403, detail="Passwortänderung erforderlich")


@app.get("/")
def index(): return FileResponse(BASE / "static" / "index.html")


@app.get("/admin")
def admin(): return FileResponse(BASE / "static" / "admin.html")


@app.get("/statistics")
def statistics_page(): return FileResponse(BASE / "static" / "statistics.html")


@app.get("/api/status")
def status():
    with db() as conn:
        rows = conn.execute("SELECT * FROM datapoints WHERE visible=1 ORDER BY sort_order,name").fetchall()
        config = connection_config(conn)
        heartbeat = (conn.execute("SELECT name,last_seen FROM datapoints WHERE id=?", (config["oxilife_datapoint_id"],)).fetchone()
                     if config["oxilife_datapoint_id"] else None)
    oxilife_configured = heartbeat is not None
    oxilife_online = bool(latest["online"] and heartbeat and heartbeat["last_seen"]
                           and int(time.time()) - heartbeat["last_seen"] <= config["oxilife_timeout_seconds"])
    return {"online": latest["online"], "updated_at": latest["updated_at"], "error": latest["error"],
            "server_time": datetime.now().astimezone().isoformat(),
            "connections": {
                "tasmota": {"name": config["tasmota_name"], "online": latest["online"]},
                "oxilife": {"name": "Oxilife", "online": oxilife_online, "configured": oxilife_configured,
                             "heartbeat_name": heartbeat["name"] if heartbeat else None,
                             "last_seen": heartbeat["last_seen"] if heartbeat else None},
            },
            "alarms": neopool_alarms(latest["raw"]),
            "datapoints": [point_dict(row, include_path=False) for row in rows]}


@app.get("/api/weather")
async def weather():
    return await current_weather()


@app.get("/api/admin/datapoints")
def datapoints(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM datapoints ORDER BY sort_order,name").fetchall()
    return [point_dict(row) for row in rows]


@app.get("/api/admin/alert-config")
def alert_config(request: Request):
    require_admin(request)
    return {
        "configured": bool(SMTP_HOST and SMTP_FROM and ALERT_EMAIL_TO),
        "recipient": ALERT_EMAIL_TO,
        "cooldown_seconds": ALERT_COOLDOWN_SECONDS,
    }


@app.get("/api/admin/weather")
def admin_weather(request: Request):
    require_admin(request)
    return weather_location() or {"postal_code": "", "city": "", "enabled": False}


@app.get("/api/admin/connection")
def admin_connection(request: Request):
    require_admin(request)
    with db() as conn:
        return connection_config(conn)


@app.put("/api/admin/connection")
def update_connection(body: ConnectionUpdate, request: Request):
    require_admin(request)
    with db() as conn:
        if body.oxilife_datapoint_id is not None:
            exists = conn.execute("SELECT 1 FROM datapoints WHERE id=?", (body.oxilife_datapoint_id,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Der ausgewählte Datenpunkt existiert nicht")
        values = {"tasmota_name": body.tasmota_name.strip(),
                  "oxilife_datapoint_id": "" if body.oxilife_datapoint_id is None else str(body.oxilife_datapoint_id),
                  "oxilife_timeout_seconds": str(body.oxilife_timeout_seconds)}
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (key, value))
    return body.model_dump()


@app.put("/api/admin/weather")
async def update_weather(body: WeatherUpdate, request: Request):
    require_admin(request)
    location = await resolve_postal_code(body.postal_code)
    save_weather_location(location)
    weather_cache.update(fetched_at=0, data=None, error=None)
    weather_data = await current_weather(force=True)
    return {**location, "weather": weather_data}


@app.put("/api/admin/datapoints/{point_id}")
def update_datapoint(point_id: int, settings: DatapointUpdate, request: Request):
    require_admin(request)
    values = settings.model_dump()
    if values["min_value"] is not None and values["max_value"] is not None and values["min_value"] > values["max_value"]:
        raise HTTPException(status_code=400, detail="Minimum darf nicht größer als Maximum sein")
    if values["warning_low"] is not None and values["warning_high"] is not None and values["warning_low"] > values["warning_high"]:
        raise HTTPException(status_code=400, detail="Warn min darf nicht größer als Warn max sein")
    if values["min_value"] is not None and values["warning_low"] is not None and values["warning_low"] < values["min_value"]:
        raise HTTPException(status_code=400, detail="Warn min muss innerhalb von Minimum und Maximum liegen")
    if values["max_value"] is not None and values["warning_high"] is not None and values["warning_high"] > values["max_value"]:
        raise HTTPException(status_code=400, detail="Warn max muss innerhalb von Minimum und Maximum liegen")
    if values["alert_low"] and values["warning_low"] is None:
        raise HTTPException(status_code=400, detail="Für die E-Mail-Warnung muss Warn min gesetzt sein")
    fields = list(values)
    with db() as conn:
        cursor = conn.execute(f"UPDATE datapoints SET {','.join(f'{key}=?' for key in fields)},auto_configured=0,updated_at=? WHERE id=?",
                              (*[int(v) if isinstance(v, bool) else v for v in values.values()], int(time.time()), point_id))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Datenpunkt nicht gefunden")
        row = conn.execute("SELECT * FROM datapoints WHERE id=?", (point_id,)).fetchone()
    return point_dict(row)


@app.get("/api/admin/history")
def history(request: Request, hours: int = 24, point_ids: str = ""):
    require_admin(request)
    hours = min(max(hours, 1), 43800)
    since = int(time.time()) - hours * 3600
    bucket = 60 if hours <= 6 else 300 if hours <= 48 else 1800 if hours <= 168 else 7200 if hours <= 744 else 21600 if hours <= 8760 else 86400
    requested = [int(x) for x in point_ids.split(",") if x.strip().isdigit()]
    with db() as conn:
        if requested:
            placeholders = ",".join("?" * len(requested))
            points = conn.execute(f"SELECT * FROM datapoints WHERE id IN ({placeholders})", requested).fetchall()
        else:
            points = conn.execute("SELECT * FROM datapoints WHERE chart=1 ORDER BY sort_order,name").fetchall()
        series = []
        for point in points:
            rows = conn.execute("""SELECT (ts / ?) * ? ts, AVG(value_num) value_num,
                                  MAX(value_text) value_text FROM readings
                                  WHERE datapoint_id=? AND ts>=? GROUP BY ts/? ORDER BY ts""",
                                (bucket, bucket, point["id"], since, bucket)).fetchall()
            stats = conn.execute("SELECT COUNT(*) samples,MIN(value_num) min,MAX(value_num) max,AVG(value_num) avg FROM readings WHERE datapoint_id=? AND ts>=?",
                                 (point["id"], since)).fetchone()
            scale = point["scale"]
            values = [{**dict(row), "value_num": None if row["value_num"] is None else row["value_num"] * scale} for row in rows]
            scaled_stats = {key: (value * scale if key != "samples" and value is not None else value)
                            for key, value in dict(stats).items()}
            if scale < 0 and scaled_stats["min"] is not None:
                scaled_stats["min"], scaled_stats["max"] = scaled_stats["max"], scaled_stats["min"]
            series.append({"datapoint": point_dict(point), "values": values, "stats": scaled_stats})
    return {"hours": hours, "bucket_seconds": bucket, "series": series}


@app.get("/api/admin/reports")
async def weekly_reports(request: Request):
    require_admin(request)
    await asyncio.to_thread(ensure_weekly_report)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return [{"filename": path.name, "size": path.stat().st_size, "created_at": int(path.stat().st_mtime)}
            for path in sorted(REPORT_DIR.glob("wochenbericht_*.pdf"), reverse=True)]


@app.get("/api/admin/reports/{filename}")
def weekly_report_download(filename: str, request: Request):
    require_admin(request)
    if not re.fullmatch(r"wochenbericht_\d{4}_KW\d{2}\.pdf", filename):
        raise HTTPException(status_code=404, detail="Bericht nicht gefunden")
    path = REPORT_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Bericht nicht gefunden")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@app.get("/api/admin/logs")
def logs(request: Request, hours: int = 24, limit: int = 500):
    require_admin(request)
    since = int(time.time()) - min(max(hours, 1), 8760) * 3600
    limit = min(max(limit, 10), 5000)
    with db() as conn:
        rows = conn.execute("""SELECT r.ts,d.id datapoint_id,d.path,d.name,d.unit,r.value_text,r.value_num
                              FROM readings r JOIN datapoints d ON d.id=r.datapoint_id
                              WHERE r.ts>=? ORDER BY r.ts DESC,r.id DESC LIMIT ?""", (since, limit)).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/raw")
def raw(request: Request):
    require_admin(request)
    return latest.get("raw", {})


@app.post("/api/admin/poll")
async def poll_now(request: Request):
    require_admin(request)
    await poll_once()
    return {"ok": latest["online"], "error": latest["error"]}


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    with db() as conn:
        admin_username = setting(conn, "admin_username", ADMIN_USER)
        password_hash = setting(conn, "admin_password_hash")
        change_required = setting(conn, "password_change_required", "1") == "1"
        auth_token = setting(conn, "session_token")
    if body.get("username") == admin_username and verify_password(str(body.get("password", "")), password_hash):
        request.session["admin"] = True
        request.session["auth_token"] = auth_token
        request.session["password_change_required"] = change_required
        return {"ok": True, "password_change_required": change_required}
    raise HTTPException(status_code=401, detail="Falsche Zugangsdaten")


@app.post("/api/change-password")
def change_password(body: PasswordChange, request: Request):
    if not valid_admin_session(request):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if body.password.lower() == "wasserwerte" or body.password == ADMIN_PASSWORD:
        raise HTTPException(status_code=400, detail="Bitte ein neues, individuelles Passwort wählen")
    with db() as conn:
        new_session_token = secrets.token_urlsafe(32)
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('admin_username',?)",
                     (body.username,))
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('admin_password_hash',?)",
                     (hash_password(body.password),))
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('password_change_required','0')")
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('session_token',?)", (new_session_token,))
    request.session["auth_token"] = new_session_token
    request.session["password_change_required"] = False
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/session")
def session(request: Request):
    is_admin = valid_admin_session(request)
    username = ""
    if is_admin:
        with db() as conn:
            username = setting(conn, "admin_username", ADMIN_USER)
            change_required = setting(conn, "password_change_required", "1") == "1"
        request.session["password_change_required"] = change_required
    else:
        request.session.clear()
    return {"admin": is_admin, "username": username,
            "password_change_required": bool(request.session.get("password_change_required")) if is_admin else False}


async def send_command(path: str) -> Any:
    if not path: raise HTTPException(status_code=500, detail="Befehl ist nicht konfiguriert")
    if not TASMOTA_BASE_URL: raise HTTPException(status_code=500, detail="Tasmota-Adresse fehlt")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(TASMOTA_BASE_URL + path)
            response.raise_for_status()
            try: return response.json()
            except Exception: return {"response": response.text}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/filter/{speed}")
async def filter_speed(speed: str, request: Request):
    require_admin(request)
    if speed not in ("1", "2", "3"): raise HTTPException(status_code=400, detail="Ungültige Filterstufe")
    return {"ok": True, "result": await send_command(COMMANDS[speed])}


@app.post("/api/backwash")
async def backwash(request: Request):
    require_admin(request)
    body = await request.json()
    if body.get("confirm") != "RÜCKSPÜLEN": raise HTTPException(status_code=400, detail="Bestätigung fehlt")
    return {"ok": True, "result": await send_command(COMMANDS["backwash"])}


@app.exception_handler(HTTPException)
def http_exception(_: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
