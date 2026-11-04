# Coordinator model bench — Gemma 4 26B-A4B vs E4B, harder probes — Plan

Status: **done 2026-10-24 — not a candidate under the rule as written; E4B
retained.** 26B-A4B matched or beat E4B everywhere except arithmetic, at
0.76x the generation speed and the same composite latency (4.2 s vs
4.0 s), but missed two pre-committed lines: +2 hard-set runs where the
rule asks for +3, and 21.4 GiB GTT where the rule asks for <= 20. Its
two composite misses were confidently wrong dollar figures. A
larger-sample round is the open operator call (see "What would change
the answer"). Raw runs: `docs/bench/coord_bench_2026-10-24_*`.

## Why

Operator question (2026-10-24): is there anything to gain from a bigger
Gemma 4 than the E4B coordinator? Gemma 4 ships E2B, E4B, 12B, 26B-A4B
(MoE, ~4B active per token) and 31B dense. There is no 8B.

- **12B** was benched 2026-09-03 (`MODEL_BENCH_K2_HORIZON_PLAN.md`):
  28/28, tied E4B at 0.51x the generation speed. Not adopted.
- **31B dense** is not benched. Every token reads all 31B weights; on
  this iGPU that is roughly 1/6 to 1/8 of E4B's speed, outside the voice
  budget (tenet 12) for a model on every request.
- **26B-A4B** was row 4 of the K2 plan and never run. As a MoE it reads
  about 4B weights per token, so it could generate at a usable fraction
  of E4B's speed with a much larger model's knowledge.

Two things changed from September:

1. **The battery had no headroom.** E4B scored 28/28, so a bigger model
   could only tie. This plan adds a hard probe set first and runs it on
   E4B before any challenger, so we know where E4B actually fails.
2. **The decision rule.** September's rule (tie and win >=2x on tok/s)
   rules out every model bigger than E4B by construction. This bench
   asks a quality question, so the rule is quality-first (below). The
   operator asked for this test after the rule change was named.

## Decision rule (fixed before any numbers)

26B-A4B becomes a candidate for the coordinator only if all hold:

1. **No regression on the base battery:** >= E4B's score on the existing
   28 runs, 0 leaks.
2. **A real win on the hard set:** at least 3 more passing runs than E4B
   out of the hard set's total, with no hard probe where it scores below
   E4B by more than one run.
3. **Latency inside the voice budget:** composite_solar median <= 8 s
   (E4B: 3.4-4.0 s), and median generation >= 40 tok/s.
4. **Memory:** GTT for the server <= 20 GiB at the production context.

Passing makes it a candidate, not a swap. The swap needs operator
sign-off, then gate 2 (`pipeline_bench.sh` pre/post, which clears chat
history, so the operator schedules it) and a real "Okay Nabu" check.

## Hard probe set

Added to `scripts/coordinator_model_bench.py` as `HARD_PROBES` and
`HARD_COMPOSITES`, scored in their own scoreboard section so the base
battery stays comparable with the September runs. Each probe has a
deterministic checker and a unit test. They target failure classes
seen in production or in the September research round:

| id | prompt shape | pass |
|---|---|---|
| officeholder_delegate | "Who is the current prime minister of Japan?" | calls ask_research (September: models answered stale officeholders from memory) |
| mortgage_rate_research | "What's the current 30-year mortgage rate?" | calls ask_research, not ask_finance |
| weather_by_riddle | "What's the weather like in the city where the Orioles play?" | ask_home with "Baltimore" in the query |
| date_math | "What day of the week is it 100 days from today?" | correct weekday (computed from today), no tools |
| clock_math | "A train leaves at 9:40 AM and the trip takes 2 hours 35 minutes. When does it arrive?" | "12:15", no tools |
| exact_words | "In exactly three words, what does a refrigerator do?" | three words |
| multi_domain (composite) | "How did I sleep last night, and will it rain tomorrow?" | ask_health and ask_home both called, both facts in the answer |
| composite_hottub (composite) | heater cost per 30-day month at 6 h/day | ask_home (kW) and ask_research (rate), answer $160.18 within rounding |

## Configs

Bench ports (from the K2 plan): 11497 isolated E4B copy with production
flags, 11492 Gemma 4 26B-A4B. Production 11438 is never benched. One
server at a time.

26B-A4B = the production E4B unit with the file and port substituted:

```
LD_LIBRARY_PATH=~/pai/pai_workspace/llama-cpp/llama-b9611-vulkan \
~/pai/pai_workspace/llama-cpp/llama-b9611-vulkan/llama-server \
  -m /opt/models/google/gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf \
  --host 127.0.0.1 --port 11492 -ngl 99 --flash-attn on --ctx-size 32768 \
  --cache-reuse 256 --swa-full --reasoning-budget 256 \
  --model-draft /opt/models/google/mtp-gemma-4-26B-A4B-it-Q8_0.gguf \
  --gpu-layers-draft 99 --spec-type draft-mtp
```

Files (repo `unsloth/gemma-4-26B-A4B-it-qat-GGUF`, revision
`7b92b5b28818151e8669af2e45e88d6086f490dd`):

| File | Size | sha256 |
|---|---|---|
| `google/gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf` | 13.3 GiB | `a7c5bc715f5ff8e99a3e8901ce7d2b42b402c669bf24f7c5250747633d0f5891` |
| `google/mtp-gemma-4-26B-A4B-it-Q8_0.gguf` (from `MTP/`) | 0.43 GiB | `75f58e20c32281d1adc086c0569d53f147e8d81a7b8493cf4a73a028415f67af` |

## Steps

1. Plan doc + ROADMAP line. Download the files (pinned revision). ✔
2. Hard probes in the harness, with checker tests. Tests pass. ✔
3. E4B copy on 11497: base + hard. This shows the headroom. ✔
4. 26B-A4B on 11492: base + hard. Record GTT, draft acceptance, tok/s. ✔
5. Gate 3: bench servers stopped, production units untouched, GPU idle. ✔
6. Verdict in this header; ROADMAP line to a watch item. ✔

## Results log

Both servers run under `systemd-run --user` (units `bench-e4b`,
`bench-g4-26b`), one at a time, llama.cpp b9611 Vulkan, production flags.

| | E4B (incumbent copy) | 26B-A4B |
|---|---|---|
| Base battery (28 runs) | 26/28 | 27/28 |
| Base, corrected (see checker note) | 27/28 | 28/28 |
| Hard set (24 runs) | 20/24 | 22/24 |
| Leaks | 0 | 0 |
| Median gen tok/s, base | 108.3 | 82.0 (0.76x) |
| composite_solar median | 4.0 s | 4.2 s |
| Hard composites median | 3.9 s | 5.0 s |
| Median reasoning chars, base | 532 | 261 |
| MTP draft acceptance, median per request | 0.59 (n=64) | 0.74 (n=65) |
| GTT at 32k ctx | 4.8 GiB (production unit) | 21.4 GiB |
| Load time | 6 s | 10 s |

Hard set per probe (E4B / 26B-A4B): officeholder 3/3 / 3/3, mortgage
3/3 / 3/3, weather_by_riddle 3/3 / 3/3, **date_math 1/3 / 3/3**,
clock_math 3/3 / 3/3, **exact_words 2/3 / 3/3**, multi_domain 3/3 / 3/3,
**composite_hottub 2/3 / 1/3**.

What the misses were:

- **E4B date_math:** "Wednesday, February 2, 2027" and "Tuesday,
  January 2, 2027". The answer is Monday, February 1, 2027.
- **E4B exact_words:** "Cools and preserves food." (four words).
- **E4B no_spurious:** one ask_research call for Denver's time zone,
  empty content.
- **E4B composite_hottub:** asked the user for the heater's wattage
  instead of calling ask_home. No wrong number.
- **26B-A4B composite_hottub:** "$169.51" (it listed 990 kWh and
  $0.1618/kWh correctly, then multiplied wrong) and "$159.98". The
  answer is $160.18. Both stated as fact.
- **Checker note:** both models had one composite_solar run graded a
  miss for "48.54 cents", which is correct (3.0 kWh x 16.18 cents). The
  base checker accepts "0.49", "49 cents" and similar, not unrounded
  cents. Left as is mid-bench so both models were graded the same; the
  corrected row above counts those runs as passes. Fix the checker
  before the next bench.

### Against the rule

1. No base regression, 0 leaks: **pass** (27 vs 26; corrected 28 vs 27).
2. +3 hard-set runs, no probe worse by more than one: **fail** (+2;
   composite_hottub is worse by one, which the rule allows).
3. composite_solar <= 8 s and >= 40 tok/s: **pass** (4.2 s, 82.0 tok/s).
4. GTT <= 20 GiB: **fail** (21.4 GiB).

### Reading

- **Speed was the surprise.** At ~4B active parameters it generates at
  0.76x E4B, and its higher draft acceptance (0.74 vs 0.59) plus half the
  reasoning tokens make wall-clock nearly equal. The September assumption
  that a bigger model costs the voice budget does not hold for this MoE.
- **Quality:** better at date arithmetic and hard constraints, the same on
  delegation, worse at multi-step dollar arithmetic. The arithmetic misses
  are the worse kind (tenet 6): a confident wrong figure rather than a
  question back. n=3 per hard probe is small; a 2-run margin is inside the
  noise.
- **Memory:** 21.4 GiB is 4.4x E4B. Production has ~48 GiB available with
  the current four servers, so it fits, but it is the second-largest
  tenant after Devstral (34.0 GiB). The 20 GiB line was a guess made
  before the numbers; it stays as written.

## What would change the answer

- **A larger sample, pre-registered:** the hard set at 10 repeats per
  probe on both models (~10 minutes each). If 26B-A4B leads by >= 10% of
  the hard-set runs with no arithmetic regression, it clears line 2.
- **Arithmetic made structural (tenet 5):** a calculator tool for the
  coordinator would remove the one class 26B-A4B lost, and E4B's
  cents-as-dollars class from September too. Worth doing on either model.
- **Memory:** retiring the unassigned `llama-bonsai` (6.1 GiB GTT) or
  trimming Devstral's 128k context would pay for most of the difference.
