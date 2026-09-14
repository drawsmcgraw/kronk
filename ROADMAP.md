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
the docs use them. 1, 2, 3, 11 and 12 are in Shipped. Pruned 2026-09-14.)*

4. **Backups** — nightly automated backup of the irreplaceable state: HA
   config volume, MA library/auth volume, orchestrator SQLite (sessions,
   metrics), tool_service `/data` (**solar.db** — the energy-counter
   snapshots are the ONLY copy of past production history, the PVS keeps
   none; plus shopping list, news state, mm-update state), Langfuse
   Postgres/ClickHouse (or accept telemetry as disposable — decide).
   Target a second disk or the NAS.
   *Why: "never `down -v`" is a rule because there is no second copy of
   anything. One bad disk erases the project. Cheapest risk-kill on this
   page.*

## Next — agreed, not started

13. **STT accuracy bench — Whisper alternatives and knobs** *(added
    2026-09-04; research done, bench not started — operator decision to
    park)*. Trigger: "play the album Toys in the Attic…" transcribed as
    " Played the album Toys in the Attic." (rid `2881890a`) — missed
    every local tier and read to Kronk as a statement. The error is
    architectural: Whisper's autoregressive decoder is a language model
    and prefers likelier English over bare imperatives; large-v3-turbo
    keeps 4 of 32 decoder layers, so short context-free commands are
    where it drifts. Findings:
    - The Wyoming server we run (`wyoming-faster-whisper` 3.1.0) already
      ships sherpa-onnx and onnx-asr handlers and its own auto-select
      **prefers NVIDIA Parakeet TDT 0.6B via sherpa-onnx for English**
      when that library is installed; our unit pins Whisper explicitly.
      Parakeet is a transducer (no LM decoder → literal transcripts),
      punctuates and capitalizes, tops the Open ASR leaderboard for
      English, runs on **CPU** (`provider="cpu"` in the handler) — which
      would take STT off the iGPU and the HIP runtime entirely. One
      `pip install sherpa-onnx` + `--stt-library sherpa --model
      sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8 --device cpu`.
    - Cheaper knobs on the incumbent: `--initial-prompt` in the command
      register ("Play the album. Play the artist. Shuffle the playlist.
      Pause. What's the weather?") — steers the prior, can pull words on
      noise; `distil-large-v3.5` (CT2 drop-in, English, slightly better
      short-form than turbo); full `large-v3` (+1–2 WER points, ~4×
      slower). HA-side completeness knob: satellite "finished speaking
      detection" (relaxed = fewer clipped endings; not tense).
    - Ruled out: Speech-to-Phrase (constrained grammar, one STT per
      pipeline → would remove Kronk's open-vocabulary tier);
      Canary-Qwen / Voxtral (LLM-class, GPU contention with Kronk's
      models); streaming models (latency, not accuracy).
    **Method (pre-committed):** real audio first — a Wyoming tee proxy
    on a bench port in front of the unchanged Whisper for a day captures
    every real utterance + production transcript (Piper-synthesized
    utterances only as a controlled corpus); each candidate runs on a
    bench port against the same WAVs, production untouched; metrics =
    WER, command-form check (imperative verb + entity names preserved),
    p95 latency; single variable per run. **Rule:** switch only on fewer
    command-form errors on real audio with p95 latency no worse than
    today; the switch itself is one flag change on the unit + restart,
    old unit file kept for rollback. Folded in 2026-09-14: the cheap
    knobs (`--vad-filter`; relax the satellites' "finished speaking
    detection" if clipped endings bite) and the Voxtral revisit
    condition (no gfx1151 PyTorch wheels; watch for `wyoming-voxtral` or
    llama.cpp support — rationale in `docs/VOICE_SETUP.md`). *Why: it
    sits in front of all three music tiers and every other voice
    request; two mistranscriptions in one week ("Played the album…",
    "stop" → "What's up?").*

14. **Per-client sessions for the web UI** *(added 2026-09-05; operator
    decision on semantics pending)*. Today every browser/device shares
    the one fixed `webui` session. Server side is half there: the store
    is keyed by session id and `/message` already accepts `session_id`;
    the UI never sends one and `/history` (GET/DELETE) plus the
    uploaded-file context are hardwired to the fixed session. Build =
    client id in localStorage sent with every message, history endpoints
    take the id, clear-history scopes to it, uploads scoped or left
    global by choice; schema unchanged. **Open decision:** (1) per
    device — phone and desktop each keep their own thread (an
    afternoon); (2) per conversation — "new chat" + a list of past
    threads; (3) threads you can pick up on any device — same list, every
    device sees every thread. Prerequisite for item 15. *Why: two devices
    on one thread interleave; the PWA makes a second device the norm.*

15. **Kronk as a PWA on the phone** *(added 2026-09-05; pinned by the
    operator, plan discussed, cert decision pending)*. LAN-only; remote
    reach is Tailscale on the operator's side, so Kronk sees LAN traffic
    either way. **Gate: HTTPS** (installability, service worker, and mic
    access all need a secure context; nginx is HTTP :80 today — keep :80
    for HA's shim, add :443). Cert options: (a) local CA, root installed
    per device; (b) public cert for a `home.hippiehouse.net` name via a
    DNS challenge; (c) if kronk joins the tailnet, `tailscale cert` for
    its `…ts.net` name + MagicDNS — no trust step, no DNS API; cloud
    contact is the ACME issuance either way. Phase 1: manifest, icons,
    service worker (app shell cached, never the message stream),
    mobile layout pass, per-device sessions (item 14). Phase 2:
    push-to-talk — mic clip → new endpoint → the existing Wyoming
    Whisper → pipeline → Piper → playback in the page; a **room picker**
    supplies the origin so music plays where the phone is. Out: public
    exposure, Web Push (rides Google/Apple relays). **External access +
    auth** lives here too (folded 2026-09-14): the posture is LAN +
    Tailscale, no public endpoint; if that ever changes, real
    authentication and rate limiting come first, decided once. *Why:
    the operator's ask — Kronk that "just appears to be an app."*

16. **Pandora thumbs by voice** *(added 2026-09-05; plan
    `docs/plans/PANDORA_THUMBS_PLAN.md`; not started)*. "Thumbs up /
    down" to a satellite trains the playing Pandora station, Echo-style.
    Finding: MA plays a station as one radio item and the current track
    (with its `trackToken`) exists only inside the provider's session,
    so no outside client can thumb — the plan is a small patch to MA's
    Pandora provider (a feedback route on its stream server, overlay
    mounted over the pinned image with a hash guard; maintenance line)
    plus a tool_service `/music/rate` route, a `rate_music` terminal
    tool, and blueprint sentences. Thumbs land in the account that
    plays (the bot), forking shared stations on first use — recorded as
    the operator's call. **Steps, in order:** (1) operator: the **bot
    account** (folded from former item 17, 2026-09-14) — a Pandora
    Premium Family member account, stations shared into it as linked
    copies, verify one first (thumbs should show), MA's Pandora provider
    re-authenticated to it; this also fixes the one-stream-per-account
    collision and gets personal credentials out of the MA config volume
    (tenet 10); YouTube Music the same way later; (2) Claude: the
    go/no-go script — log in as the bot the way the provider does, fetch
    one fragment of a throwaway station, thumb one track, confirm in
    Pandora's UI (kills or validates the plan before any patch); (3)
    operator: decide where training lands (bot as the house account,
    recommended, vs. personal account + one-stream limit). *Why: the
    operator's stated want; the training is the value of Pandora.*

18. **Playback control — the ~1 s fast path** *(added 2026-09-10; the
    Kronk tier shipped 2026-09-10, see Shipped)*. Sentence triggers in
    the blueprint fork ("stop", "pause", "resume", "skip", "louder",
    "quieter") calling `media_player.*` on the device-first-resolved
    player — matches before HA's intents and the LLM. Also: set areas on
    the MA player devices (basement, kitchen) so HA's built-in intents
    have a target and Kronk's labels name the room; HA's built-in bare
    "pause" with only the satellite's area as context did not match in
    testing (bare "resume" did) — cause not pinned
    (`docs/incidents/INVESTIGATION_2026-09-10_voice_stop.md`). *Why:
    "stop" is the most common thing said to a playing speaker; today it
    takes five seconds through Kronk.*

19. **Per-agent reasoning budgets (llama.cpp pin bump)** *(added
    2026-09-14)*. The E4B server's `--reasoning-budget 256` is one cap
    for every agent it serves; when it closes the thinking channel
    mid-thought the model finishes out loud (the leak the retract now
    hides). Measured: 27% of E4B rounds in the last week hit ≥256 output
    tokens — home 42%, research 31%. The running build (b9611, June)
    ignores a per-request `reasoning_budget_tokens`; the current llama.cpp
    tree accepts it. Bumping the pin (one unit file, MTP drafter compat
    to re-verify) would let home think briefly and research think long,
    attacking the leak at its source and cutting the home agent's tail
    latency. Single-variable: bump, bench (`pipeline_bench` pre/post +
    coordinator battery), then set budgets per agent in `llm.py`.
    *Why: the cap is a statistical latency tool with a known leak; the
    retract makes leaks invisible, budgets per agent make them rare.*

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

7. **MagicMirror — read-only investigation over voice and web**
   *(rescoped 2026-09-14)*. Tier 1 — `update_magicmirror` (the one
   sanctioned mutation: backup-then-update over a forced-command SSH
   key) — **shipped and in use** (2026-07-06; repaired 2026-08-16, see
   `docs/incidents/INVESTIGATION_2026-08-14_mm_banner.md`). Tier 2 is
   now just this: the devops agent answers questions about the mirror
   ("is it running?", "why is the weather module stale?", "what changed
   in the last update?") through the audited read-only `remote_exec`
   path, from a satellite or the web UI, and **makes no changes** —
   the standing no-mutation rule for managed hosts holds, the update
   flow is the only exception. Open work: the ops classifier's Phase-B
   quirks that block real investigations (`git -C` misparse, quoted
   pipes, `journalctl --user`); voice-shaped answers (Devstral runs
   ~15 tok/s, so a spoken answer must be two sentences, not a log dump —
   detail stays in the web UI); a small battery of investigation
   questions as the test, in the voice smoke test's shape (item 8);
   confirm the mirror phrasings route to devops from voice. *Why: the
   first Kronk capability that reaches another machine; investigation
   is the value, mutation is the risk.*

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

- **Research agent: cite-or-mark-unverified guard for officeholder
  claims** *(added 2026-09-03 from the K2 research bench)*. When the
  research answer names a *current* officeholder/CEO/leader, the claim
  must trace to a fetched page in this run or be labelled unverified.
  Receipt: K2-7B answered "Joe Biden" / "Fumio Kishida" as current
  leaders in Sept 2026 after its searches came up empty, where E4B
  abstained — a structural rule would have turned both into honest
  partials and costs E4B nothing. Tenet 5/6 shape: change the loop, not
  the prompt (e.g. a post-check that flags proper nouns absent from every
  tool result). Plan doc first; pair with the research bench as its test.
- **Flock/ALPR camera watch** — alert when a Flock Safety (or other ALPR)
  camera newly appears near home or anywhere in town. Likely source:
  OpenStreetMap surveillance nodes via the Overpass API
  (`man_made=surveillance` + ALPR/operator tags — the dataset behind
  DeFlock.me), polled on a slow cadence (daily is plenty); diff against a
  stored roster, alert on new nodes via the HA notification path. Same
  poll → diff → notify shape as the solar monitor; home coords already
  exist in tool_service (weather). Honesty requirement for the alert:
  crowdsourced data lags reality, so a new node means "newly *mapped*",
  not "newly installed" — say so in the notification.
- **Doorbell package watch (UniFi Protect)** — tell the operator when a
  package lands on the doorstep, video never leaving the house. Bridge:
  HA's UniFi Protect integration against the Dream Machine (currently not
  installed — zero Protect entities in HA, probed 2026-08-21). Tiered:
  (0) if the doorbell is a G4 Pro / AI model, Protect detects packages
  natively on the NVR → HA event → notification, no Kronk vision needed;
  (1) same event through Kronk's announce primitive + a delivery log for
  "when did it arrive?"; (2) if the model lacks package smart-detect:
  event-driven snapshot → local VLM on the GPU (gemma-3-4b + vision
  projector is the idle-hardware candidate; bake off vs a small
  purpose-built VLM) → verdict → notify. **Gating question: doorbell
  model / whether Smart Detections lists "Package".** Hard rule from the
  hang saga: event-triggered frames only, never continuous stream
  analysis — the UDM watches always, Kronk judges moments.
- **Instant Pot cook times** — kitchen voice skill: "how long for black
  beans in the instant pot?" answered fast and *correctly*. Curated local
  table (beans/legumes soaked vs dry, grains, rice, common staples —
  time, pressure level, release method), served by a small tool on the
  home agent — NOT model recall (4B models confabulate cook times; math
  in code, model narrates) and NOT web search (slow for a
  standing-at-the-counter question; answer is static). Open design
  choices at plan time: tool vs prompt-injected table on the coordinator
  path (voice latency: coordinator → ask_home adds a hop); where the
  table lives (tool_service data file, operator-editable); honest "not
  in my table — want me to look it up?" fallback for exotic foods.
- **Proactive Kronk** — announcements pushed to the Voice PE / other
  speakers (timer callbacks are the trailhead; laundry, hot-tub alerts,
  calendar reminders, solar-failure alerts follow). Design whatever timer
  verification (item 3) reveals about HA's announce path.
- **Health RAG completion** — `query_bloodwork` / `search_health_data`
  tools exist in `orchestrator/tools.py` but are wired to no agent;
  `health_service` parsing/chunking/vector-store code is in place.
- **Secrets management rebuild** — the Infisical retirement left Garmin
  and Withings sync as no-op stubs; current plan is per-service
  `/data/<service>_tokens.json` bind mounts. Unblocks the health sources.
- **More expressive TTS** — effort-ordered options already scoped in the
  README/`docs/VOICE_SETUP.md`: different Piper voice → voicebox.sh →
  XTTS-v2 on gfx1151 → Bark.
- **Peer agent handoffs** — a multi-domain query routed to a *specialist*
  still gets a single-domain answer; agents-as-tools fixed this for the
  coordinator path only. Attack if it bites in practice. See
  `TECH_DEBT.md` [ROUTING-01].
- **Voice latency program** — the Kronk fallback tier runs 15–25 s, the
  edge of tolerable. Treat as a standing constraint on new voice features;
  attack when it bites (candidate levers: context cache, smaller/faster
  routing, per-agent reasoning budgets — item 19).

## Deferred / parked — with revisit conditions

- **Hot tub monitor** — parked 2026-06-12; spa pack unreachable. See
  `TECH_DEBT.md` [HOTTUB-01].
- **Deliberately rejected** (per-domain tool services, SQLite pooling,
  Redis, etc.) — see `TECH_DEBT.md` "Considered and rejected."

## Chores / quick wins

- **Ollama blob reclaim** — delete `/usr/share/ollama/.ollama/models/blobs/`
  (~50+ GB) now that llama.cpp is stable. One careful look first.
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
