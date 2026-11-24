"""Talkie on the CPU (M24): the reply-length cap on the speech mount, and
what "ask talkie ..." produces when Talkie's server cannot be reached.

The cap is config/voice.yaml max_reply_tokens (docs/sprint/M24-build.md
has the measurements behind its value). It is sent as max_tokens on the
speech mount only; a reply that stops at it loses its unfinished last
sentence before anything is spoken.

Unit tests only: the model is faked (llm.stream, or LiteLLM itself behind
an httpx MockTransport), and the no_network fixture fails any real
connection.
"""
import json
import logging

import httpx
import pytest
import yaml
from unittest.mock import patch

from tests.test_error_style import (  # noqa: F401  (fixtures used by name)
    OTHER_MOUNTS, _no_style_env, _route, app, ask, assert_speakable, trace_status,
)

import config_loader  # noqa: E402
import errors  # noqa: E402
import llm  # noqa: E402
import speech_stream  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

VOICE = "/voice/api/chat"
ALL_MOUNTS = [VOICE, *OTHER_MOUNTS]
ASK = "ask talkie what he makes of the wireless"
WHOLE = "The canal is fifty miles long. It was opened in 1914."
CUT = WHOLE + " The total cost am"


def _model(text=CUT, finish="length", seen=None):
    """A fake llm.stream: Talkie streams `text` and stops for `finish`;
    every call's model and max_tokens land in `seen`."""
    async def stream(messages, model, tools=None, **kw):
        if seen is not None:
            seen.append((model, kw.get("max_tokens", "not sent")))
        for word in text.split(" "):
            yield {"token": word + " "}
        yield {"finish_reason": finish}
        yield {"usage": {"prompt_tokens": 390, "completion_tokens": 120}}
    return patch("agents.llm.stream", new=stream)


# ── the cut ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,tail", [
    (CUT, "The total cost am"),
    (WHOLE, ""),                                     # ends on a sentence end
    (WHOLE + " ", ""),
    ('He said "It is so."', ""),                     # closing quote after the full stop
    ("The kings were William I, William II, Henry I, Stephen", ""),   # no complete sentence
    ("", ""),
    ("One. Two? Three! Four and fi", "Four and fi"),
    ("Dr. Watson was there. And Mr. Holm", "And Mr. Holm"),
    # An unclosed list is one unit: only its unfinished last line goes.
    ("The kings, in order:\n\n- William I\n- William II\n- Hen", "- Hen"),
    ("The kings, in order:\n\n1. William I\n2. William II\n3. Henry", "3. Henry"),
    # Fix round 1: what would be left must say something on its own.
    ("\n\nThe canal is long. It was op", "It was op"),        # leading blank lines
    ("Certainly:\n- William I\n- Wil", "- Wil"),            # lead-in, then list items
    ("Kings:\n\n| King | Year |\n|---|---|\n| William I | 1066 |\n| William II | 10", "| William II | 10"),
    # A reply that is one unclosed block, with nothing before it, is kept
    # whole as before fix round 1 (the round only ever keeps more).
    ("- William I\n- William II\n- Hen", ""),
    ("| King | Year |\n|---|---|\n| William I | 1066 |\n| William II | 10", ""),
])
def test_unfinished_tail(text, tail):
    assert speech_stream.unfinished_tail(text) == tail
    assert text.endswith(tail)


# The test report's texts (M24-e2e.md should-fix 2) and more of their kind:
# the only complete units before the tail are blank lines, a heading, a
# lead-in line or one word. Dropping the tail would leave the reply silent
# or a heading alone, so nothing is dropped.
NOTHING_TO_STAND_ON = [
    "\nThe kings were William I, William II, Henry I, Stephen, Henry II",
    "Certainly:\nThe kings were William I, William II",
    "Kings of England\n\nWilliam I, William II, Henry I, Steph",
    "\n\n\nThe kings were William I, William II",
    "# Kings of England\n\nThe kings were William I, William II",
    "## Kings\n\nCertainly:\n\nThe kings were William I, William II",
    "Certainly! The kings were William I, William II",
    "Here is the list:\n\n- William I, William II, Hen",       # a list with only its cut item
    "Kings:\n\n| King | Year |\n|---|---|\n| William I | 10",  # a table with only its cut row
    "  \n\t\nThe kings were William I, William II",
]


