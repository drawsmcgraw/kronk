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

1. **Docs reorganization** *(this work)* — this file; status headers on all
   `docs/plans/*`; new `docs/features/` back-filled for voice, music,
   telemetry, and agents-as-tools routing; stale README/TECH_DEBT entries
   rewritten (README still describes the abandoned direct-to-Kronk voice
   design; TECH_DEBT `LITELLM-01` predates the 2026-07-03 hook fix).
   *Why: "where is our roadmap?" had six answers; blog posts need organized
   receipts.*

2. **Verbose voice/music error reporting** — audit every layer's error path
   (HA pipeline → ollama shim → router → agent loop → tool_service) so a
   failure speaks/returns the most specific detail available instead of
   "an unexpected error occurred", and every spoken failure is findable in
   Langfuse in one step. tool_service already returns good detail
   ("player may be powered off", "provider may need re-authentication") —
   find where it gets swallowed. Short plan doc first: "as verbose as
   reasonable" differs for voice (one clear sentence) vs chat UI (can show
   a trace link). *Why: can't troubleshoot what you can't see.*

3. **Timers via HA native intents** — verification task, likely not a build:
   HA Assist has built-in timer intents and the Voice PE runs timers
   on-device; `prefer_local_intents` is already on. Confirm "set a timer for
   10 minutes" is caught locally and never falls through to Kronk's router.
   Then execute the decommission checklist from the 2026-05-28 decision
   (`docs/VOICE_SETUP.md` → Open items): delete the broken announce
   automation, Kronk's old `set_timer` tool, the `/timer` route, and the
   `timer.voice_timer` helper. Document the result in `docs/features/`.
   *Why: kitchen timers are the #1 daily-driver voice feature.*

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

7. **MagicMirror agent** — Kronk updates the MagicMirror on voice command
   ("update the magic mirror"). MM runs on a Raspberry Pi (separate
   machine); direction chosen: **SSH from Kronk with a dedicated key and
   tightly limited authorization** (forced command / restricted shell —
   the key can do exactly one thing). Details TBD in
   `docs/plans/MAGICMIRROR_PLAN.md` — what "update" executes on the Pi,
   whether content flows Kronk→Pi or the Pi pulls from a Kronk endpoint
   backed by the context cache (item 5). Likely a `home`-agent terminal
   tool like `play_music`. *Why: first Kronk capability that reaches
   another machine; sets the pattern for doing that safely.*

8. **Voice regression smoke test** — script fires ~10 canned utterances
   through HA's `assist_pipeline/run` websocket and asserts which tier
   answered (local intent / MA blueprint / Kronk fallback) and
   success/failure shape. Run after any orchestrator/HA/MA change.
   *Why: three-tier routing changes silently; every layer broke
   independently during the music build. This is also the gate for
   item 9.*

9. **Upgrade cadence** — a deliberate, scheduled "update day" for HA, MA,
   Langfuse, and llama.cpp rebuilds, gated by the smoke test (item 8),
   instead of upgrading only when something breaks. MA 2.8.8 is already
   carrying a known ytmusicapi bug fixed upstream. *Why: drift accumulates;
   planned upgrades fail politely, forced ones don't.*

## Later — wanted, unscoped

- **Proactive Kronk** — announcements pushed to the Voice PE / other
  speakers (timer callbacks are the trailhead; laundry, hot-tub alerts,
  calendar reminders follow). Design whatever timer verification (item 3)
  reveals about HA's announce path.
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

## Stretch

- **Kronk self-description** — "Kronk, how do you work?" answered from live
  system knowledge, possibly with generated architecture diagrams. ~70%
  exists already (`kronk-context.md` + `get_kronk_context` tool +
  diagram-serving at `/static/generated/`); the gap is wiring it to an
  agent and keeping the context doc from drifting.

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
- Rewrite TECH_DEBT `LITELLM-01` — the hook now fires (`call_type` fix,
  2026-07-03); the entry describes a dead investigation.

## Shipped

Newest first; feature docs in `docs/features/` (once back-filled by item 1).

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
