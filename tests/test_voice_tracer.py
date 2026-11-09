"""voice_tracer: HA pipeline-debug and automation-trace payloads → span
trees, deterministic ids, the voice↔Kronk and voice↔blueprint joins, and
the emitter's explicit timestamps. Fixtures are real payloads recorded
2026-11-08 (the Anime Pop incident run; a Portishead blueprint run)."""
import json
import os

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from voice_tracer import convert
from voice_tracer import main as vt

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
RUN_ID = "01M6YG1W1STYKNT41DF2377DH3"


@pytest.fixture
def run():
    return json.load(open(os.path.join(FIX, "ha_pipeline_run_anime_pop.json")))["result"]


@pytest.fixture
def automation():
    return json.load(open(os.path.join(FIX, "ha_automation_trace_portishead.json")))


def _by_key(specs):
    return {s.key: s for s in specs}


def test_pipeline_run_becomes_staged_span_tree(run):
    specs = convert.pipeline_run_spans(run, RUN_ID, "kronk")
    k = _by_key(specs)
    assert [s.name for s in specs] == ["voice.run", "voice.stt", "voice.stt.listen",
                                       "voice.stt.transcribe", "voice.intent", "voice.tts"]
    root = k["run"]
    assert root.parent is None
    assert round((root.end_ns - root.start_ns) / 1e9, 1) == 73.7
    assert root.attrs["ha.satellite_id"] == "assist_satellite.home_assistant_voice_0ac919_assist_satellite"
    assert root.attrs["kronk.origin.device_id"] == "e0949788663255952ce7778975527882"
    assert root.attrs["voice.handled_by"] == "ha_local"      # HA's built-in intent took it
    assert root.attrs["voice.wake_detection"] == "satellite"  # the known gap, stated
    assert root.attrs["voice.transcript_chars"] == 110
    assert "voice.transcript" not in root.attrs              # no text by default

    stt = k["stt"]
    assert stt.parent == "run" and round((stt.end_ns - stt.start_ns) / 1e9, 1) == 10.2
    assert stt.attrs["voice.vad_end_ms"] == 9800
    assert k["stt.listen"].parent == "stt"
    assert round((k["stt.listen"].end_ns - k["stt.listen"].start_ns) / 1e9, 1) == 9.8
    assert round((k["stt.transcribe"].end_ns - k["stt.transcribe"].start_ns) / 1e9, 1) == 0.5

    intent = k["intent"]
    assert round((intent.end_ns - intent.start_ns) / 1e9, 1) == 63.4     # the incident's 63 s
    assert intent.attrs["voice.processed_locally"] is True
    assert intent.attrs["voice.engine"] == "conversation.ollama_conversation"
    assert k["tts"].attrs["voice.engine"] == "tts.piper"
    assert k["tts"].attrs["voice.streamed"] is True


def test_record_text_adds_transcript(run):
    specs = convert.pipeline_run_spans(run, RUN_ID, "kronk", record_text=True)
    root = specs[0]
    assert root.attrs["voice.transcript"].startswith("Play Anime Pop Radio.")
    assert root.attrs["voice.speech"] == "Playing media"


def test_ids_are_deterministic(run):
    a = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID))
    b = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID))
    assert [(s.trace_id, s.span_id) for s in a] == [(s.trace_id, s.span_id) for s in b]
    assert len({s.span_id for s in a}) == len(a)
    other = convert.assign_ids("pipeline", "01OTHERRUN", convert.pipeline_run_spans(run, "01OTHERRUN"))
    assert other[0].trace_id != a[0].trace_id


