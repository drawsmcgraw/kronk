"""Sprint M12 fix 1, F7 (docs/sprint/M12-e2e.md block 6, must-know 3): a
held household run is never lost, exported twice, or exported late because
voice_tracer stops or loses Home Assistant during the hold.

The real voice_tracer.main.serve() loop (connect, reconnect with backoff,
SIGTERM's stop event) runs against:

- a fake HA websocket that keeps the last 10 runs per pipeline in memory
  and forgets them when HA restarts (as HA 2026.5 does), and sends the
  satellite's idle event when a run ends;
- a fake clock: every wait the loop makes (the 1 s event wait, the
  reconnect pause) advances it instead of sleeping;
- a stubbed Tempo (search and trace-by-id) whose Kronk trace becomes
  searchable 20 s after the run, as live;
- an exporter that records when each span left.

The sequences are the end-to-end tester's (scope 3): E is a Kronk-answered
"Play French Dinner Radio." on the kitchen puck. (a) voice_tracer stopped
at E.end+100 s and back 30 s later, HA restarted in the gap; (b) stopped at
+100 s, back 60 s later, 12 more runs on the pipeline in the gap; (c) down
from +100 s for 630 s or 720 s; and an HA-local E with HA's websocket down
from +30 s for 1200 s. Each must export E exactly once and in time. Then a
stop (and a hard kill) at every few seconds of the hold, followed by a
restart, never exports a run twice.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from voice_tracer import marks
from voice_tracer import main as vt

pytestmark = pytest.mark.usefixtures("no_network")

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "m12")
SLACK_S = 15 * 60          # Tempo's ingestion_time_range_slack (telemetry/tempo.yaml)
KRONK_TRACE = "ab" * 16


def _load(name):
    d = json.load(open(os.path.join(FIX, name)))
    return d["run_id"], d["result"]


def _times(run):
    ts = [datetime.fromisoformat(e["timestamp"]).timestamp() for e in run["events"]]
    return min(ts), max(ts)


def _shift(run, seconds):
    out = json.loads(json.dumps(run))
    for e in out["events"]:
        e["timestamp"] = (datetime.fromisoformat(e["timestamp"]) + timedelta(seconds=seconds)).isoformat()
    return out


class Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


class World:
    """HA as voice_tracer sees it, plus everything scheduled to happen."""

    KEEP = 10                                    # HA's pipeline_debug keeps the last 10 runs

    def __init__(self, clock):
        self.clock = clock
        self.ha_up = True
        self.runs: dict[str, list[tuple[str, dict]]] = {"kronk": [], "kronk-dev": []}
        self.actions: list[tuple[float, int, object]] = []
        self.events: list[dict] = []
        self.conns: list[asyncio.Future] = []
        self._n = 0

    def at(self, t, fn):
        self._n += 1
        self.actions.append((t, self._n, fn))
        self.actions.sort(key=lambda a: (a[0], a[1]))

    def run_ends(self, pipeline, rid, run):
        """HA lists the run from its end, and the satellite goes idle."""
        def land():
            lst = self.runs[pipeline]
            lst.append((rid, run))
            del lst[:-self.KEEP]
            self.events.append({"data": {"entity_id": "assist_satellite.kitchen",
                                         "new_state": {"state": "idle"},
                                         "old_state": {"state": "responding"}}})
        self.at(_times(run)[1], land)

    def _drop(self):
        for c in self.conns:
            if not c.done():
                c.set_result(None)
        self.conns = []
        self.events = []

    def ha_down(self, seconds):
        def down():
            self.ha_up = False
            self._drop()
        def up():
            self.ha_up = True
        self.at(self.clock.now, down)
        self.at(self.clock.now + seconds, up)

    def ha_restart(self):
        """HA restarted: its websocket drops and its run list is gone."""
        self.ha_up = True
        self._drop()
        self.runs = {p: [] for p in self.runs}

    async def advance(self, seconds):
        target = self.clock.now + seconds
        while self.actions and self.actions[0][0] <= target:
            t, _, fn = self.actions.pop(0)
            self.clock.now = max(self.clock.now, t)
            fn()
        self.clock.now = target
        for _ in range(3):
            await asyncio.sleep(0)


class _Events:
    def __init__(self, world):
        self.world = world

    async def get(self):
        await self.world.advance(1.0)             # the loop's 1 s wait for an event
        if self.world.events:
            return self.world.events.pop(0)
        raise asyncio.TimeoutError


def fake_ha_client(world):
    class FakeHAClient:
        def __init__(self, url, token):
            self.events = _Events(world)
            self.conn = None

        async def connect(self):
            await asyncio.sleep(0)
            if not world.ha_up:
                raise OSError("HA is down")
            self.conn = asyncio.get_running_loop().create_future()
            world.conns.append(self.conn)

        async def result(self, type, **kw):
            await asyncio.sleep(0)
            if self.conn is None or self.conn.done():
                raise RuntimeError("websocket closed")
            if type == "assist_pipeline/pipeline/list":
                return {"pipelines": [{"id": p, "name": p} for p in world.runs]}
            if type in ("get_states", "subscribe_events"):
                return []
            if type == "assist_pipeline/pipeline_debug/list":
                return {"pipeline_runs": [{"pipeline_run_id": rid, "timestamp": r["events"][0]["timestamp"]}
                                          for rid, r in world.runs[kw["pipeline_id"]]]}
            if type == "assist_pipeline/pipeline_debug/get":
                for rid, r in world.runs[kw["pipeline_id"]]:
                    if rid == kw["pipeline_run_id"]:
                        return r
                raise RuntimeError("not found")
            raise AssertionError(type)

        async def reader(self):
            await self.conn

        async def close(self):
            pass
    return FakeHAClient


class TimedExporter(InMemorySpanExporter):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.at: list[tuple[float, object]] = []

    def export(self, spans):
        for s in spans:
            self.at.append((self.clock.now, s))
        return super().export(spans)


class Sim:
    def __init__(self, monkeypatch, tmp_path, start, tempo_up=lambda: True, kronk=None):
        self.clock = Clock(start)
        self.world = World(self.clock)
        self.exp = TimedExporter(self.clock)
        self.kronk = kronk or {}                  # trace id → {"start", "visible", "rule"}
        self.tempo_up = tempo_up
        monkeypatch.setattr(vt.time, "time", self.clock.time)
        monkeypatch.setattr(vt, "HAClient", fake_ha_client(self.world))
        monkeypatch.setattr(vt, "STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setattr(vt, "RECORD_TEXT", False)
        monkeypatch.setattr(vt, "AUTOMATIONS", [])
        monkeypatch.setattr(vt, "POLL_S", 60.0)

        async def pause(seconds):
            await self.world.advance(seconds)
        monkeypatch.setattr(vt, "_pause", pause)
        for k in list(vt.HEALTH):
            if isinstance(vt.HEALTH[k], int) and not isinstance(vt.HEALTH[k], bool):
                monkeypatch.setitem(vt.HEALTH, k, 0)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self._tempo), base_url="http://tempo:3200")

    def _tempo(self, request):
        if not self.tempo_up():
            return httpx.Response(503)
        if request.url.path == "/api/search":
            s, e = float(request.url.params["start"]), float(request.url.params["end"])
            hits = [{"traceID": tid, "startTimeUnixNano": str(int(v["start"] * 1e9))}
                    for tid, v in self.kronk.items()
                    if self.clock.now >= v["visible"] and s <= v["start"] <= e]
            return httpx.Response(200, json={"traces": hits})
        tid = request.url.path.rsplit("/", 1)[-1]
        attrs = [{"key": "kronk.rule", "value": {"stringValue": self.kronk[tid]["rule"]}}]
        if self.kronk[tid].get("action"):
            attrs.append({"key": "kronk.direct_action", "value": {"stringValue": self.kronk[tid]["action"]}})
        return httpx.Response(200, json={"trace": {"resourceSpans": [{"scopeSpans": [{"spans": [
            {"name": "routing.decide", "startTimeUnixNano": "1", "attributes": attrs}]}]}]}})

    def tracer(self):
        return vt.VoiceTracer(emitter=vt.Emitter(exporter=self.exp, batch=False))

    async def process(self, until=None, stop_at=None, kill_at=None):
        """One voice_tracer process from now: until a stop (SIGTERM's
        event), a hard kill (the task is cancelled: no shutdown runs), or
        `until` (then stopped)."""
        tracer = self.tracer()
        stop = asyncio.Event()
        task = asyncio.create_task(vt.serve(tracer, stop, http=self.http))
        if kill_at is not None:
            self.world.at(kill_at, task.cancel)
        else:
            self.world.at(stop_at if stop_at is not None else until, stop.set)
        try:
            await task
        except asyncio.CancelledError:
            pass
        return tracer

    async def down(self, seconds):
        await self.world.advance(seconds)         # no voice_tracer running

    def exports(self, rid):
        return [(t, s) for t, s in self.exp.at if s.name == "voice.run"
                and s.attributes.get("ha.pipeline_run_id") == rid]

    def oldest_export_age(self):
        return max((t - s.start_time / 1e9 for t, s in self.exp.at), default=0.0)


def _kronk_e():
    rid, run = _load("ha_run_kronk_answered.json")
    s, e = _times(run)
    return rid, run, s, e


def _once_in_time(sim, rid, by=None):
    ex = sim.exports(rid)
    assert len(ex) == 1, f"{rid} exported {len(ex)} times"
    t, span = ex[0]
    assert t - span.start_time / 1e9 <= vt.EXPORT_AGE_MAX_S < SLACK_S
    if by is not None:
        assert t <= by, f"exported at {t - by:+.1f}s after the deadline"
    return span


# ── the tester's three sequences ────────────────────────────────────────────

def _seq(monkeypatch, tmp_path, gap_events, down_s):
    rid, run, s, e = _kronk_e()
    sim = Sim(monkeypatch, tmp_path, s - 5, kronk={KRONK_TRACE: {"start": s + 0.8, "visible": e + 20,
                                                                  "rule": "music"}})
    sim.world.run_ends("kronk", rid, run)

    async def go():
        await sim.process(stop_at=e + 100)
        gap_events(sim, e)
        await sim.down(down_s)
        await sim.process(until=sim.clock.now + 400)
        await sim.http.aclose()
    asyncio.run(go())
    return sim, rid, e


def test_sequence_a_stop_then_ha_restarts_in_the_gap(monkeypatch, tmp_path):
    sim, rid, e = _seq(monkeypatch, tmp_path, lambda sim, e: sim.world.at(e + 110, sim.world.ha_restart), 30)
    span = _once_in_time(sim, rid, by=e + 103)
    assert span.attributes["voice.window_cut_short"] == "shutdown"
    assert span.attributes["kronk.trace_id"] == KRONK_TRACE           # joined before the stop
    assert vt.HEALTH["held_lost"] == 0 and vt.HEALTH["cut_short"] == 1


def test_sequence_b_stop_then_twelve_more_runs(monkeypatch, tmp_path):
    rid_b, run_b = _load("ha_run_ha_local_stop.json")

    def twelve(sim, e):
        b0, _ = _times(run_b)
        for i in range(12):
            sim.world.run_ends("kronk", f"01M12FIXONEMORE{i:02d}", _shift(run_b, (e + 105 + 3 * i) - b0))
    sim, rid, e = _seq(monkeypatch, tmp_path, twelve, 60)
    _once_in_time(sim, rid, by=e + 103)
    for i in range(12):
        assert len(sim.exports(f"01M12FIXONEMORE{i:02d}")) <= 1
    assert vt.HEALTH["held_lost"] == 0


@pytest.mark.parametrize("down_s", [630, 720])
def test_sequence_c_long_stop(monkeypatch, tmp_path, down_s):
    sim, rid, e = _seq(monkeypatch, tmp_path, lambda sim, e: None, down_s)
    _once_in_time(sim, rid, by=e + 103)
    assert vt.HEALTH["held_lost"] == 0


# ── HA's websocket down for 20 minutes ──────────────────────────────────────

def test_ha_outage_1200s_exports_the_held_run_at_once(monkeypatch, tmp_path):
    rid, run = _load("ha_run_ha_local_stop.json")                 # HA answered: no join
    s, e = _times(run)
    sim = Sim(monkeypatch, tmp_path, s - 5)
    sim.world.run_ends("kronk", rid, run)
    sim.world.at(e + 30, lambda: sim.world.ha_down(1200))

    async def go():
        await sim.process(until=e + 1500)
        await sim.http.aclose()
    asyncio.run(go())
    span = _once_in_time(sim, rid, by=e + 34)
    assert span.attributes["voice.window_cut_short"] == "ha_disconnected"
    assert sim.oldest_export_age() < 60


def test_ha_and_tempo_down_while_the_run_is_still_joining(monkeypatch, tmp_path):
    """HA drops 10 s after a Kronk-answered run, before the join; Tempo is
    down too. The join gives up after its last attempt (+60 s) and the run
    is exported then, not at HA's return 20 minutes later."""
    rid, run, s, e = _kronk_e()
    sim = Sim(monkeypatch, tmp_path, s - 5, tempo_up=lambda: False)
    sim.world.run_ends("kronk", rid, run)
    sim.world.at(e + 10, lambda: sim.world.ha_down(1200))

    async def go():
        await sim.process(until=e + 1500)
        await sim.http.aclose()
    asyncio.run(go())
    span = _once_in_time(sim, rid, by=e + 75)
    assert span.attributes["voice.window_cut_short"] == "ha_disconnected"
    assert span.attributes["kronk.join"] == "none"


