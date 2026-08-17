"""Tests for solar monitoring (tool_service/solar.py) + the solar_status tool.

The multi-day certainty machine is the point (docs/plans/SOLAR_MONITOR_PLAN):
a genuinely failing inverter must confirm over CONFIRM_DAYS, clouds must not
produce false alarms, and an alert fires exactly once per episode."""
import json
import time
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

import tool_service.solar as solar


# ── parsing (against the real varserver shape) ────────────────────────────────

def _inv_values(specs):
    """specs: list of (idx, sn, power, vmppt). Build varserver /vars values."""
    vals = []
    for idx, sn, p, v in specs:
        base = f"/sys/devices/inverter/{idx}/"
        vals += [{"name": base+"sn", "value": sn},
                 {"name": base+"p3phsumKw", "value": str(p)},
                 {"name": base+"vMppt1V", "value": str(v)},
                 {"name": base+"tHtsnkDegc", "value": "50"}]
    return vals


# The real 2026-07-14 read: 24 inverters, 20 healthy (~0.23 kW) + 4 failing.
# A healthy MAJORITY is required for peer detection — with the median dragged
# down by too many bad units you can't tell good from bad (a real property).
_FOUR_BAD = [
    (0, "450051817003219", 0.000, 61.5),   # dead
    (4, "450051815011992", 0.061, 61.5),   # severe
    (19, "450051818002424", 0.050, 61.7),  # severe
    (21, "450051818005632", 0.0089, 60.4), # dead
]
_TWENTY_HEALTHY = [(100+i, f"H{i:015d}", 0.22 + (i % 5) * 0.004, 52.0) for i in range(20)]
REAL_SAMPLE = _inv_values(_TWENTY_HEALTHY + _FOUR_BAD)


def test_parse_inverters_keys_by_serial():
    inv = solar.parse_inverters(REAL_SAMPLE)
    assert len(inv) == 24
    assert inv["450051817003219"]["power"] == 0.0
    assert inv["H000000000000000"]["vmppt"] == 52.0


def test_flag_underperformers_catches_the_four():
    inv = solar.parse_inverters(REAL_SAMPLE)
    med = solar.array_median_power(inv)
    flagged = set(solar.flag_underperformers(inv, med))
    assert flagged == {"450051817003219", "450051815011992",
                       "450051818002424", "450051818005632"}


def test_parse_total_kw():
    assert solar.parse_total_kw([{"name": "/sys/livedata/pv_p", "value": "4.93"}]) == 4.93
    assert solar.parse_total_kw([{"name": "/sys/other", "value": "1"}]) is None


# ── auth re-login on 401/403 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_vars_reauths_on_403(monkeypatch):
    monkeypatch.setattr(solar, "SOLAR_SERIAL", "D1901")
    solar._session = "stale"
    calls = {"login": 0, "vars": 0}

    class Resp:
        def __init__(self, code, data): self.status_code = code; self._d = data; self.text = ""
        def json(self): return self._d

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kw):
            if url.endswith("/auth?login") or "auth" in url:
                calls["login"] += 1
                return Resp(200, {"session": "fresh"})
            calls["vars"] += 1
            # first vars call 403s (stale session), second succeeds
            return Resp(200 if calls["vars"] > 1 else 403,
                        {"values": [{"name": "/x", "value": "1"}]})

    monkeypatch.setattr(solar.httpx, "AsyncClient", FakeClient)
    vals = await solar._get_vars("inverter")
    assert calls["login"] == 1        # re-authed once
    assert solar._session == "fresh"


# ── the multi-day certainty machine ───────────────────────────────────────────

@pytest.fixture
def soldb(tmp_path, monkeypatch):
    monkeypatch.setattr(solar, "SOLAR_DB", tmp_path / "solar.db")
    monkeypatch.setattr(solar, "CONFIRM_DAYS", 3)
    monkeypatch.setattr(solar, "MIN_SAMPLES", 4)
    solar.init_db()
    return solar.SOLAR_DB


def _seed_day(day_dt, inv_powers, n=6):
    """Write n producing samples for a day. inv_powers: {sn: power}. Array
    median implied by the healthy cohort."""
    for k in range(n):
        ts = day_dt.replace(hour=10, minute=k).timestamp()
        inv = {sn: {"power": p, "vmppt": 52, "temp": 50} for sn, p in inv_powers.items()}
        med = solar.array_median_power(inv)
        solar.record_poll(inv, med, ts=ts)


