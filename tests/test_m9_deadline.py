"""The voice deadline and client disconnects (sprint M9 step 4).

Voice requests (the speech mount) have a total deadline (config/voice.yaml
deadline_s). When it passes, the pipeline is stopped and one sentence is
spoken; a tool call already sent is not retried, and the sentence never
says an action did not happen once a tool that changes something was sent.
When a client disconnects, its request's pipeline is cancelled on every
chat route (client_watch). Either way the model lock is released and the
trace says how the request ended (kronk.ended).

The model is faked below the per-server lock (llm._stream_body), so the
real lock runs. docs/sprint/M9-criteria.md criteria 6, 7, 13.
"""
import asyncio
import dataclasses
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

import config_loader  # noqa: E402
import errors  # noqa: E402
import llm  # noqa: E402
import model_lock  # noqa: E402
import run_state  # noqa: E402
import telemetry  # noqa: E402
import tools  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

STAMP = "[kronk-origin] device=office_dev area=Office"
DEADLINE = 0.4


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class ToolService:
    """httpx.AsyncClient stand-in. `hang` paths never answer."""

    def __init__(self, hang=()):
        self.hang = set(hang)
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def _answer(self, method, url, json=None):
        path = url.replace(tools.TOOL_SERVICE_URL, "")
        self.calls.append((method, path, json))
        if path in self.hang:
            await asyncio.Event().wait()
        if path == "/music":
            return _Resp(200, {"detail": "Playing jazz on the Office speaker."})
        if path.startswith("/weather"):
            return _Resp(200, {"location": "Laurel", "summary": "sunny"})
        return _Resp(404, {"detail": "not faked"})

    async def post(self, url, *a, json=None, **kw):
        return await self._answer("POST", url, json)

    async def get(self, url, *a, **kw):
        return await self._answer("GET", url)

    def posts(self):
        return [p for m, p, _ in self.calls if m == "POST"]


class Server:
    """llm._stream_body stand-in. The home agent (offered play_music)
    calls `home_call` in its first round; every other generation writes
    `text`, or hangs when `hang` is set (after writing `before`)."""

    def __init__(self, text="It is sunny.", hang=False, before="", home_call=None):
        self.text, self.hang, self.before, self.home_call = text, hang, before, home_call
        self.generations = 0

    async def body(self, messages, model, tools_arg=None):
        self.generations += 1
        names = {t["function"]["name"] for t in (tools_arg or [])}
        if self.home_call and self.home_call[0] in names and messages[-1]["role"] == "user":
            yield {"tool_calls": [{"id": "c1", "function": {"name": self.home_call[0],
                                                             "arguments": self.home_call[1]}}]}
            yield {"usage": {}}
            return
        if self.hang:
            if self.before:
                yield {"token": self.before}
            await asyncio.Event().wait()
        for word in self.text.split(" "):
            yield {"token": word + " "}
        yield {"usage": {}}


def _with_deadline(seconds):
    cfg = config_loader.current()
    voice = cfg.voice.model_copy(update={"deadline_s": seconds})
    return patch.object(config_loader, "_current", dataclasses.replace(cfg, voice=voice))


def _locks_free():
    return all(not lk.locked() and not lk.waiting() for lk in model_lock._locks.values())


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_LOCK", "server")
    monkeypatch.setenv("VOICE_STREAM", "off")
    open_orig = open

    def _fake_open(path, *a, **kw):
        if "/app/static/" in str(path):
            import io
            return io.StringIO("<html></html>")
        return open_orig(path, *a, **kw)

    with mock_module.patch("builtins.open", side_effect=_fake_open):
        import orchestrator.main as orch
        with patch.object(orch.metrics, "METRICS_DB", tmp_path / "metrics.db"), \
             patch.object(orch.sessions, "SESSIONS_DB", tmp_path / "sessions.db"), \
             patch.dict(orch.sessions._cache, {}, clear=True), \
             patch.object(model_lock, "_locks", {}), patch.object(model_lock, "_servers", {}), \
             patch("agents.weather_context", new=AsyncMock(return_value=None)), \
             _with_deadline(DEADLINE):
            orch.sessions.init_db()
            yield orch


