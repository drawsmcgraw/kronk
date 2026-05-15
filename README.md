# Kronk — Self-Hosted Home AI Assistant

Kronk is a fully-local, privacy-first home AI assistant. It runs entirely on one
machine — no cloud APIs, no data leaving the house. A chat UI (voice planned)
sits in front of a **router → specialist-agent → coordinator** pipeline over
local llama.cpp models. It handles weather, web search, personal health data
(Garmin/Withings), financial documents, home device status, coding/devops help,
and questions about its own architecture.

**Machine:** Framework desktop — AMD Ryzen AI 375 (hostname `kronk`), Radeon
8060S integrated GPU (GFX1151), 122 GB RAM.

**Last updated:** 2026-05-14 — unified-streaming agent loop. Older benchmarks and
the Ollama-era model history live in [`docs/HISTORY.md`](docs/HISTORY.md).

---

## Architecture at a glance

### Request pipeline

Every chat message flows through three phases:

1. **Routing** — `gemma-3-4b` classifies the message into one of eight agents
   (`health`, `research`, `home`, `assistant`, `finance`, `coding`, `devops`,
   `talkie`) or `direct`.
2. **Specialist** — the routed agent runs a unified streaming tool-calling loop
   (see below). Each agent has an allow-listed tool set.
3. **Coordinator fallback** — if the router returned `direct`, or the specialist
   errored, `gemma-4-e4b` streams a response from the accumulated context.

Agent → model assignments come from env vars in `docker-compose.yml`
(`ROUTER_MODEL`, `COORDINATOR_MODEL`, `HEALTH_AGENT_MODEL`, …), so routing
changes don't need code edits. The `AGENTS` dict in `orchestrator/agents.py` is
the single source of truth for agents, their tools, and the router prompt.

### Service map

All containers run on the `kronk` bridge network and address each other by
service name — except `litellm`, which uses host networking (see Design
Decisions). llama.cpp model servers run on the **host** as systemd units, not
containers.

| Service | Port | Role |
|---|---|---|
| `nginx` | 80 | Reverse proxy; SSE pass-through (`proxy_buffering off`) |
| `orchestrator` | 8000 | UI, conversation history, the pipeline, tool dispatch |
| `litellm` | 8002 (host net) | OpenAI-compatible proxy in front of the llama.cpp servers |
| `tool_service` | 8003 | Weather, web search, URL fetch, shopping list, diagrams |
| `health_service` | 8004 | Garmin/Withings data, SQLite at `/data/health.db`, `/health` dashboard |
| `finance_service` | 8005 | Financial-document search over uploaded PDFs |
| `searxng` | 8080 (internal) | Self-hosted meta-search engine, used by `tool_service` |
| `retire_calc` | 8080 (internal) | Retirement-calculator app (built from `../retirement-calc`), proxied at `/retire/` |

nginx also proxies `/api/health/` and `/api/finance/` to their services,
`/probe/*` to per-service health endpoints, and `/devstral/` directly to the
devstral llama.cpp server (bypassing LiteLLM).

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
page.

| Model | systemd unit | Port | Quant | Ctx | VRAM | Role |
|---|---|---|---|---|---|---|
| `gemma-3-4b` | `llama-gemma3-4b` | 11439 | Q4_K_M | 8k | ~3.0 GB | Router |
| `gemma-4-e4b` | `llama-gemma4-e4b` | 11438 | Q4_K_M | 32k | ~3.5 GB | Coordinator + health/research/home/assistant/finance agents |
| `devstral-2512-q4` | `llama-devstral-q4` | 11440 | Q4_K_M | 128k | ~14 GB | coding + devops agents |
| `mistral-nemo` | `llama-mistral-nemo` | 11435 | Q8_0 | 16k | ~13 GB | Unassigned |
| `bonsai-8b` | `llama-bonsai` | 11437 | Q1_0 | 32k | ~1.1 GB | Unassigned |
| `talkie` | `llama-talkie` | 11441 | Q8_0 | 2k | CPU | Talkie agent (vintage-1930 persona) |

> **Note:** the LiteLLM catalog also defines `devstral-2512` (Q8) at port `11436`,
> but no systemd unit currently serves it. The coding and devops agents use the
> running Q4 variant (`devstral-2512-q4` on `11440`). Start a Q8 unit on `11436`
> if the higher-fidelity quant is wanted.

### Agents

The `AGENTS` dict in `orchestrator/agents.py` is authoritative.

