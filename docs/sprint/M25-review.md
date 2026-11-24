# M25 design review

Reviewer: Fable 5.2, 2026-11-24, about 14:05 to 14:15 local. Read:
`M25-criteria.md` (31a3521), the code and files it names, both exporter
files, `/dev/api/servers`, `/dev/api/system`, `/dev/api/metrics`, and
copies of `data/metrics.db` and `data/dev/metrics.db`. Nothing was changed.

Grades: **must** change before the build, **should** change, **note**.

| # | Grade | Where | Point |
|---|---|---|---|
| 1 | must | plumbing; criteria 3, 5, 8 | Dev has no memory file. `gpu_mem_export.py` writes one path (`data/gpu_mem.json`); dev reads `data/dev/`. `/dev/api/servers` answers `measured_age_s: null` today. |
| 2 | must | criteria 10, 13; scope 1 "health" | `/api/servers` cannot answer in 300 ms and cannot avoid model requests: each call makes LiteLLM run a completion on all three servers. Measured median 0.685 s. |
| 3 | must | criteria 8, 9 | Both pages fetch `/api/...` with a leading slash, so `/dev/resources` and `/dev/services` show production's payload, not dev's. |
| 4 | must | criteria 5, 6 (live parts) | The live steps need a changed environment or mount on the dev container; the criteria do not say how, and the guard rails forbid the obvious ways. |
| 5 | must | scope 1, 5; criterion 6 | "Running" is not defined. Three signals exist and they disagree in the cases the criteria test. |
| 6 | must | criterion 4; plumbing "Activity" | Window, boundary, "last used", time format and which database are undecided; no criterion checks the displayed time. |
| 7 | must | scope 3; Drew's condition | GPU busy and disk have no criterion. "The model filesystem" is `/`. "Disk used by models" (Drew's words) is not delivered by filesystem size and free space. |
| 8 | must | plumbing, last bullet | An exporter edit reaches production within 30 to 60 s, before any test: the timers run the working tree's script and production reads the same `data/` through a symlink. |
| 9 | should | criterion 1 | The grep always matches: `params:` hits `litellm_params:` and `drop_params:`. Word it as: `grep -c 'kronk:' litellm/config.yaml` prints 0, and `git diff item-M24 -- litellm/config.yaml` shows deleted lines only (the `kronk:` blocks and their blank padding). |
| 10 | should | criterion 7 | "Every numeric fact about a server" includes the port, and activity does not come from the exporters. List the fields: GPU memory, RAM, context, slots, file size, draft file size, both ages. |
| 11 | should | criterion 2 | The model file "equals the `-m` argument": the exporter keeps the basename only (`flags()`), and scope says file name. Word it as the basename of `-m`. The size needs the full path inside the exporter, which it reads and now drops; say the path itself stays out of the payload or goes in. Decide whether the draft file's size is reported (I would: it is a model file on disk and in memory). |
| 12 | should | criterion 3 | Say the sum is of `gtt_bytes` only, against `mem_info_gtt_used`. Measured now: 45.997 GB against 46.598 GB, 1.3 % apart, so 5 % holds. The gap is DRM clients outside the five units. |
| 13 | should | "One unit"; criterion 9 | `/services` prints `${measured_gb} GB measured` (decimal, /1e9). If the field is renamed, the line drops out silently and criterion 9 still passes. Decide: the payload carries bytes, each page formats GiB, and criterion 9 also requires the memory figure to appear on `/services` for a running server. |
| 14 | should | handoff line; scope 4 | `llm_metrics` holds one orchestrator's calls. The servers are shared: in the last 24 hours production recorded 33 calls and dev 164. Label the block "calls from this orchestrator", and soften the handoff line ("what Kronk asked of each one"). |
| 15 | should | scope 1, 2 | A `llama-*` unit that is running but not in the catalog (a model started by hand) is in neither list. Decide: list it with the other GPU processes, by unit name, with its memory and model file. Otherwise 30 GB can be in use and absent from the page. |
| 16 | should | scope 3, 4 | Not said which route carries the host block and the activity. Decide: host block in `/api/system`; activity in its own route (`/api/activity`), so `/services` does not pay for a query it does not show. |
| 17 | should | criterion 12 | `tests/test_servers_gpu_mem.py` asserts `vram_gb == 14` ("estimate retained") and that a stale file returns `{}` with no reason; both are rewritten by this item. Name the file so the count change is expected. `README.md` lines 222 to 233 are the Models text scope 7 means. |
| 18 | note | "What the investigation found" | Out of date or wrong, as measured: Talkie's RSS is 24.5 GB, not 16 GB. Production's table has 6,381 rows; last 24 hours gemma-4-e4b 31 calls, 97,424 prompt and 5,294 completion tokens, talkie 2 calls. The props timer was already firing at 14:04, not "about 14:10". devstral's RSS is 9.1 GB (the feature doc's 0.8 GB is old). |
| 19 | note | criterion 4 tests | No row in either database has a null token column (`record()` defaults to 0); production has 96 rows with 0 tokens. Production's table names seven models no longer in the catalog, none inside 24 hours, so that case exists only in tests. |
| 20 | note | `/services`, `/settings`, tests | No other reader of the `kronk:` blocks: `settings_view.py` and `model_lock.py` use `load_catalog()` for name and port only; `test_m24_catalog.py` reads `litellm_params` only. `/settings` on production has shown live props since the timer was installed, with no deploy. |
| 21 | note | promotion | LiteLLM's config is mounted from the release directory, so the tagged deploy recreates LiteLLM as every deploy does (all six production containers were "Up 2 minutes" after `item-M24`). The catalog change adds no restart of its own. |
| 22 | note | scope 1 "context size" | devstral reports `n_ctx` 131072 with `total_slots` 4; gemma 32768 with 4. Show both figures as reported; do not divide one by the other. |
| 23 | note | scope 3 "GPU busy now" | `gpu_busy_percent` is one instant; it read 0 on the host and in the container. Label it as an instant reading. |

