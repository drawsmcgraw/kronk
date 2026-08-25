"""
Tests for the post-refactor orchestrator flow.

Covers:
- No regex-based intent matching in orchestrator (routing.py has regex shortcuts,
  orchestrator/main.py should not).
- Tool catalog registered.
- `routing.classify()` deterministic shortcuts + coordinator default (the
  LLM router was removed 2026-08-18 — COORDINATOR_ROUTING_PLAN).
- `agents.run_stream()` event sequence (token + done) on both direct-answer
  and tool-then-synthesis paths.
- /message streaming of direct (coordinator) answers.
- `_execute_tool()` helper against tool_service.
- Garmin CSV upload routing.

Live integration tests are gated on KRONK_LIVE=1 (hit the real orchestrator).
"""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from fastapi.testclient import TestClient

os.environ.setdefault("LLM_SERVICE_URL",     "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL",    "http://fake-tools:8003")
os.environ.setdefault("HEALTH_SERVICE_URL",  "http://fake-health:8004")
os.environ.setdefault("FINANCE_SERVICE_URL", "http://fake-finance:8005")
os.environ.setdefault("COORDINATOR_MODEL",   "gemma-4-e4b")

import unittest.mock as mock_module
_open_orig = open

def _fake_open(path, *a, **kw):
    if "/app/static/" in str(path):
        import io
        return io.StringIO("<html></html>")
    return _open_orig(path, *a, **kw)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_history():
    import orchestrator.main as orch
    import orchestrator.sessions as sessions
    sessions.clear(orch.WEBUI_SESSION)
    orch.file_contexts.clear()
    yield
    sessions.clear(orch.WEBUI_SESSION)
    orch.file_contexts.clear()


@pytest.fixture
def client(tmp_path):
    with mock_module.patch("builtins.open", side_effect=_fake_open):
        import orchestrator.main as orch
        import orchestrator.metrics as metrics
        import orchestrator.sessions as sessions
        # Hermetic DBs — the defaults point at /data, which only exists in
        # the container.
        with mock_module.patch.object(metrics, "METRICS_DB", tmp_path / "metrics.db"), \
             mock_module.patch.object(sessions, "SESSIONS_DB", tmp_path / "sessions.db"):
            with TestClient(orch.app, raise_server_exceptions=True) as c:
                yield c


# ── Structural tests ──────────────────────────────────────────────────────────

def test_no_regex_in_orchestrator():
    """orchestrator/main.py must not contain the retired intent-routing regexes."""
    import orchestrator.main as orch
    import inspect
    source = inspect.getsource(orch)
    forbidden = ["re.compile", "WEATHER_KEYWORDS", "SEARCH_PATTERN", "LIST_ADD_PATTERN"]
    for pattern in forbidden:
        assert pattern not in source, f"Found old regex pattern in orchestrator: {pattern}"


