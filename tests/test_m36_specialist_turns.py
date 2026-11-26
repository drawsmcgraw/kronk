"""Work item M36: a pinned specialist gets the earlier turns as chat turns.

The incident (docs/incidents/INVESTIGATION_2026-11-26_play_that_album.md):
"play that album, in the kitchen" after a reply naming Pulse by Pink Floyd
was answered "I need to know the name of the album". The specialist was
given the earlier turns as text at the end of its system prompt, where the
4B model did not use them (0 of 6); as chat turns it did (6 of 6).

Criterion 1 (docs/sprint/M36-criteria.md): the messages built for a pinned
specialist hold the system text with no "[Recent conversation context]"
block, then the earlier turns in order with their roles, then the request.
The window's rules (the ruling on M36-review.md point 3) are pinned on
sessions.specialist_turns directly below.
"""
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("LLM_SERVICE_URL",  "http://fake-llm:8002")
os.environ.setdefault("TOOL_SERVICE_URL", "http://fake-tools:8003")

import unittest.mock as mock_module  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import agents  # noqa: E402
import sessions  # noqa: E402
import orchestrator.main as orch  # noqa: E402

pytestmark = pytest.mark.usefixtures("no_network")

ASK = "play the album 'pulse' by pink floyd in the kitchen"
FAILED = ("I couldn't play that. I couldn't find an album called Pink Floyd - Pulse in "
          "Music Assistant. The closest I found was Pulse by Pink Floyd.")
FOLLOW = "play that album, in the kitchen"


def u(text):
    return {"role": "user", "content": text}


def a(text):
    return {"role": "assistant", "content": text}


# ── The window: sessions.specialist_turns ────────────────────────────────────

def test_empty_history_gives_no_turns():
    assert sessions.specialist_turns([]) == []
    assert sessions.specialist_turns(None) == []


def test_one_exchange_is_kept_whole():
    assert sessions.specialist_turns([u(ASK), a(FAILED)]) == [u(ASK), a(FAILED)]


def test_at_most_two_exchanges_from_the_end():
    h = [u("1"), a("1r"), u("2"), a("2r"), u("3"), a("3r")]
    assert sessions.specialist_turns(h) == [u("2"), a("2r"), u("3"), a("3r")]


def test_odd_length_history_starts_on_a_user_turn():
    """history[-5:] of an alternating list starts on an assistant turn; that
    window gave the wrong album 2 of 18 times in the design review."""
    h = [u("0"), a("0r"), u("1"), a("1r"), u("2"), a("2r")]
    old = h[-5:]
    assert old[0]["role"] == "assistant"            # what the old code passed
    out = sessions.specialist_turns(old)
    assert out == [u("1"), a("1r"), u("2"), a("2r")]
    assert out[0]["role"] == "user"
    # And a list that is odd because it opens on a reply.
    assert sessions.specialist_turns([a("x"), u("1"), a("1r")]) == [u("1"), a("1r")]


def test_two_user_messages_in_a_row_stop_the_walk():
    """A shim array with two user messages in a row (an empty assistant
    reply dropped by _shim_context): nothing before the gap is joined on."""
    h = [u("0"), a("0r"), u("1"), u("2"), a("2r")]
    assert sessions.specialist_turns(h) == [u("2"), a("2r")]


def test_empty_message_is_a_break():
    h = [u("0"), a("0r"), u("1"), a(""), u("2"), a("2r")]
    assert sessions.specialist_turns(h) == [u("2"), a("2r")]


def test_other_roles_are_a_break():
    h = [u("0"), a("0r"), {"role": "tool", "content": "x"}, u("1"), a("1r")]
    assert sessions.specialist_turns(h) == [u("1"), a("1r")]


def test_request_duplicated_at_the_end_of_a_shim_array_is_dropped():
    """A client that sends the request twice at the end (a retry):
    _shim_context drops one copy and leaves the other in the history. It is
    not an exchange, so it goes; the exchanges before it stay."""
    msgs = [_Msg("user", ASK), _Msg("assistant", FAILED), _Msg("user", FOLLOW),
            _Msg("user", FOLLOW)]
    ctx = orch._shim_context(msgs, FOLLOW)
    assert ctx[-1] == u(FOLLOW)                     # the duplicate, as the shim leaves it
    assert sessions.specialist_turns(ctx) == [u(ASK), a(FAILED)]
    # Two unanswered user messages at the end go the same way.
    assert sessions.specialist_turns([u(ASK), a(FAILED), u("x"), u(FOLLOW)]) == [u(ASK), a(FAILED)]


def test_long_assistant_reply_is_cut_user_turn_is_not(monkeypatch):
    monkeypatch.setattr(sessions, "ASSISTANT_PROMPT_CHARS", 10)
    out = sessions.specialist_turns([u("q" * 50), a("r" * 50)])
    assert out == [u("q" * 50), a("r" * 10 + "…")]


def test_failure_replies_are_kept():
    """The incident's follow-up resolves from the failure reply."""
    assert a(FAILED) in sessions.specialist_turns([u(ASK), a(FAILED)])


def test_input_is_not_mutated():
    h = [u("1"), a("1r"), u("2")]
    before = [dict(m) for m in h]
    sessions.specialist_turns(h)
    assert h == before


