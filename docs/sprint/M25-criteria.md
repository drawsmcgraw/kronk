# M25 pass criteria: /resources shows real usage

Revised 2026-11-24 after the design review (`docs/sprint/M25-review.md`):
all 23 of its points are taken; where this file and the review disagree,
this file decides.

Source: Drew's order of 2026-11-24 ("the /resources page on kronk I don't
think ever got attention. It should represent real usage information.
Create that page as a test of our new workflow"), his choice of scope the
same day ("All measured, plus activity"), and
`docs/incidents/INVESTIGATION_2026-11-08_resources_page.md` (findings 1 to
8, option B).

Handoff line: /resources shows only measured facts about each model
server, and what this Kronk asked of each one in the last 24 hours.

## Drew's condition (2026-11-24)

Scope chosen: "No hand-written number: context, quantization and model
file read from each running server; GPU busy, memory, disk used by models;
per server, requests and tokens in the last 24 hours and when it was last
used. History over time stays in Grafana."

## What the investigation found (coordinator and reviewer, 2026-11-24)

- The premise is half right. The page was last worked on 2026-07-06
  (`docs/features/resources-live-memory.md`): GPU memory per server is
  measured every 30 s by `scripts/gpu_mem_export.py` and is correct; so
  are the system RAM and GTT totals.
- Hand-written and wrong, from the `kronk:` blocks of
  `litellm/config.yaml`: context size (the page says 128k for
  gemma-4-e4b, which runs 32,768), quantization (says Q4_K_M; the file is
  `gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf` with an MTP drafter),
  parameter count, and `vram_gb`. M24 (promoted 2026-11-24, `item-M24`)
  removed the dead entries and the "Router" label. The catalog now holds
  three servers, all running: gemma-4-e4b, devstral-2512-q4, and Talkie on
  the CPU. No code but `servers.py` and the two pages reads the `kronk:`
  values.
- Not shown at all: any activity. The page fetches `/api/metrics`, whose
  timing columns have been empty since 2026-06-12 because both
  `metrics.record` calls in `orchestrator/agents.py` pass
  `eval_duration_ns=0` and no `ttft_ms`.
- An activity source exists: table `llm_metrics` in each orchestrator's
  own `metrics.db` (production `data/metrics.db`, dev
  `data/dev/metrics.db`), one row per model call with model, agent, prompt
  and completion tokens and a timestamp: text, UTC, no zone mark, to the
  second. No token column is ever null (the default is 0). Production: 6,381
  rows; in the last 24 hours gemma-4-e4b 31 calls, 97,424 prompt and 5,294
  completion tokens, talkie 2 calls; devstral-2512-q4 last used
  2026-08-29. Dev recorded 164 calls in the same 24 hours. The table also
  names seven models no longer in the catalog, none inside 24 hours.
- `scripts/llama_props_export.py` (M21 step 2) writes each running
  server's flags and its own `/props` answer to `data/llama_props.json`
  and `data/dev/llama_props.json`. Its timer was not installed until
  today.
- `data/gpu_mem.json` already carries what the page drops: the two
  Whisper processes (about 2 GB of GPU memory each, `port: null`) and each
  process's RAM (`rss_bytes`: Talkie 24.5 GB with no GPU memory;
  gemma-4-e4b 11 GB, most of it the host prompt cache; devstral 9.1 GB).
  `scripts/gpu_mem_export.py` writes one path only: dev
  (`data/dev/gpu_mem.json`) has no memory file, and `/dev/api/servers`
  answers `measured_age_s: null`.
- **`/api/servers` sends model requests.** `fetch_health()` calls
  LiteLLM's `/health`, which runs a chat completion on every backend:
  one `POST /v1/chat/completions` per server per call (Talkie's took
  652 ms on the CPU and reset its cached prompt). Median 0.685 s over 20
  calls. The page polls every 30 s while a tab is open.
- **The pages read production under `/dev/`.** `resources.html` fetches
  `/api/system`, `/api/metrics`, `/api/servers` and `services.html`
  fetches `/api/servers` and `/api/agents`, all with a leading slash.
- **The exporters run from the working tree and production reads their
  files.** Both installed units run `scripts/*.py` from this checkout, and
  production's `/data` is the repo's `data/`. A change to an exporter is
  live in production within a minute.
- `/sys/class/drm/card1/device/gpu_busy_percent` is readable inside the
  orchestrator container. The model files are in `/opt/models/…` (18 GB)
  and `/home/drew/model-staging/…` (14 GB), both on `/`.
- `/services` reads `/api/servers` (name, port, params, quant, ctx_k,
  measured_gb, agents, healthy) and prints `${measured_gb} GB measured`.

## Plumbing, decided by the coordinator

