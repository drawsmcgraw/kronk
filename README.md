# Kronk — Self-Hosted Home AI Assistant

Kronk is a fully-local, privacy-first home AI assistant. It runs entirely on one
machine — no cloud APIs, no data leaving the house. A chat UI and a voice
pipeline (Home Assistant satellites) sit in front of a **shortcut router →
coordinator → specialist-agent** pipeline over local llama.cpp models. It
handles weather, web search, news briefs, personal health data
(Garmin/Withings), financial documents, solar, music, the hot tub, the
MagicMirror, coding/devops help, and questions about its own architecture.

**Machine:** Framework desktop — AMD Ryzen AI 375 (hostname `kronk`), Radeon
8060S integrated GPU (GFX1151), 122 GB RAM.

**Last updated:** 2026-11-04 — inventory, routing and stacks brought in line
with the running system. Older benchmarks and the Ollama-era model history live
in [`docs/HISTORY.md`](docs/HISTORY.md).

---

## Architecture at a glance

### Request pipeline

Every message — chat UI, OpenAI shim, or the Ollama shim that Home Assistant's
voice pipeline calls — flows through the same pipeline:

1. **Routing** (`orchestrator/routing.py`) — narrow deterministic shortcuts pin
   a message to a specialist (a named household entity, an explicit method, or
   domain vocabulary with context). Everything else goes to the coordinator.
   The `gemma-3-4b` LLM classifier was removed 2026-08-18; a shortcut miss now
   costs one coordinator hop, never a wrong lane.
2. **Coordinator** — `gemma-4-e4b` answers directly or delegates through its
   `ask_<agent>` tools, and delivers the pre-generated `news_brief` verbatim.
3. **Specialist** — the chosen agent runs the unified streaming tool-calling
   loop (below) over an allow-listed tool set. A pinned specialist can hand a
   multi-domain request back to the coordinator; a failed specialist reaches
   it explicitly labeled `FAILED`.

Agent → model assignments come from env vars in `docker-compose.yml`
(`COORDINATOR_MODEL`, `HEALTH_AGENT_MODEL`, …), so model swaps don't need code
edits. Details: [`docs/features/coordinator-default-routing.md`](docs/features/coordinator-default-routing.md). The `AGENTS` dict in `orchestrator/agents.py` is
the single source of truth for agents, their tools, and the router prompt.

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
| `health_service` | 8004 (internal) | Garmin/Withings data, SQLite + vector store |
| `finance_service` | 8005 (internal) | Financial documents and the positions store |
| `searxng` | 8080 (internal) | Self-hosted meta-search, used by `tool_service` |
| `retire_calc` | 8080 (internal) | Retirement calculator (built from `../retirement-calc`), at `/retire/` |
| `wyoming-piper` | 10200 | Piper TTS for the voice pipeline |
| `wyoming-whisper` (host unit) | 10300 | faster-whisper STT on the GPU |
| `homeassistant` (`kronk-ha`) | 8123 (host net) | Voice pipeline broker, satellites, automations |
| `music-assistant-server` (`kronk-ma`) | 8095 (host net) | Players, YouTube Music, Pandora, NAS library |
| Langfuse (`langfuse`) | 3000 | Telemetry v1 traces |

nginx serves the UI pages (`/`, `/health`, `/finances`, `/solar`,
`/resources`, `/performance`, `/agents`, `/services`, `/shopping_list`),
proxies `/api/health/`, `/api/finance/` and `/api/solar/` to their services,
`/probe/*` to per-service health endpoints (including HA, MA and Langfuse),
and `/devstral/` straight to a devstral llama.cpp server (bypassing LiteLLM).

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
├─ LOOP  round_idx in range(MAX_TOOL_ROUNDS)
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

**Typical case — a 1-tool agent (finance, health):** two `llm.stream()` calls,
exits at round 1.

```
round 0:  stream → {"tool_calls":[query_health]}   (no tokens yet)
          → narration "looking up your sleep data"
          → execute query_health → append tool result
round 1:  stream → tokens... tokens... tokens...   ← STREAMS live to user
          → no tool_calls → yield {"done"} → RETURN
```

Before the unified-streaming refactor, the synthesis round was a blocking
`llm.complete()` whose content was dumped as one SSE chunk — so agents that
finished inside the tool-round budget (finance, health, home, assistant) never
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
- **The `retire_calc` service** builds from a sibling repo at
  `../retirement-calc`. Clone that alongside this repo, or comment the
  `retire_calc` service out of `docker-compose.yml` if you don't need it.

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

Running as of 2026-11-04:

| Model | systemd unit | Port | Quant | Ctx | VRAM | Role |
|---|---|---|---|---|---|---|
| `gemma-4-e4b` | `llama-gemma4-e4b` | 11438 | QAT UD-Q4_K_XL + MTP drafter | 32k | ~3.5 GB | Coordinator + health/research/home/assistant/finance agents |
| `devstral-2512-q4` | `llama-devstral-q4` | 11440 | Q4_K_M | 128k | ~14 GB | coding + devops agents |
| `gemma-3-4b` | `llama-gemma3-4b` | 11439 | Q4_K_M | 8k | ~3.0 GB | None — the LLM router it served was removed 2026-08-18 |

