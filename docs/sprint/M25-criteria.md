# M25 pass criteria: /resources shows real usage

Source: Drew's order of 2026-11-24 ("the /resources page on kronk I don't
think ever got attention. It should represent real usage information.
Create that page as a test of our new workflow"), his choice of scope the
same day ("All measured, plus activity"), and
`docs/incidents/INVESTIGATION_2026-11-08_resources_page.md` (findings 1 to
8, option B).

Handoff line: /resources shows only measured facts about each model
server, and what each one did in the last 24 hours.

## Drew's condition (2026-11-24)

Scope chosen: "No hand-written number: context, quantization and model
file read from each running server; GPU busy, memory, disk used by models;
per server, requests and tokens in the last 24 hours and when it was last
used. History over time stays in Grafana."

## What the investigation found (coordinator, 2026-11-24)

- The premise is half right. The page was last worked on 2026-07-06
  (`docs/features/resources-live-memory.md`): GPU memory per server is
  measured every 30 s by `scripts/gpu_mem_export.py` and is correct; so
  are the health dots and the system RAM and GTT totals.
- Hand-written and wrong, from the `kronk:` blocks of
  `litellm/config.yaml`: context size (the page says 128k for
  gemma-4-e4b, which runs 32,768), quantization (says Q4_K_M; the file is
  `gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf` with an MTP drafter),
  parameter count, and `vram_gb`. The 2026-11-08 investigation lists the
  rest; M24 (promoted 2026-11-24, `item-M24`) removed the dead entries
  and the "Router" label. The catalog now holds three servers, all
  running: gemma-4-e4b, devstral-2512-q4, and Talkie on the CPU.
- Not shown at all: any activity. The page fetches `/api/metrics`, whose
  timing columns have been empty since 2026-06-12 because both
  `metrics.record` calls in `orchestrator/agents.py` pass
  `eval_duration_ns=0` and no `ttft_ms`.
- A usable activity source exists: `data/metrics.db`, table `llm_metrics`,
  one row per model call with model, agent, prompt and completion tokens
  and a UTC timestamp. 6,372 rows, the latest today; in the last 24 hours
  gemma-4-e4b: 24 calls, 76,485 prompt tokens, 4,507 completion tokens.
- A source for the real server facts exists and is not installed:
  `scripts/llama_props_export.py` (M21 step 2) writes each running
  server's flags and its own `/props` answer (n_ctx, slots, model path) to
  `data/llama_props.json`; `orchestrator/servers.py` already has
  `load_llama_props()`. The timer `llama-props-export.timer` was never
  enabled.
- `data/gpu_mem.json` already carries what the page drops: the two
  Whisper processes (about 2 GB of GPU memory each, `port: null`) and each
  process's RAM (`rss_bytes`: Talkie 16 GB with 0 GPU memory; gemma-4-e4b
  11 GB, most of it the host prompt cache).
- `/services` reads the same `/api/servers` fields (name, port, params,
  quant, ctx_k, measured_gb, agents, healthy).

## Plumbing, decided by the coordinator

