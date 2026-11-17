"""Code-written failure text on the speech mount (sprint M2 fix 1).

The class under test: text the code writes for a failure reaches the
speaker through a path that skips the error style. Every such path now
leaves through errors.failure_text (friendly sentence on the speech mount,
today's exact text elsewhere), and errors.speech_backstop is the last check
on a failed spoken turn. Findings 1-3 and observations 4-6 of
docs/sprint/M2-e2e.md; report docs/sprint/M2-fix1.md.

Unit tests only: tool_service and the model are stubbed, and the
no_network fixture fails any real lookup or connection.
"""
import ast
import asyncio
import json
import logging
import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.test_error_style import (  # noqa: F401  (fixtures used by name)
    DeadToolService, _call, _no_style_env, _offered, _route, app, ask,
    assert_speakable,
)

import agents  # noqa: E402
import errors  # noqa: E402
import origin  # noqa: E402
import tools  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

ORCH = pathlib.Path(__file__).resolve().parent.parent / "orchestrator"
VOICE = "/voice/api/chat"

# A tool_service detail carrying every debug shape the backstop knows.
POISON = ("Mirror preflight failed: SSH key not found at /keys/kronk-mm-update "
          "— mount ./secrets/mm; [Errno -3] at http://10.0.0.5:8080/x")


class StubToolService:
    """httpx.AsyncClient stand-in: every call answers `status` with `body`."""

    def __init__(self, status=200, body=None, routes=None):
        self.status, self.body, self.routes = status, body or {}, routes or {}
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, *a, **kw):
        self.calls.append(url)
        for frag, (status, body) in self.routes.items():
            if frag in url:
                return httpx.Response(status, json=body)
        return httpx.Response(self.status, json=self.body)

    post = get


def _news_model(refresh=False):
    """The coordinator calls news_brief (its terminal tool)."""
    async def stream(messages, model, tools=None):
        if "news_brief" in _offered(tools):
            yield _call("news_brief", {"refresh": True} if refresh else {})
        else:
            yield {"token": "?"}
        yield {"usage": {}}
    return patch("agents.llm.stream", new=stream)


def _mirror_model():
    async def stream(messages, model, tools=None):
        if "update_magicmirror" in _offered(tools):
            yield _call("update_magicmirror", {})
        else:
            yield {"token": "?"}
        yield {"usage": {}}
    return patch("agents.llm.stream", new=stream)


def _silent_model():
    """A model that streams no token and no tool call."""
    async def stream(messages, model, tools=None):
        yield {"usage": {}}
    return patch("agents.llm.stream", new=stream)


# ── Finding 1: news_brief with tool_service down ──────────────────────────────

def test_news_with_tool_service_down_on_voice_is_friendly(app):
    with _route("direct"), _news_model(), patch("tools.httpx.AsyncClient", new=DeadToolService()):
        out = ask(app, VOICE, "what's the news today")
    assert out == "That didn't work — I couldn't get through to the news service."
    assert_speakable(out)


def test_news_with_tool_service_down_elsewhere_is_debug_unchanged(app):
    with _route("direct"), _news_model(), patch("tools.httpx.AsyncClient", new=DeadToolService()):
        out = ask(app, "/api/chat", "what's the news today")
    assert out == "[Tool news_brief error: All connection attempts failed]"


def test_news_unavailable_drops_the_detail_only_on_voice(app):
    """tool_service answered the brief with an error: its detail is spoken
    on no speaker (M2 e2e F16)."""
    stub = StubToolService(503, {"detail": "upstream returned HTTP 503 at http://10.0.0.5:8080/x"})
    with _route("direct"), _news_model(), patch("tools.httpx.AsyncClient", new=stub):
        voice = ask(app, VOICE, "what's the news today")
        api = ask(app, "/api/chat", "what's the news today")
    assert voice == errors.FRIENDLY_NEWS_UNAVAILABLE
    assert_speakable(voice)
    assert api == ("The news brief isn't available right now — upstream returned HTTP 503 at "
                   "http://10.0.0.5:8080/x. Ask me to research the news live if you need it sooner.")


