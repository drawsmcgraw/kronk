# Kronk Roadmap

**This is the single source of truth for "what's next."** If a feature idea,
open item, or half-plan isn't on this page (or linked from it), it isn't on
the roadmap. The README's Roadmap section points here; `TECH_DEBT.md` tracks
what's *wrong* (this page tracks what's *wanted*); `docs/plans/` holds design
docs; `docs/features/` holds docs for shipped features.

Conventions:
- Items move **Later → Next → Now → Shipped**. Anything in **Now** or **Next**
  that's bigger than a day gets a plan doc in `docs/plans/` before build.
- When something ships: distill the plan/journal into `docs/features/<name>.md`
  (including a "blog hooks" section), mark the plan doc shipped, move the line
  here to Shipped, and add/refresh the `docs/BLOG_TOPICS.md` entry.
- Every entry says *why* in one line, so future-us doesn't have to re-derive it.

---

## Now — committed, in flight

*(Items keep their numbers when they ship — cross-references elsewhere in
the docs use them. 1 and 2 are in Shipped.)*

3. **Timers via HA native intents — DONE 2026-07-12.** Confirmed by live
   observation: a spoken 7-minute timer was caught by HA's local Assist
   intent and run on the Voice PE on-device — it created no `timer.*`
   entity, no logbook entry, and never touched Kronk (router/shim). The old
   Kronk timer code was then decommissioned (branch `decomm-timer`):
   `set_timer` tool + handler + `DEFAULT_TIMER_LABEL`, the tool_service
   `/timer` route + `TimerRequest` + `HA_TIMER_ENTITY` (compose env), and
   the home-agent wiring/prompt. `HA_URL`/`HA_TOKEN` kept (music + mirror
   announce). *Operator-side leftover to remove at leisure: the unused
   `timer.voice_timer` HA helper and the broken timer-finished announce
   automation — neither is referenced by any code now.*

4. **Backups** — nightly automated backup of the irreplaceable state: HA
   config volume, MA library/auth volume, orchestrator SQLite (sessions,
   metrics, shopping list), Langfuse Postgres/ClickHouse (or accept
   telemetry as disposable — decide). Target a second disk or the NAS.
   *Why: "never `down -v`" is a rule because there is no second copy of
   anything. One bad disk erases the project. Cheapest risk-kill on this
   page.*

## Next — agreed, not started

5. **Context/fact cache** — a small keyed store (SQLite table in the
   orchestrator, or in-memory in tool_service) of low-volatility facts with
   per-key TTLs: weather (~15 min), calendar, news top-of-feed, kronk
   context. Written by fetchers, injected into agent prompts by *one* code
   path. Replaces the hand-edited-prompt weather cache, subsumes the README's
   old "tool-result cache" sketch, and is a prerequisite for MagicMirror
   (the mirror wants exactly this data). No Redis — wrong scale.
   *Why: prompt-editing as a cache doesn't scale past one fact.*

6. **Telemetry v2** — trace **every** interaction (chat UI, voice, shims)
   end-to-end, serving two masters: troubleshooting (find the trace for
   "that thing Kronk just said" in one step — pairs with item 2) and usage
   analysis (which agents/tools/phrasings actually get used, tier hit-rates
   for voice, latency percentiles over time). Today's Langfuse setup is a
   **prototype — throwing it away is on the table.** Start with a
   requirements pass: retention, what a "usage report" should answer,
   whether Langfuse v3 still fits or something lighter/heavier serves
   better. Plan doc required. *Why: troubleshooting and pattern analysis
   both depend on it; better to re-found it now than accrete on a
   prototype.*

7. **MagicMirror agent** — **tier 1 BUILT 2026-07-06** (branch
   `magicmirror-updater`): `update_magicmirror` terminal tool → tool_service
   SSH (forced-command key, user kronk, sudoers pinned to one script) →
   full-backup-then-update on the Pi, async ack + `/magicmirror/status`.
   Awaiting Pi-side setup (operator steps in
   `docs/plans/MAGICMIRROR_PLAN.md`) and live test. Tier 2 (devops agent
   with allowlisted verbs — status/logs/restart/screen/config) comes after;
   model bench done, devstral retained. *Why: first Kronk capability that
   reaches another machine; sets the pattern for doing that safely.*