def test_tool_definitions_registered():
    import orchestrator.main as orch
    names = {t["function"]["name"] for t in orch.TOOL_DEFINITIONS}
    expected = {
        "get_weather", "web_search", "fetch_url",
        "shopping_list_view", "shopping_list_add", "shopping_list_remove", "shopping_list_clear",
        "query_health", "query_finances",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"


# ── Routing tests (routing.py) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_url_shortcut_is_deterministic():
    """A message containing http(s)://… routes to research."""
    import routing
    assert await routing.classify("Summarize https://example.com/post", []) == "research"


@pytest.mark.asyncio
async def test_route_search_phrase_shortcut_is_deterministic():
    """Explicit 'search the web' / 'look up' phrases route to research."""
    import routing
    assert await routing.classify("search the web for the latest ROCm driver", []) == "research"


@pytest.mark.asyncio
async def test_route_magic_mirror_split_is_deterministic():
    """Exact phrase 'update the magic mirror' → home (fast terminal tool);
    any other mirror mention → devops (remote_exec loop)."""
    import routing
    assert await routing.classify("update the magic mirror", []) == "home"
    assert await routing.classify("what's the uptime of the magic mirror", []) == "devops"
    assert await routing.classify("why is the magic mirror slow", []) == "devops"


@pytest.mark.asyncio
async def test_route_weather_shortcut_is_deterministic():
    """Contextual weather queries route to home (incident 2026-07-05:
    'what is tomorrow's forecast?' went to research)."""
    import routing
    assert await routing.classify("what is tomorrow's forecast?", []) == "home"


@pytest.mark.asyncio
async def test_route_search_phrase_outranks_weather():
    """Explicit search phrasing keeps weather queries on research — NWS is
    US-only, so 'look up the weather in Tokyo' must stay a web search."""
    import routing
    assert await routing.classify("look up the weather in Tokyo", []) == "research"


@pytest.mark.asyncio
async def test_route_unmatched_defaults_to_coordinator():
    """No LLM router remains (COORDINATOR_ROUTING_PLAN, 2026-08-18):
    anything unmatched — including previously LLM-routed single-domain
    queries — goes to the coordinator, which delegates via ask_*."""
    import routing
    assert not hasattr(routing, "llm")   # no code path can consult a model
    assert await routing.classify("how did I sleep last night?", []) == "direct"
    assert await routing.classify("hello", []) == "direct"


# ── agents.run_stream tests ───────────────────────────────────────────────────

# run_stream drives everything through llm.stream(messages, model, tools)
# (the unified-streaming refactor) — these mocks match that contract.

@pytest.mark.asyncio
async def test_run_stream_direct_answer_yields_tokens_and_done():
    """When the first round has no tool_calls, the content is yielded and we stop."""
    import agents

    async def fake_stream(messages, model, tools=None):
        yield {"token": "42"}
        yield {"usage": {"prompt_tokens": 5, "completion_tokens": 1}}

    agent = agents.AGENTS["health"]
    with patch("agents.llm.stream", new=fake_stream):
        events = [ev async for ev in agents.run_stream(agent, "what is the answer?", [])]

    token_events = [e for e in events if e["type"] == "token"]
    done_events  = [e for e in events if e["type"] == "done"]
    assert token_events and "42" in token_events[0]["text"]
    assert done_events and done_events[0]["ok"] is True


@pytest.mark.asyncio
async def test_run_stream_tool_then_synthesis_streams_tokens():
    """One tool round (tool_calls from the stream), then synthesis tokens."""
    import agents

    call_count = {"n": 0}

    async def fake_stream(messages, model, tools=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Round 1: the model asks for a tool (no content tokens).
            yield {"tool_calls": [
                {"id": "call_1", "function": {"name": "query_health", "arguments": {"metric": "sleep"}}}
            ]}
            yield {"usage": {"prompt_tokens": 5, "completion_tokens": 3}}
        else:
            # Round 2: synthesis streams the answer.
            for t in ["You ", "slept ", "well."]:
                yield {"token": t}
            yield {"usage": {"prompt_tokens": 9, "completion_tokens": 3}}

    async def fake_execute(name, args):
        return "sleep: 7.8h"

    agent = agents.AGENTS["health"]
    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agent, "how did I sleep?", [])]

    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "You slept well."
    assert any(e["type"] == "done" and e["ok"] for e in events)
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_run_stream_emits_delegating_event_for_ask_tools():
    """Regression (2026-08-25): the Aug-18 routing collapse moved delegation
    inside run_stream, which had no stage vocabulary — the UI's 'research
    agent searching...' indicator went dark. ask_* calls now announce
    themselves structurally."""
    import agents

    calls = {"n": 0}

    async def fake_stream(messages, model, tools=None):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"tool_calls": [
                {"id": "c1", "function": {"name": "ask_research",
                                          "arguments": {"query": "tariffs"}}}
            ]}
            yield {"usage": {}}
        else:
            yield {"token": "Tariffs are complicated."}
            yield {"usage": {}}

    async def fake_run(agent, task, context):
        return "research findings here"

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.run", new=fake_run):
        events = [ev async for ev in agents.run_stream(
            agents.COORDINATOR, "tariffs?", [])]

    assert {"type": "delegating", "agent": "research"} in events


