"""One-time history backfill for previewing charts on a new installation."""

import argparse
import math
import random
import time

from app.main import db, init_db


def demo_value(path: str, name: str, current: float | None, scale: float, phase: float) -> float:
    text = f"{name} {path}".lower()
    presets = (
        (("ph",), 7.2, 0.13),
        (("redox", "orp", " rx"), 720.0, 28.0),
        (("temperatur", "temperature", "temp"), 25.0, 1.8),
        (("salz", "salt", "conductivity", "leitfähigkeit"), 3.5, 0.18),
        (("chlor",), 0.7, 0.12),
        (("hydrolysis", "hydrolyse"), 75.0, 12.0),
        (("füllstand", "fuellstand", "level", "tank"), 72.0, 7.0),
        (("speed", "geschwindigkeit", "rpm"), 2.0, 0.8),
    )
    base = float(current) if current is not None else 50.0
    amplitude = max(abs(base) * 0.04, 0.1)
    for keys, fallback, spread in presets:
        if any(key in text for key in keys):
            base = float(current) if current not in (None, 0) else fallback
            amplitude = spread / max(abs(scale), 0.000001)
            break
    noise = random.uniform(-amplitude * 0.18, amplitude * 0.18)
    return base + math.sin(phase) * amplitude + noise


def seed(days: int, interval_minutes: int) -> tuple[int, int]:
    init_db()
    now = int(time.time())
    start = now - days * 86400
    interval = interval_minutes * 60
    random.seed(20260719)
    inserted = 0
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS demo_readings (reading_id INTEGER PRIMARY KEY)")
        points = conn.execute("""SELECT * FROM datapoints
                                 WHERE logging=1
                                 ORDER BY sort_order,name""").fetchall()
        for point in points:
            # Backfill only gaps; running the command again does not duplicate the preview.
            occupied_slots = {round((row[0] - start) / interval) for row in conn.execute(
                "SELECT ts FROM readings WHERE datapoint_id=? AND ts>=?", (point["id"], start))}
            timestamps = range(start, now, interval)
            for index, ts in enumerate(timestamps):
                if index in occupied_slots:
                    continue
                if point["data_type"] == "number":
                    phase = index / max(1, (24 * 60 // interval_minutes)) * math.tau
                    value = demo_value(point["path"], point["name"], point["last_value_num"], point["scale"], phase)
                    text_value, numeric_value = f"{value:.6f}", value
                else:
                    text_value, numeric_value = point["last_value_text"], point["last_value_num"]
                cursor = conn.execute("INSERT INTO readings(datapoint_id,ts,value_text,value_num) VALUES(?,?,?,?)",
                                      (point["id"], ts, text_value, numeric_value))
                conn.execute("INSERT INTO demo_readings(reading_id) VALUES(?)", (cursor.lastrowid,))
                inserted += 1
            measurement = any(key in f'{point["name"]} {point["path"]}'.lower() for key in
                              ("ph", "redox", "orp", "temper", "chlor", "salz", "salt", "conductivity",
                               "hydro", "füll", "fuell", "tank", "speed", "geschwindigkeit"))
            if point["data_type"] == "number" and (point["visible"] or measurement):
                conn.execute("UPDATE datapoints SET chart=1 WHERE id=?", (point["id"],))
    return len(points), inserted


def remove_demo() -> int:
    init_db()
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_readings'").fetchone()
        if not exists:
            return 0
        count = conn.execute("SELECT COUNT(*) FROM demo_readings").fetchone()[0]
        conn.execute("DELETE FROM readings WHERE id IN (SELECT reading_id FROM demo_readings)")
        conn.execute("DELETE FROM demo_readings")
        return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Erzeugt eine plausible Demo-Historie.")
    parser.add_argument("--days", type=int, default=7, choices=range(1, 32))
    parser.add_argument("--interval", type=int, default=10, choices=range(1, 61), metavar="MINUTEN")
    parser.add_argument("--remove", action="store_true", help="Entfernt ausschließlich zuvor erzeugte Demo-Werte.")
    args = parser.parse_args()
    if args.remove:
        print(f"Demo-Historie entfernt: {remove_demo()} Messwerte gelöscht.")
    else:
        count, rows = seed(args.days, args.interval)
        print(f"Demo-Historie erstellt: {rows} Messwerte für {count} Datenpunkte über {args.days} Tage.")
