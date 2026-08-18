# Coordinator-default routing — Plan (route to *how*, not *who*)

Status: **shipped 2026-08-18** (same day; all six build steps). Distilled
into `docs/features/coordinator-default-routing.md` — read that first.
Bench: coord-pre vs coord-post2 in `docs/bench/` (coord-post captured an
over-delegating intermediate prompt — see the feature doc's first gotcha).
Phase 2 (escalation terminal) remains deferred as designed.
Companion audit: the routing-shortcut precision pass (§ Shortcut audit) ships
first as its own step. Design discussion 2026-08-18 (operator + Claude),
grounded in *Agentic Design Patterns* (Gulli) chs. 2 (Routing), 6 (Planning),
7 (Multi-Agent / supervisor pattern) and the 2026-08-17 traces below.

## Motivating incidents (2026-08-17, orchestrator events)

- `7d62ffb3` — "how much money's worth have the panels produced so far
  today?" No shortcut matched ("panels" ∉ `_SOLAR_RE`); the gemma-3-4b LLM
  router picked **research**, which ran one generic `web_search` and
  confidently answered a question it had no data for. Wrong-lane answer.
- `908c1c89` — "Convert that to dollars…" routed to direct; the coordinator
  **had** the composition machinery and delegated to `ask_finance` for
  utility rates — plausible-but-wrong target choice at 4B-class.
- `066740ab` — "How about for the last 7 days?…" misrouted to direct, and
  the coordinator self-healed: `ask_home` → `solar_energy` → correct answer
  in 10.4 s. **The supervisor pattern works when it is reached.**
- 14:25–14:28 session — the operator manually orchestrated a composite
  (kWh × utility rate) across four separate prompts. The human was the
  coordinating agent; the architecture should be.

## Operator decisions (2026-08-18)

1. **Specialists stay pure** — no peer-delegation tools (rejected option:
   "peer agent handoffs"). Specialists answer their domain fast; they do
   not orchestrate.
2. **No per-use-case patches** (stored utility rate, one-off regex adds) —
   whack-a-mole; the next composite will be a different pair of domains.
3. **Full coordinator-first rejected** (every query through the
   coordinator) — latency tax on trivial queries; receipt in
   `TECH_DEBT.md` [ROUTING-01] considerations, 2026-06-12.
4. **Planner pattern rejected** — Gulli ch. 6's own rule of thumb: plan
   when the *how* must be discovered. Kronk's composites are known 2-hop
   shapes; a 4B-generated plan is a failure surface, not a guardrail.
5. **Shortcut philosophy: precision over recall** (see audit) — offenders
   removed rather than patched; the mirror update pin becomes the exact
   phrase "update the magic mirror".
6. **Escalation terminal deferred to Phase 2** — build when pinned
   composites measurably bite (see Known residual gap).

## Design

**The routing outcome set collapses to {deterministic shortcuts} ∪
{coordinator}.** The gemma-3-4b LLM classifier is deleted from the
pipeline. Any query no shortcut pins goes to the coordinator (today's
"direct" agent), whose `ask_*` menu (`agents.COORDINATOR`) becomes the one
routing surface. The coordinator answers directly, or delegates to one or
more specialists and synthesizes — this is Gulli ch. 7's supervisor
pattern, which Kronk already implements; the change is that every
non-shortcut query now reaches it.

**Why this is the right structure (the repricing argument):** under the
old design, a routing miss produced a *wrong answer* (an agent confidently
answering outside its lane). Under this design:

- a shortcut that fails to fire costs **seconds** (coordinator hop), and
- a shortcut that false-fires is the **only remaining wrong-lane class**
  — hence the precision audit.

Misroute-class failures become latency-class failures. Tenet 5 in routing
form: a 4B model can't pick the wrong specialist if specialists aren't on
its menu.

**Consequences:**

- The router LLM call (~0.5 s) disappears from every non-shortcut turn,
  and an entire failure class (classifier misroutes) is deleted. If
  nothing else uses gemma-3-4b, its systemd unit becomes decommissionable
  (frees ~4 GB GTT) — separate operator decision, not part of this plan.
- Traffic that used to LLM-route straight to a specialist (health,
  finance, shopping list, hot tub…) now pays the coordinator hop:
  **+5–8 s measured**. Frequent voice phrasings may earn *new, narrow*
  carve-outs later — only with bench/experience evidence, and only under
  the precision rule. Latency whack-a-mole is acceptable; correctness
  whack-a-mole is not.
- **`ask_*` tool descriptions become the load-bearing routing prompts.**
  Refinement channel for the whole design: describe *domain boundaries*
  crisply in the AGENTS dict (single source of truth), e.g. ask_finance:
  "your personal financial documents and positions — NOT market prices,
  utility rates, or anything needing a live lookup (that's ask_research)."
  Generalizes; enumerating use cases doesn't.
- Loop guardrails already in place cover the coordinator (delegation depth
  cap 2, exact-dup dedup, repeat-call nudge, forced synthesis). Expect the
  delegation *rate* to rise; re-measure June's spurious-delegation figure
  (was 1/5 on pure-knowledge prompts).
- `_DIRECT_OVERRIDE` ("don't search") still routes to the coordinator;
  known pre-existing nuance: nothing structurally stops the coordinator
  from calling `ask_research` anyway. Unchanged by this plan; note for a
  later hardening pass.

## Shortcut audit (agreed 2026-08-18)

**Precision rule:** a shortcut may pin only via
(a) a **named household entity**, (b) an **explicit user-stated method**,
or (c) **domain vocabulary with context**. Anything below that bar is
removed, not patched — its traffic falls to the coordinator.

| Shortcut | Verdict | Change |
|---|---|---|
| `_TALKIE_PHRASES` → talkie | keep | none (a-tier: named entity + intent verb) |
| `CLEAR_HISTORY_RE` (transport) | keep | none — the house gold standard of deliberate strictness |
| `_DIRECT_OVERRIDE` → coordinator | keep | none (b-tier: explicit method refusal) |
| `_URL_RE` → research | keep | none (b-tier: pasted URL = read it) |
| `_SEARCH_PHRASES` → research | narrow | `search` requires a mandatory qualifier (`search the web`, `search online`, `web search`); keep `look up`, `look it up`, `google`, `find me online/on the web/on the internet`, `news about`. **Remove:** bare `search`, `search for`, `what is the latest` (recency ≠ method). |
| `_WEATHER_RE` → home | narrow | **Remove bare `weather` and bare `forecast`.** Replace with contextual forms that keep the 2026-07-05 forecast-misroute fix and the high-frequency voice phrasings: `what('s| is) the weather`, `weather (like )?(today|tomorrow|this week)`, `(today|tomorrow)('s)? (weather|forecast)`, `forecast for (today|tomorrow|this week)`, `weather forecast`. ("AMD's revenue forecast", "weather the storm" no longer pin.) |
| `_SOLAR_RE` → home | narrow, then **REMOVED same day** | Narrowed in the morning; deleted in the evening after the pinned-composite gap bit twice and the escalation net measured 1-for-2 on identical inputs. Solar rides the coordinator entirely — see the feature doc's 2026-08-18-evening update. |
| `_MM_RE`/`_MM_UPDATE_RE` → home/devops | narrow | Update pin fires **only** on the exact phrase `update the magic mirror` (case-insensitive) → home's terminal update tool. All other "magic mirror" mentions → devops diagnostics (unchanged). Kills the misfired-update risk on questions *about* updates. |

**Proof harness (pre-written 2026-08-18):** the target behavior already
lives in `tests/test_routing_shortcuts.py` § "TARGET BEHAVIOR" as
`xfail(strict=True)` tests — 9 shortcut-precision cases (offenders that
must stop pinning, update-question phrasings that must stop reaching the
mirror's mutation tool) plus 2 route-collapse cases (unmatched queries →
coordinator with **no** LLM call, parameterized with the literal
rid-`7d62ffb3` phrasing). Suite is green today (355 passed, 11 xfailed).
`strict=True` means each build step **must** delete the markers it flips —
the flipped tests are the objective record of improvement. Nine companion
regression guards (weather/solar/search keepers, the exact mirror-update
phrase) already pass and stop step 1 from over-narrowing. The old "KNOWN
LIMITATION" pins that the plan obsoletes get deleted by step 1.

## Build steps + tests (each lands green before the next)

1. **Shortcut narrowing + test corpus.** Independent of the route
   collapse; ships first (single-variable change). All existing routing
   tests updated to the new corpus.
2. **Route collapse.** `routing.py`: unmatched → coordinator; delete the
   LLM-classifier path, `ROUTING_PROMPT` plumbing, and router-model
   metrics; emit `route_shortcut` as today and `route rule=default`
   for the coordinator fallthrough. Tests: routing unit tests + agentic
   loop suite.
3. **`ask_*` menu sharpening** in the AGENTS dict (incl. the
   finance-vs-live-data boundary from trace `908c1c89`).
4. **Bench.** `pipeline_bench.sh coord-pre` before step 2,
   `coord-post` after step 3 (clears chat history — confirm with the
   operator first). Re-measure spurious delegation on pure-knowledge
   prompts (gate: no worse than June's 1/5). Record per-route latency
   deltas in `docs/bench/`.
5. **Live verify** through the chat UI *and* the `/api/chat` shim (the
   HA/voice path), plus the manual voice utterance checks
   (`docs/features/voice-pipeline.md` recipe) — routing is
   pipeline-touching, tier-3 blast radius.
6. **Distill.** Update `docs/features/agents-as-tools-routing.md`
   (superseded sections), move the ROADMAP line to Shipped, retire the
   router model if decommissioned.

## Latency budget (tenet 12, from 2026-08-17 traces)

- Shortcut-pinned queries: unchanged (~3–7 s).
- Previously-LLM-routed single-domain: +≈4–7 s net (coordinator hop 5–8 s,
  minus the deleted router call ~0.5 s).
- Composite two-hop (e.g. kWh × rate): ~20–27 s, serialized under
  `_llm_lock` (no parallel fan-out on one GPU). Voice-marginal — same tier
  as today's research turns; acceptable per existing budget, stated here
  up front.

## Risks & rollback

- **Coordinator becomes the single routing judgment point** on
  gemma-4-E4B. Mitigations: sharpened `ask_*` menu, existing loop
  guardrails, bench gate before the old path is deleted.
- **Voice-latency regressions** on frequent unmatched phrasings.
  Mitigation: the carve-out process — narrow, evidence-driven, misses
  degrade to slow-not-wrong.
- **Rollback:** pure git revert; no data, schema, or config migration.

## Known residual gap → Phase 2 — **built 2026-08-18**

Same day as Phase 1: the gap bit within hours (trace `02e8b817`).

**As built:** `escalate` terminal tool + `run_stream(allow_escalation=)` +
pipeline fall-through to the coordinator (reusing the specialist-failure
seam, honestly labeled as escalation, NOT a pipeline error). Verified
live: the stranded trace's exact phrasing now answers ("29.9 kWh × 16.18 ¢
= $4.83", 18.5 s, rid `1e6d1603`); in-domain pinned queries do not
escalate (status 3.1 s, kWh 3.5 s, `escalated: false`); 0 spurious
escalations across the esc-post bench battery; 3 new tests (loop terminal,
tool-visibility gating, pipeline note-carry). Original design follows.

Narrowed pins can still strand composites: "how much money did my **solar
panels** save today" pins to home, which cannot answer the rate half. The
agreed Phase-2 mechanism (build when this measurably bites): a generic
**escalation terminal** — any specialist may end its turn declaring the
request out of (or beyond) its domain; the loop re-enters at the
coordinator carrying the specialist's partial answer, once, never
re-pinning the same specialist that turn. Not a peer tool: no specialist
learns about other agents; declining out-of-domain work *is* specialist
behavior. Until then, the operator-visible symptom of this gap is a
partial answer from home — watch for it in Langfuse under shortcut-pinned
rids with unanswered sub-questions.