def test_error_event_marks_run_and_open_stage():
    run = {"events": [
        {"type": "run-start", "timestamp": "2026-11-08T20:00:00.000+00:00", "data": {"pipeline": "p"}},
        {"type": "intent-start", "timestamp": "2026-11-08T20:00:00.100+00:00",
         "data": {"engine": "conversation.ollama_conversation", "device_id": "d1"}},
        {"type": "error", "timestamp": "2026-11-08T20:00:05.000+00:00",
         "data": {"code": "intent-failed", "message": "Unexpected error during intent recognition"}},
        {"type": "run-end", "timestamp": "2026-11-08T20:00:05.001+00:00", "data": {}},
    ]}
    specs = convert.pipeline_run_spans(run, "r")
    k = _by_key(specs)
    assert k["run"].status == convert.STATUS_ERROR
    assert "intent-failed" in k["run"].status_message
    assert k["intent"].status == convert.STATUS_ERROR
    assert k["run"].attrs["voice.handled_by"] == "kronk"    # engine says Kronk, no intent-end


def test_kronk_join_links_intent_span(run):
    specs = convert.pipeline_run_spans(run, RUN_ID)
    dev, s, e = convert.intent_window(specs)
    assert dev == "e0949788663255952ce7778975527882"
    assert round(e - s, 1) == 63.4

    convert.apply_kronk_join(specs, ["bad9680a9c8813c8583817c6eea0ef66"])
    k = _by_key(specs)
    assert k["intent"].attrs["kronk.trace_id"] == "bad9680a9c8813c8583817c6eea0ef66"
    assert k["intent"].links == ["bad9680a9c8813c8583817c6eea0ef66"]
    assert k["run"].attrs["kronk.join_matches"] == 1
    assert "kronk.join_ambiguous" not in k["run"].attrs

    specs2 = convert.pipeline_run_spans(run, RUN_ID)
    convert.apply_kronk_join(specs2, ["a" * 32, "b" * 32])
    assert _by_key(specs2)["run"].attrs["kronk.join_ambiguous"] is True
    assert _by_key(specs2)["run"].attrs["kronk.join_matches"] == 2

    specs3 = convert.pipeline_run_spans(run, RUN_ID)
    convert.apply_kronk_join(specs3, [])
    assert _by_key(specs3)["run"].attrs["kronk.join_matches"] == 0
    assert "kronk.trace_id" not in _by_key(specs3)["intent"].attrs


def test_automation_trace_becomes_step_spans(automation):
    specs = convert.automation_trace_spans(automation)
    root = specs[0]
    assert root.name == "blueprint.run"
    assert root.attrs["bp.trigger_id"] == "artist"
    assert root.attrs["bp.version"] == "20250404-kronk12"
    assert root.attrs["bp.outcome"] == "played"
    assert root.attrs["bp.target"] == "media_player.home_assistant_voice_0ab117"
    assert root.attrs["bp.media_name_chars"] == len("Portishead")
    assert "bp.media_name" not in root.attrs
    assert round((root.end_ns - root.start_ns) / 1e6) == 53
    names = [s.name for s in specs[1:]]
    assert names == ["ha.media_player.shuffle_set", "ha.music_assistant.play_media", "bp.wait"]
    play = next(s for s in specs if s.name == "ha.music_assistant.play_media")
    assert play.parent == "run"
    assert play.attrs["bp.media_type"] == "artist"
    assert play.attrs["ha.entity_id"] == ["media_player.home_assistant_voice_0ab117"]
    assert round((play.end_ns - play.start_ns) / 1e6) == 49
    shuffle = next(s for s in specs if s.name == "ha.media_player.shuffle_set")
    assert shuffle.status == convert.STATUS_ERROR     # HA recorded an error on that step
    # steps stay chronological and never overlap the run
    for s in specs[1:]:
        assert root.start_ns <= s.start_ns <= s.end_ns <= root.end_ns


def test_handoff_to_kronk_is_named(automation):
    tr = json.loads(json.dumps(automation))
    tr["trace"]["action/2/then/0"] = [{
        "path": "action/2/then/0", "timestamp": "2026-11-08T21:25:32.950000+00:00",
        "result": {"params": {"domain": "conversation", "service": "process",
                              "service_data": {"agent_id": "conversation.ollama_conversation", "text": "x"}}}}]
    specs = convert.automation_trace_spans(tr)
    assert specs[0].attrs["bp.outcome"] == "handed_to_kronk"
    hand = next(s for s in specs if s.name == "bp.handoff_kronk")
    assert hand.attrs["bp.agent_id"] == "conversation.ollama_conversation"