def test_stage_and_timing_labels_authored_at_emitter():
    """Display text is payload, not a UI lookup table (the old client-side
    table drifted — it still knew an 'assistant' agent that doesn't exist)."""
    import orchestrator.main as orch
    assert orch._stage("thinking") == {
        "type": "stage", "name": "thinking", "label": "thinking..."}
    assert orch._stage("fetching_delegate_research")["label"] == \
        "research agent searching..."
    assert orch._stage("fetching_delegate_home")["label"] == "home agent working..."
    assert orch._stage("fetching_delegate_newagent")["label"] == \
        "newagent agent working..."            # future agents label themselves
    t = orch._timing_stage("delegate_health", s=1.2, ok=True)
    assert t["label"] == "health agent" and t["service"] == "orchestrator"
    assert orch._timing_stage("routing", s=0.1)["label"] == "routing"


def test_sse_carries_stage_labels_and_delegation_stage(client):
    """The wire format: every stage event ships its label, and a coordinator
    delegation produces the fetching_delegate_* stage the UI renders."""
    async def fake_classify(text, history):
        return "direct"

    def fake_run_stream(agent, task, context, **kwargs):
        async def gen():
            yield {"type": "delegating", "agent": "research"}
            yield {"type": "token", "text": "Answer."}
            yield {"type": "done", "model": "gemma-4-e4b", "ok": True}
        return gen()

    with patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("orchestrator.main.agents.run_stream", new=fake_run_stream):
        resp = client.post("/message", json={"text": "tariffs?"})

    events = _collect_sse_events(resp.text)
    stages = {e["stage"]: e.get("stage_label") for e in events if "stage" in e}
    assert stages["thinking"] == "thinking..."
    assert stages["fetching_delegate_research"] == "research agent searching..."
    assert stages["generating"] == "generating..."
    timing = next(e["timing"] for e in events if "timing" in e)
    assert all("label" in st and "service" in st for st in timing["stages"])


@pytest.mark.asyncio
async def test_news_brief_is_terminal_and_verbatim_on_coordinator():
    """NEWS_BRIEF_PLAN: the brief's text must reach the user with no
    synthesis round after it (rid 99ce926c — a brief re-summarized to 754
    chars). One LLM call, tool text streamed exactly, turn over."""
    import agents

    BRIEF = ("Midday news brief, generated Sun 12:04 PM:\n\n"
             "World\nA multi-paragraph brief that must arrive uncompressed.\n\n"
             "Tech & AI\nMore paragraphs.\n\nCybersecurity\nStill more.")
    llm_calls = {"n": 0}

    async def fake_stream(messages, model, tools=None):
        llm_calls["n"] += 1
        yield {"tool_calls": [
            {"id": "c1", "function": {"name": "news_brief", "arguments": {}}}
        ]}
        yield {"usage": {}}

    async def fake_execute(name, args):
        assert name == "news_brief"
        return BRIEF

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(
            agents.COORDINATOR, "give me a news brief", [])]

    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == BRIEF                       # verbatim, all sections
    assert llm_calls["n"] == 1                   # no synthesis round ran
    assert any(e["type"] == "done" and e["ok"] for e in events)


def test_coordinator_menu_carries_news_brief_as_terminal():
    import agents
    names = {d["function"]["name"] for d in agents.COORDINATOR.tool_defs()}
    assert "news_brief" in names
    assert any(n.startswith("ask_") for n in names)   # agent-tools intact
    assert "news_brief" in agents.COORDINATOR.terminal_tools
    # Specialists must NOT have it — the brief belongs to the coordinator.
    assert "news_brief" not in agents.AGENTS["home"].tool_names
    assert "news_brief" not in agents.AGENTS["research"].tool_names


def test_terminal_speech_passthrough_for_news_brief():
    import agents
    text = "Evening news brief, generated Sun 06:02 PM:\n\nWorld\nlines\nlines"
    assert agents._terminal_speech(text, tool="news_brief") == text