def test_three_bad_days_confirms_then_fires_once(soldb):
    """A dead inverter (0 kW) among healthy peers → bad day ×3 → confirmed
    on day 3, and no further transition on day 4 (fires once)."""
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    powers = {"good1": 0.23, "good2": 0.24, "good3": 0.22, "dead": 0.0}
    transitions_by_day = []
    for i in range(4):
        _seed_day(base + timedelta(days=i), powers)
        # roll up as if "today" is the day after the seeded day
        now = (base + timedelta(days=i+1)).strftime("%Y-%m-%d")
        transitions_by_day.append(solar.rollup_and_confirm(now_day=now))
    # days 0,1 → no transition; day 2 (3rd bad day) → confirmed; day 3 → none
    events = [ [t["event"] for t in day] for day in transitions_by_day ]
    assert events[0] == [] and events[1] == []
    assert {"event": "confirmed_failing", "sn": "dead", "days": 3} in transitions_by_day[2]
    assert events[3] == []   # already confirmed — no re-fire
    assert [c["sn"] for c in solar.confirmed_failing()] == ["dead"]


def test_cloudy_day_produces_no_false_alarm(soldb):
    """A cloudy day dims ALL inverters equally — peer ratios stay healthy, so
    no inverter gets a bad day. And a below-floor (night) day is unjudgeable."""
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    # cloudy: everyone low but proportional; dead still 0
    _seed_day(base, {"a": 0.05, "b": 0.055, "c": 0.05, "dead": 0.0})
    solar.rollup_and_confirm(now_day=(base + timedelta(days=1)).strftime("%Y-%m-%d"))
    # 'a','b','c' near median → not bad; only the true-zero 'dead' is bad
    with solar._db() as c:
        rows = {r["sn"]: r["last_verdict"] for r in c.execute("SELECT sn, last_verdict FROM inverter_state")}
    assert rows["a"] == "good" and rows["b"] == "good"
    assert rows["dead"] == "bad"


def test_night_day_is_unjudgeable(soldb):
    """All-below-floor samples (night/heavy overcast) → no verdict, counters
    untouched."""
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    _seed_day(base, {"a": 0.001, "b": 0.0, "c": 0.002})  # array median < floor
    solar.rollup_and_confirm(now_day=(base + timedelta(days=1)).strftime("%Y-%m-%d"))
    assert solar.confirmed_failing() == []
    with solar._db() as c:
        assert c.execute("SELECT COUNT(*) FROM inverter_state").fetchone()[0] == 0


def test_recovery_transitions_back(soldb):
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    powers_bad = {"g1": 0.23, "g2": 0.24, "dead": 0.0}
    for i in range(3):
        _seed_day(base + timedelta(days=i), powers_bad)
        solar.rollup_and_confirm(now_day=(base + timedelta(days=i+1)).strftime("%Y-%m-%d"))
    assert [c["sn"] for c in solar.confirmed_failing()] == ["dead"]
    # a good day for the previously-dead inverter
    good = {"g1": 0.23, "g2": 0.24, "dead": 0.23}
    d = base + timedelta(days=3)
    _seed_day(d, good)
    trans = solar.rollup_and_confirm(now_day=(d + timedelta(days=1)).strftime("%Y-%m-%d"))
    assert {"event": "recovered", "sn": "dead", "days": 0} in trans
    assert solar.confirmed_failing() == []


# ── /solar/status route + the tool ────────────────────────────────────────────

# ── per-inverter detail + history (the analytical path) ───────────────────────

def test_daily_history_computes_ratio(soldb):
    """History is irradiance-normalized (ratio vs the day's array median), so a
    persistently-failed inverter reads low every day."""
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    # 2 days: 'dead' at 0 kW among healthy peers (~0.23 median)
    for i in range(2):
        _seed_day(base + timedelta(days=i), {"g1": 0.23, "g2": 0.24, "dead": 0.0}, n=6)
    hist = solar._daily_history("dead", 5)
    assert [h["day"] for h in hist] == ["2026-07-10", "2026-07-11"]  # oldest→newest
    assert all(h["ratio"] == 0.0 for h in hist)
    assert hist[0]["samples"] == 6


