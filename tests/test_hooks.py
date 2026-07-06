"""Tests for litellm/hooks.py — the proxy-side message normalizer.

Two shipped regressions pinned here:

1. 2026-07-03: the hook was silently dead — LiteLLM passes
   call_type="acompletion" for async proxy calls and the hook matched only
   "completion", so non-alternating histories reached llama.cpp unmerged
   (Gemma templates 400 on those — the voice-path router 400).
2. 2026-07-05 review (P0.1): _normalize rebuilt every message as
   {role, content}, stripping tool_calls / tool_call_id from agent-loop
   transcripts on every multi-round tool call. Tool transcripts must pass
   through untouched.

Import note: the repo's litellm/ directory shadows the (uninstalled) litellm
pip package, so `import litellm.hooks` cannot work in the test venv. We stub
the one upstream symbol hooks.py needs and load the file directly.
"""
import copy
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def hooks():
    if "litellm.integrations.custom_logger" not in sys.modules:
        litellm_pkg = types.ModuleType("litellm")
        integrations = types.ModuleType("litellm.integrations")
        custom_logger = types.ModuleType("litellm.integrations.custom_logger")

        class CustomLogger:  # minimal stand-in for the real base class
            pass

        custom_logger.CustomLogger = CustomLogger
        litellm_pkg.integrations = integrations
        integrations.custom_logger = custom_logger
        sys.modules.setdefault("litellm", litellm_pkg)
        sys.modules["litellm.integrations"] = integrations
        sys.modules["litellm.integrations.custom_logger"] = custom_logger

    spec = importlib.util.spec_from_file_location(
        "kronk_litellm_hooks", REPO_ROOT / "litellm" / "hooks.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── call_type gating (the 2026-07-03 dead-hook regression) ───────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["completion", "acompletion"])
async def test_hook_normalizes_on_both_completion_call_types(hooks, call_type):
    data = {"messages": [
        {"role": "user", "content": "what is the weather"},
        {"role": "user", "content": "what is the weather"},
    ]}
    out = await hooks.proxy_handler_instance.async_pre_call_hook(
        None, None, data, call_type
    )
    assert out["messages"] == [
        {"role": "user", "content": "what is the weather\n\nwhat is the weather"}
    ]


@pytest.mark.asyncio
async def test_hook_ignores_other_call_types(hooks):
    messages = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    data = {"messages": copy.deepcopy(messages)}
    out = await hooks.proxy_handler_instance.async_pre_call_hook(
        None, None, data, "aembedding"
    )
    assert out["messages"] == messages


@pytest.mark.asyncio
async def test_hook_tolerates_data_without_messages(hooks):
    data = {"input": "embed me"}
    out = await hooks.proxy_handler_instance.async_pre_call_hook(
        None, None, data, "acompletion"
    )
    assert out == {"input": "embed me"}


# ── _normalize semantics ──────────────────────────────────────────────────────

def test_normalize_appends_user_turn_after_trailing_assistant(hooks):
    out = hooks._normalize([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    assert out[-1] == {"role": "user", "content": "Please continue."}


def test_normalize_flattens_content_block_lists(hooks):
    out = hooks._normalize([
        {"role": "user", "content": [
            {"type": "text", "text": "part one"},
            {"type": "image_url", "image_url": {"url": "ignored"}},
            {"type": "text", "text": "part two"},
        ]},
    ])
    assert out == [{"role": "user", "content": "part one\npart two"}]


def test_normalize_empty_list_passthrough(hooks):
    assert hooks._normalize([]) == []


# ── tool-transcript passthrough (P0.1 regression) ─────────────────────────────

@pytest.mark.asyncio
async def test_agent_loop_transcript_passes_through_untouched(hooks):
    """A round-2 agent-loop message array must survive the hook byte-for-byte:
    rebuilding it as {role, content} strips tool_calls / tool_call_id and
    orphans the tool result. Note the trailing assistant turn also must NOT
    get a 'Please continue.' appended — the skip is total."""
    transcript = [
        {"role": "system", "content": "You are the home agent."},
        {"role": "user", "content": "how's the weather"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": "{\"location\": \"Denver\"}"},
        }]},
        {"role": "tool", "tool_call_id": "call_1", "content": "72F and sunny"},
        {"role": "assistant", "content": "It's 72F and sunny."},
    ]
    data = {"messages": copy.deepcopy(transcript)}
    out = await hooks.proxy_handler_instance.async_pre_call_hook(
        None, None, data, "acompletion"
    )
    assert out["messages"] == transcript
    assert out["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert out["messages"][3]["tool_call_id"] == "call_1"


def test_normalize_skips_any_list_containing_a_tool_message(hooks):
    """Even degenerate shapes (consecutive same-role turns) are left alone
    when tool machinery is present — template repair is chat-history-only."""
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "tool", "tool_call_id": "call_9", "content": "result"},
    ]
    assert hooks._normalize(copy.deepcopy(msgs)) == msgs
