"""Tests for orchestrator/sessions.py — the per-client conversation store."""
import importlib
from unittest.mock import patch

import pytest

import sessions as sessions_mod


@pytest.fixture
def store(tmp_path):
    """Fresh sessions module state wired to a temp DB."""
    with patch.object(sessions_mod, "SESSIONS_DB", tmp_path / "sessions.db"), \
         patch.dict(sessions_mod._cache, {}, clear=True):
        sessions_mod.init_db()
        yield sessions_mod


def test_append_and_window_roundtrip(store):
    store.append("s1", "user", "hello")
    store.append("s1", "assistant", "hi there")
    win = store.window("s1")
    assert win == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_sessions_are_isolated(store):
    store.append("a", "user", "alpha")
    store.append("b", "user", "bravo")
    assert store.window("a") == [{"role": "user", "content": "alpha"}]
    assert store.window("b") == [{"role": "user", "content": "bravo"}]


def test_window_caps_and_trims_at_user_boundary(store):
    # 3 exchanges = 6 messages; cap at 3 must NOT start mid-exchange.
    for i in range(3):
        store.append("s", "user", f"q{i}")
        store.append("s", "assistant", f"a{i}")
    with patch.object(store, "HISTORY_MAX_MESSAGES", 3):
        win = store.window("s")
    # Raw tail of 3 would be [a1, q2, a2] — boundary walk drops the leading
    # assistant turn so alternation survives.
    assert win[0]["role"] == "user"
    assert win == [
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_assistant_turns_truncated_at_read_time(store):
    long_answer = "x" * 5000
    store.append("s", "user", "q")
    store.append("s", "assistant", long_answer)
    win = store.window("s")
    assert len(win[1]["content"]) == store.ASSISTANT_PROMPT_CHARS + 1  # +ellipsis
    assert win[1]["content"].endswith("…")
    # Stored content stays full — truncation is a prompt-budget choice.
    assert store._load("s")[1]["content"] == long_answer


def test_clear_wipes_cache_and_db(store):
    store.append("s", "user", "secret")
    store.clear("s")
    assert store.window("s") == []
    # Survives a cache wipe (i.e., it's really gone from the DB too).
    store._cache.pop("s", None)
    assert store.window("s") == []


def test_durability_across_cache_loss(store):
    """Simulates an orchestrator restart: cache empty, DB intact."""
    store.append("s", "user", "remember me")
    store._cache.clear()
    assert store.window("s") == [{"role": "user", "content": "remember me"}]


def test_append_survives_db_failure_memory_only(store):
    """A broken DB must not break the conversation (memory-only fallback)."""
    with patch.object(store, "SESSIONS_DB", "/nonexistent/nope.db"):
        store.append("s", "user", "still works")
    assert store.window("s") == [{"role": "user", "content": "still works"}]