@pytest.mark.asyncio
async def test_run_stream_escalation_is_terminal():
    """Phase-2 escalation (COORDINATOR_ROUTING_PLAN): a top-level pinned
    specialist calling `escalate` ends its turn with an `escalated` event —
    no answer tokens, no done event — carrying the note for the pipeline."""
    import agents

    async def fake_stream(messages, model, tools=None):
        yield {"tool_calls": [
            {"id": "call_1", "function": {"name": "escalate", "arguments": {
                "note": "I can provide yesterday's kWh but not electricity rates."}}}
        ]}
        yield {"usage": {}}

    agent = agents.AGENTS["home"]
    with patch("agents.llm.stream", new=fake_stream):
        events = [ev async for ev in agents.run_stream(
            agent, "money's worth of solar yesterday?", [], allow_escalation=True)]

    assert events[-1]["type"] == "escalated"
    assert "kWh" in events[-1]["note"]
    assert not any(e["type"] == "done" for e in events)
    assert not any(e["type"] == "token" for e in events)


@pytest.mark.asyncio
async def test_escalate_tool_only_offered_on_top_level_runs():
    """The escalate tool is injected ONLY when allow_escalation=True —
    delegated (ask_*) runs and the coordinator never see it, so escalation
    is structurally once-per-request."""
    import agents

    captured_tools: list = []

    async def fake_stream(messages, model, tools=None):
        captured_tools.append(tools)
        yield {"token": "answer"}
        yield {"usage": {}}

    agent = agents.AGENTS["home"]
    with patch("agents.llm.stream", new=fake_stream):
        [ev async for ev in agents.run_stream(agent, "status?", [])]
        [ev async for ev in agents.run_stream(agent, "status?", [],
                                              allow_escalation=True)]

    def names(defs):
        return {d["function"]["name"] for d in (defs or [])}
    assert "escalate" not in names(captured_tools[0])   # default: no escape hatch
    assert "escalate" in names(captured_tools[1])       # top-level pinned run


@pytest.mark.asyncio
async def test_repeated_tool_calls_get_stop_nudge():
    """Incident 2026-07-05: research burned all its rounds on near-identical
    web_search calls (reworded args → exact-dup dedup never fired). The third
    call to the same tool in one turn must carry a structural stop order in
    its result so the model is told to stop searching and answer."""
    import agents

    call_count = {"n": 0}
    captured_messages: list[list[dict]] = []

    async def fake_stream(messages, model, tools=None):
        captured_messages.append([dict(m) for m in messages])
        call_count["n"] += 1
        if call_count["n"] <= 3:
            yield {"tool_calls": [
                {"id": f"call_{call_count['n']}", "function": {
                    "name": "web_search",
                    "arguments": {"query": f"weather tomorrow v{call_count['n']}"},
                }}
            ]}
            yield {"usage": {}}
        else:
            yield {"token": "Sunny, high of 90."}
            yield {"usage": {}}

    async def fake_execute(name, args):
        return "[Web search results for '...'] some links"

    agent = agents.AGENTS["research"]
    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agent, "forecast?", [])]

    # The 4th LLM call sees the nudge appended to the 3rd tool result…
    tool_results = [m["content"] for m in captured_messages[3] if m.get("role") == "tool"]
    assert len(tool_results) == 3
    assert "Do not call web_search again" in tool_results[2]
    # …and the first two results are clean.
    assert all("Do not call" not in r for r in tool_results[:2])
    assert any(e["type"] == "done" and e["ok"] for e in events)


def test_research_round_budget_single_source():
    """The budget in the research prompt must match its max_rounds — the two
    were hardcoded separately (prompt said 5) and drifted apart from the
    config once already."""
    import agents
    research = agents.AGENTS["research"]
    assert research.max_rounds == agents.RESEARCH_MAX_ROUNDS
    assert f"budget of {agents.RESEARCH_MAX_ROUNDS} tool-use rounds" in research.system_prompt


@pytest.mark.asyncio
async def test_run_stream_llm_error_is_surfaced():
    """If llm.stream raises, run_stream emits a single error event."""
    import agents

    async def fake_stream(messages, model, tools=None):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover — makes this an async generator

    agent = agents.AGENTS["health"]
    with patch("agents.llm.stream", new=fake_stream):
        events = [ev async for ev in agents.run_stream(agent, "hi", [])]

    error_events = [e for e in events if e["type"] == "error"]
    assert error_events
    assert "health agent error" in error_events[0]["message"]