@pytest.mark.parametrize("text", NOTHING_TO_STAND_ON)
def test_nothing_is_dropped_when_what_is_left_says_nothing(text):
    assert speech_stream.unfinished_tail(text) == ""


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "VOICE_STREAM"])
@pytest.mark.parametrize("text", NOTHING_TO_STAND_ON[:4] + ["The kings were William I, William II"])
def test_capped_reply_is_never_spoken_as_nothing_or_a_heading(app, monkeypatch, caplog, text, stream):
    """Through the app on the speech mount, model faked, finish_reason
    "length": the test report's four texts. Before fix round 1 the first
    three were spoken as "", "Certainly:" and "Kings of England."."""
    caplog.set_level(logging.INFO, logger="kronk.events")
    monkeypatch.setenv("VOICE_STREAM", "on" if stream else "off")
    with _route("talkie"), _model(text=text):
        resp = app.post(VOICE, json={"model": "kronk", "stream": stream,
                                     "messages": [{"role": "user", "content": ASK}]})
    if stream:
        lines = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
        spoken = "".join(x["message"]["content"] for x in lines if not x.get("done"))
    else:
        spoken = resp.json()["message"]["content"]
    assert "William I, William II" in spoken
    ev = [json.loads(r.getMessage()) for r in caplog.records if '"reply_capped"' in r.getMessage()]
    assert ev and ev[0]["dropped_chars"] == 0


# ── the cap is sent on the speech mount only ─────────────────────────────────

def test_cap_is_sent_on_the_speech_mount(app):
    seen = []
    with _route("talkie"), _model(text=WHOLE, finish="stop", seen=seen):
        ask(app, VOICE, ASK)
    assert seen == [("talkie", 120)]


@pytest.mark.parametrize("path", OTHER_MOUNTS)
def test_typed_requests_are_never_capped(app, path):
    seen = []
    with _route("talkie"), _model(text=CUT, finish="length", seen=seen):
        out = ask(app, path, ASK)
    assert seen == [("talkie", "not sent")]
    assert out.strip() == CUT                        # nothing dropped either


@pytest.mark.parametrize("route", ["home", "direct"])
def test_agents_without_a_cap_are_not_capped_on_voice(app, route):
    seen = []
    with _route(route), _model(text=WHOLE, finish="stop", seen=seen):
        ask(app, VOICE, "what is the weather")
    assert seen and all(cap == "not sent" for _, cap in seen)


# ── a capped reply loses its unfinished last sentence ────────────────────────

def test_capped_reply_is_spoken_to_its_last_full_sentence(app, caplog):
    caplog.set_level(logging.INFO, logger="kronk.events")
    with _route("talkie"), _model():
        out = ask(app, VOICE, ASK)
    assert out == WHOLE
    ev = [json.loads(r.getMessage()) for r in caplog.records if '"reply_capped"' in r.getMessage()]
    assert ev and ev[0]["agent"] == "talkie" and ev[0]["max_tokens"] == 120
    assert ev[0]["dropped_chars"] == len("The total cost am ")


def test_capped_reply_streamed_sentence_by_sentence(app, monkeypatch):
    """VOICE_STREAM=on: the unfinished sentence is still held when the cut
    is known, so it is never sent."""
    monkeypatch.setenv("VOICE_STREAM", "on")
    with _route("talkie"), _model():
        resp = app.post(VOICE, json={"model": "kronk", "stream": True,
                                     "messages": [{"role": "user", "content": ASK}]})
    lines = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
    spoken = "".join(x["message"]["content"] for x in lines if not x.get("done"))
    assert spoken == WHOLE
    assert "am" not in spoken.split()


