# M21 step 1, fix round 1: files that make a parser choke

Fix agent, 2026-11-17 (UTC), branch `refactor`, on top of `d37d2cc`.
Inputs: `docs/sprint/M21-step1-criteria.md`, `docs/sprint/M21-step1-build.md`,
`docs/sprint/M21-step1-e2e.md` (findings 1, 2, 3 and 5; observations 2 to 6
and 10). Commits, in order: `13128e9` (code and tests), `b4bc98f` (feature doc; inventory addendum in the build report), and the commit that adds this report.

## The class, and how each way out is closed

The class: any way a file under `config/` can make a loader raise, hang or
exhaust memory outside the loader's own error path. The e2e test found
three members (a parser `RecursionError` on deep nesting, an alias bomb's
`MemoryError`, and an unbounded read that held the event loop for 28 s).
The fix closes the class at three layers, so a fourth member has to get
past all three.

**Layer 1: bound the input before parsing.** A new module,
`config_files.py`, is byte-identical in `orchestrator/` and
`tool_service/` (each image builds from its own directory, so it is copied;
`test_the_shared_module_is_byte_identical_in_both_services` fails if the
copies drift). Line numbers below are the same in both copies.

| Way out | Closed by |
|---|---|
| A file too large to read in time or memory (finding 3) | `read_text` checks `st_size` from `lstat` against the cap before opening (`config_files.py:122`), then reads at most cap + 1 bytes (`:132`) so a file that grows between the check and the read is still refused (`:143`) |
| A pipe, device or directory in place of a file (a FIFO blocks `read` forever) | `lstat` must say regular file (`:120`); the descriptor is opened `O_NONBLOCK` and re-checked with `fstat` (`:125`, `:129`), so a swap after the check cannot block |
| A symlink, to anywhere (observation 3) | every path component under the config directory is `lstat`ed and a symlink is an error (`:109-117`); the open uses `O_NOFOLLOW` (`:125`). The config directory itself may be a link (a release may link it); only what is under it is checked |
| Deep nesting (finding 1) | `build()` consumes the library's parse events in a loop with an explicit stack, never recursing, and stops at the 11th nested collection (`:309`) before the parser reads further. The libraries' scanners and parsers are iterative; their composers and constructors, which recursed, are no longer used |
| An alias bomb (finding 2) | anchors (`:287`), aliases (`:285`) and merge keys (`:297`) are errors at the first one; nothing is ever expanded. Explicit tags (`!!int "9"`, `!!python/...`) are errors too (`:289`) |
| An error message as large as the file (finding 2, second half) | a rejected value is quoted to 80 characters (`quote`, `:76`), parser messages are shortened, and a load reports at most 50 problems plus a count (`cap_problems`, `:87`) |
| A slow validation path | tool_service's problem de-duplication was a list scan, O(n²): 0.83 s for a 64 KiB file of 5,900 unknown keys. Now a set (`tool_service/config_loader.py:188`) |

**Layer 2: any exception at all is a config error for that file.** Each
file's load is wrapped in `except Exception` (which includes
`RecursionError` and `MemoryError`) and becomes a `Problem` naming that
file: prompts at `orchestrator/config_loader.py:367`, `tuning.yaml` at
`:377`, the stray scan at `:384`, `music.yaml` at
`tool_service/config_loader.py:205`. Around the whole load, `_try_load`
(`orchestrator/config_loader.py:398`, `tool_service/config_loader.py:212`)
turns anything left into a `(loader)` problem. `start()` and `reload()` go
only through `_try_load`, so `start()` never raises (startup runs on the
built-in defaults with the error on `/services` and in the log) and
`reload()` always returns 200 or 422 with the last good values kept.

**Layer 3: the event loop is held for a bounded time.** `reload()` still
runs synchronously inside the `async` handler, as before (that is what
serialises concurrent reloads). Under the 64 KiB cap the slowest shape
measured parses in 0.32 s (table below), so that is the longest a reload
can hold either service; the 50 MB file that held dev for 28 s is now
refused from `stat` in about a millisecond.

### Also fixed in passing

- `orchestrator/main.py:37` `_log_config_status`: the startup re-log
  printed `config/tuning.yaml:None: too large …` when a problem had no
  line (seen live on dev in this round), and dropped warnings. It now
  prints `file:line` only when there is a line, and logs warnings.

## The caps, and why

