"""The model lock, narrowed (sprint M9).

Step 1: routing, the weather and local context and direct dispatch run
before the lock; only the model phases wait. The wait is recorded as a
`lock.wait` span and a `model_lock` event. Since step 2 that lock is the
rollback (MODEL_LOCK=pipeline); step 1's tests run with it.

Step 2: one lock per model server, held inside llm.stream for one
generation. These tests fake the HTTP part (llm._stream_body), so the real
lock wrapper runs.

docs/sprint/M9-criteria.md; docs/features/model-locks.md.
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

import model_lock
import telemetry

pytestmark = pytest.mark.usefixtures("no_network")


def _orch():
    import orchestrator.main as orch
    return orch


@pytest.fixture(autouse=True)
def fresh_lock():
    """A pipeline lock per test: an asyncio.Lock binds to the event loop of
    its first contended wait, and each async test has its own loop."""
    orch = _orch()
    with patch.object(orch, "_llm_lock", asyncio.Lock()):
        yield


async def _collect(text, voice=False, said=None):
    orch = _orch()
    return [ev async for ev in orch._run_pipeline(
        text, [], said=said or text, voice=voice, transport="shim", pipeline_name="pipeline.test")]


def _tokens(events):
    return "".join(e["text"] for e in events if e["type"] == "token")


def _answer(text="hi"):
    async def stream(messages, model, tools=None):
        yield {"token": text}
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 1}}
    return stream


def _events(caplog, name):
    out = []
    for r in caplog.records:
        msg = r.getMessage()
        if f'"event": "{name}"' in msg:
            out.append(json.loads(msg))
    return out


# ── Step 1: model-free work runs before the lock ─────────────────────────────

@pytest.mark.asyncio
async def test_routing_and_context_run_while_another_request_holds_the_lock(monkeypatch):
    monkeypatch.setenv("MODEL_LOCK", "pipeline")
    orch = _orch()
    classified = asyncio.Event()
    real_classify = orch.routing.classify

    async def classify(text, history):
        route = await real_classify(text, history)
        classified.set()
        return route

    wx = AsyncMock(return_value=None)
    with patch("agents.llm.stream", new=_answer()), patch("agents.weather_context", new=wx), \
         patch.object(orch.routing, "classify", new=classify), patch("agents.metrics.record"):
        await orch._llm_lock.acquire()          # another request is generating
        try:
            task = asyncio.ensure_future(_collect("what is the capital of France"))
            await asyncio.wait_for(classified.wait(), timeout=2)
            wx.assert_awaited()                 # the weather context was fetched too
            await asyncio.sleep(0.1)
            assert not task.done()              # but the model phase waits
        finally:
            orch._llm_lock.release()
        events = await asyncio.wait_for(task, timeout=2)
    assert _tokens(events) == "hi"
    assert not orch._llm_lock.locked()


@pytest.mark.asyncio
async def test_the_wait_is_a_span_and_an_event(caplog, monkeypatch):
    monkeypatch.setenv("MODEL_LOCK", "pipeline")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    orch = _orch()
    exporter = InMemorySpanExporter()
    telemetry.enable_for_tests(exporter)
    caplog.set_level(logging.INFO, logger="kronk.events")
    try:
        with patch("agents.llm.stream", new=_answer()), \
             patch("agents.weather_context", new=AsyncMock(return_value=None)), \
             patch("agents.metrics.record"):
            await orch._llm_lock.acquire()
            task = asyncio.ensure_future(_collect("what is two plus two", voice=True))
            await asyncio.sleep(0.2)
            orch._llm_lock.release()
            await asyncio.wait_for(task, timeout=2)
    finally:
        telemetry.ENABLED = False
    spans = exporter.get_finished_spans()
    wait = next(s for s in spans if s.name == "lock.wait")
    root = next(s for s in spans if s.name == "pipeline.test")
    assert wait.parent.span_id == root.context.span_id
    assert wait.attributes["kronk.priority"] == "voice"
    assert wait.attributes["kronk.wait_ms"] >= 150
    (ev,) = _events(caplog, "model_lock")
    assert ev["priority"] == "voice" and ev["wait_ms"] >= 150 and ev["lock"]


@pytest.mark.asyncio
async def test_a_cancelled_waiter_records_it_and_holds_nothing(caplog):
    caplog.set_level(logging.INFO, logger="kronk.events")
    lock = asyncio.Lock()
    await lock.acquire()

    async def waiter():
        async with model_lock.held(lock, "test", "display"):
            pass

    t = asyncio.ensure_future(waiter())
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    lock.release()
    assert not lock.locked()
    (ev,) = _events(caplog, "model_lock")
    assert ev["acquired"] is False

# ── Step 2: one lock per model server, held for one generation ──────────────

class _Gate:
    """A model whose generations block until released, per model name; it
    records when each generation starts and ends."""

    def __init__(self):
        self.log = []
        self.open = {}

    def gate(self, model):
        return self.open.setdefault(model, asyncio.Event())

    def stream(self):
        gate = self

        async def stream(messages, model, tools=None):
            gate.log.append(("start", model))
            await gate.gate(model).wait()
            gate.log.append(("end", model))
            yield {"token": f"from {model}"}
            yield {"usage": {}}
        return stream


def _llm_stream_with(fake_body):
    """llm.stream's lock wrapper around a fake body (no HTTP)."""
    import llm

    async def body(messages, model, tools=None):
        async for x in fake_body(messages, model, tools):
            yield x
    return patch.object(llm, "_stream_body", new=body)