In the LiteLLM catalog but **not running**: `mistral-nemo` (11435), `bonsai-8b`
(11437, stopped and disabled 2026-11-04), `devstral-2512` Q8 (11436, no unit),
and `talkie` (11441, CPU). The `talkie` agent still exists in `AGENTS`, so it
fails until `llama-talkie` is started; `/services` shows it red.

### Agents

The `AGENTS` dict in `orchestrator/agents.py` is authoritative.

| Agent | Model | Tools | Purpose |
|---|---|---|---|
| coordinator | `gemma-4-e4b` | `ask_<agent>`, `news_brief` | Default lane: answers directly or delegates |
| `health` | `gemma-4-e4b` | `query_health` | Garmin/Withings: sleep, HRV, weight, steps, activities, resting HR, body battery |
| `research` | `gemma-4-e4b` | `web_search`, `fetch_url` | Live/current info — news, prices, recent releases |
| `home` | `gemma-4-e4b` | `get_weather`, `shopping_list_*`, `query_hottub`, `solar_{status,detail,energy}`, `play_music`, `control_music`, `now_playing`, `rate_music`, `remember_music_preference`, `update_magicmirror` | Weather, shopping list, hot tub, solar, music, mirror updates |
| `assistant` | `gemma-4-e4b` | `get_kronk_context`, `generate_diagram` | Kronk's own architecture; Graphviz diagrams |
| `finance` | `gemma-4-e4b` | `query_finances` | The operator's own financial documents |
| `coding` | `devstral-2512-q4` | `web_search`, `fetch_url` | Writing/debugging code |
| `devops` | `devstral-2512-q4` | `remote_exec`, `web_search`, `fetch_url` | Server admin; read-only audited exec on managed hosts |
| `talkie` | `talkie` | — | Vintage-1930 persona; only invoked when asked for by name (model not running) |

Timers are no longer Kronk's: HA's local Assist intent runs them on the
satellite (2026-07-12).

### Tools

`get_weather` (NWS), `web_search` (SearXNG), `fetch_url`, `news_brief`,
`query_health`, `query_finances`, `query_hottub`,
`shopping_list_{view,add,remove,clear}`, `solar_{status,detail,energy}`,
`play_music` (HA → Music Assistant; resolves misspellings and picks for genre
requests — `docs/features/music-taste.md`), `control_music`, `now_playing`,
`rate_music` (Pandora thumbs), `remember_music_preference` (household taste
file), `update_magicmirror`, `remote_exec`, `get_kronk_context`,
`generate_diagram`. Definitions live in
`orchestrator/tools.py`. (`query_bloodwork` and `search_health_data` are defined
but not yet wired to an agent — see Roadmap.)

The music tools and `update_magicmirror` are **terminal tools**
(`AgentConfig.terminal_tools`): the result is spoken verbatim and ends the
agent turn. Structural guardrail — small
models otherwise re-call the tool or contradict its result. The tool verifies
the player actually reaches `playing` before reporting success (MA queues
requests async, so a 200 from HA alone proves nothing).

### Error handling