- **Server facts come from the two host exporters**, joined by port:
  `gpu_mem.json` (GPU memory, RAM) and `llama_props.json` (context, slots,
  model file, draft model, flags). The memory exporter writes both
  `data/` and `data/dev/`, as the props exporter does.
- **Exporter changes are additive.** Existing keys keep their names and
  meaning: production (`item-M24`) reads the same files. The builder
  develops against a scratch copy of a script (`--print`, or `--out` to a
  scratch path) and moves it into place only when its tests pass. The
  scripts stay read-only on the host and write their files atomically.
- **The catalog keeps names and addresses only.** The `kronk:` blocks
  leave `litellm/config.yaml`. Nothing replaces `vram_gb` as a fallback.
- **Running has one definition.** A server is running when its port
  appears in a fresh `gpu_mem.json`. Absent from a fresh file: "configured,
  not running". File stale or missing: "state unknown", not "not running".
  A running server whose `/props` did not answer (absent from a fresh
  `llama_props.json`) shows its memory and "not answering" for context,
  slots and file.
- **Health comes from the props exporter**: the server answered
  `GET /props` at the file's time. `/api/servers` does not call LiteLLM
  and causes no completion on any model server. Health is "unknown" when
  the props file is stale. (`/services` has its own liveliness probe of
  LiteLLM, `/probe/llm`; it stays.)
- **A running `llama-*` unit that is not in the catalog** is listed with
  the other GPU processes, by unit name, with its memory and model file.
- **Quantization is not a separate claim.** The page shows the model
  file's base name; the quantization is in the name. The full path stays
  out of the payload.
- **Sizes: the payload carries bytes; each page formats GiB** and says
  GiB.
- **Context and slots are shown as the server reports them** (`n_ctx`,
  `total_slots`), not divided.
- **Disk.** Per server: the size of its model file and of its draft file.
  For the host: the total of the loaded model and draft files, and the
  size and free space of each filesystem that holds one, found by
  `statvfs` on the file's directory in the exporter. No directory name is
  typed anywhere.
- **GPU busy** is read in the container from sysfs at request time and
  labelled as an instant reading.
- **Routes.** The host block (RAM, GTT, GPU busy; the disk figures come
  with the exporter file) is in `/api/system`. Activity is its own route,
  `/api/activity`, so `/services` does not pay for it.
- **Activity** comes from this orchestrator's `llm_metrics`: per model,
  requests, prompt tokens and completion tokens where
  `timestamp >= now - 24 h` (the boundary row counts; `now` is a parameter
  so a test can pin it), and "last used" over the whole table. Times leave
  the server as ISO 8601 with `Z`; the browser shows its local time. A
  model with calls inside the window that is not in the catalog is shown
  apart as "not in the catalog"; one with none inside the window is left
  out. The block is labelled "calls from this Kronk" (dev and production
  each count their own).
- **The frozen timing row goes** from `/resources`. `/performance` and the
  zeroed `metrics.record` arguments are out of scope.
- **Both pages fetch with relative URLs**, so `/dev/resources` and
  `/dev/services` show dev's data.