@pytest.fixture
def client(app_env):
    with TestClient(app_env.app, raise_server_exceptions=True) as c:
        yield c


def voice(c, text, stream=False, system=STAMP):
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": text}]
    r = c.post("/voice/api/chat", json={"model": "kronk", "messages": msgs, "stream": stream})
    assert r.status_code == 200, r.text
    if stream:
        objs = [json.loads(x) for x in r.text.split("\n") if x]
        assert objs[-1]["done"] is True and sum(o["done"] for o in objs) == 1
        return "".join(o["message"]["content"] for o in objs if not o["done"])
    body = r.json()
    assert body["done"] is True
    return body["message"]["content"]


def _events(caplog, name):
    out = []
    for r in caplog.records:
        msg = r.getMessage()
        if f'"event": "{name}"' in msg:
            out.append(json.loads(msg))
    return out


# ── Criterion 6: the deadline ────────────────────────────────────────────────

@pytest.mark.parametrize("stream", [False, True])
def test_a_stuck_model_ends_with_the_sentence_and_frees_the_lock(client, caplog, stream):
    caplog.set_level(logging.INFO, logger="kronk.events")
    server = Server(hang=True)
    with patch.object(llm, "_stream_body", new=server.body), \
         patch("tools.httpx.AsyncClient", new=ToolService()):
        reply = voice(client, "what is the capital of France", stream=stream)
    assert reply == errors.FRIENDLY_DEADLINE
    assert _locks_free()
    (ended,) = _events(caplog, "request_ended")
    assert ended["reason"] == "deadline" and ended["tools_sent"] == []
    # the next request is served
    with patch.object(llm, "_stream_body", new=Server().body):
        assert voice(client, "what is the capital of France") == "It is sunny."


def test_deadline_after_an_acting_tool_was_sent_does_not_deny_it(client, caplog):
    """play_music was sent and tool_service has not answered: the sentence
    says it may still go through, and the call is not repeated."""
    caplog.set_level(logging.INFO, logger="kronk.events")
    ts = ToolService(hang={"/music"})
    server = Server(home_call=("play_music", {"query": "jazz"}))
    with patch.object(llm, "_stream_body", new=server.body), \
         patch("tools.httpx.AsyncClient", new=ts):
        reply = voice(client, "play some jazz")
    assert reply == errors.FRIENDLY_DEADLINE_SENT
    for denial in ("didn't", "did not", "couldn't", "could not", "wasn't", "nothing was", "stopped"):
        assert denial not in reply.lower(), denial
    assert ts.posts() == ["/music"]                     # sent once, never retried
    assert _locks_free()
    (ended,) = _events(caplog, "request_ended")
    assert ended["tools_sent"] == ["play_music"]
    assert not _events(caplog, "tool_error")            # not reported as failed


def test_deadline_after_a_read_only_tool_says_it_stopped(client):
    ts = ToolService(hang={"/weather"})
    server = Server(home_call=("get_weather", {"location": "Paris"}))
    with patch.object(llm, "_stream_body", new=server.body), \
         patch("tools.httpx.AsyncClient", new=ts):
        reply = voice(client, "what's the weather like in Paris")
    assert reply == errors.FRIENDLY_DEADLINE
    assert _locks_free()


def test_deadline_sentence_by_what_was_sent():
    ro = tools.READ_ONLY_TOOLS
    assert errors.deadline_text([], ro, 25, "r", errors.FRIENDLY) == errors.FRIENDLY_DEADLINE
    assert errors.deadline_text(["web_search", "fetch_url"], ro, 25, "r", errors.FRIENDLY) \
        == errors.FRIENDLY_DEADLINE
    for acting in ("play_music", "control_music", "rate_music", "remember_music_preference",
                   "shopping_list_add", "shopping_list_remove", "shopping_list_clear",
                   "update_magicmirror", "remote_exec", "generate_diagram", "news_brief",
                   "a_future_tool"):
        assert errors.deadline_text(["web_search", acting], ro, 25, "r", errors.FRIENDLY) \
            == errors.FRIENDLY_DEADLINE_SENT, acting
    debug = errors.deadline_text(["play_music"], ro, 25, "abc", errors.DEBUG)
    assert "25 s deadline" in debug and "may still take effect: play_music" in debug and "abc" in debug


