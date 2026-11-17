"""Tests for the ERROR_STYLE toggle (orchestrator/errors.py).

The toggle governs rendering only — capture (logs, traces) always keeps
full detail. This file pins the style resolution (keyed on origin.voice,
the speech-mount signal, since sprint M2), the friendly renderings, the
debug renderings byte for byte, and — end to end through the real app —
each failure kind on the speech mount (friendly) and on the other mounts
(debug, unchanged). Friendliness never reintroduces the invented-answer
problem. Criteria: docs/sprint/M2-criteria.md (6-11, 13).
"""
import json
import logging
import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

os.environ.setdefault("LLM_SERVICE_URL",  "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

import unittest.mock as mock_module  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import agents  # noqa: E402
import errors  # noqa: E402
import origin  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _no_style_env(monkeypatch):
    """Every test starts from the code defaults; a test that wants a
    setting sets it."""
    monkeypatch.delenv("ERROR_STYLE", raising=False)
    monkeypatch.delenv("ERROR_STYLE_VOICE", raising=False)


# A friendly sentence must be fit for a speaker (criterion 6).
_TECHNICAL = re.compile(
    r"\w+(?:Error|Exception)\b|https?:|://|:\d{2,5}\b|\b[1-5]\d\d\b|\brid\b"
    r"|[\[\]{}]|Traceback|litellm|gemma|mistral|devstral|qwen|talkie",
    re.IGNORECASE,
)


def assert_speakable(text):
    assert text and not _TECHNICAL.search(text), text
    assert len(text) <= 120, text


# ── style_for: env resolution ─────────────────────────────────────────────────

def test_defaults_voice_friendly_everything_else_debug():
    assert errors.style_for(voice=True) == errors.FRIENDLY
    assert errors.style_for(voice=False) == errors.DEBUG


def test_error_style_covers_only_the_non_voice_mounts(monkeypatch):
    monkeypatch.setenv("ERROR_STYLE", "friendly")
    assert errors.style_for(voice=False) == errors.FRIENDLY
    assert errors.style_for(voice=True) == errors.FRIENDLY
    monkeypatch.setenv("ERROR_STYLE", "debug")
    assert errors.style_for(voice=False) == errors.DEBUG
    assert errors.style_for(voice=True) == errors.FRIENDLY   # voice has its own default


def test_voice_setting_switches_the_speech_mount_back_to_debug(monkeypatch):
    """The one documented setting (criterion 11)."""
    monkeypatch.setenv("ERROR_STYLE_VOICE", "debug")
    assert errors.style_for(voice=True) == errors.DEBUG
    monkeypatch.setenv("ERROR_STYLE_VOICE", "friendly")
    assert errors.style_for(voice=True) == errors.FRIENDLY
    assert errors.style_for(voice=False) == errors.DEBUG     # never reaches /api/chat or /v1


@pytest.mark.parametrize("var,voice", [("ERROR_STYLE", False), ("ERROR_STYLE_VOICE", True)])
def test_invalid_value_falls_back_to_debug(monkeypatch, var, voice):
    """A typo must not silently hide detail forever."""
    monkeypatch.setenv(var, "freindly")
    assert errors.style_for(voice=voice) == errors.DEBUG


def test_current_style_reads_the_request_signal():
    assert errors.current_style() == errors.DEBUG
    token = origin.voice.set(True)
    try:
        assert errors.current_style() == errors.FRIENDLY
    finally:
        origin.voice.reset(token)


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
    assert_speakable(out)


def test_friendly_renderings_say_what_kind_of_thing_failed():
    """Tenet 7 / criterion 7: specific where the code knows."""
    assert errors.render("routing", "x", "r", errors.FRIENDLY) == \
        "Sorry — I couldn't work out where to send that request. I've noted the details."
    assert errors.render("llm", "[coordinator agent error: All connection attempts failed]",
                         "r", errors.FRIENDLY) == \
        "Sorry — I couldn't reach my language model. Try again in a moment."
    assert errors.render("llm", "[coordinator agent error: LiteLLM returned 503: down]",
                         "r", errors.FRIENDLY) == \
        "Sorry — I couldn't reach my language model. Try again in a moment."
    assert errors.render("llm", "[coordinator agent error: LiteLLM returned 400: bad model]",
                         "r", errors.FRIENDLY) == \
        "Sorry — my language model couldn't handle that request."
    # Unknown cause: the one generic sentence.
    assert errors.render("pipeline", "KeyError: 'x'", "r", errors.FRIENDLY) == \
        "Sorry — something went wrong on my end. I've noted the details."


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


# ── the friendly phrasing rule rides on failed results, not the prompt ───────

@pytest.mark.parametrize("result", [
    "[Tool get_weather error: All connection attempts failed]",
    "[home agent error: LiteLLM returned 500: boom]",
    "[Weather lookup for Laurel, MD failed (HTTP 503): upstream down]",
    "[Could not fetch https://x: tool service returned 500]",
    "[Could not play music: The speaker may be powered off]",
    "[Diagram generation failed: 500 boom]",
])
def test_failed_result_shapes_are_recognised(result):
    assert errors.is_failed_tool_result(result)


@pytest.mark.parametrize("result", [
    "[Weather for Laurel, MD]\nSunny, 61°F",
    "[Shopping list: eggs, milk]",
    "[Music playing: Jazz on the Kitchen speaker]",
    "[get_weather was already called with these exact arguments this turn; use the earlier result]",
    "plain text",
    None,
])
def test_successful_results_are_not_failures(result):
    assert not errors.is_failed_tool_result(result)


@pytest.mark.asyncio
async def test_system_prompt_is_the_same_in_both_styles():
    """Successful requests see the same prompt on every mount: the friendly
    style adds nothing to the system prompt."""
    captured = []

    async def fake_stream(messages, model, tools=None):
        captured.append(messages[0]["content"])
        yield {"token": "ok"}
        yield {"usage": {}}

    with patch("agents.llm.stream", new=fake_stream), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)):
        agent = agents.AGENTS["assistant"]
        [ev async for ev in agents.run_stream(agent, "hi", [], error_style=errors.FRIENDLY)]
        [ev async for ev in agents.run_stream(agent, "hi", [], error_style=errors.DEBUG)]

    assert captured[0] == captured[1]
    assert errors.FRIENDLY_TOOL_PHRASING not in captured[0]