## Must-change points

**1. Dev has no memory file.** `data/dev/gpu_mem.json` does not exist;
`gpu_mem_export.py` writes the single `GPU_MEM_OUT` path, while
`llama_props_export.py` writes both `data/` and `data/dev/`. Criteria 3, 5
and 8 are all stated on `/dev/`. Add to the plumbing: the memory exporter
writes both files, the way the props exporter does. This is an exporter
change, so point 8 applies to it.

**2. Health makes `/api/servers` slow and sends model requests.**
`fetch_health()` calls LiteLLM's `/health`, which runs a real chat
completion on every backend. Twenty requests to `/dev/api/servers`: median
0.685 s, range 0.52 to 0.94 s (`/api/system`: 1 to 3 ms). The llama logs
show one `POST /v1/chat/completions` per server per call; Talkie's took
652 ms on the CPU and reset its only slot's cached prompt (`n_past was set
to 9`). The model servers are shared by production and dev, so criterion
13's "no model request is sent to production" fails at the first request
to `/dev/api/servers`, and criterion 10's own 20 requests send 60
completions. The page polls every 30 s for as long as a tab is open.
Simpler design, and the one I would decide: health comes from the props
exporter (the server answered `GET /props` at the file's `ts`), LiteLLM is
not called on this route, and health is "unknown" when the file is stale.
`/services` already probes LiteLLM itself (`/probe/llm`, liveliness only).
Then 300 ms is easy, and criterion 13 can read: "no chat request to
production, and `/api/servers` causes no completion on any model server
(shown from the llama units' journals)". If LiteLLM's health is kept
instead, criterion 10 must exclude it and criterion 13 must allow it.

**3. The pages read production under `/dev/`.** `resources.html` fetches
`/api/system`, `/api/metrics`, `/api/servers`; `services.html` fetches
`/api/servers` and `/api/agents`. nginx strips `/dev/` only from paths
that carry it, so a tester on `/dev/resources` compares the page with
production's payload, built by `item-M24`'s code. Add a criterion: both
pages use relative URLs (as `services.html` already does for
`api/config`), shown by the requests the rendered page makes.

**4. The live steps of criteria 5 and 6 are not doable as written.**
"Pointing the dev orchestrator at a stale copy" needs `GPU_MEM_PATH` or a
mount changed; "a catalog fixture" needs `/litellm-config.yaml` replaced.
`.env.dev` and `data/` are not to be edited by hand, and criterion 13
allows no write to `data/`. State the method: a compose override file in
scratch (`-f docker-compose.yml -f <scratch>/override.yml --profile dev up
-d orchestrator-dev`) that mounts scratch copies read-only and sets the
two paths, then the standard dev `up -d --build` to restore, with the
restored mounts shown by `docker inspect`. The catalog fixture is the real
catalog plus one entry, so the dev lock (`model_lock.py` reads ports from
the catalog) is not disturbed. Or drop the live parts and keep the fixture
tests; the override touches only the dev container either way.

**5. "Running" needs one definition.** The signals are: LiteLLM health,
presence in `gpu_mem.json` (a unit with a MainPID), and a `/props` answer
in `llama_props.json`. A server loading its model has a unit and no
`/props`. With both files stale nothing can be said. Decide: running means
the port appears in a fresh `gpu_mem.json`; absent from a fresh file means
"configured, not running"; with the file stale or missing the server is
listed as "state unknown", not under "not running". A running server whose
`/props` failed shows its memory and "not answering" for context, slots
and file. Criterion 6 then states which fresh file the fixture uses.

**6. Activity is under-specified, and the time can be shown wrong.**
(a) Timestamps are text, UTC, no zone mark, one-second resolution
(`2026-11-24 19:03:20`). The container has no `TZ` (its clock reads UTC),
so the server cannot produce "the host's local time"; and JavaScript
parses that string as local time, five hours off. Decide: the payload
carries ISO 8601 with `Z`, the browser shows its own local time, and a
criterion checks one displayed "last used" against the row. (b) The
window: `timestamp >= now - 24 h`, the boundary row counted, and `now`
passed in so the test can pin it. (c) "Last used": over the whole table,
not the window. On production devstral-2512-q4 was last used 2026-08-29;
"no calls in 24 hours, last used 2026-08-29" is the useful line, and the
SQL in criterion 4 depends on which is meant. (d) On dev the copy to
compare with is `data/dev/metrics.db`. (e) A model in the table and not in
the catalog, with calls inside the window: shown apart as "not in the
catalog", since leaving it out hides real use.

**7. Scope 3 has no criterion, and disk is not what Drew asked for.**
Nothing checks GPU busy, RAM, GTT or disk. `gpu_busy_percent` is readable
inside the container (checked in `orchestrator-dev`), like the GTT
counters `/api/system` already reads, so it needs no exporter and is not
30 s old; the plumbing's "added to an exporter" is the harder way. The
model files are in two directories (`/opt/models/…` 18 GB,
`/home/drew/model-staging/…` 14 GB), both on `/` (1.8 T, 706 G used by
everything). Drew's words are "disk used by models". Decide: per server
the file sizes (criterion 2), a total of the loaded model and draft files,
and the free space of the filesystem that holds them, found by `statvfs`
on each file's directory in the exporter; no directory name is typed
anywhere. Add criteria: GPU busy equals the sysfs file read at the same
moment; the total equals the sum of `stat` sizes; free space equals `df`
within 1 %.

**8. Exporter edits are live in production at once.** Both installed units
run `scripts/*.py` from the working tree, and production's `/data` is a
symlink to the repo's `data/`. A syntax error saved mid-build stops the
30 s file; after 120 s production's `/resources` falls back to "(est.)".
A changed key breaks `item-M24`'s `/settings` and `/resources`, which read
the same files. The criteria fence half-written files only. Add: exporter
changes are additive (existing keys keep their names and meaning); the
builder develops against a scratch copy of the script with `--print` or a
scratch `--out`, and moves it into place only after its tests pass; the
build report shows production's `/api/servers` `measured_age_s` under 120
after the change. The test agent checks the same.
