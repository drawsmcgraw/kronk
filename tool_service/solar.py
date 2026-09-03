"""SunPower PVS solar monitoring — fetch, detect, confirm, alert.

Design in docs/plans/SOLAR_MONITOR_PLAN.md. Two jobs:
  1. On-demand snapshot for the `solar_status` skill (live, momentary).
  2. A background poll loop that records per-inverter readings and, over
     MULTIPLE DAYS, confirms a genuinely failing inverter before alerting
     (certainty over speed — no cloud-triggered false alarms).

Data source (PVS5, confirmed 2026-07-14): the PVS varserver behind the
HAProxy bridge. Auth `GET /auth?login` (Basic ssm_owner:<serial>) → session;
then `GET /vars?match=inverter` with a session cookie. Detection is
deterministic; the LLM only narrates summaries/alerts.
"""
import base64
import json
import logging
import os
import re
import sqlite3
import ssl
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("tool_service.solar")

SOLAR_HOST   = os.getenv("SOLAR_HOST", "http://sunpower-bridge.home.hippiehouse.net")
SOLAR_SERIAL = os.getenv("SOLAR_SERIAL", "")
SOLAR_DB     = Path(os.getenv("SOLAR_DB", "/data/solar.db"))

# Detection thresholds (all env-tunable — see plan doc).
PRODUCING_FLOOR = float(os.getenv("SOLAR_PRODUCING_FLOOR", "0.03"))  # array-median kW gate
FAIL_RATIO      = float(os.getenv("SOLAR_FAIL_RATIO", "0.40"))       # inv/median below this = under
MARGINAL_BAND   = float(os.getenv("SOLAR_MARGINAL_BAND", "0.15"))    # width above FAIL_RATIO = "marginal" (flickers)
MIN_SAMPLES     = int(os.getenv("SOLAR_MIN_SAMPLES", "8"))           # producing samples to judge a day
BAD_FRACTION    = float(os.getenv("SOLAR_BAD_FRACTION", "0.70"))     # >70% of day's samples under = bad day
CONFIRM_DAYS    = int(os.getenv("SOLAR_CONFIRM_DAYS", "3"))          # consecutive bad days → alert
POLL_MIN        = int(os.getenv("SOLAR_POLL_MIN", "15"))
SOLAR_TZ        = os.getenv("SOLAR_TZ", "America/New_York")            # for "today"/period boundaries

