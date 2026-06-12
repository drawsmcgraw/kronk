# Kronk Response-Time Program — Final Report

**Date:** 2026-06-12
**Goal:** Cut end-to-end pipeline response time with zero quality loss —
prefer changes that improve both.
**Verdict up front:** Median latency dropped 29-86% across every prompt
class (typical voice/weather queries roughly halved; quick factual queries
7× faster), voice clients gained real conversation memory, and the central
tuning knob was deliberately *loosened* at the end — giving back a few
seconds on weather to protect research-answer quality. Details, decisions,
and the honest misses below.

---

## 1. Where we started

The 2026-06-10 telemetry analysis (`docs/PERF_FINDINGS_2026-06-10.md`,
data via the then-new Langfuse deployment) had established:

- The pipeline was **generation-bound**, not prompt-bound: llama.cpp
  processes prompts at ~1,400 tok/s but generates at ~53 tok/s, and
  Gemma-4-E4B (the agent/coordinator model) silently generates 176-769
  tokens of hidden chain-of-thought *per LLM round* before saying anything.
- A weather question — the most common voice query — took two LLM rounds
  plus a tool call: **10.3-10.5 s median, spiking to 17 s** depending on how
  long the model decided to think.
- Routing and tools were already fast (0.07-0.8 s).

Baseline was captured as a reusable battery (`scripts/pipeline_bench.sh`):
8 prompts covering every route (direct/home/research) and transport
(web UI, OpenAI shim, Ollama shim = the voice path), 3 repetitions each,
history cleared between items. Raw data: `docs/bench/bench_*_baseline-pre.json`.

| prompt | baseline median | min | max |
|---|---|---|---|
| direct_short | 5.53 s | 5.49 | 5.74 |
| direct_long | 7.08 s | 1.99 | 10.59 |
| weather_now | 10.32 s | 7.76 | 12.25 |
| weather_umbrella | 10.49 s | 7.57 | 10.72 |
| shopping_list | 3.72 s | 3.00 | 5.41 |
| research_bios | 22.33 s | 17.24 | 30.61 |
| shim_timezone | 0.57 s | 0.39 | 0.59 |
| shim_weather | 11.40 s | 8.48 | 13.43 |

---

## 2. What we changed and why

### 2.1 Reasoning token budget (`--reasoning-budget`) — the change we tuned three times

**Problem:** E4B is a reasoning model. Its hidden thinking is the single
largest and least predictable latency cost. Disabling it entirely
(`--reasoning-budget 0`, tested 6/10) made the model leak its deliberation
into the visible answer — rejected.

**What we did:** llama.cpp b9585 supports a true token *budget* (N>0).
We swept N ∈ {64, 128, 256} against a 5-prompt tool-dispatch harness plus
quality probes (logic trap, factual recall, conditional reasoning).

**The honest journey:**
- **64** passed every dispatch and quality probe (15/15 tool calls, clean
  answers) and shipped first. But the full-pipeline check exposed a subtle
  regression: the *research* agent stopped calling `fetch_url` — with only
  64 thinking tokens it planned shallowly, answering from search snippets.
  Faster, but worse answers. Win/win rule says: not acceptable.
- **128** produced something worse in one probe: a malformed tool call
  leaked into the visible output — budget exhaustion appears to be able to
  corrupt tool-call grammar mid-thought.
- **256** restored full research depth (`web_search` → `fetch_url` →
  sourced answers, verified ×2), kept direct queries fast (~0.7 s), and
  still caps the pathological 769-token tails. **Shipped.**