Failures surface loud and specific at every layer (tenet 7 — "an unexpected
error occurred" is a bug): services return `detail` strings naming the real
cause, tool handlers preserve them via `tools._fail()`, a failed specialist
reaches the coordinator explicitly labeled `FAILED` (with an
invented-answer ban), failed turns mark their Langfuse trace `ERROR`, and a
last-resort guard in `_run_pipeline` turns unexpected crashes into a spoken
cause + rid instead of a dead stream.

How much of that detail the *user* sees is a toggle —
`orchestrator/errors.py`, config in `docker-compose.yml`:
`ERROR_STYLE=debug` (default: causes, HTTP statuses, rid) or `friendly`
(one clean sentence); `ERROR_STYLE_VOICE` overrides just the shim/voice
transport. Rendering only — logs and traces always keep full detail.
Details: [`docs/features/verbose-errors.md`](docs/features/verbose-errors.md).

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
| Langfuse telemetry | `docker-compose.langfuse.yml` | `kronk` (no `name:` key — shares the directory default) | port 3000 |

HA mounts the **shared** `kronk_ha-config` volume (declared `external: true`
in `docker-compose.ha.yml`). The split means `docker compose up -d --build
orchestrator` no longer flickers HA. HA still reaches kronk via `localhost`
because it runs with `network_mode: host`.

### Deploy / restart

- **Kronk code change:** `docker compose up -d --build <service>` — *not*
  `restart`, which reuses the old image.
- **HA restart:** `docker compose -f docker-compose.ha.yml restart homeassistant`.
- **HA upgrade:** `docker compose -f docker-compose.ha.yml pull && docker compose -f docker-compose.ha.yml up -d`.
- **nginx config change:** `docker compose restart nginx` — the config is
  volume-mounted and `nginx -s reload` won't pick it up.
- **`litellm/config.yaml`:** bind-mounted and hot-editable; no rebuild needed.

> **Volume safety:** never run `docker compose -f docker-compose.ha.yml down -v`
> — the `-v` flag wipes `kronk_ha-config` and you lose every HA integration,
> the admin account, the timer helper, and the voice pipeline config. Plain
> `down` (no `-v`) is safe.

### llama.cpp model servers

One **user** systemd unit per model at `~/.config/systemd/user/llama-*.service`,
each loading one model permanently into GPU memory. Units are named after the
model (not the role), so reassigning an agent only touches `docker-compose.yml`.
All units bind `127.0.0.1` — only the host-network `litellm` container can reach
them.

Reference copies of every unit live in [`systemd/`](systemd/). Install with:

```bash
cp systemd/llama-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llama-gemma4-e4b llama-devstral-q4
systemctl --user list-units 'llama-*'
```

> **Known wart (tech debt):** `llama-talkie.service` points its `-m` flag at
> `/home/drew/model-staging/talkie-lm/talkie-1930-13b-it-Q8_0.gguf` rather than
> `/opt/models/...` like every other unit. Cosmetic — service runs fine. Move
> the GGUF and update the unit when convenient.

Each unit's `ExecStart` sets the GGUF path, `--port`, `--ctx-size`, `-ngl 99`,
and `--flash-attn on`, plus the runtime environment:

```ini
Environment="HSA_OVERRIDE_GFX_VERSION=11.5.1"
Environment="LD_LIBRARY_PATH=/usr/local/lib/ollama/rocm:/home/drew/pai/pai_workspace/llama-cpp/llama-gfx1151-rocm7"
```

The `render` group is required for `/dev/kfd` access — verify with `groups drew`.

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
`/home/drew/model-staging/talkie-lm/`. Download with
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
`fetch_url` strips boilerplate with BeautifulSoup and truncates to ~1,500 tokens;
it uses `verify=False` because of an intermediate-CA gap inside the container
(read-only fetches, acceptable tradeoff, that endpoint only).

**Health data: manual import, not automatic sync.** Garmin Connect auto-sync was
abandoned after compounding problems — the `garminconnect` library's breaking
auth rewrite, fragile `curl_cffi` Cloudflare fingerprinting, Cloudflare 429s on
initial auth (~90 min lockout), and MFA blocking unattended first-auth entirely.
A weekly manual export (CSV or bulk zip, uploaded through the chat UI) captures
the same data with zero maintenance surface. If revisited, tokens would live at
`/data/garmin_tokens.json` and auth would run in a one-shot container.

**Hallucination guardrails — three layers, order matters.** (1) A system-prompt
standing rule ("never fabricate real-time info"). (2) Directive failure messages
— when a tool fails, an explicit `MUST NOT answer from training data` system
message, not a soft hint. (3) Structural tool-status lines — every tool result
is prefixed `[TOOL: weather — live NWS data]` or `[TOOL: weather — FAILED]`. The
status lines are the most reliable because they're structural, not
instructional; no single layer is enough on its own.

**Secrets: a few files, no manager.** `.env` (gitignored) holds `HA_TOKEN`
and `MA_TOKEN` (an MA long-lived token, 1-year expiry, no auto-renew), both
scoped to tool_service; `.env.langfuse` holds Langfuse's own keys;
`searxng/settings.yml` holds the SearXNG secret. NWS, Open-Meteo, SearXNG and
the local LLMs are unauthenticated. The old self-hosted Infisical instance was
retired; reintroduce a secrets manager only if this grows past a handful of
files.

**Dependencies: hash-pinned.** `requirements.txt` is human-maintained;
`requirements.lock` is machine-generated with SHA-256 hashes for every
transitive dep; Docker builds use `uv pip install --require-hashes`. Regenerate a
lockfile with `uv pip compile requirements.txt --generate-hashes -o
requirements.lock` from the service directory — never edit it by hand.

**Containers: non-root (2026-07-07).** Every kronk-built service runs as
UID 1000 (`USER kronk` in each Dockerfile; retire_calc via a compose
`user:` directive), matching the host operator — so container writes to
the `./data` bind mounts land drew-owned and no service holds root. The
health_service Dockerfile creates the user *before* the fastembed model
warmup so the baked cache is readable at runtime
(`FASTEMBED_CACHE_PATH=/home/kronk/.cache/fastembed`). Upstream images
(nginx, searxng, litellm, piper) keep their own user models.

---

## Roadmap

See [`ROADMAP.md`](ROADMAP.md) — the single source of truth for planned work
(and the shipped-features list; distilled feature docs live in
[`docs/features/`](docs/features/)).

---

*Historical benchmarks and the Ollama-era model history: [`docs/HISTORY.md`](docs/HISTORY.md).*
