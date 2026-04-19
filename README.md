# Kronk AI Server — Build Notes & Model Analysis

**Last updated:** 2026-04-16 (agentic loop, LiteLLM proxy, infisical retired)
**Machine:** Framework AMD Ryzen AI 375 (hostname: kronk)
**GPU:** Radeon 8060S (GFX1151) — integrated GPU
**RAM:** 122 GB

---

## Current State (April 2026)

- **Models:** Five llama.cpp server instances, each bound to a dedicated port on the host:
  - `gemma-3-4b` (Q4_K_M, port 11439) — router
  - `gemma-4-e4b` (Q4_K_M, port 11438) — coordinator + health/research/home/assistant/finance agents
  - `devstral-2512` (Devstral-Small-2-24B-Instruct-2512 Q4_K_M, port 11436) — coding and devops agents
  - `mistral-nemo` (Q8_0, port 11435) — available, no current agent assignment
  - `bonsai-8b` (Q1_0, port 11437) — available, no current agent assignment
- **Backend:** llama.cpp built from source for gfx1151 (ROCm 7.2), one user-systemd unit per model (`~/.config/systemd/user/llama-*.service`). Ollama has been retired.
- **LiteLLM proxy:** OpenAI-compatible proxy on port 8002 (`network_mode: host`) in front of the llama.cpp servers. Containers reach it via `host.docker.internal:8002`. Model catalog lives in `litellm/config.yaml` (bind-mounted, hot-editable).
- **Pipeline per message:** Phase 1 router (`gemma-3-4b`) classifies → Phase 2 specialist agent runs its own tool-calling loop and streams synthesis → Phase 3 coordinator (`gemma-4-e4b`) fallback when the router returns `direct` or the specialist errors. The old regex-based intent detection has been fully replaced.
- **Health data:** Garmin Connect exports (CSV or bulk zip) imported manually via the chat UI file upload. Stored in SQLite. Accessible via `query_health` tool with `metric / days / end_date` parameters. Withings sync where credentials are configured.
- **Finance data:** uploaded PDFs searchable via `query_finances` tool.
- **All services running:** orchestrator (8000), litellm (8002, host network), tool_service (8003), health_service (8004), finance_service (8005), searxng (8080, internal), nginx (80).
- **Secrets:** none currently required. Garmin tokens would live at `/data/garmin_tokens.json` if live sync is re-enabled.

---

## Hardware Reality

The Radeon 8060S is an integrated GPU — it has no dedicated VRAM. Instead it carves memory out of system RAM via a mechanism called GTT (Graphics Translation Table). Ollama/ROCm initially saw ~61.9 GiB of "GPU memory" — the ROCm driver default of roughly half of total system RAM.

The GTT ceiling has since been raised to **~101.6 GB** (see below).

### Raising the GTT ceiling

The default ~50% limit is a driver policy, not a hardware constraint. It can be raised via kernel boot parameters.

**What does NOT work:**
- `amdgpu.gttsize` — deprecated, throws a kernel warning, ignored on modern kernels
- `amdttm.pages_limit` — the module is named `ttm`, not `amdttm`; this parameter is silently ignored

**What works (kernel 6.17+):**
```
ttm.pages_limit=VALUE
ttm.page_pool_size=VALUE
```

Set in `/etc/default/grub`:
```
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash ttm.pages_limit=26624000 ttm.page_pool_size=26624000"
```

Then `sudo update-grub` and reboot.