def test_ha_drop_while_joining_keeps_the_join(monkeypatch, tmp_path):
    """HA drops 5 s after the run; Tempo is up. The join still resolves
    (it only needs Tempo) and the run leaves at once, joined."""
    rid, run, s, e = _kronk_e()
    sim = Sim(monkeypatch, tmp_path, s - 5, kronk={KRONK_TRACE: {"start": s + 0.8, "visible": e + 20,
                                                                  "rule": "music"}})
    sim.world.run_ends("kronk", rid, run)
    sim.world.at(e + 5, lambda: sim.world.ha_down(600))

    async def go():
        await sim.process(until=e + 900)
        await sim.http.aclose()
    asyncio.run(go())
    span = _once_in_time(sim, rid, by=e + 33)      # the join resolves at +30; it leaves then,
    assert span.attributes["kronk.trace_id"] == KRONK_TRACE   # not at the next reconnect attempt
    assert span.attributes["voice.window_cut_short"] == "ha_disconnected"


# ── a correction after a cut-short export: late, logged, counted ────────────

def test_correction_after_a_stop_is_counted_late_across_the_restart(monkeypatch, tmp_path, caplog):
    """E is exported at a stop 10 s after it ended; voice_tracer is back 30
    s later and finds "Stop." (Kronk's playback rule), spoken 20 s after E
    while it was down. E's facts were kept in the state file, so the
    correction is logged and counted as late; E is not exported again."""
    rid, run, s, e = _kronk_e()
    rid_b = "01M12FIXLATESTOP0000000000"
    run_b = _shift(run, 20 + (e - s))
    b0, b1 = _times(run_b)
    sim = Sim(monkeypatch, tmp_path, s - 5, kronk={
        KRONK_TRACE: {"start": s + 0.8, "visible": e + 5, "rule": "music"},
        "cd" * 16: {"start": b0 + 0.8, "visible": b1 + 5, "rule": "playback", "action": "pause"}})
    sim.world.run_ends("kronk", rid, run)
    sim.world.run_ends("kronk", rid_b, run_b)
    state = tmp_path / "state.json"

    async def go():
        await sim.process(stop_at=e + 10)
        saved = json.load(open(state))
        assert saved["held"] == [] and [c["run_id"] for c in saved["cut"]] == [rid]
        assert all(c["transcript"] is None for c in saved["cut"])
        await sim.down(30)
        with caplog.at_level("WARNING", logger="voice_tracer"):
            await sim.process(until=sim.clock.now + 300)
        await sim.http.aclose()
    monkeypatch.setattr(vt, "JOIN_DELAYS_S", [6, 12, 18, 24])
    asyncio.run(go())
    _once_in_time(sim, rid)
    _once_in_time(sim, rid_b)
    assert vt.HEALTH["marks_late"] == 1
    assert "already exported; not marked" in caplog.text


