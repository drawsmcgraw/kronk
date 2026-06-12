"""Tests for the shim-transport context handling (voice path).

HA's Ollama integration resends the full conversation each request; the
shim must honor prior turns, drop system messages (Kronk owns its persona),
and not duplicate the current user message.
"""
from types import SimpleNamespace

import orchestrator.main as orch


def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


def test_drops_system_and_strips_trailing_current_message():
    msgs = [
        _msg("system", "You are someone else's persona"),
        _msg("user", "My dog is named Biscuit."),
        _msg("assistant", "Noted!"),
        _msg("user", "What is my dog called?"),
    ]
    ctx = orch._shim_context(msgs, "What is my dog called?")
    assert ctx == [
        {"role": "user", "content": "My dog is named Biscuit."},
        {"role": "assistant", "content": "Noted!"},
    ]


def test_single_message_yields_empty_context():
    msgs = [_msg("user", "hello")]
    assert orch._shim_context(msgs, "hello") == []


def test_trailing_user_message_kept_if_different_from_current():
    # Defensive: if the last user msg isn't the current text, keep it.
    msgs = [_msg("user", "earlier question"), _msg("user", "current question")]
    ctx = orch._shim_context(msgs, "current question")
    assert ctx == [{"role": "user", "content": "earlier question"}]


def test_empty_and_toolish_messages_dropped():
    msgs = [
        _msg("user", ""),               # empty content
        _msg("tool", "tool output"),    # non-chat role
        _msg("user", "real question"),
    ]
    assert orch._shim_context(msgs, "real question") == []