def test_news_refresh_failure_keeps_the_brief_and_drops_the_cause_only_on_voice(app):
    stub = StubToolService(routes={
        "/news/refresh": (500, {"detail": "ReadTimeout at http://10.0.0.5:8080/feeds"}),
        "/news/brief": (200, {"edition": "evening", "generated_at_local": "6:00 PM",
                              "brief": "Nothing happened."}),
    })
    with _route("direct"), _news_model(refresh=True), patch("tools.httpx.AsyncClient", new=stub):
        voice = ask(app, VOICE, "update the news")
        api = ask(app, "/api/chat", "update the news")
    assert voice == ("I couldn't fetch fresh news, so here's the latest brief I have.\n\n"
                     "Evening news brief, generated 6:00 PM:\n\nNothing happened.")
    assert api == ("I couldn't fetch fresh news (ReadTimeout at http://10.0.0.5:8080/feeds) — "
                   "here's the latest brief I have.\n\nEvening news brief, generated 6:00 PM:"
                   "\n\nNothing happened.")


def test_successful_news_brief_is_spoken_unchanged(app):
    stub = StubToolService(200, {"edition": "evening", "generated_at_local": "6:00 PM",
                                 "brief": "Nothing happened."})
    with _route("direct"), _news_model(), patch("tools.httpx.AsyncClient", new=stub):
        voice = ask(app, VOICE, "what's the news today")
        api = ask(app, "/api/chat", "what's the news today")
    assert api == "Evening news brief, generated 6:00 PM:\n\nNothing happened."
    assert voice == "Evening news brief, generated 6:00 PM:\n\nNothing happened."


# ── Finding 2: a mirror update that fails its preflight ───────────────────────

_PREFLIGHT = "Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm"


def test_mirror_preflight_failure_on_voice_is_not_spoken(app, caplog):
    caplog.set_level(logging.INFO, logger="kronk.events")
    stub = StubToolService(502, {"detail": _PREFLIGHT})
    with _route("home"), _mirror_model(), patch("tools.httpx.AsyncClient", new=stub):
        out = ask(app, VOICE, "update the magic mirror")
    assert any("/magicmirror/update" in c for c in stub.calls)    # the update ran
    assert out == "I couldn't start the magic mirror update. The details are in the logs."
    assert_speakable(out)
    # "The details are in the logs" is true: the debug text is logged.
    assert "failure_spoken_friendly" in caplog.text and "/keys/kronk-mm-update" in caplog.text


def test_mirror_preflight_failure_elsewhere_is_debug_unchanged(app):
    stub = StubToolService(502, {"detail": _PREFLIGHT})
    with _route("home"), _mirror_model(), patch("tools.httpx.AsyncClient", new=stub):
        out = ask(app, "/api/chat", "update the magic mirror")
    assert out == f"I couldn't update the magic mirror. {_PREFLIGHT}"


@pytest.mark.parametrize("path", [VOICE, "/api/chat"])
def test_mirror_refusal_is_spoken_unchanged(app, path):
    """The M4b fixed sentence is a refusal, not a failure."""
    stub = StubToolService(502, {"detail": _PREFLIGHT})
    with _route("home"), _mirror_model(), patch("tools.httpx.AsyncClient", new=stub):
        out = ask(app, path, "update the mirror")
    assert out.strip() == tools.MM_UPDATE_REFUSAL
    assert stub.calls == []


# ── Finding 3: a model that returns nothing ───────────────────────────────────

def test_empty_model_reply_on_voice_is_friendly(app):
    with _route("direct"), _silent_model():
        out = ask(app, VOICE, "hello")
    assert out == "Sorry — my language model didn't come up with an answer. Try asking again."
    assert_speakable(out)


@pytest.mark.parametrize("path", ["/api/chat", "/message", "/v1/chat/completions"])
def test_empty_model_reply_elsewhere_is_debug_unchanged(app, path):
    with _route("direct"), _silent_model():
        out = ask(app, path, "hello")
    assert out == "[coordinator agent returned no response]"


