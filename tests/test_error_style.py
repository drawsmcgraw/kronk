"""Tests for the ERROR_STYLE toggle (orchestrator/errors.py).

The toggle governs rendering only — capture (logs, Langfuse) always keeps
full detail. Debug renderings are contracts pinned elsewhere in the suite;
this file pins the style *resolution* and the friendly renderings, and that
friendliness never reintroduces the invented-answer problem.

NOTE for the voice smoke test (ROADMAP item 8): it always runs in debug —
its deliberate-failure assertions expect specific detail. Force
ERROR_STYLE=debug (or don't set the voice override) when running it.
"""
from unittest.mock import AsyncMock, patch

import pytest

import errors


# ── style_for: env resolution ─────────────────────────────────────────────────

def test_default_is_debug(monkeypatch):
    monkeypatch.delenv("ERROR_STYLE", raising=False)
    monkeypatch.delenv("ERROR_STYLE_VOICE", raising=False)
    assert errors.style_for("webui") == errors.DEBUG
    assert errors.style_for("shim") == errors.DEBUG


def test_global_style_applies_to_all_transports(monkeypatch):
    monkeypatch.setenv("ERROR_STYLE", "friendly")
    monkeypatch.delenv("ERROR_STYLE_VOICE", raising=False)
    assert errors.style_for("webui") == errors.FRIENDLY
    assert errors.style_for("shim") == errors.FRIENDLY


def test_voice_override_applies_only_to_shim(monkeypatch):
    """The intended end state: operator keeps debug in the chat UI while the
    kitchen hears friendly sentences."""
    monkeypatch.delenv("ERROR_STYLE", raising=False)
    monkeypatch.setenv("ERROR_STYLE_VOICE", "friendly")
    assert errors.style_for("webui") == errors.DEBUG
    assert errors.style_for("shim") == errors.FRIENDLY


def test_invalid_value_falls_back_to_debug(monkeypatch):
    """A typo must not silently hide detail forever."""
    monkeypatch.setenv("ERROR_STYLE", "freindly")
    assert errors.style_for("webui") == errors.DEBUG


# ── render ────────────────────────────────────────────────────────────────────

def test_debug_renderings_are_the_pinned_contracts():
    assert errors.render("routing", "boom", "abc123", errors.DEBUG) == \
        "Error: routing failed: boom [rid abc123]"
    assert errors.render("pipeline", "RuntimeError: x", "abc123", errors.DEBUG) == \
        "Error: the pipeline failed unexpectedly (RuntimeError: x) [rid abc123]"
    # llm kind passes run_stream's already-specific message through.
    assert errors.render("llm", "Error: could not reach model", "abc123", errors.DEBUG) == \
        "Error: could not reach model"


@pytest.mark.parametrize("kind", ["routing", "pipeline", "llm"])
def test_friendly_renderings_hide_technical_detail(kind):
    out = errors.render(kind, "RuntimeError: LiteLLM 400 kaboom", "abc123", errors.FRIENDLY)
    assert "abc123" not in out       # no rid spoken
    assert "RuntimeError" not in out  # no exception types
    assert "400" not in out           # no status codes
    assert out.startswith("Sorry")


# ── specialist_failed_block ───────────────────────────────────────────────────

@pytest.mark.parametrize("style", [errors.DEBUG, errors.FRIENDLY])
def test_failed_block_keeps_detail_and_ban_in_both_styles(style):
    """Model-facing detail is style-independent — only phrasing guidance
    changes — and the invented-answer ban never relaxes."""
    block = errors.specialist_failed_block(
        "home", "[play_music failed (HTTP 503): speaker powered off]", style
    )
    assert "FAILED" in block
    assert "speaker powered off" in block
    assert "do NOT invent an answer" in block


def test_failed_block_friendly_forbids_technical_identifiers():
    block = errors.specialist_failed_block("home", "boom", errors.FRIENDLY)
    assert "never mention HTTP status codes" in block


# ── wiring: run_stream + pipeline ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_friendly_style_adds_phrasing_rule_to_system_prompt():
    import agents

    captured = {}

    async def fake_stream(messages, model, tools=None):
        captured["system"] = messages[0]["content"]
        yield {"token": "ok"}
        yield {"usage": {}}

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)):
        agent = agents.AGENTS["health"]
        [ev async for ev in agents.run_stream(
            agent, "hi", [], error_style=errors.FRIENDLY)]
        friendly_system = captured["system"]
        [ev async for ev in agents.run_stream(agent, "hi", [])]
        default_system = captured["system"]

    assert errors.FRIENDLY_TOOL_PHRASING in friendly_system
    assert errors.FRIENDLY_TOOL_PHRASING not in default_system


def test_terminal_speech_friendly_drops_the_cause():
    import agents
    raw = "[Tool play_music error: ReadTimeout(ReadTimeout('timed out'))]"
    friendly = agents._terminal_speech(raw, errors.FRIENDLY)
    assert "ReadTimeout" not in friendly
    assert friendly == "That didn't work — I couldn't finish that request."
    # Known-good shapes are style-independent (already human sentences).
    assert agents._terminal_speech(
        "[Music playing: X on the kitchen speaker]", errors.FRIENDLY
    ) == "Now playing X on the kitchen speaker."


def test_pipeline_crash_renders_friendly_when_enabled(monkeypatch):
    """End-to-end: same crash as the debug-mode test elsewhere in the suite,
    but with ERROR_STYLE=friendly the user hears one clean sentence — while
    the trace still gets the full cause (status_message)."""
    import json as _json
    from unittest.mock import MagicMock
    import unittest.mock as mock_module
    from fastapi.testclient import TestClient
    import orchestrator.main as orch
    import orchestrator.metrics as metrics
    import orchestrator.sessions as sessions

    monkeypatch.setenv("ERROR_STYLE", "friendly")

    async def fake_classify(text, history):
        return "direct"

    async def exploding_run_stream(*args, **kwargs):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    fake_end = MagicMock()
    with mock_module.patch.object(metrics, "METRICS_DB", None), \
         mock_module.patch.object(sessions, "SESSIONS_DB", None), \
         patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("orchestrator.main.agents.run_stream", new=exploding_run_stream), \
         patch("orchestrator.main.telemetry.end_pipeline", new=fake_end):
        client = TestClient(orch.app)
        resp = client.post("/message", json={"text": "hello"})

    tokens = ""
    for line in resp.text.splitlines():
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            try:
                ev = _json.loads(line[5:].strip())
            except ValueError:
                continue
            tokens += ev.get("token", "")

    assert "kaboom" not in tokens
    assert "RuntimeError" not in tokens
    assert "rid" not in tokens
    assert tokens.startswith("Sorry")
    # Capture side unchanged: the trace still records the real cause.
    _, kwargs = fake_end.call_args
    assert kwargs.get("level") == "ERROR"
    assert "kaboom" in (kwargs.get("status_message") or "")
