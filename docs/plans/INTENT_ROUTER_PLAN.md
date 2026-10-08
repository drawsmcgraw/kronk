# A learned intent router behind the regex pins — Plan

Status: **benched 2026-10-05 — no arm passes the gate; the regex pins stay.** Eval
set and harness are in the repo (`docs/bench/routing_eval.jsonl`,
`scripts/routing_bench.py`); results `docs/bench/routing_bench_2026-10-05_*_router-gate1.*`.
Operator ask: "look into implementing a different type of model as an
intent router", after the music pin raised the regex-tech-debt question
(`MUSIC_ROUTING_PIN_PLAN.md`).

## What must not repeat (the repo's own receipts)

- An LLM classifier (gemma-3-4b, phase 1) sent "what is tomorrow's
  forecast?" to research and a composite "money's worth of my panels" to
  research (`INVESTIGATION_2026-07-05_forecast_misroute.md`, trace
  7d62ffb3). Topic pins stranded composites; the escalate net caught one
  of two (`COORDINATOR_ROUTING_PLAN.md`). Result: six precision-first
  regex pins, everything else to the coordinator.
- So any added router must pin only on high confidence and **abstain** to
  the coordinator otherwise. The routes are {talkie, research, home,
  devops, direct}; home sub-intents {weather, playback, now_playing,
  music, mm_update}.

## Options, with the numbers that matter

| Option | Latency | Data | Evidence | Verdict |
|---|---|---|---|---|
| Supervised classifier (DistilBERT / fastText) | ~5 ms CPU | thousands (DistilBERT) / hundreds per class (fastText) | 0.89–0.95 on CLINC150 at 15k examples | too data-hungry for hundreds of utterances |
| SetFit (contrastive fine-tune of a MiniLM/bge body) | ms | ~8/class matches full fine-tunes; trains in ~200 s on CPU | HF SetFit paper/docs | viable upgrade path; each retrain is an artifact to pin |
| Embedding + kNN/centroid with abstain | **3 ms** CPU (measured: bge-small via fastembed, already in `health_service`) | labeled exemplars only, no training | semantic-router: threshold fitting 35% → 89% on its example; sub-threshold → None | **recommended first arm** |
| Tiny instruct LLM + GBNF enum (Qwen3 0.6–1.7B, Gemma 4 E2B) | 1–2 s CPU or a second GPU server | zero-shot | sub-2B zero-shot intent accuracy 0.48–0.76 on CLINC150 | the 2026-08 classifier again, smaller; no |
| Purpose-built router (laya-intent-router, 150M ONNX int8) | ~160 ms p95 CPU | pretrained | claims 94.9% routing / 98.6% OOS recall | a bench arm, not a default; claims unverified |
| hassil / template NLU | ms | templates | HA's own matcher | regex with nicer syntax; the blueprint already is this tier |
| Rasa / Snips | — | — | TensorFlow stack | tenet 4: no |

Measured on Kronk 2026-10-05: `fastembed` + `BAAI/bge-small-en-v1.5`
embeds one utterance in **3 ms** on CPU inside the health_service
container, 384 dims. No new model, no GPU, no `_llm_lock`.

## Update: decision models ("Jev" and the open replicas)

Operator pointer, 2026-10-05. All of this post-dates the assistant's
knowledge; facts below are from primary sources read that day.

- **Jev** is TypeSafe AI's hosted "System One" decision model (released
  2026-09-15): a *state* plus one or more *typed questions* (choice /
  score / yes-no) in, a probability per option out, one forward pass, no
  token generation. Post-trained with "RL for calibrated decisions" so
  the probabilities are meant to be honest. 70–500 ms hosted. Weights
  closed, API-only — **out for Kronk (tenet 1)**. TypeSafe's own limits
  list is relevant to routing: literal reading, one logical hop per
  question, irrelevant text distracts it, hostile input can steer it;
  confidence is not accuracy ("0.8 does not mean 80 percent").