@pytest.mark.asyncio
async def test_run_accumulates_run_stream_tokens():
    """The sync-wrapper run() must return the full concatenated text."""
    import agents

    async def fake_stream(messages, model, tools=None):
        yield {"token": "hello "}
        yield {"token": "world"}
        yield {"usage": {}}

    agent = agents.AGENTS["health"]
    with patch("agents.llm.stream", new=fake_stream):
        text = await agents.run(agent, "hi", [])
    assert text == "hello world"


# ── /message streaming tests ──────────────────────────────────────────────────

def _collect_sse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


def test_direct_route_streams_from_coordinator(client):
    """'direct' route runs the COORDINATOR agent (agents-as-tools) and
    streams its tokens through the same run_stream loop as specialists."""

    async def fake_classify(text, history):
        return "direct"

    async def fake_stream(messages, model, tools=None):
        for t in ["Paris", " is ", "the capital."]:
            yield {"token": t}
        yield {"usage": {}}

    with patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("agents.llm.stream", new=fake_stream):
        resp = client.post("/message", json={"text": "What is the capital of France?"})

    events = _collect_sse_events(resp.text)
    tokens = [e["token"] for e in events if "token" in e]
    assert "".join(tokens) == "Paris is the capital."


def test_agent_route_streams_tokens_as_they_arrive(client):
    """A routed agent request forwards run_stream token events verbatim to SSE."""
    import orchestrator.main as orch

    async def fake_classify(text, history):
        return "health"

    async def fake_run_stream(agent, task, context, **kwargs):
        for t in ["You ", "slept ", "7.8 hours."]:
            yield {"type": "token", "text": t}
        yield {"type": "done", "model": "gemma-4-e4b", "ok": True}

    with patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("orchestrator.main.agents.run_stream", new=fake_run_stream):
        resp = client.post("/message", json={"text": "how did I sleep?"})

    events = _collect_sse_events(resp.text)
    tokens = [e["token"] for e in events if "token" in e]
    assert "".join(tokens) == "You slept 7.8 hours."
    # Timing metadata is emitted before [DONE].
    assert any("timing" in e for e in events)


def test_specialist_failure_reaches_coordinator_labeled_as_failure(client):
    """2026-07-05 review P1.1: a failed specialist's error used to be handed
    to the coordinator as a 'specialist result — use this to answer', so the
    coordinator paraphrased or invented. It must arrive labeled FAILED with
    instructions to report the cause, and the trace must be marked ERROR
    even though the coordinator recovers."""
    from unittest.mock import MagicMock
    captured = {}

    async def fake_classify(text, history):
        return "health"

    def fake_run_stream(agent, task, context, system_extra=None,
                        history_messages=None, **kwargs):
        async def gen():
            if agent.name == "health":
                yield {"type": "error",
                       "message": "[Health query failed (HTTP 503): database is locked]"}
            else:  # coordinator
                captured["system_extra"] = system_extra
                yield {"type": "token", "text": "The health service failed: database is locked."}
        return gen()

    fake_end = MagicMock()
    with patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("orchestrator.main.agents.run_stream", new=fake_run_stream), \
         patch("orchestrator.main.telemetry.end_pipeline", new=fake_end):
        resp = client.post("/message", json={"text": "how did I sleep?"})

    tokens = "".join(e["token"] for e in _collect_sse_events(resp.text) if "token" in e)
    assert "database is locked" in tokens  # detail survived to the user
    extra = captured["system_extra"]
    assert "FAILED" in extra
    assert "database is locked" in extra
    assert "use this to answer" not in extra  # the old lie is gone
    _, kwargs = fake_end.call_args
    assert kwargs.get("level") == "ERROR"
    assert "health specialist" in (kwargs.get("status_message") or "")


