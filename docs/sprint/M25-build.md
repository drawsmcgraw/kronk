# M25 build report

Builder: Opus 5.5, 2026-11-24, about 14:10 to 14:50 local. Contract:
`docs/sprint/M25-criteria.md` (50c9b48). Handoff line: /resources shows
only measured facts about each model server, and what this Kronk asked of
each one in the last 24 hours.

## What changed, file by file

| File | Change |
|---|---|
| `scripts/gpu_mem_export.py` | Writes `data/gpu_mem.json` and `data/dev/gpu_mem.json` by default (`GPU_MEM_OUT`, when set, still names one file). `--out FILE` (repeatable) and `--print`; `--once` kept as an alias of `--print`. Atomic write by `mkstemp` in the target directory, `chmod 0644`, `os.replace`; the temp file is removed on an exception. No key added or changed. |
| `scripts/llama_props_export.py` | Additive keys: per server `model_bytes` and `model_draft_bytes` (stat on the `-m` and `--model-draft` paths, relative paths resolved against `/proc/PID/cwd`; null plus `*_bytes_error` when stat fails); top-level `disk`: `model_files_bytes` (a file loaded twice counts once) and `filesystems` (`mount`, `size_bytes`, `used_bytes`, `avail_bytes` = df's Available, `files`: base names), by `statvfs` on each file's directory. Paths stay out of the file. `flags()` unchanged in output (base names); `raw_flags()` split out. Temp file removed on an exception. |
| `orchestrator/servers.py` | Rewritten. `read_source()` reads one exporter file into `{file, state (fresh/stale/missing/unreadable), age_s, max_age_s, every_s, reason}`. `build_servers()` joins catalog, memory file and props file by port under the one definition of running; every null fact has a reason in `reasons`. Other GPU processes (Whisper, uncatalogued `llama-*` units, joined to the props file by unit name). `read_gpu()` (GTT and `gpu_busy_percent` from sysfs) and `disk_block()` for `/api/system`. `load_catalog()` returns name, api_base, port only. `fetch_health()` and `_health_key()` removed: nothing calls LiteLLM. `load_llama_props()` (for `/settings`) unchanged. |
| `orchestrator/main.py` | `/api/servers` = `servers.build_servers(...)`, no network call. `/api/system` adds `gpu_busy_percent`, `gpu_reasons`, `read_at`, `disk`, `disk_reason`, `sources`; `gtt_total`/`gtt_used` are null (with a reason) rather than 0 when no card has the counters. New `/api/activity` (503 with a specific `error` when `metrics.db` cannot be read). `JSONResponse` imported. |
| `orchestrator/metrics.py` | `activity(catalog_names, now=None, hours=24)`: one `GROUP BY model` query on a read-only connection; `timestamp >= now - 24 h` (boundary counted), `MAX(timestamp)` over the whole table; ISO 8601 with `Z`; catalog models always listed, others only with calls in the window, under `not_in_catalog`. |
| `litellm/config.yaml` | The three `kronk:` blocks deleted (15 lines, deletions only). |
| `orchestrator/settings_view.py`, `orchestrator/static/settings.html` | `ctx_k_litellm` removed: `/settings` printed the catalog's hand-written `ctx_k` ("32768 tokens (litellm ctx_k 128)"). Now "32768 tokens". Not foreseen by the criteria; see "Found" below. |
| `orchestrator/static/resources.html` | Rebuilt on the new payloads, same CSS, cards and memory bar. Groups: system memory (bar with one segment per GPU process), model files on disk, model servers grouped by state, other GPU processes, calls from this Kronk in the last 24 hours. Each group names its source and age and says STALE. Sizes in GiB. Timing row removed; `/api/metrics` no longer fetched. All URLs relative, the stylesheet and script included. |
| `orchestrator/static/services.html` | Model server rows from the new payload: model file, `n_ctx`, GPU memory in GiB, the state reason when not running, agents; tag running / not running / state unknown; dot = health answering. Agent dots follow their model's health. `api/servers`, `api/agents`, `static/…` relative. The probes and `/api/upgrades`, `/api/backups` left absolute (deliberate, M16/M18). Resources card description updated. |
| `tests/test_servers_gpu_mem.py` | Rewritten (criterion 13 foresaw it): sources, joining, staleness, not running, not answering, older exporter files, every file missing, no httpx client built, `read_gpu`, `/api/system`. 18 tests (6 before). |
| `tests/test_m25_exporters.py` | New: key sets against item-M24's, sizes, missing files, relative paths, disk dedup and statvfs, default outputs, `--out`/`--print`/`--once`, failed write, SIGKILL mid-write. 13 tests. |
| `tests/test_m25_resources.py` | New: activity (the six cases of criterion 5, plus zone conversion and the route), the catalog, the pages' URLs and absent hand-written fields. 14 tests. |
| `tests/test_m21s2_view.py` | One assertion: `ctx_k_litellm == 128` became "absent". |
| `systemd/gpu-mem-export.service`, `.timer`, `systemd/llama-props-export.service` | Comments only (both output paths; no estimate fallback; `--print`). `ExecStart` and timing unchanged. |
| `docs/features/resources-live-memory.md` | Rewritten for what the page now is. |
| `docs/incidents/INVESTIGATION_2026-11-08_resources_page.md` | Dated closing line. |
| `README.md` | Models text and table: the catalog holds names and addresses; the VRAM column replaced by the model file, context (slots) and GPU memory / RAM read from `/resources` on 2026-11-24. |
| `kronk-context.md` | One line: it told the agents each catalog entry has a `kronk:` block. Not named by the criteria; it became false with criterion 1. |

## Criteria

Live checks ran against the dev pair through nginx (`http://localhost/dev/…`)
with a scratch script that reads the host side itself (`/proc`, `stat`,
`df`, sysfs, `GET /props` on the three loopback ports). Times are local
(EST).

**1. Catalog.** `grep -c 'kronk:' litellm/config.yaml` prints `0`.
`git diff item-M24 -- litellm/config.yaml`: 15 lines, all `-`, none `+`.
`test_catalog_has_no_kronk_block` fails if a `kronk:` block (or any key
beside `model_name`, `litellm_params`) returns. **Pass.**

**2. Server facts equal the server's own answer** (14:28:07, payload then
host reads within a second):