# ── a run too old when it is finally seen ───────────────────────────────────

def test_a_run_past_the_export_age_is_counted_not_sent(monkeypatch, tmp_path, caplog):
    """What the code does with a run that is too old when it is finally
    seen. A hard kill leaves the held run in the state file; voice_tracer is
    back 9.5 min later and fetches it again (HELD_RECOVERY_S), and here it is
    held for as long as anything allows (ready_to_emit forced False) until
    the next stop, at 20 min. By then it is past EXPORT_AGE_MAX_S: it is
    recorded as seen, logged with its id and age, counted in /health
    too_old, and not sent. The process after that does not fetch it again."""
    rid, run = _load("ha_run_ha_local_stop.json")
    s, e = _times(run)
    sim = Sim(monkeypatch, tmp_path, s - 5)
    sim.world.run_ends("kronk", rid, run)
    monkeypatch.setattr(marks, "ready_to_emit", lambda *a, **kw: False)

    async def go():
        await sim.process(kill_at=e + 60)
        assert json.load(open(tmp_path / "state.json"))["held"] == [rid]
        await sim.down(500)
        with caplog.at_level("WARNING", logger="voice_tracer"):
            t2 = await sim.process(until=e + 1200)
        assert rid in {k.split(":", 1)[1] for k in t2.seen}
        await sim.process(until=sim.clock.now + 120)
        await sim.http.aclose()
    asyncio.run(go())
    assert sim.exports(rid) == []
    assert vt.HEALTH["too_old"] == 1 and vt.HEALTH["held_lost"] == 0
    assert f"pipeline:{rid} not exported: its first span is" in caplog.text