def test_escalation_reaches_coordinator_with_note(client):
    """Phase-2 escalation (COORDINATOR_ROUTING_PLAN, trace 02e8b817): a
    shortcut-pinned specialist hands a composite back; the pipeline re-enters
    at the coordinator carrying the specialist's note, the coordinator's
    answer replaces the specialist's output, the re-entry run cannot itself
    escalate, and the turn is NOT recorded as a pipeline error."""
    from unittest.mock import MagicMock
    captured = {}

    async def fake_classify(text, history):
        return "home"

    def fake_run_stream(agent, task, context, system_extra=None,
                        history_messages=None, **kwargs):
        async def gen():
            if agent.name == "home":
                assert kwargs.get("allow_escalation") is True
                yield {"type": "escalated",
                       "note": "I can provide yesterday's kWh but not electricity rates."}
            else:  # coordinator re-entry
                captured["system_extra"] = system_extra
                captured["allow_escalation"] = kwargs.get("allow_escalation", False)
                yield {"type": "token", "text": "Yesterday: 12 kWh, worth about $1.94."}
                yield {"type": "done", "model": "gemma-4-e4b", "ok": True}
        return gen()

    fake_end = MagicMock()
    with patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("orchestrator.main.agents.run_stream", new=fake_run_stream), \
         patch("orchestrator.main.telemetry.end_pipeline", new=fake_end):
        resp = client.post("/message",
                           json={"text": "how much money's worth did the solar panels produce yesterday?"})

    tokens = "".join(e["token"] for e in _collect_sse_events(resp.text) if "token" in e)
    assert tokens == "Yesterday: 12 kWh, worth about $1.94."  # coordinator's answer only
    extra = captured["system_extra"]
    assert "escalated this request back" in extra
    assert "yesterday's kWh but not electricity rates" in extra
    assert "ask_home" in extra                       # the specialist stays reachable
    assert captured["allow_escalation"] is False     # re-entry can't escalate again
    _, kwargs = fake_end.call_args
    assert kwargs.get("level") != "ERROR"            # escalation is not a failure


def test_router_failure_message_is_specific_not_speculative(client):
    """2026-07-05 review P1.5: any classify exception used to be reported as
    'could not reach the language model … Is the server still loading?' —
    actively misleading for e.g. a 400. The message must carry the actual
    error and the rid."""

    async def exploding_classify(text, history):
        raise RuntimeError("LiteLLM 400: template rejected conversation")

    with patch("orchestrator.main.routing.classify", new=exploding_classify):
        resp = client.post("/message", json={"text": "hello"})

    tokens = "".join(e["token"] for e in _collect_sse_events(resp.text) if "token" in e)
    assert "routing failed" in tokens.lower()
    assert "template rejected conversation" in tokens
    assert "rid " in tokens
    assert "still loading" not in tokens


def test_shopping_list_api_failure_is_not_an_empty_list(client):
    """2026-07-05 review P1.8: tool_service being down used to return
    {"items": []} — the page rendered 'Nothing on the list.' A failure must
    be a 502 so the page shows its offline state instead."""

    class ExplodingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            raise httpx.ConnectError("connection refused")

    with patch("orchestrator.main.httpx.AsyncClient", return_value=ExplodingClient()):
        resp = client.get("/api/shopping_list")
    assert resp.status_code == 502
    assert "Could not reach tool_service" in resp.json()["detail"]


def test_pipeline_crash_yields_specific_error_and_terminates_stream(client):
    """2026-07-05 review P0.5: an unexpected raise inside the pipeline used to
    kill the stream mid-flight — no error token, no [DONE], and HA spoke a
    generic 'unexpected error'. The last-resort guard must turn it into a
    specific spoken error (with the rid for Langfuse lookup) and still
    terminate the SSE stream properly."""

    async def fake_classify(text, history):
        return "direct"

    async def exploding_run_stream(*args, **kwargs):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover — makes this an async generator

    with patch("orchestrator.main.routing.classify", new=fake_classify), \
         patch("orchestrator.main.agents.run_stream", new=exploding_run_stream):
        resp = client.post("/message", json={"text": "hello"})

    assert resp.status_code == 200
    events = _collect_sse_events(resp.text)
    tokens = "".join(e["token"] for e in events if "token" in e)
    assert "pipeline failed unexpectedly" in tokens
    assert "RuntimeError: kaboom" in tokens  # specific cause, not generic
    assert "rid " in tokens                  # findable in telemetry
    assert "data: [DONE]" in resp.text       # stream still terminates cleanly