| server | n_ctx | total_slots | model file | model bytes | draft | draft bytes |
|---|---|---|---|---|---|---|
| devstral-2512-q4 | 131072 = 131072 | 4 = 4 | `mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf` = basename of `-m` | 14334438272 = stat | none = none | none |
| gemma-4-e4b | 32768 = 32768 | 4 = 4 | `gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf` = | 4215695776 = | `mtp-gemma-4-E4B-it-Q8_0-202607.gguf` = | 98671936 = |
| talkie | 2048 = 2048 | 1 = 1 | `talkie-1930-13b-it-Q8_0.gguf` = | 14114014848 = | none | none |

All 18 comparisons OK. **Pass.**

**3. GPU memory against an independent fdinfo read** (my own reader:
distinct `(drm-pdev, drm-client-id)`, gtt + vram):

| process | payload gpu_bytes | independent | diff |
|---|---|---|---|
| devstral-2512-q4 | 36519350272 | 36519350272 | 0.000 % |
| gemma-4-e4b | 5480112128 | 5480112128 | 0.000 % |
| talkie | 6385664 | 6385664 | 0.000 % |
| wyoming-whisper-dev | 1869963264 | 1869963264 | 0.000 % |
| wyoming-whisper | 2132406272 | 2132406272 | 0.000 % |

Sum of `gtt_bytes` over every listed process 45996654592 against
`mem_info_gtt_used` 46597824512: 1.29 % (memory file 2.7 s old). Both
Whisper processes are listed under `other_processes`. **Pass.**

**4. `/api/system`.** Ten paired reads, payload then sysfs right after:
`0/0` ten times (the GPU was idle; every pair agrees, but an idle GPU makes
this a weak test of the reading). `test_read_gpu_reads_gtt_and_busy_from_one_card`
shows the value is the file's. Model total `32762820832` = sum of `stat`
on the four loaded files (`32762820832`). Filesystem `/`: payload
`avail_bytes` 1109222752256, `df -B1` Available 1109223141376, 0.0000 %;
size 1966736678912 both. **Pass.**