def test_every_tool_is_classified():
    """READ_ONLY_TOOLS names only real tools; everything else counts as acting."""
    assert tools.READ_ONLY_TOOLS <= set(tools._HANDLERS)


def test_news_brief_is_not_read_only():
    """A tool on READ_ONLY_TOOLS lets the deadline sentence say "I stopped".
    news_brief sends POST /news/refresh when asked to refresh, which
    regenerates and stores the brief after the request is gone (M9-e2e B1),
    so it counts as acting."""
    import inspect
    assert "/news/refresh" in inspect.getsource(tools._tool_news_brief)
    assert "news_brief" not in tools.READ_ONLY_TOOLS
    assert errors.deadline_text(["news_brief"], tools.READ_ONLY_TOOLS, 25, "r", errors.FRIENDLY) \
        == errors.FRIENDLY_DEADLINE_SENT


def test_streamed_writer_keeps_what_was_sent_and_ends_with_the_sentence(client, monkeypatch):
    monkeypatch.setenv("VOICE_STREAM", "on")
    first = "Paris is the capital. It is on the Seine. It has many bridges. "
    server = Server(hang=True, before=first)
    with patch.object(llm, "_stream_body", new=server.body), \
         patch("tools.httpx.AsyncClient", new=ToolService()):
        reply = voice(client, "tell me about paris", stream=True)
    assert reply.endswith(errors.FRIENDLY_DEADLINE)
    assert reply.startswith("Paris is the capital.")
    assert "bridges" not in reply                      # held, never spoken: dropped
    assert _locks_free()


def test_display_requests_have_no_deadline(client):
    async def slow(messages, model, tools_arg=None):
        await asyncio.sleep(DEADLINE + 0.3)
        yield {"token": "Paris."}
        yield {"usage": {}}
    with patch.object(llm, "_stream_body", new=slow):
        r = client.post("/api/chat", json={"model": "kronk", "stream": False,
                                           "messages": [{"role": "user", "content": "capital of France?"}]})
    assert r.json()["message"]["content"] == "Paris."


def test_deadline_trace_is_findable(client):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    try:
        with patch.object(llm, "_stream_body", new=Server(hang=True).body):
            voice(client, "what is the capital of France")
    finally:
        telemetry.ENABLED = False
    root = next(s for s in exporter.get_finished_spans() if s.name == "pipeline.shim")
    assert root.attributes["kronk.ended"] == "deadline"
    assert root.status.status_code.name == "ERROR"
    gen = [s for s in exporter.get_finished_spans() if s.name.startswith("llm.")]
    assert gen and all(s.end_time for s in gen)


def test_deadline_is_in_config_with_its_default():
    assert config_loader.Voice().deadline_s == 25
    import yaml
    from pathlib import Path
    shipped = yaml.safe_load((Path(os.environ["KRONK_CONFIG_DIR"]) / "voice.yaml").read_text())
    assert shipped["deadline_s"] == 25


# ── Criterion 7: the client goes away ────────────────────────────────────────

ROUTES = [
    ("/message", {"text": "what is the capital of France", "session_id": "m9-test"}),
    ("/api/chat", {"model": "kronk", "stream": False,
                   "messages": [{"role": "user", "content": "what is the capital of France"}]}),
    ("/api/chat", {"model": "kronk", "stream": True,
                   "messages": [{"role": "user", "content": "what is the capital of France"}]}),
    ("/v1/chat/completions", {"model": "kronk", "stream": False,
                              "messages": [{"role": "user", "content": "what is the capital of France"}]}),
    ("/v1/chat/completions", {"model": "kronk", "stream": True,
                              "messages": [{"role": "user", "content": "what is the capital of France"}]}),
    ("/voice/api/chat", {"model": "kronk", "stream": False,
                         "messages": [{"role": "user", "content": "what is the capital of France"}]}),
    ("/voice/api/chat", {"model": "kronk", "stream": True,
                         "messages": [{"role": "user", "content": "what is the capital of France"}]}),
]


