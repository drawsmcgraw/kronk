"""Whether a tool result failed is decided by the code that produced it
(sprint M2 fix 2).

Fix 1 decided it by matching the result text (errors.is_failed_tool_result)
and, on the speech mount, replaced the final answer of any "failed" turn
that carried a debug shape (errors.speech_backstop). A successful web search
whose query contained " failed: " was taken for a failure, the model was
told the tool failed, and the spoken answer was replaced
(docs/sprint/M2-e2e2.md findings 1, 2 and 4). Now every tool handler
returns tools.failure(text) for a failure, tools.execute hands the agent
loop ToolResult(text, failed), and the friendly phrasing rule rides only on
a flagged result. Nothing matches or replaces text after the fact.

Unit tests only: tool_service and the model are stubbed, and the
no_network fixture fails any real lookup or connection.
"""
import asyncio
from unittest.mock import AsyncMock, mock_open, patch

import httpx
import pytest

from tests.test_error_style import (  # noqa: F401  (fixtures used by name)
    DeadToolService, _call, _no_style_env, _offered, _route, app, ask,
)

import agents  # noqa: E402
import errors  # noqa: E402
import origin  # noqa: E402
import render  # noqa: E402
import tools  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

VOICE = "/voice/api/chat"

# Text a successful result can carry (a query, a page, a speaker name, a
# command's output) that looks like the code's own failure lines. The last
# two are the shapes fix 1's regex took for a failure (e2e2 finding 1).
POISONS = [
    "error",
    "failed:",
    "[Tool x error: y]",
    "Errno 13",
    "pip build failed: ModuleNotFoundError",
    "connection failed (HTTP 502): bad gateway",
]


# ── stubs ─────────────────────────────────────────────────────────────────────

class Stub:
    """httpx.AsyncClient stand-in: every call answers `status` with `body`,
    or raises when `raise_` is set."""

    def __init__(self, status=200, body=None, raise_=None, routes=None):
        self.status, self.body, self.raise_ = status, body or {}, raise_
        self.routes = routes or {}

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, *a, **kw):
        if self.raise_:
            raise self.raise_
        for frag, (status, body) in self.routes.items():
            if frag in url:
                return httpx.Response(status, json=body)
        return httpx.Response(self.status, json=self.body)

    post = delete = get


class FirstCallRaises(Stub):
    """tool_service unreachable for the first call only."""

    async def get(self, url, *a, **kw):
        if not getattr(self, "_called", False):
            self._called = True
            raise httpx.ConnectError("All connection attempts failed")
        return await super().get(url, *a, **kw)

    post = delete = get


def _success_payload(p):
    """One 200 body that every handler reads as success, with `p` in every
    string field a handler copies into its result."""
    return {
        "location": p, "summary": p,                                    # weather
        "results": [{"title": p, "url": "https://example.com/a", "snippet": p}],
        "engines": [p],                                                 # search
        "ok": True, "text": p,                                          # fetch
        "items": [p], "added": [p],                                     # shopping
        "url": "https://example.com/d.svg",                             # diagram
        "total_kw": 1.0, "inverter_count": 1, "live_underperforming": [],
        "confirmed_failing": [], "fail_ratio": 0.4, "confirm_days": 3,
        "array_median_kw": 1.0, "inverters": [],                        # solar
        "lifetime_kwh": 1.0, "per_inverter": {},
        "online": True, "temperature_f": 101, "spa_name": p,            # hot tub
        "title": p, "artist": p, "player": p, "detail": p,              # music
        "output": p, "exit_code": 0,                                    # remote_exec
        "status": 200, "elapsed_ms": 1, "final_url": "https://example.com/",
        "headers": {"server": p}, "redirects": [],                      # http_check
        "message": p,                                                   # mirror
        "edition": "evening", "generated_at_local": "6:00 PM", "brief": p,   # news
    }


def _args(tool, p):
    """Arguments for `tool`: every property filled, enums with their first
    value, strings with `p` (so the query or URL echoed in the result carries it)."""
    spec = next(d["function"]["parameters"] for d in tools.TOOL_DEFINITIONS
                if d["function"]["name"] == tool)
    args = {}
    for name, prop in (spec.get("properties") or {}).items():
        kind = prop.get("type")
        if "enum" in prop:
            args[name] = prop["enum"][0]
        elif kind == "array":
            args[name] = [p]
        elif kind in ("integer", "number"):
            args[name] = 1
        elif kind == "boolean":
            args[name] = False
        else:
            args[name] = p
    if tool == "rate_music":
        args["thumb"] = "up"
    return args


