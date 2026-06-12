# Pipeline performance findings — 2026-06-10 (Telemetry Phases 3-4)

First bottleneck analysis using the new Langfuse telemetry
(docs/plans/TELEMETRY_PLAN.md). Data: ~25 traced pipeline runs across all
routes (direct, home, research, shim) on 2026-06-10.

## Dashboards (Phase 4 — done)

Two dashboards live at http://kronk:3000 → Dashboards:

- **Kronk — Pipeline Bottlenecks**: P95/avg latency by stage, TTFT by
  model, output tokens/sec, call volume, token usage.
- **Kronk — Pipeline Health**: trace/observation volume, error levels,
  latency trends.

(Created by direct insert into Langfuse's Postgres `dashboards` /
`dashboard_widgets` tables — there is no public dashboards API as of
v3.181.0. Widget queries validated against `/api/public/metrics` first.
If a Langfuse upgrade changes the widget schema, these may need
re-creating by hand in the UI; the definitions are in this repo's git
history and trivially re-derivable from this doc.)

Ad-hoc SQL access (Phase 3 validation — all bottleneck questions
answerable):

    docker exec -it langfuse-clickhouse clickhouse-client \
      --user clickhouse --password "$CLICKHOUSE_PASSWORD"
    -- tables: observations, traces (database: default)

## Where the time actually goes (warm system, weather query)

| Stage | Time | Notes |
|---|---|---|
| routing.decide (gemma-3-4b) | **0.07s** | was 0.33-0.43s before --swa-full; prompt cache now hits |
| agent LLM round 1 (e4b) | **2-14s ← THE bottleneck** | see below |
| tool.get_weather | 0.73s | fine |
| agent LLM round 2 (e4b) | 1.2-2.6s | answer synthesis, ~80-110 tok |

## Findings, in order of impact

### 1. Gemma-4-E4B's hidden chain-of-thought dominates and is wildly variable

E4B is a reasoning model: before every answer or tool call it generates
`reasoning_content` the user never sees. Measured on the *same* weather
question: 176, 256, then 769 reasoning tokens (≈3.3s, 4.8s, 14.4s at
~53 tok/s). This is the single largest and least predictable cost.

Tested `--reasoning-budget 0` (disables the thinking channel):
- Tool calls stayed correct, completion dropped 63→18 tokens, end-to-end
  7.6s→5.8s, perceived TTFT 6.6s→0.6s.
- **But the model leaks its deliberation into the visible reply** ("The
  user is asking for the current weather. The available tool is…").
  Prompt instructions do not stop the leak — suppressing the channel
  redirects the thinking, it doesn't remove it.
- **Reverted.** Clean answers > speed, for now.

### 2. `--swa-full` applied to both gemma servers (KEPT — clear win)

Gemma uses sliding-window attention; without `--swa-full` llama.cpp
logged `forcing full prompt re-processing due to lack of cache data` on
every request — zero prompt-cache reuse. With it:
- Router calls collapsed 0.33-0.43s → **0.05-0.08s** (cache-hit prompt
  evals of 1-15 tokens instead of ~470).
- Repeated agent prefixes (constant system prompt + tool defs) now reuse
  cache across requests.
- Cost: ~2 GB extra GTT (18.6 GB total, pool is ~101 GB). No throughput
  penalty measured (prompt eval ~1400 tok/s before and after).

### 3. Generation throughput is the floor: ~53 tok/s on E4B (Vulkan)

Prompt eval is NOT the problem (1350-1500 tok/s). Every visible second
beyond tools is output tokens ÷ 53. Until reasoning length is controlled
(finding 1) or a faster/smaller model serves simple agents (finding 4),
total latency tracks output volume.

### 4. gemma-3-4b cannot replace E4B for agents (tested, failed)

The obvious "use the fast non-reasoning 4B for simple tool dispatch" play
doesn't work today: via llama-server it emits the tool name as plain text
(`"get_weather\n"`) instead of structured `tool_calls`. Options if we want
this later: a tool-calling-tuned small model, llama.cpp `--jinja` template
experiments, or grammar-constrained outputs. Not pursued.

### 5. Conversation history inflates every prompt

The orchestrator's in-memory `history` is appended to every coordinator
prompt, and my test traffic pushed prompts from ~470 to ~1250 tokens.
Self-inflicted in testing, but real usage accumulates the same way until
restart. With --swa-full caching the shared prefix this costs less than
before, but a history cap/summarization is worth considering eventually.

### 6. Instrumentation fix shipped with this work

For rounds that end in tool calls, `tool_calls` arrive at end-of-stream —
the original code marked TTFT there, inflating TTFT ≈ round duration for
pure-tool rounds. Fixed in agents.py; TTFT now only marks content tokens.

## Recommendations (not yet applied — operator's call)

1. **Constrain reasoning, not disable it.** Watch llama.cpp/LiteLLM for a
   working reasoning-token *budget* (vs the current 0/-1 toggle), or test
   newer E4B chat templates. The 769-token deliberations are the tail
   that hurts.
2. **Voice-path latency target**: for HA voice, consider a "fast path" —
   home-agent queries answered by a tool-calling-capable small model, E4B
   reserved for research/coding/finance. Blocked on finding 4.
3. **Tighten agent answer style** ("one short sentence") — round-2 output
   is 80-110 tokens ≈ 2s; could be ~40 ≈ 0.8s.
4. **History cap** (finding 5) — cheap, bounded prompts forever.

## State after this session

- gemma3-4b unit: + `--swa-full`
- gemma4-e4b unit: + `--swa-full` (reasoning-budget tested and reverted)
- agents.py: TTFT artifact fix
- Two dashboards live; ~25 traces of baseline data captured
- Baseline to beat (warm, weather query): **total ~5.8-17s depending on
  reasoning-length roulette; tools 0.7s; router 0.07s**