def test_capped_list_keeps_its_complete_items(app):
    text = "The kings, in order:\n\n- William I\n- William II\n- Henry I\n- Hen"
    with _route("talkie"), _model(text=text):
        out = ask(app, VOICE, ASK)
    assert "William II" in out and "Henry I" in out and not out.rstrip(" .").endswith("Hen")


def test_reply_that_stopped_on_its_own_is_kept_whole(app):
    """finish_reason "stop": an odd ending is the model's, not the cap's."""
    with _route("talkie"), _model(text=CUT, finish="stop"):
        out = ask(app, VOICE, ASK)
    assert out.rstrip(" .") == CUT                   # to_speech closes the sentence


def test_capped_reply_with_no_full_sentence_is_kept(app):
    text = "The kings were William I, William II, Henry I, Stephen, Henry II"
    with _route("talkie"), _model(text=text):
        out = ask(app, VOICE, ASK)
    assert out.rstrip(" .") == text


# ── config ───────────────────────────────────────────────────────────────────

def test_shipped_cap_is_the_built_in_default():
    import os
    from pathlib import Path
    shipped = yaml.safe_load((Path(os.environ["KRONK_CONFIG_DIR"]) / "voice.yaml").read_text())
    assert shipped["max_reply_tokens"] == {"talkie": 120}
    assert config_loader.Voice().max_reply_tokens.talkie == 120
    assert config_loader.Voice().max_reply_tokens.for_agent("home") is None
    assert config_loader.Voice().max_reply_tokens.for_agent("coordinator") is None


@pytest.mark.parametrize("value", [
    {"talkie": 0}, {"talkie": 5000}, {"talkie": "120"}, {"home": 100},
])
def test_bad_caps_are_rejected(value):
    with pytest.raises(Exception):
        config_loader.Voice.model_validate({"max_reply_tokens": value})


def test_cap_can_be_switched_off():
    assert config_loader.Voice.model_validate(
        {"max_reply_tokens": {"talkie": None}}).max_reply_tokens.for_agent("talkie") is None


# ── llm.stream sends max_tokens and reports why the generation stopped ──────

def _sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def _fake_litellm(handler):
    """llm's httpx.AsyncClient, answering from `handler` (no socket)."""
    real = httpx.AsyncClient

    def client(*a, **kw):
        kw.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **kw)
    return patch("llm.httpx.AsyncClient", new=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [120, None])
async def test_llm_stream_sends_max_tokens_only_when_set(max_tokens):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return _sse({"choices": [{"delta": {"content": "Quite so."}, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "finish_reason": "length"}]},
                    {"choices": [], "usage": {"completion_tokens": 120}})

    with _fake_litellm(handler):
        out = [c async for c in llm._stream_body([{"role": "user", "content": "q"}], "talkie",
                                                 None, max_tokens=max_tokens)]
    assert ("max_tokens" in sent[0]) is (max_tokens is not None)
    if max_tokens:
        assert sent[0]["max_tokens"] == max_tokens
    assert {"token": "Quite so."} in out and {"finish_reason": "length"} in out



# Fix round 1 (M24-e2e.md should-fix 3, mutants M03 and M04): llm.stream
# itself, not _stream_body, must hand the cap on, in both lock modes; and
# end to end, the speech mount's Talkie call reaches LiteLLM with it.

