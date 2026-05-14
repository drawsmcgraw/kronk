import csv
import io
import json
import logging
import zipfile
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from db import (
    get_activities, get_body_battery, get_conn,
    get_hrv, get_last_sync, get_sleep, get_summary, init_db,
    query_health,
    upsert_body_battery_rows, upsert_daily_summary_rows, upsert_hrv_rows, upsert_sleep_rows,
    upsert_bloodwork_rows, get_bloodwork, get_bloodwork_dates,
)
from withings_sync import sync_withings
from chunker import chunk_daily, chunk_sleep, chunk_hrv, chunk_activity, chunk_bloodwork_panel
from vector_store import upsert_chunks, chunk_count, search as vs_search
from bloodwork_parser import parse_labcorp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def sync_garmin(days_back: int = 7):
    """Stub — Garmin sync disabled pending Infisical rebuild."""
    logger.info("Garmin sync skipped — credentials not configured (Infisical pending rebuild)")
    return {"status": "skipped", "reason": "credentials not configured"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Health service started — live syncs disabled (Infisical pending rebuild)")
    yield


app = FastAPI(title="Kronk Health Service", lifespan=lifespan)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open("/app/static/index.html") as f:
        return f.read()


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary(days: int = Query(default=7, ge=1, le=730)):
    return get_summary(days)


@app.get("/api/sleep")
def api_sleep(days: int = Query(default=14, ge=1, le=730)):
    return get_sleep(days)


@app.get("/api/hrv")
def api_hrv(days: int = Query(default=30, ge=1, le=730)):
    return get_hrv(days)


@app.get("/api/body-battery")
def api_body_battery(date: str = Query(default=None)):
    return get_body_battery(date)


@app.get("/api/activities")
def api_activities(days: int = Query(default=30, ge=1, le=730)):
    return get_activities(days)


@app.get("/api/sync-status")
def api_sync_status():
    return get_last_sync() or {"status": "never synced"}


@app.post("/api/sync")
def api_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_garmin)
    return {"status": "sync started"}


@app.post("/api/sync/withings")
def api_sync_withings(background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_withings)
    return {"status": "withings sync started"}


@app.get("/api/query")
def api_query(
    metric: str = Query(default="all"),
    days: int = Query(default=30, ge=1),
    end_date: str = Query(default=None),
    resolution: str = Query(default="raw"),
):
    """Flexible health query for LLM tool use.
    metric: sleep | hrv | activities | steps | calories | stress |
            resting_hr | body_battery | distance | weight | body_composition | all
    resolution: raw | weekly | monthly | summary
    """
    return query_health(metric=metric, days=days, end_date=end_date, resolution=resolution)


# ── CSV import helpers ────────────────────────────────────────────────────────

def _norm_key(s: str) -> str:
    """Normalize a column header for fuzzy matching."""
    return s.strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _find_col(row: dict, *candidates: str):
    """Return the value of the first matching column (case/space insensitive)."""
    norm = {_norm_key(k): v for k, v in row.items()}
    for c in candidates:
        val = norm.get(_norm_key(c))
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _try_int(v) -> int | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    try:
        return int(float(s)) if s else None
    except (ValueError, TypeError):
        return None


