"""SQLite-backed LLM performance metrics."""
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

METRICS_DB = Path(os.getenv("METRICS_DB", "/data/metrics.db"))

# Canonical agent-column values:
#   "router"       — phase-1 classifier
#   "coordinator"  — direct-answer / fallback synthesis
#   "<agent.name>" — specialist agent rounds (health, research, ...)


def init_db() -> None:
    try:
        _init_db()
    except Exception as e:
        # Telemetry must never block startup (matches sessions.init_db).
        logger.warning("metrics: init failed (continuing without metrics): %s", e)


def _init_db() -> None:
    with sqlite3.connect(METRICS_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_metrics (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp         TEXT    NOT NULL DEFAULT (datetime('now')),
                agent             TEXT    NOT NULL,
                model             TEXT    NOT NULL,
                prompt_tokens     INTEGER,
                completion_tokens INTEGER,
                ttft_ms           REAL,
                gen_ms            INTEGER,
                tokens_per_sec    REAL
            )
        """)


def record(
    agent: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    eval_duration_ns: int = 0,
    ttft_ms: float | None = None,
) -> None:
    gen_ms = round(eval_duration_ns / 1e6) if eval_duration_ns else None
    tokens_per_sec = (
        round(completion_tokens / (eval_duration_ns / 1e9), 1)
        if eval_duration_ns and completion_tokens
        else None
    )
    try:
        with sqlite3.connect(METRICS_DB) as conn:
            conn.execute(
                """INSERT INTO llm_metrics
                   (agent, model, prompt_tokens, completion_tokens, ttft_ms, gen_ms, tokens_per_sec)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (agent, model, prompt_tokens, completion_tokens, ttft_ms, gen_ms, tokens_per_sec),
            )
    except Exception as e:
        logger.warning("Failed to record metric: %s", e)


def dashboard_payload() -> dict:
    with sqlite3.connect(METRICS_DB) as conn:
        conn.row_factory = sqlite3.Row

        model_rows = conn.execute("""
            SELECT model,
                   COUNT(*) as requests,
                   ROUND(AVG(ttft_ms), 1) as avg_ttft_ms,
                   ROUND(AVG(gen_ms), 0) as avg_gen_ms,
                   ROUND(AVG(tokens_per_sec), 1) as avg_tok_s,
                   SUM(completion_tokens) as total_tokens
            FROM llm_metrics
            WHERE gen_ms IS NOT NULL
            GROUP BY model
            ORDER BY requests DESC
        """).fetchall()

        agent_rows = conn.execute("""
            SELECT agent,
                   COUNT(*) as requests,
                   ROUND(AVG(ttft_ms), 1) as avg_ttft_ms,
                   ROUND(AVG(gen_ms), 0) as avg_gen_ms,
                   ROUND(AVG(tokens_per_sec), 1) as avg_tok_s
            FROM llm_metrics
            WHERE gen_ms IS NOT NULL
            GROUP BY agent
            ORDER BY requests DESC
        """).fetchall()

        daily_rows = conn.execute("""
            SELECT DATE(timestamp) as day,
                   ROUND(AVG(ttft_ms), 1) as avg_ttft_ms,
                   ROUND(AVG(gen_ms), 0) as avg_gen_ms,
                   ROUND(AVG(tokens_per_sec), 1) as avg_tok_s,
                   COUNT(*) as requests
            FROM llm_metrics
            WHERE timestamp >= DATE('now', '-30 days')
              AND gen_ms IS NOT NULL
            GROUP BY day
            ORDER BY day
        """).fetchall()

        recent_rows = conn.execute("""
            SELECT timestamp, agent, model,
                   prompt_tokens, completion_tokens,
                   ttft_ms, gen_ms, tokens_per_sec
            FROM llm_metrics
            ORDER BY id DESC
            LIMIT 50
        """).fetchall()

    return {
        "by_model": [dict(r) for r in model_rows],
        "by_agent": [dict(r) for r in agent_rows],
        "daily":    [dict(r) for r in daily_rows],
        "recent":   [dict(r) for r in recent_rows],
    }


# ── /api/activity (M25) ──────────────────────────────────────────────────────

ACTIVITY_WINDOW_H = 24
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"  # how SQLite's datetime('now') writes timestamp: UTC, no zone mark


def _iso_z(ts: str | None) -> str | None:
    """'2026-11-24 19:03:20' (UTC, as stored) -> '2026-11-24T19:03:20Z'."""
    if not ts:
        return None
    from datetime import datetime, timezone
    return datetime.strptime(ts[:19], _TS_FORMAT).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def activity(catalog_names: list[str], now=None, hours: int = ACTIVITY_WINDOW_H) -> dict:
    """What this orchestrator asked of each model: per model, the calls,
    prompt tokens and completion tokens recorded in llm_metrics with
    timestamp >= now - hours (the boundary row counts), and the last call
    over the whole table. Every catalog model is listed; a model outside
    the catalog is listed apart when it has calls inside the window and
    left out otherwise. now is a timezone-aware datetime (default: now)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    since = now - timedelta(hours=hours)
    with sqlite3.connect(f"{Path(METRICS_DB).resolve().as_uri()}?mode=ro", uri=True) as conn:
        rows = conn.execute("""
            SELECT model,
                   SUM(CASE WHEN timestamp >= :since THEN 1 ELSE 0 END),
                   SUM(CASE WHEN timestamp >= :since THEN COALESCE(prompt_tokens, 0) ELSE 0 END),
                   SUM(CASE WHEN timestamp >= :since THEN COALESCE(completion_tokens, 0) ELSE 0 END),
                   MAX(timestamp)
            FROM llm_metrics
            GROUP BY model
        """, {"since": since.strftime(_TS_FORMAT)}).fetchall()
    by_model = {m: {"model": m, "requests": r, "prompt_tokens": p, "completion_tokens": c,
                    "last_used": _iso_z(last)} for m, r, p, c, last in rows}
    models = [by_model.get(name) or {"model": name, "requests": 0, "prompt_tokens": 0,
                                     "completion_tokens": 0, "last_used": None}
              for name in catalog_names]
    outside = sorted((v for m, v in by_model.items() if m not in set(catalog_names) and v["requests"]),
                     key=lambda v: -v["requests"])
    return {"window_hours": hours,
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "llm_metrics in this orchestrator's metrics.db: calls from this Kronk only",
            "models": models, "not_in_catalog": outside}