- **Live checks that need a changed file or mount on dev** use a compose
  override file in the agent's scratch directory (`docker compose -f
  docker-compose.yml -f <scratch>/override.yml --profile dev up -d
  orchestrator-dev`) that mounts scratch copies read-only and sets the
  paths; then the standard dev `up -d --build` restores it, shown by
  `docker inspect`. A catalog fixture is the real catalog plus one entry.
  Only the dev orchestrator is ever recreated this way.
- **The coordinator installed `llama-props-export.timer` before the
  build** (2026-11-24, 14:04 local; remove with `systemctl --user disable
  --now llama-props-export.timer`). Agents install nothing. A changed
  `gpu-mem-export` or `llama-props-export` unit file, if one is needed, is
  left for the coordinator with its install step.

## Scope

1. `/api/servers` reports, per catalog server: name, port, agents, state
   (running, configured not running, unknown), health, and for a running
   server: GPU memory, RAM, context size, slots, model file name and size,
   draft model file name and size if any, and the age of each source.
   A fact with no fresh source is null with a reason, never a default.
2. `/api/servers` also reports the GPU processes that are not catalog
   servers (the Whisper units; any uncatalogued `llama-*` unit), with GPU
   memory and RAM.
3. `/api/system`: RAM total and available, GTT total and used, GPU busy
   now, the total size of loaded model files, and the size and free space
   of the filesystem(s) holding them.
4. `/api/activity`: per model over the last 24 hours, requests, prompt
   tokens, completion tokens, and last used.
5. `/resources` shows all of it, says for each group where it comes from
   and how old it is, and marks stale data as stale.
6. `/services` keeps working on the changed payload and still shows a
   running server's memory.
7. Docs: `docs/features/resources-live-memory.md` rewritten for what the
   page now is; the 2026-11-08 investigation gets a dated closing line;
   `README.md` lines 222 to 233 (the Models text and its VRAM column).

Out of scope: charts over time; `/performance`; timing figures (tokens a
second, time to first token); any change to the model servers, their
units or LiteLLM's routing; Tempo queries.

## Criteria

1. `grep -c 'kronk:' litellm/config.yaml` prints 0, and `git diff item-M24
   -- litellm/config.yaml` shows deleted lines only (the `kronk:` blocks
   and their blank padding). A test fails if a `kronk:` block returns.
2. For each of the three running servers, `/dev/api/servers` equals the
   server's own answer taken at the same time on the host: context size
   equals `GET /props` `n_ctx`; slots equal `total_slots`; model file name
   equals the base name of the running process's `-m` argument; model and
   draft file sizes equal `stat`.
3. GPU memory per server equals an independent fdinfo read within 2 %.
   The Whisper processes appear with theirs. The sum of `gtt_bytes` over
   every listed process is within 5 % of `mem_info_gtt_used`.
4. `/api/system`: GPU busy equals the sysfs file read at the same moment
   (shown over ten paired reads; an instant reading may differ between two
   reads, so show the pairs); the model total equals the sum of the `stat`
   sizes; filesystem free space equals `df` within 1 %.
5. Activity: for each model, requests and tokens over 24 hours and "last
   used" equal direct SQL on a copy of `data/dev/metrics.db` taken at the
   same moment. Tests cover: no rows; rows only older than 24 hours (not
   counted; "last used" still shown); a row exactly on the boundary
   (counted); a model with calls in the window that is not in the catalog
   (shown apart); a model out of the catalog with none in the window (left
   out); rows with 0 tokens. One "last used" on the rendered page is
   checked against its row: the page's local time equals the row's UTC
   time converted.
6. Staleness: with `gpu_mem.json` older than 120 s or `llama_props.json`
   older than 300 s, the payload nulls the affected facts with the reason,
   state is "unknown" (memory file) or health is "unknown" (props file),
   and the page says "stale" with the age. Shown in tests with fixture
   files, and once live on dev by the override method.
7. A catalog server whose port is absent from a fresh memory file appears
   under "configured, not running" with no memory, context or file
   figures. A running server absent from a fresh props file shows its
   memory and "not answering". Tests for both; the first also live on dev
   with a catalog fixture naming a port nothing listens on.
8. No hand-written number: with every exporter file missing, a test
   asserts that for every server GPU memory, RAM, context, slots, model
   file size, draft file size and both source ages are null, and state is
   "unknown". The page then shows no figure for any server.
9. `/api/servers` causes no completion on any model server and does not
   call LiteLLM: shown offline (a test in which any call to LiteLLM fails
   the test) and live (the three llama units' journals show no `POST
   /v1/chat/completions` during 20 requests to `/dev/api/servers`).
   `/dev/api/servers`, `/dev/api/system` and `/dev/api/activity` each
   answer in under 300 ms at the median of 20 requests.
10. The pages: `/dev/resources` and `/dev/services` fetch relative URLs
    (shown by the requests the rendered page makes: all under `/dev/`).
    On `/dev/resources` every figure in the rendered page matches the
    payloads fetched at the same time (the tester says how the page was
    rendered); sizes say GiB; each group names its source and age; nothing
    reads "(est.)". `/dev/services` renders its model server rows with no
    "undefined", "null" or "NaN", for running, not-running and unknown
    servers, and shows a running server's GPU memory.
11. The exporters: each change is additive (a test or a diff of the key
    sets, old against new); both write `data/` and `data/dev/`; a kill in
    the middle of a write leaves the old file whole. After the exporter
    change is in place, production's `/api/servers` still answers with
    `measured_age_s` under 120 and production's `/settings` still shows
    the llama values (production runs `item-M24`'s code on the same
    files).
12. Docs changed as scope 7 says.
13. `./scripts/run_tests.sh` passes (before: `7397 passed, 3 skipped, 54
    xfailed`). `tests/test_servers_gpu_mem.py` asserts the old estimate
    fallback and a reasonless `{}` for a stale file; it is rewritten by
    this item, so the count changes.
14. Nothing is installed, started, stopped or deployed by the build or the
    test, apart from the dev orchestrator by the override method. No chat
    request to production. `data/` is read, not written, apart from what
    the installed timers write. Production containers unchanged; HA and MA
    untouched; no sound; no notification.

Reports: `docs/sprint/M25-review.md` (done), `docs/sprint/M25-build.md`,
`docs/sprint/M25-e2e.md`.
