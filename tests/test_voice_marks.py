"""Sprint M12: the wrong-action signal in voice_tracer.

Pure logic (voice_tracer/marks.py) first: which later utterances count as a
correction, the one near-repeat definition, the 60 s window, same satellite
only, dev and production kept apart, the hold-back rule. Then the wiring in
voice_tracer/main.py, driven with recorded HA run shapes
(tests/fixtures/m12/, real 2026.5.4 payloads with the household's words
replaced by corpus sentences) and a stubbed Tempo: the mark is on the
earlier run's exported span, a late correction is counted and logged, and a
restart neither loses held runs silently nor emits a run twice.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from voice_tracer import convert, marks
from voice_tracer import main as vt

pytestmark = pytest.mark.usefixtures("no_network")

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "m12")
CORPUS = os.path.join(os.path.dirname(__file__), "voice_corpus.jsonl")
KITCHEN = "assist_satellite.home_assistant_voice_0ac919_assist_satellite"
OFFICE = "assist_satellite.satellite1_d8d1bc_assist_satellite"


def F(run_id, start, end, sat=KITCHEN, dev=False, text=None, rule=None, action=None):
    return marks.RunFacts(run_id=run_id, dev=dev, satellite=sat, start_s=start, end_s=end,
                          transcript=text, kronk_rule=rule, kronk_action=action,
                          trace_id=f"{abs(hash(run_id)):032x}"[:32])


# ── correction_kind: routing's forms, from the rule or the transcript ────────

@pytest.mark.parametrize("rule, action, kind", [
    ("music_another", None, "not_that"),
    ("rate", "down", "rate_down"),
    ("rate", "up", "control"),
    ("playback", "pause", "stop"),
    ("playback", "next", "control"),
    ("playback", "resume", "control"),
    ("playback", "volume_down", "control"),
    ("music", None, None),
    ("default", None, None),
])
def test_kind_from_kronks_routing_decision_needs_no_text(rule, action, kind):
    assert marks.correction_kind(F("b", 0, 1, rule=rule, action=action)) == kind


def test_a_playback_rule_without_its_action_is_not_guessed():
    """A Kronk trace from before M12 has kronk.rule but no direct_action:
    without text it could be a stop or a skip, so no kind."""
    assert marks.correction_kind(F("b", 0, 1, rule="playback")) is None
    assert marks.correction_kind(F("b", 0, 1, rule="playback", text="Stop!")) == "stop"


def _corpus_rows():
    rows = [json.loads(line) for line in open(CORPUS) if line.strip()]
    return [r for r in rows if r["expect"].get("rule") in ("playback", "rate") and not r.get("xfail")]


@pytest.mark.parametrize("row", _corpus_rows(), ids=lambda r: r["id"])
def test_corpus_playback_and_thumbs_rows_classify_from_text(row):
    """The corpus's own command forms: pause → stop, thumbs down →
    rate_down, the rest are controls that never mark."""
    e = row["expect"]
    want = ("stop" if e.get("action") == "pause" else
            "rate_down" if e.get("thumb") == "down" else "control")
    assert marks.correction_kind(F("b", 0, 1, text=row["text"])) == want


@pytest.mark.parametrize("text", [
    "Not that", "not that one", "No, not that.", "something else", "play something different",
    "a different station", "try a different genre please", "not that in the kitchen",
])
def test_music_another_forms_are_not_that(text):
    assert marks.correction_kind(F("b", 0, 1, text=text)) == "not_that"


@pytest.mark.parametrize("text", [
    "Thumbs down.", "I don't like this song", "never play this again", "downvote", "less like this",
])
def test_rating_down_forms(text):
    assert marks.correction_kind(F("b", 0, 1, text=text)) == "rate_down"


@pytest.mark.parametrize("text", ["stop the timer", "Play French Dinner Radio.", "what's the weather"])
def test_other_sentences_are_not_corrections(text):
    assert marks.correction_kind(F("b", 0, 1, text=text)) is None


# ── near_repeat: one definition ─────────────────────────────────────────────

@pytest.mark.parametrize("a, b, same", [
    ("Play French Dinner Radio.", "play french dinner radio", True),            # punctuation, case
    ("play anime pop radio", "Play Anime Pop Radio station.", True),             # 0.89
    ("Play French Dinner Radio.", "Play French Cafe Radio on Pandora.", False),  # 0.6
    ("play jazz", "play blues", False),                                         # 0.5
    ("put on some jazz", "put on some aerosmith", True),                       # 0.75, the edge
    ("what's the weather", "what time is it", False),
    ("", "play jazz", False),
    (None, "play jazz", False),
])
def test_near_repeat_definition(a, b, same):
    assert marks.near_repeat(a, b) is same


# ── find_mark: window, satellite, dev, text ─────────────────────────────────

def test_not_that_marks_the_run_before_it():
    a = F("a", 100, 110, text="Play French Dinner Radio.")
    b = F("b", 130, 133, text="not that")
    m = marks.find_mark([a, b], b, text_on=True)
    assert (m.earlier_run_id, m.later_run_id, m.kind, m.after_s) == ("a", "b", "not_that", 20.0)
    assert m.earlier_trace_id == a.trace_id and m.later_trace_id == b.trace_id


@pytest.mark.parametrize("gap, marked", [(0.0, True), (59.9, True), (60.0, True), (60.1, False), (240.0, False)])
def test_window_edges(gap, marked):
    """Inclusive at 60 s. A stop four minutes later (a long song, then
    "stop") is not a correction of the request."""
    a = F("a", 100, 110, rule="music")
    b = F("b", 110 + gap, 112 + gap, rule="playback", action="pause")
    assert (marks.find_mark([a, b], b, text_on=False) is not None) is marked


def test_same_satellite_only_and_interleaved_satellites():
    a = F("a", 100, 110, sat=KITCHEN, text="Play French Dinner Radio.")
    x = F("x", 112, 115, sat=OFFICE, text="play some jazz in the office")
    b = F("b", 120, 122, sat=OFFICE, text="Stop.")
    c = F("c", 125, 128, sat=KITCHEN, text="not that")
    recent = [a, x, b, c]
    mb = marks.find_mark(recent, b, text_on=True)
    assert mb.earlier_run_id == "x" and mb.kind == "stop"          # the office stop, the office request
    mc = marks.find_mark(recent, c, text_on=True)
    assert mc.earlier_run_id == "a" and mc.kind == "not_that"      # the kitchen one skips the office runs


def test_only_the_run_immediately_before_can_be_corrected():
    a = F("a", 100, 105, text="Play French Dinner Radio.")
    a2 = F("a2", 110, 112, text="what's the weather")
    b = F("b", 120, 121, text="Stop.")
    assert marks.find_mark([a, a2, b], b, text_on=True).earlier_run_id == "a2"


def test_no_satellite_never_marks():
    a = F("a", 100, 110, sat=None, rule="music")
    b = F("b", 115, 116, sat=None, rule="music_another")
    assert marks.find_mark([a, b], b, text_on=False) is None


@pytest.mark.parametrize("dev_a, dev_b, marked", [(False, True, False), (True, False, False),
                                                  (True, True, True), (False, False, True)])
def test_dev_and_production_never_pair(dev_a, dev_b, marked):
    a = F("a", 100, 110, dev=dev_a, rule="music")
    b = F("b", 115, 116, dev=dev_b, rule="music_another")
    assert (marks.find_mark([a, b], b, text_on=False) is not None) is marked


def test_a_dev_run_between_does_not_shield_or_take_a_production_correction():
    a = F("a", 100, 110, rule="music")
    d = F("d", 112, 113, dev=True, rule="music")              # Hey Jarvis on the same puck
    b = F("b", 115, 116, rule="music_another")
    assert marks.find_mark([a, d, b], b, text_on=False).earlier_run_id == "a"


def test_near_repeat_needs_text_and_is_skipped_without_it():
    a = F("a", 100, 110, text="Play French Dinner Radio.")
    b = F("b", 120, 125, text="play french dinner radio")
    assert marks.find_mark([a, b], b, text_on=True).kind == "near_repeat"
    assert marks.find_mark([a, b], b, text_on=False) is None
    # text off in production means no transcript in the facts at all
    a0, b0 = F("a", 100, 110), F("b", 120, 125)
    assert marks.find_mark([a0, b0], b0, text_on=True) is None


def test_a_control_repeat_is_not_a_near_repeat():
    """"Skip", "skip": two skips are normal use, not a wrong action."""
    a = F("a", 100, 102, text="skip")
    b = F("b", 110, 112, text="skip")
    assert marks.find_mark([a, b], b, text_on=True) is None


def test_stop_after_stop_marks_the_first():
    a = F("a", 100, 102, text="Stop.")
    b = F("b", 106, 108, text="stop")
    assert marks.find_mark([a, b], b, text_on=True).kind == "stop"


# ── ready_to_emit: the hold ─────────────────────────────────────────────────

def test_ready_after_window_and_grace():
    a = F("a", 100, 110)
    edge = 110 + marks.WINDOW_S + marks.GRACE_S
    assert not marks.ready_to_emit(a, edge - 0.1, [])
    assert marks.ready_to_emit(a, edge, [])


def test_waits_for_an_unclassified_later_run_in_its_window():
    a = F("a", 100, 110)
    b = F("b", 165, 170)                              # started inside the window, join not resolved yet
    late = 110 + marks.WINDOW_S + marks.GRACE_S + 30
    assert not marks.ready_to_emit(a, late, [b])
    assert marks.ready_to_emit(a, late, [F("o", 165, 170, sat=OFFICE)])   # other satellite
    assert marks.ready_to_emit(a, late, [F("c", 171, 172)])               # after the window
    assert marks.ready_to_emit(a, 110 + marks.HOLD_MAX_S, [b])            # the cap


def test_hold_cap_counts_from_this_processs_fetch_and_age_limit_wins():
    """After a restart the re-fetched run waits for its correction again
    (fetched_at is the restart), but never past AGE_LIMIT_S."""
    a = F("a", 100, 110)
    a.fetched_at = 600.0
    b = F("b", 165, 170)
    assert not marks.ready_to_emit(a, 610, [b])
    assert marks.ready_to_emit(a, 600 + marks.HOLD_MAX_S, [b])
    assert marks.ready_to_emit(a, 110 + marks.AGE_LIMIT_S, [b])


def test_no_satellite_is_never_held():
    assert marks.ready_to_emit(F("a", 100, 110, sat=None), 111, [])


def test_hold_cap_stays_inside_the_restart_backfill():
    assert vt.BACKFILL_S < vt.HELD_RECOVERY_S < marks.AGE_LIMIT_S < 15 * 60


# ── Kronk's routing from a Tempo trace ──────────────────────────────────────

def _tempo_trace(rule, action=None, tools=("control_music",)):
    attrs = [{"key": "kronk.rule", "value": {"stringValue": rule}},
             {"key": "kronk.output_chars", "value": {"intValue": "4"}}]
    if action:
        attrs.append({"key": "kronk.direct_action", "value": {"stringValue": action}})
    spans = [{"name": "routing.decide", "startTimeUnixNano": "1", "attributes": attrs},
             {"name": "pipeline.shim", "startTimeUnixNano": "0", "attributes": [
                 {"key": "kronk.input", "value": {"stringValue": "must never be read"}}]}]
    for i, t in enumerate(tools):
        spans.append({"name": f"tool.{t}", "startTimeUnixNano": str(10 + i),
                      "attributes": [{"key": "kronk.tool", "value": {"stringValue": t}}]})
    return {"trace": {"resourceSpans": [{"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": "kronk-orchestrator"}}]},
        "scopeSpans": [{"spans": spans}]}]}}


def test_routing_from_trace_v2_and_v1_shapes():
    v2 = _tempo_trace("playback", "pause", ("control_music",))
    assert marks.routing_from_trace(v2) == {"rule": "playback", "direct_action": "pause",
                                            "tools": ["control_music"]}
    v1 = {"batches": v2["trace"]["resourceSpans"]}
    assert marks.routing_from_trace(v1)["rule"] == "playback"
    assert marks.routing_from_trace({}) == {"rule": None, "direct_action": None, "tools": []}


# ── The tracer: recorded run shapes, stubbed Tempo, injected time ───────────

def _load(name):
    d = json.load(open(os.path.join(FIX, name)))
    return d["run_id"], d["result"]


def _shift(run, seconds, run_id_suffix=""):
    """The same run moved in time (every event timestamp)."""
    out = json.loads(json.dumps(run))
    for e in out["events"]:
        t = datetime.fromisoformat(e["timestamp"]) + timedelta(seconds=seconds)
        e["timestamp"] = t.isoformat()
    return out


def _start_end(run):
    ts = [datetime.fromisoformat(e["timestamp"]).timestamp() for e in run["events"]]
    return min(ts), max(ts)


class FakeHA:
    """pipeline_debug list/get over a fixed set of runs (read-only)."""

    def __init__(self, runs: dict[str, tuple[str, dict]]):
        self.runs = runs   # run_id → (pipeline name, run)

    async def result(self, type, **kw):
        if type == "assist_pipeline/pipeline_debug/list":
            pname = kw["pipeline_id"]
            return {"pipeline_runs": [
                {"pipeline_run_id": rid, "timestamp": r["events"][0]["timestamp"]}
                for rid, (p, r) in self.runs.items() if p == pname]}
        if type == "assist_pipeline/pipeline_debug/get":
            return self.runs[kw["pipeline_run_id"]][1]
        raise AssertionError(type)


def _tempo(kronk_traces: dict[str, dict]):
    """Search returns the Kronk trace whose start is in the asked window
    (start, value) per trace; trace-by-id returns its routing."""
    def handler(request):
        path = request.url.path
        if path == "/api/search":
            s, e = float(request.url.params["start"]), float(request.url.params["end"])
            hits = [{"traceID": tid, "startTimeUnixNano": str(int(v["start"] * 1e9))}
                    for tid, v in kronk_traces.items() if s <= v["start"] <= e]
            return httpx.Response(200, json={"traces": hits})
        tid = path.rsplit("/", 1)[-1]
        v = kronk_traces[tid]
        return httpx.Response(200, json=_tempo_trace(v["rule"], v.get("action"), v.get("tools", ())))
    return httpx.MockTransport(handler)


@pytest.fixture
def tracer_env(tmp_path, monkeypatch):
    monkeypatch.setattr(vt, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(vt, "JOIN_DELAYS_S", [0])
    monkeypatch.setattr(vt, "RECORD_TEXT", False)
    exp = InMemorySpanExporter()
    dev_exp = InMemorySpanExporter()
    tracer = vt.VoiceTracer(emitter=vt.Emitter(exporter=exp, batch=False),
                            dev_emitter=vt.Emitter(exporter=dev_exp, batch=False,
                                                   service_name=vt.DEV_SERVICE_NAME))
    tracer.pipelines = {"kronk": "kronk", "kronk-dev": "kronk-dev"}
    return tracer, exp, dev_exp, tmp_path


def _scenario(gap_s=20.0):
    """A request Kronk answered (French Dinner Radio), then `gap_s` seconds
    after it ended, "Stop." which HA's local intent handled, same puck."""
    rid_a, run_a = _load("ha_run_kronk_answered.json")
    rid_b, run_b = _load("ha_run_ha_local_stop.json")
    a0, a1 = _start_end(run_a)
    b0, _ = _start_end(run_b)
    run_b = _shift(run_b, (a1 + gap_s) - b0)
    return (rid_a, run_a), (rid_b, run_b)