| Cap | Value | Why |
|---|---|---|
| YAML file size | 64 KiB | The YAML files are 2.5 and 2.8 kB; 64 KiB is over twenty times that. The slowest YAML shape at 64 KiB parses in 0.32 s, the bound on a reload's hold of the event loop |
| Prompt file size | 64 KiB | The largest prompt is 4.2 kB (`home.md`). 64 KiB is about 16k tokens, half of gemma-4-e4b's 32k context: a larger prompt could not work as a system prompt anyway |
| Nesting | 10 collections | The deepest key today is three mappings (`tool_timeouts.per_tool.<tool>`). PyYAML's composer failed at about 500 and ruamel's at about 300 (e2e finding 1), so 10 is far below both |
| Quoted value in an error | 80 characters | Enough to recognise the value; the e2e test saw a 25 MB message from a 256-byte file |
| Problems per load | 50, plus "and N more problems, not shown" | One file of thousands of unknown keys must not become thousands of log lines and a megabyte of `/services` JSON |
| Stray scan | 500 entries per directory, 20 warnings | A directory dumped into `config/` by mistake stays one short report |

The caps live in `config_files.py`, not in `config/`: a cap the file could
raise is no cap.

Measured parse-and-validate time for 64 KiB files (scratch, the tests'
venv; each shape is rejected, so this includes building the error list):

| Shape (64 KiB) | orchestrator (PyYAML) | tool_service (ruamel) |
|---|---|---|
| 5,900 `kNNNNN: 1` lines | 0.14 s | 0.18 s (0.83 s before the set fix) |
| one flow mapping of 5,400 keys | 0.09 s | 0.12 s |
| a flow list of 32,000 items | 0.23 s | 0.28 s |
| one 64 KiB quoted string | 0.01 s | 0.01 s |
| 3,400 lists nested 9 deep | 0.25 s | 0.32 s |
| 65,000 unclosed `[` | 0.06 s | 0.08 s |
| 32,000 comment lines | 0.02 s | 0.02 s |
| a block list of 16,000 items | 0.12 s | 0.14 s |

`test_hostile_yaml_within_the_cap_parses_fast` runs seven of these shapes
on both loaders and requires each under 1 s.

## The dialect decision (observation 4)

Neither library is in both images (`pyyaml==6.0.2` only in the
orchestrator, `ruamel.yaml==0.19.1` only in tool_service; checked inside
the dev containers), and adding one would be a dependency change. So both
libraries stay, and neither decides what a value means. Each loader asks
its library only for parse events (`yaml.parse(text, Loader=SafeLoader)`
at `orchestrator/config_loader.py:287`, `YAML(typ="safe",
pure=True).parse(text)` at `tool_service/config_loader.py:152`); the two
emit the same event stream, and the shared
`build()` turns events into values with one resolver (`resolve`,
`config_files.py:208`):

- plain `null`, `Null`, `NULL`, `~` and empty: null;
- plain `true`/`false` (and their `True`/`TRUE` forms): booleans;
- plain decimal integers without leading zeros: `int`;
- plain decimal floats (`1.5`, `.5`, `5.`, `1e3`): `float`;
- everything else, and every quoted or block scalar: a string.

So `2:00:00`, `0o17`, `017`, `0x1f`, `7_200`, `1,000`, `yes`, `no`, `on`,
`off`, `.inf` and `.nan` reach the schema as strings, and every numeric
field (strict) rejects them with file and line. Mapping keys are never
resolved (they are names). Duplicate keys are found by `build()` in both
services, with the orchestrator's wording; the one existing test that
expected ruamel's wording (`test_yaml_syntax_error_and_duplicate_key_name_the_line`
in `tests/test_tool_service_config.py`) now asserts
`duplicate key verify.timeout_s` at line 3. Syntax errors still come from
each library's scanner, as `not valid YAML: <its message>` with its line.
`test_both_services_read_yaml_the_same_way` feeds 60 documents (the
surprises above, nesting, flow and block forms, duplicates, anchors,
tags, multiple documents, syntax errors, tabs) to both loaders and
requires the same data, lines and errors.

No shipped file changes meaning: both YAML files use only plain decimal
integers and floats, and the pins (`tests/test_m21_pins.py`, untouched)
pass.

## Observations 2, 5, 6 and 10

- **Observation 2, a YAML file with no keys.** Empty, whitespace,
  comments only, `~` or a bare `---`: it loads (`source files`, every value
  at its default) with the warning `all defaults (file has no keys)` on
  `/api/config` (and the `/services` card, whose badge then says
  `warning`) and in the reload answer (`"warnings": [...]`). Not an error.
  `orchestrator/config_loader.py:374`, `tool_service/config_loader.py:207`.
- **Observation 5, stray files.** The orchestrator scans `config/` and
  `config/prompts/` once for the whole directory (`_strays`,
  `orchestrator/config_loader.py:335`; `stray_entries`,
  `config_files.py:161`). Any entry no service reads is a warning: `not a
  file any service reads (a typo, or a leftover copy?); ignored`. It knows
  tool_service's `music.yaml` by name (`TOOL_SERVICE_FILES`, `:59`).
  tool_service does not scan, so a stray shows once on `/services`, under
  the orchestrator. An unknown `prompts/*.md` stays an error, as shipped:
  it looks like a prompt the operator expects to take effect, and erroring
  is louder than warning.
