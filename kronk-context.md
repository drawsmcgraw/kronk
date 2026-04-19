# Kronk System Context

Authoritative reference for Kronk's architecture, services, and configuration.
Call `get_kronk_context` before generating architecture diagrams or answering questions about the system.

---

## Services

### nginx — port 80 (published)
Reverse proxy. Single entry point for all browser traffic.
- `/ → orchestrator:8000` (chat UI, all orchestrator routes, SSE streaming)
- `/api/health/ → health_service:8004/api/`
- `/api/finance/ → finance_service:8005/api/`
- `/probe/*` → per-service health probes for the Services page
- SSE streaming: `proxy_buffering off`, extended timeouts
- Upload limit: 500 MB
- Runs on the `kronk` bridge; reaches host-bound services via `host.docker.internal` (host-gateway)

### orchestrator — port 8000
The brain. Manages conversation history, routes requests to specialist agents, streams responses.
- Serves: chat UI (`/`), services (`/services`), agents (`/agents`), resources (`/resources`), performance (`/performance`), shopping list (`/shopping_list`), finances (`/finances`), health (`/health`)
- Pipeline: Phase 1 router classifies (`gemma-3-4b`) → Phase 2 specialist agent runs its tool-calling loop and streams synthesis → Phase 3 coordinator fallback (`gemma-4-e4b`) when the router returns `direct` or the specialist fails
- Agent → model assignments come from env vars (`ROUTER_MODEL`, `COORDINATOR_MODEL`, `HEALTH_AGENT_MODEL`, `RESEARCH_AGENT_MODEL`, `FINANCE_AGENT_MODEL`, `CODING_AGENT_MODEL`, `DEVOPS_AGENT_MODEL`)
- `/api/servers` reads `litellm/config.yaml` + probes LiteLLM `/health` → powers the Resources page
- Serves generated diagrams at `/static/generated/`

### litellm — port 8002 (host network)
OpenAI-compatible proxy in front of the llama.cpp servers.
- Runs with `network_mode: host`, binds to `0.0.0.0:8002` so bridge containers can reach it via `host.docker.internal:8002`
- Host networking is required because the llama.cpp servers bind to `127.0.0.1` on the host — only a host-network process can reach them
- Config: `litellm/config.yaml` (bind-mounted). Each `model_list` entry has a `kronk:` block (`params`, `quant`, `vram_gb`, `ctx_k`) that LiteLLM ignores but the orchestrator reads for the Resources page
- `GET /health` is authoritative for per-backend health

### tool_service — port 8003
External integrations and utilities.
- **Weather**: NWS api.weather.gov (US only; geocoded via Open-Meteo)
- **Web search**: SearXNG at `http://searxng:8080`
- **URL fetch**: BeautifulSoup text extraction, `~4000` token truncation, full browser UA, structured `{ok: bool, error}` failure envelope (so agents can retry a different URL)
- **Shopping list**: JSON at `/data/shopping_list.json`
- **Diagram generation**: Graphviz `dot` → PNG → `/data/generated/`. ALWAYS call `generate_diagram` tool — never write diagram code as a code block.

### health_service — port 8004
Garmin + Withings health data.
- Storage: SQLite at `/data/health.db`
- Tables: `daily_summary`, `sleep`, `hrv`, `body_battery`, `activities`, `weight`, `body_composition`
- Import: manual via Garmin Connect export (zip or CSV) uploaded through chat UI; Withings sync where credentials are configured
- Dashboard at `/health` (Chart.js, 4w / 12w / 1yr period comparison)
- Query API: `GET /api/query?metric=<metric>&days=<n>&end_date=<YYYY-MM-DD>`
- Metrics: sleep, hrv, activities, steps, calories, stress, resting_hr, body_battery, distance, weight, body_composition, all
- Garmin live sync is currently stubbed — imports happen via manual upload

### finance_service — port 8005
Financial document store and query API.
- Stores uploaded PDFs (bank statements, tax docs, investment summaries)
- `query_finances` tool performs substring/semantic search over stored excerpts

### searxng — port 8080 (internal only)
Self-hosted meta-search. Used only by `tool_service` for `web_search`.

### llama.cpp servers — on host, user systemd units
Each model runs as a separate `llama-server` instance bound to `127.0.0.1` on the host.

