# Kronk Roadmap

**This is the single source of truth for "what's next."** If a feature idea,
open item, or half-plan isn't on this page (or linked from it), it isn't on
the roadmap. The README's Roadmap section points here; `TECH_DEBT.md` tracks
what's *wrong* (this page tracks what's *wanted*); `docs/plans/` holds design
docs; `docs/features/` holds docs for shipped features.

Conventions:
- **Admission (operator, 2026-11-04):** an item goes on this page only if it
  matters and is worth doing. A defect found in passing is fixed, recorded in
  its incident doc, or dropped; it is not parked here. Pruned 2026-11-04 from
  57 open entries to 14.
- An entry is at most a few lines: what, status, why, a link. History and
  evidence live in the plan and incident docs.
- **Now** holds only work in flight. Items move Later → Next → Now → Shipped;
  anything bigger than a day gets a plan doc in `docs/plans/` before build.
- When something ships: distill the plan/journal into `docs/features/<name>.md`
  (including a "blog hooks" section), mark the plan doc shipped, move the line
  here to Shipped, and add/refresh the `docs/BLOG_TOPICS.md` entry.
- Entries are no longer numbered. Older docs that cite "item N" refer to the
  page before the 2026-11-04 prune (commit `3bfabe8`).

---

## Now — in flight

*(Nothing in flight.)*

## Next — agreed, not started