- **Open replicas** (weeks old): OpenJev (27B, Qwen3.8 base, CC-BY-NC),
  AutoTrust JEV-9B/27B (Apache), Kev-0.8B/4B/9B (Qwen3.5 base, Apache,
  calibrated with one temperature), lev (4B, Apache), and two small
  encoders: **Julia-1 (144M, mmBERT-small, Apache)** and **Laya (421M,
  ModernBERT-large, Apache)**. Mechanism everywhere: read the logits of
  the option letters at the first output position, apply a fixed
  calibration, no decoding.
- **llama.cpp supports them natively**: PR #29818 merged 2026-10-02, first
  in build **b11361**, adds `/v1/systemone` (request: `state` +
  `questions{type, instructions, criteria}`; response: per-option
  `probabilities` + `confidence`, `output_tokens: 0`). ggml-org publishes
  official GGUFs: Julia-1 Q8 160 MB, Laya Q8 428 MB, Kev-0.8B Q8 774 MB,
  Kev-4B Q4 2.9 GB, lev Q4 2.9 GB, OpenJev Q4 17.6 GB. Latencies on an
  RTX PRO 6000: Julia-1 3 ms, Laya 5 ms, Kev-4B 12 ms, OpenJev 43 ms.
  We run b9611: using this is a **deliberate llama.cpp pin bump** (tenet
  3) — ROADMAP 19 already wants one for per-agent reasoning budgets, so
  one bump serves two items.

What the accuracy evidence actually says for *our* job (5 routes + a few
home sub-intents, short spoken utterances):
- OpenJev: 92.8% on its own intent/routing/topic set (1,218 items) — but
  its untuned base scored 93.2% on the same set; the tuning bought
  nothing there. 27B is too big next to E4B on the iGPU anyway.
- Julia-1: Banking77 zero-shot 64% (72 labels — harder than ours); typed
  Choice 71% on its own set; no public number for an 8-way router.
- Laya: card compares favourably to Jev on its own charts; the separate
  "laya-intent-router" claim (94.9% routing, 98.6% out-of-scope recall,
  160 ms p95 CPU) is the vendor's.
- Kev-4B: 0.69–0.84 across never-trained transfer sets. Kev-0.8B's card
  lists tool-call routing as **out of scope** (below chance on When2Call).
- Calibration does not transfer between models: the ggml-org post shows
  one vague query at 0.25 confidence on Julia-1 and 0.80 on Kev-4B.
  Thresholds must be fit per model on our eval set — same as kNN.