def test_emit_refuses_a_run_older_than_the_limit(monkeypatch, tmp_path, caplog):
    rid, run = _load("ha_run_ha_local_stop.json")
    s, e = _times(run)
    from voice_tracer import convert
    sim = Sim(monkeypatch, tmp_path, s + vt.EXPORT_AGE_MAX_S + 1)
    tracer = sim.tracer()
    specs = convert.assign_ids("pipeline", rid, convert.pipeline_run_spans(run, rid, "kronk"))
    with caplog.at_level("WARNING", logger="voice_tracer"):
        tracer._emit_run(rid, specs, f"pipeline:{rid}")
    assert sim.exports(rid) == []
    assert vt.HEALTH["too_old"] == 1
    assert f"pipeline:{rid}" in tracer.seen
    assert "past the 840s export limit" in caplog.text
    sim.clock.now = s + vt.EXPORT_AGE_MAX_S - 1
    other = "01M12FIXYOUNGENOUGH0000000"
    specs = convert.assign_ids("pipeline", other, convert.pipeline_run_spans(run, other, "kronk"))
    tracer._emit_run(other, specs, f"pipeline:{other}")
    assert len(sim.exports(other)) == 1
    asyncio.run(sim.http.aclose())


def test_limits_stay_inside_tempos_slack():
    assert marks.AGE_LIMIT_S < vt.EXPORT_AGE_MAX_S < SLACK_S - 5 - 10   # batch delay, exporter deadline
    assert vt.SHUTDOWN_JOIN_S + 5 < 10                                    # docker stop's 10 s