**5. Activity** (14:28:25, payload then a copy of `data/dev/metrics.db`,
SQL with the payload's `since`):

| model | api (calls, prompt, completion, last used) | SQL |
|---|---|---|
| devstral-2512-q4 | 0, 0, 0, 2026-11-18T17:38:36Z | same |
| gemma-4-e4b | 65, 170375, 8877, 2026-11-24T18:24:24Z | same |
| talkie | 99, 40468, 5886, 2026-11-24T18:56:16Z | same |

No model outside the catalog in dev's table. Tests cover: no rows; rows
only older than 24 h (not counted, last used shown); a row exactly on the
boundary (counted) and one a second before it (not); out of the catalog
with calls in the window (shown apart); out of the catalog with none (left
out); 0-token rows (counted as calls). Rendered page (Firefox, EST):
devstral "last used 11/18/2026, 12:38:36 PM" = 17:38:36Z − 5 h. **Pass.**

**6. Staleness.** Tests: memory file 121 s old is stale (120 s is not),
props file 301 s (300 s is not); stale memory → every server `unknown`,
memory facts and `memory_age_s` null with "stale: gpu_mem.json was written
200 s ago …", props facts kept; stale props → health `unknown`, context,
slots, files, `props_age_s` null with the reason, memory kept. Live by
override (14:39 to 14:41): both files copied to scratch with `ts` 400 s
back, mounted read-only at `/m25`, `GPU_MEM_PATH`/`LLAMA_PROPS_PATH` set.
`/dev/api/servers`: both sources `stale`, `age_s` 406.7, every server
`state: unknown`, `health: unknown`, all facts null with the reason,
`other_processes: []`; `/dev/api/system` `disk: null`, `disk_reason`
"stale: llama_props.json was written 407 s ago …". Rendered
`/dev/resources`: "Per-process GPU memory: gpu_mem.json STALE, written
955 s ago; figures from it are withheld", the same for both server source
lines and the disk card, servers under "STATE UNKNOWN" with no figure;
`/dev/services`: three rows tagged "state unknown" with the stale reason,
no figure. **Pass.**

**7. Not running, not answering.** Tests for both. Live by override
(14:30): the real catalog plus `m25-ghost` on port 11499 (`ss -ltn`:
nothing listens there), mounted read-only on `/litellm-config.yaml`.
`/dev/api/servers`: `state: not_running`, `state_reason` "configured, not
running: port 11499 is not in gpu_mem.json (written 1 s ago)", every
figure and both ages null with that reason, health `not_answering`.
`/dev/resources` lists it under "CONFIGURED, NOT RUNNING" with no figure;
`/dev/services` shows ":11499 configured, not running: … · unassigned",
tag "not running". The running-but-not-answering case is in the tests only,
as the criterion asks. **Pass.**

**8. No hand-written number.** `test_every_exporter_file_missing_leaves_no_figure`:
through `/api/servers` with both files absent and the real catalog, every
server's `gpu_bytes`, `rss_bytes`, `n_ctx`, `total_slots`, `model_bytes`,
`draft_bytes`, `memory_age_s`, `props_age_s` is null with a "does not
exist" reason and state is `unknown`. The page renders a reason in place of
every null (seen in the stale render: no figure on any server card).
**Pass.**

**9. No completion, no LiteLLM, speed.** Offline:
`test_api_servers_calls_nothing` replaces `httpx.AsyncClient` with a class
that fails the test when built, under the `no_network` fixture; `servers`
has no `fetch_health`. Live: journal window 14:28:11 to 14:28:14 around 20
requests each to the three routes: completion markers 0, 0, 0 for
devstral, gemma, talkie. (gemma and devstral do not log `POST
/v1/chat/completions`; a completion shows there as `params_from_: Chat
format` and `launch_slot_`. I counted all three markers, and checked they
catch a real one: production's `/api/servers` at 14:17:31 shows on all
three units.) Medians of 20: `/dev/api/servers` 1.9 ms (max 3.9),
`/dev/api/system` 0.9 ms (max 1.6), `/dev/api/activity` 7.0 ms (max 11.1).
**Pass.**

**10. Pages.** Rendered in headless Firefox 159 (snap) driven over WebDriver
BiDi on loopback, 4 to 6 s after load, scratch profile with every
non-loopback request sent to a dead proxy. Requests made by
`/dev/resources` (Resource Timing): `/dev/static/js/kronk.js`,
`/dev/static/kronk.css`, `/dev/api/system`, `/dev/api/activity`,
`/dev/api/servers`, plus the browser's own `/favicon.ico`. `/dev/services`:
`/dev/static/…`, `/dev/api/servers`, `/dev/api/config`, `/dev/api/agents`,
and, absolute by design, `/probe/*` (no `/dev/probe` route exists in nginx),
`/api/upgrades`, `/api/backups` (one status file per house, M16/M18).
Figures: 27 of 27 figures on the rendered `/dev/resources` (each server's
GPU memory, RAM, context, slots, model and draft with sizes; both Whisper
processes; disk total and filesystem; GTT; GPU busy; the three activity
rows) match `/dev/api/servers`, `/dev/api/system` and `/dev/api/activity`
fetched right after. RAM Available moved 0.2 GiB between the two reads
(42.8 against 43.0 GiB). Every size says GiB; each group names its file
and age; no "(est.)", "undefined", "null" or "NaN" in the page text of any
render. `/dev/services` rows, running: "mistralai_Devstral-…Q4_K_M.gguf ·
131072 ctx · 34.0 GiB GPU memory · → Coding Agent, Devops Agent" (and
gemma 5.1 GiB, talkie 0.01 GiB); not running and unknown as in 7 and 6.
**Pass, with the exception above for the deliberately absolute URLs on
`/services`.**

