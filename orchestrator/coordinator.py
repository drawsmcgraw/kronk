"""V2 pipeline: a single tool-using coordinator instead of router→agent.

The coordinator sees the union of:
  - Bare tools (web_search, fetch_url, get_weather, shopping_list_*, …) —
    the same tool defs from `tools.TOOL_DEFINITIONS`, filtered to the set
    that belongs at the coordinator level (everything that was bare in
    individual agents, minus deprecated ones like set_timer).
  - Agent-tools — currently just `research`. Wrapped via
    `AgentConfig.to_tool_definition()` so the coordinator invokes them
    like any other tool. Execution dispatches to `agents.run()`.

Talkie stays out: it's hit by regex pre-check in `routing._TALKIE_PHRASES`
before this pipeline runs.

set_timer stays out: HA Assist owns native multi-named timer intents per
the 2026-05-28 decision. The tool definition still exists in tools.py for
v1 compat; the v2 coordinator simply doesn't expose it.

The shape of the loop mirrors `agents.run_stream` — same MAX_TOOL_ROUNDS,
same forced-synthesis tail when the budget is exhausted, same event types
yielded ({"type":"token"|"narration"|"error"|"done"}).
"""
from __future__ import annotations

import json
import logging
import os
import time

import agents
import fast_path
import llm
import metrics
import tools
from agents import _build_assistant_msg, _args_key, _tool_narration, MAX_TOOL_ROUNDS, kronk_facts
from events import emit

logger = logging.getLogger(__name__)


COORDINATOR_MODEL = os.getenv("COORDINATOR_MODEL", "gemma-4-e4b")

# Tools the v2 coordinator can call directly (not via an agent-tool wrapper).
# These were spread across the v1 agents as their "tool_names"; the v2
# coordinator owns them all.
COORDINATOR_BARE_TOOLS: list[str] = [
    "web_search",
    "fetch_url",
    "get_weather",
    "shopping_list_view",
    "shopping_list_add",
    "shopping_list_remove",
    "shopping_list_clear",
    "query_hottub",
    "query_health",
    "query_finances",
    "get_kronk_context",
    "generate_diagram",
]

# Agents that the v2 coordinator can invoke as agent-tools. Anything in this
# list must already exist in `agents.AGENTS`. Currently just `research`
# (justified by its planning behaviour in Phase 5). The `home`, `health`,
# `finance`, `assistant` agents collapse to bare tools above; `coding` and
# `devops` are out (devstral is a separate harness); `talkie` is regex-routed.
COORDINATOR_AGENT_TOOLS: list[str] = ["research"]


COORDINATOR_SYSTEM_PROMPT = (
    "You are Kronk, a helpful home assistant. Be direct and concise. "
    "Do not use action text, emotes, or filler. Never fabricate live or "
    "private data — if a tool can answer, call the tool. If no tool is "
    "needed, answer directly from your own knowledge.\n"
    "\n"
    "Tool-use rules:\n"
    " - For ANY factual lookup where verbatim precision matters (quotes, "
    "   lyrics, statistics, dates, biographies, technical definitions), "
    "   call `web_search` rather than answering from memory.\n"
    " - For weather, ALWAYS call `get_weather`. Do not ask the user for "
    "   their location; the tool defaults to the home location.\n"
    " - For the user's personal health data, call `query_health`.\n"
    " - For their financial documents, call `query_finances`.\n"
    " - For the shopping list, the hot tub, generating a diagram of Kronk's "
    "   own architecture, etc., use the corresponding tool.\n"
    " - For intensive multi-source research (voter guides, deep dives on "
    "   a topic), invoke the `research` agent-tool with a focused task.\n"
    " - For setting timers: do NOT — Home Assistant handles timer intents "
    "   natively. If asked, say timers are handled by HA's voice assistant.\n"
    " - You may call multiple tools across a turn if a question spans "
    "   domains (e.g. weather + sleep score for a workout recommendation)."
)


def coordinator_tool_defs() -> list[dict]:
    """The full tool surface the coordinator sees: bare tools + agent-tools."""
    bare = [t for t in tools.TOOL_DEFINITIONS if t["function"]["name"] in COORDINATOR_BARE_TOOLS]
    agent_tools = []
    for name in COORDINATOR_AGENT_TOOLS:
        agent = agents.AGENTS.get(name)
        if agent is not None:
            agent_tools.append(agent.to_tool_definition())
    return bare + agent_tools