def test_classify_status_bands():
    # FAIL_RATIO=0.40, MARGINAL_BAND=0.15 → marginal zone [0.40, 0.55)
    assert solar.classify_status(0.0) == "underperforming"
    assert solar.classify_status(0.39) == "underperforming"
    assert solar.classify_status(0.40) == "marginal"    # right at the line, flickers
    assert solar.classify_status(0.54) == "marginal"
    assert solar.classify_status(0.55) == "healthy"
    assert solar.classify_status(0.9) == "healthy"
    assert solar.classify_status(None) == "unknown"


@pytest.mark.asyncio
async def test_detail_marks_marginal_and_gives_it_history(soldb, monkeypatch):
    """A near-threshold inverter is 'marginal' (not underperforming) and still
    gets history — it's the one that makes the live count flicker."""
    # median of the healthy cohort ≈ 0.23; put one inverter at ratio ~0.45
    specs = _TWENTY_HEALTHY + [(200, "MARGINAL00000001", 0.104, 58.0)]  # 0.104/0.23 ≈ 0.45
    async def fake_vars(match):
        return _inv_values(specs) if match == "inverter" else [{"name": "/sys/livedata/pv_p", "value": "5.0"}]
    monkeypatch.setattr(solar, "_get_vars", fake_vars)
    d = await solar.fetch_detail()
    marg = next(iv for iv in d["inverters"] if iv["sn"] == "MARGINAL00000001")
    assert marg["status"] == "marginal"
    assert marg["underperforming_now"] is False   # above the fail line…
    assert "history" in marg                        # …but still surfaced for reasoning


@pytest.mark.asyncio
async def test_fetch_detail_current_state_and_focused_history(soldb, monkeypatch):
    """detail: current values + state for ALL inverters; daily history only for
    the troubled ones (underperforming now OR bad_days>0)."""
    # seed a couple bad days so 'dead' carries bad_days
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    for i in range(2):
        _seed_day(base + timedelta(days=i), {"H0": 0.23, "H1": 0.24, "450051817003219": 0.0})
        solar.rollup_and_confirm(now_day=(base + timedelta(days=i+1)).strftime("%Y-%m-%d"))

    async def fake_vars(match):
        if match == "inverter":
            return REAL_SAMPLE   # 20 healthy + the 4 real bad
        return [{"name": "/sys/livedata/pv_p", "value": "4.4"}]
    monkeypatch.setattr(solar, "_get_vars", fake_vars)

    d = await solar.fetch_detail(history_days=5)
    assert d["inverter_count"] == 24 and d["total_kw"] == 4.4
    by_sn = {iv["sn"]: iv for iv in d["inverters"]}
    dead = by_sn["450051817003219"]
    assert dead["power_kw"] == 0.0 and dead["underperforming_now"] is True
    assert dead["ratio_to_median"] == 0.0
    assert dead["bad_days"] == 2
    assert "history" in dead and len(dead["history"]) == 2   # focused: has history
    healthy = by_sn["H000000000000000"]
    assert healthy["underperforming_now"] is False
    assert "history" not in healthy                          # healthy: no history dump
    # worst inverters sorted first
    assert d["inverters"][0]["sn"] in {"450051817003219", "450051818005632"}


@pytest.mark.asyncio
async def test_solar_detail_tool_formats_for_reasoning():
    import tools
    from unittest.mock import patch

    class Resp:
        status_code = 200
        def json(self):
            return {"total_kw": 4.4, "inverter_count": 24, "array_median_kw": 0.23,
                    "fail_ratio": 0.4, "confirm_days": 3,
                    "inverters": [
                        {"sn": "450051817003219", "power_kw": 0.0, "voltage_v": 61.5,
                         "temp_c": 40, "ratio_to_median": 0.0, "underperforming_now": True,
                         "bad_days": 2, "confirmed_failing": False,
                         "history": [{"day": "2026-07-14", "avg_kw": 0.0, "ratio": 0.0, "samples": 30},
                                     {"day": "2026-07-15", "avg_kw": 0.0, "ratio": 0.0, "samples": 31}]},
                        {"sn": "450051815011992", "power_kw": 0.20, "voltage_v": 52.0,
                         "temp_c": 45, "ratio_to_median": 0.87, "underperforming_now": False,
                         "bad_days": 0, "confirmed_failing": False},
                    ]}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return Resp()

    with patch("tools.httpx.AsyncClient", return_value=Client()):
        out = await tools.execute("solar_detail", {})
    # the failing inverter, its ratio, bad_days, and trend history are all present
    assert "…003219" in out and "ratio 0.0" in out and "bad_days=2" in out
    assert "daily history" in out and "07-14" in out
    assert "40" in out  # temperature surfaced