# ── terminal tools ────────────────────────────────────────────────────────────

def test_terminal_speech_friendly_drops_the_cause_and_names_the_service():
    raw = "[Tool play_music error: ReadTimeout(ReadTimeout('timed out'))]"
    friendly = agents._terminal_speech(raw, errors.FRIENDLY, tool="play_music")
    assert friendly == "That didn't work — I couldn't get through to the music service."
    assert_speakable(friendly)
    assert agents._terminal_speech(raw, errors.FRIENDLY, tool="update_magicmirror") == \
        "That didn't work — I couldn't get through to the magic mirror updater."
    assert agents._terminal_speech(raw, errors.FRIENDLY) == \
        "That didn't work — I couldn't get through to the service that handles that."
    # Debug: unchanged.
    assert agents._terminal_speech(raw, errors.DEBUG, tool="play_music") == \
        "That didn't work — the tool failed with ReadTimeout(ReadTimeout('timed out'))."
    # Known-good shapes are style-independent (already human sentences).
    assert agents._terminal_speech(
        "[Music playing: X on the kitchen speaker]", errors.FRIENDLY
    ) == "Now playing X on the kitchen speaker."


@pytest.mark.parametrize("line,spoken", [
    ("[Could not play music: The speaker may be powered off.]\nPlayback FAILED — tell the user.",
     "I couldn't play that. The speaker may be powered off."),
    ("[Could not control music: Nothing is playing on the Office speaker.]",
     "I couldn't do that. Nothing is playing on the Office speaker."),
    ("[Could not rate music: Nothing is playing.]", "I couldn't rate that. Nothing is playing."),
    ("[Could not save music taste: The taste file is read-only.]",
     "I couldn't save that. The taste file is read-only."),
    ("[Could not read the player: The Office speaker is offline.]",
     "I couldn't tell. The Office speaker is offline."),
])
@pytest.mark.parametrize("style", [errors.DEBUG, errors.FRIENDLY])
def test_music_failure_sentences_are_unchanged_in_both_styles(line, spoken, style):
    """Criterion 8."""
    assert agents._terminal_speech(line, style, tool="play_music") == spoken


# ── end to end: each failure kind, per mount ──────────────────────────────────

_open_orig = open


def _fake_open(path, *a, **kw):
    if "/app/static/" in str(path):
        import io
        return io.StringIO("<html></html>")
    return _open_orig(path, *a, **kw)


