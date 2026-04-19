import os
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path


def _db_path() -> Path:
    return Path(os.getenv("HEALTH_DB_PATH", "/data/health.db"))


@contextmanager
def get_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                date TEXT PRIMARY KEY,
                steps INTEGER,
                calories_total INTEGER,
                calories_active INTEGER,
                distance_meters REAL,
                resting_hr INTEGER,
                avg_stress INTEGER,
                max_stress INTEGER,
                body_battery_high INTEGER,
                body_battery_low INTEGER,
                synced_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sleep (
                date TEXT PRIMARY KEY,
                start_time TEXT,
                end_time TEXT,
                duration_seconds INTEGER,
                deep_seconds INTEGER,
                light_seconds INTEGER,
                rem_seconds INTEGER,
                awake_seconds INTEGER,
                score INTEGER,
                avg_hrv REAL,
                synced_at TEXT
            );

            CREATE TABLE IF NOT EXISTS hrv (
                date TEXT PRIMARY KEY,
                weekly_avg REAL,
                last_night REAL,
                last_night_5min_high REAL,
                baseline_low REAL,
                baseline_high REAL,
                status TEXT,
                synced_at TEXT
            );

            CREATE TABLE IF NOT EXISTS body_battery (
                timestamp TEXT PRIMARY KEY,
                date TEXT,
                value INTEGER,
                synced_at TEXT
            );

            CREATE TABLE IF NOT EXISTS activities (
                activity_id INTEGER PRIMARY KEY,
                date TEXT,
                name TEXT,
                type TEXT,
                duration_seconds INTEGER,
                distance_meters REAL,
                avg_hr INTEGER,
                max_hr INTEGER,
                calories INTEGER,
                synced_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                completed_at TEXT,
                status TEXT,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS withings_body (
                date TEXT PRIMARY KEY,
                weight_kg REAL,
                fat_ratio REAL,
                fat_mass_kg REAL,
                fat_free_mass_kg REAL,
                muscle_mass_kg REAL,
                hydration_kg REAL,
                bone_mass_kg REAL,
                synced_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_body_battery_date ON body_battery(date);
            CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
        """)


# ── Queries ───────────────────────────────────────────────────────────────────

def get_summary(days: int = 7) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_summary WHERE date >= ? ORDER BY date DESC",
            (cutoff,)
        ).fetchall()
        today_row = rows[0] if rows else None

        sleep_rows = conn.execute(
            "SELECT duration_seconds, score FROM sleep WHERE date >= ? ORDER BY date DESC",
            (cutoff,)
        ).fetchall()

    week = [dict(r) for r in rows]
    avg_steps = int(sum(r["steps"] or 0 for r in rows) / len(rows)) if rows else None
    avg_sleep_h = round(
        sum((r["duration_seconds"] or 0) for r in sleep_rows) / len(sleep_rows) / 3600, 1
    ) if sleep_rows else None
    avg_hr = int(sum(r["resting_hr"] or 0 for r in rows if r["resting_hr"]) /
                 len([r for r in rows if r["resting_hr"]])) if any(r["resting_hr"] for r in rows) else None

    return {
        "today": dict(today_row) if today_row else None,
        "week": week,
        "averages": {"steps": avg_steps, "sleep_hours": avg_sleep_h, "resting_hr": avg_hr},
    }


def get_sleep(days: int = 14) -> list:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sleep WHERE date >= ? ORDER BY date DESC",
            (cutoff,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        total = d["duration_seconds"] or 1
        d["duration_hours"] = round(total / 3600, 2)
        d["deep_pct"] = round((d["deep_seconds"] or 0) / total * 100)
        d["light_pct"] = round((d["light_seconds"] or 0) / total * 100)
        d["rem_pct"] = round((d["rem_seconds"] or 0) / total * 100)
        d["awake_pct"] = round((d["awake_seconds"] or 0) / total * 100)
        result.append(d)
    return result


def get_hrv(days: int = 30) -> list:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM hrv WHERE date >= ? ORDER BY date ASC",
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_body_battery(for_date: str | None = None) -> dict:
    target = for_date or date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT timestamp, value FROM body_battery WHERE date = ? ORDER BY timestamp ASC",
            (target,)
        ).fetchall()
        summary = conn.execute(
            "SELECT body_battery_high, body_battery_low FROM daily_summary WHERE date = ?",
            (target,)
        ).fetchone()

    curve = [{"time": r["timestamp"][11:16], "value": r["value"]} for r in rows]
    current = rows[-1]["value"] if rows else None
    return {
        "date": target,
        "current": current,
        "high": summary["body_battery_high"] if summary else None,
        "low": summary["body_battery_low"] if summary else None,
        "curve": curve,
    }


def get_activities(days: int = 30) -> list:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activities WHERE date >= ? ORDER BY date DESC, activity_id DESC",
            (cutoff,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["duration_minutes"] = round((d["duration_seconds"] or 0) / 60)
        d["distance_km"] = round((d["distance_meters"] or 0) / 1000, 2) if d["distance_meters"] else None
        result.append(d)
    return result


def upsert_sleep_rows(rows: list[dict]):
    with get_conn() as conn:
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO sleep
                (date, start_time, end_time, duration_seconds, deep_seconds, light_seconds,
                 rem_seconds, awake_seconds, score, avg_hrv, synced_at)
                VALUES (:date, :start_time, :end_time, :duration_seconds, :deep_seconds,
                        :light_seconds, :rem_seconds, :awake_seconds, :score, :avg_hrv, :synced_at)
            """, r)


def upsert_hrv_rows(rows: list[dict]):
    with get_conn() as conn:
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO hrv
                (date, weekly_avg, last_night, last_night_5min_high,
                 baseline_low, baseline_high, status, synced_at)
                VALUES (:date, :weekly_avg, :last_night, :last_night_5min_high,
                        :baseline_low, :baseline_high, :status, :synced_at)
            """, r)


def upsert_daily_summary_rows(rows: list[dict]):
    with get_conn() as conn:
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO daily_summary
                (date, steps, calories_total, calories_active, distance_meters,
                 resting_hr, avg_stress, max_stress, body_battery_high, body_battery_low, synced_at)
                VALUES (:date, :steps, :calories_total, :calories_active, :distance_meters,
                        :resting_hr, :avg_stress, :max_stress, :body_battery_high, :body_battery_low, :synced_at)
            """, r)


