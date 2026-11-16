# Kronk — History & Benchmarks (Archive)

Historical material moved out of the main README. Everything here predates the
current llama.cpp + LiteLLM setup and is kept for reference only — **none of it
reflects the running system.** For current state see [`../README.md`](../README.md).

---

## GPU Backend: ROCm vs Vulkan (resolved)

**What the setup guide expected (March 2026):** ROCm had incomplete support for
GFX1150/1151; the guide recommended Vulkan via a manually-built llama.cpp as the
primary inference path, claiming Vulkan could access ~88 GiB via GTT vs ROCm's
more limited pool.

**What actually happened:** Ollama 0.19.0 had ROCm support for GFX1151 out of the
box — first install detected the GPU and ran inference at 100% GPU via ROCm with
no manual configuration. Enabling Vulkan anyway (`OLLAMA_VULKAN=1`) did nothing:
Ollama still chose ROCm and GPU memory stayed at 61.9 GiB.

**Decision:** Use ROCm. It works out of the box. This conclusion carried forward
to the from-source llama.cpp build that replaced Ollama.

---

## Model History (Ollama era)

### Initial: llama3.3:70B
- Default context (131K tokens): loaded at **102 GB**, 38% CPU / 62% GPU split, ~2.2 t/s
- Reduced context (8K tokens): loaded at **45 GB**, 100% GPU, still ~2.2 t/s
- Speed didn't improve because the 70B parameter count is the bottleneck, not GPU
  utilization. Hardware ceiling for this model.

### Second: llama3.2:3B
- Loaded at **3.4 GB**, 100% GPU, ~10 t/s — 5x faster
- Drawback: childish responses (*winks*, *giggles*) despite system prompt prohibition
- Went off-topic on unrelated subjects (a MagicMirror question turned into a Starcraft tangent)

### Third: qwen3:14B
- Loaded at ~9 GB, 100% GPU
- Key issue: thinking mode — internal `<think>...</think>` reasoning before responding,
  producing 8-29s TTFT depending on prompt complexity
- Quality good but responsiveness poor for a home assistant
- Theatrical flag on the reasoning prompt despite explicit system prompt prohibition

---

## Model Benchmark (2026-04-02)

11 models tested against 6 prompts covering factual Q&A, tool use (weather),
instruction following, code generation, multi-step reasoning, and math. Raw data
and full responses were in `model_results.md` (auto-generated).

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

### Model Recommendation (as of this benchmark)

**Best fit at the time: `qwen2.5:14b`** — no thinking overhead (unlike qwen3:14b's
14s+ TTFT), zero theatrical flags, strong tool use (0.24s TTFT on the weather
prompt after tool injection), good reasoning depth for a 14B.

**Strong alternative: `mistral-nemo:12b`** — sub-0.15s TTFT on most prompts, zero
theatrical flags, fast generation; shallower reasoning than qwen2.5:14b.

| Model | Verdict (2026-04-02) |
|---|---|
| `mistral-small3.2:24b` | Best Mistral overall. No theatrical flags, faster reasoning gen than 3.1. |
| `mistral-nemo:12b` | Best Mistral for speed. Fastest TTFT in the field, zero flags. |
| `mistral-small:22b` | Solid but outclassed by the 3.2 revision and mistral-nemo. |
| `mistral-small:24b` / `3.1:24b` | Theatrical flags, no clear quality advantage. Not recommended. |
| `mistral:7b` | Reliable fallback, shows its age. **Does not support tool calling.** |
| `llama3.1:8b` | Best speed-first smaller option. Tool use built into training, zero flags. |
| `qwen3:14b` | Avoid without `/no_think` — 14s+ average TTFT is unacceptable for a home assistant. |

---

## llama.cpp Benchmark (2026-04-10)

Comparing devstral variants for the coding/devops agent and coordinator
candidates, run against the from-source llama.cpp server built for gfx1151.
Script: `pai_workspace/output/benchmark_models.py`. Full results:
`pai_workspace/output/bench_results.json`. All models Q8_0, 4096-token context,
`--flash-attn on`, `-ngl 99`. TTFT excludes the cold-load prompt.

### Devstral variants — 7 coding/devops prompts