- Composite questions — the exact failure that removed the 2026-08 LLM
  classifier — are a documented weakness of the class ("one logical
  hop", "irrelevant text distracts"). A decision model would need either
  an abstain threshold that catches them or a second yes-no question
  ("does this request need more than one kind of information?") that
  routes to the coordinator when true.

Where they beat kNN: maintenance. A decision model is zero-shot from the
*option descriptions* ("home: weather, music, shopping list, solar, hot
tub, mirror…"), so there is no exemplar file to curate; a new phrasing
needs no edit at all if the description covers it. Where kNN beats them:
3 ms on CPU with no new server or pin bump, a mechanism with no
known composite blind spot (composites get their own `direct` class of
exemplars), and accuracy that comes from our own data rather than a
vendor's benchmark.

## Recommendation (revised)

Unchanged in shape: hybrid, pins first, learned router on the
fall-through, abstain to the coordinator, one seam (`_learned_router`),
traceable decisions. Changed: the bench now has decision-model arms, and
the bench picks — not a prior.

- Arms: regex-only (baseline) · regex+kNN (bge-small, in-process) ·
  regex+SetFit · regex+Julia-1 · regex+Laya · regex+Kev-4B. The decision
  models run through `/v1/systemone` on a **bench build** of llama.cpp
  (≥ b11361) on a bench port, the way the K2 fork was benched — no change
  to the production units until something passes.
- Same eval set, same metrics, same pass rule (wrong-lane ≤ baseline and
  0 on the named cases; coverage ≥ 50%; p95 < 50 ms). One extra named
  case family for the decision arms: the composite questions.
- Expected outcome, stated so it can be wrong: kNN passes on accuracy
  with our data volume; Julia-1 or Laya pass on latency and win on
  maintenance *if* they clear the composite cases. If a small decision
  model passes, it is the better long-term seam and the pin bump is
  justified; if only kNN passes, ship kNN and revisit decision models
  when their routing benchmarks are independent.

## Steps (revised)

1. Eval set + offline harness `scripts/routing_bench.py` (no production
   change; read-only against Langfuse and HA logs).
2. kNN arm, thresholds fit, report.
3. Decision-model arms: bench build of llama.cpp, Julia-1 / Laya / Kev-4B
   GGUFs on bench ports, a `/v1/systemone` client in the harness, the
   same thresholds-fit-then-test procedure, report. ~a day including the
   build.
4. Whatever passes: `_learned_router` behind the pins, tests, deploy,
   live battery, feature doc. A decision-model winner also means the
   llama.cpp pin bump (with ROADMAP 19) and one more systemd unit.

## Open decision for the operator

Go on step 1 (eval set + harness), or park.

## Results (2026-10-05, gate 1)

Eval set: 497 distinct utterances from Langfuse (June–October), 150
hand-relabelled to the lane that *should* answer (the coordinator had
"answered" weather and music it could not know; the 2026-08 LLM router had
sent weather to research). 363 first seen before 2026-09-01 are exemplars /
threshold-fitting data; 118 from September on are the test; 16 bench
prompts excluded. Named must-pass cases: 9. Coverage pool: the 26
home-labelled test items the pins leave to the coordinator (mostly
STT-mangled music phrasings from September).

| Arm | wrong-lane | named wrong | coverage | p95 ms | raw top-1 (no threshold) |
|---|---|---|---|---|---|
| regex (baseline) | 1 | 0 | 0/26 | 0.0 | — |
| regex + kNN bge-small, @0 / @2 / @5% exemplar error | 1 | 0 | 1 / 1 / 2 of 26 | 6–10 | — |
| regex + Julia-1 (144M) | 1 | 0 | 0/26 | 12 | 49% |
| regex + Laya (421M) | 1 | 0 | 0/26 | 28 | 39% |
| regex + Kev-4B (Q4, iGPU) | 1 | 0 | 0/26 | 165–180 | 60% |

The one baseline wrong-lane is the `search_phrase` pin sending a composite
("Convert the energy produced by the solar panels last week into dollars.
Look up the typical rates…") to research; every arm inherits it.

Why nothing passes:
- **kNN**: the exemplars contain near-duplicates with different lanes —
  "how much energy did my solar panels produce yesterday?" (home) beside
  "how much money's worth did the solar panels produce yesterday?"
  (direct, composite) at cosine ≈ 0.95. A threshold that keeps composites
  out of `home` is a near-duplicate threshold, and the test phrasings are
  new by construction, so coverage collapses to 1–2 of 26.
- **Julia-1**: answers "home" for almost everything at p ≈ 1.0 — 150 of 363
  exemplars wrong at p ≥ 0.9 ("What day is it today?" → home, 1.00). The
  probability carries no information on our labels, so no threshold works.
- **Laya**: ~0.35 for every option; calls nearly everything "direct".
- **Kev-4B**: the only one with usable calibration (two confident wrong
  calls on 363), but median p 0.52 when right versus 0.44 when wrong —
  not separable — and 165 ms p95, over budget on its own.

Caveats, so the verdict is not overstated: one draft of the lane
descriptions was used (re-wording them is the prompt-tuning path tenet 5
warns against, so it was not iterated); the test pool is small (26) and
hard (STT damage); kNN on the full test set does pin 1–2 new home items
correctly with zero new mistakes, which is the only positive signal here.

## Verdict

Keep the regex pins. New phrasings keep becoming pins with receipts, which
the music pin showed is a one-hour change with tests. Revisit when (a) the
eval set has ~100 more non-bench home items from voice, and (b) a decision
model publishes an independent short-utterance routing benchmark with
calibration numbers. The harness makes a re-run a one-command bench.