- **Music selection refinement** — Kronk plays what was meant, not the
  literal words. Done 2026-11-08: the household's stations imported to the
  Kronk Pandora account; blueprint change 11 asks MA for `"<name> Radio"`
  (owned station = exact library hit) and hands a rejected radio play to
  Kronk's station scoring; Kronk reads "radio" and the station name from the
  user's sentence, not the model's arguments. MA's `offer_new_stations` is
  on (operator, 2026-11-08: Kronk must be able to create stations, wrong
  ones accepted as the cost). Next: creation only on an explicit ask ("make
  a station from X") — the blueprint plays owned stations from
  `music_assistant.get_library` and hands everything else to Kronk, Kronk
  plays a seed only on an explicit create. No new MA code; the song-title seed ranking
  goes upstream (`INVESTIGATION_2026-11-07_ma_pandora_and_ui.md`).
  *Why: four radio misfires in three weeks, two of them wrong successes.*
- **Backups** — nightly copies of the HA config volume, the MA volume, the
  orchestrator SQLite, tool_service `/data` (`solar.db` is the only copy of
  past production history) to the NAS or a second
  disk. Only hand-taken MA tarballs exist today. Tempo's `tempo-data`
  volume (traces, 90 days) is a candidate too.
  *Why: there is no second copy of anything.*
- **Reclaim storage and memory** — disk: the Ollama blobs
  (`/usr/share/ollama/.ollama/models/blobs/`, 241 GB, unused since the
  llama.cpp migration) and unused GGUFs in `/opt/models`. GPU memory: retire
  the unassigned `llama-bonsai` (6.1 GiB GTT) and the dead llama units
  (mistral-nemo, devstral Q8, talkie). One careful look before each delete.
- **Voice accuracy** — track and improve STT accuracy on real household
  speech. Known misses: "Played the album…" (rid `2881890a`), "stop" →
  "What's up?", Satellite1 "pause" → "Pasta". Capture real utterances first,
  then bench candidates against them one at a time (Parakeet TDT via
  sherpa-onnx on CPU, already supported by the installed
  `wyoming-faster-whisper` 3.1.0; distil-large-v3.5; VAD filter). Switch only
  on fewer command-form errors at no worse p95 latency.
- **SearXNG stays healthy** — search must not silently degrade (a lapsed pin
  made every search junk for a month). Built 2026-10-05: weekly
  `kronk-searxng-canary.timer` and `docs/runbooks/searxng-bump.md`. Next: once
  the runbook has been run twice by hand, a monthly headless bump (`claude -p`
  limited to compose pull/up for searxng and the canary).

## Later — wanted

- **Financial expert, phase 2** — phase 1 (positions store, zero-question
  ingestion) shipped 2026-07-10. Next: retire_calc's FERS and Monte Carlo
  math as a tested library in finance_service, liquidity-gated withdrawal,
  then "am I on track?", what-if and bridge-strategy tools.
  `docs/plans/FINANCIAL_EXPERT_PLAN.md`. *Why: early retirement is the goal
  all of this serves.*
- **Shopping list** — HA owns the list, Kronk speaks and shows it, an
  encrypted read-only copy on Netlify for the family, no inbound traffic.
  Approved 2026-09-17, parked 2026-09-19. Includes one planned HA restart.
  `docs/plans/SHOPPING_LIST_PLAN.md`.
- **Per-client web UI sessions** — every browser shares the one `webui`
  session today. Operator decision pending: per device, per conversation, or
  threads shared across devices. *Why: two devices on one thread interleave.*
- **Instant Pot cook times** — "how long for black beans?" answered from a
  curated local table behind a home-agent tool, not model recall or web
  search. Honest "not in my table" fallback.
- **Health RAG completion** — `query_bloodwork` and `search_health_data`
  exist in `orchestrator/tools.py` but are wired to no agent; the
  `health_service` parsing, chunking and vector store are in place.
- **MagicMirror read-only investigation** — the devops agent answers
  questions about the mirror through the audited read-only `remote_exec`
  path and makes no changes. Open, in order: pick the model (Ministral Q4
  next; `docs/bench/devops_bench_2026-09-14_*`), fix the ops classifier
  quirks (`git -C`, quoted pipes, `journalctl --user`), voice-shaped answers,
  a test battery, satellite routing.
- **Wi-Fi visibility for the satellites** — a ping-history probe for the
  named satellites, plus HA's UniFi Network integration on the read-only
  account in `.env`. *Why: the 2026-09-30 stutter took a console login and
  three rounds of pings to diagnose* (`INVESTIGATION_2026-09-30_kitchen_stutter.md`).

## Chores

- **Dirty MagicMirror modules** — the 2026-10-24 update skipped
  MMM-DarkSkyRadar and MMM-RAIN-RADAR as dirty. Investigate read-only via
  `/ops/exec`; any change on the Pi needs the operator's per-action go.

## Shipped

Newest first; feature docs in `docs/features/`.

- **Telemetry v2** *(2026-11-08)* — every interaction is one OpenTelemetry
  trace: orchestrator pipeline → agent rounds → LLM calls → tool calls,
  continued into tool_service (MA websocket commands, HA service calls, the
  playback-verify wait) by `traceparent`; HA's voice runs (STT, intent, TTS)
  and the music blueprint's runs read by the new `voice_tracer` and linked
  to Kronk's trace by satellite device + time window. Grafana Tempo (90-day
  retention) + Grafana with four provisioned dashboards replace Langfuse
  (six containers, 131 GB of volumes left for the operator to delete).
  `kronk.events` lines carry `trace_id`. See `docs/features/telemetry.md`,
  `docs/plans/TELEMETRY_V2_PLAN.md`, `docs/plans/TELEMETRY_V2_REPORT.md`.
- **Music that knows the household** *(2026-10-21)* — named requests resolve
  through search + scoring (misspellings, STT, provider hints, YouTube Music
  links); genre requests ("some deep house", "something chill") pick from
  household taste learned nightly from MA play history, Pandora stations
  and the NAS library, plus an operator-editable `data/music_taste.yaml`
  that "we love X" / "never play X" also write; "not that" plays the next
  pick; skips/stops/long listens on Kronk's picks feed back. The HA music
  blueprint (change 9) hands loose phrasings and failed named plays to
  Kronk. Subsumes the "media_type miss" chore. See
  `docs/features/music-taste.md`, `docs/plans/MUSIC_TASTE_AGENT_PLAN.md`.

