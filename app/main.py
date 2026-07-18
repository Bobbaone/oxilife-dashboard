import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", "/app/data/oxilife.db"))
TASMOTA_BASE_URL = os.getenv("TASMOTA_BASE_URL", "").rstrip("/")
STATUS_PATH = os.getenv("TASMOTA_STATUS_PATH", "/cm?cmnd=Status%2010")
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "bitte-aendern")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")
COMMANDS = {
    "1": os.getenv("FILTER_SPEED_COMMAND_1", ""),
    "2": os.getenv("FILTER_SPEED_COMMAND_2", ""),
    "3": os.getenv("FILTER_SPEED_COMMAND_3", ""),
    "backwash": os.getenv("BACKWASH_COMMAND", ""),
}
latest: dict[str, Any] = {"online": False, "updated_at": None, "raw": {}, "error": "Noch keine Daten empfangen"}


class DatapointUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="", max_length=40)
    visible: bool = False
    sort_order: int = Field(default=0, ge=-100000, le=100000)
    logging: bool = True
    chart: bool = False
    widget_type: str = Field(default="value", pattern="^(value|gauge|status|text)$")
    scale: float = Field(default=1.0, ge=-1000000, le=1000000)
    decimals: int = Field(default=2, ge=0, le=8)
    min_value: float | None = None
    max_value: float | None = None
    warning_low: float | None = None
    warning_high: float | None = None


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
        """)


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


def display_name(path: str) -> str:
    return path.replace(".", " › ").replace("_", " ")


def point_dict(row: sqlite3.Row, include_path: bool = True) -> dict[str, Any]:
    value: Any = row["last_value_num"] if row["data_type"] in ("number", "boolean") else row["last_value_text"]
    item = {
        "id": row["id"], "name": row["name"], "data_type": row["data_type"], "unit": row["unit"],
        "visible": bool(row["visible"]), "sort_order": row["sort_order"], "logging": bool(row["logging"]),
        "chart": bool(row["chart"]), "widget_type": row["widget_type"], "scale": row["scale"],
        "decimals": row["decimals"], "min_value": row["min_value"], "max_value": row["max_value"],
        "warning_low": row["warning_low"], "warning_high": row["warning_high"],
        "value": value, "raw_value": row["last_value_text"], "last_seen": row["last_seen"],
    }
    if include_path:
        item["path"] = row["path"]
    return item


def ingest(payload: dict[str, Any], now: int) -> None:
    flat = flatten(payload)
    with db() as conn:
        for position, (path, value) in enumerate(flat.items()):
            data_type, text_value, num_value = value_parts(value)
            conn.execute("""INSERT INTO datapoints
                (path,name,data_type,sort_order,last_value_text,last_value_num,last_seen,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                data_type=excluded.data_type,last_value_text=excluded.last_value_text,
                last_value_num=excluded.last_value_num,last_seen=excluded.last_seen,updated_at=excluded.updated_at""",
                (path, display_name(path), data_type, position, text_value, num_value, now, now, now))
            point = conn.execute("SELECT id, logging FROM datapoints WHERE path=?", (path,)).fetchone()
            if point["logging"]:
                conn.execute("INSERT INTO readings(datapoint_id,ts,value_text,value_num) VALUES(?,?,?,?)",
                             (point["id"], now, text_value, num_value))
        conn.execute("INSERT INTO poll_events(ts,online,raw_json,error) VALUES(?,1,?,NULL)",
                     (now, json.dumps(payload, ensure_ascii=False)))


async def poll_once() -> None:
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
        ingest(payload, now)
        latest.update(online=True, updated_at=now, raw=payload, error=None)
    except Exception as exc:
        message = str(exc)
        latest.update(online=False, updated_at=now, error=message)
        with db() as conn:
            conn.execute("INSERT INTO poll_events(ts,online,raw_json,error) VALUES(?,0,'{}',?)", (now, message))


async def poller() -> None:
    while True:
        await poll_once()
        await asyncio.sleep(POLL_SECONDS)


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


def require_admin(request: Request) -> None:
    if not request.session.get("admin"):
        raise HTTPException(status_code=401, detail="Nicht angemeldet")


@app.get("/")
def index(): return FileResponse(BASE / "static" / "index.html")


@app.get("/admin")
def admin(): return FileResponse(BASE / "static" / "admin.html")


@app.get("/api/status")
def status():
    with db() as conn:
        rows = conn.execute("SELECT * FROM datapoints WHERE visible=1 ORDER BY sort_order,name").fetchall()
    return {"online": latest["online"], "updated_at": latest["updated_at"], "error": latest["error"],
            "server_time": datetime.now().astimezone().isoformat(),
            "datapoints": [point_dict(row, include_path=False) for row in rows]}


@app.get("/api/admin/datapoints")
def datapoints(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM datapoints ORDER BY sort_order,name").fetchall()
    return [point_dict(row) for row in rows]


@app.put("/api/admin/datapoints/{point_id}")
def update_datapoint(point_id: int, settings: DatapointUpdate, request: Request):
    require_admin(request)
    values = settings.model_dump()
    if values["min_value"] is not None and values["max_value"] is not None and values["min_value"] > values["max_value"]:
        raise HTTPException(status_code=400, detail="Minimum darf nicht größer als Maximum sein")
    fields = list(values)
    with db() as conn:
        cursor = conn.execute(f"UPDATE datapoints SET {','.join(f'{key}=?' for key in fields)},updated_at=? WHERE id=?",
                              (*[int(v) if isinstance(v, bool) else v for v in values.values()], int(time.time()), point_id))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Datenpunkt nicht gefunden")
        row = conn.execute("SELECT * FROM datapoints WHERE id=?", (point_id,)).fetchone()
    return point_dict(row)


@app.get("/api/admin/history")
def history(request: Request, hours: int = 24, point_ids: str = ""):
    require_admin(request)
    hours = min(max(hours, 1), 8760)
    since = int(time.time()) - hours * 3600
    bucket = 60 if hours <= 6 else 300 if hours <= 48 else 1800 if hours <= 168 else 7200 if hours <= 744 else 21600
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
    if body.get("username") == ADMIN_USER and body.get("password") == ADMIN_PASSWORD:
        request.session["admin"] = True
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Falsche Zugangsdaten")


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/session")
def session(request: Request): return {"admin": bool(request.session.get("admin"))}


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
