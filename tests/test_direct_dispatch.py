"""Direct dispatch in the pipeline (sprint M7, criteria 4-11).

For the five rules whose match fixes the whole tool call (routing.decide's
`direct`), _run_pipeline runs the tool through tools.execute and speaks the
result with agents._terminal_speech, with no model call and outside
_llm_lock. These tests drive the real FastAPI app on every mount with a
fake tool_service and a model client that fails if it is called.

docs/sprint/M7-criteria.md; docs/features/agents-as-tools-routing.md.
"""
import asyncio
import json
import logging
import os
import unittest.mock as mock_module
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("LLM_SERVICE_URL", "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

from fastapi.testclient import TestClient  # noqa: E402

import agents  # noqa: E402
import errors  # noqa: E402
import origin  # noqa: E402
import routing  # noqa: E402
import telemetry  # noqa: E402
import tools  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

STAMP = "[kronk-origin] device=office_dev area=Office"
MOUNTS = ["message", "api_chat", "voice_api_chat", "openai"]
VOICE = {"voice_api_chat"}

# utterance -> (tool, args, tool_service endpoint)
CASES = {
    "pause":                   ("control_music", {"action": "pause"}, "/music/control"),
    "what song is this":       ("now_playing", {}, "/music/now_playing"),
    "thumbs up":               ("rate_music", {"thumb": "up"}, "/music/rate"),
    "not that":                ("play_music", {"query": "another", "kind": "another"}, "/music"),
    "update the magic mirror": ("update_magicmirror", {}, "/magicmirror/update"),
}

# What tool_service answers, per endpoint and scenario.
OK = {
    "/music/control": (200, {"detail": "Paused on the Office speaker."}),
    "/music/now_playing": (200, {"detail": "Teardrop by Massive Attack, on the Office speaker."}),
    "/music/rate": (200, {"detail": "Thumbs up for Teardrop by Massive Attack."}),
    "/music": (200, {"dry_run": True, "detail": "Dry run: would play Bonobo, for chill, on Office. "
                                                 "Nothing was sent to the speaker."}),
    "/magicmirror/update": (200, {"message": "updating (backup taken)"}),
}
FAIL = {
    "/music/control": (409, {"detail": "Nothing is playing on the Office speaker."}),
    "/music/now_playing": (502, {"detail": "Home Assistant is unreachable (ConnectError)."}),
    "/music/rate": (409, {"detail": "Nothing is playing on the Office speaker."}),
    "/music": (409, {"detail": "I didn't pick what's playing, so I've got nothing to swap in."}),
    "/magicmirror/update": (502, {"detail": "Mirror preflight failed: SSH key not found at /keys/x"}),
}


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeToolService:
    """httpx.AsyncClient stand-in: records (method, path, json) and answers
    from `answers`; `down=True` raises like a stopped tool_service."""

    def __init__(self, answers=None, down=False):
        self.answers = answers if answers is not None else OK
        self.down = down
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, *a, json=None, **kw):
        path = url.replace(tools.TOOL_SERVICE_URL, "")
        self.calls.append(("POST", path, json))
        if self.down:
            raise httpx.ConnectError("All connection attempts failed")
        status, body = self.answers.get(path, (404, {"detail": "not faked"}))
        return _Resp(status, body)

    async def get(self, url, *a, **kw):
        path = url.replace(tools.TOOL_SERVICE_URL, "")
        self.calls.append(("GET", path, None))
        if self.down:
            raise httpx.ConnectError("All connection attempts failed")
        return _Resp(404, {"detail": "not faked"})

    def posts(self):
        return [(p, j) for m, p, j in self.calls if m == "POST"]


