# Agent Refactor — Benchmark Report

**Run date:** 2026-05-30
**Branch:** `feature/agent-refactor`
**Scope:** 4 variants × 20 queries × 2 endpoints × 3 trials per pair = 480 trials.

---

## TL;DR

| variant | p50 api_chat | p95 api_chat | pass% api_chat | notes |
|---|---|---|---|---|
| **v1-baseline** | 7.88s | 19.56s | 88% | current production |
| **v2-gemma+fp** | 6.49s | 30.09s | 67% | template-leak failures cripple it |
| **v2-nemo-only** | 12.71s | 54.05s | 93% | reliable but too slow |
| **v2-nemo+fp** | 10.51s | 35.43s | 92% | best v2 variant — still slower than v1 |

**My read: don't flip the default yet.** No v2 variant is unambiguously better
than v1 on the voice path. v2-nemo+fp wins on quality (+4% pass) but loses on
speed (+33% latency). The architectural cleanup is real, but we need a model
with both nemo's tool-calling reliability *and* gemma's speed before the
refactor pays off. **Recommended next step:** try Qwen 2.5 7B Instruct as a
fourth-candidate coordinator (downloads ~5 GB; not done in this pass).

Full results in `benchmarks/results/`. Comparison table:
`benchmarks/results/comparison-all.md`.

---

## What was tested

The four variants:

1. **v1-baseline** — unmodified `main` (router → specialist agents → coordinator)
2. **v2-gemma+fp** — agent-as-tool coordinator (`KRONK_COORDINATOR=v2`),
   `COORDINATOR_MODEL=gemma-4-e4b`, fast-path embeddings on
3. **v2-nemo-only** — agent-as-tool coordinator, `COORDINATOR_MODEL=mistral-nemo`,
   fast-path **off** (isolated coordinator measurement)
4. **v2-nemo+fp** — same as #3 but with fast-path on (the production candidate)

Each variant ran the same 20-query suite (`benchmarks/queries.yml`) against
both `/message` (chat UI, always v1 in this pass) and `/api/chat` (Ollama
shim — voice path; v1 or v2 per variant).

**Note on `/message` numbers:** /message stays on v1 in all variants; its
column is a between-run "control" rather than a v1-vs-v2 comparison. Variance
in those numbers reflects model warm-up / state, not architecture choice.

---

## Per-query api_chat highlights

Where v2 *wins* (faster or equal quality):

| query | v1 | v2-nemo+fp | Δ |
|---|---|---|---|
| `shopping_list_view` | 1.75s | 0.31s | -1.4s — fast-path catches this perfectly |
| `lookup_avgo_close` | 36.80s (1 fail) | 7.97s (clean) | **-29s + fixes a fail** |
| `lookup_news_brief` | 8.26s (1 fail) | varies (also 2 fails — model hangs) | quality tradeoff |
| `kronk_self_arch` | 15.80s | 10.87s | -5s |

Where v2 *loses*:

| query | v1 | v2-nemo+fp | Δ |
|---|---|---|---|
| `weather_default` | 8.45s | 27.70s | **+19s** — nemo writes very long forecasts |
| `weather_specific_location` | 18.48s | 22.70s | +4s |
| `talkie_explicit` | 5.02s | 23.12s | +18s — talkie hits coordinator now, not directly |
| `hottub_status` | 4.31s | 5.29s | +1s |

Patterns:

- **Simple lookups (shopping list, hot tub, weather)** that fast-path catches
  go faster on v2-nemo+fp than on v1 — except weather, where nemo's verbose
  synthesis (~27s) erases the fast-path win.
- **Tool-heavy queries (research, code)** are roughly flat or slightly slower
  on v2-nemo+fp because nemo's synthesis is heavier per token.
- **Voter guide / news brief** show the v2 nemo timeout risk — one trial
  hung at 240s.

---

## Why v2-gemma+fp fails 33% of api_chat calls

`gemma-4-e4b` emits tool-call template syntax as *text* instead of structured
`tool_calls` ~17% of the time when given the full v2 coordinator surface
(12 bare tools + 1 agent-tool). Examples from the bench:

```
"<|tool_call>call:get_weather{}<tool_call|>"
"<|tool_call>call:shopping_list_tool{}<tool_call|>"  ← also hallucinated tool name
"<|tool_call>call:hot_tub{}<tool_call|>"             ← hallucinated tool name
```

Even fast-path-eligible queries fail: when fast-path matches and the synth
step runs (with `tools=None`), gemma *still* sometimes emits the template
syntax. So the failure is in gemma's tool-trained behaviour, not in the
v2 architecture.

v1's 6-agent setup gave gemma narrow allow-lists (1–6 tools each), so it
hit this <3% of the time. The v2 surface is too wide for gemma to
reliably navigate.

**Possible fix not tried:** add a server-side parser that detects the
template-text output and reconstructs the tool call. Recovers ~50% of leaks
(those with the correct tool name); the rest hallucinate non-existent tool
names. Could plausibly push v2-gemma+fp pass rate to 80-85%. Not a slam-dunk.

---

## Why v2-nemo+fp is slow

Mistral-nemo (12B Q8) is ~2-3× slower per token than gemma-4-e4b (8B Q4).
For weather (a verbose response), the bench measures the full synthesis time:

- Fast-path matches `get_weather` in ~10 ms (embedding)
- Tool call to `tool_service` runs in ~0.5–1.0 s
- **Synthesis (nemo writes the weather forecast): 26-28 s**