We also tested per-request budget overrides (would have allowed "deep
thinking for research, tight for dispatch") — llama.cpp ignores the
request-level parameter, so a single global budget it is.

**Lesson recorded:** the budget knob trades *planning depth* for latency,
and the cost shows up two layers above the model — in which tools an agent
decides to use. Benchmarks on dispatch alone would have missed it.

### 2.2 Weather context injection — the flagship win/win

**Problem:** every weather question paid router → agent round 1 (decide to
call tool) → get_weather → agent round 2 (synthesize). Two full LLM rounds
for data that changes hourly.

**Decision — why not cache answers:** an answer cache (Redis/semantic
matching) would only ever return stale generic text and adds cache-key
machinery that can misfire. Instead we **pre-fetch the data and put it in
the prompt**: the tool_service now refreshes the home-location forecast
hourly (background asyncio task, persisted to `/data/weather_cache.json`,
stale data kept on fetch failure) and serves it at `GET /weather/cached`.
The orchestrator injects it into the home agent's system prompt with an
instruction to answer directly and only call `get_weather` for *other*
locations. Past 2 h staleness the injection is omitted and the agent falls
back to the live tool unchanged.

**Result:** weather questions are now ONE LLM round, zero tool calls:
10.3 s → 2.6 s ("what's the weather"), 10.5 s → 4.1 s ("do I need an
umbrella tomorrow?" — answered correctly, day-aware, from injected data).
Same source data as the tool → no quality change. Arguably better: active
weather alerts ride along in the context.

### 2.3 Per-client conversation sessions

**Problem:** one global in-memory history list shared by every client and
lost on restart — and the voice path (OpenAI/Ollama shims) ignored history
entirely: voice had *no* conversation memory.

**Discovery that shaped the design:** HA's Ollama integration resends the
full conversation with every request (verified in HA's source). So voice
clients carry their own history — the shims just had to stop throwing it
away. Server-side storage is only needed for the web UI.

**Decisions:**
- **SQLite on /data** (`orchestrator/sessions.py`), not Redis: one process,
  one host — an in-process dict cache with write-through SQLite beats a
  network hop, costs zero extra containers, and the DB file IS the
  durability story (web UI conversations now survive restarts).
  LLM-memory frameworks (mem0, LangGraph checkpoints) bring summarization/
  graph machinery we don't need yet.
- **The real cost of history is prompt tokens, not I/O.** Three guards:
  capped window (`HISTORY_MAX_MESSAGES=40`) trimmed at user-turn
  boundaries; assistant turns truncated to 1,500 chars at prompt-build
  time (stored in full); and append-only prompt ordering so llama.cpp's
  `--swa-full` prompt cache makes consecutive turns pay only for new
  tokens.
- Failed turns don't persist — a router error leaves the stored
  conversation untouched.

**Voice "clear my history":** a deterministic regex intercept
(`routing.CLEAR_HISTORY_RE`, same pattern family as the existing route
shortcuts) runs *before* any LLM call on both transports, wipes the
requesting client's session, and confirms ("Done — fresh start."). The
UI's clear button now goes through the same path. Semantics note: HA owns
voice history and its conversation window expires naturally between voice
sessions; the intercept clears everything Kronk-side and costs ~0 ms.

**Verified:** multi-turn recall on web UI ✓, recall across orchestrator
restart ✓, HA-style message arrays give the shim multi-turn memory ✓,
clear-by-phrase wipes for real ✓. (Physical Voice PE follow-up test:
operator to-do.)

### 2.4 Gemma 4 QAT model swap

Google shipped quantization-aware-trained (QAT) GGUFs on 2026-06-05 —
4-bit checkpoints where the model *learned* to tolerate quantization during
training, vs our post-training-quantized Q4_K_M.

**Method:** unsloth's `gemma-4-E4B-it-qat-UD-Q4_K_XL` (4.2 GB) benchmarked
against the incumbent on an isolated server instance (port 11498),
identical flags, production untouched. Measured: generation/prompt
throughput, tool-call reliability (5×), four quality probes.

| | Q4_K_M (incumbent) | QAT UD-Q4_K_XL |
|---|---|---|
| Generation | 55.4-57.1 tok/s | **64.4-65.3 tok/s (+15%)** |
| Prompt eval | 318-599 tok/s | 407-522 tok/s |
| Tool calls (5×) | 5/5 | 5/5 |
| Quality probes (4) | 4/4 | 4/4 |
| Idle GPU after | 0% | 0% |

**What the numbers mean:** quality is indistinguishable at this probe
depth, and QAT's training-aware quantization is documented to track bf16
closer than PTQ — so the swap is at worst neutral on quality and +15% on
the metric this pipeline is bound by (generation speed). **Swapped.**
Rollback is a one-line `-m` path revert; the old GGUF stays on disk.

### 2.5 Dashboards & observability usability

Operator feedback: the dashboards didn't show what one pipeline run looks
like. Two fixes:
- `docs/TELEMETRY_GUIDE.md` — one-page operator guide: the per-run stage
  view is Langfuse's **Tracing waterfall** (not dashboards); span-name
  glossary; how the dashboards answer aggregate questions; ClickHouse SQL
  escape hatch.
- Stage-level dashboard widgets now exclude `pipeline.*` root spans
  (totals no longer dominate the bars) and carry plainer titles ("Where
  time goes, per stage (p95)"). Filter JSON shape was validated against
  the public metrics API before writing it into Langfuse's Postgres.

### 2.6 Dropped: small-model voice fast path

The idea (route home-agent queries to a fast 4B) died in testing:
gemma-3-4b emits tool names as plain text, not structured tool calls.
On-disk alternatives (Mistral-Nemo 12B, Pixtral 12B) generate slower than
E4B, so there was no win available. Weather injection + QAT delivered the
voice-latency goal by other means.

---

## 3. Before / after

Identical battery, identical method, same box, ~3 h apart
(`baseline-pre` → `final-post-v2`, both in `docs/bench/`).

| prompt | baseline median (min-max) | final median (min-max) | Δ median |
|---|---|---|---|
| direct_short | 5.53 (5.49-5.74) | **0.77** (0.45-2.13) | **-86%** |
| direct_long | 7.08 (1.99-10.59) | **5.02** (1.47-6.36) | -29% |
| weather_now | 10.32 (7.76-12.25) | **5.75** (3.59-5.83) | -44% |
| weather_umbrella | 10.49 (7.57-10.72) | **5.00** (2.90-8.80) | -52% |
| shopping_list | 3.72 (3.00-5.41) | **2.38** (1.32-3.38) | -36% |
| research_bios | 22.33 (17.24-30.61) | **10.21** (3.79-13.20) | -54% |
| shim_timezone | 0.57 (0.39-0.59) | **0.32** (0.32-0.74) | -44% |
| shim_weather | 11.40 (8.48-13.43) | **5.94** (5.35-6.08) | -48% |

**The deliberate trade at the end:** an intermediate run with
`--reasoning-budget 64` (`final-post`, also in `docs/bench/`) clocked
weather_now at **2.59 s** and direct_short at 1.95 s — but that setting is
what made the research agent skip `fetch_url`. The shipped config (budget
256) gives back ~3 s on weather-class queries to keep research answers
deep and sourced. If a future llama.cpp honors per-request budgets, both
numbers are achievable simultaneously (see watch list).

**Attribution by phase** (from per-phase spot checks):
- Weather class: Phase B (context injection) did most of it; Phase A/D
  trimmed the remaining LLM round.
- Direct queries: Phase A (reasoning budget) + Phase D (QAT +15%).
- Research: Phase D + capped worst-case reasoning; depth deliberately
  preserved at the cost of keeping research the slowest class.
- Shims: same changes via the shared pipeline + Phase C context handling.

## 4. New moving parts (architecture quick-reference)

```
voice (HA resends history) ─┐
web UI (SQLite session) ────┼─► /message | shims ─► clear-history intercept
                            │            ─► router (gemma-3-4b, regex shortcuts first)
                            │            ─► home agent ◄── [hourly weather context]
                            │                 │  (tool_service refresh loop,
                            │                 │   /weather/cached, 2h staleness cap)
                            │            ─► other agents ─► tools
                            └─► sessions.py (append after success only)
```

- `orchestrator/sessions.py` — session store (schema: `session_messages
  (session_id, role, content, created_at)`; window/truncation logic lives
  here, not in callers).
- `tool_service` weather cache — `HOME_LOCATION`, `WEATHER_REFRESH_SEC`
  envs; survives restarts via `/data/weather_cache.json`.
- `routing.CLEAR_HISTORY_RE` — the voice/UI clear-history intercept.
- llama-gemma4-e4b unit — QAT model, `--swa-full --reasoning-budget 256`.

## 4.1 Same-day field fix (2026-06-12 afternoon)

First real-world session surfaced three gaps, fixed within the hour
(reproduced from logs + Langfuse traces, then replayed to verify):

- **No date grounding**: the model had no idea what day it was and
  confidently invented "next Tuesday, October 22" *with a fabricated
  forecast*. `kronk_facts()` now stamps the live date/time into every
  system prompt, with an instruction to resolve relative dates against it.
- **Forecast window too short**: the weather cache held 6 NWS periods
  (~3 days); "next Tuesday" fell outside. Now all ~14 periods (7 days).
- **Misrouted follow-ups hallucinated**: bare follow-ups ("what about next
  Tuesday?") sometimes route `direct`, and the coordinator had no weather
  data. The weather context now rides along on the coordinator and shim
  synthesis paths too (~400 tokens, hourly-stable so prompt-cache-friendly).

Notable: session memory itself worked throughout — the home agent correctly
resolved "next Tuesday, June 16" from conversation context. What looked
like "losing context" was missing date grounding plus a data gap.

## 5. Open items & watch list

- **Gemma 4 drafter/MTP** (llama.cpp PR #23398, WIP): speculative decoding
  for E4B, ~60% throughput gain reported by a fork. When merged: download
  the drafter GGUF, re-run `pipeline_bench.sh`, expect the next big step.
- **Per-request reasoning budgets**: llama.cpp ignores request-level
  overrides today. If that lands, give research `-1` and dispatch `64`.
- **Reasoning-budget grammar corruption at 128**: observed once (malformed
  tool call in visible output). Not investigated further since 256 shipped;
  if tool-call glitches appear, suspect budget-exhaustion truncation first
  (`--reasoning-budget-message` is the untested mitigation).
- **Voice PE physical test**: multi-turn follow-up + "clear my history" by
  actual voice — operator to-do.
- **Session UX**: the web UI still has one fixed session (`webui`);
  `MessageRequest.session_id` is already plumbed if multiple named
  conversations are ever wanted.
- Stability program (separate thread): Vulkan-fix verdict window runs to
  ~2026-06-16; BIOS update still recommended.
