# M25 fix round 1: the memory bar's labels, three states on /services

Fix agent (Opus 5.5), 2026-11-24, 16:52 to 17:05 local (EST), on
`cd485f3`. Contract: `docs/sprint/M25-criteria.md`, the last ruling (S2,
S3, S4, N5, N6, N9). Findings: `docs/sprint/M25-e2e.md`.

Guard rails broken: none. No payload field renamed or removed; no
exporter change (one docstring line); no figure added to or removed from
either page. The dev orchestrator was recreated three times: the plain
build line, the override, the plain build line again.

## What changed, file by file

- `orchestrator/static/resources.html`
  - S2. The memory bar's labels are a table, `MEM_BAR_LABELS`, with two
    entries: `fresh` and `withheld` (the memory file stale, missing or
    unreadable). `memBarLabels(source)` picks one from
    `sources.gpu_mem.state`; `memBarSegments(sys, srv)` builds the
    segments from it and the two payloads. Withheld, no per-process
    segment is drawn whatever the rows carry, and the used segment reads
    "Used RAM, all of it (per-process GPU memory not known)". Fresh, each
    per-process segment reads "<name> GPU memory", the next one "Other
    used RAM". A new line under the legend (`#mem-note`, the page's
    existing small grey `src` style) says what the segments are in that
    state. The arithmetic is unchanged.
  - N9. On a server card whose context and slots are null for one reason
    (a `/props` error), the card prints "context, slots: <reason>" once
    and drops the health line when it is the same string.
- `orchestrator/static/services.html` (S3)
  - `HEALTH_DOT` maps the payload's three health values to three dot
    classes: answering `up`, not answering `down`, unknown `unknown` (a
    new `.status-dot.unknown` rule, the same grey as an unchecked dot).
  - `SERVER_TAG` and `serverView(row)`: the tag is "state unknown" when
    the memory file is not fresh, "not running", "health unknown" when
    the props file is not fresh, "running, not answering", or "running".
    The reasons go into the row's text and the dot's tooltip: the state
    reason when not running, the health reason when not answering (not
    repeated, and not added under "not running", which already says it).
  - `agentView(server)`: an agent's dot is its model server's dot, with
    the server's tag and reasons as the tooltip. A model with no server
    row is unknown, not down.
  - `setDotState(id, cls, title)` sets one of the three classes. The old
    two-state `setDot` stays for the config, upgrade and backup cards.
- `tests/test_servers_gpu_mem.py` (S4):
  `test_running_is_decided_by_the_memory_file_alone_where_the_two_files_disagree`.
  Both files fresh; `props-only` (port 11460) is in `llama_props.json`,
  with a model and a draft file, and not in `gpu_mem.json`; talkie is in
  `gpu_mem.json` and not in `llama_props.json`. Asserts `props-only` is
  `not_running` with all twelve facts null and each reason the state
  reason, and talkie `running` with its memory and `not_answering` with
  context, slots and file null.
- `tests/test_m25_fix1_pages.py` (new, 13 tests): see "The pin" below.
- `README.md` (N5): the model table drops the "GPU memory / RAM,
  2026-11-24" column and keeps model, unit, port, model file, context
  (slots) and role; "(CPU only)" moved beside Talkie's model file. The
  sentence above it now says memory drifts by the hour and `/resources`
  has it, measured.
- `scripts/gpu_mem_export.py` (N6): the docstring names
  `servers.gpu_mem_source()` instead of `servers.load_gpu_mem()`.
- `docs/features/resources-live-memory.md`: the bar's labels in both
  states; two gotchas (three dot states on `/services`; the label tables
  are JSON literals because the suite has no JavaScript runtime).

## The pin (job 6)

The repo has no way to run page JavaScript in the suite: no `node`,
`deno`, `bun` or `qjs` on the host, nothing in `tests/requirements.txt`,
and no test that runs a page's script. None was added. So:

- The state-to-label decisions are JSON literals in the page scripts
  (`MEM_BAR_LABELS`, `HEALTH_DOT`, `SERVER_TAG`). The tests read them with
  `json.loads` and assert each state's text, that `unknown` is not
  `down`, and that the health values `servers.py` can emit all have a dot.
- The small functions that select from them (`memBarLabels`,
  `memBarSegments`, `serverView`, `agentView`) are checked as text: the
  selecting expression is present, and no two-state `setDot` call is left
  on a model server or an agent.
