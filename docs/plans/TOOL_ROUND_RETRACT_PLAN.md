# Retract tool-round text (leaked thinking never reaches the user) — Plan

Status: **shipped 2026-09-14 (same day).** 8 tests in
`tests/test_retract.py`; suite 552. Measurements (below) done.

**Measurement results.**
1. Langfuse, last 7 days, E4B generation rounds: **45 of 165 (27%)
   at/over 256 output tokens** — home 14/33 (42%), research 16/51 (31%),
   coordinator 14/75 (19%). Output tokens include the answer, so this
   over-counts cap hits for long answers and is near-exact for the home
   agent's one-line replies: the cap fires on roughly two of five home
   turns. The leak is a daily event, not a rare one.
2. The running llama.cpp (b9611) **ignores** a per-request
   `reasoning_budget_tokens` (probe: budget 8 vs 2000 → 666 vs 603 chars
   of reasoning). The current tree accepts it (`tools/server/
   server-schema.cpp`). Per-agent budgets (home short, research long)
   therefore need a deliberate llama.cpp pin bump — ROADMAP item 19.

## Trigger

"Pause" from the kitchen at 12:40:38Z was answered with the home agent's
deliberation read aloud — "player: The user did not specify a player, so
the tool should use the default… 5. Construct the tool call… 6. Format
the output…" — followed by the correct terminal sentence. Cause: the
E4B server's `--reasoning-budget 256` closed the thinking channel
mid-thought and the model finished the thought as visible content
(documented 2026-06-10, "prompt instructions do not stop the leak"). The
loop streams every content token of a round as it arrives; tool calls
only appear at the end of the round, so by then the leak is out.

## Principle

Content a model produces in a round that ends with a tool call is never
the answer — the loop continues after the tool result, or a terminal tool
speaks for it. So it is retracted, deterministically, whatever the cap.

## Design

- **`run_stream`**: when a round ends with tool calls and produced
  content, yield `{"type": "retract", "chars": N, "preview": …}` before
  executing the tools, and emit `round_content_retracted` (rid-scoped,
  with a preview) so leaks stay findable (tenet 7). Direct-answer rounds
  are untouched — streaming of real answers is unchanged.
- **`run_delegated`** (coordinator ← specialist): drop the retracted
  chars from the collected text. Cleaner delegated results and cleaner
  session history as side effects.
- **`_run_pipeline`**: on retract, trim `assistant_reply` (so stored
  history and the collected voice reply exclude it) and forward a
  `retract` event. The escalation path already trims
  (`del assistant_reply[reply_mark:]`); this generalizes it.
- **SSE `/message`**: `{"retract": N}` → the web UI trims the last N
  chars from the streaming bubble and moves them into the current stage
  entry in the muted "working" style — nothing flickers away, it moves.
- **Voice (`/voice/api/chat`)**: `_ollama_collect` consumes the pipeline
  events directly and drops retracted text before `to_speech`.
- **Streaming API clients** (`/api/chat` display, `/v1`): NDJSON/SSE can't
  retract; they keep today's behaviour. Documented.

## Measurements (decide whether the cap itself moves — separate step)

1. Langfuse: E4B generation rounds at/over 256 output tokens in the last
   7 days, per agent → how often the cap fires.
2. Does the running llama.cpp (b9611) honour a per-request
   `reasoning_budget_tokens`? The current tree does (server-schema.cpp).
   If b9611 does not, an upgrade is a deliberate pin bump; if it does,
   per-agent budgets (home short, research long) attack the source.

## Tests

`tests/test_retract.py`: run_stream yields retract with the right length
on a leaking tool round and not on a direct answer; run_delegated drops
it; `/message` SSE emits the retract and the stored reply excludes it;
the voice collector drops it; terminal passthrough still verbatim.

## Verification

Suite; `pipeline_bench.sh retract-pre/-post`; deploy orchestrator +
nginx restart + shim check; live: a play and a stop from the kitchen
still behave; web UI streams a direct answer unchanged.