- **Observation 6, prompt bodies.** A body that is empty once `<!-- -->`
  comments are removed is `prompt is empty (only comments after the
  header)` (`orchestrator/config_loader.py:269`); the body sent to the
  model is unchanged (a comment inside a non-empty body is still sent, as
  before). Any Unicode control character other than newline and tab
  (NUL, BEL, ESC, DEL, form feed, C1 such as U+0085) is `control character
  U+XXXX is not allowed in a prompt (only newline and tab)` with its line
  (`:255`, `config_files.py:152`). CRLF still reads as LF.
- **Observation 10, `scripts/config_reload.sh`.** Prints `<HTTP code>
  <body>` for both services, on success and failure (`000` and the reason
  when a service does not answer). Exit status unchanged.

## Criterion 1: the inventory

`docs/sprint/M21-step1-build.md` has a new section, "Addendum, fix round
1", below the original report (which is unchanged): the values finding 5
names (the solar thresholds, `WEATHER_REFRESH_SEC`, the taste knobs,
`TEXT_PREVIEW_CHARS`) and what a fresh search of `orchestrator/` and
`tool_service/` found besides (news edition hours and feed list, per-call
HTTP timeouts in tool_service, the playback poll interval, the ops and
netguard bounds by name, the genre list and the "something else" words,
the status-probe timeouts, and the new caps themselves). Each row says
left where it is and why, or deferred to M11. No value was found half
moved, so none moved.

## Proof the new tests fail before the fix

`tests/test_config_hardening.py` (177 tests) was copied into a scratch
`git archive` of `d37d2cc` (the tree before this round, code untouched)
and run there under a 4 GB address-space limit (`ulimit -v 4000000`) so
the alias bomb could not take the host's memory:

```
$ (ulimit -v 4000000; tests/.venv/bin/python -m pytest tests/test_config_hardening.py -q --tb=line --junitxml=old.xml)
116 failed, 61 passed in 131.64s (0:02:11)
```

Per test, from the JUnit XML (the reason is the first line of the first
failure):

