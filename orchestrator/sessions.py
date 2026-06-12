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
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SESSIONS_DB = Path(os.getenv("SESSIONS_DB", "/data/sessions.db"))
HISTORY_MAX_MESSAGES = int(os.getenv("HISTORY_MAX_MESSAGES", "40"))
ASSISTANT_PROMPT_CHARS = int(os.getenv("ASSISTANT_PROMPT_CHARS", "1500"))
SESSION_IDLE_PRUNE_DAYS = int(os.getenv("SESSION_IDLE_PRUNE_DAYS", "30"))

# In-process cache: session_id -> list[{"role","content"}] (full content).
# Single-writer process; safe under the orchestrator's _llm_lock.
_cache: dict[str, list[dict]] = {}


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
    msgs = _load(session_id)
    recent = msgs[-HISTORY_MAX_MESSAGES:]
    # Never start the window mid-exchange (same walk as routing.py).
    while recent and recent[0]["role"] != "user":
        recent = recent[1:]
    out = []
    for m in recent:
        content = m["content"]
        if m["role"] == "assistant" and len(content) > ASSISTANT_PROMPT_CHARS:
            content = content[:ASSISTANT_PROMPT_CHARS] + "…"
        out.append({"role": m["role"], "content": content})
    return out


def append(session_id: str, role: str, content: str) -> None:
    _load(session_id).append({"role": role, "content": content})
    try:
        with sqlite3.connect(SESSIONS_DB) as conn:
            conn.execute(
                "INSERT INTO session_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, time.time()),
            )
    except Exception as e:
        logger.warning("sessions: append(%s) failed (in-memory only): %s", session_id, e)


def clear(session_id: str) -> None:
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