def _drive(tracer, ha, tempo, now):
    async def go():
        async with httpx.AsyncClient(transport=tempo, base_url="http://tempo:3200") as c:
            await tracer.fetch_voice_runs(ha, 0)
            await tracer.resolve_joins(c)
            tracer.flush_held(now=now)
    asyncio.run(go())


def _roots(exp):
    return {s.attributes.get("ha.pipeline_run_id"): s for s in exp.get_finished_spans() if s.name == "voice.run"}


def test_text_on_ha_local_stop_marks_kronks_play(tracer_env, monkeypatch):
    tracer, exp, _, _ = tracer_env
    monkeypatch.setattr(vt, "RECORD_TEXT", True)
    (rid_a, run_a), (rid_b, run_b) = _scenario(gap_s=20.0)
    a0, a1 = _start_end(run_a)
    ha = FakeHA({rid_a: ("kronk", run_a), rid_b: ("kronk", run_b)})
    tempo = _tempo({"a" * 32: {"start": a0 + 0.8, "rule": "music", "tools": ["play_music"]}})
    monkeypatch.setattr(vt.time, "time", lambda: a1 + 30)
    _drive(tracer, ha, tempo, now=a1 + 30)
    assert exp.get_finished_spans() == ()                      # both held: the window is open
    assert set(tracer.held) == {rid_a, rid_b}
    _drive(tracer, ha, tempo, now=a1 + 20 + 3 + marks.WINDOW_S + marks.GRACE_S + 1)
    roots = _roots(exp)
    a = roots[rid_a].attributes
    assert a["voice.corrected_by"] == "stop"
    assert a["voice.corrected_by_run_id"] == rid_b
    assert 19.0 <= a["voice.corrected_after_s"] <= 21.0
    assert a["kronk.rule"] == "music" and list(a["kronk.tools"]) == ["play_music"]
    assert a["voice.mark_basis"] == "rule_and_text"
    b = roots[rid_b].attributes
    assert b["voice.correction_kind"] == "stop" and b["voice.correction_of_run_id"] == rid_a
    assert vt.HEALTH["marked"] >= 1