def test_solar_status_route(monkeypatch):
    import tool_service.main as main_mod

    async def fake_snapshot():
        return {"total_kw": 4.93, "inverter_count": 24,
                "live_underperforming": ["a", "b", "c", "d"],
                "confirmed_failing": [{"sn": "450051817003219", "days": 5}],
                "array_median_kw": 0.23, "as_of": "2026-07-14T18:00:00+00:00"}
    monkeypatch.setattr(main_mod.solar, "fetch_snapshot", fake_snapshot)
    r = TestClient(main_mod.app).get("/solar/status")
    assert r.status_code == 200
    assert r.json()["total_kw"] == 4.93


@pytest.mark.asyncio
async def test_solar_status_tool_summarizes():
    import tools

    class Resp:
        status_code = 200
        def json(self):
            return {"total_kw": 4.93, "inverter_count": 24,
                    "live_underperforming": ["a", "b", "c", "d"],
                    "confirmed_failing": [{"sn": "450051817003219", "days": 5}]}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return Resp()

    from unittest.mock import patch
    with patch("tools.httpx.AsyncClient", return_value=Client()):
        out = await tools.execute("solar_status", {})
    assert "ISSUES" in out and "4.93 kW" in out
    assert "4 underperforming" in out
    assert "…003219" in out   # confirmed inverter named


@pytest.mark.asyncio
async def test_healthy_summary():
    import tools
    from unittest.mock import patch

    class Resp:
        status_code = 200
        def json(self):
            return {"total_kw": 5.4, "inverter_count": 24,
                    "live_underperforming": [], "confirmed_failing": []}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return Resp()

    with patch("tools.httpx.AsyncClient", return_value=Client()):
        out = await tools.execute("solar_status", {})
    assert "HEALTHY" in out and "5.4 kW" in out


# ── energy: lifetime (live) + past-period (snapshot diffs) ─────────────────────

def test_parse_lifetime_energy():
    assert solar.parse_lifetime_energy(
        [{"name": "/sys/livedata/pv_en", "value": "65649.8"}]) == 65649.8
    assert solar.parse_lifetime_energy([{"name": "/sys/livedata/pv_p", "value": "5"}]) is None


def test_parse_inverter_lifetime():
    vals = [{"name": "/sys/devices/inverter/0/sn", "value": "SN0"},
            {"name": "/sys/devices/inverter/0/ltea3phsumKwh", "value": "2252.1"},
            {"name": "/sys/devices/inverter/1/sn", "value": "SN1"},
            {"name": "/sys/devices/inverter/1/ltea3phsumKwh", "value": "3000.0"}]
    assert solar.parse_inverter_lifetime(vals) == {"SN0": 2252.1, "SN1": 3000.0}


def test_period_bounds_are_local_and_correct():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 7, 17, 14, 30, tzinfo=tz)
    midnight = datetime(2026, 7, 17, 0, 0, tzinfo=tz)
    assert solar._period_bounds("today", now) == (midnight, None)
    assert solar._period_bounds("yesterday", now) == (midnight - timedelta(days=1), midnight)
    assert solar._period_bounds("week", now) == (now - timedelta(days=7), None)
    assert solar._period_bounds("month", now) == (now - timedelta(days=30), None)
    start, end = solar._period_bounds("since:2026-07-01", now)
    assert start.date().isoformat() == "2026-07-01" and end is None
    with pytest.raises(ValueError):
        solar._period_bounds("fortnight", now)


def _mock_live_counter(monkeypatch, pv_en):
    async def fake_vars(match):
        return [{"name": "/sys/livedata/pv_en", "value": str(pv_en)}]
    monkeypatch.setattr(solar, "_get_vars", fake_vars)


@pytest.mark.asyncio
async def test_energy_for_period_diffs_snapshots(soldb, monkeypatch):
    """'week' = live counter now − the snapshot nearest 7 days ago."""
    now = time.time()
    solar.record_energy(60000.0, ts=now - 7 * 86400)   # a week ago
    solar.record_energy(60200.0, ts=now - 1 * 86400)   # yesterday
    _mock_live_counter(monkeypatch, 60350.5)           # now (live)
    d = await solar.energy_for_period("week")
    assert d["kwh"] == 350.5          # 60350.5 − 60000.0
    assert d["clamped_to"] is None