@pytest.mark.parametrize("path,friendly", [(VOICE, True), ("/api/chat", False)])
def test_delegated_empty_reply_reaches_the_coordinator_as_today(app, path, friendly):
    """A delegated specialist's empty reply is model-facing: the coordinator
    sees the same marker on every mount (the friendly rule rides on it on
    the speech mount, like any failed tool result)."""
    seen = {}

    async def stream(messages, model, tools=None):
        names = _offered(tools)
        if "play_music" in names:                 # home, delegated: says nothing
            yield {"usage": {}}
            return
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            yield _call("ask_home", {"query": "turn on the lights"})
        else:
            seen["result"] = tool_msgs[-1]["content"]
            yield {"token": "The home controls didn't answer."}
        yield {"usage": {}}

    with _route("direct"), patch("agents.llm.stream", new=stream):
        out = ask(app, path, "turn on the lights")
    assert seen["result"].startswith("[home agent returned no response]")
    assert (errors.FRIENDLY_TOOL_PHRASING in seen["result"]) is friendly
    assert out.startswith("The home controls didn't answer")


# ── Observation 4: an argument refusal is not "couldn't get through" ──────────

def test_terminal_tool_argument_refusal_says_what_failed():
    raw = "[play_music error: query is required]"
    friendly = agents._terminal_speech(raw, errors.FRIENDLY, tool="play_music")
    assert friendly == ("That didn't work — I didn't get enough detail to ask the music "
                        "service. Try saying it again.")
    assert_speakable(friendly)
    assert agents._terminal_speech(raw, errors.DEBUG, tool="play_music") == \
        "play_music error: query is required"


# ── Observation 5: failed-result shapes ───────────────────────────────────────

@pytest.mark.parametrize("result", [
    "[Hot tub status unknown: no data]",
    "[Hot tub status unavailable]",
    "[Solar energy — today] no figure available: inverter offline. Tell the user plainly.",
    "[Unknown tool: frobnicate]",
    "[play_music error: query is required]",
    "[home agent returned no response]",
    "[no such specialist: plumbing]",
])
def test_more_failed_result_shapes_are_recognised(result):
    assert errors.is_failed_tool_result(result)


@pytest.mark.parametrize("result", [
    "[Hot tub ONLINE]\nTemperature: 101°F",
    "[Solar energy — TODAY] 12.5 kWh produced from a to b. Give the user the kWh.",
    "[Shopping list is empty]",
])
def test_more_successful_results_are_not_failures(result):
    assert not errors.is_failed_tool_result(result)


# ── Observation 6, survivor M12: a successful tool result is untouched ───────

@pytest.mark.parametrize("path", [VOICE, "/api/chat"])
def test_successful_tool_result_reaches_the_model_unchanged(app, path):
    seen = {}

    async def stream(messages, model, tools=None):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            yield _call("get_weather", {})
        else:
            seen["result"] = tool_msgs[-1]["content"]
            yield {"token": "Sunny."}
        yield {"usage": {}}

    stub = StubToolService(200, {"location": "Laurel, MD", "summary": "Sunny, 61°F"})
    with _route("home"), patch("agents.llm.stream", new=stream), \
         patch("tools.httpx.AsyncClient", new=stub):
        out = ask(app, path, "what's the weather")
    assert seen["result"] == "[Weather for Laurel, MD]\nSunny, 61°F"
    assert out.startswith("Sunny")


# ── Observation 6, survivor M03: the signal is reset after the run ────────────

@pytest.mark.asyncio
async def test_voice_signal_and_turn_record_are_reset_after_the_run():
    """Driven in this test's own context (not through HTTP, where every
    request has a fresh context), so a missing reset shows."""
    import orchestrator.main as orch

    async def classify(text, history):
        return "direct"

    async def stream(messages, model, tools=None):
        yield {"token": "ok"}
        yield {"usage": {}}

    with patch("orchestrator.main.routing.classify", new=classify), \
         patch("agents.llm.stream", new=stream), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("orchestrator.main.telemetry.end_pipeline", new=MagicMock()):
        assert origin.voice.get() is False
        events = [ev async for ev in orch._kronk_pipeline_tokens(
            "hi", "m", [], None, "hi", True)]
        assert origin.voice.get() is False
        assert errors._turn.get() is None
        assert events[-1] == {"type": "outcome", "failures": []}
        [ev async for ev in orch._run_pipeline(
            "hi", [], said="hi", voice=True, transport="shim", pipeline_name="p")]
        assert origin.voice.get() is False