async def asgi(app, path, body, gone_after=None):
    """One request straight through the ASGI app. With gone_after, the
    client disconnects that many seconds after sending the body."""
    sent, delivered = [], False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        if gone_after is None:
            await asyncio.Event().wait()
        await asyncio.sleep(gone_after)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 1), "server": ("test", 80),
             "headers": [(b"content-type", b"application/json"), (b"host", b"test")]}
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return sent


@pytest.mark.parametrize("path,body", ROUTES, ids=[f"{p}-{b.get('stream', 'sse')}" for p, b in ROUTES])
@pytest.mark.asyncio
async def test_disconnect_cancels_the_pipeline_on_every_route(app_env, caplog, path, body, monkeypatch):
    caplog.set_level(logging.INFO, logger="kronk.events")
    monkeypatch.setenv("VOICE_STREAM", "on")
    with _with_deadline(None), patch.object(llm, "_stream_body", new=Server(hang=True).body):
        await asgi(app_env.app, path, body, gone_after=0.2)
    ended = _events(caplog, "request_ended")
    assert [e["reason"] for e in ended] == ["client_gone"], ended
    assert not _events(caplog, "request_complete")
    assert _locks_free()
    if path == "/message":
        assert app_env.sessions.window("m9-test") == []     # nothing half-stored
    # a request sent right after is served
    with patch.object(llm, "_stream_body", new=Server(text="Paris.").body):
        sent = await asgi(app_env.app, path, body)
    assert b"Paris" in b"".join(m.get("body", b"") for m in sent)


@pytest.mark.asyncio
async def test_disconnect_during_a_tool_call_is_not_a_retry_or_a_failure(app_env, caplog):
    caplog.set_level(logging.INFO, logger="kronk.events")
    ts = ToolService(hang={"/music"})
    with _with_deadline(None), \
         patch.object(llm, "_stream_body", new=Server(home_call=("play_music", {"query": "jazz"})).body), \
         patch("tools.httpx.AsyncClient", new=ts):
        await asgi(app_env.app, "/voice/api/chat",
                   {"model": "kronk", "stream": False,
                    "messages": [{"role": "user", "content": "play some jazz"}]}, gone_after=0.2)
    assert ts.posts() == ["/music"]
    assert not _events(caplog, "tool_error") and not _events(caplog, "failure_spoken_friendly")
    (ended,) = _events(caplog, "request_ended")
    assert ended["reason"] == "client_gone" and ended["tools_sent"] == ["play_music"]
    assert _locks_free()


@pytest.mark.asyncio
async def test_disconnect_trace_says_the_client_went_away(app_env):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    try:
        with _with_deadline(None), patch.object(llm, "_stream_body", new=Server(hang=True).body):
            await asgi(app_env.app, "/voice/api/chat",
                       {"model": "kronk", "stream": False,
                        "messages": [{"role": "user", "content": "hello"}]}, gone_after=0.2)
    finally:
        telemetry.ENABLED = False
    root = next(s for s in exporter.get_finished_spans() if s.name == "pipeline.shim")
    assert root.attributes["kronk.ended"] == "client_gone"
    assert root.status.status_code.name != "ERROR"


@pytest.mark.asyncio
async def test_a_completed_response_is_not_a_disconnect(app_env, caplog):
    """The connection closing after the whole response was sent is not a
    client going away: nothing is cancelled or marked."""
    caplog.set_level(logging.INFO, logger="kronk.events")
    with patch.object(llm, "_stream_body", new=Server(text="Paris.").body):
        sent = await asgi(app_env.app, "/api/chat",
                          {"model": "kronk", "stream": False,
                           "messages": [{"role": "user", "content": "capital?"}]}, gone_after=0.0)
    assert b"Paris" in b"".join(m.get("body", b"") for m in sent)
    assert not _events(caplog, "request_ended")
    assert len(_events(caplog, "request_complete")) == 1


@pytest.mark.asyncio
async def test_non_chat_paths_pass_straight_through(app_env):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/api/agents", "raw_path": b"/api/agents", "query_string": b"",
             "root_path": "", "client": ("127.0.0.1", 1), "server": ("test", 80), "headers": []}
    await app_env.app(scope, receive, send)
    assert sent[0]["status"] == 200
