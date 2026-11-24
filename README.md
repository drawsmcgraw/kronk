# Kronk — Self-Hosted Home AI Assistant

Kronk is a fully-local, privacy-first home AI assistant. It runs entirely on one
machine — no cloud APIs, no data leaving the house. A chat UI and a voice
pipeline (Home Assistant satellites) sit in front of a **shortcut router →
coordinator → specialist-agent** pipeline over local llama.cpp models. It
handles weather, web search, news briefs, solar, music, the hot tub, the
MagicMirror, coding/devops help, and questions about its own architecture.
It holds no health or financial data (those features were removed
2026-11-15).

**Machine:** Framework desktop — AMD Ryzen AI 375 (hostname `kronk`), Radeon
8060S integrated GPU (GFX1151), 122 GB RAM.

**Last updated:** 2026-11-17 — one request no longer blocks another: each
llama.cpp server has its own lock, held for one generation, voice served
first; voice requests stop at 25 s with one sentence; a dropped client
cancels its request (sprint M9). Before that: a request with more than one sentence or
more than 20 words skips the routing shortcuts and goes to the coordinator
whole (sprint M14, the messy-transcript gate). "ask Kronk to …" skips every routing
shortcut and goes to the model (sprint M19). Routing is an ordered rules
table, and five pinned commands run their tool directly with no model call
(sprint M7).
2026-11-16 — hot tub monitor kept: its removal (sprint
M1b) was reversed. 2026-11-15 — health, finance and retirement features
removed (sprint M1). 2026-11-04 — inventory, routing and stacks brought in
line with the running system. Older benchmarks and the Ollama-era model history live
in [`docs/HISTORY.md`](docs/HISTORY.md).

---

## Architecture at a glance

### Request pipeline

Every message — chat UI, OpenAI shim, or the Ollama shim that Home Assistant's
voice pipeline calls — flows through the same pipeline:

