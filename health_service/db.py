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

            CREATE TABLE IF NOT EXISTS bloodwork (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                panel TEXT,
                marker TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                ref_low REAL,
                ref_high REAL,
                flag TEXT,
                raw_ref TEXT,
                synced_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_bloodwork_date_marker
                ON bloodwork(date, marker);
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


def upsert_bloodwork_rows(rows: list[dict]):
    with get_conn() as conn:
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO bloodwork
                (date, panel, marker, value, unit, ref_low, ref_high, flag, raw_ref, synced_at)
                VALUES (:date, :panel, :marker, :value, :unit, :ref_low, :ref_high, :flag, :raw_ref, :synced_at)
            """, r)


def get_bloodwork(marker: str | None = None, days: int = 730) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        if marker:
            rows = conn.execute(
                "SELECT * FROM bloodwork WHERE marker LIKE ? AND date >= ? ORDER BY date ASC",
                (f"%{marker}%", cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bloodwork WHERE date >= ? ORDER BY date ASC, panel ASC, marker ASC",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_bloodwork_dates() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM bloodwork ORDER BY date DESC"
        ).fetchall()
    return [r["date"] for r in rows]


def get_last_sync() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


_PERIOD_FMT = {
    "weekly":  "%Y-%W",
    "monthly": "%Y-%m",
}


def _extremes(conn, table: str, col: str, cutoff: str, anchor_str: str) -> dict:
    """Return {min: {value, date}, max: {value, date}} for col over the date range."""
    min_r = conn.execute(
        f"SELECT date, {col} as value FROM {table} "
        f"WHERE date >= ? AND date <= ? AND {col} IS NOT NULL "
        f"ORDER BY {col} ASC LIMIT 1",
        (cutoff, anchor_str),
    ).fetchone()
    max_r = conn.execute(
        f"SELECT date, {col} as value FROM {table} "
        f"WHERE date >= ? AND date <= ? AND {col} IS NOT NULL "
        f"ORDER BY {col} DESC LIMIT 1",
        (cutoff, anchor_str),
    ).fetchone()
    return {
        "min": {"value": min_r["value"], "date": min_r["date"]} if min_r else None,
        "max": {"value": max_r["value"], "date": max_r["date"]} if max_r else None,
    }


def query_health(metric: str = "all", days: int = 30, end_date: str | None = None, resolution: str = "raw") -> dict:
    """Flexible health query for LLM tool use.

    metric: sleep | hrv | activities | steps | calories | stress |
            resting_hr | body_battery | distance | weight | body_composition | all
    days:   how many days back from end_date (or today) to include
    end_date: ISO date string; defaults to today
    resolution: raw | weekly | monthly | summary
        raw     — every daily record (default, backward-compatible)
        weekly  — 7-day averages grouped by ISO week
        monthly — calendar-month averages
        summary — single aggregate: min/max/avg with the date each extreme occurred
    """
    anchor = date.fromisoformat(end_date) if end_date else date.today()
    cutoff = (anchor - timedelta(days=days)).isoformat()
    anchor_str = anchor.isoformat()

    WITHINGS_METRICS = {"weight", "body_composition"}
    SUMMARY_METRICS  = {"steps", "calories", "stress", "resting_hr", "body_battery", "distance"}

    period_fmt = _PERIOD_FMT.get(resolution)

    with get_conn() as conn:

        result: dict = {
            "metric":         metric,
            "resolution":     resolution,
            "days_requested": days,
            "end_date":       anchor_str,
        }

        # ── sleep ─────────────────────────────────────────────────────────────
        if metric in ("sleep", "all"):
            if resolution == "summary":
                ext = _extremes(conn, "sleep", "duration_seconds", cutoff, anchor_str)
                row = conn.execute(
                    "SELECT COUNT(*), ROUND(AVG(duration_seconds)/3600.0,2), "
                    "ROUND(AVG(score),1), MIN(date), MAX(date) "
                    "FROM sleep WHERE date >= ? AND date <= ? AND duration_seconds IS NOT NULL",
                    (cutoff, anchor_str),
                ).fetchone()
                if row and row[0]:
                    result["sleep"] = {
                        "count":              row[0],
                        "avg_duration_hours": row[1],
                        "avg_score":          row[2],
                        "min_duration":       {"hours": round(ext["min"]["value"] / 3600, 2), "date": ext["min"]["date"]} if ext["min"] else None,
                        "max_duration":       {"hours": round(ext["max"]["value"] / 3600, 2), "date": ext["max"]["date"]} if ext["max"] else None,
                        "date_range":         {"from": row[3], "to": row[4]},
                    }
            elif period_fmt:
                rows = conn.execute(
                    f"SELECT strftime('{period_fmt}', date) as period, "
                    "ROUND(AVG(duration_seconds)/3600.0,2) as avg_duration_hours, "
                    "ROUND(AVG(score),1) as avg_score, "
                    "ROUND(AVG(CASE WHEN duration_seconds > 0 THEN deep_seconds*100.0/duration_seconds END),0) as avg_deep_pct, "
                    "ROUND(AVG(CASE WHEN duration_seconds > 0 THEN rem_seconds*100.0/duration_seconds END),0) as avg_rem_pct, "
                    "ROUND(AVG(CASE WHEN duration_seconds > 0 THEN light_seconds*100.0/duration_seconds END),0) as avg_light_pct, "
                    "COUNT(*) as days "
                    "FROM sleep WHERE date >= ? AND date <= ? "
                    "GROUP BY period ORDER BY period",
                    (cutoff, anchor_str),
                ).fetchall()
                data = [dict(r) for r in rows]
                result["sleep"] = data
                if data:
                    result["sleep_date_range"] = {"from": data[0]["period"], "to": data[-1]["period"]}
            else:
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

        # ── hrv ───────────────────────────────────────────────────────────────
        if metric in ("hrv", "all"):
            if resolution == "summary":
                ext = _extremes(conn, "hrv", "last_night", cutoff, anchor_str)
                row = conn.execute(
                    "SELECT COUNT(*), ROUND(AVG(last_night),1), ROUND(AVG(weekly_avg),1), "
                    "ROUND(AVG(baseline_low),1), ROUND(AVG(baseline_high),1), MIN(date), MAX(date) "
                    "FROM hrv WHERE date >= ? AND date <= ? AND last_night IS NOT NULL",
                    (cutoff, anchor_str),
                ).fetchone()
                if row and row[0]:
                    result["hrv"] = {
                        "count":             row[0],
                        "avg_last_night":    row[1],
                        "avg_weekly_avg":    row[2],
                        "avg_baseline_low":  row[3],
                        "avg_baseline_high": row[4],
                        "min_last_night":    ext["min"],
                        "max_last_night":    ext["max"],
                        "date_range":        {"from": row[5], "to": row[6]},
                    }
            elif period_fmt:
                rows = conn.execute(
                    f"SELECT strftime('{period_fmt}', date) as period, "
                    "ROUND(AVG(last_night),1) as avg_last_night, "
                    "ROUND(AVG(weekly_avg),1) as avg_weekly_avg, "
                    "ROUND(AVG(baseline_low),1) as avg_baseline_low, "
                    "ROUND(AVG(baseline_high),1) as avg_baseline_high, "
                    "COUNT(*) as days "
                    "FROM hrv WHERE date >= ? AND date <= ? AND last_night IS NOT NULL "
                    "GROUP BY period ORDER BY period",
                    (cutoff, anchor_str),
                ).fetchall()
                data = [dict(r) for r in rows]
                result["hrv"] = data
                if data:
                    result["hrv_date_range"] = {"from": data[0]["period"], "to": data[-1]["period"]}
            else:
                rows = conn.execute(
                    "SELECT date, last_night, weekly_avg, baseline_low, baseline_high, status "
                    "FROM hrv WHERE date >= ? AND date <= ? ORDER BY date DESC",
                    (cutoff, anchor_str),
                ).fetchall()
                data = [dict(r) for r in rows]
                result["hrv"] = data
                if data:
                    result["hrv_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}

        # ── activities ────────────────────────────────────────────────────────
        if metric in ("activities", "all"):
            if resolution == "summary":
                row = conn.execute(
                    "SELECT COUNT(*), "
                    "ROUND(SUM(duration_seconds)/3600.0,1) as total_hours, "
                    "ROUND(AVG(duration_seconds)/60.0,0) as avg_duration_min, "
                    "ROUND(SUM(COALESCE(distance_meters,0))/1000.0,1) as total_km, "
                    "MIN(date), MAX(date) "
                    "FROM activities WHERE date >= ? AND date <= ?",
                    (cutoff, anchor_str),
                ).fetchone()
                if row and row[0]:
                    result["activities"] = {
                        "count":                row[0],
                        "total_hours":          row[1],
                        "avg_duration_minutes": int(row[2]) if row[2] else None,
                        "total_distance_km":    row[3],
                        "date_range":           {"from": row[4], "to": row[5]},
                    }
            elif period_fmt:
                rows = conn.execute(
                    f"SELECT strftime('{period_fmt}', date) as period, "
                    "COUNT(*) as count, "
                    "ROUND(SUM(duration_seconds)/3600.0,1) as total_hours, "
                    "ROUND(AVG(avg_hr),0) as avg_hr "
                    "FROM activities WHERE date >= ? AND date <= ? "
                    "GROUP BY period ORDER BY period",
                    (cutoff, anchor_str),
                ).fetchall()
                data = [dict(r) for r in rows]
                result["activities"] = data
                if data:
                    result["activities_date_range"] = {"from": data[0]["period"], "to": data[-1]["period"]}
            else:
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

        # ── daily_summary metrics ─────────────────────────────────────────────
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
                if resolution == "summary":
                    row = conn.execute(
                        "SELECT COUNT(*), "
                        "ROUND(AVG(steps),0), ROUND(AVG(resting_hr),1), "
                        "ROUND(AVG(avg_stress),1), ROUND(AVG(body_battery_high),1), "
                        "ROUND(AVG(body_battery_low),1), ROUND(AVG(calories_total),0), "
                        "ROUND(AVG(distance_meters),0), MIN(date), MAX(date) "
                        "FROM daily_summary WHERE date >= ? AND date <= ?",
                        (cutoff, anchor_str),
                    ).fetchone()
                    if row and row[0]:
                        result["daily_summary"] = {
                            "count":                 row[0],
                            "avg_steps":             int(row[1]) if row[1] else None,
                            "avg_resting_hr":        row[2],
                            "avg_stress":            row[3],
                            "avg_body_battery_high": row[4],
                            "avg_body_battery_low":  row[5],
                            "avg_calories_total":    int(row[6]) if row[6] else None,
                            "avg_distance_meters":   int(row[7]) if row[7] else None,
                            "date_range":            {"from": row[8], "to": row[9]},
                        }
                elif period_fmt:
                    rows = conn.execute(
                        f"SELECT strftime('{period_fmt}', date) as period, "
                        "ROUND(AVG(steps),0) as avg_steps, "
                        "ROUND(AVG(calories_total),0) as avg_calories_total, "
                        "ROUND(AVG(resting_hr),1) as avg_resting_hr, "
                        "ROUND(AVG(avg_stress),1) as avg_stress, "
                        "ROUND(AVG(body_battery_high),1) as avg_body_battery_high, "
                        "ROUND(AVG(body_battery_low),1) as avg_body_battery_low, "
                        "ROUND(AVG(distance_meters),0) as avg_distance_meters, "
                        "COUNT(*) as days "
                        "FROM daily_summary WHERE date >= ? AND date <= ? "
                        "GROUP BY period ORDER BY period",
                        (cutoff, anchor_str),
                    ).fetchall()
                    data = [dict(r) for r in rows]
                    result["daily_summary"] = data
                    if data:
                        result["daily_summary_date_range"] = {"from": data[0]["period"], "to": data[-1]["period"]}
                else:
                    cols = ["date", "steps", "calories_total", "calories_active",
                            "distance_meters", "resting_hr", "avg_stress", "max_stress",
                            "body_battery_high", "body_battery_low"]
                    rows = conn.execute(
                        f"SELECT {', '.join(cols)} FROM daily_summary "
                        "WHERE date >= ? AND date <= ? ORDER BY date DESC",
                        (cutoff, anchor_str),
                    ).fetchall()
                    data = [dict(r) for r in rows]
                    result["daily_summary"] = data
                    if data:
                        result["daily_summary_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}
            else:
                cols = col_map[metric]
                primary_col = cols[0]
                if resolution == "summary":
                    ext = _extremes(conn, "daily_summary", primary_col, cutoff, anchor_str)
                    avg_sql = ", ".join(f"ROUND(AVG({c}),1) as avg_{c}" for c in cols)
                    row = conn.execute(
                        f"SELECT COUNT(*), {avg_sql}, MIN(date), MAX(date) "
                        f"FROM daily_summary WHERE date >= ? AND date <= ? AND {primary_col} IS NOT NULL",
                        (cutoff, anchor_str),
                    ).fetchone()
                    if row and row[0]:
                        d: dict = {"count": row[0], "date_range": {"from": row[-2], "to": row[-1]}}
                        for i, c in enumerate(cols):
                            d[f"avg_{c}"] = row[1 + i]
                        d[f"min_{primary_col}"] = ext["min"]
                        d[f"max_{primary_col}"] = ext["max"]
                        result[metric] = d
                elif period_fmt:
                    avg_sql = ", ".join(f"ROUND(AVG({c}),1) as avg_{c}" for c in cols)
                    rows = conn.execute(
                        f"SELECT strftime('{period_fmt}', date) as period, {avg_sql}, COUNT(*) as days "
                        f"FROM daily_summary WHERE date >= ? AND date <= ? AND {primary_col} IS NOT NULL "
                        f"GROUP BY period ORDER BY period",
                        (cutoff, anchor_str),
                    ).fetchall()
                    data = [dict(r) for r in rows]
                    result[metric] = data
                    if data:
                        result[f"{metric}_date_range"] = {"from": data[0]["period"], "to": data[-1]["period"]}
                else:
                    col_sql = ", ".join(["date"] + cols)
                    rows = conn.execute(
                        f"SELECT {col_sql} FROM daily_summary "
                        "WHERE date >= ? AND date <= ? ORDER BY date DESC",
                        (cutoff, anchor_str),
                    ).fetchall()
                    data = [dict(r) for r in rows]
                    result[metric] = data
                    if data:
                        result[f"{metric}_date_range"] = {"from": data[-1]["date"], "to": data[0]["date"]}

        # ── Withings body composition ──────────────────────────────────────────
        if metric in WITHINGS_METRICS or metric == "all":
            if resolution == "summary":
                ext = _extremes(conn, "withings_body", "weight_kg", cutoff, anchor_str)
                row = conn.execute(
                    "SELECT COUNT(*), ROUND(AVG(weight_kg),2), ROUND(AVG(fat_ratio),1), "
                    "ROUND(AVG(muscle_mass_kg),1), MIN(date), MAX(date) "
                    "FROM withings_body WHERE date >= ? AND date <= ? AND weight_kg IS NOT NULL",
                    (cutoff, anchor_str),
                ).fetchone()
                if row and row[0]:
                    result["body_composition"] = {
                        "count":              row[0],
                        "avg_weight_kg":      row[1],
                        "avg_fat_ratio":      row[2],
                        "avg_muscle_mass_kg": row[3],
                        "min_weight":         ext["min"],
                        "max_weight":         ext["max"],
                        "date_range":         {"from": row[4], "to": row[5]},
                    }
            elif period_fmt:
                rows = conn.execute(
                    f"SELECT strftime('{period_fmt}', date) as period, "
                    "ROUND(AVG(weight_kg),2) as avg_weight_kg, "
                    "ROUND(AVG(fat_ratio),1) as avg_fat_ratio, "
                    "ROUND(AVG(muscle_mass_kg),1) as avg_muscle_mass_kg, "
                    "COUNT(*) as days "
                    "FROM withings_body WHERE date >= ? AND date <= ? AND weight_kg IS NOT NULL "
                    "GROUP BY period ORDER BY period",
                    (cutoff, anchor_str),
                ).fetchall()
                data = [dict(r) for r in rows]
                result["body_composition"] = data
                if data:
                    result["body_composition_date_range"] = {"from": data[0]["period"], "to": data[-1]["period"]}
            else:
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

    has_data = any(
        (isinstance(v, list) and len(v) > 0) or (isinstance(v, dict) and v.get("count", 0) > 0)
        for k, v in result.items()
        if k not in ("metric", "resolution", "days_requested", "end_date", "status", "note")
        and not k.endswith("_date_range")
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
