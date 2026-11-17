# M21 step 1 end-to-end retest after fix round 1

**M21 STEP 1 FAILS.**

End-to-end test agent (Claude Fable 5.2), 2026-11-17 05:29Z to 06:10Z,
branch `refactor` at `708d086`. Criteria: `docs/sprint/M21-step1-criteria.md`.
`docs/sprint/M21-step1-build.md` (with its addendum) and
`docs/sprint/M21-step1-fix1.md` were read as claims to check. The commit
before the build is `ff99263`. I tested; I changed no repo code, test,
corpus or config file.

Why it fails: criteria 9 and 10, on two inputs I found while trying to
disprove the fix's claim that no file under `config/` can take a loader
down (finding 1). A file under `config/` whose *name* is not valid UTF-8,
or a duplicate YAML key written with a surrogate escape (`"\uD800"`), makes
`POST /config/reload` answer `500 Internal Server Error` and makes the
`/services` data (`/api/config`) answer 500 too, on reload and after a
restart, for as long as the file is there. Confirmed live on dev. The
loader itself does not raise: the error is built correctly, with file and
line, and is in the log; the HTTP answer cannot be encoded as JSON. This is
much narrower than the first test's failure: the service starts, stays up,
runs on the right values (defaults at startup, last good values after a
reload) and keeps answering requests. But the reload answers with neither
"what changed" nor "the error", and the error is not on `/services`, which
is what criteria 9 and 10 ask for and what the first test failed on.
Criterion 1 is also still incomplete, by two rows (finding 2).

What the fix round did fix, and it holds: first-test findings 1, 2 and 3
are closed, at startup and on reload, in scratch and live on dev. Deep
nesting, the alias bomb and the 50 MB file are each rejected in under
0.2 s with a config error on `/services`, the dev containers start and stay
healthy, and a request sent during the reload is not delayed. I could not
make either loader raise, hang or use memory: 408 hostile files (each at
startup and on reload), 24 swaps of the file between the loader's check
and its read, about 101,000 reloads against a file being rewritten,
22,000 fuzzed files and 40,000 differential inputs gave no exception out
of a loader, a slowest load of 0.27 s and a largest memory peak of 12 MB.
The move still changes no behaviour: 54,133 compared values per run are
identical between `ff99263` and `708d086`, apart from the known int/float
type of the tool timeout. Criterion 18 passes live. The replay matches the
baseline.