def _try_float(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    try:
        return float(s) if s else None
    except (ValueError, TypeError):
        return None


def _parse_duration(value: str) -> int | None:
    """Convert HH:MM:SS or MM:SS string to seconds, or plain int."""
    if not value:
        return None
    s = value.strip()
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _parse_seconds(v) -> int | None:
    """Parse either a raw seconds int or an HH:MM:SS duration string."""
    if v is None:
        return None
    s = str(v).strip()
    if ":" in s:
        return _parse_duration(s)
    return _try_int(s)


def _parse_distance_to_meters(value: str, col_name: str = "") -> float | None:
    if not value:
        return None
    try:
        dist = float(value.replace(",", ""))
    except ValueError:
        return None
    col_lower = col_name.lower()
    if "km" in col_lower:
        return dist * 1000
    if "mi" in col_lower:
        return dist * 1609.34
    if dist < 1000:
        return dist * 1000
    return dist


def _norm_ts(s: str) -> str:
    """Normalize a Garmin timestamp to ISO-8601 (replace space separator with T)."""
    return s.strip().replace(" ", "T") if s else s


def _detect_csv_type(headers: list[str]) -> str:
    """Return the category of a Garmin CSV by inspecting its headers."""
    h = {_norm_key(c) for c in headers}
    if "activitytype" in h and ("avghr" in h or "activityid" in h):
        return "activities"
    if "calendardate" in h and "deepsleepseconds" in h:
        return "sleep"
    if "weeklyavghrv" in h or "lastnightavg" in h:
        return "hrv"
    # Simpler HRV report format
    if "weeklyaverage" in h and "lastnight" in h:
        return "hrv"
    # Epoch summaries contain body battery per interval
    if "calendardate" in h and any(
        x in h for x in ("bodybatteryhighest", "bodybatteryatend", "bodybatterychargeamount")
    ):
        return "epoch_summary"
    # Wellness daily summary
    if "calendardate" in h and "totalsteps" in h:
        return "daily_summary"
    return "unknown"


# ── Per-type importers ────────────────────────────────────────────────────────

def _import_activities(reader: csv.DictReader, synced_at: str) -> tuple[int, int]:
    inserted = skipped = 0
    with get_conn() as conn:
        for row in reader:
            try:
                act_type = (row.get("Activity Type") or "unknown").lower().replace(" ", "_")
                raw_date = row.get("Date") or row.get("Start Time") or ""
                act_date = raw_date[:10] if raw_date else ""
                if not act_date:
                    skipped += 1
                    continue

                name = row.get("Title") or row.get("Activity Name") or act_type
                calories = row.get("Calories") or row.get("Calories Burned")
                avg_hr = row.get("Avg HR") or row.get("Average Heart Rate (bpm)")
                max_hr = row.get("Max HR") or row.get("Max. Heart Rate (bpm)")

                raw_dur = row.get("Time") or row.get("Elapsed Time") or row.get("Duration") or ""
                duration_s = _parse_duration(raw_dur)

                dist_col = next((c for c in row if "distance" in c.lower()), None)
                raw_dist = row.get(dist_col, "") if dist_col else ""
                dist_m = _parse_distance_to_meters(raw_dist, dist_col or "")

                act_id_raw = row.get("Activity ID") or row.get("ID")
                if act_id_raw:
                    try:
                        act_id = int(act_id_raw)
                    except ValueError:
                        act_id = abs(hash(f"{act_date}:{name}")) % (10 ** 9)
                else:
                    act_id = abs(hash(f"{act_date}:{name}")) % (10 ** 9)

                conn.execute("""
                    INSERT OR REPLACE INTO activities
                    (activity_id, date, name, type, duration_seconds,
                     distance_meters, avg_hr, max_hr, calories, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    act_id, act_date, name, act_type, duration_s, dist_m,
                    int(avg_hr) if avg_hr and str(avg_hr).strip().lstrip("-").isdigit() else None,
                    int(max_hr) if max_hr and str(max_hr).strip().lstrip("-").isdigit() else None,
                    int(float(calories)) if calories else None,
                    synced_at,
                ))
                inserted += 1
            except Exception:
                skipped += 1
    return inserted, skipped


def _import_sleep(reader: csv.DictReader, synced_at: str) -> tuple[int, int]:
    rows_out = []
    skipped = 0
    for row in reader:
        date_str = _find_col(row, "calendarDate", "calendar_date", "Date") or ""
        date_str = date_str[:10]
        if not date_str or len(date_str) < 10:
            skipped += 1
            continue

        start = _find_col(row, "startTimeGMT", "startTime", "Sleep Start")
        end   = _find_col(row, "endTimeGMT",   "endTime",   "Sleep End")

        duration = _parse_seconds(_find_col(row, "sleepTimeSeconds", "Total Sleep"))
        deep     = _parse_seconds(_find_col(row, "deepSleepSeconds",  "Deep Sleep"))
        light    = _parse_seconds(_find_col(row, "lightSleepSeconds", "Light Sleep"))
        rem      = _parse_seconds(_find_col(row, "remSleepSeconds",   "REM", "REM Sleep"))
        awake    = _parse_seconds(_find_col(row, "awakeSleepSeconds", "Awake", "Awake Time"))

        if duration is None:
            duration = sum(v or 0 for v in [deep, light, rem, awake]) or None

        score   = _try_int(_find_col(row, "overallScore", "sleepScore", "Overall Sleep Score", "Sleep Score"))
        avg_hrv = _try_float(_find_col(row, "averageHRV", "avg_hrv", "Average HRV"))

        rows_out.append({
            "date": date_str,
            "start_time": _norm_ts(start) if start else None,
            "end_time":   _norm_ts(end)   if end   else None,
            "duration_seconds": duration,
            "deep_seconds":     deep,
            "light_seconds":    light,
            "rem_seconds":      rem,
            "awake_seconds":    awake,
            "score":    score,
            "avg_hrv":  avg_hrv,
            "synced_at": synced_at,
        })

    if rows_out:
        upsert_sleep_rows(rows_out)
    return len(rows_out), skipped


def _import_hrv(reader: csv.DictReader, synced_at: str) -> tuple[int, int]:
    rows_out = []
    skipped = 0
    for row in reader:
        date_str = _find_col(row, "date", "calendarDate", "Date") or ""
        date_str = date_str[:10]
        if not date_str or len(date_str) < 10:
            skipped += 1
            continue

        weekly_avg     = _try_float(_find_col(row, "weeklyAvgHrv",      "weeklyAverage", "Weekly Average"))
        last_night     = _try_float(_find_col(row, "lastNightAvg",       "lastNight",     "Last Night"))
        last_night_5m  = _try_float(_find_col(row, "lastNight5MinHigh",  "5MinuteHigh",   "5-Minute High"))
        baseline_low   = _try_float(_find_col(row, "baselineLowUpper",   "BaselineLow",   "Baseline Low"))
        baseline_high  = _try_float(_find_col(row, "baselineHighUpper",  "BaselineHigh",  "Baseline High"))
        status         = _find_col(row, "statusKey", "status", "Status") or ""

        rows_out.append({
            "date":               date_str,
            "weekly_avg":         weekly_avg,
            "last_night":         last_night,
            "last_night_5min_high": last_night_5m,
            "baseline_low":       baseline_low,
            "baseline_high":      baseline_high,
            "status":             status,
            "synced_at":          synced_at,
        })

    if rows_out:
        upsert_hrv_rows(rows_out)
    return len(rows_out), skipped


def _import_epoch_summary(reader: csv.DictReader, synced_at: str) -> tuple[int, int]:
    """
    Parse wellness epoch summaries (15-min intervals).
    Produces:
      • body_battery rows  — one per epoch with bodyBatteryAtEnd value
      • daily_summary rows — aggregated per calendarDate
    """
    # Accumulate per day
    by_day: dict[str, dict] = defaultdict(lambda: {
        "steps": 0, "calories_total": 0,
        "bb_high": None, "bb_low": None,
        "stress_samples": [], "max_stress": None,
        "resting_hr": None,
    })
    bb_rows: list[dict] = []
    skipped = 0
    total_epochs = 0

    for row in reader:
        date_str = _find_col(row, "calendarDate") or ""
        date_str = date_str[:10]
        if not date_str or len(date_str) < 10:
            skipped += 1
            continue

        d = by_day[date_str]

        steps    = _try_int(_find_col(row, "steps")) or 0
        calories = _try_int(_find_col(row, "calories")) or 0
        d["steps"] += steps
        d["calories_total"] += calories

        bb_high = _try_int(_find_col(row, "bodyBatteryHighest", "bodyBatteryAtStart"))
        bb_low  = _try_int(_find_col(row, "bodyBatteryLowest"))
        bb_end  = _try_int(_find_col(row, "bodyBatteryAtEnd", "bodyBatteryHighest"))

        if bb_high and bb_high > 0:
            d["bb_high"] = max(d["bb_high"] or 0, bb_high)
        if bb_low and bb_low > 0:
            d["bb_low"] = min(d["bb_low"] if d["bb_low"] is not None else 999, bb_low)

        stress = _try_int(_find_col(row, "averageStressLevel"))
        max_stress = _try_int(_find_col(row, "maxStressLevel"))
        if stress and stress > 0:
            d["stress_samples"].append(stress)
        if max_stress and max_stress > 0:
            d["max_stress"] = max(d["max_stress"] or 0, max_stress)

        rhr = _try_int(_find_col(row, "restingHeartRate"))
        if rhr and rhr > 0 and d["resting_hr"] is None:
            d["resting_hr"] = rhr

        # Body battery curve point
        ts_raw = _find_col(row, "startTimeGMT", "startTime") or ""
        if ts_raw and bb_end and bb_end > 0:
            bb_rows.append({
                "timestamp": _norm_ts(ts_raw),
                "date":      date_str,
                "value":     bb_end,
                "synced_at": synced_at,
            })

        total_epochs += 1

    # Build daily summary rows
    daily_rows = []
    for date_str, d in by_day.items():
        avg_stress = (
            round(sum(d["stress_samples"]) / len(d["stress_samples"]))
            if d["stress_samples"] else None
        )
        daily_rows.append({
            "date":             date_str,
            "steps":            d["steps"] or None,
            "calories_total":   d["calories_total"] or None,
            "calories_active":  None,
            "distance_meters":  None,
            "resting_hr":       d["resting_hr"],
            "avg_stress":       avg_stress,
            "max_stress":       d["max_stress"],
            "body_battery_high": d["bb_high"],
            "body_battery_low":  d["bb_low"],
            "synced_at":        synced_at,
        })

    if daily_rows:
        upsert_daily_summary_rows(daily_rows)
    if bb_rows:
        upsert_body_battery_rows(bb_rows)

    return total_epochs, skipped


def _import_daily_summary(reader: csv.DictReader, synced_at: str) -> tuple[int, int]:
    """Parse Garmin wellness daily summary CSV (wellnessDailySummaries.csv)."""
    rows_out = []
    skipped = 0
    for row in reader:
        date_str = _find_col(row, "calendarDate", "Date") or ""
        date_str = date_str[:10]
        if not date_str or len(date_str) < 10:
            skipped += 1
            continue

        steps   = _try_int(_find_col(row, "totalSteps", "steps", "Steps"))
        cal_tot = _try_int(_find_col(row, "totalKilocalories", "calories", "Calories"))
        cal_act = _try_int(_find_col(row, "activeKilocalories", "activeCalories", "Active Calories"))
        dist    = _try_float(_find_col(row, "totalDistanceMeters", "distance", "Distance"))
        rhr     = _try_int(_find_col(row, "restingHeartRate", "minHeartRate", "Resting Heart Rate"))
        avg_str = _try_int(_find_col(row, "averageStressLevel", "avgStress", "Avg Stress"))
        max_str = _try_int(_find_col(row, "maxStressLevel", "maxStress", "Max Stress"))
        bb_high = _try_int(_find_col(row, "bodyBatteryHighest", "Body Battery High"))
        bb_low  = _try_int(_find_col(row, "bodyBatteryLowest",  "Body Battery Low"))

        rows_out.append({
            "date":             date_str,
            "steps":            steps,
            "calories_total":   cal_tot,
            "calories_active":  cal_act,
            "distance_meters":  dist,
            "resting_hr":       rhr if rhr and rhr > 0 else None,
            "avg_stress":       avg_str if avg_str and avg_str > 0 else None,
            "max_stress":       max_str if max_str and max_str > 0 else None,
            "body_battery_high": bb_high if bb_high and bb_high > 0 else None,
            "body_battery_low":  bb_low  if bb_low  and bb_low  > 0 else None,
            "synced_at":        synced_at,
        })

    if rows_out:
        upsert_daily_summary_rows(rows_out)
    return len(rows_out), skipped


# ── JSON importers (Garmin full data export) ──────────────────────────────────

def _detect_json_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith("_sleepdata.json"):
        return "sleep"
    if name.startswith("udsfile_"):
        return "daily_summary"
    if name.endswith("_healthstatusdata.json"):
        return "hrv"
    if name.endswith("_summarizedactivities.json"):
        return "activities"
    return "unknown"


def _import_sleep_json(records: list, synced_at: str) -> tuple[int, int]:
    rows = []
    for r in records:
        date_str = (r.get("calendarDate") or "")[:10]
        if not date_str:
            continue
        deep  = r.get("deepSleepSeconds")
        light = r.get("lightSleepSeconds")
        rem   = r.get("remSleepSeconds")
        awake = r.get("awakeSleepSeconds")
        duration = sum(v or 0 for v in [deep, light, rem, awake]) or None
        score = (r.get("sleepScores") or {}).get("overallScore")
        rows.append({
            "date":             date_str,
            "start_time":       r.get("sleepStartTimestampGMT"),
            "end_time":         r.get("sleepEndTimestampGMT"),
            "duration_seconds": duration,
            "deep_seconds":     deep,
            "light_seconds":    light,
            "rem_seconds":      rem,
            "awake_seconds":    awake,
            "score":            score,
            "avg_hrv":          None,
            "synced_at":        synced_at,
        })
    if rows:
        upsert_sleep_rows(rows)
        try:
            upsert_chunks([
                {"id": f"{r['date']}_sleep", "text": chunk_sleep(r), "metadata": {"date": r["date"], "type": "sleep"}}
                for r in rows
            ])
        except Exception as e:
            logger.warning("Vector store upsert failed (sleep): %s", e)
    return len(rows), 0


def _import_uds_json(records: list, synced_at: str) -> tuple[int, int]:
    rows = []
    for r in records:
        date_str = (r.get("calendarDate") or "")[:10]
        if not date_str:
            continue

        stress_total = next(
            (a for a in (r.get("allDayStress") or {}).get("aggregatorList", [])
             if a.get("type") == "TOTAL"),
            {}
        )

        bb_stats = {
            s["bodyBatteryStatType"]: s["statsValue"]
            for s in (r.get("bodyBattery") or {}).get("bodyBatteryStatList", [])
            if "bodyBatteryStatType" in s and "statsValue" in s
        }

        rows.append({
            "date":             date_str,
            "steps":            r.get("totalSteps"),
            "calories_total":   _try_int(r.get("totalKilocalories")),
            "calories_active":  _try_int(r.get("activeKilocalories")),
            "distance_meters":  r.get("totalDistanceMeters"),
            "resting_hr":       r.get("restingHeartRate") or None,
            "avg_stress":       stress_total.get("averageStressLevel"),
            "max_stress":       stress_total.get("maxStressLevel"),
            "body_battery_high": bb_stats.get("HIGHEST"),
            "body_battery_low":  bb_stats.get("LOWEST"),
            "synced_at":        synced_at,
        })
    if rows:
        upsert_daily_summary_rows(rows)
        try:
            upsert_chunks([
                {"id": f"{r['date']}_daily", "text": chunk_daily(r), "metadata": {"date": r["date"], "type": "daily"}}
                for r in rows
            ])
        except Exception as e:
            logger.warning("Vector store upsert failed (daily): %s", e)
    return len(rows), 0


def _import_health_status_json(records: list, synced_at: str) -> tuple[int, int]:
    rows = []
    for r in records:
        date_str = (r.get("calendarDate") or "")[:10]
        if not date_str:
            continue
        hrv = next(
            (m for m in r.get("metrics", []) if m.get("type") == "HRV"),
            None
        )
        if not hrv:
            continue
        rows.append({
            "date":               date_str,
            "weekly_avg":         None,
            "last_night":         hrv.get("value"),
            "last_night_5min_high": None,
            "baseline_low":       hrv.get("baselineLowerLimit"),
            "baseline_high":      hrv.get("baselineUpperLimit"),
            "status":             hrv.get("status") or "",
            "synced_at":          synced_at,
        })
    if rows:
        upsert_hrv_rows(rows)
        try:
            upsert_chunks([
                {"id": f"{r['date']}_hrv", "text": chunk_hrv(r), "metadata": {"date": r["date"], "type": "hrv"}}
                for r in rows
            ])
        except Exception as e:
            logger.warning("Vector store upsert failed (hrv): %s", e)
    return len(rows), 0


def _import_activities_json(records: list, synced_at: str) -> tuple[int, int]:
    # Unwrap outer envelope from summarizedActivities export
    if records and isinstance(records[0], dict) and "summarizedActivitiesExport" in records[0]:
        activities = records[0]["summarizedActivitiesExport"]
    else:
        activities = records

    inserted = skipped = 0
    chunk_rows: list[dict] = []
    with get_conn() as conn:
        for r in activities:
            try:
                act_id = r.get("activityId")
                if not act_id:
                    skipped += 1
                    continue

                start_ms = r.get("startTimeGmt")
                if not start_ms:
                    skipped += 1
                    continue
                act_date = datetime.utcfromtimestamp(float(start_ms) / 1000).strftime("%Y-%m-%d")

                duration_ms = r.get("duration") or r.get("elapsedDuration") or 0
                duration_s  = int(float(duration_ms) / 1000) if duration_ms else None
                name = r.get("name") or (r.get("activityType") or "unknown")
                act_type = (r.get("activityType") or "unknown").lower()
                dist = r.get("distance")
                avg_hr = _try_int(r.get("avgHr"))
                max_hr = _try_int(r.get("maxHr"))
                calories = _try_int(r.get("calories"))

                conn.execute("""
                    INSERT OR REPLACE INTO activities
                    (activity_id, date, name, type, duration_seconds,
                     distance_meters, avg_hr, max_hr, calories, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (act_id, act_date, name, act_type, duration_s,
                      dist, avg_hr, max_hr, calories, synced_at))
                inserted += 1
                chunk_rows.append({
                    "id": f"{act_id}_activity",
                    "date": act_date, "name": name, "type": act_type,
                    "duration_seconds": duration_s, "distance_meters": dist,
                    "avg_hr": avg_hr, "max_hr": max_hr, "calories": calories,
                })
            except Exception:
                skipped += 1

    try:
        upsert_chunks([
            {"id": row["id"], "text": chunk_activity(row), "metadata": {"date": row["date"], "type": "activity"}}
            for row in chunk_rows
        ])
    except Exception as e:
        logger.warning("Vector store upsert failed (activities): %s", e)

    return inserted, skipped


def _dispatch_json(data: list | dict, filename: str, synced_at: str) -> dict:
    json_type = _detect_json_type(filename)
    logger.info("JSON import: %s detected as '%s'", filename, json_type)

    if not isinstance(data, list):
        return {"type": "unknown", "inserted": 0, "skipped": 0,
                "error": "expected JSON array"}

    if json_type == "sleep":
        ins, skip = _import_sleep_json(data, synced_at)
    elif json_type == "daily_summary":
        ins, skip = _import_uds_json(data, synced_at)
    elif json_type == "hrv":
        ins, skip = _import_health_status_json(data, synced_at)
    elif json_type == "activities":
        ins, skip = _import_activities_json(data, synced_at)
    else:
        return {"type": "unknown", "inserted": 0, "skipped": 0}

    return {"type": json_type, "inserted": ins, "skipped": skip}


def _dispatch_csv(text: str, filename: str, synced_at: str) -> dict:
    """Auto-detect CSV type and import. Returns {type, inserted, skipped}."""
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    if not headers:
        return {"type": "unknown", "inserted": 0, "skipped": 0, "error": "no headers"}

    csv_type = _detect_csv_type(headers)
    logger.info("CSV import: %s detected as '%s'", filename, csv_type)

    if csv_type == "activities":
        ins, skip = _import_activities(reader, synced_at)
    elif csv_type == "sleep":
        ins, skip = _import_sleep(reader, synced_at)
    elif csv_type == "hrv":
        ins, skip = _import_hrv(reader, synced_at)
    elif csv_type == "epoch_summary":
        ins, skip = _import_epoch_summary(reader, synced_at)
    elif csv_type == "daily_summary":
        ins, skip = _import_daily_summary(reader, synced_at)
    else:
        return {"type": "unknown", "inserted": 0, "skipped": 0,
                "error": f"unrecognized CSV format (headers: {headers[:6]})"}

    return {"type": csv_type, "inserted": ins, "skipped": skip}


@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...)):
    """
    Import any recognized Garmin CSV.

    Supported: activities export, sleep data, HRV summary,
    wellness epoch summaries, wellness daily summaries.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="File must be a .csv")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    synced_at = datetime.utcnow().isoformat()
    result = _dispatch_csv(text, file.filename, synced_at)
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@app.post("/api/import/export")
async def import_export(file: UploadFile = File(...)):
    """
    Import a Garmin Connect full data export zip.

    Download from Garmin Connect → Account → Data Management → Export Your Data.
    The zip typically contains activities, sleep, HRV, and epoch wellness CSVs.
    """
    fname = file.filename or ""
    if not fname.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="File must be a .zip")

    content = await file.read()
    synced_at = datetime.utcnow().isoformat()
    results: list[dict] = []
    unrecognized: list[str] = []

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Not a valid zip file")

    for entry in zf.infolist():
        name = entry.filename
        short_name = name.split("/")[-1]
        is_csv  = name.lower().endswith(".csv")
        is_json = name.lower().endswith(".json")

        if entry.is_dir() or not (is_csv or is_json):
            continue

        try:
            raw = zf.read(name)
        except Exception as e:
            logger.warning("Could not read %s from zip: %s", name, e)
            continue

        if is_json:
            try:
                data = json.loads(raw.decode("utf-8-sig"))
            except Exception as e:
                logger.warning("Could not parse JSON %s: %s", name, e)
                continue
            result = _dispatch_json(data, short_name, synced_at)
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            result = _dispatch_csv(text, short_name, synced_at)

        if result.get("type") == "unknown":
            unrecognized.append(short_name)
        else:
            result["file"] = short_name
            results.append(result)
            logger.info("Imported %s: %d rows", short_name, result["inserted"])

    total_inserted = sum(r["inserted"] for r in results)
    return {
        "files_processed": len(results),
        "files_unrecognized": len(unrecognized),
        "total_inserted": total_inserted,
        "detail": results,
    }


@app.post("/api/import/bloodwork")
async def import_bloodwork(file: UploadFile = File(...)):
    """
    Import a LabCorp PDF bloodwork report.

    Parses structured results into SQLite and prose chunks into the vector store.
    Re-uploading the same date's results upserts (no duplicates).
    """
    fname = file.filename or ""
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="File must be a .pdf")

    content = await file.read()
    synced_at = datetime.utcnow().isoformat()

    try:
        parsed = parse_labcorp(content)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")

    report_date = parsed["date"]
    if not report_date:
        raise HTTPException(status_code=422, detail="Could not detect a date in the PDF. Is this a LabCorp report?")

    # ── SQLite upsert ─────────────────────────────────────────────────────────
    db_rows: list[dict] = []
    for panel_block in parsed["panels"]:
        panel = panel_block["panel"]
        for r in panel_block["results"]:
            db_rows.append({
                "date":      report_date,
                "panel":     panel,
                "marker":    r["marker"],
                "value":     r["value"],
                "unit":      r["unit"],
                "ref_low":   r["ref_low"],
                "ref_high":  r["ref_high"],
                "flag":      r["flag"],
                "raw_ref":   r["ref"],
                "synced_at": synced_at,
            })
    if db_rows:
        upsert_bloodwork_rows(db_rows)

    # ── Vector store upsert ───────────────────────────────────────────────────
    chunks: list[dict] = []

    # One chunk per panel (structured results if parsed, raw text fallback)
    if parsed["panels"]:
        for panel_block in parsed["panels"]:
            chunk_id = f"{report_date}_{panel_block['panel'].lower().replace(' ', '_')}_bloodwork"
            text = chunk_bloodwork_panel(report_date, panel_block["panel"], panel_block["results"])
            chunks.append({"id": chunk_id, "text": text, "metadata": {"date": report_date, "type": "bloodwork"}})
    else:
        # Fallback: chunk raw text by page-sized blocks
        raw = parsed["raw_text"]
        for i, block in enumerate(raw.split("\n\n")):
            block = block.strip()
            if len(block) > 40:
                chunks.append({
                    "id": f"{report_date}_bloodwork_raw_{i}",
                    "text": f"Bloodwork on {report_date}: {block}",
                    "metadata": {"date": report_date, "type": "bloodwork"},
                })

    try:
        upsert_chunks(chunks)
    except Exception as e:
        logger.warning("Vector store upsert failed (bloodwork): %s", e)

    return {
        "date":          report_date,
        "panels_found":  len(parsed["panels"]),
        "markers_parsed": parsed["parsed_count"],
        "db_rows":       len(db_rows),
        "chunks":        len(chunks),
        "note": "Re-upload any time — existing results for this date will be updated." if db_rows else
                "Structured parsing found 0 results. Raw text stored in vector store for search.",
    }


@app.get("/api/bloodwork")
def api_bloodwork(
    marker: str = Query(default=None, description="Filter by marker name (partial match)"),
    days: int = Query(default=730, ge=1, le=3650),
):
    """Return historical bloodwork. Omit marker for all results."""
    rows = get_bloodwork(marker=marker, days=days)
    dates = get_bloodwork_dates()
    return {"dates": dates, "count": len(rows), "results": rows}


@app.get("/api/search")
def api_search(
    q: str = Query(..., description="Natural language query"),
    n: int = Query(default=6, ge=1, le=20),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
):
    results = vs_search(q, n_results=n, start_date=start_date, end_date=end_date)
    return {"query": q, "count": len(results), "results": results}


@app.post("/api/reindex")
def api_reindex():
    """Rebuild the vector store from all SQLite data. Use after bulk imports."""
    from db import get_conn as _conn
    from datetime import date as _date
    import sqlite3

    chunks: list[dict] = []
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM daily_summary").fetchall():
            r = dict(row)
            if r.get("date"):
                chunks.append({"id": f"{r['date']}_daily", "text": chunk_daily(r), "metadata": {"date": r["date"], "type": "daily"}})
        for row in conn.execute("SELECT * FROM sleep").fetchall():
            r = dict(row)
            if r.get("date"):
                chunks.append({"id": f"{r['date']}_sleep", "text": chunk_sleep(r), "metadata": {"date": r["date"], "type": "sleep"}})
        for row in conn.execute("SELECT * FROM hrv").fetchall():
            r = dict(row)
            if r.get("date"):
                chunks.append({"id": f"{r['date']}_hrv", "text": chunk_hrv(r), "metadata": {"date": r["date"], "type": "hrv"}})
        for row in conn.execute("SELECT * FROM activities").fetchall():
            r = dict(row)
            if r.get("date"):
                chunks.append({"id": f"{r['activity_id']}_activity", "text": chunk_activity(r), "metadata": {"date": r["date"], "type": "activity"}})

    upsert_chunks(chunks)
    return {"indexed": len(chunks), "total_chunks": chunk_count()}


@app.get("/health")
def health_check():
    last = get_last_sync()
    return {"status": "ok", "last_sync": last}
