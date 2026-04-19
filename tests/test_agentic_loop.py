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
    orch.history.clear()
    orch.file_contexts.clear()
    yield
    orch.history.clear()
    orch.file_contexts.clear()


@pytest.fixture
def client():
    with mock_module.patch("builtins.open", side_effect=_fake_open):
        import orchestrator.main as orch
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

@pytest.mark.asyncio
async def test_run_stream_direct_answer_yields_tokens_and_done():
    """When the first round has no tool_calls, the content is yielded and we stop."""
    import agents

    async def fake_complete(messages, tools, model):
        return {"content": "42", "tool_calls": [], "usage": {}}

    agent = agents.AGENTS["health"]
    with patch("agents.llm.complete", new=fake_complete):
        events = [ev async for ev in agents.run_stream(agent, "what is the answer?", [])]

    token_events = [e for e in events if e["type"] == "token"]
    done_events  = [e for e in events if e["type"] == "done"]
    assert token_events and "42" in token_events[0]["text"]
    assert done_events and done_events[0]["ok"] is True


@pytest.mark.asyncio
async def test_run_stream_tool_then_synthesis_streams_tokens():
    """One tool round, then synthesis streams tokens through llm.stream()."""
    import agents

    call_count = {"n": 0}

    async def fake_complete(messages, tools, model):
        call_count["n"] += 1
        # First call: the model asks for a tool.
        return {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "query_health", "arguments": {"metric": "sleep"}}}
            ],
            "usage": {},
        }

    async def fake_stream(messages, model):
        for t in ["You ", "slept ", "well."]:
            yield {"token": t}
        yield {"usage": {"prompt_tokens": 5, "completion_tokens": 3}}

    async def fake_execute(name, args):
        return "sleep: 7.8h"

    agent = agents.AGENTS["health"]
    with patch("agents.llm.complete", new=fake_complete), \
         patch("agents.llm.stream", new=fake_stream), \
         patch("agents.tools.execute", new=fake_execute):
        events = [ev async for ev in agents.run_stream(agent, "how did I sleep?", [])]

    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "You slept well."
    assert any(e["type"] == "done" and e["ok"] for e in events)


@pytest.mark.asyncio
async def test_run_stream_llm_error_is_surfaced():
    """If llm.complete raises, run_stream emits a single error event."""
    import agents

    async def fake_complete(messages, tools, model):
        raise RuntimeError("connection refused")

    agent = agents.AGENTS["health"]
    with patch("agents.llm.complete", new=fake_complete):
        events = [ev async for ev in agents.run_stream(agent, "hi", [])]

    error_events = [e for e in events if e["type"] == "error"]
    assert error_events
    assert "health agent error" in error_events[0]["message"]


@pytest.mark.asyncio
async def test_run_accumulates_run_stream_tokens():
    """The sync-wrapper run() must return the full concatenated text."""
    import agents

    async def fake_complete(messages, tools, model):
        return {"content": "hello world", "tool_calls": [], "usage": {}}

    agent = agents.AGENTS["health"]
    with patch("agents.llm.complete", new=fake_complete):
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
    """'direct' route skips agents.run_stream and streams from the coordinator."""
    import orchestrator.main as orch

    streaming_tokens = ["Paris", " is ", "the capital."]

    async def fake_classify(text, history):
        return "direct"

    with patch("orchestrator.main.routing.classify", new=fake_classify):
        with patch("orchestrator.main.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            def mock_stream_ctx(*a, **kw):
                class FakeStream:
                    async def __aenter__(s): return s
                    async def __aexit__(s, *args): pass
                    async def aiter_lines(s):
                        for t in streaming_tokens:
                            yield f"data: {json.dumps({'choices': [{'delta': {'content': t}}]})}"
                        yield "data: [DONE]"
                return FakeStream()
            inst.stream = mock_stream_ctx

            resp = client.post("/message", json={"text": "What is the capital of France?"})

    events = _collect_sse_events(resp.text)
    tokens = [e["token"] for e in events if "token" in e]
    assert "".join(tokens) == "Paris is the capital."


def test_agent_route_streams_tokens_as_they_arrive(client):
    """A routed agent request forwards run_stream token events verbatim to SSE."""
    import orchestrator.main as orch

    async def fake_classify(text, history):
        return "health"

    async def fake_run_stream(agent, task, context):
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
