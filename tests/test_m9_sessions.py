"""Session writes with requests interleaving (sprint M9 step 5, criterion 8).

Without the pipeline-wide lock, two requests on one session can finish at
the same time. Each turn's user and assistant messages are written by
sessions.append_exchange as one transaction under one lock, so every
message is stored once, each exchange stays together, and nothing is lost,
in the cache and in the SQLite file. Real SQLite, temporary directory.
"""
import asyncio
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import pytest

import sessions as sessions_mod

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def store(tmp_path):
    with patch.object(sessions_mod, "SESSIONS_DB", tmp_path / "sessions.db"), \
         patch.dict(sessions_mod._cache, {}, clear=True):
        sessions_mod.init_db()
        yield sessions_mod


def _all(store, sid):
    """Every stored message, read from the DB (cache dropped)."""
    store._cache.pop(sid, None)
    return store._load(sid)


def _check_exchanges(msgs, expected_pairs):
    """msgs is a valid interleaving of the expected (user, assistant) pairs:
    each pair adjacent, every pair present once, nothing else."""
    assert len(msgs) == 2 * len(expected_pairs)
    seen = []
    for i in range(0, len(msgs), 2):
        u, a = msgs[i], msgs[i + 1]
        assert u["role"] == "user" and a["role"] == "assistant"
        seen.append((u["content"], a["content"]))
    assert sorted(seen) == sorted(expected_pairs)


def test_threads_appending_to_one_and_many_sessions(store):
    pairs = {f"s{k}": [] for k in range(4)}
    jobs = []
    for n in range(400):
        sid = f"s{n % 4}" if n % 5 else "s0"         # s0 gets the most traffic
        pair = (f"q{n}", f"a{n}")
        pairs[sid].append(pair)
        jobs.append((sid, pair))
    random.Random(9).shuffle(jobs)
    barrier = threading.Barrier(8)

    def worker(chunk):
        barrier.wait()
        for sid, (q, a) in chunk:
            store.append_exchange(sid, [("user", q), ("assistant", a)])

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(worker, [jobs[i::8] for i in range(8)]))

    for sid, expected in pairs.items():
        cached = list(store._cache[sid])
        _check_exchanges(cached, expected)
        _check_exchanges(_all(store, sid), expected)


def test_window_reads_while_threads_write(store):
    stop = threading.Event()
    errors = []

    def reader():
        while not stop.is_set():
            try:
                w = store.window("s")
                assert not w or w[0]["role"] == "user"
            except Exception as e:     # pragma: no cover - the failure being tested for
                errors.append(e)

    t = threading.Thread(target=reader)
    t.start()
    try:
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda n: store.append_exchange("s", [("user", f"q{n}"), ("assistant", f"a{n}")]),
                        range(200)))
    finally:
        stop.set()
        t.join()
    assert errors == []
    _check_exchanges(_all(store, "s"), [(f"q{n}", f"a{n}") for n in range(200)])


def test_append_still_appends_one_message(store):
    store.append("x", "user", "hi")
    store.append("x", "assistant", "hello")
    assert _all(store, "x") == [{"role": "user", "content": "hi"},
                                {"role": "assistant", "content": "hello"}]


def test_a_failed_write_keeps_the_exchange_in_memory(store):
    with patch.object(store, "SESSIONS_DB", "/nonexistent/nope.db"):
        store.append_exchange("m", [("user", "q"), ("assistant", "a")])
    assert store.window("m") == [{"role": "user", "content": "q"},
                                 {"role": "assistant", "content": "a"}]


@pytest.mark.asyncio
async def test_pipelines_interleaving_on_shared_and_separate_sessions(store, monkeypatch):
    """30 /message-style pipelines at once on 3 sessions, generations
    interleaved (the model yields to the loop between tokens): every
    exchange is stored once, whole, with its own reply."""
    monkeypatch.setenv("MODEL_LOCK", "server")
    import orchestrator.main as orch
    import llm
    import model_lock

    async def body(messages, model, tools=None):
        q = messages[-1]["content"]
        for part in ("answer ", "to ", q):
            await asyncio.sleep(random.random() / 200)
            yield {"token": part}
        yield {"usage": {}}

    async def one(n, sid):
        q = f"what is {n} plus {n}"
        out = [ev async for ev in orch._run_pipeline(
            q, store.window(sid), said=q, voice=False, transport="webui",
            pipeline_name="pipeline.test", session_id=sid)]
        return sid, q, "".join(e["text"] for e in out if e["type"] == "token")

    with patch.object(orch, "sessions", store), patch.object(llm, "_stream_body", new=body), \
         patch.object(model_lock, "_locks", {}), patch.object(model_lock, "_servers", {}), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("agents.metrics.record"):
        results = await asyncio.gather(*(one(n, f"web{n % 3}") for n in range(30)))

    for sid in ("web0", "web1", "web2"):
        expected = [(q, reply) for s, q, reply in results if s == sid]
        assert all(r == f"answer to {q}" for s, q, r in results if s == sid)
        _check_exchanges(list(store._cache[sid]), expected)
        _check_exchanges(_all(store, sid), expected)