@pytest.mark.asyncio
async def test_two_servers_do_not_wait_for_each_other(monkeypatch):
    import llm
    monkeypatch.setenv("MODEL_LOCK", "server")
    gate = _Gate()
    with _llm_stream_with(gate.stream()), \
         patch.object(model_lock, "_locks", {}), \
         patch.object(model_lock, "_servers", {"a": "h:1", "b": "h:2"}):
        async def run(model):
            return [x async for x in llm.stream([], model)]
        ta = asyncio.ensure_future(run("a"))
        tb = asyncio.ensure_future(run("b"))
        await asyncio.sleep(0.05)
        assert ("start", "a") in gate.log and ("start", "b") in gate.log
        gate.gate("a").set(); gate.gate("b").set()
        await asyncio.wait_for(asyncio.gather(ta, tb), timeout=2)


@pytest.mark.asyncio
async def test_one_server_runs_generations_one_at_a_time(monkeypatch):
    import llm
    monkeypatch.setenv("MODEL_LOCK", "server")
    gate = _Gate()
    with _llm_stream_with(gate.stream()), \
         patch.object(model_lock, "_locks", {}), \
         patch.object(model_lock, "_servers", {"a": "h:1", "a2": "h:1"}):
        async def run(model):
            return [x async for x in llm.stream([], model)]
        t1 = asyncio.ensure_future(run("a"))
        await asyncio.sleep(0.02)
        t2 = asyncio.ensure_future(run("a2"))      # another name, same server
        await asyncio.sleep(0.05)
        assert gate.log == [("start", "a")]
        gate.gate("a").set()
        await asyncio.sleep(0.05)
        assert gate.log == [("start", "a"), ("end", "a"), ("start", "a2")]
        gate.gate("a2").set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2)


def test_servers_come_from_the_litellm_catalog():
    cat = [{"name": "gemma-4-e4b", "api_base": "http://127.0.0.1:11438/v1"},
           {"name": "devstral-2512-q4", "api_base": "http://127.0.0.1:11440/v1"},
           {"name": "alias", "api_base": "http://127.0.0.1:11438/v1"},
           {"name": "odd", "api_base": ""}]
    with patch.object(model_lock, "_servers", None), patch.object(model_lock, "_locks", {}), \
         patch("servers.load_catalog", return_value=cat):
        assert model_lock.server_for("gemma-4-e4b") == "127.0.0.1:11438"
        assert model_lock.server_for("devstral-2512-q4") == "127.0.0.1:11440"
        assert model_lock.server_for("odd") == "odd"
        assert model_lock.server_for("unknown") == "unknown"
        assert model_lock.for_model("alias")[1] is model_lock.for_model("gemma-4-e4b")[1]
        assert model_lock.for_model("devstral-2512-q4")[1] is not model_lock.for_model("gemma-4-e4b")[1]


def test_an_unreadable_catalog_gives_one_lock_per_model_name():
    with patch.object(model_lock, "_servers", None), \
         patch("servers.load_catalog", side_effect=OSError("gone")):
        assert model_lock.server_for("gemma-4-e4b") == "gemma-4-e4b"


@pytest.mark.parametrize("raw,want", [("server", "server"), ("pipeline", "pipeline"),
                                      (" Pipeline ", "pipeline"), ("SERVER", "server"),
                                      ("off", "pipeline"), ("", "pipeline")])