For the coordinator: the fix is small. Every string that reaches a
`Problem` from outside the code (a directory entry's name, a YAML key)
has to be made encodable before it goes into the answer, or the two
handlers and `/api/config` have to encode with a replacement policy; plus
a test for each of the four inputs in finding 1 that asserts the reload
answers 200 or 422 and `/api/config` answers 200. Two inventory rows close
criterion 1.

## 1. Criteria

| # | Result | Evidence |
|---|---|---|
| 1 | **fail** (incomplete, two classes) | Every value first-test finding 5 named is now in the addendum, each with an outcome. By a wider search two classes still have no row: the tool-result sentences the model reads back (`orchestrator/tools.py` handlers, `tool_service` result and failure sentences), and the wiring and on/off env vars in `docker-compose.yml`. Finding 2 |
| 2 | pass | `git diff ff99263 HEAD -- orchestrator/routing.py litellm/ systemd/ .env.example` is empty. The `ctx_k` note is in the report and `litellm/config.yaml` is unchanged |
| 3 | pass | `config/prompts/` has exactly seven files, equal to the names in `AGENTS` plus `coordinator` (compared in the test venv); `config/tuning.yaml`, `config/music.yaml`. No stub files |
| 4 | pass | Own check: 20 keys in `tuning.yaml` and 14 in `music.yaml`, each with a comment above it. Every prompt file starts with `<!--` and names its agent. No rendered prompt contains `<!--` or `{{` |
| 5 | pass | Own scan, section 6: no secret name, no secret value, no token shape, no URL, address or e-mail in the nine files |
| 6 | pass | `./config:/config:ro` on `tool_service`, `orchestrator`, `tool_service-dev`, `orchestrator-dev` in `docker-compose.yml`. `docker inspect`: both dev containers mount the working tree's `config/` read-only. Production runs release `sprint-M2`, which has no `/config` mount yet |
| 7 | pass | Unknown key, wrong type, out of range, duplicate key, tabs, missing prompt, empty prompt, header-only prompt, non-UTF-8 bytes: each rejected with file and, for YAML, line. Scratch (section 3) and live on dev (section 4b). A few YAML errors carry no line (observation 5) |
| 8 | pass | An invalid file never took effect in any case I ran. At startup the service started on the built-in defaults in every case, including first-test findings 1 to 3 live on dev; after a bad reload the last good values stayed, checked from a state that differs from the defaults (section 4a, and every scratch case). The "reports the error" half is in the log in every case; where it does not reach `/services` is criterion 10 |
| 9 | **fail** | The endpoint exists on both services, reloads from disk, is reachable from the host and from the compose network, and every attempt through nginx's `/dev/` prefix was refused. For the inputs of finding 1 it answers `500 Internal Server Error`, with neither what changed nor the error. The nginx deny rule is not loaded in the running nginx, so it is untested live; in a throwaway container it answers 404 itself (section 5) |
| 10 | **fail** | For every ordinary bad file, and for first-test findings 1 to 3, the error is on `/dev/api/config` with file and line and in the log. For the inputs of finding 1 `/dev/api/config` answers 500 (orchestrator) or reports only "tool_service config status unavailable" (tool_service), on reload and after a restart; the `/services` page then shows "could not load api/config" |
| 11 | pass | Only `WEATHER_CTX_MAX_AGE_S` has two sources. With the env var at 600, old tree and new tree inject the forecast for exactly the same ages, from the files and from the defaults (section 2). Mutant `o10` (env override ignored) is killed |
| 12 | pass | No moved constant name is left in `orchestrator/`, `tool_service/`, `scripts/`, `systemd/`, compose, `voice_tracer/` or `hottub_monitor/`, outside one comment and the env override table. Prompt text exists only in `config/prompts/`, `prompt_defaults.py` and the pin fixture; `prompt_defaults` is read only by `config_loader`. `scripts/model_bench.py` has its own older prompt, unchanged by the build (`git diff ff99263 HEAD` on it is empty) |
| 13 | pass | The builder's pins pass (in the 2934). Independently, with a new script of my own: 13,440 single-round variants, 168 multi-round transcripts and 224 pipeline runs, identical old against new. The pins were written from `ff99263`: regenerated there, byte-identical. Section 2 |
| 14 | pass | Menus identical. Values identical in effect; the one type difference changes nothing (observation 1) |
| 15 | pass | 17 build commits, home prompt first, one piece each (first test ran the suite at each). The two fix commits: suite `2934 passed, 2 skipped, 18 xfailed` and pins green at `13128e9` and at `b4bc98f`, in scratch extracts |
| 16 | pass | `2934 passed, 2 skipped, 18 xfailed`, the count the fix report claims. The four M21 test files make no network attempt. The pre-existing name lookups of first-test finding 6 are still there and are recorded, by the coordinator's ruling (section 7) |
| 17 | pass | One complete run: 113 of the 114 baseline rows have the baseline's status; the one that differs is a known flapper. Section 7 |
| 18 | pass | Live on dev, section 4a. `config/` byte-identical at the end, `git status` clean for `config/` |
| 19 | pass | `README.md` ("Prompts and tuning"), `docs/features/config-directory.md` (schema, bounds, precedence, reload, failure behaviour), `CLAUDE.md` item 3 and tenet 8 (two edits, nothing else in the diff). Two sentences in the feature doc and in `config_files.py` are not true as written (finding 1 and observation 3), and a reload during a running request is still not described (recorded for step 2) |
| 20 | pass | Production containers kept their start times (orchestrator, tool_service, nginx, LiteLLM started 03:30Z, `restarts=0`); HA up 30 h, Music Assistant 12 days; no llama unit, LiteLLM, HA, MA or nginx restart by me. The replay reported "players unchanged (17 checked)" |

Coordinator rulings applied: first-test finding 4 (a reload during a
running request mixes old and new values) is not counted; I re-measured it
and it is unchanged (section 5). An unknown `prompts/*.md` being an error
is accepted. First-test finding 6 is recorded, not counted.

## 2. No behaviour change (criteria 13, 14), from `ff99263`

The loader was rewritten in the fix round, so this was done again from
nothing, with a script written for this retest (not the builder's test,
not its fixtures, not the first tester's script, which was not kept).

Method. `git archive ff99263` and `git archive HEAD` into two scratch
directories. The script runs inside one tree with `llm.stream` replaced by
a recorder, `httpx.AsyncClient` replaced in `agents` and `tools`, the
clock frozen, and every INET socket call and name lookup refused. It
captures:

- **single round**: `agents.run_stream` for all seven agents × error style
  (debug, friendly, derived) × voice × origin stamp × escalation × five
  `system_extra` blocks × weather context × embedded context ×
  `history_messages` × `delegated`: 13,440 runs, each the sha256 of the
  complete message list, tool menu and yielded events, plus the full text
  of two variants per agent;
- **loops**: 168 multi-round transcripts in full (seven agents × eight
  scripts × three style/voice settings): budget exhausted on distinct calls
  and on one repeated call (so the round budget, the duplicate-call path
  and the repeat-call nudge are exercised: 4 LLM calls for the default
  agents, 9 for research, 31 for the coordinator, in both trees), a
  rejected tool, two calls in one round, an empty reply, an LLM error in
  round 1 and in round 2, an escalation, and the coordinator delegating;
  each with the `timeout=` handed to httpx for every tool call;
- **pipeline**: `main._run_pipeline` for all seven routes × (ok, LLM error,
  empty reply, escalate) × voice × origin × local context: 224 runs;
- **menus**: `tool_defs()` of every agent, `agent_tool_defs()`,
  `_coordinator_tool_defs()`, `TOOL_DEFINITIONS`, `ESCALATE_TOOL`,
  `ESCALATION_GUIDANCE`, `roster()`, and each agent's model, budget,
  terminal tools, tool names, description, routing hint and system prompt;
- **values as used**: the timeout `tools.execute` hands to httpx for all 22
  tools; whether `weather_context()` injects the forecast at fifteen ages
  from 0 to 10,000,000 s; the verify window and the sentence that names
  it; `music_resolve.score()` over 6,912 query/candidate pairs;
  `music_resolve.rank()` and `_resolve_named_inner()` (the play threshold)
  for twelve queries over 48 candidates; the "closest I found" sentence at
  ten scores around the hint threshold; the resolver constants.

Result: 21,650 captured items, 54,133 compared leaves per run.

| New tree run with | Config source reported | Leaves that differ from `ff99263` |
|---|---|---|
| `config/` as committed | orchestrator files, tool_service files | 142, all the type of the tool timeout (`15` → `15.0`: 22 in the values, 120 where a loop transcript prints it); 0 others |
| `config/` absent | both defaults, 8 + 1 "file is missing" errors | 0 |
| each of the seven prompt files emptied in turn | orchestrator defaults, tool_service files | 0 (seven runs) |
| `tuning.yaml` with an unknown key | orchestrator defaults, tool_service files | 0 |
| `music.yaml` with an unknown key | orchestrator files, tool_service defaults | 142, the same type-only leaves |
| `WEATHER_CTX_MAX_AGE_S=600`, files (against the old tree with the same env) | files | 142, the same |
| `WEATHER_CTX_MAX_AGE_S=600`, `config/` absent (same) | defaults | 142, the same |

With the env var at 600 the old tree itself changes in exactly five
leaves against its own run without it (the forecast is no longer injected
at 3599, 3600, 7199, 7200 s and at 601 s; still injected at 600), and the
new tree changes in the same five.

I also read the code diff outside the new modules
(`git diff ff99263 HEAD -- orchestrator/tools.py orchestrator/main.py
orchestrator/agents.py tool_service/main.py tool_service/music_resolve.py`):
every use of a moved constant is replaced one for one by a read of the
config value, and nothing else in those files changed except the two
endpoints, the startup log and the registry hydration.

**The pin fixtures are genuine.** `git diff ff99263 4f05264` touches only
five files under `tests/`. `git diff 4f05264 HEAD -- tests/fixtures/m21`
is empty. I extracted `4f05264` (no `config/` directory in it), moved the
three fixture files away, ran `tests/test_m21_pins.py --write` there
(`3390 variants, 14 menus`), and compared with the files committed at
HEAD: `prompts.json`, `menus.json` and `values.json` are byte-identical
(`cmp`).

## 3. Loading and validation in scratch (criteria 7, 8, 10; extra scope b)

### 3a. The battery: 408 files, each at startup and on reload

Every case is a scratch copy of the committed `config/` with one thing
wrong, run twice in a fresh interpreter under a 4 GB address-space limit
and a 120 s timeout: once as the config directory at startup (the
orchestrator's import, `main._log_config_status()`, tool_service's
`start()`), once swapped in after a good start on a config that **differs
from the defaults** (research budget 7, `default_s` 16, verify window 9, a
marker word in `home.md`) and reloaded through the real `POST
/config/reload` routes of both services (FastAPI test client). Recorded per
case and per service: any exception, the answer and its size, the load
time, the peak resident memory above the level before the load
(`/proc/self/clear_refs`, `VmHWM`), the values in effect before and after,
the status, and whether `GET /api/config` / `GET /config/status` still
answer.

163 cases for `tuning.yaml`, the same 163 for `music.yaml`, 72 for prompts
and stray files, 10 for the config directory itself. 1,632 loads measured.

| | Result |
|---|---|
| Exceptions out of a loader, an import, `start()` or the startup log | 0 |
| Interpreters that hung or hit the memory limit | 0 |
| Slowest load | 0.27 s (`music.yaml`, a flow list of 31,000 items; 0.23 s for `tuning.yaml`). Median 0.002 s |
| Largest memory peak above the level before the load | 11.7 MB (`tuning.yaml`, 9,000 keys with null values) |
| Longest single error message | 158 characters |
| Largest reload answer | 5,178 bytes (13,000 unknown placeholders: 50 problems and "and N more") |
| Reload answers | 200 or 422 in every case but five (finding 1) |
| After a 422: values in effect equal the ones before, status `ok=False` | every case |
| At startup with a rejected file: source `defaults` | every case |
| The other service | unaffected in every case but one: `music.yaml` replaced by a symlink also leaves a stray `music.yaml.real` in my test, which the orchestrator reports as a warning (my set-up, correct behaviour) |

What was fed, by kind (✓ = rejected with a config error naming the file,
last good values kept, defaults at startup):

- **first-test findings**: 20,000, 500 and 300 nested brackets, 11 levels
  (✓ `nested more than 10 levels deep`, line 1; 9 levels is an ordinary
  schema error), 15,000 nested flow mappings, a block mapping 300 deep, 30,000 `- ` on one line;
  the nine-level alias bomb and a four-level one (✓ `anchors (&name) are not
  allowed`, line 1, 0.003 s, 0.0 MB), a self-referencing anchor, an
  undefined alias, a merge key; 50 MB of comment lines, a 20 MB string, a
  60 MB sparse file, a 30 MB prompt (✓ `too large: N bytes; the limit is
  65536`, 0.006 s); exactly 65,536 bytes loads, 65,537 is refused;
- **ordinary** (brief, scope 3): unknown key, wrong type, out of range,
  duplicate key, tabs, non-UTF-8 bytes, a scalar, list or word document,
  two documents (✓ with line); empty, whitespace, comments only, `~`,
  `---`, `null`, `{}` (accepted with the warning `all defaults (file has no
  keys)`);
- **parser edge cases**: `%YAML 9.9`, 1,500 `%TAG` directives, 16,000
  `---` and 16,000 `...`, tags (`!!python/object/apply:os.system`,
  `!!binary`, `!`, a verbatim tag, a 60,000-character tag: ✓, nothing
  ran), every escape form in double quotes, 47 kinds of 64 KiB "soup" (one
  character or pair repeated: brackets, quotes, `&`, `*`, `!`, `?`, `%`,
  `|`, `>`, tabs, carriage returns, `a:` repeated, deep indentation…: 43
  rejected, four accepted as files with no keys: spaces, newlines, `#`,
  carriage returns);
- **encodings**: a UTF-8 BOM at the start of a YAML file (tolerated: the
  file's real error is still reported, at the right line; in a prompt it is
  ✓ `must start with a <!-- header comment -->`) and in the middle (✓
  unknown key), UTF-16 LE and BE with
  BOM, UTF-32, a Latin-1 byte, overlong UTF-8, surrogate bytes (✓ `cannot
  be read: 'utf-8' codec can't decode…`), NUL, BEL, DEL and C1 characters
  (✓, no line), NEL and U+2028 inside a comment (✓, see observation 3),
  CR-only and CRLF line endings (read as LF; the error line is right);
- **long lines and keys**: a 60,000-character comment line (accepted), a
  60,000-character value (✓, quoted to 80 characters), 60,000-character
  keys in four positions (✓ `mapping values are not allowed here`: YAML's
  own 1,024-character key limit), 7,000 keys at the top level, 6,000 in a
  known section, 6,500 in one flow mapping, 9,000 with null values (✓ 50
  problems and one "and N more problems, not shown"; 0.10 to 0.16 s);
- **numbers**: `2:00:00`, `0o17`, `0x10`, `1_000`, `.inf`, `.nan`, `yes`,
  `true`, a quoted number, `1e999` and `1e99999999999999` (✓ with line);
  `+3`, `1e0`, `8.` and a float of 60,000 digits are valid numbers and
  load; a 5,000-digit integer is rejected without a line (observation 5);
- **filesystem**: a FIFO, a directory, a socket, a symlink outside, inside,
  dangling and to itself (✓ `is a symlink…` / `is not a regular file…`),
  mode 000 (✓ `cannot be read: Permission denied`), a hard link (accepted:
  it is a regular file), missing;
- **prompts**: missing, empty, header only, header and whitespace, header
  and a second comment only, 16,000 unclosed `<!--`, no header, unclosed
  header, BOM, UTF-16, non-UTF-8, NUL, ESC, DEL, NEL, form feed (✓ with
  line where there is one), unknown placeholder (✓ line 24), 13,000 unknown
  placeholders (✓ capped, 0.10 s), `prompts/` missing, a file, a FIFO, a
  symlink, mode 000; an unknown `prompts/*.md` in six spellings including a
  FIFO and a directory named `x.md` (✓ error, as ruled); 600 unknown
  `.md` files (✓ 20 errors); stray files and directories, 600 at the top
  and 3,000 under `prompts/` (accepted, 20 warnings and one "more entries
  not checked");
- **the config directory itself**: missing, an empty directory, a file, a
  FIFO, a socket, a dangling symlink, a symlink loop, mode 000 (✓ both
  services on defaults at startup, 422 on reload, nothing blocks); a
  symlink to a good directory loads, by design.

Accepted and worth knowing (not errors): observation 6.

### 3b. A file that changes during the read

**Swap after the check (24 cases).** `os.lstat` inside `config_files` was
wrapped so that, right after the loader's check of the file passed, the
file was replaced before the loader opened it. `tuning.yaml` (orchestrator),
`music.yaml` (tool_service) and `prompts/home.md`, each swapped for: a FIFO
with no writer, a FIFO with a writer pouring data, a symlink, a directory,
a 60 MB file, a 63 MB sparse file, nothing, non-UTF-8 content. Every swap
was refused as a config error in at most 0.027 s (`is not a regular
file…`, `cannot be read: Too many levels of symbolic links`, `too large:
more than 65536 bytes`, `cannot be read: No such file or directory`); the
loader read at most 65,537 bytes. (The 59 MB peak my harness shows for the
"60 MB file" swap is my own swap function building the 60 MB in the same
process; the sparse case shows 0.0 MB.)

**A writer racing the reload (12 runs of 6 s, 101,340 reloads).** One
thread rewrote the file while the main thread reloaded in a loop:

| Writer | Reloads (tuning / music) | Raised | Outcomes |
|---|---|---|---|
| in-place rewrite between two valid files | 4,540 / 14,698 | 0 | the two values, or **"ok, all defaults (file has no keys)"** when the read caught the file just after the truncate (observation 2) |
| in-place rewrite in three chunks | 4,216 / 10,113 | 0 | the two values, all defaults, or a clean 422 on a half-written file |
| atomic rename between two valid files | 3,211 / 6,403 | 0 | only the two values (and a stray warning for my `.tmp` file) |
| append without end, past the cap | 1,840 / 4,217 | 0 | ok until 65,536 bytes, then `too large` |
| flip between a valid file and a FIFO | 4,905 / 19,645 | 0 | the value, `is not a regular file`, `file is missing` |
| flip between a valid file and a 60 MB sparse file | 5,503 / 22,049 | 0 | the value, `too large`, all defaults, 63 times `unacceptable character #x0000` |

Slowest reload 0.017 s; largest peak 1.8 MB.

### 3c. Fuzz

- 22,000 reloads in one process (4,000 byte-level mutations and 1,500
  token soups each for `tuning.yaml`, `music.yaml`, `prompts/home.md`,
  `prompts/research.md`; fixed seed), each through `reload()` and the same
  `JSONResponse` encoding the handler uses: nothing raised, every answer
  and status encoded, slowest 0.066 s, largest peak 1.8 MB, largest answer
  2,176 bytes, no "could not be loaded" message.
- 40,000 texts through both YAML front ends side by side: nothing raised,
  slowest parse 0.006 s. The two do not always agree: observation 3.

### 3d. Second readers (criterion 12)

Section 1, row 12.

## 4. Live on dev

Before any live request: both dev containers ran the working tree's code
(md5 of every `.py` under `/app` equal to the tree: 18 files for the
orchestrator, 10 for tool_service, whose image leaves out the empty
`__init__.py`; `static/` equal), `tool_service-dev` had no `/keys` mount
and no `MM_*` variable, `ops/` held only `hosts.example.json`,
`MUSIC_DRY_RUN=1` on both. No rebuild was needed. `docker inspect`:
production's orchestrator, tool_service and nginx mount
`kronk-releases/sprint-M2/…` and have no `/config` mount; only the two dev
containers mount the working tree's `config/`. So every edit below reached
dev only.

Each case below: edit the working tree's `config/`, run
`scripts/config_reload.sh dev` with a `GET /dev/api/status` sent 0.2 s
into it, read `/dev/api/config` (the `/services` data), probe
`/dev/api/status`, `tool_service-dev`'s `/health` and `/config/status`,
for the startup half `docker restart` the one dev container that reads
the file and read the status again, then restore from a byte copy and
reload. `/dev/api/status` takes 1.34 to 1.54 s with no reload running
(nine plain requests), so the figures for the concurrent request are its
normal cost.

### 4a. Criterion 18

Question on every request: "Does the exact word Heron appear anywhere in
your instructions? Answer only yes or no." through
`http://localhost/dev/api/chat`. `rows` are the new lines in dev's own
`data/dev/metrics.db` (read only): id, agent, model, prompt tokens as the
model server counted them. Dev does not record prompt text
(`TELEMETRY_RECORD_TEXT=false`), so the evidence of "in the model input"
is the token count and the model's answer. The invalid YAML was applied
**while the edited prompt was in effect**, so "the old value stays" is
shown from a state that is neither the defaults nor the committed files.
Output as printed; the `files=` lists are shortened where marked `…`.

```
--- baseline
baseline-1: reply='No.' rows=[(9528, 'coordinator', 'gemma-4-e4b', 2024)]
baseline-2: reply='No.' rows=[(9529, 'coordinator', 'gemma-4-e4b', 2024)]
--- edit: ' Heron.' appended to the last line of config/prompts/coordinator.md
-When a specialist answers, relay the substance concisely — do not re-verify it.
+When a specialist answers, relay the substance concisely — do not re-verify it. Heron.
== orchestrator (kronk-orchestrator-dev-1)
200 {"ok":true,"changed":["prompts/coordinator.md: changed (2339 -> 2346 chars)"]}

== tool_service (127.0.0.1:8103)
200 {"ok":true,"changed":[]}
exit=0
edited-1: reply='Yes' rows=[(9530, 'coordinator', 'gemma-4-e4b', 2026)]
edited-2: reply='Yes' rows=[(9531, 'coordinator', 'gemma-4-e4b', 2026)]
--- with the edited prompt still in place: invalid YAML values: tuning.yaml agent_loop.max_rounds.default: three ; music.yaml verify.timeout_s: 800
== orchestrator (kronk-orchestrator-dev-1)
422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":17,"message":"agent_loop.max_rounds.default: Input should be a valid integer (got 'three')"}],"kept":"last good values (files, loaded 2026-11-17 05:51:57)"}

== tool_service (127.0.0.1:8103)
422 {"ok":false,"errors":[{"file":"config/music.yaml","line":19,"message":"verify.timeout_s: Input should be less than or equal to 60 (got 800)"}],"kept":"last good values (files, loaded 2026-11-17 05:51:57)"}
exit=1
--- dev /services data (http://localhost/dev/api/config)
orchestrator ok= False event= reload source= files kept= last good values (files, loaded 2026-11-17 05:51:57) files= {… 'prompts/coordinator.md': '2a9f56617ece', … 'tuning.yaml': '6012df576095'}
     {'file': 'config/tuning.yaml', 'line': 17, 'message': "agent_loop.max_rounds.default: Input should be a valid integer (got 'three')"}
tool_service ok= False event= reload source= files kept= last good values (files, loaded 2026-11-17 05:51:57) files= {'music.yaml': 'c491a4b42753'}
     {'file': 'config/music.yaml', 'line': 19, 'message': 'verify.timeout_s: Input should be less than or equal to 60 (got 800)'}
--- dev /services page
GET /dev/services -> 200 | has the Config section: True | fetches api/config: True
--- last good values still in effect (the edited prompt, not the defaults and not the committed file)
after-bad-reload-1: reply='Yes.' rows=[(9532, 'coordinator', 'gemma-4-e4b', 2026)]
after-bad-reload-2: reply='Yes' rows=[(9533, 'coordinator', 'gemma-4-e4b', 2026)]
tool_service-dev /health -> 200 {"status":"ok","music_dry_run":true}
--- dev logs
2026-11-17 05:52:05,079 ERROR config_loader: config on reload rejected: config/tuning.yaml:17: agent_loop.max_rounds.default: Input should be a valid integer (got 'three') (keeping last good values (files, loaded 2026-11-17 05:51:57))
ERROR:tool_service.config:config on reload rejected: config/music.yaml:19: verify.timeout_s: Input should be less than or equal to 60 (got 800) (keeping last good values (files, loaded 2026-11-17 05:51:57))
--- revert all three files (byte copies of the committed files) and reload
== orchestrator (kronk-orchestrator-dev-1)
200 {"ok":true,"changed":["prompts/coordinator.md: changed (2346 -> 2339 chars)"]}

== tool_service (127.0.0.1:8103)
200 {"ok":true,"changed":[]}
exit=0
reverted-1: reply='No.' rows=[(9534, 'coordinator', 'gemma-4-e4b', 2024)]
reverted-2: reply='No' rows=[(9535, 'coordinator', 'gemma-4-e4b', 2024)]
orchestrator ok= True event= reload source= files kept= None files= {… 'prompts/coordinator.md': '26cdf52adb44', … 'tuning.yaml': '6012df576095'}
tool_service ok= True event= reload source= files kept= None files= {'music.yaml': 'c491a4b42753'}
config/ sha256 identical to before
```

During the failed reload the digest shown for `coordinator.md`
(`2a9f56617ece`) is the edited file and the one for `tuning.yaml`
(`6012df576095`) is the committed one: the last good load, unchanged.

### 4b. First-test findings 1, 2 and 3 again, on reload and at startup

| Input | Reload answer (time of the whole script) | Concurrent `GET /dev/api/status` | On `/dev/api/config` | After `docker restart` of the dev container that reads it |
|---|---|---|---|---|
| **1.** `tuning.yaml`: `agent_loop: ` + 20,000 `[` + 20,000 `]` | 422 `config/tuning.yaml:1: nested more than 10 levels deep` (0.19 s) | 200 in 1.48 s | orchestrator `ok=False source=files`, the error, `kept=last good values…` | healthy in 7 s, `restarts=0`; `ok=False source=defaults event=startup`, the same error; probes 200 |
| **1.** `music.yaml`: `verify: ` + the same brackets | 422 `config/music.yaml:1: nested more than 10 levels deep` (0.18 s) | 200 in 1.46 s | tool_service the same | healthy in 7 s; `source=defaults`, the error |
| **1.** `tuning.yaml` nested 500 (the old PyYAML threshold) | 422, the same message (0.11 s) | 200 in 1.55 s | the error | not restarted |
| **1.** `music.yaml` nested 300 (the old ruamel threshold) | 422, the same message (0.09 s) | 200 in 1.45 s | the error | not restarted |
| **2.** `tuning.yaml`: the nine-level alias bomb of the first report | 422 `config/tuning.yaml:1: anchors (&name) are not allowed in config files` (0.07 s) | 200 in 1.51 s | the error | healthy in 7 s; `source=defaults`, the error |
| **2.** `music.yaml`: the same, `verify: *i` | 422 `config/music.yaml:1: anchors (&name)…` (0.08 s) | 200 in 1.43 s | the error | healthy in 7 s; `source=defaults`, the error |
| **3.** `tuning.yaml`: 50 MB of `# pad` lines, then `default_s: 601` | 422 `config/tuning.yaml: too large: 52431615 bytes; the limit is 65536` (0.08 s; 27.83 s in the first test) | 200 in 1.45 s (28.3 s in the first test) | the error | healthy in 6 s; `source=defaults`, the error |
| **3.** `music.yaml`: the same with `timeout_s: 800` | 422 `…too large: 52431347 bytes…` (0.08 s) | 200 in 1.41 s | the error | healthy in 7 s; `source=defaults`, the error |
| **3.** `prompts/home.md`: 30 MB | 422 `config/prompts/home.md: too large: 31461439 bytes; the limit is 65536` (0.08 s) | 200 in 1.49 s | the error | healthy in 7 s; `source=defaults`, the error |
| `tuning.yaml` of 65,537 bytes | 422 `too large: 65537 bytes; the limit is 65536` | 200 in 1.51 s | the error | not restarted |
| `tuning.yaml` of 65,536 bytes, valid | 200 `{"ok":true,"changed":[]}` | 200 in 1.51 s | `ok=True` | not restarted |

The alias bomb was run on dev this time: in scratch it is refused at its
first anchor with no measurable memory, so it could not harm the host.
Both dev probes answered 200 after every step. Log lines, one case:

```
2026-11-17 05:46:08,590 ERROR config_loader: config on reload rejected: config/tuning.yaml:1: nested more than 10 levels deep (keeping last good values (files, loaded 2026-11-17 05:46:03))
2026-11-17 05:46:13,760 ERROR main: config at startup rejected: config/tuning.yaml:1: nested more than 10 levels deep (running on built-in defaults)
ERROR:tool_service.config:config at startup rejected: config/music.yaml:1: anchors (&name) are not allowed in config files (running on built-in defaults)
2026-11-17 05:47:12,319 ERROR main: config at startup rejected: config/tuning.yaml: too large: 52431615 bytes; the limit is 65536 (running on built-in defaults)
```

After each restore and reload both services were back to `ok=True
source=files` without a second restart.

### 4c. Ordinary bad files on dev (brief, scope 3)

Both probes answered 200 after every case; the concurrent status request
took 1.37 to 1.53 s in every case.

| Input | Reload answer | On `/dev/api/config` |
|---|---|---|
| `tuning.yaml`: `bogus_key: 1` under `agent_loop` | 422 `config/tuning.yaml:25: agent_loop.bogus_key: unknown key` | same error, `ok=False`; after a restart `source=defaults`, same error |
| `tuning.yaml`: `default: '3'` | 422 `…:17: agent_loop.max_rounds.default: Input should be a valid integer (got '3')` | same |
| `tuning.yaml`: `default_s: 601` | 422 `…:38: tool_timeouts.default_s: Input should be less than or equal to 600 (got 601)` | same |
| `tuning.yaml`: second `weather_context:` block | 422 `…:64: duplicate key weather_context` | same |
| `tuning.yaml`: a tab before `repeat_tool_nudge_at` | 422 `…:24: not valid YAML: found character '\t' that cannot start any token` | same |
| `tuning.yaml`: 0 bytes | 200 `{"ok":true,"changed":[],"warnings":[{"file":"config/tuning.yaml","line":null,"message":"all defaults (file has no keys)"}]}` | `ok=True` with the warning |
| `tuning.yaml`: bytes `ff fe` in a comment | 422 `config/tuning.yaml: cannot be read: 'utf-8' codec can't decode byte 0xff in position 2: invalid start byte` | same |
| `tuning.yaml`: `max_age_s: 2:00:00` (accepted as 7200 in the first test) | 422 `…:32: weather_context.max_age_s: Input should be a valid integer (got '2:00:00')` | same |
| `tuning.yaml`: symlink to a scratch file outside `config/` | 422 `config/tuning.yaml: is a symlink; config files must be regular files inside the config directory` | same |
| `prompts/talkie.md` removed | 422 `config/prompts/talkie.md: file is missing` | same |
| `prompts/home.md`: 0 bytes | 422 `config/prompts/home.md:1: must start with a <!-- header comment -->` | same |
| `prompts/home.md`: only the header comment | 422 `config/prompts/home.md:1: prompt is empty` | same |
| `prompts/home.md`: header, then only a second comment | 422 `…:1: prompt is empty (only comments after the header)` | same |
| `prompts/home.md`: byte `e9` in the body | 422 `config/prompts/home.md: cannot be read: 'utf-8' codec can't decode byte 0xe9 in position 4162…` | same |
| `prompts/home.md`: a NUL byte in the body | 422 `…:24: control character U+0000 is not allowed in a prompt (only newline and tab)` | same |
| `prompts/home.md`: symlink to a scratch file outside `config/` | 422 `config/prompts/home.md: is a symlink; config files must be regular files…` | same |
| `prompts/extra.md` added | 422 `config/prompts/extra.md: unknown prompt file: no agent of that name (known: …)` | same |
| stray `config/tunning.yaml` and `config/prompts/home.md.bak` | 200 with two warnings `not a file any service reads (a typo, or a leftover copy?); ignored` | `ok=True` with both warnings |
| `music.yaml`: `bogus: 1` under `resolver` | 422 `config/music.yaml:58: resolver.bogus: unknown key` | same; after a restart `source=defaults`, same error |
| `music.yaml`: `timeout_s: '8'` | 422 `…:19: verify.timeout_s: Input should be a number (got '8')` | same |
| `music.yaml`: `play_min_score: 1.5` | 422 `…:31: resolver.play_min_score: Input should be less than or equal to 1 (got 1.5)` | same |
| `music.yaml`: `timeout_s` twice | 422 `…:20: duplicate key verify.timeout_s` | same |
| `music.yaml`: a tab before `timeout_s` | 422 `…:19: not valid YAML: found character '\t' that cannot start any token` | same |
| `music.yaml`: 0 bytes | 200 with the warning `all defaults (file has no keys)` | `ok=True` with the warning |
| `music.yaml`: bytes `ff fe` | 422 `config/music.yaml: cannot be read: 'utf-8' codec can't decode byte 0xff…` | same |
| `music.yaml`: symlink to a scratch file outside `config/` | 422 `config/music.yaml: is a symlink; config files must be regular files…` | same |

In each rejected case the file digests on `/dev/api/config` stayed those
of the committed files (`tuning.yaml=6012df576095`,
`music.yaml=c491a4b42753`, `home.md=d18e56f4a821`).

## 5. Reload (criterion 9)

```
== host -> both, via scripts/config_reload.sh dev
== orchestrator (kronk-orchestrator-dev-1)
200 {"ok":true,"changed":[]}

== tool_service (127.0.0.1:8103)
200 {"ok":true,"changed":[]}
exit=0
== compose network: from tool_service-dev
http://orchestrator-dev:8100/config/reload 200 {"ok":true,"changed":[]}
http://tool_service-dev:8103/config/reload 200 {"ok":true,"changed":[]}
== compose network: from orchestrator-dev
http://tool_service-dev:8103/config/reload 200 {"ok":true,"changed":[]}
http://orchestrator-dev:8100/config/reload 200 {"ok":true,"changed":[]}
== POST through nginx, dev prefix only (curl --path-as-is); production root path not requested
/dev/config/reload                 403 {"detail":"/config/reload is not served through nginx; call it from the host (scripts/config_reload.sh) or the compose network"}
/dev//config/reload                403      /dev/./config/reload          403
/dev/x/../config/reload            403      /dev/%63onfig/reload          403
/dev/config%2Freload               403      /dev/config/reload?x=1        403
/dev/static/../config/reload       403      /dev/voice/../config/reload   403
/dev/config/./reload               403      /dev/api/../config/reload     403
/dev/config/reload/                307      /dev/CONFIG/reload            404
/dev/voice/config/reload           404      /dev/config/reload;x          404
== GET /dev/config/status through nginx (tool_service's route does not exist on the orchestrator)
404
== straight at the handler, inside orchestrator-dev (header checks)
X-Real-IP: 192.0.2.9 -> 403        X-Forwarded-For: 192.0.2.9 -> 403
x-real-ip: ::1 (lower case) -> 403 X-Real-IP: (empty) -> 200
Forwarded: for=192.0.2.9 -> 200    no header -> 200
GET -> 405   PUT -> 405   DELETE -> 405   PATCH -> 405
routes containing config: [('/api/config', ['GET']), ('/config/reload', ['POST'])]
== tool_service-dev routes
routes containing config: [('/config/reload', ['POST']), ('/config/status', ['GET'])]
== published ports
kronk-orchestrator-dev-1:
kronk-tool_service-dev-1: 8103/tcp -> 127.0.0.1:8103
```

(Folded two or more to a row; every 403 carried the same `detail`.)

**nginx rule: not tested live.** nginx was not restarted by the build or
by the fix round; `docker inspect` shows the running nginx (started
03:30Z) mounts `kronk-releases/sprint-M2/nginx/nginx.conf`, which differs
from the tree's by exactly the comment and the two deny lines. As the
brief directs for that case, no request went to production's root path,
and the handler-level refusal was tested on dev (above). `nginx/` is
unchanged since the first test (`git diff bc39131 HEAD -- nginx/` is
empty). In a throwaway `nginx:1.29-alpine` container with `--network none`
and the tree's config (`nginx -t` successful), POST from inside it:

```
/config/reload 404   /config/ 404   /config/x/y 404   /dev/config/reload 404   /dev/config/ 404
/dev//config/reload 404   /dev/x/../config/reload 404   /dev/./config/reload 404
/dev/%63onfig/reload 404  /config%2Freload 404   /dev/config%2Freload 404
/x/../config/reload 404   //config/reload 404
/config 502   /CONFIG/reload 502   /dev/config 502   /api/status 502   (502 = handed to an upstream; none there)
/api/solar/../config/reload 502   upstream: "http://127.0.0.1:8000/api/solar/../config/reload"
/api/solar/..%2Fconfig/reload 502 upstream: "http://127.0.0.1:8000/api/solar/..%2Fconfig/reload"
```

The last two lines settle what the first test left as reasoning: nginx's
own error log names the upstream, and it is the orchestrator's port
(8000), not tool_service's (8003). A path that climbs out of
`/api/solar/` leaves that location, so tool_service's `/config/reload`
cannot be reached through nginx; the orchestrator has no such route and
refuses proxied reloads anyway.

**Concurrent reloads, live on dev.** 80 reloads on each dev service from
eight parallel workers on the compose network, while one chat request ran
and `coordinator.md` was swapped (atomic rename) four times between two
valid texts:

```
chat finished in 2.5s: 878 chars: 'Tea is made by infusing tea leaves with hot water. The process varies slightly depending o'
file flips (atomic rename) during the bursts: 4
tool_service reload answers: 80; codes {200: 80}
    80  []
orchestrator reload answers: 80; codes {200: 80}
    76  []
     2  ["prompts/coordinator.md: changed (2339 -> 2346 chars)"]
     2  ["prompts/coordinator.md: changed (2346 -> 2339 chars)"]
```

Each of the four transitions was reported exactly once. (A first attempt
of mine flipped the file faster than my reload batches sampled it and
proved nothing; this is the rerun.)

**A reload during a running request** (first-test finding 4; recorded for
step 2 by the coordinator's ruling, not counted). Re-measured in scratch
on the rewritten loader; unchanged:

```
reload during round 2: {"ok": true, "changed": ["prompts/research.md: changed (1127 -> 1141 chars)", "tuning.yaml:agent_loop.max_rounds.research: 8 -> 2", "tuning.yaml:agent_loop.repeat_tool_nudge_at: 3 -> 2", "tuning.yaml:tool_timeouts.default_s: 15.0 -> 99.0"]}
  LLM calls made: 9 (old budget 8 -> 9 calls with the forced synthesis; new budget 2 -> 3)
  prompt marker seen in any round: False | every round's prompt still says 'budget of 8': True
  tool timeouts in effect per call: [15.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0]
  repeat-call nudges present by round: [0, 0, 1, 2, 3, 4, 5, 6, 7]
```

The request keeps the old prompt and budget and takes the new nudge
threshold and timeout from its second tool call on. The feature doc still
does not say so.

## 6. Secrets (criterion 5)

My own scan of the nine files in `config/` (no symlinks, no hidden files):

- names: every key in `.env`, `.env.dev`, `.env.telemetry` and the
  `.example` files, plus every `${NAME}` in the compose files (28 names),
  as whole words: no hit;
- values: every value of six or more characters from `.env`, `.env.dev`
  and `.env.telemetry` (19 values), searched as fixed strings without
  printing them: one hit, `TALKIE_MODEL` from `.env.dev`, whose value is
  the word "talkie". Not a secret;
- shapes: JWT, 32+ hex, 40+ base64, `sk-`/`pk-`, AWS and GitHub token
  prefixes, PEM headers, `password|secret|token|api_key|bearer|login|
  username|credential` followed by `:` or `=`: no hit;
- words of 24+ characters: `test_agent_tool_separation`,
  `remember_music_preference`. Identifiers;
- URLs, IPv4 addresses, e-mail addresses: none.

The builder's test would catch a bare paste of the four token-type
secrets in `.env` (checked by shape, values not printed); it has no shape
for a bare password or login, and a planted `ghp_…` string survives it
(mutant `f4`, observation 8).

## 7. Tests, mutation check, replay

```
$ ./scripts/run_tests.sh -q
2934 passed, 2 skipped, 18 xfailed, 6 warnings in 20.85s
```

The count the fix report claims. The two fix commits, each in a scratch
extract with every INET socket call and name lookup refused and logged:

```
13128e9 suite: 2934 passed, 2 skipped, 18 xfailed, 6 warnings in 21.00s
13128e9 pins+hardening: 183 passed, 3 warnings in 3.76s
b4bc98f suite: 2934 passed, 2 skipped, 18 xfailed, 6 warnings in 20.81s
b4bc98f pins+hardening: 183 passed, 3 warnings in 3.78s
```

**Network attempts (first-test finding 6, recorded).** Under the guard
the suite still passes at HEAD, and the guard refused 34 attempts in one
run: 32 `getaddrinfo` (the name `fake-tools` and the news feeds), one
`getaddrinfo('localhost')`, one `bind(('::1', 0))` at import. By test file:
`test_agentic_loop.py` 10, `test_tool_service_weather.py` 7,
`test_error_surfacing.py` 5, `test_retract.py` 5, `test_render.py` 4,
`test_playback_control.py` 1, `test_magicmirror.py` 1. None from
`test_config_loader.py`, `test_tool_service_config.py`,
`test_m21_pins.py` or `test_config_hardening.py`. `./scripts/run_tests.sh`
as the brief had me run it has no guard, so my one plain run made those
lookups for real.

**Mutation check.** 71 single edits in four scratch copies of HEAD, the
whole suite after each (`-x`), under the same guard. An edit to
`config_files.py` was applied to both copies, so the "byte-identical"
test is not what kills it. No mutant opened a socket: 1,089 attempts over
the 71 runs were all refused (918 lookups of `fake-tools`, 86 of five news
feed hosts, 14 of `localhost`, 71 binds of `::1`), none from the four M21
test files. 58 killed, 13 survived:

| Survivor | Edit | What it means |
|---|---|---|
| c9 | `O_NOFOLLOW` dropped from the open | The second line of defence in `read_text` (a file swapped after the `lstat` check) has no test. Every test file that feeds a symlink, a FIFO or a large file is stopped by the first check. My swap-after-check runs (section 3b) exercised it and it works |
| c10 | `O_NONBLOCK` dropped | the same gap: with this edit a FIFO swapped in after the check would block the service |
| c11 | the `lstat` regular-file check removed (the `fstat` check is left) | equivalent in outcome; listed for completeness |
| c13 | the read loop unbounded | the same gap: only the `stat` size check is tested |
| c14 | an over-cap read accepted | the same gap: a file that grows after the check would be accepted |
| c25 | the stray scan's 500-entry bound removed | the bound on directory entries has no test (the 20-warning bound has: `c22` is killed) |
| o9 | `_SECONDS` upper bound 600 → 6000 | no test feeds a timeout between 600 and 6000 (first test's o10) |
| a1 | `weather_context`: `>` → `>=` at the age limit | the boundary age is not tested (first test's a4) |
| m1 | `config_loader.start()` removed from tool_service's lifespan | nothing tests that tool_service loads at startup rather than at first use (first test's m2) |
| m2 | `_not_found_sentence` compares with a literal 0.5 | no test changes `hint_min_score` and looks at the sentence (first test's m4) |
| r1 | `score()` compares coverage with a literal 0.80 | no test changes `coverage_min` and looks at a score (first test's r2) |
| r2 | `score()` adds 0.06 instead of `provider_bonus` | no test pins the effect of the provider bonus on a score |
| f4 | a comment line of `ghp_` followed by 38 letters and digits (a made-up string in the shape of a GitHub token) planted in `tuning.yaml` | the secrets test has no shape for it (observation 8) |

The two survivors the first test called important (a rejected reload
falling back to the defaults, `o4` and `s3` here) are now killed. Killed,
among others: either size cap raised, the nesting limit raised, anchors,
tags, merge keys or duplicate keys accepted, the symlink check removed,
the size check from `stat` removed, quotes not shortened, the problem
list not capped, leading-zero integers or `yes`/`on` accepted, quoted
scalars resolved, control characters allowed in prompts, each of the six
`except Exception` layers narrowed, the no-keys warning dropped in either
service, an unknown `prompts/*.md` downgraded to a warning, unknown keys
ignored or types coerced in either service, schema errors losing their
line, the env override ignored, per-agent budgets or per-tool timeouts
not applied, the placeholder not filled, the reload refusal removed, a
rejected reload answering 200 in either service, either nginx deny line
removed, a mount made read-write, one character changed in a prompt file,
a default prompt or either YAML file, a secret's name planted, the reload
script dropping tool_service's HTTP code.

**Replay.** One run, to the end; no row was skipped for player activity.

```
$ python3 scripts/voice_replay.py --label m21-e2e2
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  …
voice_replay: {'pass': 99, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_053151_m21-e2e2.json
exit=0
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`, row
by row: 113 of the 114 baseline rows have the baseline's status, none was
not run. The one that differs is `inc-0930-search-deep-house-ytm`, fail →
pass, a known "YouTube Music" search flapper (the same change the build's,
the first test's and the fix round's replays showed). The 8 rows added
since M10 (`m4b-*`): 7 pass, 1 skip. The replay ran before any of my
edits to `config/`, on dev as the fix round left it.

## 8. Findings

**1. A name or key that is not valid UTF-8 text turns the reload answer
and the `/services` data into a 500. (Criteria 9 and 10; brief, extra
scope b.)**

Four inputs, each confirmed in scratch at startup and on reload, and live
on dev on reload and after a restart:

| Input | What the loader decides (correctly) | `POST /config/reload` | `/dev/api/config` |
|---|---|---|---|
| a stray file `config/caf\xe9.yaml` (a Latin-1 name; content `x: 1`) | loads, with the warning "not a file any service reads" | orchestrator **500** | **500** `Internal Server Error` |
| an unknown prompt file `config/prompts/caf\xe9.md` | rejected: "unknown prompt file" | orchestrator **500** | **500** |
| `tuning.yaml` + two lines `"\uD800": 1` and `"\uD800": 2` | rejected: `config/tuning.yaml:65: duplicate key \ud800` | orchestrator **500** | **500** |
| `music.yaml` + the same two lines | rejected: `config/music.yaml:59: duplicate key \ud800` | tool_service **500** | 200, but tool_service's entry is only `tool_service config status unavailable: Server error '500 Internal Server Error' for url 'http://tool_service-dev:8103/config/status'`, with `file: null` |

Live on dev, the first input, as printed except for two lines of my own
script that only repeat the 500 (the other three have the same shape):

```
=== NEW: a stray file whose name is not UTF-8: config/caf\xe9.yaml (Latin-1 name)
   reload: exit 1 in 0.10s
      == orchestrator (kronk-orchestrator-dev-1)
      500 Internal Server Error
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
   a GET /dev/api/status sent 0.2 s into the reload: 200 in 1.41s
   /dev/api/config -> 500 (21 bytes, 0.01s)
      body: 'Internal Server Error'
   probes: /dev/api/status 200 (1.40s), tool_service-dev /health 200, tool_service-dev /config/status 200
   -- startup: kronk-orchestrator-dev-1: running healthy restarts=0 after 7s
   /dev/api/config -> 500 (21 bytes, 0.03s)
      body: 'Internal Server Error'
   probes: /dev/api/status 200 (1.38s), tool_service-dev /health 200, tool_service-dev /config/status 200
   log orchestrator: 2026-11-17 05:48:38,189 WARNING config_loader: config on reload: config/caf\udce9.yaml: not a file any service reads (a typo, or a leftover copy?); ignored
   log orchestrator: 2026-11-17 05:48:42,563 INFO main: config: files from /config (8 files)
   log orchestrator: 2026-11-17 05:48:42,563 WARNING main: config at startup: config/caf\udce9.yaml: not a file any service reads (a typo, or a leftover copy?); ignored
   restored; reload after restore: exit 0 | == orchestrator (kronk-orchestrator-dev-1) 200 {"ok":true,"changed":[]} == tool_service (127.0.0.1:8103) 200 {"ok":true,"changed":[]}
```

And the duplicate key in `tuning.yaml` (excerpt):

```
      == orchestrator (kronk-orchestrator-dev-1)
      500 Internal Server Error
   /dev/api/config -> 500 (21 bytes, 0.01s)
   -- startup: kronk-orchestrator-dev-1: running healthy restarts=0 after 7s
   /dev/api/config -> 500 (21 bytes, 0.03s)
   log orchestrator: 2026-11-17 05:48:15,345 ERROR config_loader: config on reload rejected: config/tuning.yaml:65: duplicate key \ud800 (keeping last good values (files, loaded 2026-11-17 05:48:10))
   log orchestrator: 2026-11-17 05:48:19,840 ERROR main: config at startup rejected: config/tuning.yaml:65: duplicate key \ud800 (running on built-in defaults)
```

What holds in all four: the service starts and stays healthy
(`restarts=0`), `/dev/api/status` and tool_service's `/health` answer 200,
the values in effect are right (in scratch, from a non-default state: kept
after each rejection, defaults at startup), a second reload answers
500 again without harm, the log has the message with file and line, and
removing the file and reloading puts everything back.

What does not hold: the reload answers with neither what changed nor the
error (criterion 9); the error is not on `/services`, where the Config
section shows "could not load api/config" for both services for as long
as the file is there, including after a restart (criterion 10). In the
first input the config is valid and the reload **did take effect** (in
scratch the research budget went 7 → 8) while the answer was 500, so an
operator is told a successful reload failed. The feature doc's sentence
"`reload()` always answers" and the fix report's "`reload()` always
returns 200 or 422" are true of the function and not of the endpoint.

Cause, from the code: `Problem.message` and `Problem.file` carry a
directory entry's name (`config_files.stray_entries`, a `str` with
surrogate escapes when the name is not UTF-8) or a YAML key
(`duplicate key {…}` in `config_files.build`; a double-quoted `"\uD800"`
is a lone surrogate once the library has unescaped it). Starlette's
`JSONResponse` encodes with `ensure_ascii=False` and then `.encode("utf-8")`,
which raises `UnicodeEncodeError` on a lone surrogate, inside the route
(`config_api.config_reload`, `config_status`; tool_service's
`config_reload`, `config_status`). Other paths that put a surrogate key
into a message go through `repr` or through pydantic and are escaped
(an unknown key `"\uD800"` gives a 422 with a readable message), which is
why only the duplicate-key message is affected among YAML errors.

How likely: the file name needs a file copied in from a system with a
different file-name encoding; the key needs someone to type a surrogate
escape. Neither is common. The first one is an ordinary accident, though,
and its effect is to hide every config error from `/services` at once.

**2. The inventory still has two classes without a row. (Criterion 1.)**

Everything first-test finding 5 named is now in the addendum, with an
outcome each: the solar thresholds, `WEATHER_REFRESH_SEC`, the taste
knobs, `TEXT_PREVIEW_CHARS`, and more besides. I searched again, wider:
every module-level constant in `orchestrator/` and `tool_service/` whose
value is a literal, a collection or an env read (207 names), and every env
name in `docker-compose.yml`, against the build report. 90 constants are
named; of the 117 that are not, most are not prompts, model-facing text
or tuning (tables of the ops classifier, regexes, paths, protocol
constants). Two classes are in the criterion's scope and have no row:

- **the sentences a tool returns, which the model reads back**:
  `orchestrator/tools.py` handler results (35 `return failure(…)` /
  `ToolResult(…)` / `_fail(…)` sites; by name `MM_UPDATE_REFUSAL`,
  `NEWS_REFRESH_FAILED`, `NEWS_UNAVAILABLE`) and tool_service's result and
  failure sentences (`_not_found_sentence`, `_no_start_sentence`,
  `_failure_sentence`, the `HTTPException` details). The inventory has
  rows for tool descriptions, terminal-speech templates, `errors.py`
  sentences and loop guardrail texts, and mentions tool_service's result
  lines only as what the speech templates are coupled to;
- **wiring and on/off env vars in `docker-compose.yml`** (the criterion
  lists "env vars in `docker-compose.yml`"): `HA_URL`, `MA_URL`,
  `SEARXNG_URL`, `LLM_SERVICE_URL`, `TOOL_SERVICE_URL`, `TEMPO_URL`,
  `OTEL_*`, `MM_SSH_TARGET`, `MM_SSH_KEY`, `MM_SCRIPT`, `OPS_HOSTS`,
  `SOLAR_HOST`, `SOLAR_DB`, `MUSIC_TASTE_DB`, `MUSIC_TASTE_FILE`,
  `MUSIC_TASTE_ENABLED`, `SOLAR_POLL_ENABLED`,
  `VOICE_TRACER_AUTOMATIONS`. The addendum has one wiring row with five
  other names; `NEWS_*` and "telemetry vars" cover theirs by wildcard.

Both are "left where it is" rows, one line each. A documentation fix. If
the coordinator reads criterion 1 as bounded by its own list of examples,
the first class falls outside it and the second is the only gap.

**3. First-test findings, state after the fix round.**

| First-test finding | State |
|---|---|
| 1. Deep nesting crashes startup, 500 on reload | **fixed**: scratch and live, startup and reload (sections 3a, 4b) |
| 2. Alias bomb not rejected; error message as large as the file | **fixed**: refused at the first anchor; longest message in 408 cases 158 characters |
| 3. A large file blocks the service for the length of the reload | **fixed**: refused from `stat` in 0.08 s; slowest load of any file within the cap 0.27 s |
| 4. A reload during a running request mixes old and new values | unchanged; recorded for step 2 by ruling (section 5) |
| 5. The inventory misses tuning values | the named values are there; two other classes are not (finding 2) |
| 6. The suite makes network attempts | unchanged; recorded by ruling (section 7) |
| 7. Test gaps shown by the mutation check | the two that mattered are closed; five old ones remain and the new read-path defences have none (section 7) |

## 9. Observations

1. **Timeout type.** From the files the per-tool timeout reaches httpx as
   a float (`15.0`) where the old constant was an int (`15`); from the
   built-in defaults it is still an int, and after an env override a
   float again. It goes only to `httpx.AsyncClient(timeout=…)`. The only
   differing leaves in section 2. Unchanged since the first test.
2. **A reload that races an in-place save can load an empty file as
   valid.** A file truncated and then rewritten (what `open(path, "w")`
   does) is, for a moment, zero bytes; a reload at that moment answers
   `ok` with every value at its default and the warning "all defaults
   (file has no keys)". In my tight loop that was two reloads in three
   (section 3b); by hand it needs bad luck. A writer that renames a
   finished file into place never showed it. Step 2's `/settings` page
   will write and reload: it should write atomically.
3. **The two YAML front ends are not quite one language.** The fix report
   and `config_files.py` say both libraries emit the same event stream.
   In 40,000 inputs through both: 36,280 agreed exactly, 3,468 were
   rejected by both at different lines, 40 loaded the same data with
   different line numbers, in 207 one accepted what the other rejected,
   and in 5 both loaded different data. The causes I saw: PyYAML treats
   U+0085, U+2028 and U+2029 as line breaks and ruamel does not
   (`verify: y:` is two top-level keys in one and a nested key in the
   other; a line number after such a character differs by one); ruamel
   accepts an empty plain key (`: 1`) and some `? key` forms that PyYAML
   rejects. Each file is read by one service only, and in every case I saw
   the schema then rejects the odd result, so nothing follows from it in
   step 1. One consequence does show: a U+2028 or U+0085 inside a comment
   makes the rest of that comment line YAML content, and the file is
   rejected (`expected '<document start>'…`).
4. **Key names are not shortened in messages.** The 80-character quote
   applies to values. YAML's own limit caps a key at 1,024 characters, so
   a 64 KiB file of 63 unknown 1,000-character keys gives 51 problems of
   about 1,013 characters each and a 53 kB reload answer (0.01 s). Bounded,
   but not small.
5. **Some YAML errors have no line.** A control character anywhere in a
   YAML file (`not valid YAML: unacceptable character #x001b: special
   characters are not allowed`), an integer of more than 4,300 digits and
   a `"\UFFFFFFFF"` escape (`could not be loaded (ValueError: …)`, caught
   by the catch-all layer) are rejected with the file and `line: null`.
   The library knows the position in the first case.
6. **Accepted, and worth knowing.** In a prompt: U+2028, a bidi override
   (U+202E), zero-width characters, noncharacters and private-use
   characters (only category Cc is refused); a body of 65,000 `{`; a
   header `<!-- a <!-- b --> c -->`, which ends at the first `-->` so that
   ` c -->` becomes the first line of the prompt; CR-only line endings
   (read as LF). In YAML: an unknown directive (`%FOO bar`) and `%YAML 1.1`
   are accepted without a word. A hard link to a file outside `config/` is
   a regular file and loads. A stray entry's name is echoed as it is, so a
   name containing ESC sequences or a newline reaches the log raw.
7. **The handler refusal keys on a non-empty header.** `X-Real-IP` with an
   empty value passes; nginx always sets it non-empty. `/dev/config/reload/`
   (trailing slash) is answered by the app with a 307. Both unchanged
   since the first test.
8. **The secrets test's shapes.** JWT, 32+ hex, `sk-`/`pk-`, PEM and
   `password=`-style assignments. A `ghp_…` token or a 40-character
   base64 string in a comment passes it. The four token-type secrets this
   repo has would be caught.
9. **The suite leaves FIFOs behind.**
   `test_a_pipe_in_place_of_the_file_does_not_block` makes its FIFOs under
   pytest's `tmp_path`, and pytest keeps the last three runs' directories
   under `/tmp/pytest-of-drew/`. They are harmless there (nothing reads
   them), but they are FIFOs left on disk after every run.
10. **Production's nginx and the promotion step.** The deny rule and the
    `/config` mount reach production only at promotion, as the build
    report says. Until then production has no `/config/reload` at all.

## 10. What I changed temporarily, and reverted

- `config/` in the working tree, on dev only: the edits of sections 4a,
  4b, 4c, 5 and finding 1, each restored from a byte copy taken before the
  first edit. At the end `sha256sum` of all nine files equals the copy,
  `HEAD:config/` equals the working tree equals `/config` inside
  `orchestrator-dev` file by file, there is no symlink, FIFO or extra
  entry in `config/`, and `git status` shows only the files of this
  report.
- `docker restart` by name: `kronk-orchestrator-dev-1` eight times,
  `kronk-tool_service-dev-1` five times (sections 4b, 4c, finding 1). No
  rebuild. No other container was restarted, stopped or reconfigured.
- One throwaway `nginx:1.29-alpine` container, `--network none --rm`,
  twice (the first try failed its own config test for a missing host
  mapping and proved nothing); gone.
- At the end: both dev containers up and healthy, running the working
  tree's code (md5 rechecked) and the committed `config/`, both `ok=True
  source=files`, the shim path answering (`/dev/api/chat` → "Paris");
  `tool_service-dev` without `/keys` and without `MM_*`; `ops/` with only
  `hosts.example.json`; `llama-gemma4-e4b`, `llama-gemma3-4b`,
  `llama-devstral-q4`, `wyoming-whisper`, the hot tub monitor and the
  memory watchdog active; production's containers with their start times
  of 03:30Z.

Not tested: the nginx deny rule in the running nginx (not loaded);
production's `/config/reload` in any form (not deployed, and out of
bounds); the reload path through `/api/solar/` against production (the
throwaway container shows where nginx sends it).

Departures and leftovers I should own:

- My restore step rewrote files with mode 644 where they had been 664.
  Git does not track that bit and `git status` stayed clean; I set all
  nine back to 664, as found.
- An early version of my live script printed "values in effect" from a
  second interpreter inside the dev container, which loads the files
  itself and so says nothing about the running process. I replaced it
  with the digests from `/dev/api/config` and used none of its output
  here. Twice I also ran `python -c "import main"` inside a dev container
  to list its routes: a second interpreter, no server, nothing written.
- One plain `./scripts/run_tests.sh`, as the brief asks, made the name
  lookups of first-test finding 6 for real, left one empty
  `/tmp/kronk-taste-*` directory, and left two FIFOs under
  `/tmp/pytest-of-drew/pytest-586/` (observation 9), which I removed with
  `rm -f`. Two older FIFOs under `pytest-585/`, from a run before mine,
  I left alone.
- Scratch clean-up inside my scripts used `os.unlink` and `os.rmdir` on
  named paths under the session scratch directory, never following links;
  the final clean-up used `rm -f` on files and `rmdir`. All my Python runs
  had `TMPDIR` inside the scratch directory, so nothing of mine went to
  `/tmp` except the one suite run above.
- No secret value was printed or written: environment listings were cut
  to names, and the value scan printed only key names and yes/no.
- No safety control stopped a test.