| Agent | Model | Tools | Purpose |
|---|---|---|---|
| `health` | `gemma-4-e4b` | `query_health` | Garmin/Withings: sleep, HRV, weight, steps, activities, resting HR, body battery |
| `research` | `gemma-4-e4b` | `web_search`, `fetch_url` | Live/current info — news, prices, recent releases |
| `home` | `gemma-4-e4b` | `get_weather`, `shopping_list_*`, `query_hottub` | Weather, shopping list, hot tub status |
| `assistant` | `gemma-4-e4b` | `get_kronk_context`, `generate_diagram` | Kronk's own architecture; Graphviz diagrams |
| `finance` | `gemma-4-e4b` | `query_finances` | Search uploaded financial PDFs |
| `coding` | `devstral-2512-q4` | `web_search`, `fetch_url` | Writing/debugging code |
| `devops` | `devstral-2512-q4` | `web_search`, `fetch_url` | Server admin, Docker, systemd, networking |
| `talkie` | `talkie` | — | Vintage-1930 persona; only invoked when asked for by name |

### Tools

`get_weather` (NWS), `web_search` (SearXNG), `fetch_url`, `query_health`,
`query_finances`, `query_hottub`, `shopping_list_{view,add,remove,clear}`,
`get_kronk_context`, `generate_diagram`. Definitions live in
`orchestrator/tools.py`. (`query_bloodwork` and `search_health_data` are defined
but not yet wired to an agent — see Roadmap.)

---

## Operations runbook

### Deploy / restart

- **Code change:** `docker compose up -d --build <service>` — *not* `restart`,
  which reuses the old image.
- **nginx config change:** `docker compose restart nginx` — the config is
  volume-mounted and `nginx -s reload` won't pick it up.
- **`litellm/config.yaml`:** bind-mounted and hot-editable; no rebuild needed.

### llama.cpp model servers

One **user** systemd unit per model at `~/.config/systemd/user/llama-*.service`,
each loading one model permanently into GPU memory. Units are named after the
model (not the role), so reassigning an agent only touches `docker-compose.yml`.
All units bind `127.0.0.1` — only the host-network `litellm` container can reach
them.

```bash
systemctl --user daemon-reload
systemctl --user enable --now llama-gemma3-4b llama-gemma4-e4b llama-devstral-q4
systemctl --user list-units 'llama-*'
```

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

GGUFs live under `/opt/models/{google,mistralai,bonsai}/`; `talkie` is staged at
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

**Secrets: none required.** Every external dependency (NWS, Open-Meteo, SearXNG,
local LLMs) is unauthenticated; health imports are manual; finance works over
uploaded docs. The old self-hosted Infisical instance was retired. If a future
integration needs a credential, the plan is a host volume at
`/data/<service>_tokens.json` bind-mounted into the one service that needs it —
reintroduce a secrets manager only if that grows past a handful of files.

**Dependencies: hash-pinned.** `requirements.txt` is human-maintained;
`requirements.lock` is machine-generated with SHA-256 hashes for every
transitive dep; Docker builds use `uv pip install --require-hashes`. Regenerate a
lockfile with `uv pip compile requirements.txt --generate-hashes -o
requirements.lock` from the service directory — never edit it by hand.

---

## Roadmap

**In progress**
- **Health RAG + bloodwork parsing** — `health_service/bloodwork_parser.py`,
  `chunker.py`, `vector_store.py`; `query_bloodwork` and `search_health_data`
  tools are defined in `orchestrator/tools.py` but not yet wired to an agent.
- **Voice pipeline** — STT + TTS + wake word; currently stubbed in the UI.

**On the table**
- Reclaim Ollama blob storage (`/usr/share/ollama/.ollama/models/blobs/`, ~50+ GB)
  now that the llama.cpp setup is stable.
- More tools — Philips Hue, calendar, home automation.
- Additional health sources — Fitbit (family member), Withings scale (sync
  scaffolding in `health_service/withings_sync.py`).
- Publish the shopping-list page externally so it works off the home network.
- Garmin live sync remains blocked on MFA / Cloudflare (see Design Decisions).

**Recently shipped**
- Unified-streaming agent loop — every agent streams token-by-token; `llm.stream()`
  accumulates `tool_calls` from deltas and raises on LiteLLM 5xx.
- Migration from Ollama to from-source llama.cpp behind a LiteLLM proxy.
- Router → specialist → coordinator pipeline, replacing regex intent detection.
- `query_health` tool + `/health` dashboard; Infisical retired.

---

*Historical benchmarks and the Ollama-era model history: [`docs/HISTORY.md`](docs/HISTORY.md).*