def test_text_off_ha_local_stop_is_not_guessed(tracer_env, monkeypatch):
    """Criterion 9: HA's local intent carries no intent name, so without
    the words a local "Stop." is not identifiable and nothing is marked."""
    tracer, exp, _, _ = tracer_env
    (rid_a, run_a), (rid_b, run_b) = _scenario(gap_s=20.0)
    a0, a1 = _start_end(run_a)
    ha = FakeHA({rid_a: ("kronk", run_a), rid_b: ("kronk", run_b)})
    tempo = _tempo({"a" * 32: {"start": a0 + 0.8, "rule": "music"}})
    monkeypatch.setattr(vt.time, "time", lambda: a1 + 1000)
    _drive(tracer, ha, tempo, now=a1 + 1000)
    roots = _roots(exp)
    assert "voice.corrected_by" not in roots[rid_a].attributes
    assert "voice.transcript" not in roots[rid_a].attributes
    assert roots[rid_a].attributes["voice.mark_basis"] == "rule_only"


def test_text_off_kronk_not_that_marks_from_the_rule(tracer_env, monkeypatch):
    """Kronk answered the correction: its routing rule says music_another,
    no transcript needed."""
    tracer, exp, _, _ = tracer_env
    rid_a, run_a = _load("ha_run_kronk_answered.json")
    a0, a1 = _start_end(run_a)
    run_b = _shift(run_a, 30)
    rid_b = "01M12FIXTURELATERRUN000000"
    ha = FakeHA({rid_a: ("kronk", run_a), rid_b: ("kronk", run_b)})
    tempo = _tempo({"a" * 32: {"start": a0 + 0.8, "rule": "music"},
                    "b" * 32: {"start": a0 + 30.8, "rule": "music_another", "tools": ["play_music"]}})
    monkeypatch.setattr(vt.time, "time", lambda: a1 + 1000)
    _drive(tracer, ha, tempo, now=a1 + 1000)
    a = _roots(exp)[rid_a].attributes
    assert a["voice.corrected_by"] == "not_that"
    assert a["voice.corrected_after_s"] == pytest.approx(30 - (a1 - a0), abs=0.2)