**11. Exporters.** Additive: key diff of the live outputs, item-M24's
script against the new one (old file vs new `--print`): `gpu_mem.json`
removed none, added none; `llama_props.json` removed none, added `disk.*`,
`servers[].model_bytes`, `servers[].model_draft_bytes`. Tests pin
item-M24's key sets. Both write `data/` and `data/dev/`
(`test_both_write_data_and_data_dev_by_default`; live: all four files
written by the timers, ages 17 s and 60 s at 14:44:34). A SIGKILL in the
middle of a write leaves the old file byte for byte
(`test_kill_in_the_middle_of_a_write_leaves_the_old_file_whole`, both
scripts: a child process writes half the JSON, flushes, is killed; the
target is unchanged and the half is in the temp file). Production: after
the memory exporter went in (rename at 14:17:16, timer at 14:17:24),
`curl -s http://localhost/api/servers` gave `measured_age_s` 7.3 with
devstral 36.5, gemma 5.5, talkie 0.0 GB; after the props exporter (rename
14:17:42, timer 14:18:24), production `/api/settings` gave `props_age_s`
12.3, no error, the three servers with n_ctx 131072/32768/2048 and their
model files; again at 14:44:34: `props_age_s` 59.7, no error. Neither
script was ever saved half-edited into `scripts/`: both were built and
tested in scratch and moved in with `cp` to a dot-file then `mv`. **Pass.**

**12. Docs.** Feature doc rewritten, investigation closed with a dated
line, README lines 222 to 233 rewritten. **Pass.**

**13. Suite.** `./scripts/run_tests.sh`, last lines:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
7436 passed, 3 skipped, 54 xfailed, 6 warnings in 99.42s (0:01:39)
```

Before: 7397 passed. 45 tests in the new and rewritten files, 6 gone from
the old `test_servers_gpu_mem.py` (7397 − 6 + 45 = 7436). **Pass.**

**14. Nothing installed, no chat to production, data read only.**
Nothing installed, enabled, started or stopped; no unit file under
`~/.config/systemd/user/` touched. Only the dev pair was rebuilt (the
standard line, twice) and the dev orchestrator recreated by override
(three times: catalog, stale, stale after a page fix). Production
containers: all "Up 42 minutes" at 14:44, as at the start. No write under
`data/` by hand; the timers wrote the new files. No sound, no
notification, HA and MA untouched. Two caveats, both below under "Found":
my two `curl` checks of production `/api/servers` (the brief's check) and
the renders of `/dev/services` each triggered LiteLLM health completions
through production code. **Pass with those noted.**

Dev restored at 14:41:11 with `docker compose --profile dev up -d --build
tool_service-dev orchestrator-dev`; `docker inspect kronk-orchestrator-dev-1`:
mounts `data/dev -> /data`, `kronk-context.md`, `litellm/config.yaml ->
/litellm-config.yaml` (ro), `kronk-config/dev -> /config`; no
`GPU_MEM_PATH` or `LLAMA_PROPS_PATH` in its environment; all three
servers `running`/`answering`, both sources fresh.

## Not done, and why

- **`/probe/orchestrator` on `/services`** still reaches LiteLLM's
  `/health` (below). Out of scope: criterion 9 is about `/api/servers`, and
  `/api/status` is the deploy check.
- **`/performance` and the zeroed `metrics.record` arguments**: out of
  scope, as stated.
- **Running but not answering, live**: tests only, as criterion 7 asks.
- **GPU busy on a busy GPU**: not shown live; making the GPU busy needs a
  model request, which this item does not send.
- **Other pages' links on `/dev/services`** (`/resources`, `/settings`, …
  in the Pages list) are absolute and open production's pages. Not
  changed: links, not fetches, and the M21 comment in the file shows the
  absolute/relative choice is made per section.

## Found that the criteria did not foresee

1. **`/settings` read the `kronk:` block.** `settings_view.models()`
   returned `ctx_k_litellm` from the catalog, and `settings.html` printed
   "(litellm ctx_k 128)" beside the real 32768. Review point 20 said no
   other reader existed. Removed with the block; one assertion in
   `tests/test_m21s2_view.py` changed with it.
2. **`kronk-context.md` told the agents about the `kronk:` block.** One
   line corrected. It is mounted from the release directory, so production
   gets it at the deploy.
3. **`/services`' orchestrator dot costs a completion on every model
   server.** `/probe/orchestrator` proxies to production's `/api/status`,
   which calls LiteLLM `/health`, which runs a chat completion on each
   backend. Rendering `/dev/services` at 14:29:12 put 2, 2 and 4
   completion markers in the gemma, devstral and talkie journals. The page
   polls it every 30 s while open, and `scripts/deploy_tag.sh` checks the
   same route. The same cost M25 removed from `/api/servers`. A candidate
   for its own item (a probe that does not run a model, e.g. LiteLLM's
   `/health/liveliness`, which `/probe/llm` already uses).
4. **The brief's production check has the same cost.** Each
   `curl http://localhost/api/servers` against production (item-M24's
   code) runs three completions. I made it once (14:17:31), as the brief
   ordered, and used `/api/settings` and the files' ages for the later
   checks. After the deploy, production's `/api/servers` no longer does
   this.