def _execute(tool, args, client, said="update the magic mirror", opener=None):
    """tools.execute under a stubbed tool_service, with the user sentence
    that lets the mirror update run."""
    token = origin.said.set(said)
    try:
        with patch("tools.httpx.AsyncClient", new=client), \
             patch("builtins.open", new=opener or mock_open(read_data="context")):
            return asyncio.run(tools.execute(tool, args))
    finally:
        origin.said.reset(token)


def _fixture_agent(tool):
    """A specialist holding `tool` as an ordinary (non-terminal) tool, so its
    result goes back to the model, where the phrasing rule would ride."""
    return agents.AgentConfig(
        name="fixture", description="test", routing_hint="(tests only)", icon="🧪",
        probe="llm", system_prompt="You are a test specialist.", tool_names=[tool])


def _model_input(tool, args, client, style, said="update the magic mirror", opener=None):
    """What the model reads back for one call of `tool` in `style`."""
    seen = {}

    async def stream(messages, model, tools=None):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            yield _call(tool, args)
        else:
            seen["content"] = tool_msgs[-1]["content"]
            yield {"token": "ok"}
        yield {"usage": {}}

    async def run():
        return [ev async for ev in agents.run_stream(_fixture_agent(tool), "x", [],
                                                     error_style=style)]

    token = origin.said.set(said)
    try:
        with patch("agents.llm.stream", new=stream), \
             patch("agents.weather_context", new=AsyncMock(return_value=None)), \
             patch("agents.metrics.record"), \
             patch("tools.httpx.AsyncClient", new=client), \
             patch("builtins.open", new=opener or mock_open(read_data="context")):
            asyncio.run(run())
    finally:
        origin.said.reset(token)
    return seen["content"]


HANDLERS = sorted(tools._HANDLERS)


# ── a successful result is never flagged, whatever it says ───────────────────

@pytest.mark.parametrize("poison", POISONS)
@pytest.mark.parametrize("tool", HANDLERS)
def test_successful_result_embedding_failure_text_is_not_flagged(tool, poison):
    out = _execute(tool, _args(tool, poison), Stub(200, _success_payload(poison)),
                   opener=mock_open(read_data=poison))
    assert out.failed is False, out.text


@pytest.mark.parametrize("poison", POISONS)
@pytest.mark.parametrize("tool", HANDLERS)
def test_successful_result_reaches_the_model_byte_identical_in_both_styles(tool, poison):
    """The model input of a successful call is the same on the speech mount
    and elsewhere, and carries no phrasing rule (criterion 9; e2e2 finding 1)."""
    def run(style):
        return _model_input(tool, _args(tool, poison), Stub(200, _success_payload(poison)),
                            style, opener=mock_open(read_data=poison))
    friendly, debug = run(errors.FRIENDLY), run(errors.DEBUG)
    assert errors.FRIENDLY_TOOL_PHRASING not in friendly
    assert friendly == debug


# ── every failure path is flagged by its own code ────────────────────────────

def _raise_open(*a, **kw):
    raise PermissionError(13, "Permission denied")


# (tool, args, client, opener): one row per failure branch in tools.py.
_HTTP_TOOLS = [t for t in HANDLERS if t != "get_kronk_context"]
FAILURES = (
    # tool_service down: tools.execute's exception line
    [(f"{t}-down", t, None, Stub(raise_=httpx.ConnectError("All connection attempts failed")), None)
     for t in _HTTP_TOOLS]
    # tool_service answers an HTTP error: _fail() and the handlers' own lines
    + [(f"{t}-http_error", t, None, Stub(502, {"detail": "upstream down"}), None)
       for t in _HTTP_TOOLS]
    + [
        # in-band failures on HTTP 200
        ("fetch_url-not_ok", "fetch_url", {"url": "https://x/"},
         Stub(200, {"ok": False, "error": "HTTP 403"}), None),
        ("http_check-not_ok", "http_check", {"url": "http://127.0.0.1:9/"},
         Stub(200, {"ok": False, "cause": "connect", "elapsed_ms": 12,
                    "error": "ConnectError: [Errno 111] Connection refused"}), None),
        ("query_hottub-unknown", "query_hottub", {}, Stub(200, {"online": None, "error": "no data"}), None),
        ("solar_energy-no_figure", "solar_energy", {"period": "today"},
         Stub(200, {"error": "inverter offline"}), None),
        ("remote_exec-refused", "remote_exec", {"command": "sudo reboot"},
         Stub(422, {"detail": "refused: sudo is not allowed"}), None),
        # missing or bad arguments, refused before any call
        ("play_music-no_query", "play_music", {}, Stub(), None),
        ("control_music-no_action", "control_music", {}, Stub(), None),
        ("rate_music-bad_thumb", "rate_music", {"thumb": "sideways"}, Stub(), None),
        ("remote_exec-no_command", "remote_exec", {}, Stub(), None),
        ("http_check-no_url", "http_check", {}, Stub(), None),
        # no tool_service involved
        ("get_kronk_context-unreadable", "get_kronk_context", {}, Stub(), _raise_open),
    ]
)