| Service | Model | Port | GGUF | Agents |
|---|---|---|---|---|
| `llama-gemma3-4b` | gemma-3-4b | 11439 | `google/gemma-3-4b-it-Q4_K_M.gguf` | router |
| `llama-gemma4-e4b` | gemma-4-e4b | 11438 | `google/google_gemma-4-E4B-it-Q4_K_M.gguf` | coordinator, health, research, home, assistant, finance |
| `llama-devstral` | devstral-2512 | 11436 | `mistralai/mistralai_Devstral-Small-2-24B-Instruct-2512-Q8_0.gguf` | coding, devops |
| `llama-mistral-nemo` | mistral-nemo | 11435 | Mistral-NeMo Q8_0 | (available; no current agent) |
| `llama-bonsai` | bonsai-8b | 11437 | `bonsai/Bonsai-8B.gguf` | (available; no current agent) |

Units in `~/.config/systemd/user/llama-*.service`. GPU: AMD Radeon 8060S (gfx1151), ROCm via `HSA_OVERRIDE_GFX_VERSION=11.5.1`. Bind to `127.0.0.1` — only the host-network `litellm` can reach them.

---

## Data Stores

| Store | Path | Owner | Notes |
|---|---|---|---|
| `health.db` | `/data/health.db` | health_service | SQLite, Garmin data back to 2006 |
| `metrics.db` | `/data/metrics.db` | orchestrator | TTFT / tok/s per agent and model |
| `shopping_list.json` | `/data/shopping_list.json` | tool_service | JSON |
| Generated diagrams | `/data/generated/` | tool_service writes, orchestrator serves | Graphviz PNGs |
| `garmin_tokens.json` | `/data/garmin_tokens.json` | health_service | Reserved for future live sync |

---

## Message Pipeline

```
User (browser)
  └─► nginx :80
        └─► orchestrator :8000
              │
              ├─► Phase 1: route
              │     └─► litellm :8002 → llama-gemma3-4b :11439  (gemma-3-4b)
              │         returns one of: health | research | home | finance |
              │                         coding | devops | assistant | direct
              │
              ├─► Phase 2: specialist agent (if routed to one)
              │     ├─► plan rounds (non-streaming, tool-calling)
              │     │     └─► litellm :8002 → llama-<agent-model>
              │     ├─► tool calls (per agent's allow-list)
              │     │     └─► tool_service / health_service / finance_service
              │     └─► synthesis (streaming SSE back to browser)
              │           └─► litellm :8002 → llama-<agent-model>
              │
              ├─► Phase 3: coordinator fallback (routed=direct OR specialist errored)
              │     └─► litellm :8002 → llama-gemma4-e4b :11438  (gemma-4-e4b)
              │
              └─► metrics.db  (TTFT, tok/s, per agent/model)
```

---

## Agents

Defined in `orchestrator/agents.py` (`AGENTS` dict — single source of truth). Router prompt, valid-route set, and `/api/agents` roster are all derived from it.

| Agent | Tools | Default model |
|---|---|---|
| health | `query_health` | gemma-4-e4b |
| research | `web_search`, `fetch_url` | gemma-4-e4b |
| home | `get_weather`, `shopping_list_*` | gemma-4-e4b |
| assistant | `get_kronk_context`, `generate_diagram` | gemma-4-e4b |
| finance | `query_finances` | gemma-4-e4b |
| coding | `web_search`, `fetch_url` | devstral-2512 |
| devops | `web_search`, `fetch_url` | devstral-2512 |

Each specialist runs a tool-calling loop (up to 3 plan rounds) then streams a final synthesis. The old regex-based intent detection has been fully replaced by this agentic loop.

---

## Infrastructure

- **Deployment**: Docker Compose. Bridge network `kronk` for all services except `litellm`, which runs `network_mode: host` so it can reach the `127.0.0.1`-bound llama.cpp servers.
- **Inter-container reach**: containers address each other by service name (`orchestrator`, `tool_service`, etc.). They reach the host-network LiteLLM via `host.docker.internal:8002` (host-gateway extra host).
- **Published ports**: only `nginx:80`. Everything else is internal.
- **Host**: Framework AMD Ryzen AI 375, 122 GB RAM, hostname `kronk`.
- **GPU / LLM backend**: llama.cpp built from source for gfx1151 with ROCm 7.2. Binaries in `pai_workspace/llama-cpp/`.
- **Model storage**: `/opt/models/` (GGUF files).
- **Dependency pinning**: every Python service pins to hashed lockfiles (`requirements.lock`, built with `uv pip compile --generate-hashes`). Docker builds install with `--require-hashes`.
- **Secrets**: none currently required. Garmin tokens will live at `/data/garmin_tokens.json` if live sync is re-enabled.