5. **gemma and devstral do not log `POST /v1/chat/completions`** with
   this llama.cpp build; Talkie's older build does. A journal check for
   completions must also look for `params_from_: Chat format` or
   `launch_slot_`.
6. **A stray headless browser.** The first renders used Firefox's
   `--screenshot`, which fires before the page's fetches finish (the
   `/services` dynamic sections came out empty), so I drove Firefox over
   WebDriver BiDi instead. Stopping the snap wrapper left the real browser
   running (PID 3009754, started 14:31:15, killed 14:34:25), on
   `/dev/services`. In that window the journals show no completion and
   nginx three `/probe/orchestrator` requests in ten minutes, so it added
   no load I can find. The renderer then killed the browser by the PID in
   the profile lock, and a fresh profile was used per run (a reused one
   would not open its remote port). The render profiles lived in
   `~/snap/firefox/common/m25-render` (snap Firefox cannot read `/tmp`);
   I removed that directory, which I had created, at 14:42.
7. **`/api/system` returned `gtt_total: 0` when no card had counters.**
   Now null with a reason, per "never a default".
8. **The memory bar's "Free" warning thresholds** are now in GiB (2, 4, 8
   GiB) instead of GB; the page says GiB throughout. A 7 % shift of each
   threshold.

## Install and deploy steps for the coordinator

- **Units**: no `ExecStart` or timer change. `systemd/gpu-mem-export.service`,
  `systemd/gpu-mem-export.timer` and `systemd/llama-props-export.service`
  changed in comments only. Installing them is optional and changes
  nothing at run time: `cp systemd/gpu-mem-export.{service,timer}
  systemd/llama-props-export.service ~/.config/systemd/user/ &&
  systemctl --user daemon-reload`.
- **Exporters**: already live (they run from the working tree since 14:17).
  Nothing to do at the deploy.
- **Deploy**: tag and `scripts/deploy_tag.sh plan|deploy`. The deploy
  carries `litellm/config.yaml` without the `kronk:` blocks (LiteLLM is
  recreated by every deploy anyway; no extra restart) and `kronk-context.md`.
- **Verify on production after the deploy**: `/api/servers` `sources`
  both `fresh` and every server `running`/`answering`; `/resources`
  renders with no "(est.)"; `/services` model rows show GiB; `/settings`
  model table reads "N tokens" without "litellm ctx_k". Note that
  `deploy_tag.sh`'s own `/api/status` check runs a LiteLLM health round
  (finding 3).
- **ROADMAP**: the coordinator's (move the line; finding 3 is a candidate
  for Drew).

## Questions

None.
