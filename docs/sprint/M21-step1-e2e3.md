# M21 step 1 end-to-end retest after fix round 2

**M21 STEP 1 PASSES.**

End-to-end test agent (Claude Fable 5.2), 2026-11-17 06:39Z to 07:20Z,
branch `refactor` at `31dd75b`. Criteria: `docs/sprint/M21-step1-criteria.md`,
including the coordinator rulings at its end. `docs/sprint/M21-step1-build.md`
(with both addenda), `M21-step1-fix1.md` and `M21-step1-fix2.md` were read as
claims to check. The commit before the build is `ff99263`. I tested; I
changed no repo code, test, corpus or config file.

Why it passes. Every finding of the two earlier tests is closed, in scratch
and live on dev, on reload and at startup (section 4b). I then tried again
to find a file or a file name under `config/` that takes a service down,
keeps it from starting, puts a wrong value into effect, or hides the error
from both `/services` and the logs, which is what the rulings make the test
of criteria 7 to 10. I found none: 281 hostile config directories, each at
startup and on reload in a fresh interpreter (1,124 loads); 60,000 fuzzed
files checked against an independent reading of the YAML; 42 cases live on
dev, 26 of them with a restart of the dev container that reads the file
(27 restarts).
No exception left a loader, no answer was anything but 200 or 422, every
rejection kept the last good values (or the defaults at startup) and was on
`/services` and in the log, and every accepted file put into effect exactly
what it says. The move still changes no behaviour: 40,961 compared values
per run are identical between `ff99263` and HEAD apart from the known
int/float type of the tool timeout (section 2). The inventory now covers the
two classes the first retest found missing (section 8). Tests, mutation
check and replay are in section 7.

What the coordinator should know before step 2 (observations, section 9;
none fails a criterion under the rulings):

- A reload that lands in the middle of a save written in several pieces can
  load the first part of the file as a valid file, with no warning: values
  from the part not yet written fall to their built-in defaults
  (observation 1). Fix round 2's read-twice check narrows this and does not
  close it; the feature doc describes only the empty-file case. Atomic
  writing by step 2's editor is already a step 2 requirement by ruling, and
  this is the reason it matters.
- A reload during a running request still mixes old and new values in that
  request (first-test finding 4; a step 2 requirement by ruling).
  Re-measured, unchanged (section 5).
- The nginx deny rule is still not loaded in the running nginx. Untested
  live, as the brief directs; it answers 404 in a throwaway container
  (section 5).

## 1. Criteria

| # | Result | Evidence |
|---|---|---|
| 1 | pass | The fix-round-2 addendum covers both classes the first retest found missing. By my own count: all 42 env names set under `environment:` in `docker-compose.yml` are named in the report; the 60 `HTTPException` sites in `tool_service/main.py` are the 60 line numbers the row lists, one for one; the rows for `orchestrator/tools.py:505-1058` span all 38 `failure(…)`/`_fail(…)`/`ToolResult(…)` sites; `MM_UPDATE_REFUSAL`, `NEWS_REFRESH_FAILED`, `NEWS_UNAVAILABLE` and the three sentence helpers are named. Every row has an outcome. Section 8 |
| 2 | pass | `git diff ff99263 HEAD -- orchestrator/routing.py litellm/ systemd/ .env.example scripts/model_bench.py` is empty. The `ctx_k` note is in the report |
| 3 | pass | `config/prompts/` holds exactly seven files; the loader's prompt names equal `AGENTS` plus `coordinator` (compared in the test venv); `config/tuning.yaml`, `config/music.yaml`. No stub files, no symlinks, no hidden files |
| 4 | pass | Own check: 20 keys in `tuning.yaml` and 14 in `music.yaml`, each with a comment line directly above it. Every prompt file starts with `<!--`. No rendered prompt holds `<!--` or `{{` |
| 5 | pass | Own scan, section 6: no secret name, no secret value, no token shape, no URL, address or e-mail in the nine files. The builder's test kills a planted secret name and a planted JWT (mutants `f5`, `f6`) |
| 6 | pass | `./config:/config:ro` four times in `docker-compose.yml` (lines 67, 208, 308, 358). `docker inspect`: both dev containers mount the working tree's `config/` with `rw=false`. Production runs release `sprint-M2`, which has no `/config` mount yet. A mount made read-write is killed (mutant `h5`) |
| 7 | pass | Unknown key, wrong type, out of range, duplicate key, tabs, missing prompt, empty prompt, header-only prompt, non-UTF-8 bytes: each rejected with the file and, for YAML, the line. Scratch (section 3) and live on dev (section 4c) |
| 8 | pass | No invalid file took effect in any case. At startup with a bad file the service started on the built-in defaults (27 restarts live, 234 scratch cases); after a bad reload the last good values stayed, checked from a state that differs from the defaults in scratch (every case) and live (section 4a) |
| 9 | pass, nginx rule not tested live | `POST /config/reload` exists on both services, reloads from disk, and answered 200 with what changed or 422 with the errors in every case, scratch and live (no 500 anywhere). Reachable from the host and from the compose network; every attempt through nginx's `/dev/` prefix was refused. The nginx deny rule is not loaded in the running nginx, so it is untested live; in a throwaway container it answers 404 itself. Concurrent reloads are serialised. Section 5 |
| 10 | pass | In every rejected case the error was on `/dev/api/config` (the `/services` data) with the file, and the line where there is one, and in the log; the first retest's four inputs now answer 200/422 with the error shown (section 4b). In scratch `GET /api/config` and `GET /config/status` answered 200 after all 1,124 loads |
| 11 | pass | Only `WEATHER_CTX_MAX_AGE_S` has two sources. Env set: it wins over the file, from the files and from the defaults, and old tree and new tree inject the forecast for exactly the same ages. An invalid value is an error naming the variable. Sections 2 and 3d. Mutant `v16` (override ignored) is killed |
| 12 | pass | No moved constant name is left in `orchestrator/`, `tool_service/`, `scripts/`, `systemd/`, the compose files, `voice_tracer/`, `hottub_monitor/`, `ha/`, `nginx/` or `litellm/`, outside one comment, the env override table and a comment in `tuning.yaml`. `prompt_defaults` is read only by `config_loader`. Prompt text exists only in `config/prompts/`, `prompt_defaults.py`, the pin fixture, old bench results and `scripts/model_bench.py` (its own older prompt, unchanged by the build). Files as shipped equal the built-in defaults (prompts, tuning and music compared in the test venv) |
| 13 | pass | The builder's pins pass. Independently, with a script written for this retest: 13,440 single-round variants, 231 multi-round transcripts and 336 pipeline runs, identical old against new. The pins were written from `ff99263`: regenerated there, byte-identical. Section 2 |
| 14 | pass | Menus identical. Values identical in effect; the one type difference changes nothing (observation 6) |
| 15 | pass | 17 build commits, home prompt first, one piece each (the first test ran the suite at each). The five fix commits each pass the suite and the pins in scratch extracts with sockets refused (section 7) |
| 16 | pass | `3003 passed, 2 skipped, 18 xfailed`, the claimed count. The five M21 test files make no network attempt; the pre-existing name lookups are recorded, by ruling (section 7) |
| 17 | pass | One complete run: 113 of the 114 baseline rows have the baseline's status; the one that differs, `eval-shuffle-it`, is a known flapper. Section 7 |
| 18 | pass | Live on dev, section 4a. `config/` byte-identical at the end, `git status` clean for `config/` |
| 19 | pass | `README.md` ("Prompts and tuning", runbook line), `docs/features/config-directory.md` (schema, bounds, precedence, reload, failure behaviour), `CLAUDE.md` item 3 and tenet 8 (two edits, nothing else in the diff). Two things the feature doc does not say: observations 1 and 2 |
| 20 | pass | Production containers kept their start times (orchestrator, tool_service, nginx, LiteLLM, SearXNG, voice_tracer started 03:30Z, `restarts=0`); Home Assistant started 2026-11-16 00:28Z, Music Assistant 2026-11-04; the llama units, `wyoming-whisper`, the hot tub monitor and the memory watchdog active with their old start times. No restart of any of them by me. The replay reported "players unchanged (17 checked)" |

Coordinator rulings applied: first-test finding 4 is recorded, not counted.
An unknown `prompts/*.md` being an error is accepted. The pre-existing name
lookups in the suite are recorded. Criteria 7 to 10 are judged by the
materiality ruling.

## 2. No behaviour change (criteria 13, 14), from `ff99263`

