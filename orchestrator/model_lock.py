"""Waiting for the model, made visible (sprint M9).

`held(lock, name, priority)` takes `lock` for the enclosed block and records
how long the request waited for it: a `lock.wait` span under the current
span (attributes `kronk.lock`, `kronk.priority`, `kronk.wait_ms`) and a
`model_lock` event with the same fields. Generation time is the time spent
inside the block, so a trace shows the two apart.

Priority class: "voice" for a request on the speech mount (origin.voice),
"display" for every other.

docs/features/model-locks.md.
"""
import time
from contextlib import asynccontextmanager

import origin
import telemetry
from events import emit

VOICE = "voice"
DISPLAY = "display"


def priority() -> str:
    """The running request's class: voice (speech mount) or display."""
    return VOICE if origin.voice.get() else DISPLAY


@asynccontextmanager
async def held(lock, name: str, cls: str | None = None):
    """Hold `lock` for the block; record the wait as a span and an event."""
    cls = cls or priority()
    span = telemetry.current().child_span("lock.wait", metadata={"lock": name, "priority": cls})
    t0 = time.monotonic()
    if not hasattr(lock, "acquire"):
        # Any async context manager (tests lift the lock with nullcontext()).
        async with lock:
            span.end(metadata={"wait_ms": 0})
            yield 0
        return
    try:
        await lock.acquire()
    except BaseException:
        wait_ms = round((time.monotonic() - t0) * 1000)
        span.end(status_message="not acquired (cancelled)", metadata={"wait_ms": wait_ms})
        emit("model_lock", lock=name, priority=cls, wait_ms=wait_ms, acquired=False)
        raise
    wait_ms = round((time.monotonic() - t0) * 1000)
    span.end(metadata={"wait_ms": wait_ms})
    emit("model_lock", lock=name, priority=cls, wait_ms=wait_ms)
    try:
        yield wait_ms
    finally:
        lock.release()
