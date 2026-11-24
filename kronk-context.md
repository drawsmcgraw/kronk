# Kronk System Context

Authoritative reference for Kronk's architecture, services, and configuration.
Call `get_kronk_context` before generating architecture diagrams or answering questions about the system.

---

## Services

### nginx — port 80 (published)
Reverse proxy. Single entry point for all browser traffic.
- `/ → orchestrator:8000` (chat UI, all orchestrator routes, SSE streaming)
- `/api/solar/ → tool_service:8003/solar/`
- `/probe/*` → per-service health probes for the Services page
- SSE streaming: `proxy_buffering off`, extended timeouts
- Upload limit: 500 MB
- Runs on the `kronk` bridge; reaches host-bound services via `host.docker.internal` (host-gateway)

### orchestrator — port 8000
The brain. Manages conversation history, routes requests to specialist agents, streams responses.
- Serves: chat UI (`/`), services (`/services`), agents (`/agents`), resources (`/resources`), performance (`/performance`), shopping list (`/shopping_list`), solar (`/solar`)
- Pipeline: Phase 1 routing rules (`orchestrator/routing.py`, no model; there has been no LLM router since 2026-08-18) pin a request to a specialist or send it to the coordinator → Phase 2 specialist agent runs its tool-calling loop and streams synthesis → Phase 3 coordinator (`gemma-4-e4b`) answers or delegates, and takes over when a specialist fails (not for Talkie: its failure is the reply)
- Agent → model assignments come from env vars (`COORDINATOR_MODEL`, `RESEARCH_AGENT_MODEL`, `CODING_AGENT_MODEL`, `DEVOPS_AGENT_MODEL`, `TALKIE_MODEL`)
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

### searxng — port 8080 (internal only)
Self-hosted meta-search. Used only by `tool_service` for `web_search`.

### llama.cpp servers — on host, user systemd units
Each model runs as a separate `llama-server` instance bound to `127.0.0.1` on the host.

| Service | Model | Port | GGUF | Agents |
|---|---|---|---|---|
| `llama-gemma4-e4b` | gemma-4-e4b | 11438 | `google/gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf` (+ MTP drafter) | coordinator, research, home, assistant |
| `llama-devstral-q4` | devstral-2512-q4 | 11440 | `mistralai/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf` | coding, devops |
| `llama-talkie` | talkie | 11441 | `talkie-1930-13b-it-Q8_0.gguf` (in `~/model-staging/talkie-lm/`), CPU only | talkie (only when asked for by name) |

Units in `~/.config/systemd/user/llama-*.service`. GPU: AMD Radeon 8060S (gfx1151); the two GPU units use a Vulkan build of llama.cpp, Talkie a ROCm build run on the CPU only (`--device none -ngl 0`). Bind to `127.0.0.1` — only the host-network `litellm` can reach them. The four other servers (gemma-3-4b, bonsai-8b, mistral-nemo, devstral-2512 Q8) were retired 2026-11-24.

---

## Data Stores

| Store | Path | Owner | Notes |
|---|---|---|---|
| `metrics.db` | `/data/metrics.db` | orchestrator | TTFT / tok/s per agent and model |
| `shopping_list.json` | `/data/shopping_list.json` | tool_service | JSON |
| Generated diagrams | `/data/generated/` | tool_service writes, orchestrator serves | Graphviz PNGs |

---

## Message Pipeline

```
User (browser)
  └─► nginx :80
        └─► orchestrator :8000
              │
              ├─► Phase 1: route (routing rules, no model)
              │         returns one of: research | home | coding | devops |
              │                         assistant | talkie | direct
              │
              ├─► Phase 2: specialist agent (if routed to one)
              │     ├─► plan rounds (non-streaming, tool-calling)
              │     │     └─► litellm :8002 → llama-<agent-model>
              │     ├─► tool calls (per agent's allow-list)
              │     │     └─► tool_service
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

Defined in `orchestrator/agents.py` (`AGENTS` dict — single source of truth). The valid-route set and the `/api/agents` roster are derived from it. Each agent's system prompt is a plain file, `prompts/<name>.md`; round budgets and tool timeouts are in `tuning.yaml`, music thresholds in `music.yaml`, all in the instance's live config directory (`kronk-config/prod`; the repo's `config/` holds the shipped defaults). The `/settings` page shows every value in effect and edits them behind a passphrase.

| Agent | Tools | Default model |
|---|---|---|
| research | `web_search`, `fetch_url` | gemma-4-e4b |
| home | `get_weather`, `shopping_list_*` | gemma-4-e4b |
| assistant | `get_kronk_context`, `generate_diagram` | gemma-4-e4b |
| coding | `web_search`, `fetch_url` | devstral-2512-q4 |
| devops | `remote_exec` (read-only, audited), `http_check` (one GET to any address the host can reach; no body returned); no web tools — research looks things up first | devstral-2512-q4 |
| talkie | none; a 1930s persona, only when asked for by name ("ask talkie …") | talkie |

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
- **Personal data**: Kronk holds no health, fitness or financial records (those features were removed 2026-11-15).