# ── The enumeration ──────────────────────────────────────────────────────────

def _token_texts(path):
    """Every `{"type": "token", "text": X}` dict literal in a module."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
            if "type" in keys and "text" in keys:
                t = node.values[keys.index("type")]
                if isinstance(t, ast.Constant) and t.value == "token":
                    yield node.lineno, node.values[keys.index("text")]


# Literal user text that is not a failure.
_LITERAL_TOKENS_ALLOWED = {"Done — fresh start."}


@pytest.mark.parametrize("module", ["main.py", "agents.py"])
def test_no_literal_text_reaches_the_user_without_the_error_style(module):
    """User-visible text the pipeline writes itself is either a variable
    (model tokens, rendered failures) or a call into errors.*. A new
    bracketed marker or failure string yielded as a token fails here
    until it goes through errors.failure_text."""
    bad = []
    for lineno, expr in _token_texts(ORCH / module):
        if isinstance(expr, ast.Constant) and expr.value in _LITERAL_TOKENS_ALLOWED:
            continue
        if isinstance(expr, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            bad.append((lineno, ast.unparse(expr)))
        elif isinstance(expr, ast.Call):
            name = ast.unparse(expr.func)
            if not (name.startswith("errors.") or name in {"_empty_reply"}):
                bad.append((lineno, ast.unparse(expr)))
    assert bad == [], f"{module}: token text not through errors.failure_text: {bad}"


def _terminal_tools():
    names = set()
    for a in list(agents.AGENTS.values()) + [agents.COORDINATOR]:
        names |= set(a.terminal_tools)
    return sorted(names)


def _valid_args(tool):
    spec = next(d["function"]["parameters"] for d in tools.TOOL_DEFINITIONS
                if d["function"]["name"] == tool)
    args = {}
    for p in spec.get("required", []):
        prop = spec["properties"][p]
        args[p] = prop["enum"][0] if "enum" in prop else "jazz"
    return args


# Criterion 8: the music failures with their own sentence carry
# tool_service's (human) detail in both styles.
_OWN_SENTENCE = re.compile(r"^\[Could not (?:play music|control music|rate music"
                           r"|save music taste|read the player): ")


@pytest.mark.parametrize("tool", _terminal_tools())
@pytest.mark.parametrize("service", ["down", "http_error", "bad_args"])
def test_every_terminal_tool_failure_is_speakable_on_voice(tool, service):
    """Every terminal tool, every way it can fail: what the speech mount
    would speak carries none of the code's debug shapes. A new terminal
    tool, or a new failure branch that puts detail in its sentence, fails
    here until its failure goes through errors.failure_text."""
    client = (DeadToolService() if service == "down"
              else StubToolService(502, {"detail": POISON}))
    args = {} if service == "bad_args" else _valid_args(tool)
    if tool == "news_brief" and service == "http_error":
        args = {"refresh": True}
    said = origin.said.set("update the magic mirror")   # lets the mirror update run
    try:
        with patch("tools.httpx.AsyncClient", new=client):
            result = asyncio.run(tools.execute(tool, args))
    finally:
        origin.said.reset(said)
    spoken = agents._terminal_speech(result, errors.FRIENDLY, tool=tool)
    if _OWN_SENTENCE.match(result):
        assert spoken == agents._terminal_speech(result, errors.DEBUG, tool=tool)
        return
    assert errors.debug_shape(spoken) is None, (result, spoken)
    assert len(spoken) <= 200, spoken


# ── The backstop ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,shape", [
    ("[Tool get_weather error: boom]", "[Tool get_weather error"),
    ("[home agent error: LiteLLM returned 500]", "[home agent error"),
    ("[coordinator agent returned no response].", "[coordinator agent returned no response"),
    ("I got [Errno -3] Temporary failure in name resolution.", "Errno"),
    ("Traceback (most recent call last): File x", "Traceback (most recent call last)"),
    ("It raised ConnectError.", "ConnectError"),
    ("Try http://tool_service-dev:8103/news.", "http://tool_service-dev:8103"),
    ("The key at /keys/kronk-mm-update is missing.", "/keys/"),
    ("Check /data/hottub/state.json.", "/data/"),
])
def test_debug_shapes_are_recognised(text, shape):
    assert errors.debug_shape(text) == shape


@pytest.mark.parametrize("text", [
    "Paris.",
    "It's 5:30 PM and 61 degrees.",
    "Now playing Glory Box by Portishead on the Office speaker.",
    "The weather service isn't answering right now.",
    "See example.com for details.",
])
def test_plain_sentences_have_no_debug_shape(text):
    assert errors.debug_shape(text) is None


def test_backstop_replaces_a_failed_turns_debug_text_and_records_it(caplog):
    caplog.set_level(logging.INFO)
    span = MagicMock()
    with patch("errors.telemetry.current", return_value=span):
        out = errors.speech_backstop("I got [Errno -3] Temporary failure.",
                                     ["get_weather: [Tool get_weather error: …]"])
    assert out == "Sorry — something went wrong on my end. I've noted the details."
    assert "speech_backstop" in caplog.text and "[Errno -3]" in caplog.text
    attrs = span.set.call_args.kwargs
    assert attrs["kronk.speech_backstop"] is True
    assert attrs["kronk.speech_backstop.original"] == "I got [Errno -3] Temporary failure."


@pytest.mark.parametrize("text", [
    "[Tool x error: [Errno -3]] at http://h:8080 /keys/k Traceback (most recent call last)",
    "Paris.",
])
def test_backstop_never_touches_a_turn_without_a_failure(text):
    assert errors.speech_backstop(text, []) == text


def test_backstop_is_off_when_the_operator_asks_for_debug_on_voice(monkeypatch):
    monkeypatch.setenv("ERROR_STYLE_VOICE", "debug")
    text = "[Tool get_weather error: [Errno -3]]"
    assert errors.speech_backstop(text, ["x"]) == text


def _echo_model(echo):
    """get_weather fails; the model parrots the failure into its answer."""
    async def stream(messages, model, tools=None):
        if not [m for m in messages if m.get("role") == "tool"]:
            yield _call("get_weather", {})
        else:
            yield {"token": echo}
        yield {"usage": {}}
    return patch("agents.llm.stream", new=stream)


def test_model_echoing_debug_text_on_a_failed_voice_turn_is_replaced(app):
    echo = "The lookup failed with [Errno -3] Temporary failure in name resolution."
    with _route("home"), _echo_model(echo), patch("tools.httpx.AsyncClient", new=DeadToolService()):
        voice = ask(app, VOICE, "what's the weather")
        api = ask(app, "/api/chat", "what's the weather")
    assert voice == "Sorry — something went wrong on my end. I've noted the details."
    assert api == echo                                        # debug mounts untouched


def test_model_echoing_a_failure_in_plain_words_is_kept(app):
    plain = "The weather service isn't answering right now."
    with _route("home"), _echo_model(plain), patch("tools.httpx.AsyncClient", new=DeadToolService()):
        assert ask(app, VOICE, "what's the weather") == plain


def test_backstop_never_touches_a_successful_voice_answer(app):
    """A successful turn whose answer happens to carry debug-looking text
    is spoken as the model wrote it (after the speech render)."""
    answer = "Kronk's tool service listens on http://tool_service:8103 and reads /data/state.json."
    seen_failures = []
    real = errors.speech_backstop

    def spy(text, failures):
        seen_failures.append(list(failures))
        return real(text, failures)

    async def stream(messages, model, tools=None):
        yield {"token": answer}
        yield {"usage": {}}

    with _route("direct"), patch("agents.llm.stream", new=stream), \
         patch("orchestrator.main.errors.speech_backstop", new=spy):
        out = ask(app, VOICE, "where does the tool service listen")
    assert seen_failures == [[]]
    assert out == answer