def init_fast_path() -> None:
    """Compute embeddings for every coordinator tool + agent-tool. Call once
    at app startup."""
    corpus: dict[str, str] = {}
    for td in coordinator_tool_defs():
        fn = td["function"]
        corpus[fn["name"]] = fn["description"]
    fast_path.init(corpus)


async def _dispatch_tool(name: str, args: dict) -> str:
    """Call a coordinator tool — agent-tool or bare tool. Returns a string
    suitable for appending to the tool-response message."""
    if name in COORDINATOR_AGENT_TOOLS and name in agents.AGENTS:
        # Agent-tool invocation. `task` is the parameter we defined in
        # `AgentConfig.to_tool_definition()`. Forward to the agent's run().
        task = args.get("task") or ""
        if not task:
            return f"[{name} agent-tool called with no task]"
        result = await agents.run(agents.AGENTS[name], task, [])
        return f"[{name} agent result]\n{result}"
    # Plain tool — same dispatcher the v1 agents use.
    return await tools.execute(name, args)


async def coordinator_stream(task: str, context: list[dict], model: str | None = None):
    """V2 coordinator pipeline.

    Async generator yielding the same event shapes as `agents.run_stream`:
      {"type": "token",     "text": str}              — incremental content token
      {"type": "narration", "text": str}              — pre-tool status string
      {"type": "error",     "message": str}           — terminal; no more events follow
      {"type": "done",      "model": str, "ok": bool} — terminal

    `context` is a list of prior turns (`{"role": ..., "content": ...}`) for
    history. The coordinator's system prompt is prepended; kronk_facts() too.
    """
    model = model or COORDINATOR_MODEL
    tool_defs = coordinator_tool_defs() or None

    # ── Fast-path: high-confidence embedding match bypasses the coordinator
    # LLM decide-step and goes straight to the matched tool. Only fires for
    # tools on fast_path.SAFELIST_TOOL_INVOCATION (those whose args can be
    # derived without an LLM). Synthesis is still done by the model at the
    # end so the answer is natural-language.
    matched, similarity = fast_path.match(task)
    if matched is not None:
        emit("coordinator_fastpath", tool=matched, similarity=round(similarity, 3))
        yield {"type": "narration", "text": _tool_narration(matched, {})}
        try:
            t_tool = time.monotonic()
            args = fast_path.args_for(matched, task)
            result = await _dispatch_tool(matched, args)
            emit(
                "coordinator_tool_complete",
                tool=matched,
                duration_s=round(time.monotonic() - t_tool, 2),
                via="fastpath",
            )
        except Exception as e:
            logger.error("Fast-path tool %s failed: %s — falling through to coordinator", matched, e)
            result = None
        if result is not None:
            # Run a final-synthesis LLM call with no tools, just the tool
            # result. Construct a valid 4-message conversation: system, user,
            # assistant-with-tool_call (faked — WE made the call), tool-result.
            # Mistral's chat template requires this assistant→tool pairing
            # AND the tool_call_id must be 9 alphanumeric chars.
            import uuid as _uuid
            fake_tool_call_id = _uuid.uuid4().hex[:9]
            synth_msgs = [
                {"role": "system", "content": COORDINATOR_SYSTEM_PROMPT + "\n\n" + kronk_facts()},
                {"role": "user", "content": task},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id":   fake_tool_call_id,
                        "type": "function",
                        "function": {
                            "name":      matched,
                            "arguments": json.dumps(args),
                        },
                    }],
                },
                {"role": "tool", "tool_call_id": fake_tool_call_id, "content": result},
            ]
            t_synth = time.monotonic()
            had_any = False
            try:
                async for chunk in llm.stream(synth_msgs, model, tools=None):
                    if "token" in chunk:
                        had_any = True
                        yield {"type": "token", "text": chunk["token"]}
            except Exception as e:
                logger.error("Fast-path synthesis failed: %s", e)
                yield {"type": "error", "message": f"[coordinator fast-path synth error: {e}]"}
                return
            emit(
                "coordinator_round",
                model=model,
                phase="fastpath_synthesis",
                duration_s=round(time.monotonic() - t_synth, 2),
            )
            if not had_any:
                yield {"type": "token", "text": "[coordinator returned no response]"}
            yield {"type": "done", "model": model, "ok": True}
            return
        # Fall through to normal coordinator if fast-path failed.

    system_content = COORDINATOR_SYSTEM_PROMPT + "\n\n" + kronk_facts()

    # Build the conversation: [system] + prior user/assistant turns + current user turn.
    # The model sees an actual chat history rather than a synthetic block stuffed
    # into the system prompt — better for context-following.
    messages: list[dict] = [{"role": "system", "content": system_content}]
    for m in context:
        # Only carry user/assistant turns; system messages from clients are
        # ignored (Kronk's own system prompt is authoritative).
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": task})

    seen_calls: set[str] = set()
    last_usage: dict = {}

    for round_idx in range(MAX_TOOL_ROUNDS):
        round_content: list[str] = []
        round_tool_calls: list[dict] = []
        t_llm = time.monotonic()

        try:
            async for chunk in llm.stream(messages, model, tool_defs):
                if "token" in chunk:
                    round_content.append(chunk["token"])
                    yield {"type": "token", "text": chunk["token"]}
                elif "tool_calls" in chunk:
                    round_tool_calls = chunk["tool_calls"]
                elif "usage" in chunk:
                    last_usage = chunk["usage"]
        except Exception as e:
            emit("coordinator_llm_error", model=model, error=str(e))
            logger.error("Coordinator stream failed: %s", e)
            yield {"type": "error", "message": f"[coordinator error: {e}]"}
            return

        phase = "synthesis" if not round_tool_calls else f"plan_{round_idx + 1}"
        emit(
            "coordinator_round",
            model=model,
            phase=phase,
            duration_s=round(time.monotonic() - t_llm, 2),
        )
        metrics.record(
            agent="coordinator_v2",
            model=model,
            prompt_tokens=last_usage.get("prompt_tokens", 0),
            completion_tokens=last_usage.get("completion_tokens", 0),
            eval_duration_ns=0,
        )

        if not round_tool_calls:
            if not round_content:
                yield {"type": "token", "text": "[coordinator returned no response]"}
            yield {"type": "done", "model": model, "ok": True}
            return

        # Execute tools, then loop for the next round.
        messages.append(_build_assistant_msg("".join(round_content), round_tool_calls))

        for call in round_tool_calls:
            fn_name = call["function"]["name"]
            fn_args = call["function"]["arguments"] or {}
            key = _args_key(fn_name, fn_args)
            if key in seen_calls:
                result = f"[{fn_name} was already called with these exact arguments this turn; use the earlier result]"
            else:
                yield {"type": "narration", "text": _tool_narration(fn_name, fn_args)}
                t_tool = time.monotonic()
                emit("coordinator_tool_call", tool=fn_name, args=list(fn_args.keys()))
                result = await _dispatch_tool(fn_name, fn_args)
                emit(
                    "coordinator_tool_complete",
                    tool=fn_name,
                    duration_s=round(time.monotonic() - t_tool, 2),
                )
                seen_calls.add(key)

            messages.append({
                "role":         "tool",
                "tool_call_id": call["id"],
                "content":      result,
            })

    # Tool budget exhausted: forced synthesis with tools disabled.
    t_llm = time.monotonic()
    final_content: list[str] = []
    try:
        async for chunk in llm.stream(messages, model, tools=None):
            if "token" in chunk:
                final_content.append(chunk["token"])
                yield {"type": "token", "text": chunk["token"]}
            elif "usage" in chunk:
                last_usage = chunk["usage"]
    except Exception as e:
        emit("coordinator_llm_error", model=model, error=str(e))
        logger.error("Coordinator forced-synthesis stream failed: %s", e)
        yield {"type": "error", "message": f"[coordinator error: {e}]"}
        return

    emit(
        "coordinator_round",
        model=model,
        phase="synthesis_forced",
        duration_s=round(time.monotonic() - t_llm, 2),
    )
    metrics.record(
        agent="coordinator_v2",
        model=model,
        prompt_tokens=last_usage.get("prompt_tokens", 0),
        completion_tokens=last_usage.get("completion_tokens", 0),
        eval_duration_ns=0,
    )

    if not final_content:
        yield {"type": "token", "text": "[coordinator returned no response]"}
    yield {"type": "done", "model": model, "ok": True}