**Value calculation:** `([size in GB] * 1024 * 1024) / 4.096`
- 104 GB → `26624000` (what we used — results in ~101.6 GB after driver rounding)
- 108 GB → `27648000` (Jeff Geerling's tested maximum on identical silicon before segfaults)

**Verify after reboot:**
```bash
awk '{printf "%.1f GB\n", $1/1024/1024/1024}' /sys/class/drm/card1/device/mem_info_gtt_total
cat /sys/module/ttm/parameters/pages_limit
```

**Safety ceiling:** Do not exceed 108 GB (~27648000). Jeff Geerling confirmed 110 GB causes segfaults on the same Strix Halo silicon (AI Max+ 395 / Radeon 890M, GFX1151).

**GTT is dynamic:** Allocations are not permanently reserved — the OS can reclaim GTT memory when the GPU isn't using it. Raising the ceiling does not reduce available system RAM at idle.

---

## LLM Backend: llama.cpp Server

### Why migrate from Ollama

Ollama is a process manager that wraps llama.cpp internally. The underlying inference engine is the same. Migrating to llama.cpp server directly gives:
- Full control over server flags (flash attention, context size, GPU targets)
- No model format conversion — llama.cpp uses GGUF natively
- OpenAI-compatible API throughout, no translation layer
- Slightly lower overhead (no Ollama wrapper process)

Performance results after migration (historical, see benchmark sections below): pixtral-12b achieved **133ms avg TTFT** at **17.5 tok/s**; devstral-2512 achieves **~350ms warm TTFT** at **8.7 tok/s**. Current router/coordinator uses the smaller gemma-3/gemma-4-e4b pair for faster routing.

### Building llama.cpp for gfx1151

The pre-built ROCm binaries on GitHub releases do not include gfx1151 in their GPU target list — they report "no ROCm-capable device" even with `HSA_OVERRIDE_GFX_VERSION=11.5.1`. Must build from source.

Ubuntu 25.10 (Questing) has dependency conflicts with the ROCm apt packages. Solution: build inside a ROCm 7.2 Docker container (matches Ollama's bundled ROCm runtime version), then copy the binary and shared libs out.

**Build command (inside `rocm/dev-ubuntu-24.04:7.2-complete`):**
```bash
cmake -B build \
  -DGGML_HIP=ON \
  -DAMDGPU_TARGETS="gfx1151" \
  -DGGML_HIP_NO_VMM=ON \
  -DBUILD_SHARED_LIBS=ON
cmake --build build --config Release -j$(nproc) --target llama-server
```

Key flags:
- `GGML_HIP_NO_VMM=ON` — required for iGPU (no VMM support on integrated GPU)
- `BUILD_SHARED_LIBS=ON` — required to generate `libggml-hip.so` alongside the binary
- Do NOT set `GGML_HIP_ROCWMMA_FATTN=ON` — causes build failure on ROCm 6.x; removed, not needed on 7.2

Binary and all shared libs live at `pai_workspace/llama-cpp/llama-gfx1151-rocm7/`.

### Runtime environment

Reuses Ollama's bundled ROCm runtime — no system-wide ROCm install needed:
```
HSA_OVERRIDE_GFX_VERSION=11.5.1
LD_LIBRARY_PATH=/usr/local/lib/ollama/rocm:/home/drew/pai/pai_workspace/llama-cpp/llama-gfx1151-rocm7
```

The `render` group is required for `/dev/kfd` access. Verify with `groups drew` — if missing: `sudo usermod -aG render drew` then log out and back in.

### Systemd service units

One **user** systemd unit per model, each loading one model permanently into GPU memory. Units live at `~/.config/systemd/user/llama-*.service`. Naming convention: unit named after the model, not a role (so agent → model reassignment only touches `docker-compose.yml`).

Current units:

| Unit | Model | Port | GGUF |
|---|---|---|---|
| `llama-gemma3-4b`   | gemma-3-4b   | 11439 | `google/gemma-3-4b-it-Q4_K_M.gguf` |
| `llama-gemma4-e4b`  | gemma-4-e4b  | 11438 | `google/google_gemma-4-E4B-it-Q4_K_M.gguf` |
| `llama-devstral`    | devstral-2512 | 11436 | `mistralai/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf` |
| `llama-mistral-nemo`| mistral-nemo | 11435 | Mistral-NeMo Q8_0 |
| `llama-bonsai`      | bonsai-8b    | 11437 | `bonsai/Bonsai-8B.gguf` |

Shape of each unit (example, `llama-gemma4-e4b.service`):
```ini
[Service]
Environment="HSA_OVERRIDE_GFX_VERSION=11.5.1"
Environment="LD_LIBRARY_PATH=/usr/local/lib/ollama/rocm:/home/drew/pai/pai_workspace/llama-cpp/llama-gfx1151-rocm7"
ExecStart=/home/drew/pai/pai_workspace/llama-cpp/llama-gfx1151-rocm7/llama-server \
    -m /opt/models/google/google_gemma-4-E4B-it-Q4_K_M.gguf \
    --host 127.0.0.1 --port 11438 -ngl 99 --flash-attn on --ctx-size 131072
```

All units bind to `127.0.0.1` — only the host-network `litellm` container can reach them. Manage with `systemctl --user`:
```bash
systemctl --user daemon-reload
systemctl --user enable --now llama-gemma3-4b llama-gemma4-e4b llama-devstral
```

### VRAM budget at steady state

| Model | Size | Quant | VRAM |
|---|---|---|---|
| devstral-2512 | 24B | Q4_K_M | ~14 GB |
| mistral-nemo  | 12B | Q8_0   | ~13 GB |
| gemma-4-e4b   | 4B  | Q4_K_M | ~3.5 GB |
| gemma-3-4b    | 4B  | Q4_K_M | ~3.0 GB |
| bonsai-8b     | 8B  | Q1_0   | ~1.1 GB |
| KV caches     | —   | —      | ~4 GB |
| **Total**     |     |        | **~39 GB / 102 GB** |

Numbers in `litellm/config.yaml` under each model's `kronk:` block are edit-in-place estimates — adjust without rebuild. ~60 GB headroom remaining.

### Model storage

All models at `/opt/models/mistralai/`. Download with:
```bash
hf download bartowski/<repo> --include "*Q8_0*" --local-dir /opt/models/mistralai/
```

---

## GPU Backend: ROCm vs Vulkan

### What we expected
The setup guide (written March 2026) warned that ROCm had incomplete support for GFX1150/1151 and recommended Vulkan via a manually-built llama.cpp as the primary inference path. It noted that Vulkan could access ~88 GiB via GTT vs ROCm's more limited pool.

### What actually happened
Ollama 0.19.0 has ROCm support for GFX1151 out of the box. On first install, Ollama immediately detected the GPU and ran inference at 100% GPU via ROCm — no manual configuration needed.

We tried enabling Vulkan anyway (`OLLAMA_VULKAN=1` in the Ollama systemd service) to see if it would unlock more GPU memory as the guide suggested. It did not — Ollama saw the Vulkan flag but still chose ROCm as the preferred backend, and GPU memory stayed at 61.9 GiB.

**Decision:** Use ROCm. It works out of the box and Ollama prefers it. Vulkan is configured as a fallback but isn't active.

---

## Architecture Decisions

### LLM servers outside Docker

All llama.cpp server instances run on the host as user-systemd units, not in Docker. Reasoning:
- ROCm device passthrough into Docker (`/dev/kfd`, `/dev/dri`, group IDs, capabilities) is non-trivial and fragile
- Models are large files on host storage (`/opt/models/`); no need to bind-mount into containers
- Each server binds to `127.0.0.1`; only the `litellm` container (`network_mode: host`) can reach them

Ollama was retired after the llama.cpp migration. Its model blob storage (`/usr/share/ollama/.ollama/models/blobs/`, ~50+ GB) can be reclaimed once the new setup is confirmed stable.

### litellm on host networking, the rest on a bridge
The application containers (`orchestrator`, `tool_service`, `health_service`, `finance_service`, `searxng`, `nginx`) all run on the `kronk` bridge network and address each other by service name. Only `litellm` runs on `network_mode: host`, because llama.cpp binds to `127.0.0.1` and the host-gateway shim resolves to `172.17.0.1` — an unreachable address for loopback-bound servers.

Bridge containers reach LiteLLM via `host.docker.internal:8002` (an `extra_hosts` entry resolving to `host-gateway`). LiteLLM itself binds `0.0.0.0:8002` on the host for this reason.

Tradeoff: `network_mode: host` only works on Linux (not Mac/Windows Docker Desktop). Fine for this machine, worth noting if the stack ever moves.

### Service design
- **nginx** (port 80): reverse proxy. Required `proxy_buffering off` to pass SSE tokens through without buffering. Proxies `/ → orchestrator`, `/api/health/ → health_service`, `/api/finance/ → finance_service`, `/probe/* → per-service health endpoints`.
- **orchestrator** (port 8000): manages conversation history, serves the UI, runs the router → specialist → coordinator pipeline, owns tool dispatch.
- **litellm** (port 8002, host network): OpenAI-compatible proxy in front of the llama.cpp servers. Config at `litellm/config.yaml` is bind-mounted and hot-editable; each model entry's `kronk:` block carries UI metadata (params/quant/vram_gb/ctx_k) that LiteLLM ignores but the orchestrator reads for the Resources page.
- **tool_service** (port 8003): external API integrations — weather, web search, URL fetch, shopping list, Graphviz diagram generation.
- **health_service** (port 8004): Garmin + Withings health data, SQLite at `/data/health.db`, dashboard at `/health`.
- **finance_service** (port 8005): financial document store. `query_finances` tool does substring/semantic search over uploaded PDFs.
- **searxng** (port 8080, internal only): self-hosted meta-search engine. Used by tool_service for web search.

### Services directory page

A `/services` page is served by the orchestrator, listing all services with status indicators that ping each service's health endpoint every 30 seconds. Provides clickable links to all web UIs from one place. A parallel `/resources` page renders the LLM model catalog dynamically from `/api/servers` (which reads `litellm/config.yaml` + probes LiteLLM `/health`).

### Tool integration pattern
Each specialist agent runs its own tool-calling loop. The pipeline:

1. **Phase 1 — routing**. A small, fast router model (`gemma-3-4b`) classifies the message into one of: `health`, `research`, `home`, `assistant`, `finance`, `coding`, `devops`, or `direct`.
2. **Phase 2 — specialist**. If routed to an agent, the agent's model runs up to 3 non-streaming "plan" rounds, emitting real `tool_calls`. Each agent has an allow-listed tool set (see `orchestrator/agents.py` — the `AGENTS` dict is the single source of truth). After planning, the same model streams a final synthesis over SSE.
3. **Phase 3 — coordinator fallback**. If the router returned `direct`, or the specialist errored, the coordinator model (`gemma-4-e4b`) streams a response using the accumulated context.

This replaces the old regex-based intent detection. Agent → model assignments come from env vars (`ROUTER_MODEL`, `COORDINATOR_MODEL`, `HEALTH_AGENT_MODEL`, etc.) so routing changes don't require code edits.

---

## Tools

### Weather — National Weather Service (api.weather.gov)

**Why NWS over Open-Meteo:** NWS is a US government service, free, no API key, and provides significantly richer data — named forecast periods with narrative descriptions ("patchy fog before 8am"), hourly breakdowns, and active weather alerts. Open-Meteo gives a snapshot; NWS gives a story.

**Two-step flow:**
1. Geocode via Open-Meteo (NWS has no geocoder) to get lat/lon
2. `GET /points/{lat},{lon}` → NWS grid assignment → parallel fetch of hourly forecast, named periods, and alerts via `asyncio.gather()`

**US-only limitation and fallback:** NWS only covers US locations. When it returns non-200, the pipeline doesn't fail silently — it falls back to a web search for "current weather [location]". The model is told explicitly that the data came from web search, not a live feed. If both fail, the model is told to say so rather than guess.

**Keep Open-Meteo for geocoding:** NWS provides no location lookup. Open-Meteo's geocoding API is free, returns results in a consistent format, and works globally — keeping it for this step is the right call.

### Web Search — SearXNG (self-hosted)

**Why self-hosted:** Privacy was the primary driver. A home assistant that sends every query to Google or Bing defeats the point of running locally. SearXNG is a meta-search engine — it queries multiple sources on your behalf and returns aggregated results. Queries never leave the house.

**Why SearXNG over a search API:** No API key, no rate limits, no cost. The tradeoff is result quality can vary vs. a dedicated paid API, but for a home assistant context it's more than adequate.

**Snippet-only approach:** Search results are injected as title + URL + snippet, not full page content. This keeps context size small. If the model or user needs the full article, a URL can be passed to the fetch tool for a deep dive.

### URL Fetch

Fetches a URL, strips boilerplate (nav, header, footer, scripts) with BeautifulSoup, collapses whitespace, and truncates to ~1,500 tokens (~6,000 chars). This keeps the context injection from blowing up the context window on long pages.

**`verify=False` on httpx:** SSL certificate verification fails inside the container even after installing `ca-certificates` and `certifi`. The failure is an intermediate CA gap in the container environment, not a problem with the target sites. Since this is a read-only fetch for a home assistant, `verify=False` is an acceptable tradeoff. Only the fetch endpoint uses it.

### Shopping List

JSON file persistence at `/data/shopping_list.json` via a Docker volume mount. No database — a JSON file is sufficient for a personal shopping list and survives container restarts. CRUD via natural language: add, remove, view, clear.

Includes a mobile-friendly web page at `/shopping_list` (served by the orchestrator) with a dark theme and 30-second auto-refresh, so a phone can be used as a read-only view at the store without needing to talk to Kronk.

### File Upload

PDF and plain text files can be attached and injected as system messages on every request in the session. Token count is estimated and displayed per file; a warning is shown when total attached context exceeds ~2,000 tokens.

---

## Pipeline Reliability

### Hallucination guardrails

Early testing showed the model would fabricate data when tools failed. Asking about Madrid weather returned "as of my latest training data, the weather in Madrid is..." — confidently wrong. Three layers of guardrails were added, and the order matters:

1. **System prompt standing rule** — "Never fabricate real-time information. If no tool data is present, say so." This is the backstop for cases the pipeline doesn't anticipate.

2. **Directive failure messages** — When a tool fails, the pipeline injects a system message with explicit `MUST NOT answer from training data` language, not a soft suggestion. Models are better at following explicit prohibitions than inferring them from absence.

3. **Structural tool status lines** — Every tool result (success or failure) is prefixed with `[TOOL: weather — live NWS data]` or `[TOOL: weather — FAILED]`. The model always sees explicit state rather than having to infer it from context. This is the most reliable layer because it's structural, not instructional.

A single layer isn't enough. The system prompt rule is too easy to rationalize around. The failure message alone can be overridden by the model's helpful instinct. The status lines close the gap by making the tool state unambiguous in the prompt.

### Pipeline stages and timing

The timing model was initially a single slot — one `fetch_tool` variable and one timestamp. This was wrong: when the weather tool failed and search ran as a fallback, the weather attempt was overwritten and disappeared from the timing display.

Replaced with a `stages` list. Each tool attempt appends `{tool, duration_s, ok}` to the list when it completes. The timing event sends the full list. Benefits:
- Every attempt is recorded, including partial failures
- Failed stages are visually distinct (shown in red) in the UI
- New tools automatically appear in timing without any extra wiring

---

## UI

### Streaming and markdown rendering

Tokens are streamed and appended to the bubble as plain text during generation. When `[DONE]` is received, the completed text is run through a markdown renderer that converts fenced code blocks, inline code, and `[text](url)` links to HTML. Links open in a new tab with `rel="noopener noreferrer"`.

**Why render on completion, not per-token:** Running the markdown parser on a partial stream causes flickering — a half-written `[link](` gets rendered incorrectly mid-stream, then corrected. For typical response lengths, the snap to rendered markdown at the end of generation is imperceptible.

**No external library:** The renderer is ~15 lines of regex + string manipulation. It covers the patterns Kronk actually produces (code blocks, inline code, links). A full markdown library would handle more edge cases but adds an external CDN dependency for minimal practical gain.

### Stage indicators

Each tool call emits a stage event (`fetching_weather`, `fetching_search`, `fetching_url`, `fetching`) before the async work begins. The UI shows a spinner with a label so the user knows what the pipeline is doing during the fetch phase. The stage is cleared when the first token arrives.

---

## Context Window

Context size is set per llama.cpp server via `--ctx-size` in each systemd unit. Tradeoff:

1. Smaller KV cache → smaller loaded model size → more fits on GPU
2. Faster prefill on long conversations
3. Against: the agentic loop streams history + tool results + synthesis through the specialist agent in a single request, so running out of context mid-turn is a hard failure.

Current settings lean generous on context for the routing/coordinator pair (128k on the gemmas, where the KV cost is small) and match what the coding agent needs for devstral (128k). See each unit's `--ctx-size` flag.

---

## Model History

### Initial: llama3.3:70B
- Default context (131K tokens): loaded at **102 GB**, 38% CPU / 62% GPU split, ~2.2 t/s
- Reduced context (8K tokens): loaded at **45 GB**, 100% GPU, still ~2.2 t/s
- The speed didn't improve because the 70B parameter count is the bottleneck, not GPU utilization. This is the hardware ceiling for this model.

### Second: llama3.2:3B
- Loaded at **3.4 GB**, 100% GPU, ~10 t/s — 5x faster
- Drawback: childish responses (*winks*, *giggles*) despite system prompt prohibition
- Went off-topic on unrelated subjects (MagicMirror question turned into Starcraft tangent)

### Third: qwen3:14B (current as of 2026-04-01)
- Loaded at ~9 GB, 100% GPU
- Key issue: thinking mode — model internally reasons using `<think>...</think>` tokens before responding, producing 8-29s TTFT depending on prompt complexity
- Quality is good but responsiveness is poor for a home assistant
- Theatrical flag on reasoning prompt despite explicit system prompt prohibition

---

## Model Benchmark (2026-04-02)

11 models tested against 6 prompts covering factual Q&A, tool use (weather), instruction following, code generation, multi-step reasoning, and math.

Raw benchmark data and full responses: `model_results.md` (auto-generated, do not edit manually).

### Summary

| Model | Avg TTFT | Avg generation | Theatrical flags |
|---|---|---|---|
| `qwen3:14b` | 14.73s | 3.04s | 1 / 6 |
| `qwen2.5:14b` | 0.66s | 3.86s | 0 / 6 |
| `mistral:7b` | 0.32s | 2.47s | 0 / 6 |
| `mistral-nemo:12b` | 0.60s | 0.81s | 0 / 6 |
| `mistral-small:22b` | 0.72s | 3.71s | 0 / 6 |
| `mistral-small:24b` | 0.80s | 7.01s | 1 / 6 |
| `mistral-small3.1:24b` | 1.43s | 5.46s | 1 / 6 |
| `mistral-small3.2:24b` | 1.10s | 3.11s | 0 / 6 |
| `llama3.1:8b` | 0.36s | 1.32s | 0 / 6 |
| `gemma3:12b` | 0.59s | 0.77s | 0 / 6 |
| `phi4:14b` | 0.49s | 4.08s | 1 / 6 |

### Per-prompt timing (TTFT / generation, seconds)

| Model | factual | weather | theatrical | code | reasoning | math |
|---|---|---|---|---|---|---|
| `qwen3:14b` | 8.96 / 2.93 | 6.05 / 2.07 | 7.32 / 0.67 | 22.65 / 1.00 | 28.71 / 10.96 | 14.71 / 0.58 |
| `qwen2.5:14b` | 3.01 / 0.48 | 0.24 / 1.62 | 0.13 / 0.57 | 0.22 / 7.69 | 0.22 / 12.67 | 0.14 / 0.13 |
| `mistral:7b` | 1.46 / 1.51 | 0.15 / 1.00 | 0.07 / 0.64 | 0.06 / 3.92 | 0.11 / 7.38 | 0.06 / 0.36 |
| `mistral-nemo:12b` | 2.62 / 0.38 | 0.35 / 0.75 | 0.13 / 0.41 | 0.14 / 0.75 | 0.20 / 2.50 | 0.14 / 0.10 |
| `mistral-small:22b` | 2.68 / 1.05 | 0.78 / 2.66 | 0.10 / 0.79 | 0.33 / 1.87 | 0.30 / 15.68 | 0.15 / 0.24 |
| `mistral-small:24b` | 3.38 / 3.80 | 0.34 / 2.61 | 0.22 / 0.83 | 0.21 / 12.65 | 0.40 / 21.98 | 0.25 / 0.21 |
| `mistral-small3.1:24b` | 6.99 / 4.00 | 0.34 / 2.77 | 0.26 / 0.90 | 0.25 / 5.56 | 0.44 / 18.36 | 0.30 / 1.16 |
| `mistral-small3.2:24b` | 5.13 / 1.35 | 0.33 / 2.54 | 0.21 / 0.87 | 0.22 / 4.61 | 0.43 / 9.09 | 0.29 / 0.20 |
| `llama3.1:8b` | 1.45 / 0.84 | 0.19 / 0.82 | 0.11 / 0.38 | 0.17 / 4.02 | 0.16 / 1.55 | 0.11 / 0.34 |
| `gemma3:12b` | 2.28 / 0.20 | 0.29 / 0.90 | 0.20 / 0.62 | 0.27 / 1.80 | 0.27 / 0.94 | 0.23 / 0.16 |
| `phi4:14b` | 1.95 / 0.39 | 0.27 / 1.23 | 0.19 / 1.13 | 0.18 / 6.37 | 0.22 / 15.02 | 0.14 / 0.34 |

---

## llama.cpp Benchmark (2026-04-10)

Benchmark comparing devstral variants for the coding/devops agent and coordinator candidates. Run against the llama.cpp server binary built for gfx1151. Script: `pai_workspace/output/benchmark_models.py`. Full results: `pai_workspace/output/bench_results.json`.

All models: Q8_0 quantization, 4096 token context, `--flash-attn on`, `-ngl 99`. TTFT excludes the first cold-load prompt (model loading from disk into VRAM).

### Devstral variants — 7 coding/devops prompts

| Model | Warm TTFT (avg) | tok/s | Notes |
|---|---|---|---|
| devstral-2505 | ~325ms | 8.7–8.8 | Baseline |
| devstral-2507 | ~327ms | 8.6 | Marginal regression vs 2505 |
| **devstral-2512** | **~383ms** | **8.7** | Latest (Dec 2025), best quality |

All three are statistically tied on performance. Winner selected on model recency/quality: **devstral-2512**.

### Coordinator candidates — 5 routing/reasoning prompts

| Model | Avg TTFT | tok/s | Notes |
|---|---|---|---|
| **pixtral-12b** | **133ms** | **17.5** | 12B; dominant on all prompts |
| mistral-small-3.2 | 1241ms | 8.7 | 24B; 4.5s TTFT on longer prompts |

pixtral-12b is 9× faster TTFT and 2× throughput. Response quality was indistinguishable across all 5 prompts (identical instruction-following, correct JSON routing outputs). Winner: **pixtral-12b**.

Note: pixtral is Mistral's multimodal model — vision capability is additive on top of a strong 12B instruction-following base. Text-only performance is unaffected.

---

## Model Recommendation

### Best fit: `qwen2.5:14b`

- **No thinking overhead.** Unlike qwen3:14b (avg TTFT 14s+), qwen2.5:14b responds immediately. The difference is stark on the code and reasoning prompts where qwen3 spent 22-29s in its thinking phase before generating a single token.
- **Zero theatrical flags.** Respected the system prompt across all prompts. qwen3:14b, phi4:14b, mistral-small:24b, and mistral-small3.1:24b all produced `*emote*` patterns on the reasoning prompt despite explicit prohibition.
- **Strong tool use.** Weather prompt completed with 0.24s TTFT — essentially no latency introduced by the model after the tool result was injected.
- **Quality ceiling at 14B.** On the reasoning prompt, qwen2.5:14b produced a detailed, well-structured response. Faster models (llama3.1:8b at 1.55s gen, mistral-nemo:12b at 2.50s) were noticeably shallower.

To switch: change `MODEL_NAME=qwen2.5:14b` in `docker-compose.yml`.

### Strong alternative: `mistral-nemo:12b`

The standout of the Mistral testing. Sub-0.15s TTFT on most prompts, zero theatrical flags, fast generation. Tradeoff: at 12B, reasoning depth is lower than qwen2.5:14b on complex multi-step prompts. Worth a real-world trial if responsiveness matters more than answer depth.

### Mistral family summary

| Model | Verdict |
|---|---|
| `mistral-small3.2:24b` | Best Mistral overall. No theatrical flags, faster reasoning gen than 3.1. High factual TTFT (5s) is a cold-cache artifact. |
| `mistral-nemo:12b` | Best Mistral for speed. Fastest TTFT in the field, zero flags, good for assistant workloads. |
| `mistral-small:22b` | Solid but outclassed by 3.2 revision and mistral-nemo. |
| `mistral-small:24b` / `3.1:24b` | Theatrical flags, slower or no clear quality advantage. Not recommended. |
| `mistral:7b` | Reliable fallback, shows its age vs newer 7-8B options. |

### Runner-up (speed-first): `llama3.1:8b`

If 14B feels slow in daily use, llama3.1:8b is the best smaller option. Meta built tool use directly into this model's training, it's consistently fast, and produced zero theatrical flags. Ceiling is lower but it punches above its weight.

### Avoid: `qwen3:14b` without `/no_think`

14s+ average TTFT is unacceptable for a home assistant. If you want to keep it, append `/no_think` to the system prompt — but at that point qwen2.5:14b is a better choice.

---

## Health Service

### Data import — manual via chat UI

Health data is imported manually by uploading a Garmin Connect data export through the Kronk chat UI. The orchestrator detects the file type and routes it to the health service for import into SQLite at `/data/health.db`.

**Two supported formats:**
- **Bulk zip export** — from Garmin Connect → Account → Data Management → Export Data. Contains multiple CSV types in a single zip. The health service unpacks it and routes each CSV to the appropriate import handler. Best for initial load or backfilling a long history.
- **Per-metric CSV** — individual exports from specific Garmin dashboards (sleep, HRV, activities, etc.). Useful for incremental weekly updates.

**Tables:** `daily_summary`, `sleep`, `hrv`, `body_battery`, `activities`.

**Import cadence:** Weekly or whenever the data feels stale. Not automated — the manual step is intentional (see below).

**Kronk tool:** `query_health` with parameters `metric`, `days`, and `end_date`. Covers all metrics back to 2006 (data permitting). No upper bound on `days` — returns oldest available records if the window exceeds what's in the DB.

### Dashboard

A Chart.js dashboard at `/health` (proxied via nginx) shows trends over selectable periods (4 weeks / 12 weeks / 1 year):
- Comparison cards: sleep duration, sleep score, HRV, resting HR — current period vs prior period with delta arrows
- Sleep chart: stacked bar (deep / REM / light) + score line
- Recovery chart: HRV line with baseline band + resting HR on secondary axis
- Activity list + daily steps bar chart + active calories histogram

### Why manual import instead of automatic sync

Automatic Garmin Connect sync was the original plan. It was abandoned after hitting a series of compounding problems:

**The auth library broke.** The `garminconnect` Python library underwent a major breaking change — the old garth/OAuth/cookie login no longer works. It now authenticates using the same mobile SSO flow as the Garmin Connect Android app, obtaining DI OAuth Bearer tokens. The token format changed from garth's session string to `garmin_tokens.json`. Upgrading from any pinned version below `0.2.25` is required.

**`curl_cffi` is essential and fragile.** The library's login flow uses `curl_cffi` to impersonate a Chrome TLS fingerprint to get past Cloudflare. Without it, `requests` falls back to plain TLS which Cloudflare fingerprints and 429s. This is a runtime dependency that can break silently when `curl_cffi` version or the Cloudflare challenge changes.

**Cloudflare 429s on initial auth.** The SSO login flow makes ~8-10 HTTP requests in rapid succession. On a fresh IP or after failed attempts, Cloudflare rate-limits the IP for ~90 minutes. Even with exponential backoff, this makes initial setup painful.

**MFA blocks unattended auth entirely.** With MFA enabled on the Garmin account, first-time authentication requires an interactive session. Background jobs can't complete it. Ongoing syncs would work after the token is saved, but the initial hurdle is manual no matter what.

**The data doesn't change fast enough to justify the complexity.** Garmin syncs to Connect on a schedule that doesn't guarantee fresh data at any given moment anyway. A weekly manual export captures the same data with zero maintenance surface — no token refresh logic, no Cloudflare cat-and-mouse, no broken syncs to debug.

**The token file approach is worth keeping for future reference.** If automatic sync is ever revisited: tokens live at `/data/garmin_tokens.json` on a host volume mount, `login(tokenstore=path)` handles load/auth/save in one call, and auth should be done in a one-shot container to keep the host clean:
```bash
docker compose -f docker-compose.setup-auth.yml run --rm garmin_setup
```

---

## Secrets and Dependency Management

### Secrets: none currently required

Kronk has no runtime secrets at present. The previous self-hosted Infisical instance (postgres + redis backed, port 8200) was removed after the agentic-loop refactor eliminated every external API that required credentials from the critical path. NWS, Open-Meteo, SearXNG, and the local LLMs are all unauthenticated. Health imports are manual, and the finance service operates over uploaded documents, not live APIs.

If a future integration needs a credential (e.g. Garmin live sync, Withings, Fitbit, Philips Hue), the replacement plan is: put the token on a host volume at `/data/<service>_tokens.json` and bind-mount it into the one service that needs it. If that grows past a handful of files, reintroduce a secrets manager at that point — not before.

### Dependencies: hash pinning

Every dependency is pinned to an exact SHA-256 content hash. If a package is tampered with or swapped, the hash won't match and the build fails.

- `requirements.txt` — direct dependencies, human-maintained
- `requirements.lock` — machine-generated, every transitive dependency with hashes
- Docker builds install with `uv pip install --require-hashes -r requirements.lock`

**Generating / updating lockfiles:**
```bash
# From the service directory, using uv on the host
cd orchestrator && uv pip compile requirements.txt --generate-hashes -o requirements.lock
```

Repeat for each service directory. When updating a direct dependency, regenerate the lockfile and commit both files. Never edit the lockfile by hand.

---

---

## Lessons Learned

### Model selection
- **`mistral:7b` does not support tool calling** — returns prose instead of `tool_calls`. Any agent that needs to dispatch tools requires at least `mistral-nemo:12b`.
- **`mistral-nemo:12b` sometimes emits transitional content without `tool_calls`** — e.g. "Understood. Fetching your HRV data..." as preamble before a tool call. This should be shown to the user as real-time feedback, not treated as the final answer. The pipeline must always proceed to the coordinator phase regardless of whether the router produced content.
- Standardizing on a single model family (all mistral) simplifies reasoning about behavior across agents and eliminates cross-family quirks.

### Ollama model pinning
- `keep_alive: -1` in the warmup/preload request is not enough. Ollama applies its default 5-minute expiry on subsequent `/api/chat` calls unless `keep_alive: -1` is also set in every request. Set it on every `/chat` and `/complete` call in `llm_service/main.py`.

### Docker Compose deployment
- **`docker compose restart <service>` does not pick up a rebuilt image.** It reuses the existing container. Use `docker compose up -d <service>` to recreate the container from the new image.
- **`nginx -s reload` inside the container does not pick up a changed volume-mounted config.** Use `docker compose restart nginx` from the host.

### Pipeline design
- A Phase 3 shortcut that skips the coordinator when the router already has content (and no tool_calls) misfires — the router sometimes produces transitional preamble without tool_calls. Remove the shortcut; always run the coordinator phase.
- Router transitional content ("Understood, fetching...") is valuable real-time feedback. Show it in the UI as styled thinking text, not suppressed and not treated as the final answer.

### UI / SSE streaming
- `white-space: pre-wrap` on the message bubble conflicts with markdown rendering — it re-adds literal newlines after `innerHTML` sets formatted HTML. Apply a `.rendered` class that switches to `white-space: normal` only after streaming completes.
- A vanishing spinner gives no information about what already completed. A persistent stage log (`✓ thinking · 0.4s` / `✓ health data · 1.2s` / `⟳ generating...`) is more informative and preferred.
- Users prefer explicit real-time feedback about what the pipeline is doing over a clean but opaque interface.

### Health data
- Garmin exports come in two formats: per-metric CSV and a bulk zip with multiple CSV types. The zip must be routed to separate import handlers per CSV type.
- Arbitrary API caps (e.g. `le=90`) cause silent 422 errors swallowed by JS catch blocks, making period selectors appear broken. Remove caps and let the DB query return what it has.
- Health tools should expose flexible parameterized queries (`metric + days + end_date`) rather than fixed snapshots — the useful questions are always trend-based.

---

## What's Still on the Table

- **Garmin MFA / initial auth** — MFA blocks unattended first-time authentication. Requires an interactive run of `setup_auth.py` to get the initial token; after that, syncs are silent. The Cloudflare 429 rate limit on initial auth is separate — requires waiting ~90 minutes between attempts.
- ~~**Agentic loop (Option B)**~~ — Done. LLM-driven tool dispatch via router + coordinator pipeline with full tool-calling support.
- ~~**Health data in Kronk**~~ — Done. `query_health` tool with `metric / days / end_date` parameters; covers all Garmin metrics back to 2006.
- ~~**Query routing**~~ — Done. `ROUTER_MODEL` for dispatch, `COORDINATOR_MODEL` for generation, per-agent model env vars for future specialization.
- ~~**Migrate from Ollama to llama.cpp**~~ — Done. Five llama.cpp server instances as user-systemd units behind a LiteLLM proxy. Ollama retired pending storage cleanup.
- ~~**Agentic tool-calling loop**~~ — Done. Specialist agents run their own tool-call loops via LiteLLM's OpenAI-compatible function calling; old regex intent detection removed.
- ~~**Retire Infisical**~~ — Done. No runtime secrets currently required.
- **Reclaim Ollama blob storage** — `/usr/share/ollama/.ollama/models/blobs/` (~50+ GB). Safe to delete once llama.cpp is confirmed stable.
- **Voice pipeline** — STT (Whisper.cpp), TTS (Piper), wake word (openWakeWord); stubbed in the current UI.
- **More tools** — Philips Hue, calendar, home automation.
- **Additional health sources** — Fitbit (for a family member), Withings scale. Withings sync scaffolding is in `health_service/withings_sync.py`.
- **External shopping list** — publish the shopping list page externally (GitHub Pages / Cloudflare Pages) so it's accessible without being on the home network.
