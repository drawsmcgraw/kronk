# Streaming Tool-Calls Refactor — Plan

**Status:** planned, not yet implemented
**Author:** drafted 2026-05-13
**Related:** `orchestrator/agents.py`, `orchestrator/llm.py`

---

## Goal

Replace the current two-call agent pattern (non-streaming `llm.complete()` plan rounds + non-streaming `llm.complete()` final synthesis OR streaming `llm.stream()` synthesis only when `MAX_TOOL_ROUNDS` is exhausted) with a single unified streaming call that accumulates `tool_calls` from streamed deltas.

After this change, every agent — regardless of how many tools it uses — produces tokens that stream continuously from the first available delta. The mid-loop single-chunk fallback that today affects finance/health/home/assistant goes away.

---

## Why this matters

1. **Consistency.** Today, `finance` (1 tool, exits at round 2) dumps the full answer as a single SSE event; `research` (frequently 3+ rounds) streams. Same loop, two different UX paths.
2. **Voice-readiness.** A speaker can't render "a wall of text instantly." The single-chunk path becomes "5 s of silence, then 30 s of uninterruptible speech" once TTS is wired in. Continuous token flow is a precondition for the sentence-boundary emitter that will sit in front of TTS.
3. **Removes accidental architecture.** The current streaming path only fires when `MAX_TOOL_ROUNDS` is exhausted — the safety cap is doing double duty as "have we earned streaming?" That coupling goes away.
4. **Removes `synthesis_model`.** Today exists only because plan and synthesis are separate calls. With one call per round, one model per agent.

---

## Current architecture (what we're replacing)

```
agents.run_stream()
├── if no tools: stream directly via llm.stream() — fast path
└── else:
    ├── for round in range(MAX_TOOL_ROUNDS):
    │   ├── completion = await llm.complete(messages, tools, model)   # blocking
    │   ├── if no tool_calls: yield content as ONE chunk, return     # ← bug
    │   └── execute tools, append to messages
    └── final synthesis: stream via llm.stream(messages, synth_model) # tools removed
```

Three exit paths. Only one streams. `synthesis_model` exists to let the synthesis path use a different model than the planning path.

---

## Target architecture

```
agents.run_stream()
└── for round in range(MAX_TOOL_ROUNDS):
    ├── async for chunk in llm.stream(messages, model, tools):
    │   ├── if chunk has token:      yield {"type": "token", "text": ...}
    │   ├── if chunk has tool_calls: stash for end-of-stream processing
    │   └── if chunk has usage:      stash for metrics
    ├── if no tool_calls accumulated: done, return
    └── execute tools (with narration), append to messages, continue
```

One call per round. Same path whether tools are used or not (tools=None for talkie). No `synthesis_model` field. The MAX_TOOL_ROUNDS cap is purely a safety bound now.

---

## Implementation

### Phase 1 — `llm.stream()` accepts tools and emits tool_calls

**File:** `orchestrator/llm.py`

Current `stream()` only handles content deltas. Extend it to:

1. Accept `tools: list[dict] | None = None` parameter. When non-None, include in payload as `tools` + `tool_choice: "auto"`.
2. Accumulate `tool_calls` deltas across chunks. OpenAI streams them as:
   ```
   {"delta": {"tool_calls": [{
       "index": 0,
       "id": "call_xyz",          # first chunk only
       "function": {
           "name": "...",         # first chunk(s) only
           "arguments": "{\"q"    # appended across many chunks
       }
   }]}}
   ```
   Accumulator keyed by `index` since a model can emit multiple parallel calls. Concatenate `function.name` and `function.arguments` string-by-string across deltas.
3. At end of stream (on `[DONE]` or when `finish_reason: "tool_calls"`):
   - Parse each accumulated `arguments` JSON string into a dict (mirror what `complete()` does)
   - Backfill any missing `id` with `f"call_{uuid.uuid4().hex[:12]}"`
   - Yield once: `{"tool_calls": [...]}` with the same shape `complete()` returns
4. Keep `stream_options.include_usage` behavior. Usage chunk arrives at end, after the finish chunk.

**New yielded event types from `llm.stream()`:**
- `{"token": str}` — content delta (unchanged)
- `{"tool_calls": [...]}` — emitted once at end if any accumulated
- `{"usage": {...}}` — token counts (unchanged)

### Phase 2 — Rewrite `agents.run_stream()`

**File:** `orchestrator/agents.py`

Collapse the three-path branching into one loop:

```python
async def run_stream(agent, task, context):
    messages = [system, user]
    seen_calls: set[str] = set()
    tool_defs = agent.tool_defs() or None  # None if empty list

    for round_idx in range(MAX_TOOL_ROUNDS):
        accumulated_tool_calls = []
        usage = {}
        content_collected = []

        try:
            async for chunk in llm.stream(messages, agent.model, tool_defs):
                if "token" in chunk:
                    content_collected.append(chunk["token"])
                    yield {"type": "token", "text": chunk["token"]}
                elif "tool_calls" in chunk:
                    accumulated_tool_calls = chunk["tool_calls"]
                elif "usage" in chunk:
                    usage = chunk["usage"]
        except Exception as e:
            yield {"type": "error", "message": f"[{agent.name} agent error: {e}]"}
            return

        # record metrics, emit agent_round event...

        if not accumulated_tool_calls:
            # Done. Content already streamed token-by-token.
            if not content_collected:
                yield {"type": "token", "text": f"[{agent.name} agent returned no response]"}
            yield {"type": "done", "model": agent.model, "ok": True}
            return

        # Build assistant message from the round (content + tool_calls)
        messages.append(_build_assistant_msg_from_stream(content_collected, accumulated_tool_calls))

        # Execute tools (with narration + dedup — same logic as today)
        for call in accumulated_tool_calls:
            # ...narration event, tools.execute(), append tool message, dedup via seen_calls
            ...

    # MAX_TOOL_ROUNDS exhausted with tool_calls every round.
    # Issue one final streaming call with tools disabled to force synthesis.
    async for chunk in llm.stream(messages, agent.model, tools=None):
        if "token" in chunk:
            yield {"type": "token", "text": chunk["token"]}
        elif "usage" in chunk:
            usage = chunk["usage"]
    yield {"type": "done", "model": agent.model, "ok": True}
```

Notes:
- The `talkie` fast path goes away — calling `llm.stream(messages, model, tools=None)` is identical behavior.
- `_build_assistant_msg()` needs a sibling that builds from streamed `(content_parts, tool_calls)` instead of from a `complete()` return value. Or refactor the existing helper to take those two args directly.
- Narration emission stays exactly as today — emit before each `tools.execute()` call.
- Dedup via `seen_calls` stays exactly as today.

### Phase 3 — Cleanup

- Remove `synthesis_model` field from `AgentConfig` (currently set only on `research`, equal to `model` — no behavior change).
- Keep `llm.complete()`. It is still used by `routing.py:91` for the router classification (single-token output, no streaming needed).
- Keep `agents.run()` (the sync collector wrapper) — it's a small wrapper that may have non-streaming callers; verify with `grep -rn "agents\.run\b"` before touching.

---

## API surface changes

| Symbol | Change |
|---|---|
| `llm.stream(messages, model)` | New optional `tools=None` parameter; new yielded chunk type `{"tool_calls": [...]}` |
| `llm.complete()` | Unchanged — still used by router |
| `agents.run_stream()` | Same yielded event shapes (`token`, `narration`, `error`, `done`) — caller in `main.py` needs no changes |
| `AgentConfig.synthesis_model` | Removed |

The orchestrator's SSE consumer (`main.py:229-242`) is **unchanged** — same event types come out of `run_stream()`. The refactor is entirely below that boundary.

---

## Edge cases to handle

1. **Model emits content before tool_calls in same response.** Common with mistral-nemo ("Understood. Fetching your HRV data…" then tool_call). With unified streaming this becomes natural: the preamble streams as content, then the tool_calls accumulate, then execute at end of stream. No special-casing needed. The user sees the preamble in real time, which is good for voice.
2. **Model emits multiple parallel tool_calls in one response.** Different `index` values in deltas. Accumulator handles both; execute serially in `index` order (same as today). Future: parallelize via `asyncio.gather`.
3. **Model emits zero content and zero tool_calls.** Loop exits with empty content_collected and empty accumulated_tool_calls — yield the existing "agent returned no response" sentinel.
4. **`finish_reason: "tool_calls"` vs `finish_reason: "stop"`.** Both possible. Don't gate on `finish_reason` — gate on whether any tool_calls accumulated. Some llama.cpp builds set `finish_reason: "stop"` even when tool_calls were emitted.
5. **Partial JSON in `function.arguments` across deltas.** Only parse at end of stream, never mid-stream. If JSON parse fails at end, fall back to `{}` (same as `complete()` does today).
6. **Missing `id` on a tool_call.** Backfill with UUID (mirrors `complete()`).
7. **Usage chunk arrives after `[DONE]`.** Don't return on `[DONE]` until after draining one more chunk for usage. Existing code returns on `[DONE]` — needs a small change to keep reading until the connection closes OR until usage is seen. Verify against LiteLLM's actual ordering.