class ModelMustNotRun:
    """agents.llm.stream stand-in that fails the request if it is called."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, model, tools=None):
        self.calls += 1

        async def gen():
            raise AssertionError("the model was called on a direct-dispatch request")
            yield  # pragma: no cover
        return gen()


def scripted_home(call):
    """A home model that makes `call` (tool, args), and a coordinator that
    answers in words: the agent path as it ran before M7."""
    log = []

    async def stream(messages, model, tools_arg=None):
        names = {t["function"]["name"] for t in (tools_arg or [])}
        if call[0] in names:
            log.append("home")
            yield {"tool_calls": [{"id": "c1", "function": {"name": call[0], "arguments": dict(call[1])}}]}
        else:
            log.append("coordinator")
            yield {"token": "ok"}
        yield {"usage": {}}
    stream.log = log
    return stream


def _agent_path_decide(text):
    """routing.decide as it behaved before M7: same route, no direct call."""
    d = _REAL_DECIDE(text)
    return routing.Decision(d.route, d.rule)


_REAL_DECIDE = routing.decide


@pytest.fixture
def client(tmp_path):
    open_orig = open

    def _fake_open(path, *a, **kw):
        if "/app/static/" in str(path):
            import io
            return io.StringIO("<html></html>")
        return open_orig(path, *a, **kw)

    with mock_module.patch("builtins.open", side_effect=_fake_open):
        import orchestrator.main as orch
        metrics, sessions = orch.metrics, orch.sessions   # the modules main uses
        with mock_module.patch.object(metrics, "METRICS_DB", tmp_path / "metrics.db"), \
             mock_module.patch.object(sessions, "SESSIONS_DB", tmp_path / "sessions.db"), \
             patch("agents.weather_context", new=AsyncMock(return_value=None)) as wx:
            sessions.clear(orch.WEBUI_SESSION)
            with TestClient(orch.app, raise_server_exceptions=True) as c:
                c.orch, c.sessions, c.weather = orch, sessions, wx
                yield c
            sessions.clear(orch.WEBUI_SESSION)


def send(c, mount, text, system=None):
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": text}]
    if mount == "message":
        r = c.post("/message", json={"text": text})
        out = []
        for line in r.text.splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                out.append(json.loads(line[5:]).get("token", ""))
        return "".join(out)
    path = {"api_chat": "/api/chat", "voice_api_chat": "/voice/api/chat",
            "openai": "/v1/chat/completions"}[mount]
    r = c.post(path, json={"model": "kronk", "messages": msgs, "stream": False})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["choices"][0]["message"]["content"] if mount == "openai" else body["message"]["content"]


def run_direct(c, mount, text, fake, system=None):
    model = ModelMustNotRun()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        reply = send(c, mount, text, system)
    return reply, model


def run_agent_path(c, mount, text, fake, system=None):
    call = CASES[text][:2]
    stream = scripted_home(call)
    with patch("agents.llm.stream", new=stream), patch("tools.httpx.AsyncClient", new=fake), \
         patch.object(routing, "decide", new=_agent_path_decide):
        reply = send(c, mount, text, system)
    assert stream.log == ["home"], stream.log
    return reply


# ── Criterion 4: each rule succeeds with the model client failing ────────────

EXPECTED_OK = {
    "pause": "Paused on the Office speaker.",
    "what song is this": "Teardrop by Massive Attack, on the Office speaker.",
    "thumbs up": "Thumbs up for Teardrop by Massive Attack.",
    "not that": "Dry run: would play Bonobo, for chill, on Office. Nothing was sent to the speaker.",
    "update the magic mirror": "The magic mirror is updating (backup taken).",
}


@pytest.mark.parametrize("mount", MOUNTS)
@pytest.mark.parametrize("text", list(CASES))
def test_direct_rule_answers_without_the_model(client, mount, text):
    tool, args, endpoint = CASES[text]
    fake = FakeToolService()
    reply, model = run_direct(client, mount, text, fake,
                              system=STAMP if mount in VOICE else None)
    assert model.calls == 0
    assert reply == EXPECTED_OK[text]
    assert [p for p, _ in fake.posts()] == [endpoint]
    sent = fake.posts()[0][1] or {}
    assert {k: sent[k] for k in args} == args or (tool == "play_music" and sent["kind"] == "another")
    client.weather.assert_not_awaited()    # no model, so no weather context


# ── Criterion 5: the same sentence as the agent path ─────────────────────────

SCENARIOS = {"ok": dict(answers=OK), "fail": dict(answers=FAIL), "down": dict(down=True)}


@pytest.mark.parametrize("mount", MOUNTS)
@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("text", list(CASES))
def test_direct_reply_is_the_agent_paths_sentence(client, mount, scenario, text):
    system = STAMP if mount in VOICE else None
    direct, _ = run_direct(client, mount, text, FakeToolService(**SCENARIOS[scenario]), system)
    agent = run_agent_path(client, mount, text, FakeToolService(**SCENARIOS[scenario]), system)
    assert direct == agent, (mount, scenario, text)


@pytest.mark.parametrize("text", list(CASES))
def test_failure_sentences_by_style(client, text):
    """Friendly on the speech mount, debug elsewhere; the music failure
    sentences are the same in both (M2, criterion 8 there)."""
    endpoint = CASES[text][2]
    detail = FAIL[endpoint][1]["detail"]
    voice, _ = run_direct(client, "voice_api_chat", text, FakeToolService(answers=FAIL), STAMP)
    debug, _ = run_direct(client, "api_chat", text, FakeToolService(answers=FAIL))
    lead = {"pause": "I couldn't do that.", "what song is this": "I couldn't tell.",
            "thumbs up": "I couldn't rate that.", "not that": "I couldn't play that."}
    if text in lead:
        assert voice == debug == f"{lead[text]} {detail}"
    else:
        assert voice == errors.FRIENDLY_MIRROR_NOT_STARTED
        assert debug == f"I couldn't update the magic mirror. {detail}"


# ── Criterion 6: the speaker that heard the request is still the target ─────

@pytest.mark.parametrize("stamp", [STAMP, None], ids=["stamped", "unstamped"])
@pytest.mark.parametrize("text", [t for t in CASES if t != "update the magic mirror"])
def test_origin_reaches_tool_service_as_on_the_agent_path(client, text, stamp):
    direct_fake, agent_fake = FakeToolService(), FakeToolService()
    run_direct(client, "voice_api_chat", text, direct_fake, stamp)
    run_agent_path(client, "voice_api_chat", text, agent_fake, stamp)
    assert direct_fake.posts() == agent_fake.posts()
    sent = direct_fake.posts()[0][1]
    if stamp:
        assert (sent["origin_device"], sent["origin_area"]) == ("office_dev", "Office")
    else:
        assert "origin_device" not in sent and "origin_area" not in sent


def test_named_room_on_another_is_the_player(client):
    fake = FakeToolService()
    run_direct(client, "voice_api_chat", "not that in the kitchen", fake, STAMP)
    sent = fake.posts()[0][1]
    assert (sent["player"], sent["kind"], sent["origin_area"]) == ("kitchen", "another", "Office")


# ── Criterion 7: the M4b pin holds on the direct path ────────────────────────
# The odd request shapes (tests/test_mm_update_pin.py) now run the direct
# path (their llm_log is []); these pin the guard from this side too.

@pytest.mark.parametrize("mount", ["api_chat", "voice_api_chat", "openai"])
def test_mirror_update_needs_the_users_own_last_message(client, mount):
    path = {"api_chat": "/api/chat", "voice_api_chat": "/voice/api/chat",
            "openai": "/v1/chat/completions"}[mount]
    fake, model = FakeToolService(), ModelMustNotRun()
    msgs = [{"role": "user", "content": "update the magic mirror"},
            {"role": "assistant", "content": "Done."}]
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=fake):
        r = client.post(path, json={"model": "kronk", "messages": msgs, "stream": False})
    body = r.json()
    reply = body["choices"][0]["message"]["content"] if mount == "openai" else body["message"]["content"]
    assert reply == tools.MM_UPDATE_REFUSAL
    assert fake.posts() == [] and model.calls == 0


# ── Criterion 8: a failure is spoken and the turn ends ───────────────────────

@pytest.mark.parametrize("text", list(CASES))
def test_failed_tool_ends_the_turn(client, text):
    for answers in (dict(answers=FAIL), dict(down=True)):
        fake = FakeToolService(**answers)
        reply, model = run_direct(client, "voice_api_chat", text, fake, STAMP)
        assert model.calls == 0 and reply
        assert len(fake.posts()) == 1          # one attempt, no retry


def test_raising_direct_call_is_spoken_not_retried(client):
    async def boom(name, args):
        raise RuntimeError("handler exploded")
    model = ModelMustNotRun()
    with patch("agents.llm.stream", new=model), patch.object(tools, "execute", new=boom):
        voice = send(client, "voice_api_chat", "pause", STAMP)
        debug = send(client, "api_chat", "pause")
    assert model.calls == 0
    assert voice == "Sorry — something went wrong on my end. I've noted the details."
    assert debug.startswith("Error: the pipeline failed unexpectedly (RuntimeError: handler exploded)")


# ── Criterion 9: outside _llm_lock ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_direct_request_does_not_wait_for_the_model_lock(monkeypatch):
    # The pipeline-wide lock exists only with MODEL_LOCK=pipeline since M9
    # step 2; tests/test_m9_lock.py covers the per-server locks.
    monkeypatch.setenv("MODEL_LOCK", "pipeline")
    import orchestrator.main as orch

    async def collect(text):
        return [ev async for ev in orch._run_pipeline(
            text, [], said=text, voice=True, transport="shim", pipeline_name="pipeline.test")]

    async def answer(messages, model, tools_arg=None):
        yield {"token": "hi"}
        yield {"usage": {}}

    fake = FakeToolService()
    with patch("tools.httpx.AsyncClient", new=fake), patch("agents.llm.stream", new=answer), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch.object(orch.telemetry, "end_pipeline"):
        await orch._llm_lock.acquire()          # another request is generating
        try:
            events = await asyncio.wait_for(collect("pause"), timeout=2)
            assert [e["text"] for e in events if e["type"] == "token"] == ["Paused on the Office speaker."]
            waiting = asyncio.ensure_future(collect("hello"))   # not direct: must queue
            await asyncio.sleep(0.3)
            assert not waiting.done()
        finally:
            orch._llm_lock.release()
        events = await asyncio.wait_for(waiting, timeout=2)
        assert [e["text"] for e in events if e["type"] == "token"] == ["hi"]


# ── Criterion 10: telemetry, events, history ─────────────────────────────────

def test_trace_shape_and_events(client, caplog):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    caplog.set_level(logging.INFO, logger="kronk.events")
    try:
        run_direct(client, "voice_api_chat", "pause", FakeToolService(), STAMP)
    finally:
        telemetry.ENABLED = False
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "pipeline.shim")
    tool_span = next(s for s in spans if s.name == "tool.control_music")
    decide = next(s for s in spans if s.name == "routing.decide")
    assert tool_span.parent.span_id == root.context.span_id
    assert decide.parent.span_id == root.context.span_id
    assert not [s for s in spans if s.name.startswith(("agent.", "llm."))]
    assert root.attributes.get("kronk.route") == "home"

    evs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kronk.events"]
    by = {e["event"]: e for e in evs}
    assert by["route_shortcut"]["direct"] is True and by["route_shortcut"]["tool"] == "control_music"
    assert (by["tool_call"]["agent"], by["tool_call"]["tool"], by["tool_call"]["direct"]) == (
        "home", "control_music", True)
    assert by["tool_complete"]["tool"] == "control_music"
    assert by["request_complete"]["route"] == "home"
    assert "agent_complete" not in by


def test_webui_history_records_the_exchange(client):
    run_direct(client, "message", "pause", FakeToolService())
    hist = client.sessions.window(client.orch.WEBUI_SESSION)
    assert hist[-2:] == [{"role": "user", "content": "pause"},
                         {"role": "assistant", "content": "Paused on the Office speaker."}]


def test_sse_timing_names_the_tool_and_no_model(client):
    model = ModelMustNotRun()
    with patch("agents.llm.stream", new=model), patch("tools.httpx.AsyncClient", new=FakeToolService()):
        r = client.post("/message", json={"text": "pause"})
    evs = [json.loads(line[5:]) for line in r.text.splitlines()
           if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]")]
    timing = next(e["timing"] for e in evs if "timing" in e)
    assert timing["model"] == "none (direct call)"
    assert [s["tool"] for s in timing["stages"]] == ["routing", "control_music"]
    assert any(e.get("narration") == "pausing the music..." for e in evs)


# ── Criterion 11: negatives stay on the model path ───────────────────────────

@pytest.mark.parametrize("text", ["stop the timer", "pause my subscription",
                                  "what is the next holiday", "pause the music in the office",
                                  "play another one"])
def test_negatives_go_to_a_model(client, text):
    calls = []

    async def answer(messages, model, tools_arg=None):
        calls.append(model)
        yield {"token": "ok"}
        yield {"usage": {}}
    fake = FakeToolService()
    with patch("agents.llm.stream", new=answer), patch("tools.httpx.AsyncClient", new=fake):
        reply = send(client, "voice_api_chat", text, STAMP)
    assert calls and reply == "ok."        # render.to_speech closes the sentence
    assert fake.posts() == []