The synthesis dominates. v1 + `gemma-4-e4b` synthesizes the same forecast in
~3-5 s because the model is smaller and faster.

This is fundamentally a model-throughput issue, not a code issue.

---

## What the fast-path achieved

Telemetry from the v2-nemo+fp bench shows:

- 9 fast-path matches × 3 tools (shopping_list_view, query_hottub,
  get_weather) — 27 of 60 api_chat trials hit fast-path
- The other 33 trials went through the full coordinator decide-step
- Fast-path eliminates the ~3-5 s coordinator "decide which tool" LLM call

For simple-lookup queries the fast-path is a clear win:

```
shopping_list_view via fast-path:  0.28-0.29 s synth, ~0.5 s tool, ~1 s total
shopping_list_view via coordinator: 5-6 s decide + 0.5 s tool + 3 s synth = ~9 s
```

The savings show up in the data — `shopping_list_view` went from v1's 1.75s
to v2-nemo+fp's 0.31s. Fast-path works.

The reason fast-path *doesn't* save weather: the synthesis (writing the
forecast paragraph) is the slow step, not the tool-decide step. Nemo writes
slowly, so weather is still slow.

---

## Decisions made autonomously

(captured here so they're visible from the report; also in
`docs/AGENT_REFACTOR.md` §9)

- Started on a `feature/agent-refactor` branch (operator said they'd
  make one but I started first)
- Bench cycle script restarts llama.cpp servers at start of every cycle —
  caught one mid-bench `gemma-4-e4b` drift and aborted/re-ran v1 baseline
- Per-client sessions implemented as part of the same batch: shims now
  use the full `messages` array as conversation context (was being dropped)
- Fast-path safelist limited to tools whose args are derivable without an
  LLM (web_search, get_weather, shopping_list_view, query_hottub,
  get_kronk_context). Other tools fall through even on high-similarity match.
- Tool-call ID normalization (9-char alphanumeric) — required by Mistral's
  chat template, accepted by all others. Was a hard-fail bug for v2-nemo
  until fixed.
- Fast-path synthesis message-shape fix — was `[system, user, tool]`, now
  `[system, user, assistant(tool_call), tool]` to satisfy Mistral's
  jinja template.
- Did **not** flip the default to v2 anywhere — all v2 paths are
  feature-flag gated. Reverting is just `KRONK_COORDINATOR=v1` (default).
- Did **not** download any new models. The `mistral-nemo` candidate was
  already loaded; downloading Qwen would have been a bigger commitment
  and the user said to use judgment — I chose to surface findings first.

---

## Recommendations (for the operator on return)

In rough priority order:

1. **Keep v1 as the default** (it already is — KRONK_COORDINATOR defaults
   to v1). The v2 refactor is shipped behind a feature flag but should
   not be promoted to default yet. Tests pass, code is clean, just not
   measurably better.

2. **Try Qwen 2.5 7B Instruct as a v2 coordinator candidate.** Same
   benchmark, same bench harness. Qwen is well-regarded for tool
   calling, smaller than nemo (likely faster), and may give us
   gemma-speed + nemo-reliability. ~5 GB download. About 30-40 min
   of operator time to set up the systemd unit + add to LiteLLM
   catalog + re-run bench.

3. **If Qwen doesn't pan out, write the template-leak parser** to
   recover gemma's failed tool calls. ~50% recovery rate estimated.
   Could make v2-gemma+fp viable as the production path. Modest code
   work (~150 lines) plus careful testing.

4. **Optional: Reduce nemo's verbosity** with a tighter coordinator
   system prompt ("answer in ≤2 sentences for status queries"). Might
   close the gap with v1's per-query speeds without changing models.

5. **Defer Phase 5 (research planning agent)** until we pick a
   coordinator. The Planning pattern would compound the model-speed
   issues we just saw.

---

## What's in the tree

```
benchmarks/
  queries.yml              # 20-query suite
  agent_bench.py           # bench harness (clears /history between trials)
  run_cycle.sh             # restart llamas + rebuild orchestrator + bench
  summarize.py             # 1- or 2-file per-query summary
  multi_compare.py         # N-file comparison (used for this report)
  results/
    v1-baseline-*.jsonl
    v2-coord-*.jsonl       # v2-gemma+fp
    v2-coord-nemo-nofp-*.jsonl
    v2-nemo-fp-*.jsonl
    comparison-all.md      # 4-way side-by-side
    REPORT.md              # this file

orchestrator/
  coordinator.py           # NEW — v2 single tool-using coordinator
  fast_path.py             # NEW — embedding fast-path (Phase 2.5)
  agents.py                # +AgentConfig.to_tool_definition()
  llm.py                   # +9-char tool_call_id normalization
  main.py                  # +KRONK_COORDINATOR dispatch, +context plumbing
  requirements.txt         # +fastembed==0.8.0

docs/
  AGENT_ARCHITECTURE_RESEARCH.md  # the book research
  AGENT_REFACTOR.md               # the design doc + decision log
```

Three new env vars on `orchestrator` (all defaulting to v1-compatible values):

- `KRONK_COORDINATOR` (default `v1`) — v1 router or v2 coordinator
- `KRONK_FAST_PATH_ENABLED` (default `true`) — embedding fast-path on/off
- `KRONK_FAST_PATH_THRESHOLD` (default `0.65`) — cosine similarity cutoff
