"""Requests interleaving without the pipeline lock (sprint M9, criteria 10,
11, 12, 13).

10: many mixed requests at once (voice and display, different origins,
    some failing) through the app, each checked against the same request
    run alone: reply, what reached tool_service (origin, said), stored
    history, trace attributes, and the M2 failure flag.
11: a second request arriving during a first one's tool round does not
    change what the first may do (mirror pin, devops tool set).
12: for a request run alone every route sends the same bytes as before M9
    (tests/fixtures/m9/replies.json, written from b762fe9).
13: every failure path leaves no lock held and no waiter stuck.

Stubbed model (below the per-server lock: llm._stream_body) and
tool_service; no sockets.
"""
import asyncio
import json
import os
import random
import unittest.mock as mock_module
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("LLM_SERVICE_URL", "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

import agents  # noqa: E402
import errors  # noqa: E402
import llm  # noqa: E402
import model_lock  # noqa: E402
import origin  # noqa: E402
import telemetry  # noqa: E402
import tools  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

# tools.httpx is the httpx module: patching tools.httpx.AsyncClient swaps
# the class for everyone, so the test's own client is taken first.
RealAsyncClient = httpx.AsyncClient

FIXTURE = Path(__file__).parent / "fixtures" / "m9" / "replies.json"


def stamp(device, area):
    return f"[kronk-origin] device={device} area={area}"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class ToolService:
    """Records every call with its payload; answers after a random short
    wait so tool rounds of different requests overlap."""

    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def _answer(self, method, url, payload):
        path = url.replace(tools.TOOL_SERVICE_URL, "")
        await asyncio.sleep(random.random() / 50)
        self.calls.append((method, path, payload))
        if path == "/music":
            return _Resp(200, {"detail": f"Playing {payload.get('query')} on {payload.get('origin_area')}."})
        if path == "/music/now_playing":
            return _Resp(502, {"detail": "Home Assistant is unreachable (ConnectError)."})
        if path == "/music/control":
            return _Resp(200, {"detail": "Paused."})
        if path == "/magicmirror/update":
            return _Resp(200, {"message": "updating"})
        return _Resp(200, {"location": "Laurel", "summary": "sunny"})

    async def post(self, url, *a, json=None, **kw):
        return await self._answer("POST", url, json or {})

    async def get(self, url, *a, **kw):
        return await self._answer("GET", url, {})


async def model(messages, model_name, tools_arg=None):
    """Deterministic per request, slow enough to interleave: answers from
    the request's own words, so a leak shows as a wrong reply."""
    names = {t["function"]["name"] for t in (tools_arg or [])}
    last = messages[-1]
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    await asyncio.sleep(random.random() / 50)
    if "explode" in user:
        raise RuntimeError("LiteLLM returned 503: upstream down")
    if last["role"] == "tool":
        for word in f"tool said: {last['content']}".split(" "):
            await asyncio.sleep(0)
            yield {"token": word + " "}
    elif "play_music" in names and user.startswith("play "):
        yield {"tool_calls": [{"id": "p", "function": {"name": "play_music",
                                                        "arguments": {"query": user[5:]}}}]}
    elif "ask_home" in names and "kitchen" in user:
        yield {"tool_calls": [{"id": "h", "function": {"name": "ask_home", "arguments": {"query": user}}}]}
    elif "now_playing" in names and "kitchen" in user:
        yield {"tool_calls": [{"id": "n", "function": {"name": "now_playing",
                                                        "arguments": {"player": "kitchen"}}}]}
    else:
        for word in f"answer to {user}".split(" "):
            await asyncio.sleep(0)
            yield {"token": word + " "}
    yield {"usage": {"prompt_tokens": 10, "completion_tokens": 3}}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_LOCK", "server")
    monkeypatch.setenv("VOICE_STREAM", "off")
    real_open = open

    def fake_open(path, *a, **kw):
        if "/app/static/" in str(path):
            import io
            return io.StringIO("<html></html>")
        return real_open(path, *a, **kw)

    with mock_module.patch("builtins.open", side_effect=fake_open):
        import orchestrator.main as orch
        with patch.object(orch.metrics, "METRICS_DB", tmp_path / "metrics.db"), \
             patch.object(orch.sessions, "SESSIONS_DB", tmp_path / "sessions.db"), \
             patch.dict(orch.sessions._cache, {}, clear=True), \
             patch.object(model_lock, "_locks", {}), patch.object(model_lock, "_servers", {}), \
             patch.object(llm, "_stream_body", new=model), \
             patch("agents.weather_context", new=AsyncMock(return_value=None)):
            orch.sessions.init_db()
            yield orch


def _locks_free():
    return all(not lk.locked() and not lk.waiting() for lk in model_lock._locks.values())


# Each request: (mount, text, stamp or None, session or None)
REQUESTS = []
for n in range(6):
    REQUESTS += [
        ("voice", f"what is {n} plus {n}", stamp(f"dev{n}", f"Room{n}"), None),
        ("display", f"what is {n} times {n}", None, None),
        ("voice", f"play jazz number {n}", stamp(f"dev{n}", f"Room{n}"), None),
        ("display", f"play blues number {n}", None, None),
        ("voice", "what's playing in the kitchen", stamp(f"kit{n}", "Kitchen"), None),   # fails
        ("display", "what's playing in the kitchen", None, None),                          # fails
        ("voice", "pause", stamp(f"pa{n}", f"Hall{n}"), None),                             # direct
        ("message", f"what is {n} minus one", None, f"web{n % 2}"),
        ("display", f"please explode {n}", None, None),                                    # model error
    ]


async def send(client, req):
    mount, text, st, session = req
    if mount == "message":
        r = await client.post("/message", json={"text": text, "session_id": session})
        toks = []
        for line in r.text.splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                toks.append(json.loads(line[5:]).get("token", ""))
        return "".join(toks)
    path = "/voice/api/chat" if mount == "voice" else "/api/chat"
    msgs = ([{"role": "system", "content": st}] if st else []) + [{"role": "user", "content": text}]
    r = await client.post(path, json={"model": "kronk", "messages": msgs, "stream": False})
    assert r.status_code == 200, r.text
    assert "message" in r.json(), (req, r.text[:300])
    return r.json()["message"]["content"]


def _norm(s):
    import re
    return re.sub(r"\[rid [0-9a-f]+\]", "[rid R]", s)


@pytest.mark.asyncio
async def test_many_mixed_requests_at_once_match_each_run_alone(app):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    random.seed(4)
    transport = httpx.ASGITransport(app=app.app)

    async def run(reqs, concurrent):
        ts = ToolService()
        exporter = InMemorySpanExporter()
        telemetry.enable_for_tests(exporter)
        app.sessions._cache.clear()
        for k in ("web0", "web1"):
            app.sessions.clear(k)
        try:
            with patch("tools.httpx.AsyncClient", new=ts), patch.object(telemetry, "RECORD_TEXT", True):
                async with RealAsyncClient(transport=transport, base_url="http://t") as c:
                    if concurrent:
                        replies = await asyncio.gather(*(send(c, r) for r in reqs))
                    else:
                        replies = [await send(c, r) for r in reqs]
        finally:
            telemetry.ENABLED = False
        roots = {}
        for s in exporter.get_finished_spans():
            if s.name.startswith("pipeline."):
                a = s.attributes
                roots.setdefault(a.get("kronk.input"), []).append(
                    (a.get("kronk.voice"), a.get("kronk.origin.device_id"), a.get("kronk.origin.area"),
                     a.get("kronk.route"), s.status.status_code.name))
        stored = {k: [m["content"] for m in app.sessions.window(k)] for k in ("web0", "web1")}
        return [_norm(x) for x in replies], ts.calls, roots, stored

    alone = await run(REQUESTS, concurrent=False)
    together = await run(REQUESTS, concurrent=True)
    assert _locks_free()

    # replies, one by one
    assert together[0] == alone[0]
    # every voice failure got the friendly phrasing, every display failure not
    for (mount, text, _, _), reply in zip(REQUESTS, together[0]):
        if "kitchen" in text:
            assert (errors.FRIENDLY_TOOL_PHRASING.split("\n")[0][:40] in reply) == (mount == "voice") \
                or "couldn't" in reply, (mount, reply)
        if "explode" in text:
            assert reply.startswith("[coordinator agent error") or reply.startswith("Error"), reply
    # what reached tool_service: same calls with the same origin and words
    key = lambda c: json.dumps(c, sort_keys=True)  # noqa: E731
    assert sorted(map(key, together[1])) == sorted(map(key, alone[1]))
    for method, path, payload in together[1]:
        if path == "/music":
            n = payload["query"].split()[-1]
            if payload.get("origin_device"):
                assert payload["origin_device"] == f"dev{n}" and payload["origin_area"] == f"Room{n}"
                assert payload["said"] == f"play jazz number {n}"
            else:
                assert payload["said"] == f"play blues number {n}"
    # trace roots: same attributes per input
    assert {k: sorted(v) for k, v in together[2].items()} == {k: sorted(v) for k, v in alone[2].items()}
    # stored history: each session holds its own exchanges, whole
    for sid in ("web0", "web1"):
        msgs = together[3][sid]
        pairs = sorted(zip(msgs[0::2], msgs[1::2]))
        assert pairs == sorted(zip(alone[3][sid][0::2], alone[3][sid][1::2]))
        for q, a in pairs:
            assert a.strip() == f"answer to {q}"


# ── Criterion 11: timing cannot widen what a request may do ──────────────────

@pytest.mark.asyncio
async def test_a_request_in_its_tool_round_keeps_its_own_limits(app):
    """Request A (devops: no web tools; its turn has no words of its own, so
    no mirror update) is held in a tool round while request B (research,
    and the pinned mirror sentence) runs to the end. A's next round then
    calls web_search and update_magicmirror: both refused, as alone."""
    gate, in_round = asyncio.Event(), asyncio.Event()
    ts = ToolService()
    a_rounds = []

    async def two_models(messages, model_name, tools_arg=None):
        names = {t["function"]["name"] for t in (tools_arg or [])}
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if "remote_exec" in names:                      # request A, the devops agent
            a_rounds.append(messages[-1]["role"])
            if len(a_rounds) == 1:
                yield {"tool_calls": [{"id": "r", "function": {"name": "remote_exec",
                                                                "arguments": {"command": "uptime"}}}]}
            elif len(a_rounds) == 2:
                yield {"tool_calls": [
                    {"id": "w", "function": {"name": "web_search", "arguments": {"query": "x"}}},
                    {"id": "m", "function": {"name": "update_magicmirror", "arguments": {}}}]}
            else:
                yield {"token": "done"}
        else:
            yield {"token": f"answer to {user}"}
        yield {"usage": {}}

    async def slow_execute(name, args):
        if name == "remote_exec":
            in_round.set()
            await gate.wait()
        return await real_execute(name, args)

    real_execute = tools.execute
    with patch.object(llm, "_stream_body", new=two_models), patch("tools.httpx.AsyncClient", new=ts), \
         patch("agents.tools.execute", new=slow_execute):
        orch = app
        token = origin.said.set(None)
        try:
            a = asyncio.ensure_future(_collect(orch, "check the uptime of the magic mirror", said=None))
        finally:
            origin.said.reset(token)
        await asyncio.wait_for(in_round.wait(), timeout=2)
        b1 = await _collect(orch, "search the web for magic mirrors")
        b2 = await _collect(orch, "update the magic mirror")      # direct, pinned sentence
        gate.set()
        await asyncio.wait_for(a, timeout=2)
    assert "answer to" in b1
    posts = [p for m, p, _ in ts.calls if m == "POST"]
    assert posts.count("/magicmirror/update") == 1               # B's own, not A's
    assert not any(p.startswith("/search") for p in posts)       # A's web_search never ran
    assert _locks_free()


async def _collect(orch, text, said="same", voice=False):
    out = [ev async for ev in orch._run_pipeline(
        text, [], said=text if said == "same" else said, voice=voice,
        transport="shim", pipeline_name="pipeline.test")]
    return "".join(e["text"] for e in out if e["type"] == "token")


# ── Criterion 12: same bytes as before M9, request run alone ─────────────────

def test_every_route_sends_the_bytes_it_sent_before_m9(app, monkeypatch):
    import sys
    sys.path.insert(0, str(FIXTURE.parent))
    try:
        import replies as replies_mod
    finally:
        sys.path.pop(0)
    monkeypatch.delenv("ERROR_STYLE", raising=False)
    monkeypatch.delenv("ERROR_STYLE_VOICE", raising=False)
    with patch.object(llm, "_stream_body", new=replies_mod.model):
        now = replies_mod.run_all(app)
    before = json.loads(FIXTURE.read_text())
    assert set(now) == set(before)
    differ = [k for k in before if now[k] != before[k]]
    assert differ == [], differ[:5]


# ── Criterion 13: failure paths release everything ───────────────────────────

FAILURES = {
    "model_error": lambda: _raising_model(RuntimeError("LiteLLM returned 503: upstream down")),
    "exception_inside_the_lock": lambda: _raising_model(KeyError("bug"), after_tokens=2),
}


def _raising_model(exc, after_tokens=0):
    async def body(messages, model_name, tools_arg=None):
        for _ in range(after_tokens):
            yield {"token": "x "}
        raise exc
        yield  # pragma: no cover
    return body


@pytest.mark.parametrize("kind", list(FAILURES))
@pytest.mark.asyncio
async def test_a_failing_holder_frees_the_lock_for_the_waiter(app, kind):
    """A request fails while holding its server's lock, with another one
    waiting: the waiter is served, nothing stays held."""
    started = asyncio.Event()
    failing = FAILURES[kind]()

    async def body(messages, model_name, tools_arg=None):
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if user.startswith("fail"):
            started.set()
            await asyncio.sleep(0.05)
            async for x in failing(messages, model_name, tools_arg):
                yield x
        else:
            yield {"token": "ok"}
            yield {"usage": {}}

    with patch.object(llm, "_stream_body", new=body):
        a = asyncio.ensure_future(_collect(app, "fail now"))
        await asyncio.wait_for(started.wait(), timeout=2)
        b = asyncio.ensure_future(_collect(app, "what is two plus two"))
        out_a, out_b = await asyncio.wait_for(asyncio.gather(a, b), timeout=3)
    assert out_b == "ok"
    assert "error" in out_a.lower()
    assert _locks_free()


@pytest.mark.asyncio
async def test_a_tool_error_frees_everything(app):
    async def boom(name, args):
        raise RuntimeError("handler exploded")
    with patch("agents.tools.execute", new=boom), patch("tools.execute", new=boom):
        out = await _collect(app, "play some jazz")
    assert out
    assert _locks_free()
    with patch("tools.httpx.AsyncClient", new=ToolService()):
        assert "answer to" in await _collect(app, "what is two plus two")


@pytest.mark.asyncio
async def test_a_cancelled_waiter_and_a_cancelled_holder_free_everything(app):
    hold = asyncio.Event()

    async def body(messages, model_name, tools_arg=None):
        await hold.wait()
        yield {"token": "late"}
        yield {"usage": {}}

    with patch.object(llm, "_stream_body", new=body):
        holder = asyncio.ensure_future(_collect(app, "first"))
        await asyncio.sleep(0.05)
        waiter = asyncio.ensure_future(_collect(app, "second"))
        await asyncio.sleep(0.05)
        assert any(lk.waiting() for lk in model_lock._locks.values())
        waiter.cancel()
        holder.cancel()
        for t in (waiter, holder):
            with pytest.raises(asyncio.CancelledError):
                await t
    assert _locks_free()
    with patch.object(llm, "_stream_body", new=model):
        assert "answer to" in await _collect(app, "what is two plus two")
