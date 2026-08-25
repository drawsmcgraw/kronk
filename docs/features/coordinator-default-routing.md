# Feature: Coordinator-default routing (route to *how*, not *who*)

**Shipped:** 2026-08-18 · **Origin:** `docs/plans/COORDINATOR_ROUTING_PLAN.md`
(design record — operator decisions, the shortcut audit, rejected
alternatives) · **Motivating incident:** trace `7d62ffb3`, 2026-08-17 —
"how much money's worth have the panels produced so far today?" LLM-routed
to research, which confidently answered a solar question from one generic
web search. Supersedes the LLM-router half of
`agents-as-tools-routing.md`.

## What it does

Routing is now **narrow deterministic shortcuts, or the coordinator —
nothing else**. The gemma-3-4b LLM classifier is gone. A shortcut pins a
query to its specialist only on a named household entity, an explicit
user-stated method, or domain vocabulary with context; every unmatched
query goes to the coordinator, which answers directly or delegates via its
`ask_*` tools (Gulli's supervisor pattern) and synthesizes.

**The repricing:** a shortcut miss now costs seconds (one coordinator
hop), never a wrong lane. Shortcut false-fires are the only remaining
wrong-lane class — which is why the shortcuts were narrowed in the same
change (bare `solar`/`weather`/`forecast`/`search`/"what is the latest"
removed; the mirror-update pin is the exact phrase "update the magic
mirror"). Tenet 5 in routing form: a 4B model can't pick the wrong
specialist if specialists aren't on its menu.

## Verified behavior (2026-08-18, live)

- **The motivating query now works end-to-end** via the HA shim:
  route `rule=default` → coordinator → `ask_home` → `solar_energy` →
  `ask_research` → `web_search` → synthesis with arithmetic
  ("3.0 kWh × 16.18 ¢ = $0.49") in **14.3 s** (rid `925778a4`).
- **Bench** (`docs/bench/`, coord-pre vs coord-post2): direct queries
  *faster* (2.74 → 2.18 s — the router call is gone); shortcut-pinned
  weather unchanged; previously-LLM-routed home traffic +≈3 s
  (shopping list 1.89 → 4.76 s) — the accepted trade.
- **Spurious delegation**: 0/3 on the pure-knowledge probe ("What time
  zone is Denver in?") after prompt rebalancing — better than the June
  baseline of 1/5.
- Proof harness: 11 pre-written `xfail(strict=True)` tests in
  `test_routing_shortcuts.py` flipped to permanent pins as the steps
  landed; suite 361 passed.

## Update 2026-08-18 (evening): the solar topic pin is gone

Removed hours after being narrowed: solar questions are frequently
composite, a topic regex can't see compositeness, and the escalation net
under the pin proved probabilistic (1-for-2 on identical inputs, traces
`1e6d1603`/`143bbc62`). All solar queries now ride the coordinator.
**Calibration** (repeated identical prompts, history cleared between runs):

- composite "money's worth yesterday": **5/5** composed (kWh × rate),
  median 13.7 s
- single-domain status: **5/5**, median 7.5 s (was ~3 s pinned — the
  accepted hop)
- pure kWh: **3/3**, median 6.6 s; pure-knowledge control: **3/3**, no
  spurious delegation, median 2.0 s

Distilled lesson: **METHOD pins** (user states the how: URL, "search the
web", "don't search", "ask talkie", "update the magic mirror") are sound;
**TOPIC pins** survive only on frequency + simplicity grounds (weather —
on probation by the same test if composite usage appears; mirror→devops).

Watch item found by calibration, independent of routing: 2/8
yesterday-answers summed yesterday+today (home chose
`solar_energy(since:<date>)`, which runs through the present, instead of
`period=yesterday`). Candidate one-line fix in the `solar_energy` tool
description; not yet applied.

## Update 2026-08-25: the stage-log regression, and labels-in-payload

The routing collapse silently killed the web UI's "thinking" indicators
for delegated queries: delegation moved from the pipeline's Phase-2 path
(which emitted `stage: fetching_delegate_<agent>`) into the coordinator's
`run_stream` loop, which had no stage vocabulary — only the transient
narration line, cleared at first token. Undetected for a week because
delegation testing ran through the shims, which render no stages.

Fix, two parts:

1. `run_stream` yields `{"type": "delegating", "agent": …}` when an
   `ask_*` call starts; the pipeline maps it to the stage event the UI
   already renders.
2. **Stage/timing display text is authored at the emitter** and shipped in
   the payload (`stage_label`; timing entries carry `label`/`service`).
   The UI's two client-side lookup tables are deleted — they drifted (one
   still labeled a `fetching_delegate_assistant` agent that doesn't
   exist) and taxed every new tool/agent with a second edit in a second
   file. Generic server-side fallbacks mean future agents label
   themselves ("newagent agent working...") with zero UI changes.

## Gotchas

- **The coordinator prompt is a balance, not a list.** First deploy
  over-delegated ("What time zone is Denver in?" → research, 3/3) because
  the new multi-part guidance lacked a counterweight; fixed by pairing it
  with explicit answer-directly examples. Tune both sides together —
  `ask_*` descriptions (AGENTS dict) and the coordinator system prompt are
  the entire routing surface now.
- **Voice latency carve-outs are earned, not preemptive.** Frequent
  phrasings that fall to the coordinator (+≈3–7 s) may get new narrow
  shortcuts, but only with bench/trace evidence — misses are slow, not
  wrong, so there's no urgency.
- gemma-3-4b no longer serves routing; decommissioning its systemd unit
  (~4 GB GTT) is a pending operator decision.
- ~~Phase 2 (deferred)~~ — **built 2026-08-18, same day**: the gap bit
  within hours of shipping (trace `02e8b817` — "money's worth did the
  **solar panels** produce" pinned to home, which answered nothing useful).
  The **escalation terminal** closed it: every top-level pinned specialist
  gets a generic `escalate` tool (never on delegated or coordinator runs —
  once-per-request by construction); calling it ends the specialist's turn
  and the pipeline falls through to the coordinator with the specialist's
  note, riding the existing specialist-failure seam but labeled honestly
  and not marked as an error. Verified: the stranded phrasing now answers
  (29.9 kWh × 16.18 ¢ = $4.83, 18.5 s, rid `1e6d1603`); in-domain pinned
  queries don't escalate (3.1–3.5 s, `escalated: false`); 0 spurious
  escalations across a full bench battery. Watch item: over-escalation is
  behavioral, not structural — the `escalate`/`agent_complete escalated=`
  events make it measurable in Langfuse.

## Blog hooks

- Fire your router: the classifier that misroutes 1-in-N is worse than no
  classifier plus a supervisor that's always reachable.
- Repricing failure classes: turning wrong-answer bugs into
  latency costs, and why that's the right trade for a home assistant.
- Test-first ops on a hobby box: xfail(strict=True) as a proof harness —
  the improvement isn't real until the marker won't stay on.
- A 4B coordinator can orchestrate — if the loop, not the model, owns the
  budget (3 rounds, forced synthesis, structural depth cap).
