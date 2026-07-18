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
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).resolve().parent
DB_PATH = Path('/app/data/oxilife.db')
TASMOTA_BASE_URL = os.getenv('TASMOTA_BASE_URL', '').rstrip('/')
STATUS_PATH = os.getenv('TASMOTA_STATUS_PATH', '/cm?cmnd=Status%2010')
POLL_SECONDS = max(5, int(os.getenv('POLL_SECONDS', '10')))
ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'bitte-aendern')
SESSION_SECRET = os.getenv('SESSION_SECRET', 'change-me')

COMMANDS = {
    '1': os.getenv('FILTER_SPEED_COMMAND_1', ''),
    '2': os.getenv('FILTER_SPEED_COMMAND_2', ''),
    '3': os.getenv('FILTER_SPEED_COMMAND_3', ''),
    'backwash': os.getenv('BACKWASH_COMMAND', ''),
}

latest: dict[str, Any] = {
    'online': False,
    'updated_at': None,
    'ph': None,
    'rx': None,
    'temperature': None,
    'hydrolysis': None,
    'filter_speed': None,
    'filter_start': None,
    'filter_end': None,
    'raw': {},
    'error': 'Noch keine Daten empfangen',
}


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            ph REAL,
            rx REAL,
            temperature REAL,
            hydrolysis REAL,
            filter_speed TEXT,
            filter_start TEXT,
            filter_end TEXT,
            online INTEGER NOT NULL,
            raw_json TEXT NOT NULL
        )''')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(measurements)')}
        if 'filter_start' not in columns:
            conn.execute('ALTER TABLE measurements ADD COLUMN filter_start TEXT')
        if 'filter_end' not in columns:
            conn.execute('ALTER TABLE measurements ADD COLUMN filter_end TEXT')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts)')


def flatten(data: Any, prefix: str = '') -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            path = f'{prefix}.{key}' if prefix else str(key)
            result.update(flatten(value, path))
    elif isinstance(data, list):
        for i, value in enumerate(data):
            result.update(flatten(value, f'{prefix}.{i}'))
    else:
        result[prefix.lower()] = data
    return result


def pick(flat: dict[str, Any], aliases: list[str]) -> Any:
    for alias in aliases:
        alias = alias.lower()
        for key, value in flat.items():
            if key == alias or key.endswith('.' + alias) or alias in key:
                if value not in ('', None):
                    return value
    return None


def number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(',', '.').strip().split(' ')[0]
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    flat = flatten(payload)
    return {
        'ph': number(pick(flat, ['ph', 'phvalue', 'ph_value'])),
        'rx': number(pick(flat, ['rx', 'redox', 'orp', 'redoxvalue'])),
        'temperature': number(pick(flat, ['temperature', 'temp', 'watertemp', 'ds18b20.temperature'])),
        'hydrolysis': number(pick(flat, ['hydrolysis', 'hydro', 'electrolysis', 'production', 'percent'])),
        'filter_speed': pick(flat, ['filter_speed', 'filterspeed', 'pump_speed', 'speed', 'stufe']),
        'filter_start': pick(flat, ['filter_start', 'filterstart', 'starttime']),
        'filter_end': pick(flat, ['filter_end', 'filterend', 'stoptime', 'endtime']),
    }


async def poll_once() -> None:
    now = int(time.time())
    if not TASMOTA_BASE_URL:
        latest.update(online=False, updated_at=now, error='TASMOTA_BASE_URL fehlt')
        return
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(TASMOTA_BASE_URL + STATUS_PATH)
            response.raise_for_status()
            payload = response.json()
        values = normalize(payload)
        latest.update(values)
        latest.update(online=True, updated_at=now, raw=payload, error=None)
        with db() as conn:
            conn.execute('''INSERT INTO measurements
                (ts, ph, rx, temperature, hydrolysis, filter_speed, filter_start, filter_end, online, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)''',
                (now, values['ph'], values['rx'], values['temperature'], values['hydrolysis'],
                 None if values['filter_speed'] is None else str(values['filter_speed']),
                 None if values['filter_start'] is None else str(values['filter_start']),
                 None if values['filter_end'] is None else str(values['filter_end']), json.dumps(payload)))
    except Exception as exc:
        latest.update(online=False, updated_at=now, error=str(exc))
        with db() as conn:
            conn.execute('INSERT INTO measurements (ts, online, raw_json) VALUES (?, 0, ?)', (now, '{}'))


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


app = FastAPI(title='Oxilife Pool Dashboard', lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site='lax', https_only=False)


def require_admin(request: Request) -> None:
    if not request.session.get('admin'):
        raise HTTPException(status_code=401, detail='Nicht angemeldet')


@app.get('/')
def index():
    return FileResponse(BASE / 'static' / 'index.html')


@app.get('/admin')
def admin():
    return FileResponse(BASE / 'static' / 'admin.html')


@app.get('/api/status')
def status():
    data = dict(latest)
    data['server_time'] = datetime.now().astimezone().isoformat()
    data.pop('raw', None)
    return data


@app.get('/api/history')
def history(request: Request, hours: int = 24):
    require_admin(request)
    hours = min(max(hours, 1), 8760)
    since = int(time.time()) - hours * 3600
    bucket = 60 if hours <= 6 else 300 if hours <= 48 else 1800 if hours <= 168 else 7200 if hours <= 744 else 21600
    with db() as conn:
        rows = conn.execute('''SELECT (ts / ?) * ? AS ts,
                                      ROUND(AVG(ph), 3) AS ph,
                                      ROUND(AVG(rx), 2) AS rx,
                                      ROUND(AVG(temperature), 2) AS temperature,
                                      ROUND(AVG(hydrolysis), 2) AS hydrolysis,
                                      MAX(filter_speed) AS filter_speed,
                                      MAX(filter_start) AS filter_start,
                                      MAX(filter_end) AS filter_end,
                                      MIN(online) AS online
                               FROM measurements WHERE ts >= ?
                               GROUP BY ts / ? ORDER BY ts ASC''',
                            (bucket, bucket, since, bucket)).fetchall()
        stats = conn.execute('''SELECT COUNT(*) AS samples,
                                      ROUND(MIN(ph), 3) AS ph_min, ROUND(MAX(ph), 3) AS ph_max, ROUND(AVG(ph), 3) AS ph_avg,
                                      ROUND(MIN(rx), 2) AS rx_min, ROUND(MAX(rx), 2) AS rx_max, ROUND(AVG(rx), 2) AS rx_avg,
                                      ROUND(MIN(temperature), 2) AS temperature_min, ROUND(MAX(temperature), 2) AS temperature_max, ROUND(AVG(temperature), 2) AS temperature_avg,
                                      ROUND(MIN(hydrolysis), 2) AS hydrolysis_min, ROUND(MAX(hydrolysis), 2) AS hydrolysis_max, ROUND(AVG(hydrolysis), 2) AS hydrolysis_avg,
                                      SUM(CASE WHEN online = 0 THEN 1 ELSE 0 END) AS offline_samples
                               FROM measurements WHERE ts >= ?''', (since,)).fetchone()
    return {'hours': hours, 'bucket_seconds': bucket, 'series': [dict(row) for row in rows], 'stats': dict(stats)}


@app.get('/api/logs')
def logs(request: Request, hours: int = 168, limit: int = 200):
    require_admin(request)
    hours = min(max(hours, 1), 8760)
    limit = min(max(limit, 10), 2000)
    since = int(time.time()) - hours * 3600
    with db() as conn:
        rows = conn.execute('''SELECT ts, ph, rx, temperature, hydrolysis, filter_speed,
                                      filter_start, filter_end, online
                               FROM measurements WHERE ts >= ? ORDER BY ts DESC LIMIT ?''',
                            (since, limit)).fetchall()
    return [dict(row) for row in rows]


@app.get('/api/raw')
def raw(request: Request):
    require_admin(request)
    return latest.get('raw', {})


@app.post('/api/login')
async def login(request: Request):
    body = await request.json()
    if body.get('username') == ADMIN_USER and body.get('password') == ADMIN_PASSWORD:
        request.session['admin'] = True
        return {'ok': True}
    raise HTTPException(status_code=401, detail='Falsche Zugangsdaten')


@app.post('/api/logout')
def logout(request: Request):
    request.session.clear()
    return {'ok': True}


@app.get('/api/session')
def session(request: Request):
    return {'admin': bool(request.session.get('admin'))}


async def send_command(path: str) -> Any:
    if not path:
        raise HTTPException(status_code=500, detail='Befehl ist nicht konfiguriert')
    if not TASMOTA_BASE_URL:
        raise HTTPException(status_code=500, detail='Tasmota-Adresse fehlt')
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(TASMOTA_BASE_URL + path)
            response.raise_for_status()
            try:
                return response.json()
            except Exception:
                return {'response': response.text}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/filter/{speed}')
async def filter_speed(speed: str, request: Request):
    require_admin(request)
    if speed not in ('1', '2', '3'):
        raise HTTPException(status_code=400, detail='Ungültige Filterstufe')
    result = await send_command(COMMANDS[speed])
    latest['filter_speed'] = speed
    return {'ok': True, 'result': result}


@app.post('/api/backwash')
async def backwash(request: Request):
    require_admin(request)
    body = await request.json()
    if body.get('confirm') != 'RÜCKSPÜLEN':
        raise HTTPException(status_code=400, detail='Bestätigung fehlt')
    result = await send_command(COMMANDS['backwash'])
    return {'ok': True, 'result': result}


@app.exception_handler(HTTPException)
def http_exception(_: Request, exc: HTTPException):
    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