def test_mode_switch(monkeypatch, caplog, raw, want):
    monkeypatch.setenv("MODEL_LOCK", raw)
    assert model_lock.mode() == want


def test_mode_defaults_to_server(monkeypatch):
    monkeypatch.delenv("MODEL_LOCK", raising=False)
    assert model_lock.mode() == "server"


class _ScriptedServer:
    """llm._stream_body stand-in. The home agent (offered get_weather) calls
    the tool in its first round and answers in its second; anything else
    answers at once. Each generation is logged with its model."""

    def __init__(self):
        self.log = []

    async def body(self, messages, model, tools=None):
        names = {t["function"]["name"] for t in (tools or [])}
        self.log.append(("gen", messages[-1]["role"]))
        if "get_weather" in names and messages[-1]["role"] == "user":
            yield {"tool_calls": [{"id": "w1", "function": {"name": "get_weather",
                                                             "arguments": {"location": "Paris"}}}]}
        else:
            yield {"token": "answer"}
        yield {"usage": {}}


@pytest.mark.asyncio
async def test_a_tool_wait_holds_no_lock(monkeypatch):
    """Request A's tool call is stuck at tool_service; request B's
    generation on the same model server runs and finishes meanwhile."""
    monkeypatch.setenv("MODEL_LOCK", "server")
    import llm
    import tools as tools_mod
    server = _ScriptedServer()
    tool_started, tool_release = asyncio.Event(), asyncio.Event()

    async def slow_execute(name, args):
        tool_started.set()
        await tool_release.wait()
        return tools_mod.ToolResult("[Weather for Paris] sunny")

    with patch.object(llm, "_stream_body", new=server.body), \
         patch.object(model_lock, "_locks", {}), patch.object(model_lock, "_servers", {}), \
         patch("agents.tools.execute", new=slow_execute), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("agents.metrics.record"):
        a = asyncio.ensure_future(_collect("what's the weather like in Paris"))
        await asyncio.wait_for(tool_started.wait(), timeout=2)
        b = await asyncio.wait_for(_collect("what is the capital of France"), timeout=2)
        assert _tokens(b) == "answer"
        assert not a.done()
        tool_release.set()
        assert _tokens(await asyncio.wait_for(a, timeout=2)) == "answer"
    assert all(not lk.locked() for lk in model_lock._locks.values())


@pytest.mark.asyncio
async def test_direct_request_does_not_wait_for_a_busy_server(monkeypatch):
    monkeypatch.setenv("MODEL_LOCK", "server")
    import tools as tools_mod
    lock = asyncio.Lock()
    await lock.acquire()           # every model server is busy
    with patch.object(model_lock, "for_model", return_value=("busy", lock)), \
         patch("tools.execute", new=AsyncMock(return_value=tools_mod.ToolResult("[Now playing: x]"))):
        events = await asyncio.wait_for(_collect("what song is this"), timeout=2)
    assert _tokens(events)
    lock.release()


@pytest.mark.asyncio
async def test_the_switch_restores_one_request_at_a_time(monkeypatch):
    """MODEL_LOCK=pipeline: a second request waits for the first's whole
    model phase, even on another model server; with server, two requests
    on two servers (one lock each, handed out in turn) overlap."""
    import llm
    for mode, overlap in (("pipeline", False), ("server", True)):
        monkeypatch.setenv("MODEL_LOCK", mode)
        running, peak = 0, 0

        async def body(messages, model, tools=None):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1
            yield {"token": "ok"}
            yield {"usage": {}}

        locks = iter([("h:1", asyncio.Lock()), ("h:2", asyncio.Lock())])
        with patch.object(llm, "_stream_body", new=body), \
             patch.object(model_lock, "for_model", new=lambda model: next(locks)), \
             patch("agents.weather_context", new=AsyncMock(return_value=None)), \
             patch("agents.metrics.record"):
            await asyncio.gather(_collect("what is two plus two"),
                                 _collect("what is three plus three"))
        assert (peak > 1) is overlap, mode

# ── Step 3: the per-server lock is a priority queue ──────────────────────────

async def _hold_then_queue(lock, classes):
    """Hold `lock`, queue one waiter per class in `classes` (in that order),
    release, and return the order the waiters got the lock in."""
    order = []

    async def waiter(i, cls):
        await lock.acquire(cls)
        order.append((i, cls))
        await asyncio.sleep(0)
        lock.release()

    await lock.acquire(model_lock.DISPLAY)
    tasks = []
    for i, cls in enumerate(classes):
        tasks.append(asyncio.ensure_future(waiter(i, cls)))
        await asyncio.sleep(0)          # arrival order is the list order
    assert lock.waiting() == list(classes)
    lock.release()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
    assert not lock.locked()
    return order