def test_dev_runs_never_mark_production(tracer_env, monkeypatch):
    tracer, exp, dev_exp, _ = tracer_env
    monkeypatch.setattr(vt, "RECORD_TEXT", True)
    (rid_a, run_a), (rid_b, run_b) = _scenario(gap_s=10.0)
    a0, a1 = _start_end(run_a)
    ha = FakeHA({rid_a: ("kronk", run_a), rid_b: ("kronk-dev", run_b)})
    tempo = _tempo({"a" * 32: {"start": a0 + 0.8, "rule": "music"}})
    monkeypatch.setattr(vt.time, "time", lambda: a1 + 1000)
    _drive(tracer, ha, tempo, now=a1 + 1000)
    assert "voice.corrected_by" not in _roots(exp)[rid_a].attributes
    assert "voice.correction_kind" not in _roots(dev_exp)[rid_b].attributes


def test_a_late_correction_is_counted_and_logged(tracer_env, monkeypatch, caplog):
    """The earlier run was already exported (here: the correction is only
    fetched after the earlier run's window closed, as when HA is slow to
    list it): no mark, but a counter and a warning, never silent."""
    tracer, exp, _, _ = tracer_env
    monkeypatch.setattr(vt, "RECORD_TEXT", True)
    (rid_a, run_a), (rid_b, run_b) = _scenario(gap_s=20.0)
    a0, a1 = _start_end(run_a)
    tempo = _tempo({"a" * 32: {"start": a0 + 0.8, "rule": "music"}})
    monkeypatch.setattr(vt.time, "time", lambda: a1 + marks.HOLD_MAX_S)
    _drive(tracer, FakeHA({rid_a: ("kronk", run_a)}), tempo, now=a1 + marks.HOLD_MAX_S)
    assert rid_a in _roots(exp)                                   # forced out
    before = vt.HEALTH["marks_late"]
    with caplog.at_level("WARNING", logger="voice_tracer"):
        _drive(tracer, FakeHA({rid_a: ("kronk", run_a), rid_b: ("kronk", run_b)}), tempo,
               now=a1 + marks.HOLD_MAX_S + 1)
    assert vt.HEALTH["marks_late"] == before + 1
    assert "already exported; not marked" in caplog.text
    assert "voice.corrected_by" not in _roots(exp)[rid_a].attributes