- **Pandora station creation** *(2026-10-21; search-cache fix in kronk.9)* —
  "play X radio" creates the Pandora station when it doesn't exist, through
  a patched MA provider in the derived image. Upstream PR pending.
  `docs/plans/PANDORA_CREATE_STATION_PLAN.md`.
- **Music provider canary** *(2026-10-08)* — daily `kronk-music-canary.timer`
  alerts when YouTube Music, Pandora or the NAS provider stops reporting
  available (`INVESTIGATION_2026-10-06_youtube_cookie.md`).
- **Pandora thumbs by voice** *(2026-10-06; on MA's `set_favorite` since
  2026-10-16)* — "thumbs up/down" trains the playing station. See
  `docs/features/pandora-thumbs.md`.
- **SearXNG canary** *(2026-10-05)* — weekly quality and pin-age check with
  a phone alert, plus the bump runbook `docs/runbooks/searxng-bump.md`.
- **Skip on a Pandora station** *(2026-09-19)* — MA 2.11.0b2 serves stations
  as track queues, so `next` works. `INVESTIGATION_2026-09-19_pandora_skip.md`.
- **NAS library credited to the right artists** *(2026-09-15)* — MA's
  filesystem provider set to use track artists when the album-artist tag is
  missing; 910 of 1,063 albums had been "Various Artists".