@pytest.mark.asyncio
async def test_energy_today_since_local_midnight(soldb, monkeypatch):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(solar.SOLAR_TZ)
    midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    solar.record_energy(64000.0, ts=midnight.timestamp())        # baseline at midnight
    solar.record_energy(64010.0, ts=time.time() - 60)            # a recent snapshot
    _mock_live_counter(monkeypatch, 64018.0)                     # live now
    d = await solar.energy_for_period("today")
    assert d["kwh"] == 18.0           # 64018 − 64000 since local midnight


@pytest.mark.asyncio
async def test_energy_period_clamps_before_history(soldb, monkeypatch):
    """Asking for a period older than our earliest snapshot clamps to it and
    says so — honest about what predates tracking (the PVS can't recover it)."""
    now = time.time()
    solar.record_energy(65000.0, ts=now - 2 * 86400)   # only 2 days of history
    _mock_live_counter(monkeypatch, 65120.0)
    d = await solar.energy_for_period("month")          # asks for 30 days
    assert d["kwh"] == 120.0                             # from earliest, not 30d ago
    assert d["clamped_to"] is not None


@pytest.mark.asyncio
async def test_energy_no_history_is_honest(soldb, monkeypatch):
    _mock_live_counter(monkeypatch, 65000.0)
    d = await solar.energy_for_period("today")
    assert "error" in d and "history" in d["error"]


@pytest.mark.asyncio
async def test_fetch_lifetime_system_and_per_inverter(monkeypatch):
    async def fake_vars(match):
        if match == "livedata":
            return [{"name": "/sys/livedata/pv_en", "value": "65649.8"}]
        return [{"name": "/sys/devices/inverter/0/sn", "value": "SN0"},
                {"name": "/sys/devices/inverter/0/ltea3phsumKwh", "value": "2252.13"}]
    monkeypatch.setattr(solar, "_get_vars", fake_vars)
    d = await solar.fetch_lifetime()
    assert d["lifetime_kwh"] == 65649.8
    assert d["per_inverter"] == {"SN0": 2252.1}


@pytest.mark.asyncio
async def test_solar_energy_tool_lifetime():
    import tools
    from unittest.mock import patch

    class Resp:
        status_code = 200
        def json(self):
            return {"lifetime_kwh": 65649.8,
                    "per_inverter": {"450051817003219": 2252.1, "H1": 3000.0},
                    "as_of": "2026-07-17T18:00:00+00:00"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return Resp()

    with patch("tools.httpx.AsyncClient", return_value=Client()):
        out = await tools.execute("solar_energy", {"period": "lifetime"})
    assert "LIFETIME" in out and "65649.8 kWh" in out
    assert "…003219" in out          # lowest-lifetime inverter named


@pytest.mark.asyncio
async def test_solar_energy_tool_period_and_clamp_note():
    import tools
    from unittest.mock import patch

    class Resp:
        status_code = 200
        def json(self):
            return {"period": "month", "kwh": 120.0,
                    "from": "2026-07-15T10:00:00-04:00",
                    "to": "2026-07-17T14:00:00-04:00",
                    "clamped_to": "2026-07-15T10:00:00-04:00"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return Resp()

    with patch("tools.httpx.AsyncClient", return_value=Client()):
        out = await tools.execute("solar_energy", {"period": "month"})
    assert "120.0 kWh" in out
    assert "tracking only began" in out and "2026-07-15" in out


def test_solar_energy_route(monkeypatch):
    import tool_service.main as main_mod

    async def fake_lifetime():
        return {"lifetime_kwh": 65649.8, "per_inverter": {}, "as_of": "x"}
    async def fake_period(period):
        return {"period": period, "kwh": 42.0, "from": "a", "to": "b", "clamped_to": None}
    monkeypatch.setattr(main_mod.solar, "fetch_lifetime", fake_lifetime)
    monkeypatch.setattr(main_mod.solar, "energy_for_period", fake_period)
    client = TestClient(main_mod.app)
    assert client.get("/solar/energy").json()["lifetime_kwh"] == 65649.8
    assert client.get("/solar/energy", params={"period": "week"}).json()["kwh"] == 42.0