def test_restart_refetches_held_runs_and_emits_each_once(tracer_env, monkeypatch):
    """Held runs are in the state file. A new process fetches them again
    (they are older than the 10 min backfill but inside HELD_RECOVERY_S),
    recomputes the mark, and emits each run once in total."""
    tracer, exp, _, tmp_path = tracer_env
    monkeypatch.setattr(vt, "RECORD_TEXT", True)
    (rid_a, run_a), (rid_b, run_b) = _scenario(gap_s=20.0)
    a0, a1 = _start_end(run_a)
    ha = FakeHA({rid_a: ("kronk", run_a), rid_b: ("kronk", run_b)})
    tempo = _tempo({"a" * 32: {"start": a0 + 0.8, "rule": "music"}})
    monkeypatch.setattr(vt.time, "time", lambda: a1 + 30)
    _drive(tracer, ha, tempo, now=a1 + 30)
    assert exp.get_finished_spans() == ()
    assert set(json.load(open(tmp_path / "state.json"))["held"]) == {rid_a, rid_b}

    # the process dies; a new one starts 10.5 minutes after the first run
    exp2 = InMemorySpanExporter()
    t2 = vt.VoiceTracer(emitter=vt.Emitter(exporter=exp2, batch=False))
    t2.pipelines = {"kronk": "kronk"}
    assert t2.prev_held == {rid_a, rid_b}
    now = a0 + 10.5 * 60
    monkeypatch.setattr(vt.time, "time", lambda: now)

    async def go():
        async with httpx.AsyncClient(transport=tempo, base_url="http://tempo:3200") as c:
            await t2.fetch_voice_runs(ha, now - vt.BACKFILL_S, also=t2.prev_held)
            t2.account_for_prev_held()
            await t2.resolve_joins(c)
            t2.flush_held(now=now)
            await t2.fetch_voice_runs(ha, now - vt.BACKFILL_S)     # the next poll finds nothing new
            await t2.resolve_joins(c)
            t2.flush_held(now=now + 60)
    asyncio.run(go())
    roots = [s for s in exp2.get_finished_spans() if s.name == "voice.run"]
    assert sorted(s.attributes["ha.pipeline_run_id"] for s in roots) == sorted([rid_a, rid_b])
    a = next(s for s in roots if s.attributes["ha.pipeline_run_id"] == rid_a).attributes
    assert a["voice.corrected_by"] == "stop"
    assert vt.HEALTH["held_lost"] == 0
    assert json.load(open(tmp_path / "state.json"))["held"] == []


def test_restart_reports_held_runs_ha_no_longer_lists(tracer_env, monkeypatch, caplog):
    tracer, exp, _, tmp_path = tracer_env
    (tmp_path / "state.json").write_text(json.dumps({"seen": [], "held": ["01GONE"]}))
    t2 = vt.VoiceTracer(emitter=None)
    t2.pipelines = {"kronk": "kronk"}
    before = vt.HEALTH["held_lost"]

    async def go():
        await t2.fetch_voice_runs(FakeHA({}), 0, also=t2.prev_held)
    asyncio.run(go())
    with caplog.at_level("WARNING", logger="voice_tracer"):
        t2.account_for_prev_held()
    assert vt.HEALTH["held_lost"] == before + 1
    assert "01GONE" in caplog.text