def _litellm_recording(sent, finish="stop"):
    def handler(request):
        sent.append(json.loads(request.content))
        return _sse({"choices": [{"delta": {"content": "Quite so."}, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "finish_reason": finish}]},
                    {"choices": [], "usage": {"completion_tokens": 3}})
    return _fake_litellm(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize("lock_mode", ["server", "pipeline"])
@pytest.mark.parametrize("max_tokens", [120, None])
async def test_llm_stream_hands_the_cap_on_in_each_lock_mode(monkeypatch, lock_mode, max_tokens):
    monkeypatch.setenv("MODEL_LOCK", lock_mode)
    sent = []
    with _litellm_recording(sent, finish="length"):
        out = [c async for c in llm.stream([{"role": "user", "content": "q"}], "talkie",
                                           None, max_tokens=max_tokens)]
    assert len(sent) == 1
    assert sent[0].get("max_tokens", "not sent") == (max_tokens if max_tokens else "not sent")
    assert {"finish_reason": "length"} in out


@pytest.mark.parametrize("lock_mode", ["server", "pipeline"])
@pytest.mark.parametrize("path", ALL_MOUNTS)
def test_the_cap_reaches_litellm_on_the_speech_mount_only(app, monkeypatch, lock_mode, path):
    monkeypatch.setenv("MODEL_LOCK", lock_mode)
    sent = []
    with _route("talkie"), _litellm_recording(sent):
        ask(app, path, ASK)
    talkie = [b for b in sent if b["model"] == "talkie"]
    assert len(talkie) == 1
    assert talkie[0].get("max_tokens", "not sent") == (120 if path == VOICE else "not sent")

# ── criterion 7: Talkie's server cannot be reached ───────────────────────────
# LiteLLM answers 500 for a model whose server is down. The talkie agent
# fails, and its failure is the reply (agents.AgentConfig
# coordinator_fallback=False), rendered as sprint M2 renders an unreachable
# model: the specific debug text off the speech mount, one plain sentence on
# it. Before M24 the failure went to the coordinator, which on dev answered
# "ask talkie ..." itself 2 times in 10 (docs/sprint/M24-build.md).

TALKIE_DOWN = ("litellm.APIConnectionError: APIConnectionError: OpenAIException - "
               "Connection error. Received Model Group=talkie")
UNREACHABLE = "Sorry — I couldn't reach my language model. Try again in a moment."


def _litellm_with_talkie_down(seen):
    """A fake LiteLLM (no socket): every talkie call gets LiteLLM's 500; any
    other model call is recorded and answered."""
    def handler(request):
        body = json.loads(request.content)
        if body["model"] == "talkie":
            return httpx.Response(500, json={"error": {"message": TALKIE_DOWN}})
        seen.append(body["model"])
        return _sse({"choices": [{"delta": {"content": "London is a city."}, "finish_reason": "stop"}]},
                    {"choices": [], "usage": {"completion_tokens": 5}})
    return _fake_litellm(handler)


@pytest.mark.parametrize("path", OTHER_MOUNTS)
def test_talkie_down_on_chat_is_the_specific_failure(app, path):
    seen = []
    with _route("talkie"), _litellm_with_talkie_down(seen):
        out = ask(app, path, ASK)
    assert seen == []                                # nobody answers in Talkie's place
    assert out.strip() == f"[talkie agent error: LiteLLM returned 500: {TALKIE_DOWN}]"
    status = trace_status(app)
    assert "talkie specialist" in status and "Connection error" in status


def test_talkie_down_on_voice_is_a_plain_sentence(app):
    seen = []
    with _route("talkie"), _litellm_with_talkie_down(seen):
        out = ask(app, VOICE, ASK)
    assert seen == []
    assert out == UNREACHABLE
    assert_speakable(out)
    assert "Connection error" in trace_status(app)   # the detail is kept (tenet 7)


@pytest.mark.parametrize("path,expected", [
    (VOICE, UNREACHABLE),
    ("/api/chat", "[talkie agent error: All connection attempts failed]"),
])
def test_litellm_down_too(app, path, expected):
    """LiteLLM itself unreachable: the same M2 renderings."""
    def handler(request):
        raise httpx.ConnectError("All connection attempts failed")
    with _route("talkie"), _fake_litellm(handler):
        out = ask(app, path, ASK)
    assert out.strip() == expected
    assert "All connection attempts failed" in trace_status(app)


def test_only_talkie_skips_the_coordinator_fallback():
    import agents
    assert {n for n, a in agents.AGENTS.items() if not a.coordinator_fallback} == {"talkie"}