class _Msg:
    def __init__(self, role, content):
        self.role, self.content = role, content


# ── run_stream: one form for earlier turns ───────────────────────────────────

@pytest.mark.asyncio
async def test_run_stream_refuses_a_context_block():
    """The "[Recent conversation context]" branch is gone. A caller that
    still passes turns that way fails loud instead of losing them."""
    with pytest.raises(ValueError, match="history_messages"):
        [ev async for ev in agents.run_stream(agents.AGENTS["home"], "x", [u("hi")])]


# ── Criterion 1: what a pinned specialist is sent, on every entry point ──────

_open_orig = open


def _fake_open(path, *a, **kw):
    if "/app/static/" in str(path):
        import io
        return io.StringIO("<html></html>")
    return _open_orig(path, *a, **kw)


@pytest.fixture
def client(tmp_path):
    with mock_module.patch("builtins.open", side_effect=_fake_open), \
         mock_module.patch.object(orch.metrics, "METRICS_DB", tmp_path / "metrics.db"), \
         mock_module.patch.object(orch.sessions, "SESSIONS_DB", tmp_path / "sessions.db"), \
         patch("agents.weather_context", new=AsyncMock(return_value=None)), \
         patch("orchestrator.main.agents.weather_context", new=AsyncMock(return_value=None)):
        orch.sessions.clear(orch.WEBUI_SESSION)
        with TestClient(orch.app, raise_server_exceptions=True) as c:
            yield c
        orch.sessions.clear(orch.WEBUI_SESSION)


@pytest.fixture
def sent():
    """Every message list the model is sent, by agent model call; the
    model answers with text so no tool runs."""
    calls: list[list[dict]] = []

    async def fake_stream(messages, model, tools=None, **kw):
        calls.append([dict(m) for m in messages])
        yield {"token": "OK."}
        yield {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    with patch("agents.llm.stream", new=fake_stream):
        yield calls


def _post(client, path, messages):
    if path == "/message":
        return client.post(path, json={"text": messages[-1]["content"]})
    resp = client.post(path, json={"model": "kronk", "messages": messages, "stream": False})
    assert resp.status_code == 200, resp.text
    return resp


def _assert_specialist_messages(msgs, turns, request):
    assert msgs[0]["role"] == "system"
    assert "[Recent conversation context]" not in msgs[0]["content"]
    for t in turns:                                  # no turn copied into the system text
        assert t["content"] not in msgs[0]["content"]
    assert msgs[1:] == turns + [u(request)]


@pytest.mark.parametrize("path", ["/api/chat", "/voice/api/chat", "/v1/chat/completions"])
def test_shim_specialist_gets_turns_as_messages(client, sent, path):
    """The incident's exchange resent by a client: the home agent (music
    rule) gets system text, the two earlier turns, then the request."""
    _post(client, path, [{"role": "system", "content": "You are a voice assistant."},
                         u(ASK), a(FAILED), u(FOLLOW)])
    assert len(sent) == 1
    _assert_specialist_messages(sent[0], [u(ASK), a(FAILED)], FOLLOW)


def test_chat_page_specialist_gets_turns_as_messages(client, sent):
    sessions.append_exchange(orch.WEBUI_SESSION, [("user", ASK), ("assistant", FAILED)])
    _post(client, "/message", [u(FOLLOW)])
    assert len(sent) == 1
    _assert_specialist_messages(sent[0], [u(ASK), a(FAILED)], FOLLOW)


def test_long_shim_history_is_windowed(client, sent):
    """A shim array has no cap and no cut of its own: the specialist sees
    the last two exchanges, the reply cut at ASSISTANT_PROMPT_CHARS."""
    long_reply = "Abbey Road is playing. " + "x" * (sessions.ASSISTANT_PROMPT_CHARS + 50)
    msgs = [a("stray opening reply"), u("play some jazz"), a("Jazz is on."),
            u("play Abbey Road"), a(long_reply), u(ASK), a(FAILED), u(FOLLOW)]
    _post(client, "/api/chat", msgs)
    cut = long_reply[:sessions.ASSISTANT_PROMPT_CHARS] + "…"
    _assert_specialist_messages(sent[0], [u("play Abbey Road"), a(cut), u(ASK), a(FAILED)], FOLLOW)


def test_no_history_gives_system_then_request(client, sent):
    _post(client, "/api/chat", [u(FOLLOW)])
    _assert_specialist_messages(sent[0], [], FOLLOW)


def test_coordinator_path_unchanged(client, sent):
    """The coordinator still gets the whole history as turns (not the
    specialist window)."""
    h = [u("1"), a("1r"), u("2"), a("2r"), u("3"), a("3r")]
    _post(client, "/api/chat", h + [u("tell me a joke")])
    msgs = sent[-1]
    assert msgs[0]["role"] == "system"
    assert msgs[1:] == h + [u("tell me a joke")]


def test_run_delegated_still_gets_no_turns(sent):
    """A specialist asked by the coordinator through ask_* gets the query
    alone, as before."""
    import asyncio
    with patch("agents.weather_context", new=AsyncMock(return_value=None)):
        asyncio.run(agents.run_delegated(agents.AGENTS["home"], "what is the weather", []))
    assert [m["role"] for m in sent[0]] == ["system", "user"]