- From Python, the payload fields the labels are built from, in every
  state the pages show: `sources.gpu_mem.state` (fresh, stale, missing,
  unreadable) and no row carrying a GPU figure when it is not fresh;
  `state`, `health`, `state_reason` and `health_reason` for props stale,
  memory stale, running not answering, and both missing, each reason
  naming its file and age; for a `/props` error, the context, slots and
  health reasons being one string (what the card's print-once relies on).

This is a pin of the decisions, not of the rendering. A rewrite that
keeps the behaviour and changes the text fails these tests; a change in
how the page draws a correct label passes them. N10 stays open.

## Mutants

In a scratch copy of the tracked files, removed after. The two from the
test report's S4, against `tests/test_servers_gpu_mem.py` and the new file:

| Mutant (`orchestrator/servers.py`) | HEAD's test file | This round's |
|---|---|---|
| Running if the port is in the props file (`elif proc:` to `elif proc or pentry:`, with `proc = proc or {}` before `_memory`) | 18 passed | **1 failed**, 31 passed: `test_running_is_decided_by_the_memory_file_alone_where_the_two_files_disagree` |
| A not-running server keeps its props figures (`if out["state"] == NOT_RUNNING:` to `if False:`) | 18 passed | **1 failed**, 31 passed: the same test |

Eight page mutants against `tests/test_m25_fix1_pages.py`, each killed (1
failed, 12 passed): the unknown dot set to `down`; the agent dot back to
two states; "health unknown" tagged "running"; a stale memory file given
the fresh labels; the withheld label back to "System / other"; the legend
without "GPU memory"; the `/props` error printed again as the health
line; withheld still listing processes.

## Live on dev

Dev rebuilt from the tree with the plain build line (16:52). Rendered in
headless snap Firefox through `geckodriver` (WebDriver on loopback), window
1280 wide, text read from the DOM about 3 s after load. Every request
went through a forward proxy of mine on loopback that passed `GET` to this
host's port 80 only and refused `/probe/*`, `CONNECT` and every other
host: 99 probe requests and 442 of the browser's own connections refused;
none passed. Passed: `/dev/resources`, `/dev/services`, `/dev/static/*`,
`/dev/api/{system,servers,activity,agents,config}`, and the two
production read-only status routes `/services` reads by design
(`/api/upgrades`, `/api/backups`), and `/favicon.ico`.

Stale cases by the override method: a compose override in scratch for
`orchestrator-dev` only (`--no-deps`), a scratch directory mounted
read-only at `/m25`, `GPU_MEM_PATH` and `LLAMA_PROPS_PATH` set to it. The
files in it were copies of `data/dev/`'s with `ts` rewritten just before
each render (memory 200 s old; props 400 s old). The "before" text is
HEAD's two pages, served by the proxy from a saved copy (`git show
HEAD:…`) at `/dev/old-resources` and `/dev/old-services`, so they fetched
the same dev routes at the same moment.

### Memory file fresh

Before, legend: `devstral-2512-q4 — 34.0 GiB`, `gemma-4-e4b — 5.1 GiB`,
`talkie — 0.01 GiB`, `wyoming-whisper-dev — 1.9 GiB`,
`wyoming-whisper — 1.9 GiB`, `System / other — 29.0 GiB`.

After, legend: `devstral-2512-q4 GPU memory — 34.0 GiB`,
`gemma-4-e4b GPU memory — 5.1 GiB`, `talkie GPU memory — 0.01 GiB`,
`wyoming-whisper-dev GPU memory — 1.9 GiB`,
`wyoming-whisper GPU memory — 1.9 GiB`, `Other used RAM — 29.1 GiB`.
Under it: "Per-process segments are GPU memory (GTT + VRAM). Other used
RAM is used RAM minus them: the system, caches, and each process's own
RAM (the RAM figure on its card)." Segment tooltips the same, plus
`Available: 50.8 GiB`.

`/dev/services`, fresh, after: three rows, dot `up`, tag "running",
e.g. "mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf · 131072
ctx · 34.0 GiB GPU memory · → Coding Agent, Devops Agent". Seven agents
`up`, tooltip "gemma-4-e4b: running" and so on. Same as before.

### Memory file stale (200 s)

Before, legend: `System / other — 72.1 GiB`.

After, legend: `Used RAM, all of it (per-process GPU memory not known) —
72.0 GiB`. Under it: "Per-process GPU memory is not known (gpu_mem.json,
below), so it is not split out: the model servers' GPU memory is inside
used RAM." Stats: "GPU memory, listed processes stale". Source line:
"Per-process GPU memory: gpu_mem.json STALE, written 202 s ago; figures
from it are withheld". The string "System / other" is nowhere on the page.

`/dev/services`, before and after the same: tag "state unknown", dot
`up` (the props file is fresh and each server answered), and the reason in
the row: "mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf ·
131072 ctx · stale: gpu_mem.json was written 205 s ago (the exporter runs
every 30 s; older than 120 s is stale) · → Coding Agent, Devops Agent".
After, the dot's tooltip carries the same reason, and each agent's reads
"gemma-4-e4b: state unknown (stale: gpu_mem.json was written 205 s ago
…)".

### Props file stale (400 s)

Before, `/dev/services`: every row "34.0 GiB GPU memory · → Coding
Agent, Devops Agent", tag "running", dot `down`; all seven agent dots
`down`. No reason anywhere.

After: every row dot `unknown` (grey), tag "health unknown", text
"34.0 GiB GPU memory · stale: llama_props.json was written 405 s ago (the
exporter runs every 60 s; older than 300 s is stale) · → Coding Agent,
Devops Agent" (gemma 5.1 GiB, talkie 0.01 GiB the same way). All seven
agent dots `unknown`, tooltip "devstral-2512-q4: health unknown (stale:
llama_props.json was written 405 s ago …)".

`/dev/resources` in this state: legend as fresh (the memory file is
fresh); "Health, context, slots and files: llama_props.json STALE, written
402 s ago; figures from it are withheld".

### Both stale

After: legend withheld as above; every `/services` row dot `unknown`, tag
"state unknown", both reasons: "stale: gpu_mem.json was written 205 s ago
(…) · stale: llama_props.json was written 405 s ago (…) · → …".

### Running, not answering, and a `/props` error (both files fresh)

Fixture: gemma's entry removed from the props file; devstral's replaced
by one with `"error": "GET /props failed: TimeoutError (fixture, fix
round 1)"` and no context or slots.

