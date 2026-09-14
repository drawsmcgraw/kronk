"""Tool-round text is retracted before it is spoken or stored
(docs/plans/TOOL_ROUND_RETRACT_PLAN.md).

2026-09-14: "pause" was answered with the home agent's leaked deliberation
("5. Construct the tool call…") followed by the real sentence. Content a
model produces in a round that ends with a tool call is never the answer,
so the loop retracts it; every consumer that can honour that does.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))
os.environ.setdefault("LLM_SERVICE_URL",     "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL",    "http://fake-tools:8003")
os.environ.setdefault("HEALTH_SERVICE_URL",  "http://fake-health:8004")
os.environ.setdefault("FINANCE_SERVICE_URL", "http://fake-finance:8005")

import unittest.mock as mock_module  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import agents  # noqa: E402
import orchestrator.main as orch  # noqa: E402

LEAK = "player: The user did not specify a player. 5. Construct the tool call."

_open_orig = open


def _fake_open(path, *a, **kw):
    if "/app/static/" in str(path):
        import io
        return io.StringIO("<html></html>")
    return _open_orig(path, *a, **kw)


@pytest.fixture
def client(tmp_path):
    """Same hermetic /message client as test_agentic_loop."""
    # Patch the modules the app actually holds (orch.sessions / orch.metrics),
    # whatever name they were imported under.
    with mock_module.patch("builtins.open", side_effect=_fake_open), \
         mock_module.patch.object(orch.metrics, "METRICS_DB", tmp_path / "metrics.db"), \
         mock_module.patch.object(orch.sessions, "SESSIONS_DB", tmp_path / "sessions.db"):
        orch.sessions.clear(orch.WEBUI_SESSION)
        with TestClient(orch.app, raise_server_exceptions=True) as c:
            yield c
        orch.sessions.clear(orch.WEBUI_SESSION)


def _leaky_then_answer(answer="Sunny, 72."):
    """Round 1: leaks text, then calls a tool. Round 2: the answer."""
    n = {"round": 0}

    async def fake_stream(messages, model, tools=None):
        n["round"] += 1
        if n["round"] == 1:
            for piece in (LEAK[:20], LEAK[20:]):
                yield {"token": piece}
            yield {"tool_calls": [{"id": "c1", "function": {"name": "get_weather", "arguments": {"location": "x"}}}]}
        else:
            yield {"token": answer}
        yield {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return fake_stream


async def _fake_execute(name, args):
    return "72F sunny"


def _apply(events):
    """What a retract-honouring consumer ends up with."""
    text = ""
    for e in events:
        if e["type"] == "token":
            text += e["text"]
        elif e["type"] == "retract":
            text = text[:-e["chars"]]
    return text


# ── the loop ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_round_content_is_retracted_before_the_tool_runs():
    with patch("agents.llm.stream", new=_leaky_then_answer()), patch("agents.tools.execute", new=_fake_execute):
        events = [ev async for ev in agents.run_stream(agents.AGENTS["home"], "weather?", [])]
    types_ = [e["type"] for e in events]
    r = types_.index("retract")
    assert events[r]["chars"] == len(LEAK) and events[r]["preview"].startswith("player:")
    assert types_.index("narration") > r or "narration" not in types_   # retract precedes tool execution
    assert _apply(events) == "Sunny, 72."


@pytest.mark.asyncio
async def test_direct_answer_is_never_retracted():
    async def fake_stream(messages, model, tools=None):
        yield {"token": "Denver is in Mountain Time."}
        yield {"usage": {}}
    with patch("agents.llm.stream", new=fake_stream):
        events = [ev async for ev in agents.run_stream(agents.AGENTS["home"], "tz?", [])]
    assert not [e for e in events if e["type"] == "retract"]
    assert _apply(events) == "Denver is in Mountain Time."


@pytest.mark.asyncio
async def test_delegated_result_excludes_the_leak():
    with patch("agents.llm.stream", new=_leaky_then_answer()), patch("agents.tools.execute", new=_fake_execute):
        text, terminal = await agents.run_delegated(agents.AGENTS["home"], "weather?", [])
    assert text == "Sunny, 72." and terminal is False


@pytest.mark.asyncio
async def test_terminal_passthrough_stays_clean_when_the_specialist_leaks():
    """Coordinator → ask_home; home leaks, then calls a terminal tool."""
    coord_prompt = agents.COORDINATOR.system_prompt[:60]
    home_prompt = agents.AGENTS["home"].system_prompt[:60]

    async def fake_stream(messages, model, tools=None):
        system = messages[0]["content"]
        if system.startswith(coord_prompt):
            yield {"token": "Let me handle that. "}          # coordinator chatter before delegating
            yield {"tool_calls": [{"id": "c1", "function": {"name": "ask_home", "arguments": {"query": "pause"}}}]}
        elif system.startswith(home_prompt):
            yield {"token": LEAK}
            yield {"tool_calls": [{"id": "h1", "function": {"name": "control_music", "arguments": {"action": "pause"}}}]}
        yield {"usage": {}}

    async def fake_execute(name, args):
        return "[Music control: Paused on the Kitchen speaker.]"

    with patch("agents.llm.stream", new=fake_stream), patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agents.COORDINATOR, "Pause.", [])]
    assert _apply(events) == "Paused on the Kitchen speaker."


# ── the transports ──────────────────────────────────────────────────────────

def test_trim_reply_across_token_boundaries():
    parts = ["Let me ", "handle ", "that."]
    orch._trim_reply(parts, 5)              # "that." → gone
    assert parts == ["Let me ", "handle "]
    orch._trim_reply(parts, 3)              # "le " from "handle "
    assert parts == ["Let me ", "hand"]
    orch._trim_reply(parts, 99)             # over-trim never raises
    assert parts == []


@pytest.mark.asyncio
async def test_voice_collector_drops_retracted_text():
    async def fake_tokens(text, model, context=None, req_origin=None):
        yield {"type": "token", "text": LEAK}
        yield {"type": "retract", "chars": len(LEAK), "preview": LEAK[:160]}
        yield {"type": "token", "text": "Paused on the Kitchen speaker."}
    with patch.object(orch, "_kronk_pipeline_tokens", new=fake_tokens):
        assert await orch._ollama_collect("pause", "m", []) == "Paused on the Kitchen speaker."


@pytest.mark.asyncio
async def test_streaming_shims_pass_tokens_and_skip_retracts():
    async def fake_tokens(text, model, context=None, req_origin=None):
        yield {"type": "token", "text": "a"}
        yield {"type": "retract", "chars": 1, "preview": "a"}
        yield {"type": "token", "text": "b"}
    with patch.object(orch, "_kronk_pipeline_tokens", new=fake_tokens):
        lines = [l async for l in orch._ollama_pipeline_stream("x", "m", [])]
        contents = [json.loads(l)["message"]["content"] for l in lines if json.loads(l).get("message")]
        assert contents[:2] == ["a", "b"]                        # can't retract; documented
        sse = [c async for c in orch._openai_pipeline_stream("x", "m", "rid", [])]
        assert '"content": "a"' in "".join(sse) and '"content": "b"' in "".join(sse)


def test_message_sse_emits_retract_and_history_excludes_it(client):
    """Full /message path: the SSE carries {"retract": N}; the stored reply
    is the clean answer."""
    sessions = orch.sessions

    def fake_run_stream(agent, task, context, system_extra=None, history_messages=None, **kw):
        async def gen():
            yield {"type": "token", "text": LEAK}
            yield {"type": "retract", "chars": len(LEAK), "preview": LEAK[:160]}
            yield {"type": "token", "text": "Sunny, 72."}
            yield {"type": "done", "model": "gemma-4-e4b", "ok": True}
        return gen()

    with patch("orchestrator.main.agents.run_stream", new=fake_run_stream):
        resp = client.post("/message", json={"text": "how is it outside"})
    events = [json.loads(l[5:]) for l in resp.text.splitlines() if l.startswith("data:") and l[5:].strip() != "[DONE]"]
    assert {"retract": len(LEAK)} in events
    assert "".join(e["token"] for e in events if "token" in e) == LEAK + "Sunny, 72."   # streamed, then retracted
    hist = sessions.window(orch.WEBUI_SESSION)
    assert hist[-1]["role"] == "assistant" and hist[-1]["content"] == "Sunny, 72."