# ── _execute_tool helper tests (unchanged) ────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_tool_weather():
    import orchestrator.main as orch

    class FakeResp:
        status_code = 200
        def json(self): return {"location": "Laurel, MD", "summary": "Sunny, 72F"}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kw):
            assert "/weather" in url
            return FakeResp()

    with patch("orchestrator.main.httpx.AsyncClient", return_value=FakeClient()):
        result = await orch._execute_tool("get_weather", {"location": "Laurel, MD"})
    assert "Laurel, MD" in result and "Sunny" in result


@pytest.mark.asyncio
async def test_execute_tool_query_health_no_data():
    import orchestrator.main as orch

    class FakeResp:
        status_code = 200
        def json(self): return {"status": "no_data"}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kw): return FakeResp()

    with patch("orchestrator.main.httpx.AsyncClient", return_value=FakeClient()):
        result = await orch._execute_tool("query_health", {})
    assert "no data" in result.lower()


@pytest.mark.asyncio
async def test_execute_tool_query_finances_no_documents():
    import orchestrator.main as orch

    class FakeResp:
        status_code = 200
        def json(self): return {"status": "no_documents", "results": []}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kw): return FakeResp()

    with patch("orchestrator.main.httpx.AsyncClient", return_value=FakeClient()):
        result = await orch._execute_tool("query_finances", {"query": "income"})
    assert "none uploaded" in result.lower() or "no_documents" in result.lower()


@pytest.mark.asyncio
async def test_execute_tool_unknown():
    import orchestrator.main as orch
    result = await orch._execute_tool("does_not_exist", {})
    assert "Unknown tool" in result or "does_not_exist" in result


@pytest.mark.asyncio
async def test_execute_tool_handles_http_failure():
    import orchestrator.main as orch

    class FailingClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): raise httpx.ConnectError("refused")

    with patch("orchestrator.main.httpx.AsyncClient", return_value=FailingClient()):
        result = await orch._execute_tool("get_weather", {"location": "Laurel, MD"})
    assert "error" in result.lower() or "unavailable" in result.lower()


@pytest.mark.asyncio
async def test_tool_failure_surfaces_service_detail():
    """2026-07-05 review P1.2: six handlers flattened sub-service errors into
    generic strings ('[Web search failed]'). The _fail helper must keep the
    HTTP status and the service's JSON detail."""
    import tools

    class FakeResp:
        status_code = 503

        @property
        def text(self):
            return '{"detail": "SearXNG returned HTTP 500: upstream exploded"}'

        def json(self):
            return {"detail": "SearXNG returned HTTP 500: upstream exploded"}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return FakeResp()

    with patch("tools.httpx.AsyncClient", return_value=FakeClient()):
        result = await tools.execute("web_search", {"query": "anything"})
    assert "HTTP 503" in result
    assert "upstream exploded" in result
    assert result != "[Web search failed]"


@pytest.mark.asyncio
async def test_shopping_list_clear_verifies_instead_of_assuming():
    """2026-07-05 review P1.2/tenet 6: clear ignored the response entirely
    and always claimed '[Shopping list cleared]'."""
    import tools

    class FakeResp:
        status_code = 500
        text = "disk full"
        def json(self): raise ValueError("not json")

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def delete(self, *a, **kw): return FakeResp()

    with patch("tools.httpx.AsyncClient", return_value=FakeClient()):
        result = await tools.execute("shopping_list_clear", {})
    assert "cleared" not in result.lower()
    assert "HTTP 500" in result
    assert "disk full" in result


def test_terminal_speech_never_speaks_raw_tool_internals():
    """2026-07-05 review P1.4: an unmapped terminal result like a transport
    error used to be spoken verbatim — a stack trace read aloud."""
    import agents
    speech = agents._terminal_speech(
        "[Tool play_music error: ReadTimeout(ReadTimeout('timed out'))]"
    )
    assert speech.startswith("That didn't work")
    assert "ReadTimeout" in speech          # cause survives, shortened
    assert "Tool play_music error" not in speech  # scaffolding doesn't
    # The known shapes still map exactly as before.
    assert agents._terminal_speech("[Music playing: X on the kitchen speaker]") == \
        "Now playing X on the kitchen speaker."


# ── Garmin CSV auto-routing (unchanged) ───────────────────────────────────────