| Test (both loaders where it applies) | Fail on `d37d2cc` | Pass on `d37d2cc` | Why it fails there (first line of the failure) |
|---|---|---|---|
| `test_deeply_nested_yaml_is_a_config_error` | 2 | 0 | `RecursionError: maximum recursion depth exceeded` |
| `test_deeply_nested_yaml_on_reload_answers_422_not_500` | 2 | 0 | `RecursionError: maximum recursion depth exceeded` |
| `test_nesting_just_under_the_limit_is_an_ordinary_schema_error` | 0 | 2 | guard: passes before and after |
| `test_alias_bomb_is_refused_at_its_first_anchor` | 2 | 0 | `MemoryError` |
| `test_alias_bomb_on_reload_answers_422` | 2 | 0 | `MemoryError` |
| `test_a_single_harmless_anchor_and_alias_are_refused` | 2 | 0 | `AssertionError: assert 'zz_copy: unknown key' == 'anchors (&na... config files'` |
| `test_merge_key_is_refused` | 2 | 0 | `AssertionError: assert ('tuning.yaml...o 60 (got 9)') == ('tuning.yaml...config files')` |
| `test_explicit_tags_are_refused` | 8 | 0 | `assert (1 == 1 and False)` |
| `test_the_caps_are_what_the_report_says` | 1 | 0 | `AttributeError: module 'config_loader' has no attribute 'config_files'. Did you mean: 'config_dir'?` |
| `test_yaml_over_the_cap_is_refused_from_stat` | 2 | 0 | `Failed: DID NOT RAISE <class 'config_loader.ConfigInvalid'>` |
| `test_yaml_at_the_cap_still_loads` | 0 | 2 | guard: passes before and after |
| `test_huge_sparse_file_is_refused_without_reading_it` | 2 | 0 | `assert ('tuning.yaml' == 'tuning.yaml'` |
| `test_huge_file_on_reload_answers_at_once` | 2 | 0 | `AssertionError: {'errors': [{'file': 'config/tuning.yaml', 'line': None, 'message': 'not valid YAML:` |
| `test_prompt_over_the_cap_is_refused` | 1 | 0 | `Failed: DID NOT RAISE <class 'config_loader.ConfigInvalid'>` |
| `test_a_pipe_in_place_of_the_file_does_not_block` | 2 | 0 | `Failed: still running after 2s (blocked)` |
| `test_a_directory_in_place_of_the_file_is_an_error` | 2 | 0 | `assert ('tuning.yaml' == 'tuning.yaml'` |
| `test_symlink_to_a_valid_file_outside_is_refused` | 2 | 0 | `AssertionError: assert ('tuning.yaml...o 60 (got 9)') == ('tuning.yaml...ig directory')` |
| `test_symlink_inside_the_directory_is_refused_too` | 2 | 0 | `Failed: DID NOT RAISE <class 'config_loader.ConfigInvalid'>` |
| `test_symlinked_prompt_and_prompts_directory_are_refused` | 1 | 0 | `Failed: DID NOT RAISE <class 'config_loader.ConfigInvalid'>` |
| `test_any_exception_while_loading_a_file_is_a_config_error` | 6 | 0 | `RecursionError: maximum recursion depth exceeded` |
| `test_any_exception_while_loading_a_prompt_names_the_prompt` | 1 | 0 | `RecursionError: maximum recursion depth exceeded` |
| `test_a_failure_outside_any_file_still_leaves_the_service_up` | 2 | 0 | `RuntimeError: unforeseen` |
| `test_bad_reload_keeps_a_non_default_last_good_value` | 2 | 0 | `RecursionError: maximum recursion depth exceeded` |
| `test_a_long_rejected_value_is_quoted_short` | 2 | 0 | `assert (20067 < 250)` |
| `test_both_services_read_yaml_the_same_way` | 15 | 45 | `AssertionError: assert ('ok', {'a': ..., {('a',): 1}) == ('ok', {'a': ..., {('a',): 1})` |
| `test_yaml_1_1_surprises_stay_strings` | 10 | 6 | `AssertionError: ('config_loader', '2:00:00')` |
| `test_schema_rejects_yaml_1_1_numbers_and_booleans` | 12 | 4 | `AssertionError: assert False` |
| `test_the_shared_module_is_byte_identical_in_both_services` | 1 | 0 | `FileNotFoundError: [Errno 2] No such file or directory: '/tmp/claude-1000/-home-drew-git-repos-draws` |
| `test_tool_service_image_copies_the_shared_module` | 1 | 0 | `assert 'config_files.py' in 'FROM python:3.12-slim\n\n# openssh-client: the /magicmirror routes shel` |
| `test_hostile_yaml_within_the_cap_parses_fast` | 2 | 0 | `AssertionError: many keys` |
| `test_yaml_file_with_no_keys_loads_with_a_warning` | 10 | 0 | `KeyError: 'warnings'` |
| `test_shipped_files_load_with_no_warnings` | 2 | 0 | `KeyError: 'warnings'` |
| `test_stray_files_are_warnings_on_status_and_in_the_reload_answer` | 1 | 0 | `KeyError: 'warnings'` |
| `test_an_unknown_prompt_md_stays_an_error` | 0 | 1 | guard: passes before and after |
| `test_tool_service_does_not_report_strays_twice` | 1 | 0 | `AttributeError: 'Config' object has no attribute 'warnings'` |
| `test_prompt_with_only_comments_after_the_header_is_empty` | 1 | 0 | `Failed: DID NOT RAISE <class 'config_loader.ConfigInvalid'>` |
| `test_control_characters_in_a_prompt_are_errors` | 6 | 0 | `Failed: DID NOT RAISE <class 'config_loader.ConfigInvalid'>` |
| `test_tabs_and_crlf_in_a_prompt_are_fine` | 0 | 1 | guard: passes before and after |
| `test_config_reload_script_prints_the_http_code_of_both_services` | 2 | 0 | `AssertionError: == orchestrator (kronk-orchestrator-dev-1)` |
| `test_startup_log_names_file_and_line_only_when_there_is_a_line` | 1 | 0 | `AttributeError: module 'main' has no attribute '_log_config_status'` |
| `test_stray_scan_is_sorted_and_bounded` | 1 | 0 | `AttributeError: 'Config' object has no attribute 'warnings'` |

The 61 that pass on the old tree are guards, not bug claims: the depth
and size boundaries just inside the limits, an unknown `prompts/*.md`
staying an error, tabs and CRLF in a prompt, and the dialect rows where
the two libraries already agreed (45 of 60). The 15 dialect rows that
fail there are where PyYAML (YAML 1.1) and ruamel (1.2) disagreed:
`1e3`, `2:00:00`, `0o17`, `017`, `yes`, `no`, `on`, `off`, `.nan`, `=`,
`1: x`, `~: x`, a duplicate key, a complex key and a merge key. The
schema test fails on the old tree for `2:00:00`, `017`, `0x1f`, `1_0`,
`yes`, `on` and `.inf` in `tuning.yaml` (PyYAML accepted `2:00:00` as
7200 and `yes` as true) and for `0o17` (accepted as 15 by ruamel), `017`,
`0x1f`, `1_0` and `.inf` in `music.yaml`.