8. **Voice regression smoke test** — script fires ~10 canned utterances
   through HA's `assist_pipeline/run` websocket and asserts which tier
   answered (local intent / MA blueprint / Kronk fallback) and
   success/failure shape. Run after any orchestrator/HA/MA change.
   Always runs with `ERROR_STYLE=debug` — its deliberate-failure
   assertions expect specific detail (operator decision 2026-07-05).
   *Why: three-tier routing changes silently; every layer broke
   independently during the music build. This is also the gate for
   item 9.*

10. **Financial expert** *(added 2026-07-07; plan approved-in-conversation,
   `docs/plans/FINANCIAL_EXPERT_PLAN.md`)* — the finance agent learns the
   operator's actual investment positions in service of early retirement:
   positions store with liquid-vs-age-gated as a first-class distinction,
   format-agnostic monthly-export ingestion (LLM maps columns once,
   deterministic upsert extraction forever), absorption of retirement-calc's
   validated math (FERS matrix, Monte Carlo) as a tested library with
   liquidity-gated withdrawal, then chat tools: "am I on track?",
   "my retirement number", what-ifs, and bridge strategies (Roth ladder,
   72(t), Rule of 55). retire_calc app is retired at parity. *Why: the
   actual goal all of this serves — early retirement — deserves the same
   engineering as the plumbing.*

9. **Upgrade cadence** — a deliberate, scheduled "update day" for HA, MA,
   Langfuse, and llama.cpp rebuilds, gated by the smoke test (item 8),
   instead of upgrading only when something breaks. MA 2.8.8 is already
   carrying a known ytmusicapi bug fixed upstream. *Why: drift accumulates;
   planned upgrades fail politely, forced ones don't.*

## Later — wanted, unscoped

- **Solar system health monitoring** — proactively alert when any part of
  the SunPower PV system is *failing* (motivating incident: a failing
  inverter found only by chance). Poll the PVS6 `DeviceList` API per-device
  (not the aggregate gauge the mirror shows), detect dead/missing/outlier
  inverters, alert via announce + a sticky channel. Plan +
  research in `docs/plans/SOLAR_MONITOR_PLAN.md`. First consumer of the
  announce primitive beyond the mirror; gated on PVS network reachability
  from the Kronk box.
- **Proactive Kronk** — announcements pushed to the Voice PE / other
  speakers (timer callbacks are the trailhead; laundry, hot-tub alerts,
  calendar reminders, solar-failure alerts follow). Design whatever timer
  verification (item 3) reveals about HA's announce path.
- **External access + auth** — the real question behind "publish the
  shopping list off-network." Decide the posture once (Tailscale sidesteps
  most of it) before any endpoint goes public.
- **Health RAG completion** — `query_bloodwork` / `search_health_data`
  tools exist in `orchestrator/tools.py` but are wired to no agent;
  `health_service` parsing/chunking/vector-store code is in place.
- **Secrets management rebuild** — the Infisical retirement left Garmin
  and Withings sync as no-op stubs; current plan is per-service
  `/data/<service>_tokens.json` bind mounts. Unblocks the health sources.
- **More integrations** — Philips Hue, calendar, Fitbit (family member),
  Withings scale.
- **More expressive TTS** — effort-ordered options already scoped in the
  README/`docs/VOICE_SETUP.md`: different Piper voice → voicebox.sh →
  XTTS-v2 on gfx1151 → Bark.
- **STT accuracy quick wins** — enable faster-whisper `--vad-filter` and/or
  relax the Voice PE's `finished_speaking_detection` if empty
  transcriptions on borderline audio start to bite (~30 min each,
  low-risk; from `docs/VOICE_SETUP.md`).
- **Synology NAS music** — MA's local-files/SMB provider, Phase 6 of
  `docs/plans/MUSIC_ASSISTANT_PLAN.md` (may need the elevated container
  caps we deliberately skipped at MA install).
- **Peer agent handoffs** — a multi-domain query routed to a *specialist*
  still gets a single-domain answer; agents-as-tools fixed this for the
  coordinator path only. Attack if it bites in practice. See
  `TECH_DEBT.md` [ROUTING-01].
- **Voice latency program** — the Kronk fallback tier runs 15–25 s, the
  edge of tolerable. Treat as a standing constraint on new voice features;
  attack when it bites (candidate levers: context cache, smaller/faster
  routing, Voxtral when unblocked).