def upsert_body_battery_rows(rows: list[dict]):
    with get_conn() as conn:
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO body_battery
                (timestamp, date, value, synced_at)
                VALUES (:timestamp, :date, :value, :synced_at)
            """, r)


def upsert_withings_rows(rows: list[dict]):
    with get_conn() as conn:
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO withings_body
                (date, weight_kg, fat_ratio, fat_mass_kg, fat_free_mass_kg,
                 muscle_mass_kg, hydration_kg, bone_mass_kg, synced_at)
                VALUES (:date, :weight_kg, :fat_ratio, :fat_mass_kg, :fat_free_mass_kg,
                        :muscle_mass_kg, :hydration_kg, :bone_mass_kg, :synced_at)
            """, r)


def get_last_sync() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def query_health(metric: str = "all", days: int = 30, end_date: str | None = None) -> dict:
    """Flexible health query for LLM tool use.

    metric: sleep | hrv | activities | steps | calories | stress |
            resting_hr | body_battery | distance | all
    days:   how many days back from end_date (or today) to include
    end_date: ISO date string; defaults to today
    """
    anchor = date.fromisoformat(end_date) if end_date else date.today()
    cutoff = (anchor - timedelta(days=days)).isoformat()
    anchor_str = anchor.isoformat()

    # Metrics that come from withings_body
    WITHINGS_METRICS = {"weight", "body_composition"}

    # Metrics that come from daily_summary
    SUMMARY_METRICS = {"steps", "calories", "stress", "resting_hr", "body_battery", "distance"}

    with get_conn() as conn:

        def _date_range(table: str) -> tuple[str | None, str | None]:
            row = conn.execute(
                f"SELECT MIN(date), MAX(date) FROM {table} WHERE date >= ? AND date <= ?",
                (cutoff, anchor_str),
            ).fetchone()
            return (row[0], row[1]) if row else (None, None)

        result: dict = {
            "metric": metric,
            "days_requested": days,
            "end_date": anchor_str,
        }

        # ── sleep ────────────────────────────────────────────────────────────
        if metric in ("sleep", "all"):
            rows = conn.execute(
                "SELECT date, duration_seconds, deep_seconds, light_seconds, rem_seconds, "
                "awake_seconds, score, avg_hrv FROM sleep "
                "WHERE date >= ? AND date <= ? ORDER BY date DESC",
                (cutoff, anchor_str),
            ).fetchall()
            data = []
            for r in rows:
                d = dict(r)
                secs = d.pop("duration_seconds") or 0
                d["duration_hours"] = round(secs / 3600, 2) if secs else None
                d["deep_pct"]  = round(d.pop("deep_seconds")  / secs * 100) if secs else None
                d["light_pct"] = round(d.pop("light_seconds") / secs * 100) if secs else None
                d["rem_pct"]   = round(d.pop("rem_seconds")   / secs * 100) if secs else None
                d["awake_pct"] = round(d.pop("awake_seconds") / secs * 100) if secs else None
                data.append(d)
            result["sleep"] = data
            if data:
                result["sleep_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}

        # ── hrv ─────────────────────────────────────────────────────────────
        if metric in ("hrv", "all"):
            rows = conn.execute(
                "SELECT date, last_night, weekly_avg, baseline_low, baseline_high, status "
                "FROM hrv WHERE date >= ? AND date <= ? ORDER BY date DESC",
                (cutoff, anchor_str),
            ).fetchall()
            data = [dict(r) for r in rows]
            result["hrv"] = data
            if data:
                result["hrv_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}

        # ── activities ───────────────────────────────────────────────────────
        if metric in ("activities", "all"):
            rows = conn.execute(
                "SELECT date, name, type, duration_seconds, distance_meters, "
                "avg_hr, max_hr, calories FROM activities "
                "WHERE date >= ? AND date <= ? ORDER BY date DESC",
                (cutoff, anchor_str),
            ).fetchall()
            data = []
            for r in rows:
                d = dict(r)
                secs = d.pop("duration_seconds") or 0
                d["duration_minutes"] = round(secs / 60)
                meters = d.pop("distance_meters") or 0
                d["distance_km"] = round(meters / 1000, 2) if meters else None
                data.append(d)
            result["activities"] = data
            if data:
                result["activities_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}

        # ── daily_summary metrics ──���─────────────────────────────────────────
        if metric in SUMMARY_METRICS or metric == "all":
            col_map = {
                "steps":        ["steps"],
                "calories":     ["calories_total", "calories_active"],
                "stress":       ["avg_stress", "max_stress"],
                "resting_hr":   ["resting_hr"],
                "body_battery": ["body_battery_high", "body_battery_low"],
                "distance":     ["distance_meters"],
            }
            if metric == "all":
                cols = ["date", "steps", "calories_total", "calories_active",
                        "distance_meters", "resting_hr", "avg_stress", "max_stress",
                        "body_battery_high", "body_battery_low"]
            else:
                cols = ["date"] + col_map[metric]

            col_sql = ", ".join(cols)
            rows = conn.execute(
                f"SELECT {col_sql} FROM daily_summary "
                "WHERE date >= ? AND date <= ? ORDER BY date DESC",
                (cutoff, anchor_str),
            ).fetchall()
            data = [dict(r) for r in rows]

            key = "daily_summary" if metric == "all" else metric
            result[key] = data
            if data:
                result[f"{key}_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}

        # ── withings body composition ────────────────────────────────────────
        if metric in WITHINGS_METRICS or metric == "all":
            rows = conn.execute(
                "SELECT date, weight_kg, fat_ratio, fat_mass_kg, fat_free_mass_kg, "
                "muscle_mass_kg, hydration_kg, bone_mass_kg FROM withings_body "
                "WHERE date >= ? AND date <= ? ORDER BY date DESC",
                (cutoff, anchor_str),
            ).fetchall()
            data = [dict(r) for r in rows]
            result["body_composition"] = data
            if data:
                result["body_composition_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}

    # Surface a clear no-data status so the LLM knows why results are empty
    has_data = any(
        isinstance(v, list) and len(v) > 0
        for v in result.values()
    )
    if not has_data:
        result["status"] = "no_data"
        result["note"] = (
            f"No {metric} data found between {cutoff} and {anchor_str}. "
            "The user may not have synced Garmin data for this period."
        )
    else:
        result["status"] = "ok"

    return result
