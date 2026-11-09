# Investigation 2026-11-08 — /resources: right memory, wrong everything around it

**Status:** audit done (read-only); fix awaiting the operator's choice.

## Symptom

Operator: the `/resources` page "has been wrong for a long time".

## Findings (measured 2026-11-08 16:37 EST)

Correct: per-model measured GPU memory (`measured_gb`, host fdinfo exporter
`scripts/gpu_mem_export.py`, correct since edd7fc6 on 2026-07-06; an
independent fdinfo scan matched it byte for byte), health dots, system RAM
and GTT totals.

Wrong:

1. Dead catalog entries render as full model cards (bonsai-8b, mistral-nemo,
   devstral-2512 Q8, talkie): `servers.load_catalog()` lists every
   `litellm/config.yaml` `model_list` entry; the catalog was last edited
   2026-05-14.
2. Context sizes from hand-written `kronk.ctx_k`: gemma-4-e4b shows 128k,
   runs 32k (`/props` n_ctx 32768); gemma-3-4b shows 128k, runs 8k. Wrong
   since the systemd units landed (c70e4ee, 2026-05-24).
3. Quant: gemma-4-e4b shows Q4_K_M; it runs
   `gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf` plus an MTP Q8 drafter.
4. "Router" chip on gemma-3-4b: `servers.py:119-121` labels the
   `ROUTER_MODEL` env model; the router was removed 2026-08-18 (flagged as
   D5 in `REVIEW_2026-10-09_consolidation.md`, not fixed).
5. Whisper's ~2.07 GB GTT is unattributed: the exporter records it with
   `port: null`; `servers.py:38` drops port-less entries and `/api/servers`
   joins by port only.
6. Perf row frozen: both `metrics.record` calls (`agents.py`) pass
   `eval_duration_ns=0` and no `ttft_ms`, so `gen_ms` is NULL and every
   dashboard query filters it out. Last usable row 2026-06-12 17:34:27; 6,133
   rows total, latest 2026-11-08. The shown gemma-4-e4b numbers predate the
   July weight refresh.
7. gemma-4-e4b's ~4.45 GB host prompt cache lands in "System / other"; its
   real footprint is ~9.9 GB, not 5.5 GB.
8. Minor: "Models (est.)" label although values are measured; decimal GB
   throughout while the box is discussed in GiB; `measured_gb` adds VRAM
   carve-out (outside MemTotal) to a MemTotal-based bar (~0.36 GB);
   stale "ROCm/HIP" comments — the llama servers are Vulkan builds since
   23d57ea (2026-06-12).

`/api/servers` also feeds `/services` (`services.html` reads name, port,
params, quant, ctx_k, measured_gb, agents, healthy), so items 1–4 show there
too.

## Options

- A — hygiene pass on the hand-written metadata (1–2 h); drifts again.
- B — exporter as source of truth: it already walks the units; add `/props`
  (model file, n_ctx, slots), file size, per-process RssAnon; `/api/servers`
  takes live fields from it and the catalog only for name/port/agents; page
  splits "Loaded" from "Configured, not running"; whisper and host prompt
  cache appear (~half a day + tests).
- C — perf row: delete it now; real LLM timings come with Telemetry v2
  (`TELEMETRY_V2_PLAN.md`).
- D — fold /resources into /services (keep the memory bar, the 2026-05-31
  early warning).

Recommended: B + delete the perf row (C). Pruning dead catalog entries (the
talkie agent still points at one) is a separate operator decision.

## What would have caught it sooner

Facts copied by hand into `litellm/config.yaml` `kronk:` blocks instead of
read from the running servers (tenet 8), and a recording call whose zeroed
arguments silently produced filtered-out rows for five months — a test that
the dashboard query returns the latest recorded call would have failed on
2026-06-12.
