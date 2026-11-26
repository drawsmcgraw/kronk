"""SQLite-backed per-client conversation sessions.

Replaces the old single global in-memory `history` list (which mixed all
clients together and forgot everything on restart). Built for the 2026-06
response-time program — see docs/REPORT_2026-06_response_time_program.md.

Design notes:
- SQLite on /data (same dir/pattern as metrics.py), NOT Redis: one process,
  one host — an in-process dict cache + write-through beats a network hop,
  and the DB file survives restarts for free.
- The performance cost of history is prompt tokens, not I/O. Three guards:
  (1) the window returned for prompting is capped at HISTORY_MAX_MESSAGES,
      trimmed at user-turn boundaries so role alternation survives;
  (2) assistant turns are truncated to ASSISTANT_PROMPT_CHARS when *read*
      for prompting (stored in full — truncation is a prompt-budget choice,
      not data loss);
  (3) callers keep prompt order stable (system first, history appended), so
      llama.cpp's --swa-full prompt cache makes consecutive turns pay only
      for new tokens.
- Voice path note: HA's Ollama integration sends the full message history
  with every request (verified against HA source 2026-06-12), so voice
  conversations are HA-owned and never touch this store. This store serves
  the web UI and any future client that doesn't carry its own history.
- All functions are exception-safe no-ops on storage failure (mirrors
  metrics.py) — a broken disk must not break the pipeline.
"""
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SESSIONS_DB = Path(os.getenv("SESSIONS_DB", "/data/sessions.db"))
HISTORY_MAX_MESSAGES = int(os.getenv("HISTORY_MAX_MESSAGES", "40"))
ASSISTANT_PROMPT_CHARS = int(os.getenv("ASSISTANT_PROMPT_CHARS", "1500"))
SESSION_IDLE_PRUNE_DAYS = int(os.getenv("SESSION_IDLE_PRUNE_DAYS", "30"))

# In-process cache: session_id -> list[{"role","content"}] (full content).
# Requests run concurrently since sprint M9 (no pipeline-wide lock), so the
# cache and the database are written under _write_lock, and a turn's user
# and assistant messages are written together (append_exchange): one
# transaction, one cache update, never split by another request's turn.
# A threading lock, not an asyncio one: the critical sections never await,
# and it also holds if a caller ever runs these on a worker thread.
_cache: dict[str, list[dict]] = {}
_write_lock = threading.RLock()