def test_blueprint_joins_voice_run_by_intent_window(run, automation):
    voice = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID))
    # move the blueprint run inside the voice run's intent window
    tr = json.loads(json.dumps(automation))
    tr["timestamp"] = {"start": "2026-11-08T21:12:08.500000+00:00", "finish": "2026-11-08T21:12:09.000000+00:00"}
    bp = convert.assign_ids("automation", tr["run_id"], convert.automation_trace_spans(tr))
    assert convert.join_blueprint(voice, bp) is True
    assert voice[0].attrs["voice.handled_by"] == "blueprint"
    assert voice[0].attrs["ha.blueprint_run_id"] == tr["run_id"]
    assert format(bp[0].trace_id, "032x") in voice[0].links
    assert bp[0].attrs["ha.pipeline_run_id"] == RUN_ID
    assert format(voice[0].trace_id, "032x") in bp[0].links
    # a run outside the window does not join
    bp2 = convert.assign_ids("automation", "zz", convert.automation_trace_spans(automation))
    voice2 = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID))
    assert convert.join_blueprint(voice2, bp2) is False
    # a blueprint run joins one voice run only
    voice3 = convert.assign_ids("pipeline", "01OTHER", convert.pipeline_run_spans(run, "01OTHER"))
    assert convert.join_blueprint(voice3, bp) is False
    assert bp[0].attrs["ha.pipeline_run_id"] == RUN_ID


def test_blueprint_never_joins_the_next_utterance(run, automation):
    """Live 2026-11-08: two text utterances 0.4 s apart; the blueprint run of
    the first landed on the second with a ±0.5 s slack. A blueprint run
    starts inside the intent stage that triggered it, never before it."""
    voice = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID))
    _, s, e = convert.intent_window(voice)
    tr = json.loads(json.dumps(automation))
    from datetime import datetime, timezone
    before = datetime.fromtimestamp(s - 0.3, tz=timezone.utc).isoformat()
    tr["timestamp"] = {"start": before, "finish": before}
    bp = convert.assign_ids("automation", "early", convert.automation_trace_spans(tr))
    assert convert.join_blueprint(voice, bp) is False
    assert voice[0].attrs["voice.handled_by"] == "ha_local"


def test_kronk_handled_run_keeps_its_label_when_blueprint_handed_off(run, automation):
    voice = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID))
    voice[0].attrs["voice.handled_by"] = "kronk"
    next(x for x in voice if x.key == "intent").attrs["voice.handled_by"] = "kronk"
    tr = json.loads(json.dumps(automation))
    tr["timestamp"] = {"start": "2026-11-08T21:12:08.500000+00:00", "finish": "2026-11-08T21:12:16.000000+00:00"}
    tr["trace"]["action/2/then/0"] = [{"path": "action/2/then/0", "timestamp": "2026-11-08T21:12:08.600000+00:00",
                                       "result": {"params": {"domain": "conversation", "service": "process",
                                                             "service_data": {"agent_id": "conversation.ollama_conversation"}}}}]
    bp = convert.assign_ids("automation", tr["run_id"], convert.automation_trace_spans(tr))
    assert convert.join_blueprint(voice, bp) is True
    assert voice[0].attrs["voice.handled_by"] == "blueprint_then_kronk"