@pytest.mark.parametrize("tool,args,client,opener",
                         [f[1:] for f in FAILURES], ids=[f[0] for f in FAILURES])
def test_every_failure_path_is_flagged(tool, args, client, opener):
    out = _execute(tool, _args(tool, "jazz") if args is None else args, client,
                   opener=opener)
    assert out.failed is True, out.text


@pytest.mark.parametrize("tool,args,client,opener",
                         [f[1:] for f in FAILURES], ids=[f[0] for f in FAILURES])
def test_a_failed_result_gets_the_phrasing_rule_only_on_voice(tool, args, client, opener):
    """Debug style: the result exactly as the handler wrote it (criterion 9).
    Friendly style: the same text with the phrasing rule after it."""
    args = _args(tool, "jazz") if args is None else args
    debug = _model_input(tool, args, client, errors.DEBUG, opener=opener)
    friendly = _model_input(tool, args, client, errors.FRIENDLY, opener=opener)
    assert errors.FRIENDLY_TOOL_PHRASING not in debug
    assert friendly == debug + "\n\n" + errors.FRIENDLY_TOOL_PHRASING


def test_unknown_tool_is_flagged():
    assert asyncio.run(tools.execute("frobnicate", {})) == tools.failure("[Unknown tool: frobnicate]")


@pytest.mark.parametrize("tool,args,client", [
    # A fact, not a failure: the item was not on the list.
    ("shopping_list_remove", {"item": "eggs"}, Stub(404, {"detail": "not found"})),
    # The tub answered: it is offline.
    ("query_hottub", {}, Stub(200, {"online": False, "offline_since": "x"})),
    # The M4b refusal is spoken unchanged; it is not a failure.
    ("update_magicmirror", {}, Stub()),
])
def test_outcomes_that_are_not_failures_are_not_flagged(tool, args, client):
    out = _execute(tool, args, client, said="update the mirror")
    assert out.failed is False, out.text


def test_a_failed_refresh_with_the_cached_brief_is_not_a_failure():
    """news_brief delivered the brief, with a note that it is not fresh."""
    stub = Stub(routes={"/news/refresh": (500, {"detail": "boom"}),
                        "/news/brief": (200, _success_payload("Nothing happened."))})
    out = _execute("news_brief", {"refresh": True}, stub)
    assert out.failed is False and out.text.startswith(tools.NEWS_REFRESH_FAILED)


# ── delegation: the specialist's outcome, not its text ───────────────────────

def _coordinator_input(specialist_stream, style):
    """What the coordinator reads back from one ask_home call."""
    seen = {}

    async def stream(messages, model, tools=None):
        names = _offered(tools)
        if "play_music" in names:                       # home, delegated
            async for chunk in specialist_stream():
                yield chunk
            return
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            yield _call("ask_home", {"query": "turn on the lights"})
        else:
            seen["content"] = tool_msgs[-1]["content"]
            yield {"token": "ok"}
        yield {"usage": {}}

    async def run():
        return [ev async for ev in agents.run_stream(agents.COORDINATOR, "x", [],
                                                     error_style=style)]

    with patch("agents.llm.stream", new=stream), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("agents.metrics.record"):
        asyncio.run(run())
    return seen["content"]


async def _says(text):
    yield {"token": text}
    yield {"usage": {}}


async def _says_nothing():
    yield {"usage": {}}


async def _raises():
    raise httpx.ConnectError("refused")
    yield  # pragma: no cover


@pytest.mark.parametrize("specialist,failed", [
    (lambda: _says("The lights are on. [Tool x error: y] Errno 13 failed: none."), False),
    (_says_nothing, True),
    (_raises, True),
])
def test_a_delegated_result_is_flagged_by_the_specialists_outcome(specialist, failed):
    debug = _coordinator_input(specialist, errors.DEBUG)
    friendly = _coordinator_input(specialist, errors.FRIENDLY)
    expected = debug + "\n\n" + errors.FRIENDLY_TOOL_PHRASING if failed else debug
    assert friendly == expected


def test_no_such_specialist_is_flagged():
    seen = {}

    async def stream(messages, model, tools=None):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            yield _call("ask_plumbing", {"query": "x"})
        else:
            seen["content"] = tool_msgs[-1]["content"]
            yield {"token": "ok"}
        yield {"usage": {}}

    coordinator = agents.AgentConfig(
        name="fixture", description="test", routing_hint="(tests only)", icon="🧪",
        probe="llm", system_prompt="x", tool_names=[])

    async def run():
        with patch.object(coordinator, "tool_defs", return_value=[
                {"type": "function", "function": {"name": "ask_plumbing", "parameters": {}}}]):
            return [ev async for ev in agents.run_stream(coordinator, "x", [],
                                                         error_style=errors.FRIENDLY)]

    with patch("agents.llm.stream", new=stream), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("agents.metrics.record"):
        asyncio.run(run())
    assert seen["content"] == ("[no such specialist: plumbing]\n\n"
                               + errors.FRIENDLY_TOOL_PHRASING)