The loaders changed again in fix round 2, so this was done again from
nothing, with a script written for this retest (not the builder's test, not
its fixtures, not an earlier tester's script; none was kept).

Method. `git archive ff99263` and `git archive HEAD` into two scratch
directories. The script runs inside one tree with `llm.stream` replaced by a
scripted recorder, `httpx.AsyncClient` replaced by a recorder of the
`timeout=` it is given, every tool handler replaced by a fixed result, the
clock frozen, the request id fixed, and every INET socket call and name
lookup refused. It captures:

- **single round**: `agents.run_stream` for all seven agents × error style
  (debug, friendly, derived) × voice × origin stamp × escalation × five
  `system_extra` blocks × weather context × embedded context ×
  `history_messages` × `delegated`: 13,440 runs, each the sha256 of the
  complete message list, tool menu and yielded events; and 28 of them in
  full text (each agent, with and without escalation, voice and not);
- **loops**: 231 multi-round transcripts in full (seven agents × eleven
  scripts × three style/voice settings): the budget exhausted on distinct
  calls and on one repeated call (so the round budget, the duplicate-call
  path and the repeat-call nudge are exercised, with the number of LLM
  calls recorded), a rejected tool, two calls in one round with one failing,
  an empty reply, an LLM error in round 1 and in round 2, an escalation, a
  terminal tool succeeding and failing, and the coordinator delegating;
  each with the timeout handed to httpx for every tool call;
- **pipeline**: `main._run_pipeline` for all seven routes × six scripts ×
  voice × origin × local context: 336 runs, three in full;
- **menus**: `tool_defs()` of every agent, `agent_tool_defs()`,
  `_coordinator_tool_defs()`, `TOOL_DEFINITIONS`, `ESCALATE_TOOL`,
  `ESCALATION_GUIDANCE`, `roster()`, `kronk_facts()`, and each agent's model,
  budget, terminal tools, tool names, description, routing hint and system
  prompt;
- **values as used**: the timeout `tools.execute` hands to httpx for all 22
  tools; what `weather_context()` returns at nineteen ages from 0 to
  10,000,000 s; the verify window and the sentence that names it;
  `music_resolve.score()` over 35,504 query/candidate pairs;
  `music_resolve.rank()` for 56 queries in four type settings and
  `_resolve_named_inner()` (the play threshold) for the same queries in
  three; the "closest I found" sentence at twelve scores around the hint
  threshold; the resolver constants.

Result: 15,102 captured items, 40,961 compared leaves per run.

| New tree run with | Config source reported | Leaves that differ from `ff99263` |
|---|---|---|
| `config/` as committed | orchestrator files, tool_service files | 339, all the number type of the tool timeout (`15` → `15.0`), where a value or a transcript prints it; 0 others |
| `config/` absent | both defaults, 8 + 1 "file is missing" errors | 0 |
| each of the seven prompt files emptied in turn | orchestrator defaults (`must start with a <!-- header comment -->`, line 1), tool_service files | 0 (seven runs) |
| each of the seven prompt files removed in turn | orchestrator defaults (`file is missing`), tool_service files | 0 (seven runs) |
| `tuning.yaml` with an unknown key; with `default_s: 601`; with `agent_loop: [` | orchestrator defaults (lines 65, 38, 2), tool_service files | 0 (three runs) |
| `music.yaml` with an unknown key; with `timeout_s: 800`; with `verify: {` | orchestrator files, tool_service defaults (lines 59, 19, 2) | 339, the same type-only leaves (three runs) |
| `WEATHER_CTX_MAX_AGE_S=600`, files (against the old tree with the same env) | files, `env_overrides` shows the variable | 339, the same |
| `WEATHER_CTX_MAX_AGE_S=600`, `config/` absent (same) | defaults | 339, the same (observation 6) |

With the env var at 600 the old tree itself changes in exactly six leaves
against its own run without it (the forecast is no longer injected at 601,
3599, 3600, 3601, 7199 and 7200 s; still injected at 600), and the new tree
changes in the same six.

Control, to show the script can see a change: the new tree with one word
appended to `home.md`, `research: 7` and `hint_min_score: 0.51` differs from
`ff99263` in 5,272 leaves (the home and research prompts in every run that
carries them, the research budget, the "closest I found" sentences).

One socket call was refused per run, the same in both trees (in the suite's
guard log the single call made at import is a bind of `::1`).

I also read the code diff outside the new modules (`git diff ff99263 HEAD`
on `agents.py`, `tools.py`, `main.py`, `tool_service/main.py`,
`music_resolve.py`): every use of a moved constant is replaced one for one
by a read of the config value; nothing else changed in those files except
the two endpoints, the startup log line and the registry hydration.

**The pin fixtures are genuine.** `git diff ff99263 4f05264` touches only
five files under `tests/`. `git diff 4f05264 HEAD -- tests/fixtures/m21` is
empty; `tests/test_m21_pins.py` changed since only in how it reads the
values (the config accessors in place of the removed constants) and by one
added test for the built-in defaults. `4f05264` has no `config/` directory.
I extracted it, moved the three fixture files away, ran
`tests/test_m21_pins.py --write` there (`3390 variants, 14 menus`), and
compared with the files committed at HEAD: `prompts.json`, `menus.json` and
`values.json` are byte-identical (`cmp`).

## 3. Loading and validation in scratch (criteria 7, 8, 10, 11; extra scope b)

### 3a. The battery: 281 config directories, each at startup and on reload

Every case is a scratch copy of the committed `config/` with one thing
wrong (or odd, or valid but changed), run twice in a fresh interpreter
under a 4 GB address-space limit, a 60 s alarm and refused sockets:

- **startup**: the orchestrator's imports (`agents`, `main`),
  `main._log_config_status()`, tool_service's import and `start()`;
- **reload**: started on a config that differs from the defaults (research
  budget 7, `default_s` 16, verify window 9, a marker word in `home.md`),
  the case directory renamed into its place, then the real `POST
  /config/reload` routes of both services (FastAPI test client), `GET
  /api/config` (with tool_service's app answering in process), `GET
  /config/status`, `GET /health`, and a second reload.

Recorded per case and service: any exception, the answer and its size, the
time, the peak resident memory above the level before the load, the values
in effect before and after (prompt digests, budgets, the whole tuning and
music models, the timeout of every tool, the verify sentence), the status,
and the log lines. A case states which service must reject it; a mismatch
is flagged.

104 cases for `tuning.yaml`, the same 104 for `music.yaml`, 35 for prompts,
24 for file names and stray entries, 7 for the config directory itself, 7
others. 1,124 loads.

| | Result |
|---|---|
| Exceptions out of an import, a loader, `start()`, the startup log or a route | 0 |
| Interpreters that hung or hit the memory limit | 0 |
| Reload answers | 321 × 200, 241 × 422, nothing else; the second reload gave the same code every time |
| `GET /api/config`, `GET /config/status`, `GET /health` after every load | 200 |
| After a 422: values in effect equal the ones before; status `ok=False` with the error; an ERROR line in the log | every case |
| At startup with a rejected file: source `defaults`, values equal the built-in defaults, error on the status and in the log | every case (234 of the 281 rejected one service or both) |
| Accepted files: values in effect | as the file says, in every case (reviewed one by one; below) |
| Cases whose outcome differed from the one I expected | 0 |
| Slowest reload answer | 0.29 s (`music.yaml`, a flow list of 31,000 items; 0.23 s for `tuning.yaml`) |
| Largest memory peak during a reload | 7.8 MB (6,000 unknown keys) |
| Largest reload answer | 10,265 bytes (600 unknown prompt files with 240-byte names that are not UTF-8: 20 errors) |
| Longest single message | 158 characters |

Three cases were flagged on the first run for faults of my harness (a unix
socket path too long for `bind`; the reload half of "config directory
missing" renaming a directory that was not there). Fixed and rerun; clean.

What was fed, by kind (✓ = rejected by the service that reads the file,
with a config error naming the file, last good values kept, defaults at
startup):

- **the earlier findings**: 20,000, 500, 300 and 11 nested brackets and a
  block mapping 300 deep (✓ `nested more than 10 levels deep`; 9 levels is
  an ordinary schema error); the nine-level alias bomb, a four-level one
  and a self-referencing anchor (✓ `anchors (&name) are not allowed`); 50 MB
  of comment lines, a 20 MB string, a 30 MB prompt (✓ `too large: N bytes;
  the limit is 65536`); 65,537 bytes refused and 65,536 loaded; a duplicate
  key `"\uD800"` (✓ `duplicate key \ud800`, line named), the same key once
  and the same escape as a value (✓); a stray `caf\xe9.yaml` (loads, with
  the warning, name shown as `caf\udce9.yaml`) and `prompts/caf\xe9.md`
  (✓ `unknown prompt file`);
- **the brief's list**: unknown key in a section and at the top; for the
  integer and the float key of each file: a word, a quoted number, a bool,
  a list, a mapping, null, zero, negative, too large, `.inf`, `.nan`,
  `1e999`, `017`, `0x10`, `1_0`, `1:00`, `yes`, a 4,000-digit and a
  5,000-digit integer (✓ with the line; `3.0` for an integer is ✓, and is a
  valid verify window); duplicate key in a section, at the top, and written
  once quoted; tabs; an empty file and a comments-only file (load, with the
  warning `all defaults (file has no keys)`); non-UTF-8 bytes, CESU
  surrogate bytes, UTF-16 (✓ `cannot be read`); NUL and ESC in a comment
  (✓ with the character, line and byte offset); U+2028 and U+0085 in a
  comment (✓); a symlink to a scratch file outside, a dangling symlink, a
  symlink to itself (✓ `is a symlink…`); a FIFO, a directory and a unix
  socket in the file's place (✓ `is not a regular file…`); mode 000 (✓
  `cannot be read: Permission denied`); missing; a hard link (a regular
  file: loads);
- **more YAML**: two documents, a document end followed by more keys, a
  scalar or list document, a section that is a list or null, a merge key, a
  `!!python/object/apply:os.system` tag (✓, nothing ran), `!!int`, a complex
  key, an empty key, keys holding an escaped NUL, a newline and ESC, a bidi
  override (✓, shown escaped: `duplicate key a\x0ab\x1b[31m`), a
  `"\UFFFFFFFF"` escape, 6,000 unknown keys (✓ 50 problems and a count), a
  60,000-character value (✓, quoted to 80), `%YAML 9.9` (✓), eleven kinds of
  64 KiB soup (brackets, `a: `, `? `, `- `, quotes, `{a: `, commas, colons,
  `&a `, `!t `, deep indentation: all ✓), a 31,000-item flow list (✓); a
  60,000-character comment, a BOM, CRLF and CR-only line endings load
  unchanged;
- **valid edits**: every tuning value changed and every music value
  changed, in flow and block style, with `+31`, `3.6e1`, `0.001` and an
  integer for a float. Each took effect exactly: the "changed" list, the
  model, the per-agent budgets, the timeout of every tool and the verify
  sentence all show the file's values, and the tools not named keep their
  built-in defaults;
- **prompts**: missing, empty, header only, header and whitespace, header
  and no-break spaces, header and a second comment only, no header,
  unclosed header, non-UTF-8, UTF-16, BOM, NUL, ESC and form feed in the
  body (✓ with the line where there is one), an unknown placeholder and
  10,000 of them (✓ capped), 65,537 bytes (✓), a symlink, a FIFO, a
  directory, mode 000 (✓); `prompts/` missing, a file, a FIFO, a symlink to
  a good directory, mode 000 (✓ all seven prompts named). Accepted, as they
  should be: a placeholder written across three lines, `{{max_rounds}}` in
  the coordinator's prompt, 65,000 `{`, `%s`/`{0}`/backslash text, 65,536
  bytes, CRLF. Accepted and worth knowing: observation 5;
- **names**: `prompts/extra.md`, `Home.md`, `.md`, a FIFO, a directory and
  a dangling symlink named `x.md` (✓ unknown prompt file);
  `prompts/home.md.bak`, `tunning.yaml`, `-rf`, a stray FIFO and a dangling
  symlink (load, with the warning); names holding ESC and a newline, a bidi
  override, `%s%d{0}%(a)s`, only invalid bytes, CESU bytes, 255 bytes (shown
  escaped and cut; nothing raw reached a log line: `config/a\x1b[31m\x0ab.yaml`);
  600 stray files, 3,000 stray files under `prompts/`, 600 unknown `.md`
  files (bounded: 20 reported and "more entries not checked");
- **the config directory**: missing, empty, a file, a FIFO, a dangling
  symlink, a symlink loop, mode 000 (✓ both services on defaults at
  startup, 422 on reload).

### 3b. Fuzz with an independent oracle

60,000 reloads in four processes (seeds 911 to 914, 15,000 each): the
shipped `tuning.yaml`, `music.yaml`, `prompts/home.md` and
`prompts/research.md` with one to six random edits (delete a span, insert
or substitute a token from a list of YAML syntax, schema words, numbers,
escapes and odd characters, duplicate, swap or re-indent a line, replace a
number with a boundary value, truncate, append token soup, insert an
invalid byte). Each through `reload()`, `render_reload`, `render_status`
and the encoding `JSONResponse` uses. For every accepted YAML file the
values in effect were compared with an independent reading: PyYAML's
`safe_load` of the same bytes, validated by the schema.

| | Result |
|---|---|
| Raised | 0 |
| Answers that could not be encoded (the fallback sentence) | 0 |
| Rejected with the values in effect changed | 0 of 22,259 |
| Accepted | 37,741 |
| Accepted with values that differ from the independent reading | 0 |
| Accepted where the independent reading rejects | 11, all the value `1e2` for a timeout: PyYAML's YAML 1.1 reads it as a string, the loader reads 100.0, as its documented dialect says |
| Slowest reload | 0.013 s |
| Largest answer; longest message | 2,956 bytes; 167 characters |

### 3c. A writer racing the reload

One thread rewrote the file between two valid contents while the main
thread reloaded in a loop, 6 s per row. The two valid states are named in
the first column; anything else in effect is a third state.

```
valid states: tuning (7, 16.0) or (9, 17.0); music (7, 0.4) or (9, 0.6). Anything else in effect is a third state.
tuning.yaml, in-place rewrite, one write: 6536 reloads, raised 0, slowest 0.003s
        3860  ok, in effect (8, 15) [warning: all defaults (file has no keys)]
        2316  422 file changed during read (read twice); reload again once it  | kept (8, 15)
         110  ok, in effect (9, 17.0)
          95  ok, in effect (7, 16.0)
          88  422 file changed during read (read twice); reload again once it  | kept (9, 17.0)
          67  422 file changed during read (read twice); reload again once it  | kept (7, 16.0)
tuning.yaml, in-place rewrite, 5 chunks: 6391 reloads, raised 0, slowest 0.003s
        2945  422 file changed during read (read twice); reload again once it  | kept (8, 15)
        2714  ok, in effect (8, 15) [warning: all defaults (file has no keys)]
         182  422 file changed during read (read twice); reload again once it  | kept (7, 16.0)
         152  422 file changed during read (read twice); reload again once it  | kept (9, 17.0)
         142  ok, in effect (7, 16.0)
         133  ok, in effect (9, 17.0)
          37  422 file changed during read (read twice); reload again once it  | kept (7, 15)
          33  ok, in effect (9, 15)
tuning.yaml, atomic rename: 3375 reloads, raised 0, slowest 0.003s
        1714  ok, in effect (9, 17.0)
        1661  ok, in effect (7, 16.0)
music.yaml, in-place rewrite, one write: 50761 reloads, raised 0, slowest 0.010s
       36421  ok, in effect (8, 0.5) [warning: all defaults (file has no keys)]
       12061  422 file changed during read (read twice); reload again once it  | kept (8, 0.5)
         822  ok, in effect (7, 0.4)
         812  ok, in effect (9, 0.6)
         347  422 file changed during read (read twice); reload again once it  | kept (9, 0.6)
         298  422 file changed during read (read twice); reload again once it  | kept (7, 0.4)
music.yaml, in-place rewrite, 5 chunks: 46133 reloads, raised 0, slowest 0.003s
       25398  ok, in effect (8, 0.5) [warning: all defaults (file has no keys)]
       16486  422 file changed during read (read twice); reload again once it  | kept (8, 0.5)
         727  ok, in effect (7, 0.4)
         688  ok, in effect (9, 0.6)
         470  422 file changed during read (read twice); reload again once it  | kept (7, 0.4)
         448  422 file changed during read (read twice); reload again once it  | kept (9, 0.6)
         365  422 resolver: Input should be a valid dictionary or instance of  | kept (8, 0.5)
         323  422 not valid YAML: could not find expected ':' | kept (8, 0.5)
music.yaml, atomic rename: 6898 reloads, raised 0, slowest 0.001s
        3450  ok, in effect (9, 0.6)
        3448  ok, in effect (7, 0.4)
```

(The table prints the eight commonest outcomes per row.) Nothing raised. A
writer that renames a finished file into place gives only the two valid
states. A writer that saves in place gives the empty-file state with its
warning, which the fix report and the feature doc describe, and, when it
writes in pieces, a state they do not describe: `ok, in effect (9, 15)`,
33 times, is the research budget of the new file with the built-in
`default_s`, because the read fell between two pieces, the file was steady
at that moment, and its first part is a valid file. No warning. This is
observation 1; by the ruling on atomic writing it is step 2's to close.

### 3d. Precedence (criterion 11)

The file says `max_age_s: 3600`; the built-in default is 7200.

```
  env=None      reload ok=True in effect=3600 env_overrides={} errors=[]
             startup source=files in effect=3600 errors=[]
  env='600'     reload ok=True in effect=600 env_overrides={'WEATHER_CTX_MAX_AGE_S': '600'} errors=[]
             startup source=files in effect=600 errors=[]
  env=' 900 '   reload ok=True in effect=900 env_overrides={'WEATHER_CTX_MAX_AGE_S': ' 900 '} errors=[]
             startup source=files in effect=900 errors=[]
  env=''        reload ok=True in effect=3600 env_overrides={} errors=[]
             startup source=files in effect=3600 errors=[]
  env='abc'     reload ok=False in effect=3600 env_overrides={} errors=[('(env WEATHER_CTX_MAX_AGE_S)', "not an integer: 'abc'")]
             startup source=defaults in effect=7200 errors=[('(env WEATHER_CTX_MAX_AGE_S)', "not an integer: 'abc'")]
  env='30'      reload ok=False in effect=3600 env_overrides={} errors=[('(env WEATHER_CTX_MAX_AGE_S)', 'weather_context.max_age_s: Input should be greater than or equal to 60 (got 30)')]
             startup source=defaults in effect=7200 errors=[('(env WEATHER_CTX_MAX_AGE_S)', 'weather_context.max_age_s: Input should be greater than or equal to 60 (got 30)')]
  env='86401'   reload ok=False in effect=3600 env_overrides={} errors=[('(env WEATHER_CTX_MAX_AGE_S)', 'weather_context.max_age_s: Input should be less than or equal to 86400 (got 86401)')]
             startup source=defaults in effect=7200 errors=[('(env WEATHER_CTX_MAX_AGE_S)', 'weather_context.max_age_s: Input should be less than or equal to 86400 (got 86401)')]
  env='6e2'     reload ok=False in effect=3600 env_overrides={} errors=[('(env WEATHER_CTX_MAX_AGE_S)', "not an integer: '6e2'")]
             startup source=defaults in effect=7200 errors=[('(env WEATHER_CTX_MAX_AGE_S)', "not an integer: '6e2'")]
  env='600.0'   reload ok=False in effect=3600 env_overrides={} errors=[('(env WEATHER_CTX_MAX_AGE_S)', "not an integer: '600.0'")]
             startup source=defaults in effect=7200 errors=[('(env WEATHER_CTX_MAX_AGE_S)', "not an integer: '600.0'")]
```

Env wins over the file; an invalid env value is an error that names the
variable, and at startup the whole config then runs on the built-in
defaults (all or nothing, as the loader's rules say). Before the build an
invalid value crashed the import.

### 3e. Second readers (criterion 12)

Section 1, row 12.

## 4. Live on dev

Before any live request: both dev containers ran the working tree's code
(md5 of every `.py` under `/app` and of `static/` equal to the tree: 28
files for the orchestrator, 10 for tool_service, whose image leaves out the
empty `__init__.py`). No rebuild was needed. `tool_service-dev` has no
`/keys` mount and no `MM_*` variable; `ops/` holds only
`hosts.example.json`; `MUSIC_DRY_RUN=1` on both. `docker inspect`:
production's orchestrator, tool_service and nginx mount
`kronk-releases/sprint-M2/…` and have no `/config` mount; only the two dev
containers mount the working tree's `config/` (read-only). So every edit
below reached dev only.

Each case below: edit the working tree's `config/`, run
`scripts/config_reload.sh dev` with a `GET /dev/api/status` sent 0.2 s into
it, read `/dev/api/config` (the `/services` data), probe `/dev/api/status`,
`tool_service-dev`'s `/health` and `/config/status`; for the startup half,
`docker restart` by name of the one dev container that reads the file and
the same reads again; then restore from a byte copy of the committed files
(checked against `git show HEAD:config/…` before the first edit), reload,
and check the reload answers `ok`. Over all 42 cases: the reload script
took 0.09 to 0.28 s; the concurrent status request took 1.37 to 1.55 s,
which is its normal cost; all probes answered 200; every restart came back
`healthy` with `restarts=0` in 6 or 7 s.

### 4a. Criterion 18

Question on every request: "Does the exact word Kestrel appear anywhere in
your instructions? Answer only yes or no." through
`http://localhost/dev/api/chat`. `rows` are the new lines in dev's own
`data/dev/metrics.db` (opened read-only): id, agent, model, prompt tokens
as the model server counted them. Dev does not record prompt text
(`TELEMETRY_RECORD_TEXT=false`), so the evidence of "in the model input" is
the token count and the model's answer. The invalid YAML was applied while
the edited prompt was in effect, so "the old value stays" is shown from a
state that is neither the defaults nor the committed files. As printed:

```
--- baseline
baseline-1: reply='No.' rows=[(9705, 'coordinator', 'gemma-4-e4b', 2025)]
baseline-2: reply='No.' rows=[(9706, 'coordinator', 'gemma-4-e4b', 2025)]
--- edit: ' Kestrel.' appended to the last line of config/prompts/coordinator.md
-When a specialist answers, relay the substance concisely — do not re-verify it.
+When a specialist answers, relay the substance concisely — do not re-verify it. Kestrel.
   reload (scripts/config_reload.sh dev): exit 0 in 0.10s
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":["prompts/coordinator.md: changed (2339 -> 2348 chars)"]}
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
   a GET /dev/api/status sent 0.2 s into the reload: 200 in 1.49s
edited-1: reply='Yes.' rows=[(9707, 'coordinator', 'gemma-4-e4b', 2028)]
edited-2: reply='Yes.' rows=[(9708, 'coordinator', 'gemma-4-e4b', 2028)]
--- with the edited prompt still in place: invalid YAML values: tuning.yaml agent_loop.max_rounds.default: three ; music.yaml verify.timeout_s: 800
   reload (scripts/config_reload.sh dev): exit 1 in 0.10s
      == orchestrator (kronk-orchestrator-dev-1)
      422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":17,"message":"agent_loop.max_rounds.default: Input should be a valid integer (got 'three')"}],"kept":"last good values (files, loaded 2026-11-17 07:02:35)"}
      == tool_service (127.0.0.1:8103)
      422 {"ok":false,"errors":[{"file":"config/music.yaml","line":19,"message":"verify.timeout_s: Input should be less than or equal to 60 (got 800)"}],"kept":"last good values (files, loaded 2026-11-17 07:02:35)"}
   a GET /dev/api/status sent 0.2 s into the reload: 200 in 1.48s
--- dev /services data
   /dev/api/config -> 200 (1071 bytes, 0.01s)
      orchestrator ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 07:02:35)
         error: {"file": "config/tuning.yaml", "line": 17, "message": "agent_loop.max_rounds.default: Input should be a valid integer (got 'three')"}
      tool_service ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 07:02:35)
         error: {"file": "config/music.yaml", "line": 19, "message": "verify.timeout_s: Input should be less than or equal to 60 (got 800)"}
--- dev /services page: GET /dev/services -> 200 | has the Config section: True | fetches api/config: True
--- last good values still in effect (the edited prompt, not the defaults and not the committed file)
after-bad-reload-1: reply='Yes.' rows=[(9709, 'coordinator', 'gemma-4-e4b', 2028)]
after-bad-reload-2: reply='Yes' rows=[(9710, 'coordinator', 'gemma-4-e4b', 2028)]
   probes: /dev/api/status 200 (1.40s), tool_service-dev /health 200, /config/status 200
--- dev logs
   log orchestrator: INFO:     127.0.0.1:53220 - "POST /config/reload HTTP/1.1" 422 Unprocessable Entity
   log orchestrator: 2026-11-17 07:02:41,429 ERROR config_loader: config on reload rejected: config/tuning.yaml:17: agent_loop.max_rounds.default: Input should be a valid integer (got 'three') (keeping last good values (files, loaded 2026-11-17 07:02:35))
   log orchestrator: 2026-11-17 07:02:41,429 INFO kronk.events: {"ts": 1794898961.429, "rid": "-", "event": "config_error", "when": "on reload", "effect": "keeping last good values (files, loaded 2026-11-17 07:02:35)", "problems": ["config/tuning.yaml:17: agent_loop.max_rounds.default: Input should be a valid integer (got 'three')"], "trace_id": "bac97e9f4ca409d915ed06c3fe876bec"}
   log tool_service: INFO:     172.18.0.1:33746 - "POST /config/reload HTTP/1.1" 422 Unprocessable Entity
   log tool_service: ERROR:tool_service.config:config on reload rejected: config/music.yaml:19: verify.timeout_s: Input should be less than or equal to 60 (got 800) (keeping last good values (files, loaded 2026-11-17 07:02:35))
--- revert all three files (byte copies of the committed files) and reload
   restored config/ (9 files, sha256 equal to the copy; git status clean for config/)
   reload (scripts/config_reload.sh dev): exit 0 in 0.10s
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":["prompts/coordinator.md: changed (2348 -> 2339 chars)"]}
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
   a GET /dev/api/status sent 0.2 s into the reload: 200 in 1.48s
reverted-1: reply='No' rows=[(9711, 'coordinator', 'gemma-4-e4b', 2025)]
reverted-2: reply='No.' rows=[(9712, 'coordinator', 'gemma-4-e4b', 2025)]
   /dev/api/config -> 200 (779 bytes, 0.01s)
      orchestrator ok=True source=files event=reload kept=None
      tool_service ok=True source=files event=reload kept=None
   restored config/ (9 files, sha256 equal to the copy; git status clean for config/)
final reload: 0 == orchestrator (kronk-orchestrator-dev-1) | 200 {"ok":true,"changed":[]} | == tool_service (127.0.0.1:8103) | 200 {"ok":true,"changed":[]}
```

### 4b. Every finding of both earlier tests, on reload and at startup

| Input | Reload answer | On `/dev/api/config` | After `docker restart` of the dev container that reads it |
|---|---|---|---|
| **First test, finding 1.** `tuning.yaml`: `agent_loop: ` + 20,000 `[` + 20,000 `]` (then: crash loop at startup, 500 on reload) | 422 `config/tuning.yaml:1: nested more than 10 levels deep` | orchestrator `ok=False source=files`, the error, `kept=last good values…` | healthy in 7 s, `restarts=0`; `ok=False source=defaults event=startup`, the same error |
| **1.** `music.yaml`: `verify: ` + the same brackets | 422 `config/music.yaml:1: nested more than 10 levels deep` | tool_service the same | healthy in 7 s; `source=defaults`, the error |
| **First test, finding 2.** `tuning.yaml`: the nine-level alias bomb + `weather_context: *i` (then: `MemoryError` in scratch; not run live) | 422 `config/tuning.yaml:1: anchors (&name) are not allowed in config files` | the error | healthy in 7 s; `source=defaults`, the error |
| **2.** `music.yaml`: the bomb + `verify: *i` | 422 `config/music.yaml:1: anchors (&name)…` | the error | healthy in 7 s; `source=defaults`, the error |
| **First test, finding 3.** `tuning.yaml`: 50 MB of `# pad` lines, then `default_s: 601` (then: 27.83 s, the service blocked) | 422 `config/tuning.yaml: too large: 52431615 bytes; the limit is 65536`; script 0.10 s; concurrent status 200 in 1.49 s | the error | healthy in 7 s; `source=defaults`, the error |
| **3.** `music.yaml`: the same with `timeout_s: 800` | 422 `…too large: 52431347 bytes…`; 0.10 s | the error | healthy in 7 s; `source=defaults`, the error |
| **3.** `prompts/home.md`: 30 MB | 422 `config/prompts/home.md: too large: 31457291 bytes; the limit is 65536`; 0.10 s | the error | healthy in 7 s; `source=defaults`, the error |
| **First retest, finding 1.** a stray file `config/caf\xe9.yaml` (then: 500 from the reload and from `/api/config`) | **200** `{"ok":true,"changed":[],"warnings":[{"file":"config/caf\\udce9.yaml","line":null,"message":"not a file any service reads (a typo, or a leftover copy?); ignored"}]}` | 200; orchestrator `ok=True` with the warning | healthy in 7 s; `ok=True source=files event=startup` with the warning |
| **1.** an unknown prompt `config/prompts/caf\xe9.md` | **422** `config/prompts/caf\udce9.md: unknown prompt file: no agent of that name (known: …)` | 200; the error | healthy in 7 s; `source=defaults`, the error |
| **1.** `tuning.yaml` + `"\uD800": 1` and `"\uD800": 2` | **422** `config/tuning.yaml:65: duplicate key \ud800` | 200; the error | healthy in 7 s; `source=defaults`, the error |
| **1.** `music.yaml` + the same two lines (then: tool_service's status "unavailable") | **422** `config/music.yaml:59: duplicate key \ud800` | 200; tool_service `ok=False` with the error, file and line | healthy in 7 s; `source=defaults`, the error |

Log lines, three of the cases:

```
2026-11-17 07:03:04,068 ERROR config_loader: config on reload rejected: config/tuning.yaml:1: nested more than 10 levels deep (keeping last good values (files, loaded 2026-11-17 07:02:55))
2026-11-17 07:09:37,787 ERROR config_loader: config on reload rejected: config/prompts/a\x1b[31m\x0ab.md: unknown prompt file: no agent of that name (known: home, coordinator, assistant, coding, talkie, devops, research) (keeping last good values (files, loaded 2026-11-17 07:09:36))
2026-11-17 07:09:42,381 ERROR main: config at startup rejected: config/prompts/a\x1b[31m\x0ab.md: unknown prompt file: no agent of that name (known: home, coordinator, assistant, coding, talkie, devops, research) (running on built-in defaults)
```

After each restore and reload both services were back to `ok=True
source=files` without a second restart.

### 4c. Ordinary bad files (brief, scope 3)

"Restarted" in the last column means the startup half was run too and gave
`source=defaults` with the same error (or, for the empty files, `ok=True
source=files` with the warning).

| Input | Reload answer (and `/dev/api/config`: the same error, `ok=False`, last good values kept) | Restarted |
|---|---|---|
| `tuning.yaml`: `bogus_key: 1` under `agent_loop` | 422 `config/tuning.yaml:25: agent_loop.bogus_key: unknown key` | yes |
| `tuning.yaml`: `default: '3'` | 422 `…:17: agent_loop.max_rounds.default: Input should be a valid integer (got '3')` | |
| `tuning.yaml`: `default_s: 601` | 422 `…:38: tool_timeouts.default_s: Input should be less than or equal to 600 (got 601)` | |
| `tuning.yaml`: a second `weather_context:` block | 422 `…:64: duplicate key weather_context` | |
| `tuning.yaml`: a tab before `repeat_tool_nudge_at` | 422 `…:24: not valid YAML: found character '\t' that cannot start any token` | |
| `tuning.yaml`: 0 bytes | 200 `{"ok":true,"changed":[],"warnings":[{"file":"config/tuning.yaml","line":null,"message":"all defaults (file has no keys)"}]}`; `ok=True` with the warning | yes |
| `tuning.yaml`: bytes `ff fe` in a comment | 422 `config/tuning.yaml: cannot be read: 'utf-8' codec can't decode byte 0xff in position 2: invalid start byte` | |
| `tuning.yaml`: symlink to a scratch file outside `config/` | 422 `config/tuning.yaml: is a symlink; config files must be regular files inside the config directory` | |
| `tuning.yaml`: a FIFO in its place | 422 `config/tuning.yaml: is not a regular file; config files must be regular files inside the config directory` | yes |
| `tuning.yaml`: mode 000 | 422 `config/tuning.yaml: cannot be read: Permission denied` | yes |
| `prompts/talkie.md` removed | 422 `config/prompts/talkie.md: file is missing` | yes |
| `prompts/home.md`: 0 bytes | 422 `config/prompts/home.md:1: must start with a <!-- header comment -->` | |
| `prompts/home.md`: only the header comment | 422 `config/prompts/home.md:1: prompt is empty` | |
| `prompts/home.md`: byte `e9` in the body | 422 `config/prompts/home.md: cannot be read: 'utf-8' codec can't decode byte 0xe9 in position 4162: invalid continuation byte` | |
| `prompts/home.md`: symlink to a scratch file outside `config/` | 422 `config/prompts/home.md: is a symlink; config files must be regular files…` | |
| `music.yaml`: `bogus: 1` under `resolver` | 422 `config/music.yaml:58: resolver.bogus: unknown key` | yes |
| `music.yaml`: `timeout_s: '8'` | 422 `…:19: verify.timeout_s: Input should be a number (got '8')` | |
| `music.yaml`: `play_min_score: 1.5` | 422 `…:31: resolver.play_min_score: Input should be less than or equal to 1 (got 1.5)` | |
| `music.yaml`: `timeout_s` twice | 422 `…:20: duplicate key verify.timeout_s` | |
| `music.yaml`: a tab before `timeout_s` | 422 `…:19: not valid YAML: found character '\t' that cannot start any token` | |
| `music.yaml`: 0 bytes | 200 with the warning `all defaults (file has no keys)` | yes |
| `music.yaml`: bytes `ff fe` | 422 `config/music.yaml: cannot be read: 'utf-8' codec can't decode byte 0xff in position 2: invalid start byte` | |
| `music.yaml`: a FIFO in its place | 422 `config/music.yaml: is not a regular file…` | yes |
| `music.yaml`: symlink to a scratch file outside `config/` | 422 `config/music.yaml: is a symlink…` | |

### 4d. New attempts on dev (extra scope b)

Each with a restart of `orchestrator-dev` (and `tool_service-dev` where
`music.yaml` is touched). All probes 200; both containers healthy with
`restarts=0` after every restart.

| Input | Reload answer | At startup |
|---|---|---|
| a stray file `config/a\x1b[31m\nb.yaml` (ESC sequence and a newline in the name) | 200 with the warning, name shown as `config/a\x1b[31m\x0ab.yaml`; the log line is one line with the same escapes | `ok=True source=files` with the warning |
| an unknown prompt `config/prompts/a\x1b[31m\nb.md` | 422 `config/prompts/a\x1b[31m\x0ab.md: unknown prompt file: …` | `source=defaults`, the error |
| a stray file with a 255-byte name of bytes `\xff` | 200 with the warning, name shown as `\udcff…` cut with `…` | `ok=True` with the warning |
| an unknown prompt with a 255-byte name of bytes `\xe9` | 422, name shown as `\udce9…` cut with `…` | `source=defaults`, the error |
| a stray FIFO `config/queue` and a dangling symlink `config/prompts/x.md` | 422 `config/prompts/x.md: unknown prompt file: …` (the FIFO is never opened) | `source=defaults`, the error |
| `tuning.yaml` + an unknown key `"a\0b\e[31m\uD800"`; `music.yaml` with `timeout_s: "\uD800"` | orchestrator 422 `config/tuning.yaml: (top level): Input should be a valid string, unable to parse raw data as a unicode string (got 'a\x00b\x1b[31m\ud800')` (no line: observation 4); tool_service 422 `config/music.yaml:19: verify.timeout_s: Input should be a number (got '\ud800')` | both `source=defaults`, the same errors |
| `tuning.yaml` + 6,000 unknown keys | 422, 50 errors with lines and a count; script 0.28 s | `source=defaults`, the errors |

Every odd name was created by my script and removed with
`rm -f -- "${F:?}"`, `F` built by `printf` from octal escapes; the FIFOs
and symlinks the same way.

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
== POST through nginx, dev prefix only (curl --path-as-is); production's root path not requested
/dev/config/reload                 403 {"detail":"/config/reload is not served through nginx; call it from the host (scripts/config_reload.sh) or the compose network"}
/dev//config/reload                403      /dev/./config/reload          403
/dev/x/../config/reload            403      /dev/%63onfig/reload          403
/dev/config%2Freload               403      /dev/config/reload?x=1        403
/dev/static/../config/reload       403      /dev/voice/../config/reload   403
/dev/config/./reload               403      /dev/api/../config/reload     403
/dev/v1/../config/reload           403      /dev/config/reload#x          403
/dev/config/reload/                307      /dev/CONFIG/reload            404
/dev/voice/config/reload           404      /dev/config/reload;x          404
/dev/config/reload%20              404      /dev/api/config/reload        404
/dev/config/reload%00              400 (nginx's own Bad Request)
== GET through nginx
/dev/config/status                 404
/dev/config/reload                 405
/dev/api/config                    200
== straight at the handler, inside orchestrator-dev (header checks)
   X-Real-IP: 192.0.2.9 -> 403
   X-Forwarded-For: 192.0.2.9 -> 403
   x-real-ip: ::1 -> 403
   X-Real-IP: (empty) -> 200
   Forwarded: for=192.0.2.9 -> 200
   no header -> 200
   GET -> 405
   PUT -> 405
   DELETE -> 405
   PATCH -> 405
   orchestrator routes containing config: [('/api/config', ['GET']), ('/config/reload', ['POST'])]
   tool_service routes containing config: [('/config/reload', ['POST']), ('/config/status', ['GET'])]
== published ports
kronk-orchestrator-dev-1:
kronk-tool_service-dev-1: 8103/tcp -> 127.0.0.1:8103
```

(Folded two to a row; every 403 carried the same `detail`.)

**nginx rule: not tested live.** nginx was not restarted by the build or by
either fix round: the running nginx started 03:30Z and mounts
`kronk-releases/sprint-M2/nginx/nginx.conf`, which differs from the tree's
by exactly the comment and the two deny lines (`diff` of the file inside
the container against the tree). As the brief directs for that case, no
request went to production's root path, and the handler-level refusal was
tested on dev (above). `nginx/` is unchanged since the first test. In a
throwaway `nginx:1.29-alpine` container with `--network none` and the
tree's config (`nginx -t` successful), POST from inside it:

```
/config/reload 404   /config/ 404   /config/x/y 404   /dev/config/reload 404   /dev/config/ 404
/dev//config/reload 404   /dev/x/../config/reload 404   /dev/./config/reload 404
/dev/%63onfig/reload 404  /config%2Freload 404   /dev/config%2Freload 404
/x/../config/reload 404   //config/reload 404
/config 502   /CONFIG/reload 502   /dev/config 502   /api/status 502   (502 = handed to an upstream; none there)
/api/solar/../config/reload 502   upstream: "http://127.0.0.1:8000/api/solar/../config/reload"
/api/solar/..%2Fconfig/reload 502 upstream: "http://127.0.0.1:8000/api/solar/..%2Fconfig/reload"
```

So the rule answers the reload path itself in every spelling, and a path
that climbs out of `/api/solar/` goes to the orchestrator's port (8000),
not tool_service's: tool_service's `/config/reload` cannot be reached
through nginx.

**Concurrent reloads, live on dev.** 64 reloads on each dev service from
eight parallel workers on the compose network, while one chat request ran
and `coordinator.md` was swapped (atomic rename) four times between two
valid texts:

```
chat finished in 3.8s: 345 chars: 'Tea is made by steeping dried tea leaves in hot water. The brewing process allows the flav'
file flips (atomic rename) during the bursts: 4
orchestrator reload answers: 64; codes {200: 64}
     60  []
      2  ["prompts/coordinator.md: changed (2339 -> 2348 chars)"]
      2  ["prompts/coordinator.md: changed (2348 -> 2339 chars)"]
tool_service reload answers: 64; codes {200: 64}
     64  []
   restored config/ (9 files, sha256 equal to the copy; git status clean for config/)
final reload: 0 == orchestrator (kronk-orchestrator-dev-1) | 200 {"ok":true,"changed":[]} | == tool_service (127.0.0.1:8103) | 200 {"ok":true,"changed":[]}
```

Each of the four transitions was reported exactly once; every answer was
200.

**A reload during a running request** (first-test finding 4; a step 2
requirement by ruling, not counted). Re-measured in scratch, unchanged:

```
reload during round 2: {"ok": true, "changed": ["prompts/research.md: changed (1127 -> 1143 chars)", "tuning.yaml:agent_loop.max_rounds.research: 8 -> 2", "tuning.yaml:agent_loop.repeat_tool_nudge_at: 3 -> 2", "tuning.yaml:tool_timeouts.default_s: 15.0 -> 99.0"]}
  LLM calls made: 9 (old budget 8 -> 9 calls with the forced synthesis; new budget 2 -> 3)
  prompt marker seen in any round: False | every round's prompt still says 'budget of 8': True
  tool timeouts in effect per call: [15.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0]
  repeat-call nudges present by round: [0, 0, 1, 2, 3, 4, 5, 6, 7]
```

The request keeps the old prompt and budget and takes the new nudge
threshold and timeout from its second tool call on. The next request has
the new prompt and makes 3 calls. The feature doc still does not say so.

## 6. Secrets (criterion 5)

My own scan of the nine files in `config/` (no symlinks, no hidden files):

- names: every key in `.env`, `.env.dev`, `.env.telemetry` and the
  `.example` files, plus every `${NAME}` in the compose files (28 names),
  as whole words: no hit;
- values: every value of six or more characters from `.env`, `.env.dev`
  and `.env.telemetry` (19 values), searched as fixed strings without
  printing them: one hit, `TALKIE_MODEL` from `.env.dev`, whose value is
  the word "talkie". Not a secret;
- shapes: JWT, 32+ hex, 40+ base64, `sk-`/`pk-`, GitHub, AWS and Slack
  token prefixes, PEM headers, URLs and URL credentials, IPv4 addresses,
  e-mail addresses, and `password|passwd|secret|token|api_key|bearer|login|
  username|credential|auth` followed by `:` or `=`: no hit;
- words of 22 or more characters: three file paths and
  `remember_music_preference`. Identifiers. No word of 16 or more
  characters mixes upper case, lower case and digits.

## 7. Tests, mutation check, replay

```
$ ./scripts/run_tests.sh -q
3003 passed, 2 skipped, 18 xfailed, 6 warnings in 22.16s
```

The claimed count. The fix commits, each in a scratch extract with every
INET socket call and name lookup refused and logged:

```
13128e9 M21 fix 1: no config/ file can take a loader down | suite: 2934 passed, 2 skipped, 18 xfailed, 6 warnings in 21.20s | pins: 6 passed, 3 warnings in 0.81s
b4bc98f M21 fix 1: document the bounds and dialect; complete the inv | suite: 2934 passed, 2 skipped, 18 xfailed, 6 warnings in 21.22s | pins: 6 passed, 3 warnings in 0.83s
efcb5c8 M21 fix 2: config text is always safe to encode and log | suite: 3003 passed, 2 skipped, 18 xfailed, 6 warnings in 23.04s | pins: 6 passed, 3 warnings in 0.79s
d622655 M21 fix 2: feature doc and inventory addendum | suite: 3003 passed, 2 skipped, 18 xfailed, 6 warnings in 23.03s | pins: 6 passed, 3 warnings in 0.80s
ecd006c M21 fix 2: feature doc example is real output | suite: 3003 passed, 2 skipped, 18 xfailed, 6 warnings in 23.03s | pins: 6 passed, 3 warnings in 0.78s
```

**Network attempts (first-test finding 6, recorded by ruling).** Under the
guard the suite passes at HEAD and the guard refused 34 attempts in one
run: 32 `getaddrinfo` (25 of the name `fake-tools`, 7 of three news feed
hosts), one `getaddrinfo('localhost', 8123)`, one `bind(('::1', 0))` at
import. None from `test_config_loader.py`, `test_tool_service_config.py`,
`test_m21_pins.py`, `test_config_hardening.py` or `test_config_fix2.py`.
My one plain `./scripts/run_tests.sh` has no guard, so it made those
lookups for real.

**Mutation check.** 76 single edits in eight scratch copies of HEAD, the
whole suite after each (`-x`), under the same guard. An edit to
`config_files.py` was applied to both copies of the file. No mutant opened
a socket: 1,126 attempts over the 76 runs were all refused (1,050 lookups,
76 binds of `::1` at import), none from the five M21 test files. 65 killed,
11 survived:

| Survivor | Edit | What it means |
|---|---|---|
| r3 | mtime dropped from the changed-during-read check | the tests change the size; a same-size rewrite during the read is not tested |
| r4 | `O_NOFOLLOW` dropped from the open | survived the first retest too: the second line of defence (a file swapped after the `lstat` check) has no test |
| y6 | the duplicate-key message not made safe before it is composed | equivalent in outcome: the `Problem` makes it safe when it is built |
| y9 | the orchestrator's "changed" lines not made safe | equivalent in outcome today: every part of a line is already a quoted value or a number |
| y11 | the orchestrator's `context_mark` fallback for the line removed | no test feeds a YAML error that has only a context mark |
| v12 | tool_service: a startup rejection is not logged | criterion 10's "and in the logs" is tested only for the orchestrator's startup (`v11` is killed). Live, the lines are there (section 4) |
| v13 | orchestrator: a reload rejection is not logged | the same gap |
| v14 | tool_service: a reload rejection is not logged | the same gap |
| v15 | orchestrator: the startup rejection is not repeated by `main._log_config_status` | the same gap; this is the line that reaches the container log, since the first load runs before logging is set up |
| a5 | the repeat-call nudge threshold read from a literal 3 | no test changes `repeat_tool_nudge_at` and looks at a transcript; the pins hold only the value |
| a8 | `config_loader.start()` removed from tool_service's lifespan | survived both earlier tests: nothing tests that tool_service loads at startup rather than at first use |

Killed, among others: `safe()` no longer escaping surrogates, control or
format characters, or no longer capping; a `Problem`'s file or message not
made safe, in either service; `renderable()` not catching; each of the four
endpoints not passed through `render_reload`/`render_status`; a file
changed during the read accepted, and no second read; the `stat` size check
and the symlink check removed; `check_yaml_text` skipped in either service;
U+2028 accepted; ruamel back in YAML 1.2 mode; an empty key accepted; key
names not shortened; a too-long integer losing its line; the size cap
doubled; the nesting limit raised by one; anchors or duplicate keys
accepted; types coerced or unknown keys ignored in either service; a
rejected reload falling back to the defaults in either service; a
rejection's errors left off the status, at startup or on reload, in either
service; the env override ignored; an unknown `prompts/*.md` downgraded to
a warning; a prompt without a header or an empty prompt accepted; the
placeholder not filled; per-agent budgets not applied; the weather age, the
tool timeout, the verify window or the resolver thresholds read from a
literal; the reload refusal removed or weakened; either nginx deny line
removed; a mount made read-write; one character changed in a prompt file or
a default prompt; one number changed in either YAML file; a secret's name
or a JWT planted in `config/`; the pytest retention setting or the
`conftest` cleanup removed.

**Replay.** One run, to the end; no row was skipped for player activity and
the players were unchanged. It ran before any of my edits to `config/`, on
dev as fix round 2 left it. Exit status 1, for the one failed row and the
one xpass, both known flappers.

```
$ python3 scripts/voice_replay.py --label m21-e2e3
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  . pass  checked    plan-anime-pop-radio-wet
  . pass  checked    inc-1108-anime-pop-transcript
  . pass  checked    inc-1108-anime-pop-wet-basement
  . pass  checked    inc-1108-mushroom-jazz-basement
  . pass  checked    inc-1108-artist-portishead-basement
  . pass  checked    inc-1108-pause-basement-player
  . pass  checked    inc-1108-timer-2min
  . pass  executed   plan-orbital-station-youtube
  . pass  executed   inc-1004-tell-kronk-orbital
  . pass  checked    inc-1004-play-orbital-radio
  x xfail executed   plan-like-massive-attack-radio
  x xfail executed   inc-1004-well-play-orbital
  . pass  checked    inc-1004-french-dinner-pandora
  . pass  checked    inc-1004-french-dinner
  . pass  checked    inc-1004-french-cafe-pandora
  . pass  checked    plan-girl-with-the-sun
  . pass  checked    plan-shuffle-anime-bangers
  . pass  checked    plan-put-on-some-jazz
  . pass  executed   plan-stop-bang
  . pass  executed   inc-0910-stop-transcript
  . pass  checked    plan-ninetys-two-thousands
  x xfail executed   plan-mishearing-pasta
  x xfail executed   plan-mishearing-whats-up
  . pass  checked    inc-0915-daisies-album-eels-ytm
  . pass  checked    inc-0915-pause-kitchen
  . pass  checked    inc-0915-skip-kitchen
  x xfail checked    inc-0915-who-is-this
  F fail  executed   inc-0930-search-deep-house-ytm  [tool ['coordinator:ask_research', 'research:web_search']]
  . pass  executed   inc-1019-thumbs-up-dot
  . pass  executed   inc-1019-thumbs-up
  . pass  executed   inc-1019-thumbs-down-dot
  . pass  checked    inc-1019-destiny-waving
  . pass  checked    inc-1019-destiny-waving-ytm-kitchen
  . pass  checked    inc-1017-hotel-costes-albums-atlas
  x xfail executed   inc-1017-pay-the-album-hotel-costes-3
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  x xfail executed   inc-1021-ninetys-no-verb
  . pass  checked    inc-1021-ninetys-from-pandora
  . pass  checked    inc-1021-woodstock-from-pandora
  . pass  checked    inc-1021-shuffle-old-man-sings-basement
  . pass  checked    inc-1025-ninetys-quoted-kitchen
  . pass  checked    m17-play-some-jazz-office
  . pass  checked    m11-some-deep-house-chill
  . pass  checked    m11-italian-music
  . pass  checked    m11-deep-house
  . pass  checked    m11-portishead
  . pass  checked    m11-play-the-news
  . pass  checked    eval-play-top-pink-floyd-songs-on-the
  . pass  checked    eval-play-k-pop-radio-on-sonos-move
  . pass  executed   eval-shuffle-the-anime-bangers-playlist-on-sonos
  . pass  checked    eval-play-music-by-pink-floyd
  . pass  checked    eval-play-pink-floyd-on-sonos-move
  . pass  checked    eval-play-some-jazz-on-the-sonos-move
  . pass  checked    eval-play-the-beatles-in-the-kitchen
  . pass  checked    eval-put-on-some-mellow-pink-floyd-in
  . pass  executed   eval-pause-sonos-move-dirt
  . pass  checked    eval-play-albuquerque-by-weird-al-yankovic
  . pass  checked    eval-play-top-songs-by-weird-al-yankovic
  . pass  checked    eval-play-journey-on-the-sonos
  . pass  checked    eval-play-pink-floyd-on-the-blue-group
  . pass  checked    eval-play-pink-floyd-on-the-sonos-move
  . pass  executed   eval-stop-sonos-move-derp
  . pass  checked    eval-play-don-t-stop-me-now-by
  . pass  checked    eval-play-the-artist-aerosmith-in-the-office
  . pass  checked    eval-play-toys-in-the-attic-by-aerosmith
  x xfail executed   eval-played-the-album-toys-in-the-attic
  . pass  executed   eval-resume-the-music
  . pass  checked    eval-shuffle-my-youtube-music-playlist-anime-bankers
  . pass  checked    eval-shuffle-my-youtube-playlist-old-man-video
  . pass  checked    eval-shuffle-my-youtube-playlist-anime-bangers
  . pass  checked    eval-shuffle-the-youtube-music-playlist-anime-bangers
  . pass  checked    eval-play-some-aerosmith-please
  . pass  checked    eval-put-on-some-aerosmith
  x xfail executed   eval-oh-play-portishead-radio
  . pass  checked    eval-put-on-the-radio-station-zorblax-fnordwave
  . pass  executed   eval-stop
  . pass  checked    eval-pause
  . pass  executed   eval-resume-the-music-2
  . pass  checked    eval-skip-this-song
  x xfail executed   eval-i-have-to-play-the-radio-station
  x xfail executed   eval-stop-stop
  . pass  executed   eval-what-song-is-this
  . pass  executed   eval-what-s-playing
  . pass  checked    eval-play-groove-armada-radio-from-pandora-in
  . pass  executed   eval-stop-2
  . pass  executed   eval-what-song-is-this-2
  . pass  checked    eval-play-the-albo-daisies-of-the-galaxy
  . pass  checked    eval-shuffle-my-youtube-playlist-video-game-pretty
  . pass  checked    eval-shuffle-the-youtube-playlist-old-man-video
  x xfail executed   eval-skip-the-current-song
  . pass  executed   eval-skip-treat-me-my-wallet-son
  . pass  executed   eval-turn-it-down
  x xfail executed   eval-volume-50
  . pass  executed   eval-what-song-is-this-3
  x xfail executed   eval-i-shuffled-my-youtube-playlist-old-man
  x xfail executed   eval-late-evening-from-radio-on-pandora
  . pass  checked    eval-skip
  x xfail executed   eval-j-envoie-my-youtube-playlist-video-game
  X xpass executed   eval-shuffle-it  []
  . pass  checked    eval-play-deep-chill-house-radio-from-pandora
  x xfail executed   eval-clay-thief-recreperation-radio-on-pandora
  . pass  checked    eval-skip-2
  . pass  executed   eval-look-for-some-good-deep-house-chill
  . pass  executed   eval-search-youtube-music-for-some-deep-chill
  . pass  checked    eval-play-lunchlady-land-by-adam-sandler-in
  . pass  checked    eval-play-lunchlady-land-by-adam-sandler-in-2
  . pass  checked    eval-play-the-song-lunchlady-land-by-adam
  . pass  executed   eval-what-song-is-playing-in-the-kitchen
  . pass  executed   eval-what-s-playing-on-the-kitchen
  . pass  executed   eval-what-s-playing-on-the-kitchen-2
  x xfail executed   eval-i-hope-you-play-anime-pop-radio
  x xfail executed   eval-we-play-justin-johnson-radio-on-pandora
  . pass  executed   eval-start-a-zzqx-florbent-radio-station-on
  . pass  executed   m4b-update-the-magicmirror
  . pass  executed   m4b-okay-update-the-magic-mirror-please
  . pass  executed   m4b-update-the-mirror
  . pass  executed   m4b-update-magic-mirror
  . pass  executed   m4b-upgrade-the-magic-mirror
  . pass  executed   m4b-run-the-mirror-update
  . pass  executed   m4b-upgrade-the-mirror-latest
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: {'pass': 98, 'xfail': 19, 'fail': 1, 'skip': 3, 'xpass': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_063949_m21-e2e3.json
exit=1
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`, row
by row: 113 of the 114 baseline rows have the baseline's status; none was
not run. The one that differs is `eval-shuffle-it`, xfail → xpass, a known
flapper named in the brief. `inc-0930-search-deep-house-ytm` failed, as it
does in the baseline (a known "YouTube Music" search flapper; it passed in
the fix round's replay). The 8 rows added since M10 (`m4b-*`): 7 pass, 1
skip. The seven mirror-update rows reach dev's own `/magicmirror/update`,
and `tool_service-dev` has no `MM_*` variable, no `/keys` mount and no
`ops/hosts.json`, so nothing was sent to the MagicMirror Pi.

## 8. Findings

No finding fails a criterion.

**The earlier findings, state after fix round 2.**

| Finding | State |
|---|---|
| First test 1. Deep nesting crashes startup, 500 on reload | fixed: scratch and live, startup and reload (sections 3a, 4b) |
| First test 2. Alias bomb not rejected; error message as large as the file | fixed: refused at the first anchor; longest message in 1,124 scratch loads 158 characters |
| First test 3. A large file blocks the service for the length of the reload | fixed: refused from `stat`; live 0.10 s where it took 27.83 s; slowest load of any file within the cap 0.29 s |
| First test 4. A reload during a running request mixes old and new values | unchanged; a step 2 requirement by ruling (section 5) |
| First test 5 and first retest 2. The inventory misses values, then two classes | closed (below) |
| First test 6. The suite makes network attempts | unchanged; recorded by ruling (section 7) |
| First test 7. Test gaps shown by the mutation check | the two that mattered stay closed (`v5`, `v6` killed); section 7 lists what is left |
| First retest 1. A name or key that is not valid UTF-8 text turns the reload answer and the `/services` data into a 500 | fixed: all four inputs answer 200 or 422 with the error on `/dev/api/config`, on reload and after a restart (section 4b); in scratch no answer of 1,124 loads and 60,000 fuzzed reloads needed the fallback |

**The inventory addendum (extra scope d).** Checked by search, not by
reading the rows alone:

- *wiring and on/off env vars in `docker-compose.yml`*: I parsed every name
  set under an `environment:` block (42 names, the number the addendum
  states) and searched the build report for each as a whole word: all 42
  are named. The two dev services also read `.env.dev` through `env_file`;
  its 12 names are all named in the report;
- *tool-result sentences the model reads back*: `tool_service/main.py` has
  60 `HTTPException(` sites, and the row lists 60 line numbers: the two
  sets are equal. `orchestrator/tools.py` has 38
  `failure(…)`/`_fail(…)`/`ToolResult(…)` sites between lines 507 and 1058,
  all inside the ranges of the ten rows for that file. The three named
  constants and the three sentence helpers are named, with their use sites.

Every row says "left where it is" and why; none says moved, and none was:
`git diff 6f5487d HEAD` touches no prompt, no value and no fixture.

## 9. Observations

1. **A reload in the middle of a save written in pieces can load the first
   part of the file, with no warning.** Section 3c: a writer that rewrites
   `tuning.yaml` in place in five writes, against a reload loop, gave 33
   reloads of 6,391 that answered `ok` with the new file's research budget
   and the built-in `default_s`, because the part of the file read so far
   was a valid file and nothing changed during that read. Values from the
   part not yet written fall to their built-in defaults and nothing says
   so. Fix round 2's read-twice check catches a file that changes during
   the read (2,945 clean 422s in the same run) and cannot catch a file that
   is steady between two writes. With a single `write` the only third state
   is the empty file, which does carry the warning `all defaults (file has
   no keys)`; with an atomic rename there is none. By hand this needs a
   reload sent while a save is in progress. Step 2's `/settings` page writes
   and reloads; the ruling already makes atomic writing its requirement.
   The feature doc ("A file saved while it is read") describes only the
   empty-file case.
2. **A key written with a null value is not "left out".** `research: ~` (or
   `research:` with nothing after it) under `max_rounds` gives the research
   agent the `default` budget, 3, not its built-in 8; `play_music:` with
   nothing after it under `per_tool` gives 15 s, not 20. The reload reports
   it (`max_rounds.research: 8 -> None`). The file header and the feature
   doc say "a key left out takes its built-in default" and do not say what
   an empty value does.
3. **An emptied YAML file is valid, with a warning** (`all defaults (file
   has no keys)`), as since fix round 1. `/services` shows it.
4. **A few errors carry no line.** An unknown key holding a lone surrogate
   gives `config/tuning.yaml: (top level): Input should be a valid string,
   unable to parse raw data as a unicode string (got 'a\x00b\x1b[31m\ud800')`,
   file named, no line, and a message that does not say "unknown key"
   (section 4d). Whole-file problems (`too large`, `cannot be read`, `file
   is missing`) have no line by nature.
5. **Accepted in a prompt, and worth knowing.** A body of one zero-width
   space (U+200B) passes the empty-prompt check; no-break spaces alone do
   not. U+2028, a bidi override and zero-width characters in the body are
   accepted. A prompt of 65,536 bytes is accepted and is larger than the
   model's context.
6. **Timeout type.** From the files the per-tool timeout reaches httpx as a
   float (`15.0`) where the old constant was an int (`15`); from the
   built-in defaults it is still an int, and after an env override a float
   again. It goes only to `httpx.AsyncClient(timeout=…)`. The only
   differing leaves in section 2. Unchanged since the first test.
7. **The handler refusal keys on a non-empty header.** `X-Real-IP` with an
   empty value passes; nginx always sets it non-empty. `/dev/config/reload/`
   (trailing slash) is answered by the app with a 307. Both unchanged since
   the first test.
8. **`1e2` is a number here.** The loader's dialect reads `1e2` as 100.0
   where YAML 1.1 (PyYAML's own resolver) reads a string. It is what the
   feature doc says ("decimal floats"); the only disagreement with an
   independent reading in 37,741 accepted files.
9. **Production's nginx and the promotion step.** The deny rule and the
   `/config` mount reach production only at promotion, as the build report
   says. Until then production has no `/config/reload` at all.

## 10. What I changed temporarily, and reverted

- `config/` in the working tree, on dev only: the edits of sections 4 and
  5, each restored from a byte copy taken before the first edit and checked
  against `git show HEAD:config/…`. At the end the nine files are
  byte-identical to HEAD in the working tree and in `/config` inside both
  dev containers (sha256 compared), all mode 664 as found, `/config` holds
  its 11 entries, there is no symlink, FIFO or extra entry in `config/`,
  and `git status` shows only the files of this report.
- `docker restart` by name: `kronk-orchestrator-dev-1` 19 times,
  `kronk-tool_service-dev-1` 8 times (sections 4b, 4c, 4d). No rebuild. No
  other container was restarted, stopped or reconfigured.
- One throwaway `nginx:1.29-alpine` container, `--network none --rm`, twice
  (the first try failed its own config test for a missing host mapping and
  proved nothing); gone.
- At the end, checked at 07:15Z: both dev containers up and healthy,
  running the working tree's code (md5 rechecked) and the committed
  `config/`, both `ok=True source=files` with no errors or warnings, the
  shim path answering (`/dev/api/chat` → "Paris."); `tool_service-dev`
  without `/keys` and without `MM_*`; `ops/` with only
  `hosts.example.json`; production's containers and the host units as in
  row 20.

Not tested: the nginx deny rule in the running nginx (not loaded);
production's `/config/reload` in any form (not deployed, and out of
bounds); the reload path through `/api/solar/` against production (the
throwaway container shows where nginx sends it).

Departures and leftovers I should own:

- Scratch set-up and tear-down inside my scripts used `os.unlink`,
  `os.rmdir` and a small non-following remover confined to my scratch
  directory (for the cases that replace a copied directory), not `rm -f`.
  The final clean-up of the scratch directory was `rm -f -- "${F:?}"` on
  each file and `rmdir` on each directory; one file under a directory that
  was mode 000 when the list was made was removed the same way in a second
  pass. Everything in `config/` was removed with `rm -f -- "${F:?}"`.
- The scratch extracts of the repo held the repo's own tracked symlinks
  (`scripts/lib`, one to `/usr/bin/gnutimeout`); my test symlinks pointed
  at scratch files, at themselves or at `/nonexistent`. None pointed at
  repo data or secrets. All removed.
- No file over 64 MB was made (the largest: 50 MB).
- My first run of the criterion 18 script stopped at its first request on
  a wrong table name in my read of `metrics.db`; nothing had been edited,
  and its clean-up reloaded an unchanged `config/`. The second run is the
  one in section 4a.
- One plain `./scripts/run_tests.sh`, as the brief asks, made the name
  lookups of first-test finding 6 for real. It left nothing in `/tmp`: no
  new `kronk-taste-*` directory and no FIFO, so fix round 2's cleanup
  works. All my other Python runs had `TMPDIR` inside the scratch
  directory.
- No secret value was printed or written: environment listings were cut to
  names, and the value scan printed only key names.
- No safety control stopped a test.