def test_emitter_writes_explicit_times_parents_and_links(run):
    exp = InMemorySpanExporter()
    em = vt.Emitter(exporter=exp, batch=False)
    specs = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID, "kronk"))
    convert.apply_kronk_join(specs, ["bad9680a9c8813c8583817c6eea0ef66"])
    em.emit(specs)
    spans = {s.name: s for s in exp.get_finished_spans()}
    assert set(spans) == {"voice.run", "voice.stt", "voice.stt.listen", "voice.stt.transcribe",
                          "voice.intent", "voice.tts"}
    root = spans["voice.run"]
    assert root.context.trace_id == specs[0].trace_id
    assert root.context.span_id == specs[0].span_id
    assert root.start_time == specs[0].start_ns and root.end_time == specs[0].end_ns
    assert spans["voice.stt"].parent.span_id == root.context.span_id
    assert spans["voice.stt.listen"].parent.span_id == spans["voice.stt"].context.span_id
    assert len({s.context.trace_id for s in spans.values()}) == 1
    (link,) = spans["voice.intent"].links
    assert format(link.context.trace_id, "032x") == "bad9680a9c8813c8583817c6eea0ef66"
    assert spans["voice.intent"].attributes["kronk.trace_id"] == "bad9680a9c8813c8583817c6eea0ef66"
    assert spans["voice.run"].resource.attributes["service.name"] == "kronk-voice-tracer"


def test_tempo_search_query_shape(monkeypatch):
    """The join query asks Tempo for Kronk pipeline roots of this device in
    the intent window, and filters the hits by start time."""
    import httpx

    seen = {}

    def handler(request: httpx.Request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"traces": [
            # Tempo strips leading zeros from ids in search results
            {"traceID": "12e537d82941f6648282c5324dce278", "startTimeUnixNano": str(int(1000.5 * 1e9))},
            {"traceID": "bbbb", "startTimeUnixNano": str(int(2000.0 * 1e9))},
        ]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await vt.find_kronk_traces(c, "dev1", 1000.0, 1010.0)

    import asyncio
    ids = asyncio.run(go())
    assert ids == ["012e537d82941f6648282c5324dce278"]
    q = seen["params"]["q"]
    assert 'resource.service.name = "kronk-orchestrator"' in q
    assert 'span.kronk.origin.device_id = "dev1"' in q
    assert 'name =~ "pipeline' in q
    assert seen["params"]["start"] == "995" and seen["params"]["end"] == "1016"


def test_catchall_handoff_still_looks_up_the_kronk_trace(run, automation, tmp_path, monkeypatch):
    """Live 2026-11-08: a catch-all utterance was emitted as blueprint_then_kronk
    with no Kronk trace, because the blueprint join ran after the decision
    whether to search Tempo. HA says processed_locally=True for those runs;
    the blueprint's hand-off is what makes them Kronk's."""
    import asyncio
    import httpx

    monkeypatch.setattr(vt, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(vt, "JOIN_DELAYS_S", [0])
    tracer = vt.VoiceTracer(emitter=None)

    voice = convert.assign_ids("pipeline", RUN_ID, convert.pipeline_run_spans(run, RUN_ID))   # ha_local
    _, s, e = convert.intent_window(voice)
    tr = json.loads(json.dumps(automation))
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
    tr["timestamp"] = {"start": iso(s + 0.01), "finish": iso(s + 4.0)}
    tr["trace"]["action/2/then/0"] = [{"path": "action/2/then/0", "timestamp": iso(s + 0.02),
                                       "result": {"params": {"domain": "conversation", "service": "process",
                                                             "service_data": {"agent_id": "conversation.ollama_conversation"}}}}]
    bp = convert.assign_ids("automation", tr["run_id"], convert.automation_trace_spans(tr))
    tracer.recent_bp[tr["run_id"]] = bp
    tracer.pending_voice[RUN_ID] = {"specs": voice, "ends_at": 0, "attempt": 0, "key": f"pipeline:{RUN_ID}"}

    def handler(request):
        return httpx.Response(200, json={"traces": [{"traceID": "abc", "startTimeUnixNano": str(int((s + 0.5) * 1e9))}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await tracer.resolve_joins(c)

    asyncio.run(go())
    assert RUN_ID not in tracer.pending_voice                     # emitted
    assert voice[0].attrs["voice.handled_by"] == "blueprint_then_kronk"
    assert voice[0].attrs["kronk.trace_id"] == "abc".zfill(32)
    assert voice[0].attrs["kronk.join_matches"] == 1
    assert bp[0].attrs["ha.pipeline_run_id"] == RUN_ID
    assert json.load(open(tmp_path / "state.json"))["seen"] == [f"pipeline:{RUN_ID}"]
