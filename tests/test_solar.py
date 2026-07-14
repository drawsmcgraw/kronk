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
