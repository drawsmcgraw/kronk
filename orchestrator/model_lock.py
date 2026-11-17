"""The model locks (sprint M9).

One lock per model server (step 2): `llm.stream` holds the lock of the
server its model runs on for one generation, so a request waits only for a
generation on the same server, never for another request's tool calls or
for a generation on another server. Servers are told apart by the host and
port of each model's api_base in litellm/config.yaml (servers.load_catalog);
a model not found there gets a lock of its own name.

Each server lock is a priority queue (step 3, PriorityLock): a waiting
voice request is served before a waiting display request, first come first
served within a class; a running generation is never interrupted; a
display request is overtaken by at most tuning.yaml
model_lock.max_overtakes voice requests.

MODEL_LOCK=pipeline (env, read per request) is the rollback: no per-server
lock, and main._run_pipeline holds its pipeline-wide `_llm_lock` around a
request's model phases as before step 2.

`held(lock, name, priority)` takes `lock` for the enclosed block and records
how long the request waited for it: a `lock.wait` span under the current
span (attributes `kronk.lock`, `kronk.priority`, `kronk.wait_ms`) and a
`model_lock` event with the same fields. Generation time is the time spent
inside the block, so a trace shows the two apart.

Priority class: "voice" for a request on the speech mount (origin.voice),
"display" for every other.

docs/features/model-locks.md.
"""
import asyncio
import itertools
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

import config_loader
import origin
import servers
import telemetry
from events import emit

logger = logging.getLogger(__name__)

VOICE = "voice"
DISPLAY = "display"


def _max_overtakes() -> int:
    return config_loader.current().tuning.model_lock.max_overtakes


@dataclass(eq=False)
class _Waiter:
    cls: str
    seq: int
    fut: asyncio.Future
    overtaken: int = 0


class PriorityLock:
    """One holder at a time (sprint M9 step 3). Waiters are served voice
    before display, first come first served within a class. No preemption:
    the holder keeps the lock until it releases it. A display waiter is
    overtaken by at most `max_overtakes()` voice requests that arrived after
    it; then it is served next (no starvation).

    Loop-agnostic: a waiter's future is made on the running loop when it has
    to wait, so the lock is not bound to the loop of its first use."""

    def __init__(self, name: str, max_overtakes=_max_overtakes):
        self.name = name
        self._max_overtakes = max_overtakes
        self._held = False
        self._waiters: list[_Waiter] = []
        self._seq = itertools.count()

    def locked(self) -> bool:
        return self._held

    def waiting(self) -> list[str]:
        """Classes of the requests waiting, in arrival order."""
        return [w.cls for w in sorted(self._waiters, key=lambda w: w.seq)]

    async def acquire(self, cls: str = DISPLAY) -> bool:
        if not self._held:
            self._held = True
            return True
        w = _Waiter(cls, next(self._seq), asyncio.get_running_loop().create_future())
        self._waiters.append(w)
        try:
            await w.fut
        except BaseException:
            if w.fut.done() and not w.fut.cancelled():
                self.release()          # granted, then cancelled: pass it on
            elif w in self._waiters:
                self._waiters.remove(w)
            raise
        return True

    def _pick(self) -> _Waiter:
        limit = self._max_overtakes()
        starving = [w for w in self._waiters if w.cls == DISPLAY and w.overtaken >= limit]
        if starving:
            return min(starving, key=lambda w: w.seq)
        return min(self._waiters, key=lambda w: (w.cls != VOICE, w.seq))

    def release(self) -> None:
        if not self._held:
            raise RuntimeError(f"model lock {self.name} released while not held")
        while self._waiters:
            w = self._pick()
            self._waiters.remove(w)
            if w.fut.done():
                continue
            for o in self._waiters:
                if o.cls == DISPLAY and o.seq < w.seq:
                    o.overtaken += 1
            w.fut.set_result(True)      # handed over: stays held
            return
        self._held = False


_MODES = {"server", "pipeline"}
_mode_warned: set[str] = set()


def mode() -> str:
    """MODEL_LOCK (env, read per request): "server", the default, holds one
    lock per model server for one generation; "pipeline" restores the lock
    around a whole request's model phases, for rollback. Anything else is
    "pipeline" (the older behaviour) and is logged once."""
    raw = os.getenv("MODEL_LOCK", "server")
    v = raw.strip().lower()
    if v in _MODES:
        return v
    if raw not in _mode_warned:
        _mode_warned.add(raw)
        logger.error("MODEL_LOCK=%r is not server or pipeline; using pipeline", raw)
    return "pipeline"


_servers: dict[str, str] | None = None
_locks: dict[str, "PriorityLock"] = {}


def server_for(model: str) -> str:
    """The model server a LiteLLM model name runs on: host:port of its
    api_base in litellm/config.yaml, or the model name when not found."""
    global _servers
    if _servers is None:
        table: dict[str, str] = {}
        try:
            for entry in servers.load_catalog():
                base = urlsplit(entry.get("api_base") or "")
                if base.hostname and base.port:
                    table[entry["name"]] = f"{base.hostname}:{base.port}"
        except Exception as e:
            logger.warning("model locks: server catalog unreadable (%s); "
                           "one lock per model name", e)
        _servers = table
    return _servers.get(model, model)


def for_model(model: str) -> tuple[str, "PriorityLock"]:
    """(server name, its lock) for a model; one lock per server."""
    name = server_for(model)
    lock = _locks.get(name)
    if lock is None:
        lock = _locks[name] = PriorityLock(name)
    return name, lock


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
        if isinstance(lock, PriorityLock):
            await lock.acquire(cls)
        else:
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