GARMIN_CSV_HEADERS = (
    "Activity ID,Activity Type,Date,Title,Distance,Calories,Time,Avg HR,Max HR\r\n"
    "12345,Running,2026-01-15 07:30:00,Morning Run,5.2,420,00:28:15,155,172\r\n"
)
NON_GARMIN_CSV = "name,value\nfoo,bar\n"


def test_is_garmin_csv_detects_garmin_export():
    import orchestrator.main as orch
    assert orch._is_garmin_csv(GARMIN_CSV_HEADERS.encode(), "activities.csv") is True


def test_is_garmin_csv_rejects_generic_csv():
    import orchestrator.main as orch
    assert orch._is_garmin_csv(NON_GARMIN_CSV.encode(), "data.csv") is False


def test_is_garmin_csv_rejects_non_csv_extension():
    import orchestrator.main as orch
    assert orch._is_garmin_csv(GARMIN_CSV_HEADERS.encode(), "activities.txt") is False


def test_upload_garmin_csv_routes_to_health_service(client):
    import orchestrator.main as orch

    class FakeHealthResp:
        status_code = 200
        text = ""
        def json(self): return {"inserted": 1, "skipped": 0}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            assert "import/csv" in url
            return FakeHealthResp()

    with patch("orchestrator.main.httpx.AsyncClient", return_value=FakeClient()):
        resp = client.post(
            "/files",
            files={"file": ("activities.csv", GARMIN_CSV_HEADERS.encode(), "text/csv")},
        )
    assert resp.status_code == 200
    assert resp.json()["routed_to"] == "health_service"
    assert len(orch.file_contexts) == 0


def test_upload_non_garmin_csv_goes_to_file_contexts(client):
    import orchestrator.main as orch
    resp = client.post("/files", files={"file": ("data.csv", NON_GARMIN_CSV.encode(), "text/csv")})
    assert resp.status_code == 200
    assert "routed_to" not in resp.json()
    assert any(fc["name"] == "data.csv" for fc in orch.file_contexts)


# ── Live integration (opt-in via KRONK_LIVE=1) ────────────────────────────────

LIVE = os.getenv("KRONK_LIVE") == "1"
LIVE_BASE = os.getenv("KRONK_LIVE_URL", "http://kronk.local")


@pytest.mark.skipif(not LIVE, reason="KRONK_LIVE not set — skipping live integration")
def test_live_status_endpoint_reports_ok():
    r = httpx.get(f"{LIVE_BASE}/api/status", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.skipif(not LIVE, reason="KRONK_LIVE not set — skipping live integration")
def test_live_message_returns_stream():
    """Smoke-test /message end-to-end against a running kronk stack."""
    with httpx.stream("POST", f"{LIVE_BASE}/message", json={"text": "hello"}, timeout=60) as r:
        assert r.status_code == 200
        saw_token = False
        for line in r.iter_lines():
            if line.startswith("data:") and "token" in line:
                saw_token = True
                break
    assert saw_token


@pytest.mark.asyncio
async def test_forced_synthesis_scrubs_leaked_tool_syntax():
    """Budget-cliff guardrail (2026-06-12): if the model emits tool-call
    syntax after its tools are stripped, the agent must scrub it and tell
    the user honestly instead of streaming raw syntax."""
    import agents

    calls = {"n": 0}

    async def fake_stream(messages, model, tools=None):
        calls["n"] += 1
        if tools is not None:
            # Every budgeted round burns the budget with a tool call.
            yield {"tool_calls": [
                {"id": f"c{calls['n']}", "function": {"name": "query_health",
                 "arguments": {"metric": f"m{calls['n']}"}}}
            ]}
            yield {"usage": {}}
        else:
            # Forced synthesis: the model tries to keep tool-calling as text.
            yield {"token": "<|tool_call>call:web_search{query:heads of state}<tool_call|>"}
            yield {"usage": {}}

    async def fake_execute(name, args):
        return "partial data"

    agent = agents.AGENTS["health"]
    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agent, "multi-step question", [])]

    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "tool_call" not in tokens
    assert "ran out of research steps" in tokens
    # The closure message must have been injected before the final call.
    assert calls["n"] == agent.max_rounds + 1
