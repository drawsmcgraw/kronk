"""
Tests for the post-refactor orchestrator flow.

Covers:
- No regex-based intent matching in orchestrator (routing.py has regex shortcuts,
  orchestrator/main.py should not).
- Tool catalog registered.
- `routing.classify()` deterministic pre-checks + LLM fallback + invalid-route guard.
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
os.environ.setdefault("ROUTER_MODEL",        "gemma-3-4b")

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
    """A message containing http(s)://… bypasses the LLM and routes to research."""
    import routing
    with patch("routing.llm.complete", new=AsyncMock()) as fake_complete:
        route = await routing.classify("Summarize https://example.com/post", [])
    assert route == "research"
    fake_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_search_phrase_shortcut_is_deterministic():
    """Explicit 'search for' / 'look up' phrases bypass the LLM."""
    import routing
    with patch("routing.llm.complete", new=AsyncMock()) as fake_complete:
        route = await routing.classify("search for the latest ROCm driver", [])
    assert route == "research"
    fake_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_magic_mirror_split_is_deterministic():
    """update/upgrade → home (fast terminal tool); any other mirror mention
    → devops (remote_exec loop). Neither consults the LLM router."""
    import routing
    with patch("routing.llm.complete", new=AsyncMock()) as fake:
        assert await routing.classify("update the magic mirror", []) == "home"
        assert await routing.classify("what's the uptime of the magic mirror", []) == "devops"
        assert await routing.classify("why is the magic mirror slow", []) == "devops"
    fake.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_weather_shortcut_is_deterministic():
    """Weather/forecast queries bypass the LLM and route to home (incident
    2026-07-05: 'what is tomorrow's forecast?' went to research)."""
    import routing
    with patch("routing.llm.complete", new=AsyncMock()) as fake_complete:
        route = await routing.classify("what is tomorrow's forecast?", [])
    assert route == "home"
    fake_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_search_phrase_outranks_weather():
    """Explicit search phrasing keeps weather queries on research — NWS is
    US-only, so 'look up the weather in Tokyo' must stay a web search."""
    import routing
    with patch("routing.llm.complete", new=AsyncMock()) as fake_complete:
        route = await routing.classify("look up the weather in Tokyo", [])
    assert route == "research"
    fake_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_llm_picks_valid_agent():
    """When no shortcut applies, the router LLM's single-word output is returned."""
    import routing
    fake = AsyncMock(return_value={"content": "health", "usage": {}})
    with patch("routing.llm.complete", new=fake):
        route = await routing.classify("how did I sleep last night?", [])
    assert route == "health"


@pytest.mark.asyncio
async def test_route_invalid_llm_output_falls_back_to_direct():
    """If the router returns garbage, classify() must fall back to 'direct'."""
    import routing
    fake = AsyncMock(return_value={"content": "¯\\_(ツ)_/¯", "usage": {}})
    with patch("routing.llm.complete", new=fake):
        route = await routing.classify("hello", [])
    assert route == "direct"


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