1. **Routing** (`orchestrator/routing.py`) — an ordered table of 17 rules
   (`routing.RULES`): narrow deterministic shortcuts pin a message to a
   specialist (a named household entity, an explicit method, or domain
   vocabulary with context); the last rule, `default`, sends everything else
   to the coordinator. The `gemma-3-4b` LLM classifier was removed 2026-08-18;
   a shortcut miss now costs one coordinator hop, never a wrong lane.
   **Direct dispatch:** for five rules whose match fixes the whole tool call
   (`playback`, `now_playing`, `rate`, `music_another`, `mm_update` — "pause",
   "what song is this", "thumbs up", "not that", "update the magic mirror")
   the pipeline runs that tool itself, with no model call and without waiting
   for the model lock, and speaks the result the way the home agent would
   ([`docs/features/direct-dispatch.md`](docs/features/direct-dispatch.md)).
   **The lead-in:** the first rule, `lead_in`, takes any message that starts
   with "ask Kronk to" (or "ask Kronk", optionally after "hey", "okay", "can
   you" …), cuts the lead-in off and sends the rest to the coordinator, past
   every shortcut and direct dispatch. The rule for users: **say it plainly
   for the fast path; start with "ask Kronk to" and the model handles it.**
   The words live in `config/lead_in.yaml`
   ([`docs/features/lead-in.md`](docs/features/lead-in.md)).
   **The messy-transcript gate:** the `messy` rule, right after the lead-in,
   Talkie, `direct_override` and the two typed-link rules, sends a message
   with more than one sentence or more than 20 words to the coordinator with
   the whole text, past every other shortcut and direct dispatch (trailing
   speech after "Play Anime Pop Radio." is the case it is for). "Plainly"
   means one short sentence. Nothing is trimmed. Limits in
   `config/tuning.yaml` (`transcript_gate`)
   ([`docs/features/messy-transcript-gate.md`](docs/features/messy-transcript-gate.md)).
2. **Coordinator** — `gemma-4-e4b` answers directly or delegates through its
   `ask_<agent>` tools, and delivers the pre-generated `news_brief` verbatim.
3. **Specialist** — the chosen agent runs the unified streaming tool-calling
   loop (below) over an allow-listed tool set. A pinned specialist can hand a
   multi-domain request back to the coordinator; a failed specialist reaches
   it explicitly labeled `FAILED`.

Agent → model assignments come from env vars in `docker-compose.yml`
(`COORDINATOR_MODEL`, `RESEARCH_AGENT_MODEL`, …), so model swaps don't need code
edits. Details: [`docs/features/coordinator-default-routing.md`](docs/features/coordinator-default-routing.md). The `AGENTS` dict in `orchestrator/agents.py` is
the single source of truth for agents and their tools; their prompts and the
loop's tuning live in `config/` (see "Prompts and tuning" below).

### Service map

Kronk's containers run on the `kronk` bridge network and address each other
by service name — except `litellm`, which uses host networking (see Design
Decisions). llama.cpp model servers and GPU STT run on the **host** as user
systemd units, not containers. The live view is the `/services` page.

| Service | Port | Role |
|---|---|---|
| `nginx` | 80 | Reverse proxy (the only Kronk port on the LAN); SSE pass-through |
| `orchestrator` | 8000 (internal) | UI pages, conversation history, the pipeline, tool dispatch, shims |
| `litellm` | 8002 (host net) | OpenAI-compatible proxy in front of the llama.cpp servers |
| `tool_service` | 8003 (localhost) | Weather, search, URL fetch, music (HA/MA), solar, news brief, shopping list, hot tub, MagicMirror ops, diagrams |
| `searxng` | 8080 (internal) | Self-hosted meta-search, used by `tool_service` |
| `wyoming-piper` | 10200 | Piper TTS for the voice pipeline |
| `wyoming-whisper` (host unit) | 10300 | faster-whisper STT on the GPU, no prompt (production pipelines) |
| `wyoming-whisper-dev` (host unit) | 10301 (loopback) | The same model with the household vocabulary prompt; HA's `kronk-dev` pipeline only (sprint M13) |
| `kronk-stt-prompt` (host timer) | — | Nightly 04:00: rebuilds the dev Whisper prompt (`scripts/build_stt_prompt.py`), restarts `wyoming-whisper-dev` only when the text changed |
| `kronk-hottub-monitor` (host unit) | — | Polls the spa pack (`geckolib`) every 5 min and writes `data/hottub/status.json`, which `tool_service` serves at `/hottub` |
| `homeassistant` (`kronk-ha`) | 8123 (host net) | Voice pipeline broker, satellites, automations |
| `music-assistant-server` (`kronk-ma`) | 8095 (host net) | Players, YouTube Music, Pandora, NAS library |
| `voice_tracer` | 8010 (internal) | HA voice runs + blueprint runs → OpenTelemetry traces (Telemetry v2) |
| Grafana (`kronk-telemetry`) | 3000 | Telemetry v2 dashboards and trace search |
| Tempo (`kronk-telemetry`) | 3200 (loopback), 4318 (OTLP, docker network) | Trace store, 90-day retention |

nginx serves the UI pages (`/`, `/solar`, `/resources`, `/performance`,
`/agents`, `/services`, `/shopping_list`), proxies `/api/solar/` to
`tool_service`,
`/probe/*` to per-service health endpoints (including HA, MA, Grafana and Tempo),
and `/devstral/` straight to port 11436 (bypassing LiteLLM), where the retired
Devstral Q8 server listened; nothing listens there now, so it answers 502.

### Agent loop — unified streaming

`agents.run_stream()` runs one streaming LLM call per round. Content tokens are
yielded the instant they arrive — there is no buffer-then-dump path. `tool_calls`
accumulate inside `llm.stream()` and surface as one `{"tool_calls"}` event at
end-of-stream. A round with zero `tool_calls` is terminal (its content already
streamed live). The forced-synthesis tail only runs if the model used tools in
*every* round.

```
run_stream(agent, task, context)
│
├─ build messages = [system, user]
│
├─ LOOP  round_idx in range(agent.max_rounds)   (config/tuning.yaml)
│  │
│  ├─ llm.stream(messages, model, tool_defs) — consume streamed chunks:
│  │     {"token"}      → yield to user immediately (live stream); buffer it
│  │     {"tool_calls"} → accumulate from deltas; stash for end-of-round
│  │     {"usage"}      → stash for metrics
│  │     LiteLLM 5xx    → llm.stream() raises → yield {"error"} → RETURN
│  │                      (main.py then falls back to the coordinator)
│  │
│  ├─ stream ends → emit agent_round, metrics.record
│  │
│  ├─ no tool_calls stashed?
│  │     YES → content already streamed live → yield {"done"} → RETURN
│  │
│  └─ tool_calls stashed?
│        YES → append assistant msg (buffered content + tool_calls)
│              for each tool_call:
│                 seen this turn already → canned "already called" result
│                 otherwise              → yield {"narration"}
│                                          result = tools.execute(...)
│                                          emit tool_call / tool_complete
│                 append {"role": "tool", result}
│              → continue to the next round
│
└─ loop exhausted (every round used tools) → forced synthesis
      llm.stream(messages, model, tools=None)   — tools disabled, must answer
      yield tokens (live stream) → yield {"done"} → RETURN
```

**Typical case — one tool call, then the answer (e.g. `assistant` reading
`get_kronk_context`):** two `llm.stream()` calls, exits at round 1.

```
round 0:  stream → {"tool_calls":[get_kronk_context]}   (no tokens yet)
          → narration "reading Kronk's configuration..."
          → execute get_kronk_context → append tool result
round 1:  stream → tokens... tokens... tokens...   ← STREAMS live to user
          → no tool_calls → yield {"done"} → RETURN
```

Before the unified-streaming refactor, the synthesis round was a blocking
`llm.complete()` whose content was dumped as one SSE chunk — so agents that
finished inside the tool-round budget (home, assistant) never
streamed. Now every round streams, so every agent streams. A 5xx from LiteLLM
raises out of `llm.stream()`, becomes an `error` event, and falls back to the
coordinator instead of dead-ending.

---

## Setup

The container stack runs on any Linux host with Docker; it was built for the
specific machine described above, but nothing in the compose stack is tied to
it. The involved part is the LLM layer — the stack expects llama.cpp model
servers already running on the host (see the Operations runbook).

### Prerequisites

- **Docker + Docker Compose, on Linux.** The `litellm` service uses
  `network_mode: host`, which is Linux-only (not Docker Desktop on Mac/Windows).
- **llama.cpp model servers** running as host systemd units — LiteLLM proxies to
  them, so the stack is not useful without them. See
  [llama.cpp model servers](#llamacpp-model-servers) in the Operations runbook.

### Steps

1. Clone the repo.
2. **Create the SearXNG config** — see the callout below.
3. Set `LOCATION` in `docker-compose.yml` to your area (the weather tool's default).
4. Bring up the stack:
   ```bash
   docker compose up -d --build
   ```
5. Open `http://localhost/` for the chat UI.

> **SearXNG `secret_key` — required before first run.** SearXNG refuses to start
> unless `server.secret_key` is set to a unique value. The real
> `searxng/settings.yml` is **gitignored** so the secret never enters version
> control — the repo tracks `searxng/settings.yml.example` as the template
> instead. Before the first `docker compose up`:
>
> ```bash
> cp searxng/settings.yml.example searxng/settings.yml
> sed -i "s/ultrasecretkey/$(openssl rand -hex 32)/" searxng/settings.yml
> ```
>
> Leaving the `ultrasecretkey` placeholder in place will crash-loop the
> `searxng` container.

---

## Current inventory

### Models

Each model is one llama.cpp server bound to `127.0.0.1` on the host. The LiteLLM
catalog is `litellm/config.yaml` (bind-mounted, hot-editable); each entry's
`kronk:` block carries UI metadata the orchestrator reads for the `/resources`
page. Per-model **memory on that page is measured** (host-side DRM-fdinfo
exporter on a 30 s timer — `scripts/gpu_mem_export.py`, joined by port into
`/api/servers`); the `vram_gb` metadata is only the fallback estimate. See
[`docs/features/resources-live-memory.md`](docs/features/resources-live-memory.md).
The VRAM column below is the static estimate, kept for orientation only.

Running as of 2026-11-24 (these three are the whole catalog;
`tests/test_m24_catalog.py` fails if `litellm/config.yaml` names a model no
unit in `systemd/` serves):

| Model | systemd unit | Port | Quant | Ctx | VRAM | Role |
|---|---|---|---|---|---|---|
| `gemma-4-e4b` | `llama-gemma4-e4b` | 11438 | QAT UD-Q4_K_XL + MTP drafter | 32k | ~3.5 GB | Coordinator + research/home/assistant agents |
| `devstral-2512-q4` | `llama-devstral-q4` | 11440 | Q4_K_M | 128k | ~14 GB | coding + devops agents |
| `talkie` | `llama-talkie` | 11441 | Q8_0 | 2k | none (CPU only; ~14 GB RAM as page cache) | talkie agent, when asked for by name |

Talkie runs on the CPU (`--device none -ngl 0`): about 7.5 tokens a second
through Kronk, so on the speech mount its reply is capped at
`config/voice.yaml` `max_reply_tokens.talkie` (120 tokens, about 16 s) to stay
inside the voice deadline (M24; measurements in `docs/sprint/M24-build.md`).

Retired 2026-11-24 (M24): `gemma-3-4b` (served the LLM router removed
2026-08-18), `bonsai-8b`, `mistral-nemo` and the `devstral-2512` Q8 build.
Their units, catalog entries and GGUFs are gone; `docs/HISTORY.md` has the
model history.

### Agents

The `AGENTS` dict in `orchestrator/agents.py` is authoritative for agents and
their tools. Each agent's system prompt is `config/prompts/<name>.md`.

| Agent | Model | Tools | Purpose |
|---|---|---|---|
| coordinator | `gemma-4-e4b` | `ask_<agent>`, `news_brief` | Default lane: answers directly or delegates |
| `research` | `gemma-4-e4b` | `web_search`, `fetch_url` | Live/current info — news, prices, recent releases |
| `home` | `gemma-4-e4b` | `get_weather`, `shopping_list_*`, `query_hottub`, `solar_{status,detail,energy}`, `play_music`, `control_music`, `now_playing`, `rate_music`, `remember_music_preference`, `update_magicmirror` | Weather, shopping list, hot tub, solar, music, mirror updates |
| `assistant` | `gemma-4-e4b` | `get_kronk_context`, `generate_diagram` | Kronk's own architecture; Graphviz diagrams |
| `coding` | `devstral-2512-q4` | `web_search`, `fetch_url` | Writing/debugging code |
| `devops` | `devstral-2512-q4` | `remote_exec`, `http_check` | Server admin; read-only audited exec on managed hosts; reachability checks (`http_check`: one GET to any address the host can reach). No web tools (sprint M4): the coordinator gets documentation from `research` first |
| `talkie` | `talkie` | — | Vintage-1930 persona; only when asked for by name ("ask talkie …", rule `talkie_explicit`). Replies capped on the speech mount; if its server fails, the failure is the reply (the coordinator does not answer in its place) |

No agent holds both a web tool and `remote_exec` or `http_check`, so fetched
text and LAN access never share a loop; `run_stream` rejects any tool call the
agent was not offered (`tests/test_agent_tool_separation.py`).

Timers are no longer Kronk's: HA's local Assist intent runs them on the
satellite (2026-07-12).

### Tools

`get_weather` (NWS), `web_search` (SearXNG), `fetch_url`, `news_brief`,
`query_hottub`,
`shopping_list_{view,add,remove,clear}`, `solar_{status,detail,energy}`,
`play_music` (HA → Music Assistant; resolves misspellings and picks for genre
requests — `docs/features/music-taste.md`), `control_music`, `now_playing`,
`rate_music` (Pandora thumbs), `remember_music_preference` (household taste
file), `update_magicmirror`, `remote_exec`, `http_check`, `get_kronk_context`,
`generate_diagram`. Definitions live in
`orchestrator/tools.py`.

The music tools and `update_magicmirror` are **terminal tools**
(`AgentConfig.terminal_tools`): the result is spoken verbatim and ends the
agent turn. For the five direct-dispatch rules the same tools run with no
agent at all (routing step 1 above), through the same `tools.execute` and
the same spoken sentence. Structural guardrail — small
models otherwise re-call the tool or contradict its result. The tool verifies
the player actually reaches `playing` before reporting success (MA queues
requests async, so a 200 from HA alone proves nothing).

### Prompts and tuning (`config/`, the live directories, `/settings`)

Prompts and tuning values are plain files (sprint M21 step 1). The repo's
`config/` holds the **shipped defaults**; each instance reads its own
**live config directory**, a small git repository outside the repo
(`kronk-config/prod`, `kronk-config/dev`, next to `kronk-releases/`),
mounted at `/config` (sprint M21 step 2). **`/settings`** (and
`/dev/settings`) shows every prompt and tuning value in effect, with its
source, comment and last commit, plus the routing rules, env settings,
model servers and model-facing text in code read only; editing is behind a
passphrase (`scripts/settings_passphrase.sh`), validates, writes, reloads,
verifies and commits. The files:

- `config/prompts/<name>.md` — the system prompt of every agent and the
  coordinator. A `<!-- … -->` header says what it is for and is stripped;
  the rest is sent byte for byte. `{{max_rounds}}` is the agent's round
  budget.
- `config/tuning.yaml` (orchestrator) — round budgets, the repeat-call
  nudge, the weather-context age, per-tool HTTP timeouts, the
  messy-transcript gate's limits (sprint M14).
- `config/lead_in.yaml` (orchestrator) — the words of the "ask Kronk to"
  lead-in: verb, name (and any mis-heard spelling captured data shows),
  leading fillers (sprint M19).
- `config/music.yaml` (`tool_service`) — the playback verify window and the
  music resolver's thresholds.

Every key carries a comment. Each service validates its files against a
strict schema (`config_loader.py` in each): unknown keys, wrong types,
out-of-range numbers, a missing or empty prompt are errors naming the file
and line. An invalid file never takes effect: at startup the service runs
on built-in defaults (the values before the move); after a bad reload the
last good values stay. The error is logged and shown on `/services`.
Precedence: `WEATHER_CTX_MAX_AGE_S` (the one pre-existing env var for a moved
value) > file > built-in default. Reload with `POST /config/reload` on each
service — host and compose network only, never through nginx:
`scripts/config_reload.sh dev|prod`. No secrets in `config/` (a test scans
it). Routing regexes stay in `routing.py` (the lead-in's words are the one
routing input in `config/`); model assignments stay env vars;
llama unit flags and `litellm/config.yaml` are unchanged. Details:
[`docs/features/config-directory.md`](docs/features/config-directory.md).

### Error handling

Failures surface loud and specific at every layer (tenet 7 — "an unexpected
error occurred" is a bug): services return `detail` strings naming the real
cause, tool handlers preserve them via `tools._fail()`, a failed specialist
reaches the coordinator explicitly labeled `FAILED` (with an
invented-answer ban), failed turns mark their trace `ERROR` in Tempo, and a
last-resort guard in `_run_pipeline` turns unexpected crashes into a spoken
cause + rid instead of a dead stream.

How much of that detail the *user* sees is a toggle —
`orchestrator/errors.py`, config in `docker-compose.yml`:
`ERROR_STYLE=debug` (default: causes, HTTP statuses, rid) or `friendly`
(one clean sentence) for `/message`, `/api/chat` and `/v1`;
`ERROR_STYLE_VOICE` for the speech mount `/voice/api/chat` only, friendly by
default (set it to `debug` to hear full detail on the speakers). The style is
keyed on `origin.voice`, the request-scoped "came in on the speech mount"
value. Rendering only — logs and traces always keep full detail.
Details: [`docs/features/verbose-errors.md`](docs/features/verbose-errors.md).

### Telemetry

Every request is one OpenTelemetry trace: the orchestrator's pipeline, agent
rounds, LLM calls and tool calls, continued into `tool_service` (Music
Assistant commands, HA service calls, the playback-verify wait) over W3C
`traceparent`. Home Assistant's voice runs and the music blueprint's runs are
read by `voice_tracer` and linked to Kronk's trace by satellite device and
time window. Storage is Grafana Tempo, UI is Grafana at `http://kronk:3000`
(four provisioned dashboards: Overview, Stages, Slowest requests, Music).
`TELEMETRY_ENABLED=false` turns every span into a no-op. Traces hold what was
said and answered only with `TELEMETRY_RECORD_TEXT=true` (production turns it
on for sprint M12; the dev pair has it on); with it off, no span attribute or
log line carries the words, apart from the named exceptions in
`docs/features/voice-capture.md` (failure detail behind a spoken error,
exception text, a fetched page's URL, nginx's access log). Grafana needs no login, so on the LAN transcripts
are readable by anyone, guests included. Since M12 every puck also records
audio (no end date; recordings older than 14 days move to the NAS weekly), and `voice_tracer` marks voice runs the user corrected
within 60 s: [`docs/features/voice-capture.md`](docs/features/voice-capture.md).
See [`docs/features/telemetry.md`](docs/features/telemetry.md) and
[`docs/TELEMETRY_GUIDE.md`](docs/TELEMETRY_GUIDE.md).

---

## Operations runbook

### Four compose stacks

Each stack has its own compose file and project name, so a Kronk rebuild never
touches HA or MA. Each file opens with an `# Operations:` block of day-2
commands — read it before running compose against that stack.

| Stack | File | Project | Reach via |
|---|---|---|---|
| Kronk (orchestrator, tools, llm proxy, nginx, …) | `docker-compose.yml` | `kronk` | port 80 |
| Home Assistant | `docker-compose.ha.yml` | `kronk-ha` | port 8123 |
| Music Assistant + YT PO-token helper | `docker-compose.ma.yml` | `kronk-ma` | port 8095 |
| Telemetry (Tempo + Grafana) | `docker-compose.telemetry.yml` | `kronk-telemetry` | port 3000 (Grafana) |

HA mounts the **shared** `kronk_ha-config` volume (declared `external: true`
in `docker-compose.ha.yml`). The split means `docker compose up -d --build
orchestrator` no longer flickers HA. HA still reaches kronk via `localhost`
because it runs with `network_mode: host`.

### Deploy / restart

- **Kronk code change:** commit, tag, deploy from the tag (next line).
  Never `docker compose up` in the working tree against the production
  `kronk` project: it moves production off its tag.
- **Production from a git tag** (sprint M17): `scripts/deploy_tag.sh plan <tag>`
  previews (changes nothing), `deploy <tag>` builds and starts the kronk
  project from an exported copy of the tag, and `status` says which tag (or
  the working tree) each container was created from. Never touches HA or MA.
  Details and caveats in the script header.
- **Dev pair** (`orchestrator-dev`, `tool_service-dev`, compose profile
  `dev`, reached at `/dev/` and by HA's `kronk-dev` pipeline on "Hey
  Jarvis"): recipes in the comment above those services in
  `docker-compose.yml`.
- **HA restart:** `docker compose -f docker-compose.ha.yml restart homeassistant`.
- **HA upgrade:** never by hand with `pull`. Patch releases: the monthly
  patch run (`docs/features/upgrade-cadence.md`). Major releases:
  `docs/runbooks/major-upgrade.md`, in a window Drew names.
- **Backups and restore:** `docs/features/backups.md` (monthly config tar
  and weekly recordings archive to the NAS share, timers on since
  2026-11-20) and `docs/runbooks/restore.md`.
- **nginx config, `litellm/config.yaml`, `kronk-context.md`,
  `magicmirror/`:** mounted from the release directory
  (`../kronk-releases/<tag>`), so a change reaches production only through
  the next tagged deploy.
- **Prompts, tuning:** edit on `/settings` (production) or `/dev/settings`
  (dev). By hand: edit the file in `kronk-config/prod` or `kronk-config/dev`,
  commit it there, then `scripts/config_reload.sh prod` (or `dev`). The
  answer lists what changed, or the errors with file and line and the values
  kept. No rebuild. The repo's `config/` is the shipped defaults; editing it
  changes nothing running until a new live directory is seeded from it.

> **Volume safety:** never run `docker compose -f docker-compose.ha.yml down -v`
> — the `-v` flag wipes `kronk_ha-config` and you lose every HA integration,
> the admin account, the timer helper, and the voice pipeline config. Plain
> `down` (no `-v`) is safe.

### llama.cpp model servers

One **user** systemd unit per model at `~/.config/systemd/user/llama-*.service`,
each loading one model permanently: into GPU memory, except Talkie, which runs
on the CPU. Units are named after the
model (not the role), so reassigning an agent only touches `docker-compose.yml`.
All units bind `127.0.0.1` — only the host-network `litellm` container can reach
them.

Reference copies of every unit live in [`systemd/`](systemd/). Install with:

```bash
cp systemd/llama-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llama-gemma4-e4b llama-devstral-q4 llama-talkie
systemctl --user list-units 'llama-*'
```

> **Known wart (tech debt):** `llama-talkie.service` points its `-m` flag at
> `/home/drew/model-staging/talkie-lm/talkie-1930-13b-it-Q8_0.gguf` rather than
> `/opt/models/...` like every other unit. Cosmetic — service runs fine. Move
> the GGUF and update the unit when convenient.

Each GPU unit's `ExecStart` sets the GGUF path, `--port`, `--ctx-size`,
`-ngl 99` and `--flash-attn on`, plus the runtime environment, and uses a
Vulkan build of llama.cpp (see each unit in `systemd/`). Talkie's unit uses a
ROCm (HIP) build of a llama.cpp fork, the only build that loads its model,
held off the GPU with `--device none -ngl 0` (an idle ROCm process on the GPU
spins it: `docs/incidents/INVESTIGATION_2026-06-09_hangs.md`). It still
carries the ROCm environment, and loads ROCm libraries from Ollama's
directory, as the Whisper units do:

```ini
Environment="HSA_OVERRIDE_GFX_VERSION=11.5.1"
Environment="LD_LIBRARY_PATH=/usr/local/lib/ollama/rocm:/home/drew/pai/pai_workspace/llama-cpp/talkie-gfx1151-rocm7"
```

The `render` group is required for `/dev/kfd` access — verify with `groups drew`.

### Whisper (STT) units

`wyoming-whisper.service` (production, `0.0.0.0:10300`, no prompt) and
`wyoming-whisper-dev.service` (sprint M13, `127.0.0.1:10301`) run the same
`large-v3-turbo` model from `~/services/wyoming-whisper/`; each loads it
once, at start (about 2.1 GB GTT each). The dev unit adds
`--initial-prompt` from `~/services/wyoming-whisper/dev-prompt.env`, written
by `scripts/build_stt_prompt.py` and rebuilt nightly by
`kronk-stt-prompt.timer`. Reference copies in [`systemd/`](systemd/); install,
stop and removal recipes in each unit's header and in
`docs/features/voice-pipeline.md` ("The dev Whisper instance").

### Building llama.cpp for gfx1151

Pre-built ROCm binaries on GitHub don't include gfx1151 — they report "no
ROCm-capable device" even with `HSA_OVERRIDE_GFX_VERSION=11.5.1`. Build from
source inside a ROCm 7.2 container (`rocm/dev-ubuntu-24.04:7.2-complete`), then
copy the binary + shared libs out to `pai_workspace/llama-cpp/`:

```bash
cmake -B build -DGGML_HIP=ON -DAMDGPU_TARGETS="gfx1151" \
  -DGGML_HIP_NO_VMM=ON -DBUILD_SHARED_LIBS=ON
cmake --build build --config Release -j$(nproc) --target llama-server
```

- `GGML_HIP_NO_VMM=ON` — required for the iGPU (no VMM support)
- `BUILD_SHARED_LIBS=ON` — needed to emit `libggml-hip.so` alongside the binary
- Do **not** set `GGML_HIP_ROCWMMA_FATTN=ON` — build failure on ROCm, not needed on 7.2

### GPU memory (GTT ceiling)

The Radeon 8060S has no dedicated VRAM — it carves memory from system RAM via
GTT (Graphics Translation Table). The driver default caps GTT at ~50% of RAM;
this is a driver policy, not a hardware limit, and is raised via kernel boot
params (kernel 6.17+):

```
# /etc/default/grub  →  then: sudo update-grub && reboot
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash ttm.pages_limit=26624000 ttm.page_pool_size=26624000"
```

Value = `([GB] * 1024 * 1024) / 4.096`. We use `26624000` (104 GB → ~101.6 GB
after rounding). **Do not exceed 108 GB** (`27648000`) — 110 GB segfaults on this
silicon. GTT is dynamic: raising the ceiling doesn't reduce idle RAM.

Verify: `cat /sys/module/ttm/parameters/pages_limit` and
`awk '{printf "%.1f GB\n",$1/1024/1024/1024}' /sys/class/drm/card1/device/mem_info_gtt_total`.

(Note: `amdgpu.gttsize` and `amdttm.pages_limit` do **not** work — deprecated /
wrong module name. The module is `ttm`.)

### Model storage

GGUFs live under `/opt/models/<vendor>/`; `talkie` is staged at
`/home/drew/model-staging/talkie-lm/`. Only the files the three running units
load are kept: the other 24 GGUFs (212 GB) were deleted 2026-11-24 (M24). Download with
`hf download <repo> --include "<pattern>" --local-dir /opt/models/<vendor>/`.

---

## Design decisions

**LLM servers on the host, not in Docker.** ROCm device passthrough into
containers (`/dev/kfd`, `/dev/dri`, group IDs, capabilities) is fragile, and the
GGUFs are large host files. Each server binds `127.0.0.1`; only the host-network
`litellm` container reaches them.

**`litellm` on host networking, everything else on a bridge.** llama.cpp binds
`127.0.0.1`, and the Docker host-gateway shim resolves to an address that's
unreachable for loopback-bound servers. So `litellm` runs `network_mode: host`
and binds `0.0.0.0:8002`; bridge containers reach it via
`host.docker.internal:8002`. Tradeoff: host networking is Linux-only — worth
noting if the stack ever moves off this machine. `litellm/hooks.py` normalizes
messages before they reach llama.cpp (registered as a LiteLLM callback).

**Weather: NWS, with web-search fallback.** `api.weather.gov` is free, no key,
and richer than Open-Meteo (named forecast periods, hourly breakdowns, alerts).
NWS is US-only; on a non-200 the pipeline falls back to a web search and tells
the model explicitly that the data came from search, not a live feed. Open-Meteo
is still used for geocoding (NWS has no geocoder).

**Web search: self-hosted SearXNG.** Privacy — queries never leave the house. No
API key, no rate limits, no cost. Results are injected as title + URL + snippet
only; the model can call `fetch_url` for a full page when it needs depth.
`fetch_url` strips boilerplate (trafilatura, BeautifulSoup fallback) and
truncates to ~1,500 tokens. TLS verification is on. It reaches public
addresses only: loopback, RFC 1918, link-local, CGNAT and other non-global
addresses, Docker service names and non-http(s) URLs are refused, and
redirects are followed by hand with the same check on every hop
(`docs/features/fetch-address-block.md`). LAN reachability is the devops
agent's `http_check`: one GET (no body returned) to any http/https address
the host can reach, LAN or public. It has no host allow-list; that was the
operator's decision (2026-11-16). Some LAN devices change state on a plain
GET (a relay toggled by URL), and `http_check` can touch any address on the
LAN.

**Model locks: one per server, one generation at a time (sprint M9).** Each
llama.cpp server has a priority lock that `llm.stream` holds for one
generation; tool calls and other servers' work hold nothing, and waiting
voice requests go before display ones (a display request is overtaken at
most `config/tuning.yaml` `model_lock.max_overtakes` times). Routing and
direct dispatch take no lock. Voice requests stop at `config/voice.yaml`
`deadline_s` (25 s) with one spoken sentence that never denies an action
already sent, and a client that disconnects cancels its request. Rollback:
`MODEL_LOCK=pipeline`. Before M9 one lock wrapped every request's whole
pipeline ([`docs/features/model-locks.md`](docs/features/model-locks.md)).

**Hallucination guardrails — three layers, order matters.** (1) A system-prompt
standing rule ("never fabricate real-time info"). (2) Directive failure messages
— when a tool fails, an explicit `MUST NOT answer from training data` system
message, not a soft hint. (3) Structural tool-status lines — every tool result
is prefixed `[TOOL: weather — live NWS data]` or `[TOOL: weather — FAILED]`. The
status lines are the most reliable because they're structural, not
instructional; no single layer is enough on its own.

**Secrets: a few files, no manager.** `.env` (gitignored) holds `HA_TOKEN`
and `MA_TOKEN` (an MA long-lived token, 1-year expiry, no auto-renew), both
scoped to tool_service; `.env.telemetry` holds the Grafana admin login;
`searxng/settings.yml` holds the SearXNG secret. `.env` also holds the
Pandora, UniFi and NAS backup logins; `secrets/` holds the MagicMirror SSH
key and the YouTube token; the config tar on the NAS holds all of them in
the clear (`CLAUDE.md`, Secrets). NWS, Open-Meteo, SearXNG and
the local LLMs are unauthenticated. The old self-hosted Infisical instance was
retired; reintroduce a secrets manager only if this grows past a handful of
files.

**Dependencies: hash-pinned.** `requirements.txt` is human-maintained;
`requirements.lock` is machine-generated with SHA-256 hashes for every
transitive dep; Docker builds use `uv pip install --require-hashes`. Regenerate a
lockfile with `uv pip compile requirements.txt --generate-hashes -o
requirements.lock` from the service directory — never edit it by hand.

**Containers: non-root (2026-07-07).** Every kronk-built service runs as
UID 1000 (`USER kronk` in each Dockerfile), matching the host operator — so
container writes to the `./data` bind mounts land drew-owned and no service
holds root. Upstream images
(nginx, searxng, litellm, piper) keep their own user models.

---

## Roadmap

See [`ROADMAP.md`](ROADMAP.md) — the single source of truth for planned work
(and the shipped-features list; distilled feature docs live in
[`docs/features/`](docs/features/)).

---

*Historical benchmarks and the Ollama-era model history: [`docs/HISTORY.md`](docs/HISTORY.md).*