# ── a stop or a hard kill at every point of the hold ────────────────────────

def _restart_case(monkeypatch, tmp_path, k, down_s, kill):
    """E (Kronk-answered) then "Stop." (HA local) 20 s later on the same
    puck; voice_tracer stopped or killed at E.end + k, back after down_s."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    rid, run, s, e = _kronk_e()
    rid_b, run_b = _load("ha_run_ha_local_stop.json")
    b0, _ = _times(run_b)
    run_b = _shift(run_b, (e + 20) - b0)
    sim = Sim(monkeypatch, tmp_path, s - 5, kronk={KRONK_TRACE: {"start": s + 0.8, "visible": e + 20,
                                                                  "rule": "music"}})
    monkeypatch.setattr(vt, "RECORD_TEXT", True)      # an HA-local "Stop." is known by its words only
    sim.world.run_ends("kronk", rid, run)
    sim.world.run_ends("kronk", rid_b, run_b)

    async def go():
        if kill:
            await sim.process(kill_at=e + k)
        else:
            await sim.process(stop_at=e + k)
        await sim.down(down_s)
        await sim.process(until=max(sim.clock.now + 200, e + 400))
        await sim.http.aclose()
    asyncio.run(go())
    return sim, rid, rid_b


@pytest.mark.parametrize("down_s", [3, 45, 200])
def test_a_stop_at_any_point_exports_each_run_once_and_in_time(monkeypatch, tmp_path, down_s):
    for k in range(2, 262, 8):
        sim, rid, rid_b = _restart_case(monkeypatch, tmp_path / f"s{k}", k, down_s, kill=False)
        for r in (rid, rid_b):
            _once_in_time(sim, r)
        assert vt.HEALTH["held_lost"] == 0, k
        a = sim.exports(rid)[0][1].attributes
        if k >= 30:                               # the stop came after "Stop." was classified
            assert a.get("voice.corrected_by") == "stop", k
        if a.get("voice.window_cut_short") and k < 20:   # stopped before Kronk's trace was searchable
            assert a.get("kronk.join") == "none", k
        else:                                     # joined in the loop, by the last attempt at the
            assert a.get("kronk.trace_id") == KRONK_TRACE, k   # stop, or by the next process


@pytest.mark.parametrize("down_s", [3, 45, 200])
def test_a_hard_kill_at_any_point_never_exports_twice(monkeypatch, tmp_path, down_s):
    for k in range(2, 262, 8):
        sim, rid, rid_b = _restart_case(monkeypatch, tmp_path / f"k{k}", k, down_s, kill=True)
        for r in (rid, rid_b):
            assert len(sim.exports(r)) <= 1, (k, r)
        # HA still lists both and they are inside HELD_RECOVERY_S: nothing lost either
        for r in (rid, rid_b):
            _once_in_time(sim, r)


# ── SIGTERM: docker stop no longer waits out its timeout ────────────────────

def test_sigterm_sets_the_stop_and_main_returns(monkeypatch):
    """main() installs a SIGTERM handler (PID 1 ignores the signal
    otherwise, so docker stop waited 10 s and then killed the process). The
    signal is raised only once a handler is confirmed, so a missing handler
    fails the test instead of ending the test run."""
    import signal
    monkeypatch.setattr(vt, "HA_TOKEN", "t")
    monkeypatch.setattr(vt, "ENABLED", False)
    monkeypatch.setattr(vt, "_serve_health", lambda: None)
    monkeypatch.setattr(vt, "STATE_FILE", "/nonexistent-m12-fix1/state.json")
    seen = {}

    async def fake_serve(tracer, stop, http=None):
        seen["handler"] = signal.getsignal(signal.SIGTERM)
        assert seen["handler"] not in (signal.SIG_DFL, signal.SIG_IGN, None)
        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(stop.wait(), 5)
        seen["stopped"] = True
    monkeypatch.setattr(vt, "serve", fake_serve)
    asyncio.run(vt.main())
    assert seen.get("stopped") is True
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL   # loop.close() removed it