def init_db() -> None:
    try:
        with sqlite3.connect(SESSIONS_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_messages
                ON session_messages (session_id, id)
            """)
    except Exception as e:
        logger.warning("sessions: init failed (continuing without persistence): %s", e)


def _load(session_id: str) -> list[dict]:
    if session_id in _cache:
        return _cache[session_id]
    rows: list[dict] = []
    try:
        with sqlite3.connect(SESSIONS_DB) as conn:
            cur = conn.execute(
                "SELECT role, content FROM session_messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
            rows = [{"role": r, "content": c} for r, c in cur.fetchall()]
    except Exception as e:
        logger.warning("sessions: load(%s) failed: %s", session_id, e)
    _cache[session_id] = rows
    return rows


def window(session_id: str) -> list[dict]:
    """History window for prompting: capped, boundary-aligned, truncated."""
    with _write_lock:
        msgs = list(_load(session_id))
    recent = msgs[-HISTORY_MAX_MESSAGES:]
    # Never start the window mid-exchange (same walk as routing.py).
    while recent and recent[0]["role"] != "user":
        recent = recent[1:]
    return [{"role": m["role"], "content": _prompt_content(m)} for m in recent]


def _prompt_content(m: dict) -> str:
    """A message's content as it is read for prompting: an assistant turn
    is cut at ASSISTANT_PROMPT_CHARS (the stored text stays whole)."""
    content = m["content"]
    if m["role"] == "assistant" and len(content) > ASSISTANT_PROMPT_CHARS:
        content = content[:ASSISTANT_PROMPT_CHARS] + "…"
    return content


# How many whole exchanges a pinned specialist is given (work item M36).
SPECIALIST_EXCHANGES = 2


def specialist_turns(history: list[dict]) -> list[dict]:
    """The earlier turns a pinned specialist is given, as chat messages.

    Work item M36 (docs/sprint/M36-criteria.md, Rulings). Used by
    main._run_pipeline for every entry point: the chat page passes
    window() above, the shims pass the client's array (_shim_context).

    - Whole exchanges from the end, a user message then the assistant's
      reply, SPECIALIST_EXCHANGES at most, so the window always starts on a
      user turn. A window that started on an assistant turn gave the wrong
      album 2 of 18 times in the design review.
    - User messages after the last reply are dropped first. On a shim
      request that does not end in a user message, the request itself is
      the last user message and stays in the history (_turn_said); it is
      not an exchange.
    - The walk stops at the first break in alternation (two user messages
      in a row, an empty message, any other role), so nothing from before
      a gap is joined to what follows it.
    - Each assistant turn is cut at ASSISTANT_PROMPT_CHARS. Failure replies
      are kept: the incident's follow-up resolves from one ("The closest I
      found was Pulse by Pink Floyd").
    """
    msgs = list(history or [])
    while msgs and msgs[-1].get("role") == "user":
        msgs.pop()
    out: list[dict] = []
    i = len(msgs) - 1
    while i >= 1 and len(out) < 2 * SPECIALIST_EXCHANGES:
        reply, ask = msgs[i], msgs[i - 1]
        if not (ask.get("role") == "user" and reply.get("role") == "assistant"
                and ask.get("content") and reply.get("content")):
            break
        out[:0] = [{"role": "user", "content": ask["content"]},
                   {"role": "assistant", "content": _prompt_content(reply)}]
        i -= 2
    return out


def append(session_id: str, role: str, content: str) -> None:
    append_exchange(session_id, [(role, content)])


def append_exchange(session_id: str, messages: list[tuple[str, str]]) -> None:
    """Store `messages` ((role, content) pairs, in order) as one write: one
    transaction, then one cache update, under the write lock. A turn's user
    and assistant messages go in together, so a concurrent request on the
    same session cannot land between them (sprint M9 step 5)."""
    with _write_lock:
        # Populate the cache BEFORE the DB write: _load on a cache miss reads
        # the DB, so loading after the insert would double-count these very
        # messages (caught by test_sessions on first-append-after-restart).
        msgs = _load(session_id)
        # Persist, then mutate the cache — if the write fails the caller
        # still gets a usable in-memory conversation, but we say so honestly
        # instead of silently diverging memory from disk.
        now = time.time()
        try:
            with sqlite3.connect(SESSIONS_DB) as conn:
                conn.executemany(
                    "INSERT INTO session_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    [(session_id, role, content, now) for role, content in messages],
                )
        except Exception as e:
            logger.warning(
                "sessions: append(%s) NOT persisted — memory-only, will not survive restart: %s",
                session_id, e,
            )
        msgs.extend({"role": role, "content": content} for role, content in messages)


def clear(session_id: str) -> None:
    with _write_lock:
        _cache[session_id] = []
        try:
            with sqlite3.connect(SESSIONS_DB) as conn:
                conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
        except Exception as e:
            logger.warning("sessions: clear(%s) failed: %s", session_id, e)


def prune_idle() -> int:
    """Delete sessions whose newest message is older than the prune horizon."""
    cutoff = time.time() - SESSION_IDLE_PRUNE_DAYS * 86400
    try:
        with sqlite3.connect(SESSIONS_DB) as conn:
            cur = conn.execute("""
                DELETE FROM session_messages WHERE session_id IN (
                    SELECT session_id FROM session_messages
                    GROUP BY session_id HAVING MAX(created_at) < ?
                )""", (cutoff,))
            return cur.rowcount
    except Exception as e:
        logger.warning("sessions: prune failed: %s", e)
        return 0