- **Server facts come from the two host exporters**, joined by port:
  `gpu_mem.json` (memory) and `llama_props.json` (context, slots, model
  file, draft model, flags). Whatever else a fact needs (the model file's
  size on disk, the filesystem's free space, the GPU busy reading) is
  added to an exporter, not typed into a file.
- **The catalog keeps names and addresses only.** The `kronk:` blocks
  (`params`, `quant`, `vram_gb`, `ctx_k`) leave `litellm/config.yaml`.
  Nothing replaces `vram_gb` as a fallback: a server with no fresh
  measurement shows "not measured", never an estimate.
- **Quantization is not a separate claim.** The page shows the model
  file's name as the server reports it; the quantization is in the name.
- **Activity comes from `llm_metrics`** (requests, prompt tokens,
  completion tokens, last used), over the last 24 hours, per model. No new
  store. Times are shown in the host's local time.
- **One unit.** Sizes are GiB everywhere on the page and labelled GiB (the
  2026-11-08 finding 8).
- **The frozen timing row goes** from `/resources`. `/performance` and the
  zeroed `metrics.record` arguments are out of scope.
- **The coordinator installed `llama-props-export.timer` before the
  build** (2026-11-24, about 14:10 local; a read-only exporter, built and
  tested in M21 step 2; remove with `systemctl --user disable --now
  llama-props-export.timer`). `data/llama_props.json` and
  `data/dev/llama_props.json` are written every 60 s. Agents install
  nothing. If the build changes an exporter script, the timers run the
  working tree's copy at their next firing: the script must stay read-only
  and must never fail in a way that leaves a half-written file.

## Scope

1. `/api/servers` reports, per catalog server: name, port, agents, health,
   and, when the server is running, measured GPU memory, process RAM,
   context size, slots, model file name, draft model file name if any,
   model file size on disk, and the age of each measurement. A fact with
   no fresh measurement is null with a reason, never a default.
2. `/api/servers` also reports the GPU processes that are not model
   servers (the Whisper units), with their GPU memory and RAM.
3. A host-level block: RAM total and available, GTT total and used, GPU
   busy now, and the model filesystem's size and free space.
4. Activity per model over the last 24 hours: requests, prompt tokens,
   completion tokens, last used. A model with no calls says so.
5. `/resources` shows all of it, says for each number where it comes from
   and how old it is, and marks stale data as stale. A catalog server that
   is not running is listed apart as "configured, not running" with no
   numbers.
6. `/services` keeps working on the changed payload.
7. `docs/features/resources-live-memory.md` rewritten for what the page
   now is; the 2026-11-08 investigation gets a dated closing line;
   `README.md`'s Models section (the catalog no longer carries metadata;
   the VRAM column).

Out of scope: charts over time; `/performance`; timing figures (tokens a
second, time to first token); any change to the model servers, their
units or LiteLLM's routing; Tempo queries.

## Criteria

1. `grep -n "vram_gb\|ctx_k\|quant\|params:" litellm/config.yaml` finds no
   `kronk:` metadata, and LiteLLM's own `litellm_params` are byte for byte
   as before. A test fails if a `kronk:` block returns.
2. For each running server, `/dev/api/servers` equals the server's own
   answer: context size equals `GET /props` n_ctx read at the same time on
   the host; model file equals the `-m` argument of the running process;
   file size equals `stat`. Shown for all three servers.
3. Measured GPU memory per server equals an independent fdinfo read within
   2 %, as the 2026-07-06 work showed; the Whisper processes appear with
   theirs; the sum of every listed process is within 5 % of GTT used.
4. Activity: for each model, requests and tokens over 24 hours equal a
   direct SQL query on a copy of `metrics.db` taken at the same moment.
   Tests cover: no rows, rows only older than 24 hours, a row exactly on
   the boundary, a model in the table that is no longer in the catalog
   (shown apart or left out, stated which), null token columns.
5. Staleness: with `gpu_mem.json` older than 120 s or `llama_props.json`
   older than 300 s (shown in tests with fixture files, and once live on
   dev by pointing the dev orchestrator at a stale copy), the payload
   nulls the affected facts with the reason and the page says "stale" with
   the age. No number from a stale file is shown as current.
6. A catalog server whose unit is not running (shown in tests, and live on
   dev with a catalog fixture naming a port nothing listens on) appears
   under "configured, not running" with no memory, context or file
   figures.
7. No hand-written number: a test walks the `/api/servers` payload built
   from fixtures with every exporter file missing and asserts every
   numeric fact about a server is null. The page then shows no figure for
   any server.
8. The page: on `/dev/resources`, every figure in the rendered page
   matches the payload fetched at the same time (the tester shows how it
   rendered the page); sizes are GiB and say so; each group names its
   source and age; nothing reads "(est.)".
9. `/dev/services` renders its model server rows with no "undefined",
   "null" or "NaN" in the text, for running and not-running servers.
10. The `/api/servers` and `/api/system` answers on dev take under 300 ms
    at the median of 20 requests (the page polls them).
11. Docs changed as scope 7 says.
12. `./scripts/run_tests.sh` passes (before: `7397 passed, 3 skipped, 54 xfailed`).
13. Nothing is installed, started, stopped or deployed by the build or the
    test; no model request is sent to production; `data/` is read, not
    written, apart from what the installed timers write. Production
    containers unchanged; HA and MA untouched; no sound; no notification.

Reports: `docs/sprint/M25-review.md` (design review, before the build),
`docs/sprint/M25-build.md`, `docs/sprint/M25-e2e.md`.