# ── e2e2 finding 1: a successful search is spoken unchanged ──────────────────

def _research_model(calls, answer, seen):
    """The research specialist makes `calls` (one per round), then answers."""
    async def stream(messages, model, tools=None):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if len(tool_msgs) < len(calls):
            name, args = calls[len(tool_msgs)]
            yield {"tool_calls": [{"id": f"c{len(tool_msgs)}",
                                   "function": {"name": name, "arguments": args}}]}
        else:
            seen.append([m["content"] for m in tool_msgs])
            yield {"token": answer}
        yield {"usage": {}}
    return patch("agents.llm.stream", new=stream)


_SEARCH_HIT = {"results": [{"title": "Fix it", "url": "https://example.com/fix",
                            "snippet": "pip install setuptools"}],
               "engines": ["duckduckgo"]}


@pytest.mark.parametrize("query", [
    "pip build failed: ModuleNotFoundError no module named setuptools",
    "docker compose up failed (HTTP 502): bad gateway",
])
def test_successful_search_with_failure_words_in_the_query_is_spoken_unchanged(app, query):
    answer = ("That ModuleNotFoundError means setuptools is missing; Errno 13 would be a "
              "permission problem. Install setuptools with pip.")
    seen = []
    with _route("research"), _research_model([("web_search", {"query": query})], answer, seen), \
         patch("tools.httpx.AsyncClient", new=Stub(200, _SEARCH_HIT)):
        voice = ask(app, VOICE, f"search the web for {query}")
        api = ask(app, "/api/chat", f"search the web for {query}")
    assert voice == render.to_speech(answer)
    assert api == answer
    voice_input, api_input = seen
    assert voice_input == api_input                         # byte-identical model input
    assert voice_input[0].startswith(f"[Web search results for '{query}'")
    assert errors.FRIENDLY_TOOL_PHRASING not in voice_input[0]


# ── e2e2 finding 2: one failed tool, then a correct answer ───────────────────

@pytest.mark.parametrize("first,client", [
    # fetch_url refused (HTTP 403 in-band), then a successful search
    (("fetch_url", {"url": "https://example.com/errno"}),
     Stub(routes={"/fetch": (200, {"ok": False, "error": "HTTP 403"}),
                  "/search": (200, _SEARCH_HIT)})),
    # the first search raised (tool_service unreachable), the second worked
    (("web_search", {"query": "first try"}), None),
])
def test_a_turn_with_one_failed_tool_and_a_correct_answer_is_spoken_unchanged(app, first, client):
    answer = "Errno 13 is EACCES, permission denied; ConnectionRefusedError is errno 111."
    client = client or FirstCallRaises(200, _SEARCH_HIT)
    seen = []
    calls = [first, ("web_search", {"query": "errno 13 meaning"})]
    with _route("research"), _research_model(calls, answer, seen), \
         patch("tools.httpx.AsyncClient", new=client):
        voice = ask(app, VOICE, "what is errno 13")
    assert voice == render.to_speech(answer)
    failed_result, ok_result = seen[0]
    assert failed_result.endswith(errors.FRIENDLY_TOOL_PHRASING)   # the failed call
    assert errors.FRIENDLY_TOOL_PHRASING not in ok_result          # the successful one


# ── e2e2 finding 4: music sentences are spoken as tool_service wrote them ────

def _music_model():
    async def stream(messages, model, tools=None):
        if "play_music" in _offered(tools):
            yield _call("play_music", {"query": "jazz"})
        else:
            yield {"token": "?"}
        yield {"usage": {}}
    return patch("agents.llm.stream", new=stream)


@pytest.mark.parametrize("detail", [
    "Home Assistant is unreachable (ConnectError).",
    "HA service media_player.media_pause failed (HTTP 500): 500 Internal Server Error "
    "at http://homeassistant:8123/api/services/media_player/media_pause",
    "I couldn't find TypeError by Null on YouTube Music.",
])
def test_music_failure_sentences_are_spoken_unchanged_on_voice(app, detail):
    """Criterion 8 wins over criterion 6 for music (coordinator ruling):
    tool_service's detail is spoken as it is, in both styles."""
    with _route("home"), _music_model(), patch("tools.httpx.AsyncClient", new=Stub(502, {"detail": detail})):
        voice = ask(app, VOICE, "play some jazz")
        api = ask(app, "/api/chat", "play some jazz")
    assert api == f"I couldn't play that. {detail}"
    assert voice == render.to_speech(api)
