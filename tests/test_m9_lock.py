"""The model lock, narrowed (sprint M9).

Step 1: routing, the weather and local context and direct dispatch run
before the lock; only the model phases wait. The wait is recorded as a
`lock.wait` span and a `model_lock` event.

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
async def test_routing_and_context_run_while_another_request_holds_the_lock():
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
async def test_the_wait_is_a_span_and_an_event(caplog):
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