Before, `/dev/services`: gemma and devstral dot `down`, tag "running",
no reason ("5.1 GiB GPU memory · → Coordinator, Research Agent, Home
Agent, Assistant Agent"). Before, devstral's card on `/dev/resources`
(N9): "context GET /props failed: TimeoutError (fixture, fix round 1) ·
slots GET /props failed: TimeoutError (fixture, fix round 1) | model … ·
13.3 GiB | GET /props failed: TimeoutError (fixture, fix round 1)".

After, `/dev/services`:
- gemma: dot `down`, tag "running, not answering", "5.1 GiB GPU memory ·
  not answering: port 11438 is not in llama_props.json (written 5 s ago)
  · → Coordinator, Research Agent, Home Agent, Assistant Agent".
- devstral: dot `down`, tag "running, not answering",
  "mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf · 34.0 GiB GPU
  memory · GET /props failed: TimeoutError (fixture, fix round 1) · →
  Coding Agent, Devops Agent".
- talkie: dot `up`, tag "running". The gemma and devstral agents `down`
  with the reason as tooltip; Talkie Agent `up`.

After, devstral's card: "running | not answering | context, slots: GET
/props failed: TimeoutError (fixture, fix round 1) | model
mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf · 13.3 GiB". The
error is printed once. gemma's card is unchanged ("context, slots, files:
not answering: port 11438 is not in llama_props.json (written 2 s ago)",
once before and after).

No "undefined", "null" or "NaN" in the model or agent rows of
`/dev/services`, or anywhere on `/dev/resources`, in any of the five
states.

### Restore

`docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev` at 17:01. `docker inspect kronk-orchestrator-dev-1`:
started 22:01:00Z; mounts `kronk-config/dev -> /config`, `data/dev ->
/data`, `kronk-context.md -> /kronk-context.md` (ro),
`litellm/config.yaml -> /litellm-config.yaml` (ro); no `GPU_MEM_PATH` or
`LLAMA_PROPS_PATH` in its environment. `/app/static/resources.html` and
`services.html` in the container have the tree's SHA-256 (`6fa1ec83…`,
`e7b05aaf…`). `/dev/api/servers`: both sources fresh, three servers
`running`, `answering`. Production containers' creation times are the
ones recorded before (14:02:20 to 14:02:21 for the `kronk` project).

The browser: started and ended by the render script on each of thirteen
runs (four of them failed at the first page with WebDriver's HTTP 500
because my proxy had not started: a quoting error in its script, fixed,
then rerun); the profile `~/snap/firefox/common/m25-fix1` was created and removed each
time. After the last, `pgrep` finds no `firefox` or `geckodriver`, and the
proxy is stopped. `~/snap/firefox/common` holds `.cache` (14:28 today) and
`.mozilla` (April), both older than this round; not mine, left.

## Suite

Before: `7487 passed, 3 skipped, 54 xfailed`. After
(`./scripts/run_tests.sh -q -p no:cacheprovider`):

```
7501 passed, 3 skipped, 54 xfailed, 6 warnings in 103.13s (0:01:43)
```

14 new: one in `test_servers_gpu_mem.py`, 13 in `test_m25_fix1_pages.py`.

## Not done

- No JavaScript runtime added; N10 open (above).
- N11 untouched: in the window between the two timers a server can be
  `not_running` with `health: answering`. The S4 test pins the facts of
  that server, not its health; `/services` shows it "not running" with a
  green dot.
- The probe dots on `/dev/services` (orchestrator, tools, …) were refused
  by the proxy and are not judged. S1 is accepted as it is.
- A not-running server's dot on `/services` stays red (it is not
  answering because it is not running); the ruling did not ask for a
  change there.
- The bar's arithmetic, colours (N3) and every other left-over note.