@pytest.fixture
def app(tmp_path):
    import orchestrator.main as orch
    end = MagicMock()
    with mock_module.patch("builtins.open", side_effect=_fake_open), \
         mock_module.patch.object(orch.metrics, "METRICS_DB", tmp_path / "metrics.db"), \
         mock_module.patch.object(orch.sessions, "SESSIONS_DB", tmp_path / "sessions.db"), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("orchestrator.main.telemetry.end_pipeline", new=end):
        orch.sessions.clear(orch.WEBUI_SESSION)
        with TestClient(orch.app, raise_server_exceptions=True) as c:
            c.end_pipeline = end
            yield c
        orch.sessions.clear(orch.WEBUI_SESSION)


def ask(client, path, text="what's going on"):
    if path == "/message":
        resp = client.post(path, json={"text": text})
        out = ""
        for line in resp.text.splitlines():
            body = line[5:].strip() if line.startswith("data:") else ""
            if body and body != "[DONE]":
                out += json.loads(body).get("token", "")
        return out
    resp = client.post(path, json={"model": "kronk", "stream": False,
                                   "messages": [{"role": "user", "content": text}]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if path == "/v1/chat/completions":
        return body["choices"][0]["message"]["content"]
    return body["message"]["content"]


def trace_status(client):
    _, kwargs = client.end_pipeline.call_args
    assert kwargs.get("level") == "ERROR"
    return kwargs.get("status_message") or ""


OTHER_MOUNTS = ["/message", "/api/chat", "/v1/chat/completions"]
RID = r"\[rid [0-9a-f]{8}\]"


def _route(name):
    async def classify(text, history):
        return name
    return patch("orchestrator.main.routing.classify", new=classify)


def _offered(tools_arg):
    return {t["function"]["name"] for t in (tools_arg or [])}


def _call(name, args):
    return {"tool_calls": [{"id": "c1", "function": {"name": name, "arguments": args}}]}


class DeadToolService:
    """httpx.AsyncClient stand-in for a stopped tool_service."""

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, *a, **kw):
        raise httpx.ConnectError("All connection attempts failed")

    post = get


# 1. routing failure

def _routing_boom():
    async def classify(text, history):
        raise RuntimeError("router exploded at http://litellm:4000 (HTTP 500)")
    return patch("orchestrator.main.routing.classify", new=classify)


def test_routing_failure_on_voice_is_friendly_and_logged(app, caplog):
    caplog.set_level(logging.ERROR)
    with _routing_boom():
        out = ask(app, "/voice/api/chat")
    assert out == "Sorry — I couldn't work out where to send that request. I've noted the details."
    assert_speakable(out)
    assert "router exploded at http://litellm:4000 (HTTP 500)" in trace_status(app)
    assert "router exploded at http://litellm:4000" in caplog.text


@pytest.mark.parametrize("path", OTHER_MOUNTS)
def test_routing_failure_elsewhere_is_debug_unchanged(app, path):
    with _routing_boom():
        out = ask(app, path)
    assert re.fullmatch(
        r"Error: routing failed: router exploded at http://litellm:4000 \(HTTP 500\) " + RID, out
    ), out


# 2. pipeline crash

def _crash():
    async def exploding_run_stream(*args, **kwargs):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover
    return patch("orchestrator.main.agents.run_stream", new=exploding_run_stream)


def test_pipeline_crash_on_voice_is_friendly_and_logged(app, caplog):
    caplog.set_level(logging.ERROR)
    with _route("direct"), _crash():
        out = ask(app, "/voice/api/chat")
    assert out == "Sorry — something went wrong on my end. I've noted the details."
    assert "RuntimeError: kaboom" in trace_status(app)
    assert "kaboom" in caplog.text


@pytest.mark.parametrize("path", OTHER_MOUNTS)
def test_pipeline_crash_elsewhere_is_debug_unchanged(app, path):
    with _route("direct"), _crash():
        out = ask(app, path)
    assert re.fullmatch(r"Error: the pipeline failed unexpectedly \(RuntimeError: kaboom\) " + RID,
                        out), out


def test_pipeline_crash_renders_friendly_everywhere_when_enabled(app, monkeypatch):
    """ERROR_STYLE=friendly still turns friendly on for the other mounts."""
    monkeypatch.setenv("ERROR_STYLE", "friendly")
    with _route("direct"), _crash():
        out = ask(app, "/message")
    assert out == "Sorry — something went wrong on my end. I've noted the details."
    assert "kaboom" in trace_status(app)


# 3. unreachable model server (and a model server that refuses)

def _llm_raises(exc):
    async def stream(messages, model, tools=None):
        raise exc
        yield  # pragma: no cover
    return patch("agents.llm.stream", new=stream)


def test_unreachable_model_on_voice_is_friendly(app):
    with _route("direct"), _llm_raises(httpx.ConnectError("All connection attempts failed")):
        out = ask(app, "/voice/api/chat")
    assert out == "Sorry — I couldn't reach my language model. Try again in a moment."
    assert "All connection attempts failed" in trace_status(app)


def test_refusing_model_on_voice_is_friendly(app):
    exc = RuntimeError("LiteLLM returned 400: Invalid model name passed in model=gemma-4-e4b")
    with _route("direct"), _llm_raises(exc):
        out = ask(app, "/voice/api/chat")
    assert out == "Sorry — my language model couldn't handle that request."
    assert "Invalid model name" in trace_status(app)


@pytest.mark.parametrize("path", OTHER_MOUNTS)
def test_unreachable_model_elsewhere_is_debug_unchanged(app, path):
    with _route("direct"), _llm_raises(httpx.ConnectError("All connection attempts failed")):
        out = ask(app, path)
    assert out == "[coordinator agent error: All connection attempts failed]"


# 4. tool_service outage

def _music_model():
    """home → play_music; coordinator → ask_home (the delegated path)."""
    async def stream(messages, model, tools=None):
        names = _offered(tools)
        if "play_music" in names:
            yield _call("play_music", {"query": "jazz"})
        elif "ask_home" in names:
            yield _call("ask_home", {"query": "play jazz"})
        else:
            yield {"token": "?"}
        yield {"usage": {}}
    return patch("agents.llm.stream", new=stream)


@pytest.mark.parametrize("route", ["home", "direct"])   # pinned, and delegated via ask_home
def test_tool_service_outage_on_voice_is_friendly(app, route):
    with _route(route), _music_model(), patch("tools.httpx.AsyncClient", new=DeadToolService()):
        out = ask(app, "/voice/api/chat", "play some jazz")
    assert out == "That didn't work — I couldn't get through to the music service."
    assert_speakable(out)


@pytest.mark.parametrize("route", ["home", "direct"])
def test_tool_service_outage_elsewhere_is_debug_unchanged(app, route):
    with _route(route), _music_model(), patch("tools.httpx.AsyncClient", new=DeadToolService()):
        out = ask(app, "/api/chat", "play some jazz")
    assert out == "That didn't work — the tool failed with All connection attempts failed."


@pytest.mark.parametrize("path,friendly", [("/voice/api/chat", True), ("/api/chat", False)])
def test_failed_non_terminal_tool_carries_the_rule_only_on_voice(app, path, friendly):
    """A model-phrased failure (get_weather with tool_service down): the
    result keeps full detail on every mount; only on the speech mount is
    the phrasing rule appended to it."""
    seen = {}

    async def stream(messages, model, tools=None):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            yield _call("get_weather", {})
        else:
            seen["result"] = tool_msgs[-1]["content"]
            yield {"token": "The weather service isn't answering right now."}
        yield {"usage": {}}

    with _route("home"), patch("agents.llm.stream", new=stream), \
         patch("tools.httpx.AsyncClient", new=DeadToolService()):
        out = ask(app, path, "what's the weather")
    assert seen["result"].startswith("[Tool get_weather error: All connection attempts failed]")
    assert (errors.FRIENDLY_TOOL_PHRASING in seen["result"]) is friendly
    assert out.startswith("The weather service isn't answering right now")


# 5. specialist agent failure

@pytest.mark.parametrize("path,friendly", [("/voice/api/chat", True), ("/api/chat", False)])
def test_specialist_failure_reaches_the_coordinator_in_the_mounts_style(app, path, friendly):
    seen = {}

    async def stream(messages, model, tools=None):
        names = _offered(tools)
        if "play_music" in names:                     # the pinned home specialist
            raise RuntimeError("LiteLLM returned 500: home model crashed")
        seen["system"] = messages[0]["content"]
        yield {"token": "The home controls aren't working right now."}
        yield {"usage": {}}

    with _route("home"), patch("agents.llm.stream", new=stream):
        out = ask(app, path, "turn on the lights")
    assert "home model crashed" in seen["system"]                 # full detail, model-side
    assert ("never mention HTTP status codes" in seen["system"]) is friendly
    assert ("report the failure and its cause" in seen["system"]) is not friendly
    assert out.startswith("The home controls aren't working right now")
    assert "home specialist" in trace_status(app) and "home model crashed" in trace_status(app)