@pytest.mark.asyncio
async def test_voice_waiters_go_first_fifo_within_a_class():
    lock = model_lock.PriorityLock("t", max_overtakes=lambda: 10)
    order = await _hold_then_queue(lock, ["display", "voice", "display", "voice"])
    assert order == [(1, "voice"), (3, "voice"), (0, "display"), (2, "display")]


@pytest.mark.asyncio
async def test_three_waiters():
    lock = model_lock.PriorityLock("t", max_overtakes=lambda: 3)
    order = await _hold_then_queue(lock, ["display", "display", "voice"])
    assert order == [(2, "voice"), (0, "display"), (1, "display")]


@pytest.mark.asyncio
async def test_a_display_request_cannot_starve():
    """Voice requests keep arriving while the lock is busy; the display
    request that was waiting first is served after at most max_overtakes."""
    lock = model_lock.PriorityLock("t", max_overtakes=lambda: 2)
    order = []

    async def waiter(name, cls):
        await lock.acquire(cls)
        order.append(name)
        # each holder lets one more voice request arrive before releasing
        lock.release()

    await lock.acquire(model_lock.VOICE)
    d = asyncio.ensure_future(waiter("display", "display"))
    await asyncio.sleep(0)
    vs = []
    for i in range(5):
        vs.append(asyncio.ensure_future(waiter(f"v{i}", "voice")))
        await asyncio.sleep(0)
    lock.release()
    await asyncio.wait_for(asyncio.gather(d, *vs), timeout=2)
    assert order.index("display") == 2, order


@pytest.mark.asyncio
async def test_no_preemption_the_holder_keeps_the_lock():
    lock = model_lock.PriorityLock("t")
    await lock.acquire("display")
    v = asyncio.ensure_future(lock.acquire("voice"))
    await asyncio.sleep(0.05)
    assert not v.done() and lock.locked()
    lock.release()
    await asyncio.wait_for(v, timeout=1)
    lock.release()
    assert not lock.locked()


@pytest.mark.asyncio
async def test_cancelled_waiters_leave_no_trace():
    lock = model_lock.PriorityLock("t")
    await lock.acquire("display")
    a = asyncio.ensure_future(lock.acquire("voice"))
    b = asyncio.ensure_future(lock.acquire("display"))
    await asyncio.sleep(0)
    a.cancel()
    await asyncio.sleep(0)
    lock.release()
    await asyncio.wait_for(b, timeout=1)
    assert lock.waiting() == []
    lock.release()
    assert not lock.locked()


@pytest.mark.asyncio
async def test_granted_then_cancelled_passes_the_lock_on():
    lock = model_lock.PriorityLock("t")
    await lock.acquire("display")
    a = asyncio.ensure_future(lock.acquire("voice"))
    b = asyncio.ensure_future(lock.acquire("display"))
    await asyncio.sleep(0)
    lock.release()          # granted to a, which has not run yet
    a.cancel()
    await asyncio.wait_for(b, timeout=1)
    assert lock.locked()
    lock.release()
    assert not lock.locked() and lock.waiting() == []


def test_max_overtakes_default_is_the_shipped_file():
    import config_loader
    assert config_loader.ModelLock().max_overtakes == 3
    assert config_loader.current().tuning.model_lock.max_overtakes == 3


@pytest.mark.asyncio
async def test_queue_order_through_held_and_the_priority_class():
    """Three requests queue on one server through model_lock.held, which
    takes the class from origin.voice: the voice one goes first."""
    import origin
    lock = model_lock.PriorityLock("t", max_overtakes=lambda: 3)
    order = []

    async def request(name, voice):
        token = origin.voice.set(voice)
        try:
            async with model_lock.held(lock, "t"):
                order.append(name)
                await asyncio.sleep(0)
        finally:
            origin.voice.reset(token)

    await lock.acquire(model_lock.DISPLAY)
    tasks = []
    for name, voice in (("d1", False), ("d2", False), ("v1", True)):
        tasks.append(asyncio.ensure_future(request(name, voice)))
        await asyncio.sleep(0)
    lock.release()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
    assert order == ["v1", "d1", "d2"]