# HA (shared with music/mirror announce).
HA_URL   = os.getenv("HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE   # PVS self-signed; bridge is on the LAN


class SolarError(Exception):
    """Fetch/auth failure — carries a specific reason."""


# ── PVS varserver client (session cached, re-login on 401/403) ────────────────

_session: str | None = None


def _auth_header(serial: str) -> str:
    return "Basic " + base64.b64encode(f"ssm_owner:{serial}".encode()).decode()


async def _login(client: httpx.AsyncClient) -> str:
    global _session
    if not SOLAR_SERIAL:
        raise SolarError("SOLAR_SERIAL not configured")
    r = await client.get(f"{SOLAR_HOST}/auth?login",
                         headers={"Authorization": _auth_header(SOLAR_SERIAL)})
    if r.status_code != 200:
        raise SolarError(f"PVS auth failed (HTTP {r.status_code}): {r.text[:150]}")
    sess = (r.json() or {}).get("session")
    if not sess:
        raise SolarError("PVS auth returned no session token")
    _session = sess
    return sess


async def _get_vars(match: str) -> list[dict]:
    """GET /vars?match=<match> with the cached session; re-login once on 401/403."""
    global _session
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        if not _session:
            await _login(client)
        for attempt in (1, 2):
            r = await client.get(f"{SOLAR_HOST}/vars",
                                 params={"match": match},
                                 headers={"Cookie": f"session={_session}"})
            if r.status_code in (401, 403) and attempt == 1:
                logger.info("solar: session expired, re-authenticating")
                _session = None
                await _login(client)
                continue
            if r.status_code != 200:
                raise SolarError(f"PVS /vars?match={match} → HTTP {r.status_code}: {r.text[:150]}")
            return (r.json() or {}).get("values", [])
    return []


# ── parsing (pure) ────────────────────────────────────────────────────────────

_INV_RE = re.compile(r"^/sys/devices/inverter/(\d+)/(\w+)$")


def parse_inverters(values: list[dict]) -> dict[str, dict]:
    """varserver /vars?match=inverter values → {sn: {power, vmppt, temp}}.

    Keyed by serial (the stable roster key), not index — indices can shift."""
    by_idx: dict[str, dict] = {}
    for v in values:
        m = _INV_RE.match(v.get("name", ""))
        if m:
            by_idx.setdefault(m.group(1), {})[m.group(2)] = v.get("value")

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    out: dict[str, dict] = {}
    for fields in by_idx.values():
        sn = fields.get("sn")
        if not sn:
            continue
        out[str(sn)] = {"power": _f(fields.get("p3phsumKw")),
                        "vmppt": _f(fields.get("vMppt1V")),
                        "temp":  _f(fields.get("tHtsnkDegc"))}
    return out


def parse_total_kw(values: list[dict]) -> float | None:
    for v in values:
        if v.get("name") == "/sys/livedata/pv_p":
            try:
                return round(float(v.get("value")), 2)
            except (TypeError, ValueError):
                return None
    return None


def parse_lifetime_energy(values: list[dict]) -> float | None:
    """System lifetime energy counter (kWh) from /vars?match=livedata."""
    for v in values:
        if v.get("name") == "/sys/livedata/pv_en":
            try:
                return float(v.get("value"))
            except (TypeError, ValueError):
                return None
    return None


_COUNTER_NAMES = {
    "/sys/livedata/pv_en":        "pv_en",
    "/sys/livedata/site_load_en": "site_load_en",
    "/sys/livedata/net_en":       "net_en",
}


def parse_counters(values: list[dict]) -> dict:
    """All lifetime counters from /vars?match=livedata. site_load_en and
    net_en are recorded as BASELINE even though this install mirrors them
    (no consumption CTs — probed 2026-08-27, values byte-identical):
    history starts when snapshots start, and CTs may get wired later.
    Consumers gate on consumption_data_real()."""
    out = {"pv_en": None, "site_load_en": None, "net_en": None}
    for v in values:
        key = _COUNTER_NAMES.get(v.get("name"))
        if key:
            try:
                out[key] = float(v.get("value"))
            except (TypeError, ValueError):
                pass
    return out


def parse_inverter_lifetime(values: list[dict]) -> dict[str, float]:
    """Per-inverter lifetime energy (kWh) → {sn: ltea3phsumKwh}, from
    /vars?match=inverter. Keyed by serial like parse_inverters."""
    by_idx: dict[str, dict] = {}
    for v in values:
        m = _INV_RE.match(v.get("name", ""))
        if m:
            by_idx.setdefault(m.group(1), {})[m.group(2)] = v.get("value")
    out: dict[str, float] = {}
    for fields in by_idx.values():
        sn = fields.get("sn")
        try:
            out[str(sn)] = float(fields.get("ltea3phsumKwh"))
        except (TypeError, ValueError):
            continue
    return out


def classify_status(ratio: float | None) -> str:
    """Granular current status from the inverter's ratio-to-median:
      underperforming — below the fail line (counts as bad right now);
      marginal        — just above it (within MARGINAL_BAND), so it flickers
                        in and out of the underperforming set as sunlight/
                        the array median shifts — this is what makes the live
                        'N failing' count change moment to moment;
      healthy         — comfortably above;
      unknown         — no reading."""
    if ratio is None:
        return "unknown"
    if ratio < FAIL_RATIO:
        return "underperforming"
    if ratio < FAIL_RATIO + MARGINAL_BAND:
        return "marginal"
    return "healthy"


def array_median_power(inverters: dict[str, dict]) -> float:
    powers = [d["power"] for d in inverters.values() if d.get("power") is not None]
    return median(powers) if powers else 0.0


def flag_underperformers(inverters: dict[str, dict], med: float) -> list[str]:
    """Inverters below FAIL_RATIO × array median right now. Momentary — used
    for the live snapshot only, never for alerting. Corroborate with the
    elevated MPPT voltage (a faulted inverter open-circuits its panel)."""
    if med <= 0:
        return []
    flagged = []
    for sn, d in inverters.items():
        p = d.get("power")
        if p is not None and p < FAIL_RATIO * med:
            flagged.append(sn)
    return flagged


# ── snapshot (live, for the skill) ────────────────────────────────────────────

async def fetch_snapshot() -> dict:
    inv_values = await _get_vars("inverter")
    inverters = parse_inverters(inv_values)
    med = array_median_power(inverters)
    under = flag_underperformers(inverters, med)
    try:
        total = parse_total_kw(await _get_vars("livedata"))
    except SolarError:
        total = None
    confirmed = confirmed_failing()  # from the multi-day state (DB)
    return {
        "total_kw": total,
        "inverter_count": len(inverters),
        "live_underperforming": sorted(under),
        "confirmed_failing": confirmed,
        "array_median_kw": round(med, 4),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


# ── storage + multi-day certainty machine ─────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(SOLAR_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    SOLAR_DB.parent.mkdir(parents=True, exist_ok=True)
    with _db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS readings (
                ts REAL NOT NULL, day TEXT NOT NULL, sn TEXT NOT NULL,
                power REAL, vmppt REAL, temp REAL, array_median REAL,
                producing INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_readings_day ON readings(day);
            CREATE TABLE IF NOT EXISTS days_rolled (day TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS inverter_state (
                sn TEXT PRIMARY KEY,
                bad_days INTEGER NOT NULL DEFAULT 0,
                confirmed INTEGER NOT NULL DEFAULT 0,
                last_verdict TEXT, updated_at TEXT
            );
            -- One row per poll: a snapshot of the PVS lifetime energy counter.
            -- Lifetime totals are read live (never stored); these snapshots
            -- exist only to answer "energy over a PAST period" — the PVS keeps
            -- no history of its own counter, so a boundary value is gone unless
            -- we recorded it. Separate table (not on `readings`) because
            -- `readings` holds one row per inverter per poll (24×); pv_en is a
            -- single system value per poll.
            CREATE TABLE IF NOT EXISTS energy (
                ts REAL PRIMARY KEY, day TEXT NOT NULL, pv_en REAL NOT NULL
            );
        """)
        # 2026-08-27: consumption/net counter baseline (SOLAR_VIZ_PLAN).
        # Idempotent widening; pre-existing rows stay NULL.
        for col in ("site_load_en", "net_en"):
            try:
                c.execute(f"ALTER TABLE energy ADD COLUMN {col} REAL")
            except sqlite3.OperationalError:
                pass  # column already present


def record_poll(inverters: dict[str, dict], med: float, ts: float | None = None) -> None:
    """Append one poll's per-inverter readings. `producing` marks whether the
    array was generating (median above the floor) — only producing samples
    get a daily verdict."""
    ts = ts if ts is not None else time.time()
    day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    producing = 1 if med > PRODUCING_FLOOR else 0
    with _db() as c:
        c.executemany(
            "INSERT INTO readings (ts, day, sn, power, vmppt, temp, array_median, producing) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(ts, day, sn, d.get("power"), d.get("vmppt"), d.get("temp"), med, producing)
             for sn, d in inverters.items()])


# ── energy: live lifetime + past-period diffs from snapshots ──────────────────

def record_energy(pv_en: float | None, ts: float | None = None,
                  site_load_en: float | None = None,
                  net_en: float | None = None) -> None:
    """Snapshot the lifetime counters for one poll (skip if pv read failed)."""
    if pv_en is None:
        return
    ts = ts if ts is not None else time.time()
    day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO energy (ts, day, pv_en, site_load_en, net_en) "
                  "VALUES (?,?,?,?,?)",
                  (ts, day, pv_en, site_load_en, net_en))


def _snapshot_near(ts: float) -> tuple[float, float] | None:
    """(pv_en, ts) of the stored snapshot closest to `ts`, or None if empty."""
    with _db() as c:
        row = c.execute(
            "SELECT pv_en, ts FROM energy ORDER BY ABS(ts - ?) ASC LIMIT 1",
            (ts,)).fetchone()
    return (row["pv_en"], row["ts"]) if row else None


def _earliest_snapshot() -> tuple[float, float] | None:
    with _db() as c:
        row = c.execute(
            "SELECT pv_en, ts FROM energy ORDER BY ts ASC LIMIT 1").fetchone()
    return (row["pv_en"], row["ts"]) if row else None


async def fetch_lifetime() -> dict:
    """Live lifetime energy — system total and per-inverter. No storage."""
    total = parse_lifetime_energy(await _get_vars("livedata"))
    per_inv = parse_inverter_lifetime(await _get_vars("inverter"))
    return {
        "lifetime_kwh": round(total, 1) if total is not None else None,
        "per_inverter": {sn: round(v, 1) for sn, v in per_inv.items()},
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _period_bounds(period: str, now_local: datetime) -> tuple[datetime, datetime | None]:
    """Resolve a period name to (start, end) in local time. end=None means
    'up to now' (use the live counter). Raises ValueError on an unknown name."""
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    p = period.strip().lower()
    if p == "today":
        return midnight, None
    if p == "yesterday":
        return midnight - timedelta(days=1), midnight
    if p in ("week", "this week", "7d", "7 days"):
        return now_local - timedelta(days=7), None
    if p in ("month", "this month", "30d", "30 days"):
        return now_local - timedelta(days=30), None
    if p.startswith("since:"):
        d = datetime.fromisoformat(p[len("since:"):].strip())
        if d.tzinfo is None:
            d = d.replace(tzinfo=now_local.tzinfo)
        return d, None
    raise ValueError(f"unknown period: {period!r}")


async def energy_for_period(period: str) -> dict:
    """Energy (kWh) produced over a named past period, as counter(end) −
    counter(start). `start`/`end` past boundaries come from stored snapshots;
    an open end uses the live counter. If the requested start predates our
    earliest snapshot, the baseline is clamped to it and `clamped_to` names the
    real start — honest about what we can and can't know (the PVS can't recover
    it either)."""
    tz = ZoneInfo(SOLAR_TZ)
    now_local = datetime.now(tz)
    try:
        start, end = _period_bounds(period, now_local)
    except ValueError as e:
        return {"period": period, "error": str(e)}

    earliest = _earliest_snapshot()
    if earliest is None:
        return {"period": period, "error": "no energy history yet — tracking just started"}

    start_ts = start.timestamp()
    base = _snapshot_near(start_ts)
    clamped_to = None
    # If the period starts before we have any data, clamp to the first snapshot.
    if base[1] > start_ts + POLL_MIN * 60:
        clamped_to = datetime.fromtimestamp(base[1], tz).isoformat()

    if end is None:
        end_kwh = parse_lifetime_energy(await _get_vars("livedata"))
        end_ts = now_local.timestamp()
        if end_kwh is None:
            return {"period": period, "error": "could not read the live PVS counter"}
    else:
        e = _snapshot_near(end.timestamp())
        end_kwh, end_ts = e[0], e[1]

    return {
        "period": period,
        "kwh": round(end_kwh - base[0], 1),
        "from": datetime.fromtimestamp(base[1], tz).isoformat(),
        "to": datetime.fromtimestamp(end_ts, tz).isoformat(),
        "clamped_to": clamped_to,
    }


def _day_verdicts(day: str) -> dict[str, bool] | None:
    """Per-inverter bad(True)/good(False) for a day, or None if the day had
    too few PRODUCING samples to judge (cloudy/short day → no verdict)."""
    with _db() as c:
        rows = c.execute(
            "SELECT sn, power, array_median FROM readings "
            "WHERE day=? AND producing=1", (day,)).fetchall()
        roster = [r["sn"] for r in c.execute(
            "SELECT DISTINCT sn FROM readings WHERE day=?", (day,)).fetchall()]
    if not rows:
        return None
    # group producing samples per inverter
    per: dict[str, list[tuple[float, float]]] = {}
    n_samples = 0
    for r in rows:
        per.setdefault(r["sn"], []).append((r["power"], r["array_median"]))
    n_samples = max((len(v) for v in per.values()), default=0)
    if n_samples < MIN_SAMPLES:
        return None
    verdicts: dict[str, bool] = {}
    for sn in roster:
        samples = per.get(sn, [])
        if not samples:
            verdicts[sn] = True   # present in roster but no producing reads = bad
            continue
        under = sum(1 for p, m in samples
                    if m > 0 and p is not None and p < FAIL_RATIO * m)
        verdicts[sn] = (under / len(samples)) > BAD_FRACTION
    return verdicts


def rollup_and_confirm(now_day: str | None = None) -> list[dict]:
    """Roll up every completed, not-yet-rolled day; advance each inverter's
    consecutive-bad-day counter. Returns the state transitions made
    (`confirmed_failing` / `recovered`) — the caller sends the HA alerts, so
    detection stays pure/sync and testable. The `confirmed` flag gates
    re-firing, so a confirmed inverter alerts exactly once per episode."""
    today = now_day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    transitions = []
    with _db() as c:
        days = [r["day"] for r in c.execute(
            "SELECT DISTINCT day FROM readings WHERE day < ? "
            "AND day NOT IN (SELECT day FROM days_rolled) ORDER BY day", (today,)).fetchall()]
    for day in days:
        verdicts = _day_verdicts(day)
        if verdicts is None:
            with _db() as c:   # unjudgeable day: mark rolled, don't touch counters
                c.execute("INSERT OR IGNORE INTO days_rolled (day) VALUES (?)", (day,))
            continue
        for sn, bad in verdicts.items():
            t = _advance(sn, bad)
            if t:
                transitions.append(t)
        with _db() as c:
            c.execute("INSERT OR IGNORE INTO days_rolled (day) VALUES (?)", (day,))
    return transitions


def _advance(sn: str, bad: bool) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT * FROM inverter_state WHERE sn=?", (sn,)).fetchone()
        bad_days = (row["bad_days"] if row else 0)
        confirmed = bool(row["confirmed"]) if row else False
        bad_days = bad_days + 1 if bad else 0
        transition = None
        if bad_days >= CONFIRM_DAYS and not confirmed:
            confirmed = True
            transition = {"sn": sn, "event": "confirmed_failing", "days": bad_days}
        elif not bad and confirmed:
            confirmed = False   # recovered
            transition = {"sn": sn, "event": "recovered", "days": 0}
        c.execute(
            "INSERT INTO inverter_state (sn, bad_days, confirmed, last_verdict, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(sn) DO UPDATE SET "
            "bad_days=excluded.bad_days, confirmed=excluded.confirmed, "
            "last_verdict=excluded.last_verdict, updated_at=excluded.updated_at",
            (sn, bad_days, int(confirmed),
             "bad" if bad else "good", datetime.now(timezone.utc).isoformat()))
    return transition


def confirmed_failing() -> list[dict]:
    try:
        with _db() as c:
            rows = c.execute(
                "SELECT sn, bad_days FROM inverter_state WHERE confirmed=1").fetchall()
        return [{"sn": r["sn"], "days": r["bad_days"]} for r in rows]
    except sqlite3.OperationalError:
        return []


def _inverter_states() -> dict[str, dict]:
    try:
        with _db() as c:
            return {r["sn"]: {"bad_days": r["bad_days"], "confirmed": bool(r["confirmed"]),
                              "last_verdict": r["last_verdict"]}
                    for r in c.execute("SELECT * FROM inverter_state").fetchall()}
    except sqlite3.OperationalError:
        return {}


def _daily_history(sn: str, days: int) -> list[dict]:
    """Recent per-day production for one inverter, oldest→newest. `ratio` is
    the day's avg power vs the day's avg array median — irradiance-normalized,
    so a persistently-failed inverter reads low every day while a marginal one
    wobbles (the distinction behind "4 failing, now 2")."""
    try:
        with _db() as c:
            rows = c.execute(
                "SELECT day, AVG(power) ap, AVG(array_median) am, COUNT(*) n "
                "FROM readings WHERE sn=? AND producing=1 "
                "GROUP BY day ORDER BY day DESC LIMIT ?", (sn, days)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in reversed(rows):
        am = r["am"] or 0
        out.append({"day": r["day"], "avg_kw": round(r["ap"] or 0, 3),
                    "ratio": round((r["ap"] or 0) / am, 2) if am > 0 else None,
                    "samples": r["n"]})
    return out


async def fetch_detail(history_days: int = 5) -> dict:
    """Per-inverter breakdown for analytical questions: current power/voltage/
    temp + ratio-to-median + multi-day state, plus a short daily history for
    the inverters that are underperforming now OR carrying bad-days (the ones
    a 'why did it change' question is actually about). Healthy inverters get
    current values only, to keep it focused."""
    inverters = parse_inverters(await _get_vars("inverter"))
    med = array_median_power(inverters)
    try:
        total = parse_total_kw(await _get_vars("livedata"))
    except SolarError:
        total = None
    states = _inverter_states()

    rows = []
    for sn, d in sorted(inverters.items(), key=lambda kv: (kv[1].get("power") is None,
                                                           kv[1].get("power") or 0)):
        p = d.get("power")
        ratio = round(p / med, 2) if (p is not None and med > 0) else None
        st = states.get(sn, {})
        status = classify_status(ratio)
        under = status == "underperforming"
        rec = {"sn": sn, "power_kw": p, "voltage_v": d.get("vmppt"),
               "temp_c": d.get("temp"), "ratio_to_median": ratio,
               "status": status,               # underperforming | marginal | healthy | unknown
               "underperforming_now": under,
               "bad_days": st.get("bad_days", 0),
               "confirmed_failing": st.get("confirmed", False)}
        # History for anything not clearly healthy — the flickering marginals
        # are exactly what a "why did the count change" question is about.
        if status in ("underperforming", "marginal") or st.get("bad_days", 0) > 0:
            rec["history"] = _daily_history(sn, history_days)
        rows.append(rec)

    return {
        "total_kw": total, "inverter_count": len(inverters),
        "array_median_kw": round(med, 4), "fail_ratio": FAIL_RATIO,
        "confirm_days": CONFIRM_DAYS,
        "inverters": rows,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


# ── HA persistent notification ────────────────────────────────────────────────

def _panel_hint(sn: str) -> str:
    return f"inverter …{sn[-6:]}"


async def notify_ha_failing(sn: str, days: int, total_kw: float | None = None) -> bool:
    """One HA persistent notification per confirmed failure (stable id → the
    same inverter updates rather than duplicating; recovery dismisses it)."""
    if not HA_TOKEN:
        logger.warning("solar alert skipped: HA_TOKEN not configured")
        return False
    out = f" Current array output {total_kw} kW." if total_kw is not None else ""
    msg = (f"Solar {_panel_hint(sn)} has produced near-zero power for {days} "
           f"days while its neighbors are normal — likely failed.{out}")
    return await _ha_service("persistent_notification", "create", {
        "notification_id": f"solar_inv_{sn}",
        "title": "Solar inverter likely failing",
        "message": msg,
    })


async def dismiss_ha(sn: str) -> bool:
    return await _ha_service("persistent_notification", "dismiss",
                             {"notification_id": f"solar_inv_{sn}"})


async def _ha_service(domain: str, service: str, data: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{HA_URL}/api/services/{domain}/{service}",
                headers={"Authorization": f"Bearer {HA_TOKEN}",
                         "Content-Type": "application/json"},
                json=data)
        if r.status_code // 100 != 2:
            logger.error("HA %s.%s failed (HTTP %s): %s",
                         domain, service, r.status_code, r.text[:150])
            return False
        return True
    except Exception as e:
        logger.error("HA %s.%s failed: %s", domain, service, e)
        return False


# ── Viz series (docs/plans/SOLAR_VIZ_PLAN.md) ────────────────────────────────
# Read-only aggregations for the /solar dashboard page. Buckets are chosen
# by window so the payload stays small; verdicts are DERIVED from readings
# with the same rules as the daily rollup (no verdict-history table exists
# — derive, don't duplicate).

def _bucket_seconds(days: int) -> int:
    if days <= 2:
        return 900
    if days <= 14:
        return 3600
    return 86400


def power_series(days: int, sn: str | None = None) -> list[dict]:
    """Bucketed power series — system total (per-poll sum over inverters,
    averaged per bucket) or one inverter's power/vmppt/temp."""
    since = time.time() - days * 86400
    bs = _bucket_seconds(days)
    with _db() as c:
        if sn:
            rows = c.execute(
                "SELECT CAST(ts/? AS INTEGER) b, AVG(power), AVG(vmppt), AVG(temp) "
                "FROM readings WHERE ts >= ? AND sn = ? GROUP BY b ORDER BY b",
                (bs, since, sn)).fetchall()
            return [{"t": int(b * bs + bs / 2),
                     "kw":    round(p, 3) if p is not None else None,
                     "vmppt": round(v, 1) if v is not None else None,
                     "temp":  round(tp, 1) if tp is not None else None}
                    for b, p, v, tp in rows]
        rows = c.execute(
            "SELECT CAST(ts/? AS INTEGER) b, AVG(psum) FROM "
            "(SELECT ts, SUM(power) psum FROM readings WHERE ts >= ? GROUP BY ts) "
            "GROUP BY b ORDER BY b", (bs, since)).fetchall()
    return [{"t": int(b * bs + bs / 2), "kw": round(p, 3) if p is not None else None}
            for b, p in rows]


def daily_energy(days: int) -> list[dict]:
    """Per-day kWh: last snapshot of each stored (UTC) day diffed against
    the previous day's. UTC midnight is ~20:00 local — after generation
    ends — so daily figures track solar_energy's within rounding.
    load/net stay None until those counters were recorded (and consumers
    should gate display on consumption_data_real())."""
    with _db() as c:
        rows = c.execute(
            "SELECT e.day, e.pv_en, e.site_load_en, e.net_en FROM energy e "
            "JOIN (SELECT day, MAX(ts) mt FROM energy GROUP BY day) last "
            "ON e.day = last.day AND e.ts = last.mt ORDER BY e.day").fetchall()

    def diff(a, b):
        return round(a - b, 1) if a is not None and b is not None else None

    out: list[dict] = []
    prev = None
    for day, pv, load, net in rows:
        if prev is not None:
            out.append({"day": day, "pv_kwh": diff(pv, prev[0]),
                        "load_kwh": diff(load, prev[1]),
                        "net_kwh": diff(net, prev[2])})
        prev = (pv, load, net)
    return out[-days:]


def heatmap(days: int) -> dict:
    """Per inverter × day verdicts for the health heatmap, derived from
    producing-gated readings with the rollup's own thresholds."""
    since = time.time() - days * 86400
    with _db() as c:
        rows = c.execute(
            "SELECT day, sn, COUNT(*) n, "
            "SUM(CASE WHEN array_median > 0 AND power < ? * array_median "
            "    THEN 1 ELSE 0 END) low "
            "FROM readings WHERE ts >= ? AND producing = 1 GROUP BY day, sn",
            (FAIL_RATIO, since)).fetchall()
        state = {sn: {"bad_days": bd, "confirmed": bool(cf)}
                 for sn, bd, cf in c.execute(
                     "SELECT sn, bad_days, confirmed FROM inverter_state")}
    cells: dict[str, dict] = {}
    days_seen: set[str] = set()
    for day, sn, n, low in rows:
        days_seen.add(day)
        if n < MIN_SAMPLES:
            verdict = "few"
        elif low / n > 0.7:
            verdict = "bad"
        else:
            verdict = "good"
        cells.setdefault(sn, {})[day] = verdict
    return {"days": sorted(days_seen), "cells": cells, "state": state}


def consumption_data_real() -> bool:
    """True once site_load_en genuinely diverges from net_en — i.e. the
    install has working consumption CTs. Until then the mirror data is
    baseline-only and no consumption view should render."""
    with _db() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM energy WHERE site_load_en IS NOT NULL "
            "AND net_en IS NOT NULL AND ABS(site_load_en - net_en) > 0.01").fetchone()
    return bool(row[0])