| Model | Warm TTFT (avg) | tok/s | Notes |
|---|---|---|---|
| devstral-2505 | ~325ms | 8.7–8.8 | Baseline |
| devstral-2507 | ~327ms | 8.6 | Marginal regression vs 2505 |
| **devstral-2512** | **~383ms** | **8.7** | Latest (Dec 2025), best quality |

All three statistically tied on performance. Winner selected on recency/quality:
**devstral-2512**.

### Coordinator candidates — 5 routing/reasoning prompts

| Model | Avg TTFT | tok/s | Notes |
|---|---|---|---|
| **pixtral-12b** | **133ms** | **17.5** | 12B; dominant on all prompts |
| mistral-small-3.2 | 1241ms | 8.7 | 24B; 4.5s TTFT on longer prompts |

pixtral-12b was 9× faster TTFT and 2× throughput with indistinguishable response
quality. (The current setup later moved to the smaller gemma-3-4b / gemma-4-e4b
pair for the router/coordinator roles — see the main README.)

---

## Health, finance and retirement features (removed 2026-11-15)

Removed in sprint item M1 (`docs/sprint/M1-build.md`): the operator did not
use them, and any voice at any satellite, guests included, could reach the
health and finance agents. The last code is at git tag `sprint-M17`.

What existed:

- `health` agent (`query_health`) over `health_service` (port 8004): Garmin
  and Withings data in SQLite (`/data/health.db`) plus a chroma vector store
  (`/data/chroma/`), a `/health` dashboard, and chat-UI uploads of Garmin CSV
  and zip exports and LabCorp PDFs forwarded for import. `query_bloodwork` and
  `search_health_data` were defined but never wired to an agent.
- `finance` agent (`query_finances`) over `finance_service` (port 8005):
  uploaded statements and the positions store (`/data/finance/`), and a
  `/finances` page. Plan: `docs/history/FINANCIAL_EXPERT_PLAN.md`.
- `retire_calc`, built from the sibling repo `../retirement-calc`, served at
  `/retire/` (`/data/retire/`). The sibling repo itself was not touched.

On the operator's instruction (2026-11-14) the stored data is deleted, not
archived, as the last step of promoting M1 to production; the deletion
manifest is in the build report.

Design decision kept for the record. **Health data: manual import, not
automatic sync.** Garmin Connect auto-sync was abandoned after compounding
problems: the `garminconnect` library's breaking auth rewrite, fragile
`curl_cffi` Cloudflare fingerprinting, Cloudflare 429s on initial auth
(about a 90-minute lockout), and MFA blocking unattended first auth. A weekly
manual export (CSV or bulk zip, uploaded through the chat UI) captured the same
data with no maintenance surface.

---

## Hot tub monitor (removed 2026-11-15)

Removed in sprint item M1b (`docs/sprint/M1b-build.md`) on the operator's
decision of 2026-11-14 that the feature never worked. The last code is at git
tag `sprint-M1`.

What existed:

- `hottub_monitor/monitor.py`, run by the host user unit
  `kronk-hottub-monitor.service` from its own virtualenv
  (`hottub_monitor/.venv`, `geckolib` 1.0.15). It connected to the Gecko
  in.touch 2 module on the LAN, read the water and set temperatures every
  5 minutes, and wrote `data/hottub/status.json`. Alerting was never built.
  The unit had no reference copy in `systemd/`.
- `GET /hottub` in `tool_service`, which returned that file.
- The `query_hottub` tool on the `home` agent.
- `composite_hottub`, a hard composite in `scripts/coordinator_model_bench.py`
  (heater kW from `ask_home` times the rate from `ask_research`).

Track record, from the unit's journal (retained from 2026-08-23): until
2026-10-24 the module did not answer (a connect attempt every 5 minutes,
about 10,400 failures logged), which is what TECH_DEBT HOTTUB-01 recorded.
From 2026-10-24 13:50 it connected and reported temperatures, with no
offline event after that (20 non-fatal geckolib errors: "Cannot get
reminders" timeouts, and "Network is unreachable" during the 2026-11-04
boot), until the unit was stopped for removal on 2026-11-15. A tarball of the directory,
virtualenv included, is at `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz`
on the host.