- **Ollama blob reclaim** — delete `/usr/share/ollama/.ollama/models/blobs/`
  (~50+ GB) now that llama.cpp is stable. Chore; needs one careful look
  first.
- **Productize Kronk** - Kronk can be an open source project to allow 
  people to run their own local AI server. Investigate what we need to
  to (configs, parameterizations, etc) to support this.

## Stretch

- **Kronk self-description** — "Kronk, how do you work?" answered from live
  system knowledge, possibly with generated architecture diagrams. More
  built than first thought: the `assistant` agent is already wired with
  `get_kronk_context` + `generate_diagram` (2026-07-05 review). The real
  remaining gap is keeping `kronk-context.md` from drifting — it's
  hand-maintained (tenet 8 violation waiting to happen) — plus routing
  quality into that agent.

## Deferred / parked — with revisit conditions

- **Voxtral STT** — no gfx1151 PyTorch/vLLM wheels. Revisit when AMD ships
  wheels, `wyoming-voxtral` appears, or llama.cpp adds Voxtral support.
  Full rationale in `docs/VOICE_SETUP.md`.
- **Hot tub monitor** — parked 2026-06-12; spa pack unreachable. See
  `TECH_DEBT.md` [HOTTUB-01].
- **Deliberately rejected** (per-domain tool services, SQLite pooling,
  Redis, etc.) — see `TECH_DEBT.md` "Considered and rejected."

## Chores / quick wins

- Rename MA player "Sonos Move Derp" → "Sonos Move" in the MA UI so the
  blueprint fast path resolves natural phrasing (entity_id is unchanged;
  nothing else moves).
- Operator kitchen voice tests — real "Okay Nabu" music commands from the
  Voice PE (the one untested layer of the 2026-07-03 music work).
- Backfill tests for the 2026-07-03 fixes — routing-history merge/drop
  (`routing.py`), terminal-tool turn-ending (`agents.py`), hooks.py
  `call_type` normalization. They shipped before the definition-of-done
  rule existed; each is a regression waiting for cover.

## Shipped

Newest first; feature docs in `docs/features/`.

- **Verbose error reporting** *(item 2, 2026-07-05)* — every layer surfaces
  its most specific failure cause; failed turns marked ERROR in Langfuse;
  "an unexpected error occurred" is now a bug by tenet. Includes the
  `ERROR_STYLE` toggle (debug now, friendly later — rendering only, capture
  always full; `ERROR_STYLE_VOICE` overrides per transport). With the P0
  correctness batch and the forecast-misroute fixes (weather routing
  shortcut, repeat-tool-call guardrail, research budget 5→8) from the same
  review. See `docs/features/verbose-errors.md`,
  `docs/incidents/INVESTIGATION_2026-07-05_forecast_misroute.md`.
- **Docs reorganization** *(item 1, 2026-07-05)* — this file as single
  source of truth; `docs/features/`; status headers on all plan docs;
  engineering tenets + definition-of-done + incident rule in `CLAUDE.md`.

- **Voice music control** (2026-07-03) — two-tier: MA's local-intent
  blueprint catches strict "play the artist X on Y" grammar in ~2 s; fuzzy
  requests fall through to Kronk's `home` agent + `play_music` terminal
  tool. Also fixed the voice-path router 400 (HA local-intent fallback
  sends non-alternating history; LiteLLM's normalize hook was dead —
  `call_type` mismatch).
- **Voice pipeline** (2026-05) — HA Voice PE → Home Assistant broker →
  Wyoming faster-whisper STT (host, GPU) / Piper TTS (container) → Kronk
  via the Ollama shim. Build journal: `docs/VOICE_SETUP.md`.
- **Langfuse telemetry v1** (2026-06-10) — prototype; see item 6.
- **Unified-streaming agent loop** — every agent streams token-by-token;
  `llm.stream()` accumulates `tool_calls` from deltas.
- **Agents-as-tools routing** (2026-06-12) — router misses self-heal via
  the coordinator's `ask_<agent>` tools.
- **Router → specialist → coordinator pipeline**, replacing regex intent
  detection.
- **Migration from Ollama to from-source llama.cpp** behind a LiteLLM
  proxy.
- **`query_health` tool + `/health` dashboard**; Infisical retired.