---

## Testing approach

Manual smoke test, one prompt per agent, in this order:
1. **talkie** (no tools) — must stream tokens
2. **finance** (1 tool: `query_finances`) — must stream tokens after the tool runs
3. **health** (1 tool: `query_health`) — must stream tokens after the tool runs
4. **home** (1–2 tools: weather/shopping) — must stream
5. **research** (2+ tools: web_search + fetch_url) — must stream (regression check; already worked)
6. **coding / devops** — must stream

For each, confirm in the browser:
- Tokens appear progressively, not in one chunk
- Narration events appear before each tool call ("searching the web for...", "looking up your hrv data")
- Final response is identical to what the chat UI showed before the refactor

Regression watch:
- `mistral-nemo:12b` "preamble + tool_call" case (per README "Lessons Learned"). Should show the preamble streaming, then execute the tool, then stream the synthesis.
- The router phase is unchanged. Verify routing decisions look the same.

No automated test exists for the agent loop today. Worth adding one only if this refactor uncovers a bug we want to lock down.

---

## Risks

1. **LiteLLM / llama.cpp tool_call streaming behavior is the unknown.** Some llama.cpp templates emit the entire tool_call in a single delta; others split it. The accumulator handles both, but we should sniff the wire format from a real call before committing to the implementation. One throwaway curl with `stream: true` and `tools: [...]` against LiteLLM will show us the shape.
2. **`stream_options.include_usage` interaction with tool_calls.** Need to confirm usage chunk still arrives when tool_calls are present. If not, metrics for tool-using rounds will be lost — annoying but not blocking.
3. **First-token latency may shift.** Currently the model can think silently during the plan call before any tokens are emitted. With unified streaming, if the model decides to call tools without any content preamble, TTFT will *appear* to stall at zero tokens until the tools run. The narration events fill this gap — verify they fire promptly. If not, consider an explicit "thinking..." narration before the first stream call.
4. **MAX_TOOL_ROUNDS post-loop fallback.** When the cap is exhausted, we issue one more stream call with `tools=None` to force synthesis. This is the only "extra" LLM call introduced by the design, and it only fires on agents that genuinely use all 3 tool rounds. Acceptable.

---

## Out of scope — explicitly not doing now

- **Sentence-boundary emitter in front of TTS.** Comes after this refactor, when voice is being wired in. Buffers tokens, flushes on `. `, `? `, `! `, `\n\n`. Trivial once streaming is consistent.
- **Removing the router.** Router stays. It's small, fast, and routes well in practice. Revisit only if voice TTFT measurements show the extra hop is hurting.
- **Cross-agent tool access (global tool allow-list).** Today each agent has its own `tool_names`. Keeping that partition for now; the router commits the turn to one tool set. Reconsider only if we see frequent misroutes that need cross-agent tool access.
- **Parallel tool execution within a round.** Tools execute serially in `index` order today; keep that. `asyncio.gather` is a one-line change later if needed.
- **Streaming a tool_call to the executor before the full stream ends.** Could shave hundreds of ms by kicking off tool execution as soon as one `function.arguments` JSON parses cleanly. Complex and rarely useful (most tool_calls finish their delta stream in <100ms). Skip.
- **Refactoring `main.py`'s direct LiteLLM stream (lines 290+).** The coordinator/direct path bypasses `llm.stream()` entirely. Out of scope for this refactor; may be worth folding in later for symmetry.
- **Removing `llm.complete()`.** Still used by `routing.py` for one-token classification output. Keep.
- **Adding automated tests for the agent loop.** No test scaffolding exists today; building it is its own task. Manual smoke test for now.

---

## Done criteria

- Every agent's response streams token-by-token in the browser UI, including tool-using agents that finish in 2 rounds (the original `finance` bug).
- `synthesis_model` field is gone from `AgentConfig`.
- `llm.stream()` accepts `tools=` and yields a `{"tool_calls": [...]}` chunk at end of stream when applicable.
- Narration events still appear before each tool execution.
- All seven agents (talkie, finance, health, home, research, coding, devops) pass the manual smoke test above.
- No regression in router latency or routing accuracy.
