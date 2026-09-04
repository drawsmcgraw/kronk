# Coordinator model bench — K2 Horizon vs Gemma 4 — Plan

Status: **done 2026-09-03 (same day) — E4B retained.** Gemma 4 12B ties
E4B on correctness at 0.51× the speed; K2-Horizon-7B ties-minus-one at
0.24× (Q8) / 0.38× (Q4) and needs `reasoning_effort=high` to keep its
thinking out of the reply. Nothing on the table can satisfy the
pre-committed rule (tie on correctness AND ≥2× generation), and the
3.7B / MoVA pairings were not run: 3.7B was gated on 7B passing, and
MoVA cannot reach 2× E4B on this iGPU whatever it scores, so its 69 GiB
download would buy information, not a decision. Revisit condition on
the roadmap. Scoreboard in "Results log"; raw runs in
`docs/bench/coord_bench_2026-09-03_*`. **Round 2** (same afternoon, on
the operator's observation that the coordinator battery had no headroom)
benched K2-7B in the devops and research slots — see "Round 2" and
"Final verdict": no swap there either, one structural follow-up for the
research agent.

## Why

IFM (Institute of Foundation Models, MBZUAI's lab — the LLM360 people)
released **K2 Horizon** on 2026-09-03: six fully-open Apache-2.0 models,
0.9B → 375B, one custom architecture (`k2_horizon`), 512K native
context. The 7B card claims "best under 10B" against Gemma 4 12B,
Qwen3.5-9B and Granite 4.2-8B. Operator question: is any of them a
better coordinator/general model for Kronk than the incumbent Gemma 4
E4B? Same question, asked of the *current* Gemma line at the same time,
so the answer is one bench, not two.

## What we learned before building (2026-09-03 probe)

- **No upstream llama.cpp support.** IFM's GGUF cards say "PR in
  progress" and point at `MBZUAI-IFM/llama.cpp` branch `model/K2Horizon`
  (last commit 2026-09-01, 5 ahead / 123 behind master, no upstream PR
  yet). We build that fork ourselves, pinned to
  `35999d101cf2233fc54f09c3c8d599da7303ce02`.
- **The fork adds the model, not a chat parser.** Diff touches loader,
  graph, vocab, tokenizer conversion and a template file — nothing under
  `common/chat*.cpp`. Whether llama-server separates K2's thinking into
  `reasoning_content` and returns *structured* tool calls rides on the
  generic auto-parser. This is the gemma-3-4b failure mode (tool names as
  plain text, REPORT_2026-06 §2.6) and it is gate 0.
- **Thinking cannot be switched off.** The template emits `<ifm|think>`,
  `<ifm|think_fast>` or `<ifm|think_faster>` for `reasoning_effort`
  high/medium/low and raises on anything else. IFM's guidance: always
  high, ≥32K output tokens, temp 1.0 / top-p 0.95. Kronk's regime is the
  opposite (`--reasoning-budget 256`, voice budget). Our budget flag only
  works if the parser knows K2's think-close token — gate 0 again.
- **No MTP draft head.** Gemma 4 ships `-assistant` MTP drafters for
  every size; `--spec-type draft-mtp` is production config (+37–48%
  generation in June). K2 has none. That handicap is fair — the flag is
  how Kronk runs, not a lab setting.
- **Quantization asymmetry.** Gemma runs QAT Q4. IFM ships BF16 GGUFs
  only (7B = 16 GiB). Community Q4/Q8 exist, unverified. We quantize
  locally from IFM's BF16 with the fork's `llama-quantize` and record
  the source hash. K2 runs at **Q8_0 as quality reference** and
  **Q4_K_M as speed candidate** so quant loss is its own variable.
- **The card's benchmark table is the wrong regime** (high effort, 32K
  tokens; e.g. SWE-bench 68.4 vs Gemma-4-12B 30.6). We measure ours.
- Toolchain: box needed `glslc` + `ninja-build` (operator installed) and
  the `SPIRV-Headers` CMake package (installed to a user prefix under
  `~/pai/pai_workspace/llama-cpp/`, tag `vulkan-sdk-1.4.321.0`;
  `sudo apt install spirv-headers` is the tidier permanent fix).

## Candidates and pairings (one variable at a time, in this order)

| # | Challenger | Paired against | Why this order |
|---|---|---|---|
| 1 | Gemma 4 12B (QAT UD-Q4_K_XL + MTP Q8_0, unsloth) | E4B incumbent | known-good toolchain; answers "bigger Gemma?" independent of K2 |
| 2 | K2-Horizon-7B (Q8_0 ref, Q4_K_M speed) | E4B and 12B | the headline model |
| 3 | K2-Horizon-3.7B | E4B | only if 7B passes gate 1 — E4B's weight class |
| 4 | K2-Horizon-MoVA-36B-A4B | Gemma 4 26B-A4B (QAT + MTP) | only if the fork converts the expert layout; the latency play |

Skipped: 0.9B (no LLM router to give it), 32B dense (Devstral's class —
a coding-agent bench, separate), 375B (too big).

## Gates (pre-committed — the rule is written before the numbers exist)

- **Gate 0 — support.** Fork builds with Vulkan. Q8 loads on Vulkan and
  answers coherently. `reasoning_content` arrives separated (no
  deliberation leaking into `content`). A Kronk tool definition yields
  *structured* `tool_calls` 5/5. Any failure stops the K2 track for now:
  ROADMAP watch item keyed on the upstream merge.
- **Gate 1 — isolated.** Same method as the June QAT swap and the July
  devops bench: bench port, identical flags, production untouched,
  llama-server hit directly (not LiteLLM, not the pipeline). Harness:
  `scripts/coordinator_model_bench.py` — uses the **real** coordinator
  system prompt, `kronk_facts()` stamp and ask_* menu imported from
  `orchestrator/agents.py` (one source of truth), sends **no sampling
  params** (mirrors `orchestrator/llm.py`; server defaults apply), and
  repeats probes to count reliability. Probes: delegation on personal
  data (weather, shopping list), no spurious delegation on pure
  knowledge, the composite solar money's-worth query run as a scripted
  multi-turn (ask_home → canned kWh → ask_research → canned rate →
  arithmetic in the answer), news_brief terminal + refresh
  discrimination, plain-answer discipline (no placeholders, no emotes),
  markdown discipline, and a **leak counter** (reasoning or raw tool-call
  syntax in `content`). Metrics: gen/prompt tok/s, TTFT-ish elapsed,
  completion + reasoning tokens.
  **Decision rule** (same as the devops bench): a challenger must beat
  the incumbent on correctness, or tie on correctness and win ≥2× on
  generation speed.
- **Gate 2 — pipeline (finalist only).** `litellm/config.yaml` entry
  (hot-editable) → `COORDINATOR_MODEL` flip → `pipeline_bench.sh` pre/post
  + the 2026-08-18 calibration battery (composite money's-worth 5×,
  single-domain 5×, pure-knowledge control 3×; baselines 5/5, 5/5, 3/3)
  + `research_bench.sh`. No weather/shim-class prompt may regress its
  median by >1 s (tenet 12). Clears chat history — operator schedules it.
- **Gate 3 — box health.** GPU idle 0% after; memwatch canary quiet;
  GTT delta recorded. (The devstral/ROCm idle-spin is why this exists.)

## Configs

Bench ports: 11497 **E4B incumbent (isolated copy, production flags —
the production server on 11438 is never benched; no shared slots, no
live-user contention)** · 11491 Gemma 4 12B · 11492 Gemma 4 26B-A4B ·
11493 K2-7B Q8 · 11496 K2-7B Q4 · 11494 K2-3.7B · 11495 K2-MoVA. Started
by hand, one challenger at a time (single variable; memory).

Gemma challengers = the production E4B unit with file/port substituted:

```
LD_LIBRARY_PATH=~/pai/pai_workspace/llama-cpp/llama-b9611-vulkan \
~/pai/pai_workspace/llama-cpp/llama-b9611-vulkan/llama-server \
  -m /opt/models/google/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf \
  --host 127.0.0.1 --port 11491 -ngl 99 --flash-attn on --ctx-size 32768 \
  --cache-reuse 256 --swa-full --reasoning-budget 256 \
  --model-draft /opt/models/google/mtp-gemma-4-12B-it-Q8_0.gguf \
  --gpu-layers-draft 99 --spec-type draft-mtp
```

K2 on the fork build (sampling per IFM's card; no draft; `--swa-full` is
Gemma-specific and omitted; the budget flag is a gate-0 question):

```
LD_LIBRARY_PATH=~/pai/pai_workspace/llama-cpp/llama-k2h-35999d1-vulkan \
~/pai/pai_workspace/llama-cpp/llama-k2h-35999d1-vulkan/llama-server \
  -m /opt/models/ifm/K2-Horizon-7B-Q8_0.gguf \
  --host 127.0.0.1 --port 11493 -ngl 99 --flash-attn on --ctx-size 32768 \
  --cache-reuse 256 --temp 1.0 --top-p 0.95 \
  --chat-template-kwargs '{"reasoning_effort":"low"}' \
  --reasoning-format auto --reasoning-budget 256
```

Files (sha256 verified on download; recorded in `docs/bench/` JSON):

| File | Source | sha256 |
|---|---|---|
| `google/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf` (6.2 GiB) | unsloth/gemma-4-12B-it-qat-GGUF | `90fd44e2…c940c370` |
| `google/mtp-gemma-4-12B-it-Q8_0.gguf` (0.4 GiB) | same repo, `MTP/` | `f58dff98…8eaec1b` |
| `ifm/K2-Horizon-7B-BF16.gguf` (16 GiB) | IFM/K2-Horizon-7B-GGUF | `088c5d08…40000444` |
| `ifm/K2-Horizon-7B-{Q8_0,Q4_K_M}.gguf` | quantized locally from the BF16 with the fork's `llama-quantize` | `/opt/models/ifm/PROVENANCE.sha256` |

## Results log

- **2026-09-03 13:20 — E4B incumbent, gate 1** (`coord_bench_…_k2h-gate1-e4b`):
  28/28 probes, 0 leaks, median 103.8 gen tok/s, composite median 3.4 s
  (all 5 gathered kWh + rate and did the arithmetic; 2–3 turns).
  Consequence for the rule: the incumbent is at the ceiling of this
  battery, so a challenger can at best *tie* on correctness and must then
  win ≥2× on generation (≥ ~208 tok/s) — which no larger model will. A
  quality *win* over E4B would need harder probes than this battery has
  (research-synthesis depth, long-context recall); noted, not added
  mid-bench — the rule was fixed before the numbers.
- **13:24 — Gemma 4 12B (QAT + MTP), gate 1** (`…_k2h-gate1-12b`): 28/28,
  0 leaks, median **52.6 tok/s (0.51× E4B)**, composite median 5.8 s
  (1.7× slower). Reasons less (330 vs 710 chars median) but pays more
  per token. **Verdict: tie on correctness, loses on speed — E4B
  retained** against 12B. Pairing 1 closed.
- **13:35 — K2-Horizon-7B Q8, gate 0** (fork build `b10671-35999d101`,
  Vulkan). Loads and answers coherently; `llama-quantize` from the fork
  handled the BF16 (Q8_0 in 8 s). **Structured tool calls: yes** —
  every `<ifm|tool_calls>` block parsed into `tool_calls` (weather 4/4,
  then the whole battery). **Reasoning separation: effort-dependent.**
  llama.cpp's auto-parser discovers a template's think markers by
  rendering with `enable_thinking` on/off and diffing
  (`common/chat-diff-analyzer.cpp`); K2 keys thinking on
  `reasoning_effort` and has no off state, so the analyzer only learns
  the default `<ifm|think>` pair. Result: `high` → `reasoning_content`
  separated, clean content; `medium` → deliberation leaks into content
  (and the model closed with the wrong tag); `low` (`think_faster`) →
  near-empty thinking, the close tag leaks into every reply. Gate 0
  passes **only at `reasoning_effort=high`** (+ `--reasoning-budget
  256`, which the parser can then honor). Filed as the upstream watch
  condition: a parser that knows the `_fast`/`_faster` variants.
- **13:38 — K2-7B Q8 at `low`, gate 1** (`…_k2h-gate1-k2-7b-q8`):
  25/28, **28/28 leaks** (the close tag), median **24.4 tok/s (0.23×
  E4B)**, composite 4/5 — the miss was an arithmetic slip ($4.85 for
  3.0 kWh × 16.18 ¢) with no thinking available; markdown_list 0/2 (one
  spurious ask_home for baking-soda uses, one prose answer). Speed is
  memory-bound: Q8 weights are 2× the bytes of E4B's Q4 and the 250K
  vocab makes the output head heavy; no draft head. Re-run at `high`
  follows.
- **13:50 — K2-7B Q8 at `high` + budget 256, gate 1**
  (`…_k2h-gate1-k2-7b-q8-high`): **27/28, 0 leaks**, median 24.6 tok/s,
  composite 4/5 at median **8.0 s** (2.4× E4B's 3.4 s). Both composite
  misses across the two K2 runs were the same class — cents read as
  dollars ($4.85, $48.54) — E4B and 12B were 10/10 on that arithmetic.
  Reasoning cost ~210 chars median per reply under the budget.
- **13:55 — K2-7B Q4_K_M at `high`, speed only**: 40 tok/s (0.38×
  E4B), reasoning separated, tool calls structured (5/5 smoke).
- **Gate 3**: after stopping the bench servers, GPU 0% busy / 15 W, the
  four production units untouched, 58 GiB available. The fork's Vulkan
  build idles like the release builds.

**Scoreboard** (all: 5/5 weather, 3/3 shopping, 5/5 no-spurious, 3/3
news, 3/3 refresh, 2/2 prose unless noted):

| Model | config | correctness | leaks | gen tok/s | composite s |
|---|---|---|---|---|---|
| Gemma 4 E4B (incumbent) | QAT Q4 + MTP, budget 256 | **28/28** | 0 | **103.8** | **3.4** |
| Gemma 4 12B | QAT Q4 + MTP, budget 256 | **28/28** | 0 | 52.6 | 5.8 |
| K2-Horizon-7B Q8 | effort=low | 25/28 (list 0/2, composite 4/5) | 28 | 24.4 | 4.6 |
| K2-Horizon-7B Q8 | effort=high, budget 256 | 27/28 (composite 4/5) | 0 | 24.6 | 8.0 |
| K2-Horizon-7B Q4_K_M | effort=high (smoke) | 5/5 tool calls | 0 | 40.3 | — |

**Verdict: E4B retained.** Rule applied as written.

## Round 2 (same day): the specialist slots

Operator observation after round 1: the coordinator battery had no
headroom above E4B, so it could only confirm the incumbent. The
"addition" half of the question lives in the specialist slots, where
K2's card numbers (SWE-bench, Terminal-Bench, BrowseComp) apply and
tok/s matters less. Both existing specialist benches were run with
K2-7B Q4_K_M at `reasoning_effort=high`.

### Devops/coding slot — `devops_model_bench.py` vs Devstral 24B Q4

Isolated Devstral copy on 11498 (production flags), K2 on 11496; the
bench's `temperature=0.0` applies to both. `docs/bench/devops_bench_2026-09-03_144419_k2h-devops.*`.

| | Devstral-2512 Q4 (incumbent) | K2-Horizon-7B Q4 |
|---|---|---|
| Probes (7, 17 runs) | **17/17** | **17/17** |
| Median gen tok/s | 14.8 | **38.9 (2.6×)** |
| Median wall, tool probes | 0.9 s | 1.4–1.8 s |
| Median wall, prose probes | 5.5 / 11.8 / 9.4 s | 4.1 / 13.5 / 9.3 s |
| Completion tokens, prose | 72 / 158 / 130 | 146 / 513 / 360 |

By the devops bench's own rule (tie on correctness, ≥2× generation) K2
**wins on paper**. Two things the scoreboard does not show:

- **Wall-clock is a wash.** K2 generates 2.6× faster and emits 2–3× more
  tokens (forced thinking at `high` + longer answers), so per-probe time
  lands within ±2 s of Devstral either way.
- **One confident confabulation the keyword checker passed.** K2's pm2
  answer ends by showing how to change MagicMirror's port *inside a
  weather module's config block* — wrong; the port is a top-level
  `config.js` key. Devstral said "edit `config.js` and change the
  `port` value" — terse and right. On the systemd 203/EXEC probe both
  got cause 1 right (ExecStart path / not executable) and cause 2 wrong
  (Devstral: `Type=`; K2: "binary crashes on startup" — that is not what
  203 means). The config-edit answers were identical.

Precedent: in July the operator kept Devstral over a Qwen3-Coder that
tied at 5.7× the speed. This result is the same shape with a smaller
speed margin and a documented prose miss. **Operator's call; not
recommending a swap on this evidence.** If tried: a `llama-k2-horizon-7b`
unit on the fork build (pinned), `--chat-template-kwargs
'{"reasoning_effort":"high"}' --reasoning-budget 256`, LiteLLM entry,
`CODING_AGENT_MODEL`/`DEVOPS_AGENT_MODEL` flip, and the devops bench
re-run through the pipeline — the fork build is the risk to own.

### Research slot — `research_bench.sh` through the pipeline

Baseline with the incumbent first, then K2 in the slot via a LiteLLM
entry + a compose override (`RESEARCH_AGENT_MODEL=k2-horizon-7b`),
nginx restarted, verified through the shim, reverted the same way after
the run (env, LiteLLM entry, bench server all back to production state;
verified). Multi-hop battery, 2 reps, quality read by hand.
`docs/bench/research_2026-09-03_*_k2h-research-{e4b,k2}.json`.

| question | E4B (incumbent) | K2-7B |
|---|---|---|
| gdp_heads (5-hop) | 0/2 — abstained ("could not look up"), 53 s / 17 s | 2/2 answered, **stale**: "Joe Biden", "Fumio Kishida" as current leaders in Sept 2026; **183 s / 136 s** |
| sa_capitals | 2/2 correct, ~4.5 s | 2/2 correct, ~2.5 s |
| ceo_marketcap | 1/2 (r2 abstained), 13–15 s | 2/2 answered; r2 caveats that the live ranking wasn't verified — good behaviour; 15 s / 45 s |
| py_version | 1/2 (r2 abstained), 19 s / 9 s | 2/2 "3.14.7" (matches E4B's r1), 24 s / 23 s |
| leaked tool syntax | 0 | 0 |

Reading: K2 finishes the task more often (8/8 answered vs 4/8) and is
better on the 2–3-hop questions. On the hardest question it did the
worse thing: when the searches didn't yield the list, it answered from
training data and presented it as current, at 2–3 minutes a run. E4B
abstained. For a research agent, "I couldn't find it" beats a stale
confident answer (tenet 6: never let a model claim what the code didn't
verify). The caveat K2 volunteered on ceo r2 shows it *can* flag
unverified answers — it just didn't on gdp. **Not a swap on this
evidence either**; the interesting follow-up is structural, not model:
a research-agent rule that any named-person "current officeholder"
claim must cite a fetched page or be marked unverified — that would
have converted both K2 gdp answers into honest partials and costs
nothing on E4B.

## Final verdict (2026-09-03)

- **Coordinator:** E4B retained (round 1).
- **Devops/coding:** K2-7B ties Devstral on the battery at 2.6× tok/s
  but not on wall-clock, with one confident prose error. Operator's
  call; precedent says keep Devstral.
- **Research:** K2-7B completes more, abstains less, and confabulated
  stale officeholders on the hardest question. Keep E4B; consider the
  "cite or mark unverified" guard for the research agent regardless.

## What would change the answer

- Upstream llama.cpp merges K2 Horizon **and** its parser learns the
  `think_fast`/`think_faster` markers (or IFM adds an `enable_thinking`
  path to the template) — then `low` effort becomes usable and the
  speed gap narrows to the memory-bound floor (~40 tok/s at Q4, still
  <0.5× E4B).
- A K2 MTP/draft head, or a QAT Q4 checkpoint from IFM.
- A different question: for the *research* or *coding* agents, where
  depth matters more than tok/s, K2-7B's card numbers (SWE-bench,
  Terminal-Bench, BrowseComp) argue for a separate bench with harder
  probes. Not this bench's question.

## Steps

1. Plan doc + ROADMAP line. Kick off fork build and downloads. ✔ started
2. Harness `scripts/coordinator_model_bench.py` (+ unit test for the
   probe checkers, no network).
3. Gate 1 on E4B (prod port 11438 — don't run during a live session) and
   Gemma 4 12B on 11491.
4. Gate 0 on K2-7B: quantize, start on 11493, run the harness; read the
   leak counter and tool-call shape before anything else.
5. Gate 1 on K2-7B if gate 0 passes; 3.7B / MoVA pairings per the table.
6. Report with the scoreboard; operator schedules gate 2 for a finalist.
7. Close the docs loop: verdict in this header, feature doc only if a
   model actually changes, ROADMAP line to Shipped or to a watch item.