Two old-tree reasons need a word. `test_bad_reload_keeps_a_non_default_last_good_value`
is the guard for e2e mutants o6 and s4; it fails on the old tree only
because its bad file is the nested one (finding 1). `test_the_caps_are_what_the_report_says`
fails there because the caps did not exist.

On the fixed tree the file runs in about 3.5 s in all; the slowest test
is the hostile-shapes test, 0.8 s (orchestrator) and 1.0 s (tool_service) for seven shapes each. Every hostile
input is small or sparse: the alias bomb is 326 bytes and refused at its
first anchor, the nesting file is 40 kB and refused at depth 11, the
over-cap files are one byte past the cap or sparse (16 MB of holes,
refused from `stat` without a read).

## Live on dev

`docker inspect` first: `kronk-orchestrator-1` and `kronk-tool_service-1`
mount `kronk-releases/sprint-M2/…` and have no `/config` mount; only
`kronk-orchestrator-dev-1` and `kronk-tool_service-dev-1` mount the
working tree's `config/` (read-only). So the scratch edits below reached
dev only.

The dev pair was rebuilt by name (`docker compose --profile dev up -d
--build tool_service-dev orchestrator-dev`), and the code in both
containers was checked against the tree (md5 of `config_files.py`,
`config_loader.py` and the orchestrator's `main.py`: equal). The run
below is the last of three, on the committed code (`b4bc98f`). For each
file and each case it edits the working tree's file, reloads through
`scripts/config_reload.sh dev` with a `/dev/api/status` request sent
0.2 s into the reload, reads `/dev/api/config` (the `/services` data),
probes both services, then restarts only the dev container that reads the
file and reads the status again, then restores a byte copy (`cmp`) and
reloads. The `log:` lines are the last three config lines in that
container's log over 90 s, so a line from the previous case can appear.
The `/dev/api/status` endpoint takes 1.4 to 1.5 s with no reload running
(three plain requests: 1.49, 1.42, 1.50 s), so the concurrent figures
are its normal cost, not time spent waiting on a reload.

```
=== nested, config/tuning.yaml, on reload
   config/tuning.yaml: 40013 bytes
   == orchestrator (kronk-orchestrator-dev-1)
   422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":1,"message":"nested more than 10 levels deep"}],"kept":"last good values (files, loaded 2026-11-17 05:15:59)"}
   
   == tool_service (127.0.0.1:8103)
   200 {"ok":true,"changed":[]}
   reload script exit=1, took 0.19s
   concurrent /dev/api/status: 200 in 1.465827s
   /dev/api/config: orchestrator ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 05:15:59)
      error: {'file': 'config/tuning.yaml', 'line': 1, 'message': 'nested more than 10 levels deep'}
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
   probes: /dev/api/status 200, tool_service-dev /health 200
=== nested, config/tuning.yaml, at startup (orchestrator-dev restarted)
    Container kronk-orchestrator-dev-1 Restarting 
    Container kronk-orchestrator-dev-1 Started 
   kronk-orchestrator-dev-1: healthy restarts=0 after 6s
   /dev/api/config: orchestrator ok=False source=defaults event=startup kept=built-in defaults
      error: {'file': 'config/tuning.yaml', 'line': 1, 'message': 'nested more than 10 levels deep'}
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
   probes: /dev/api/status 200, tool_service-dev /health 200
   log: 2026-11-17 05:21:16,400 ERROR config_loader: config on reload rejected: config/tuning.yaml:1: nested more than 10 levels deep (keeping last good values (files, loaded 2026-11-17 05:15:59))
   log: config at startup rejected: config/tuning.yaml:1: nested more than 10 levels deep (running on built-in defaults)
   log: 2026-11-17 05:21:20,907 ERROR main: config at startup rejected: config/tuning.yaml:1: nested more than 10 levels deep (running on built-in defaults)
   restored (cmp equal)
   reload after restore exit=0
=== nested, config/music.yaml, on reload
   config/music.yaml: 40009 bytes
   == orchestrator (kronk-orchestrator-dev-1)
   200 {"ok":true,"changed":[]}
   
   == tool_service (127.0.0.1:8103)
   422 {"ok":false,"errors":[{"file":"config/music.yaml","line":1,"message":"nested more than 10 levels deep"}],"kept":"last good values (files, loaded 2026-11-17 05:21:26)"}
   reload script exit=1, took 0.22s
   concurrent /dev/api/status: 200 in 1.450277s
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
   /dev/api/config: tool_service ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 05:21:26)
      error: {'file': 'config/music.yaml', 'line': 1, 'message': 'nested more than 10 levels deep'}
   probes: /dev/api/status 200, tool_service-dev /health 200
=== nested, config/music.yaml, at startup (tool_service-dev restarted)
    Container kronk-tool_service-dev-1 Restarting 
    Container kronk-tool_service-dev-1 Started 
   kronk-tool_service-dev-1: healthy restarts=0 after 6s
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
   /dev/api/config: tool_service ok=False source=defaults event=startup kept=built-in defaults
      error: {'file': 'config/music.yaml', 'line': 1, 'message': 'nested more than 10 levels deep'}
   probes: /dev/api/status 200, tool_service-dev /health 200
   log: ERROR:tool_service.config:config on reload rejected: config/music.yaml:1: nested more than 10 levels deep (keeping last good values (files, loaded 2026-11-17 05:21:26))
   log: ERROR:tool_service.config:config at startup rejected: config/music.yaml:1: nested more than 10 levels deep (running on built-in defaults)
   restored (cmp equal)
   reload after restore exit=0
=== alias, config/tuning.yaml, on reload
   config/tuning.yaml: 321 bytes
   == orchestrator (kronk-orchestrator-dev-1)
   422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":1,"message":"anchors (&name) are not allowed in config files"}],"kept":"last good values (files, loaded 2026-11-17 05:21:36)"}
   
   == tool_service (127.0.0.1:8103)
   200 {"ok":true,"changed":[]}
   reload script exit=1, took 0.11s
   concurrent /dev/api/status: 200 in 1.457847s
   /dev/api/config: orchestrator ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 05:21:36)
      error: {'file': 'config/tuning.yaml', 'line': 1, 'message': 'anchors (&name) are not allowed in config files'}
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
   probes: /dev/api/status 200, tool_service-dev /health 200
=== alias, config/tuning.yaml, at startup (orchestrator-dev restarted)
    Container kronk-orchestrator-dev-1 Restarting 
    Container kronk-orchestrator-dev-1 Started 
   kronk-orchestrator-dev-1: healthy restarts=0 after 6s
   /dev/api/config: orchestrator ok=False source=defaults event=startup kept=built-in defaults
      error: {'file': 'config/tuning.yaml', 'line': 1, 'message': 'anchors (&name) are not allowed in config files'}
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
   probes: /dev/api/status 200, tool_service-dev /health 200
   log: 2026-11-17 05:21:37,041 ERROR config_loader: config on reload rejected: config/tuning.yaml:1: anchors (&name) are not allowed in config files (keeping last good values (files, loaded 2026-11-17 05:21:36))
   log: config at startup rejected: config/tuning.yaml:1: anchors (&name) are not allowed in config files (running on built-in defaults)
   log: 2026-11-17 05:21:41,598 ERROR main: config at startup rejected: config/tuning.yaml:1: anchors (&name) are not allowed in config files (running on built-in defaults)
   restored (cmp equal)
   reload after restore exit=0
=== alias, config/music.yaml, on reload
   config/music.yaml: 317 bytes
   == orchestrator (kronk-orchestrator-dev-1)
   200 {"ok":true,"changed":[]}
   
   == tool_service (127.0.0.1:8103)
   422 {"ok":false,"errors":[{"file":"config/music.yaml","line":1,"message":"anchors (&name) are not allowed in config files"}],"kept":"last good values (files, loaded 2026-11-17 05:21:47)"}
   reload script exit=1, took 0.11s
   concurrent /dev/api/status: 200 in 1.536762s
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
   /dev/api/config: tool_service ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 05:21:47)
      error: {'file': 'config/music.yaml', 'line': 1, 'message': 'anchors (&name) are not allowed in config files'}
   probes: /dev/api/status 200, tool_service-dev /health 200
=== alias, config/music.yaml, at startup (tool_service-dev restarted)
    Container kronk-tool_service-dev-1 Restarting 
    Container kronk-tool_service-dev-1 Started 
   kronk-tool_service-dev-1: healthy restarts=0 after 6s
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
   /dev/api/config: tool_service ok=False source=defaults event=startup kept=built-in defaults
      error: {'file': 'config/music.yaml', 'line': 1, 'message': 'anchors (&name) are not allowed in config files'}
   probes: /dev/api/status 200, tool_service-dev /health 200
   log: ERROR:tool_service.config:config at startup rejected: config/music.yaml:1: nested more than 10 levels deep (running on built-in defaults)
   log: ERROR:tool_service.config:config on reload rejected: config/music.yaml:1: anchors (&name) are not allowed in config files (keeping last good values (files, loaded 2026-11-17 05:21:47))
   log: ERROR:tool_service.config:config at startup rejected: config/music.yaml:1: anchors (&name) are not allowed in config files (running on built-in defaults)
   restored (cmp equal)
   reload after restore exit=0
=== overcap, config/tuning.yaml, on reload
   config/tuning.yaml: 52431614 bytes
   == orchestrator (kronk-orchestrator-dev-1)
   422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":null,"message":"too large: 52431614 bytes; the limit is 65536"}],"kept":"last good values (files, loaded 2026-11-17 05:21:57)"}
   
   == tool_service (127.0.0.1:8103)
   200 {"ok":true,"changed":[]}
   reload script exit=1, took 0.1s
   concurrent /dev/api/status: 200 in 1.450825s
   /dev/api/config: orchestrator ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 05:21:57)
      error: {'file': 'config/tuning.yaml', 'line': None, 'message': 'too large: 52431614 bytes; the limit is 65536'}
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
   probes: /dev/api/status 200, tool_service-dev /health 200
=== overcap, config/tuning.yaml, at startup (orchestrator-dev restarted)
    Container kronk-orchestrator-dev-1 Restarting 
    Container kronk-orchestrator-dev-1 Started 
   kronk-orchestrator-dev-1: healthy restarts=0 after 6s
   /dev/api/config: orchestrator ok=False source=defaults event=startup kept=built-in defaults
      error: {'file': 'config/tuning.yaml', 'line': None, 'message': 'too large: 52431614 bytes; the limit is 65536'}
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
   probes: /dev/api/status 200, tool_service-dev /health 200
   log: 2026-11-17 05:21:58,103 ERROR config_loader: config on reload rejected: config/tuning.yaml: too large: 52431614 bytes; the limit is 65536 (keeping last good values (files, loaded 2026-11-17 05:21:57))
   log: config at startup rejected: config/tuning.yaml: too large: 52431614 bytes; the limit is 65536 (running on built-in defaults)
   log: 2026-11-17 05:22:02,678 ERROR main: config at startup rejected: config/tuning.yaml: too large: 52431614 bytes; the limit is 65536 (running on built-in defaults)
   restored (cmp equal)
   reload after restore exit=0
=== overcap, config/music.yaml, on reload
   config/music.yaml: 52431345 bytes
   == orchestrator (kronk-orchestrator-dev-1)
   200 {"ok":true,"changed":[]}
   
   == tool_service (127.0.0.1:8103)
   422 {"ok":false,"errors":[{"file":"config/music.yaml","line":null,"message":"too large: 52431345 bytes; the limit is 65536"}],"kept":"last good values (files, loaded 2026-11-17 05:22:08)"}
   reload script exit=1, took 0.11s
   concurrent /dev/api/status: 200 in 1.415917s
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
   /dev/api/config: tool_service ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 05:22:08)
      error: {'file': 'config/music.yaml', 'line': None, 'message': 'too large: 52431345 bytes; the limit is 65536'}
   probes: /dev/api/status 200, tool_service-dev /health 200
=== overcap, config/music.yaml, at startup (tool_service-dev restarted)
    Container kronk-tool_service-dev-1 Restarting 
    Container kronk-tool_service-dev-1 Started 
   kronk-tool_service-dev-1: healthy restarts=0 after 6s
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
   /dev/api/config: tool_service ok=False source=defaults event=startup kept=built-in defaults
      error: {'file': 'config/music.yaml', 'line': None, 'message': 'too large: 52431345 bytes; the limit is 65536'}
   probes: /dev/api/status 200, tool_service-dev /health 200
   log: ERROR:tool_service.config:config at startup rejected: config/music.yaml:1: anchors (&name) are not allowed in config files (running on built-in defaults)
   log: ERROR:tool_service.config:config on reload rejected: config/music.yaml: too large: 52431345 bytes; the limit is 65536 (keeping last good values (files, loaded 2026-11-17 05:22:08))
   log: ERROR:tool_service.config:config at startup rejected: config/music.yaml: too large: 52431345 bytes; the limit is 65536 (running on built-in defaults)
   restored (cmp equal)
   reload after restore exit=0
=== warnings: comments-only config/music.yaml and a stray config/tunning.yaml, on reload
   == orchestrator (kronk-orchestrator-dev-1)
   200 {"ok":true,"changed":[],"warnings":[{"file":"config/tunning.yaml","line":null,"message":"not a file any service reads (a typo, or a leftover copy?); ignored"}]}
   
   == tool_service (127.0.0.1:8103)
   200 {"ok":true,"changed":[],"warnings":[{"file":"config/music.yaml","line":null,"message":"all defaults (file has no keys)"}]}
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
      warning: {'file': 'config/tunning.yaml', 'line': None, 'message': 'not a file any service reads (a typo, or a leftover copy?); ignored'}
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
      warning: {'file': 'config/music.yaml', 'line': None, 'message': 'all defaults (file has no keys)'}
   restored (cmp equal)
   == orchestrator (kronk-orchestrator-dev-1)
   200 {"ok":true,"changed":[]}
   
   == tool_service (127.0.0.1:8103)
   200 {"ok":true,"changed":[]}
=== shim path: POST /dev/api/chat
   {"model":"gemma-4-e4b","created_at":"2026-11-17T05:22:21.000000000Z","message":{"role":"assistant","content":"Paris."},"done":true,"done_reason":"stop","total_duration":0,"load_duration":0,"prompt_eval_count":10,"eval_count":1}
   http 200 in 2.392800s

=== end
   /dev/api/config: orchestrator ok=True source=files event=reload kept=None
   /dev/api/config: tool_service ok=True source=files event=reload kept=None
   probes: /dev/api/status 200, tool_service-dev /health 200
```

`git status --short config/` printed nothing at the end. A first run of
the same battery, before the `main.py` log fix, printed the startup
re-log as `config/tuning.yaml:None: too large: …`; that is the one-line
fix under "Also fixed in passing", and the run above shows the corrected
line.

## Tests and replay, verbatim

```
$ ./scripts/run_tests.sh -q
2934 passed, 2 skipped, 18 xfailed, 6 warnings in 20.55s
```

Baseline before this round: `2757 passed, 2 skipped, 18 xfailed`. 177
new tests; `tests/test_m21_pins.py` and its fixtures are untouched
(`git diff d37d2cc -- tests/test_m21_pins.py tests/fixtures config/` is
empty) and pass (`6 passed`). One existing assertion changed, for the
dialect: the duplicate-key message in `tests/test_tool_service_config.py`.

```
$ python3 scripts/voice_replay.py --label m21-fix1
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
voice_replay: {'pass': 99, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_052228_m21-fix1.json
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`,
row by row: 113 of the 114 baseline rows have the baseline's status; the
one that differs is `inc-0930-search-deep-house-ytm`, fail → pass, a
known "YouTube Music" search flapper (the same change the build's and the
e2e replays showed). The 8 rows added since M10 (`m4b-*`): 7 pass, 1 skip.
No row was skipped for player activity; the replay ran once, to the end,
and reported the players unchanged.

## Not done, and why

- **Finding 4 (a reload during a running request mixes old and new
  values).** Not in this round's brief ("nothing else"). Still true and
  still undocumented in the feature doc. It matters for step 2, whose
  `/settings` page reloads while people talk to it.
- **Finding 6 (the suite makes name lookups: `fake-tools`, three news
  feeds).** Pre-existing and not caused by M21; not in this brief. The new
  tests open no socket: every fake is in-process, and the script test puts
  stub `docker` and `curl` first on `PATH`.
- **Mutation survivors o10, a4, m2, m4, r2 (finding 7).** Not in this
  brief. o6 and s4 (falling back to the defaults instead of keeping the
  last good values) are now killed by
  `test_bad_reload_keeps_a_non_default_last_good_value`, which starts from
  a non-default value.
- **Observations 7, 8, 9, 11.** Not in this brief.
- **Reload still runs on the event loop.** Bounded by the caps (0.32 s
  worst measured) rather than moved to a thread; moving it would need a
  lock to keep concurrent reloads serialised, for a gain of a third of a
  second. Recorded here so step 2 can revisit it.
- **No values moved, no prompt or value changed, no routing change.** The
  inventory addendum lists the missing rows with outcomes only.
- **nginx was not restarted** after the dev orchestrator rebuild
  (CLAUDE.md's usual step): it is a production container and the brief
  forbids it. The dev path answered through it without a restart (shim
  check above).
- **The unknown `prompts/*.md` case stays an error**, not the warning
  observation 5 describes for stray files in general; see Observation 5
  above for why.

## What I changed temporarily, and reverted

- The working tree's `config/tuning.yaml` and `config/music.yaml`, on dev
  only (production mounts release `sprint-M2` and has no `/config` mount;
  `docker inspect` before the first edit). Each case restored from a byte
  copy taken before the first edit and checked with `cmp`; a scratch
  `config/tunning.yaml` was created and removed with `rm -f`. At the end
  `git status --short config/` is empty and both dev services report
  `ok=True source=files`.
- `docker compose --profile dev restart` of `orchestrator-dev` and
  `tool_service-dev`, by name, three times each per battery run, three
  runs (nine each); three rebuilds of the dev pair by name. No other container was started,
  stopped, restarted or rebuilt; production's orchestrator, tool_service
  and nginx kept their uptime.
- Scratch files (the old-tree extract, backups, scripts and logs) only in
  the session scratch directory.
- End state, checked after the replay: `kronk-orchestrator-1`,
  `kronk-tool_service-1`, `kronk-nginx-1` and LiteLLM "Up 2 hours" (from
  before this round), Home Assistant up 29 hours, Music Assistant 12
  days; `llama-gemma4-e4b`, `llama-gemma3-4b`, `llama-devstral-q4`,
  `wyoming-whisper` and the hot tub monitor active; both dev containers
  healthy on the committed code, `ok=True source=files`. No sound was
  made: dev runs `MUSIC_DRY_RUN=1` and the replay reported "players
  unchanged (17 checked)".