- **Leaked thinking never reaches the user** *(2026-09-14)* — text a
  model produces in a round that ends with a tool call is retracted by
  the loop before it is spoken or stored (voice drops it, the web UI
  parks it in the stage log, delegations and history get the clean
  text; streaming API shims keep today's behaviour). Trigger: "pause"
  answered with "5. Construct the tool call…" when the reasoning cap
  closed the channel mid-thought. See
  `docs/plans/TOOL_ROUND_RETRACT_PLAN.md`; the cap itself is item 19.
- **Playback control + now-playing by voice (Kronk tier)** *(2026-09-10,
  2026-09-14)* — `control_music` (stop == pause, effect verified) and
  `now_playing` terminal tools on the home agent, origin-aware, with
  routing pins for bare verbs ("Stop!") and the "what song is this"
  questions; fork change 4 routes station-first phrasing ("Anime Pop
  Radio on Pandora") to MA as radio instead of HA's built-in
  search-and-play, which had been playing YouTube title matches. See
  `docs/plans/PLAYBACK_CONTROL_PLAN.md`, `docs/plans/MUSIC_ACCURACY_PLAN.md`.
- **Real errors reach the speaker** *(2026-09-05)* — the cause of a
  failed play was being dropped three times: HA's REST API answers a
  bare 500 for integration errors (the message lives only in HA's log),
  tool_service spoke a generic sentence, and the coordinator reworded
  the specialist's terminal sentence. Now tool_service calls HA services
  over the **websocket** (which returns the message), speaks it
  verbatim after still verifying playback, and a delegated specialist
  that ended on a terminal tool passes through the coordinator
  untouched (the news_brief rule generalized). Live: "Could not resolve
  Zorblax Fnordwave Nonexistent to playable media item" came out of the
  pipeline word for word. See `docs/plans/ERROR_SURFACING_PLAN.md`.
- **Synology NAS music in Music Assistant** *(2026-09-04)* — the host
  mounts `//atlas.local/music` read-only over SMB (root-only credentials
  under `/etc/kronk/`, `nofail` so a NAS outage never blocks boot) and
  binds it read-only into the MA container with `rslave` propagation;
  MA's local filesystem provider at `/media/nas`. No container
  capabilities — the in-container SMB feature stays off. Three read-only
  layers: NAS user, mount, bind. See `docs/plans/MUSIC_ASSISTANT_PLAN.md`
  Phase 6.
- **Gemma 4 E4B — July-2026 weight refresh** *(2026-09-14)* — Google
  re-issued every Gemma 4 checkpoint on 2026-07-15 (tool-calling and
  chat-template fixes; E4B +8/+6 on two agentic benchmarks); our QAT
  GGUF was the June file. New unsloth QAT UD-Q4_K_XL + MTP drafter,
  hash-verified, stored as `*-202607.gguf` beside the old files for
  rollback. Coordinator battery 28/28, 0 leaks, 112 tok/s (was 104),
  median reasoning 530 chars (was 712); devops battery unchanged
  (22/23). `systemd/llama-gemma4-e4b.service` and the live unit point
  at the new files; restarted and verified through `/api/chat`.
- **Model bench — K2 Horizon vs incumbents, no swap** *(item 11,
  2026-09-03)* — Gemma 4 12B tied E4B at half the speed; K2-Horizon-7B
  (IFM's llama.cpp fork, own quants) tied-minus-one at a quarter to a
  third of the speed and keeps its thinking out of the reply only at
  `reasoning_effort=high`; ties Devstral on the devops battery at 2.6×
  tok/s but equal wall-clock; answers more research questions than E4B
  but confabulated stale officeholders where E4B abstained. Harness
  `scripts/coordinator_model_bench.py` stays. Revisit when upstream
  llama.cpp parses K2's fast-thinking markers or IFM ships a draft
  head/QAT checkpoint. See `docs/plans/MODEL_BENCH_K2_HORIZON_PLAN.md`.
- **Voice music plays on the device that asked (Kronk tier)**
  *(2026-09-04)* — HA stamps the requesting satellite's device id and
  area onto the prompt it already sends (one Jinja line in the Ollama
  conversation instructions); the shim reads that line into a
  request-scoped origin (`orchestrator/origin.py`), the play tool passes
  it to tool_service, which resolves named player → named room → own
  device (MAC join) → own room → default, matching the blueprint fork.
  Verified live: "put on some aerosmith" from the office played in the
  office. Finding: HA's built-in `HassMediaSearchAndPlay` intent is a
  third, area-aware local tier ahead of both. See
  `docs/features/voice-music-control.md`,
  `docs/plans/VOICE_MUSIC_ORIGIN_KRONK_PLAN.md`.
- **Voice music plays on the device that asked (HA tier)** *(2026-09-04)*
  — a Kronk fork of the MA voice blueprint resolves named player → named
  room → **own device** (MAC join) → own room → default, prefers a room's
  sync group, and says "playing in the Office" instead of reading device
  names. Verified by pipeline runs as each satellite; the group rung
  awaits the second kitchen satellite. Maintenance line: re-apply the
  three marked changes when MA updates the blueprint. See
  `docs/features/voice-music-control.md`,
  `docs/plans/VOICE_MUSIC_DEVICE_FIRST_PLAN.md`.
- **Music players discovered from HA** *(2026-09-04)* — the compose-side
  player map is gone; the play tool asks HA for every Music Assistant
  player with its area and resolves spoken speaker/room in the MA
  blueprint's own order (name → area → origin area → default), so a new
  satellite only needs an area in HA. `origin_area` slot reserved for
  "play from the device that asked". See
  `docs/features/voice-music-control.md`,
  `docs/plans/MUSIC_PLAYERS_FROM_HA_PLAN.md`.
- **Solar dashboard** *(2026-08-27)* — `/solar` page: now-strip, power
  curve, daily energy bars, and the inverter-health heatmap with
  per-panel drill-down (1/7/30/90-day windows); `GET /solar/series`
  aggregation endpoint; zero new dependencies. Counter baseline shipped
  with it: every poll now snapshots `site_load_en`/`net_en` alongside
  `pv_en` — this install mirrors them (no consumption CTs), so
  consumption views are gated until the data is real, but history counts
  from today. See `docs/features/solar-viz.md`,
  `docs/plans/SOLAR_VIZ_PLAN.md`.
- **Render profiles** *(2026-08-25)* — canonical markdown inside, one
  render seam at the transport boundary: display (default) passes
  through, speech (`/voice` mount, an explicit client declaration)
  deterministically scrubs markdown for TTS. HA's Ollama integration
  re-pointed at `/voice` via storage edit (main entry not reconfigurable
  by API), so all voice devices ride the speech profile with no
  per-device or per-skill work; the news prompt's markdown suppression
  deleted. See `docs/features/render-profiles.md`,
  `docs/plans/RENDER_PROFILES_PLAN.md`.
- **News brief** *(2026-08-24)* — pre-generated editions (6am/noon/6pm)
  from 8 RSS feeds (world + tech/AI + cybersecurity), one LiteLLM
  summarize call, cached in tool_service and delivered VERBATIM by the
  coordinator's first terminal service tool — fixes the
  double-summarization tax (754-char briefs) and the confabulated-brief
  failure. Follow-ups by story name ride ask_research. 1.7 s delivery.
  See `docs/features/news-brief.md`, `docs/plans/NEWS_BRIEF_PLAN.md`.
- **Coordinator-default routing** *(2026-08-18)* — routing collapsed to
  narrow deterministic shortcuts or the coordinator; the gemma-3-4b LLM
  classifier deleted (a shortcut miss now costs seconds, never a wrong
  lane). Shortcut precision audit (bare solar/weather/forecast/search
  released; mirror update = exact phrase), `ask_*` menu sharpened as the
  routing surface, multi-domain composites now compose (kWh × rate in
  14 s — the 2026-08-17 misroute, fixed). Phase 2 (specialist escalation
  terminal) shipped the same day after the shortcut-stranded-composite gap
  bit within hours: pinned specialists can hand composites back to the
  coordinator (trace `02e8b817` → fixed, 29.9 kWh × rate = $4.83 in 18.5 s).
  See `docs/features/coordinator-default-routing.md`,
  `docs/plans/COORDINATOR_ROUTING_PLAN.md`.
- **Solar health + energy monitoring** *(2026-07-14 → 2026-07-17)* —
  SunPower PVS5 per-inverter failure detection (peer-ratio vs array median,
  3-consecutive-bad-days confirmation → one HA persistent notification per
  episode) plus `solar_status` / `solar_detail` / `solar_energy` tools on
  the home agent; energy history via 15-min lifetime-counter snapshots
  (the PVS keeps none of its own). Superseded the original PVS6-DeviceList
  sketch — the PVS5 varserver API turned out reachable via the bridge Pi.
  See `docs/features/solar-monitoring.md`,
  `docs/plans/SOLAR_MONITOR_PLAN.md`.
- **Timers via HA native intents** *(item 3, 2026-07-12)* — a spoken
  timer is caught by HA's local Assist intent and runs on the Voice PE
  on-device; Kronk's timer tool, route, and env were decommissioned.
  Operator leftover: the unused `timer.voice_timer` helper and the
  broken timer-finished announce automation in HA.
- **Financial expert, phase 1** *(2026-07-10)* — positions store with
  liquid vs age-gated accounts and zero-question ingestion (CSV, XLSX,
  statement PDF; LLM extraction behind a reconciliation gate).
  `docs/plans/FINANCIAL_EXPERT_PLAN.md`.
- **MagicMirror updates by voice** *(2026-07-06; repaired 2026-08-16 and
  2026-10-22)* — backup-then-update over a forced-command SSH key.
  `INVESTIGATION_2026-08-14_mm_banner.md`,
  `INVESTIGATION_2026-10-22_mm_core_lockfile.md`.
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
- **Langfuse telemetry v1** (2026-06-10) — prototype; retired 2026-11-08 by Telemetry v2.
- **Unified-streaming agent loop** — every agent streams token-by-token;
  `llm.stream()` accumulates `tool_calls` from deltas.
- **Agents-as-tools routing** (2026-06-12) — router misses self-heal via
  the coordinator's `ask_<agent>` tools.
- **Router → specialist → coordinator pipeline**, replacing regex intent
  detection.
- **Migration from Ollama to from-source llama.cpp** behind a LiteLLM
  proxy.
- **`query_health` tool + `/health` dashboard**; Infisical retired.
