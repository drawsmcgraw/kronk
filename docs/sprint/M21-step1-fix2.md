# M21 step 1, fix round 2: text that cannot be encoded

Fix agent, 2026-11-17 (UTC), branch `refactor`, on top of `6f5487d`.
Inputs: `docs/sprint/M21-step1-criteria.md`, `docs/sprint/M21-step1-fix1.md`,
`docs/sprint/M21-step1-e2e2.md` (finding 1 against criteria 9 and 10,
finding 2 against criterion 1; observations 2, 3, 4, 5 and 9). Commits, in
order: `efcb5c8` (code and tests), `d622655` (feature doc; inventory
addendum in the build report), the next commit (the feature doc's example
replaced with real output), and the commit that adds this report.

## The class, and the two layers

The class: text taken from a file or a file name reaches an HTTP answer or
a log line without being made safe to encode. The e2e retest found two
members: a file under `config/` whose name is not valid UTF-8 (Python
decodes it to lone surrogates, `caf\udce9.yaml`) and a duplicate YAML key
written `"\uD800"` (a lone surrogate once the library unescapes it). Both
put a string into a `Problem` that Starlette's `JSONResponse` cannot encode
(`ensure_ascii=False`, then UTF-8), so `POST /config/reload` and
`/api/config` answered 500.

**Layer 1: nothing unsafe can exist in the status object.**
`config_files.safe()` (`orchestrator/config_files.py:109`; the file is
byte-identical in `tool_service/`, so line numbers are the same there)
takes any object, cuts it to the limit, escapes every character of
category Cc (control), Cs (lone surrogate), Cf (format: bidi overrides,
zero-width marks), Zl and Zp (line and paragraph separators) as a visible
`\xNN`, `\uNNNN` or `\UNNNNNNNN`, and caps the result (80 characters by
default, `MAX_MESSAGE` = 300 for a whole message, `:85`). It is applied:

| Where | Line |
|---|---|
| Every `Problem` (errors and warnings), file and message, in `__post_init__` | `orchestrator/config_loader.py:89`, `tool_service/config_loader.py:72` |
| Key names in schema errors, cut like values (observation 4) | `orchestrator/config_loader.py:324`, `tool_service/config_loader.py:204` |
| The duplicate-key message, before it is composed | `config_files.py:455` |
| Every "changed" line of a reload | `orchestrator/config_loader.py:454`, `tool_service/config_loader.py:269` |
| The env override value shown on `/api/config` | `orchestrator/config_loader.py:228` |
| The config directory shown on the status | `orchestrator/config_loader.py:537`, `tool_service/config_loader.py:334` |
| Parser messages and quoted values (`shorten` and `quote` now go through `safe`) | `config_files.py:124-134` |

Because the `Problem` itself is made safe when it is built, the log lines
(`_report`, `_warn`, `_log_config_status`), the `config_error` event and
the reload answer all carry the escaped form: a stray name with an ESC
sequence and a newline is logged as `a\x1b[31m\x0ab.yaml`, on one line.

**Layer 2: the boundary never answers a bare 500.** `renderable()`
(`config_files.py:152`) encodes the answer exactly as `JSONResponse` will;
if that raises, it logs the exception and returns a fallback built from
fixed text. `render_reload()` (`:175`) and `render_status()` (`:167`) are
the two fallbacks:

- `POST /config/reload` on the orchestrator (`orchestrator/config_api.py:36`)
  and tool_service (`tool_service/main.py:2560`): `{"ok": <outcome>,
  "errors": [{"file": null, "line": null, "message": "the config status
  could not be rendered as JSON (UnicodeEncodeError); the reload took
  effect; see the service log"}]}` (or "…was rejected and the last good
  values stay in effect…"), with the reload's own status code, 200 or 422.
- `GET /config/status` on tool_service (`tool_service/main.py:2566`) and each
  of the two parts of the orchestrator's `GET /api/config`
  (`orchestrator/config_api.py:56-57`) separately: a status entry with `ok:
  false`, `source: "unknown"` and the one error "the config status could
  not be rendered as JSON (…); see the service log". The `/services` page
  prints `errors[].message`, so it shows that sentence, and the other
  service's card still shows its own status. This also covers a tool_service
  answer whose JSON holds a `\ud800` escape, which `resp.json()` would turn
  back into a lone surrogate inside the orchestrator.

## Each numbered item

1. **One function, applied at construction.** `safe()` as above. Every
   `Problem`, warning and "changed" line is built through it. Key names are
   cut to 80 characters like values: a 1,000-character unknown key now gives
   `weather_context.kkkk…: unknown key` (93 characters: the key path cut
   to 80) instead of a message of over 1,000 characters.
2. **Belt and braces at the boundary.** `render_reload` / `render_status`
   at both reload endpoints, tool_service's status endpoint and per part of
   `/api/config`, as above. Tested by forcing an unencodable answer
   (patched `reload()` and `status()`), on both services.
3. **A file changed during the read** (observation 2). `read_text()`
   (`config_files.py:204`) reads through `_read_once()` (`:222`), which
   takes `fstat` of the open descriptor before the read (`:250`) and after
   it (`:260`) and returns nothing if the size, the mtime (ns) or the number
   of bytes read differ (`:265`). Then it reads once more; a second change
   is the config error `file changed during read (read twice); reload again
   once it is saved` (`:173`), so the last good values stay. Applies to
   every file both loaders read, prompts included. What it cannot catch: a
   read that falls wholly inside the moment between an in-place save's
   truncate and its first write sees a steady empty file (same size and
   mtime before and after) and loads it, with the existing warning `all
   defaults (file has no keys)`. Only an atomic write closes that; the
   feature doc says so, for step 2's `/settings` page.
4. **The two YAML front ends** (observation 3). Neither image has both
   libraries (checked inside the running dev containers:
   `kronk-orchestrator-dev-1` has `yaml` and not `ruamel.yaml`,
   `kronk-tool_service-dev-1` the reverse; the lockfiles agree), so one
   library in both would be a dependency change. I took the other option:
   make the two accept the same language, with a shared table. Two
   changes close every difference the differential search found, and a
   third makes a key always a name:
   - `check_yaml_text()` (`config_files.py:291`, pattern at `:277`) refuses,
     before either library sees the text, control characters other than
     newline and tab (C0, DEL and C1, so U+0085 too), U+2028, U+2029, lone
     surrogates, U+FFFE and U+FFFF. PyYAML read U+0085, U+2028 and U+2029 as
     line breaks and ruamel did not; both refused the rest without a line.
   - tool_service runs ruamel's scanner in YAML 1.1 mode
     (`tool_service/config_loader.py:157-164`), so `{a?b: 1}` and `a: [b?c]`
     (a `?` inside a plain scalar in flow context), `: 1` and `{: 1}` (an
     empty plain key, which ruamel read as `{'': 1}`) are refused by both,
     as PyYAML does. Only ruamel's scanner and parser are used, never its
     resolver, so the version changes no value. `config_files.build()`
     still decides every scalar.
   - `build()` refuses an empty plain key (`? ` with nothing after it,
     `:453`), which both libraries read as `{'': 1}` before.

   The shared table is `ONE_LANGUAGE` in `tests/test_config_fix2.py` (28
   rows, each with the outcome both must give), next to fix round 1's 60-row
   `DIALECT` table, and `test_both_loaders_agree_on_generated_texts` runs
   2,000 generated texts (seeded) through both. A scratch differential run
   (the e2e's method: the shipped YAML files with random edits and token
   soups, line endings normalised as `read_text` does), 40,000 texts, seed 1:

   | Outcome | `6f5487d` | `d622655` |
   |---|---|---|
   | same answer | 34,254 | 38,730 |
   | both refuse, same line | 2,106 | 1,229 |
   | both refuse, different line | 3,118 | 41 |
   | one accepts, the other refuses | 413 | 0 |
   | both accept, different data | 5 | 0 |
   | both accept, same data, different key lines | 104 | 0 |

   Seeds 2, 3 and 4 (40,000 each) on the fixed code: 0, 0, 0 in the last
   three rows; 46, 49 and 48 refused by both at different lines. Those are
   texts both libraries refuse, where each reports its own position (for
   example `>\n[`: PyYAML stops at line 2, ruamel reads to the end and
   `build()` says line 1, "must be a mapping"). The outcome is the same:
   refused, file named, last good values kept. I did not chase the line.
5. **YAML errors without a line** (observation 5). A character
   `check_yaml_text` refuses is named with its kind, code point, line and
   byte offset. An ESC in a comment after `max_age_s: 7200` in the shipped
   `tuning.yaml`: `config/tuning.yaml:32: control character U+001B at byte
   1627 is not allowed in a YAML file (only newline and tab)`. The offset is
   counted in UTF-8 in the text as the loader reads it, so a CRLF line end
   counts as one byte; exact for every file without CR (the feature doc
   says so). A 5,000-digit integer now names its line: `number too long
   (5000 digits)` (`config_files.py:462`). What is still without a line
   says so: a library error with no position (`not valid YAML: <message>;
   line unknown`, `orchestrator/config_loader.py:306`,
   `tool_service/config_loader.py:180`, `:183`) and an exception the
   library raises that is not its own error, such as `"\UFFFFFFFF"`
   (`not valid YAML (ValueError: chr() arg not in range(0x110000)); line
   unknown`, `config_files.unparsable`, `:139`). Whole-file problems (`file
   is missing`, `too large`, `cannot be read`) keep `line: null` without
   the words: a line means nothing there.
6. **No FIFO or scratch file left behind** (observation 9). Three causes,
   three fixes: `test_a_pipe_in_place_of_the_file_does_not_block` removes
   its FIFO in a `finally` (`tests/test_config_hardening.py:310`);
   `tests/conftest.py:20` removes the `kronk-taste-*` directory it creates
   at import, at exit (every run had left one; `/tmp` held 654 of them
   before this round); a new `pytest.ini` sets `tmp_path_retention_policy
   = failed`, so pytest removes each passing test's `tmp_path` when the run
   ends and keeps only a failed test's, for debugging. The rootdir is
   unchanged (the repo), and nothing else is in the file. A passing run now
   leaves only pytest's own empty per-user directory
   (`$TMPDIR/pytest-of-<user>/`).
7. **Criterion 1, finding 2.** `docs/sprint/M21-step1-build.md`, new
   section "Addendum, fix round 2": 14 rows for the tool-result sentences
   (`orchestrator/tools.py:505-1058` by handler group, named constants
   `MM_UPDATE_REFUSAL`, `NEWS_REFRESH_FAILED`, `NEWS_UNAVAILABLE`;
   tool_service's three sentence helpers, all 60 `HTTPException` sites by
   line, and the result text of every endpoint), and 8 rows covering all 42
   env names set under `environment:` in `docker-compose.yml`, each by
   name with its lines (two rows point to the earlier rows that already
   covered 17 of them). Every row: left where it is, and why. No value
   moved.
8. **Tests.** `tests/test_config_fix2.py`, 69 tests, run against both
   services wherever the case applies to both (the non-UTF-8 file name
   cases are the orchestrator's: tool_service reads one named file and does
   not scan). No test opens a socket: the orchestrator's call to
   tool_service's `/config/status` is answered by tool_service's app in
   process; the scratch-file test runs pytest in a subprocess with `TMPDIR`
   inside its own `tmp_path`. `tests/test_m21_pins.py` and its fixtures are
   untouched (`git diff 6f5487d -- tests/test_m21_pins.py tests/fixtures
   config/` is empty) and pass. One existing test changed:
   `test_a_pipe_in_place_of_the_file_does_not_block` (item 6, cleanup only;
   its assertion is unchanged).

Also changed, as part of item 4: the orchestrator's YAML error now falls
back to the library's `context_mark` when there is no `problem_mark`, as
tool_service already did, so the two report a line in the same cases.

Not changed: the `/services` page (it already prints `errors[].message`),
any prompt, any value, routing, nginx.

## Proof the new tests fail before the fix

`tests/test_config_fix2.py` copied into a scratch `git archive` of
`6f5487d` (code untouched) and run there with `TMPDIR` in the scratch
directory:

```
$ tests/.venv/bin/python -m pytest tests/test_config_fix2.py -q -p no:cacheprovider --tb=line --junitxml=old.xml
62 failed, 7 passed, 3 warnings in 2.40s
```

Per test, from the JUnit XML:

| Test | Fail on `6f5487d` | Pass on `6f5487d` | First line of the first failure |
|---|---|---|---|
| `test_a_stray_file_whose_name_is_not_utf8_still_answers` | 1 | 0 | `UnicodeEncodeError: 'utf-8' codec can't encode character '\udce9' in position 55: surrogates not allowed` |
| `test_an_unknown_prompt_whose_name_is_not_utf8_is_an_error_that_answers` | 1 | 0 | `UnicodeEncodeError: 'utf-8' codec can't encode character '\udce9' in position 49: surrogates not allowed` |
| `test_a_duplicate_surrogate_key_is_an_error_that_answers` | 2 | 0 | `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' in position 86: surrogates not allowed` |
| `test_the_service_answers_a_normal_request_meanwhile` | 1 | 0 | `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' in position 86: surrogates not allowed` |
| `test_safe_escapes_visibly` | 8 | 0 | `AttributeError: module 'config_files' has no attribute 'safe'` |
| `test_safe_caps_length_after_escaping` | 1 | 0 | `AttributeError: module 'config_files' has no attribute 'safe'` |
| `test_a_problem_is_made_safe_when_it_is_built` | 2 | 0 | `AssertionError: assert (False)` |
| `test_key_names_are_shortened_like_values` | 2 | 0 | `AssertionError: weather_context.kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk` |
| `test_control_characters_in_a_stray_name_do_not_reach_the_log_raw` | 1 | 0 | `AssertionError: assert 'config/a\x1b[31m\nb.yaml' == 'config/a\\x1b[31m\\x0ab.yaml'` |
| `test_a_reload_answer_that_cannot_be_encoded_still_answers_json` | 4 | 0 | `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' in position 23: surrogates not allowed` |
| `test_a_status_that_cannot_be_encoded_still_answers_json` | 2 | 0 | `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' in position 54: surrogates not allowed` |
| `test_tool_service_status_with_a_surrogate_escape_does_not_break_api_config` | 1 | 0 | `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' in position 695: surrogates not allowed` |
| `test_a_file_saved_in_place_during_the_read_is_read_again` | 2 | 0 | `AssertionError: {'changed': [], 'ok': True, 'warnings': [{'file': 'config/tuning.yaml', 'line': None, 'message` |
| `test_a_file_that_keeps_changing_is_an_error_and_the_last_good_values_stay` | 2 | 0 | `AssertionError: {"ok":true,"changed":[],"warnings":[{"file":"config/tuning.yaml","line":null,"message":"all de` |
| `test_a_prompt_saved_in_place_during_the_read_is_read_again` | 1 | 0 | `AssertionError: {'errors': [{'file': 'config/prompts/home.md', 'line': 1, 'message': 'must start with a <!-- h` |
| `test_both_loaders_read_the_same_language` | 21 | 7 | `AssertionError: (('ok', {'a': 'x', 'b': 1}, {('a',): 1, ('b',): 2}), ('error', 1, 'not valid YAML: mapping val` |
| `test_both_loaders_agree_on_generated_texts` | 1 | 0 | `AssertionError: [(': 8#\|\u2029', ('error', 1, "not valid YAML: expected <block end>, but found ':'"), ('ok', {` |
| `test_a_control_character_in_yaml_names_the_character_line_and_byte` | 2 | 0 | `AssertionError: assert ('tuning.yaml', None) == ('tuning.yaml', 32)` |
| `test_an_escape_no_library_can_read_says_line_unknown` | 2 | 0 | `AssertionError: assert (None is None and False)` |
| `test_a_yaml_error_without_a_mark_says_line_unknown` | 2 | 0 | `AssertionError: assert (None, 'not v... no position') == (None, 'not v...line unknown')` |
| `test_a_very_long_integer_names_its_line` | 2 | 0 | `AssertionError: assert None == (31 + 1)` |
| `test_a_run_of_the_fifo_tests_leaves_nothing_behind` | 1 | 0 | `AssertionError: ['/tmp/claude-1000/-home-drew-git-repos-drawsmcgraw-kronk/0194af2e-d656-4892-8f8e-157db53b7126` |

The 7 that pass on the old tree are guard rows of the shared table where
the two libraries already agreed: `? a\n: 1`, `? a`, `{? a: 1}`, `a?b: 1`,
`a: x?y`, a BOM at the start, and a single `"\uD800"` key (accepted by
both; the surrogate is only a problem once it reaches a message, which the
duplicate-key row covers). The old-tree run of
`test_a_run_of_the_fifo_tests_leaves_nothing_behind` left, in my scratch
directory, the two FIFOs and the `kronk-taste-*` directory it reports.

On the fixed tree the file runs in about 2.3 s; the slowest test is the
generated-text one (2,000 texts through both loaders).

## Live on dev

`docker inspect` first: `kronk-orchestrator-1` and `kronk-tool_service-1`
mount `kronk-releases/sprint-M2/…` and have no `/config` mount;
`kronk-orchestrator-dev-1` and `kronk-tool_service-dev-1` mount the working
tree's `config/` read-only. So every scratch file below reached dev only.

The dev pair was rebuilt by name (`docker compose --profile dev up -d
--build tool_service-dev orchestrator-dev`; both healthy), and the code in
both containers checked against the committed tree (md5 of
`config_files.py`, `config_loader.py`, `config_api.py` and `main.py`:
equal). The battery, as printed (`scripts/config_reload.sh dev` for each
reload, `/dev/api/config` for the `/services` data, `GET /dev/api/status`
and tool_service-dev's `/health` as the normal requests, `docker compose
--profile dev restart` of the one container that reads the file, then the
file removed with `rm -f -- "${F:?}"`, the name built from `printf
'caf\351…'`, or the tracked file restored from a byte copy and checked with
`cmp`):

```
=== a stray file whose name is not UTF-8: config/caf\xe9.yaml
   created: $'/home/drew/git-repos/drawsmcgraw/kronk/config/caf\351.yaml'
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":[],"warnings":[{"file":"config/caf\\udce9.yaml","line":null,"message":"not a file any service reads (a typo, or a leftover copy?); ignored"}]}
      
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
      exit=0
   /dev/api/config -> 200 (846 bytes)
      orchestrator ok=True source=files event=reload kept=None
         warning: {"file": "config/caf\\udce9.yaml", "line": null, "message": "not a file any service reads (a typo, or a leftover copy?); ignored"}
      tool_service ok=True source=files event=reload kept=None
   normal requests: GET /dev/api/status 200 in 1.544792s; tool_service-dev GET /health 200
   restart: docker compose --profile dev restart orchestrator-dev
       Container kronk-orchestrator-dev-1 Restarting 
       Container kronk-orchestrator-dev-1 Started 
      kronk-orchestrator-dev-1: healthy restarts=0
   /dev/api/config -> 200 (837 bytes)
      orchestrator ok=True source=files event=startup kept=None
         warning: {"file": "config/caf\\udce9.yaml", "line": null, "message": "not a file any service reads (a typo, or a leftover copy?); ignored"}
      tool_service ok=True source=files event=reload kept=None
   normal requests: GET /dev/api/status 200 in 1.376412s; tool_service-dev GET /health 200
   log (kronk-orchestrator-dev-1, config lines since the edit):
      2026-11-17 06:28:39,134 INFO main: config: files from /config (8 files)
      2026-11-17 06:28:39,134 WARNING main: config at startup: config/caf\udce9.yaml: not a file any service reads (a typo, or a leftover copy?); ignored
      2026-11-17 06:28:43,350 INFO httpx: HTTP Request: GET http://tool_service-dev:8103/config/status "HTTP/1.1 200 OK"
      INFO:     172.18.0.9:47820 - "GET /api/config HTTP/1.1" 200 OK
   removed: $'/home/drew/git-repos/drawsmcgraw/kronk/config/caf\351.yaml'; exists now: no
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":[]}
      
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
      exit=0
=== an unknown prompt whose name is not UTF-8: config/prompts/caf\xe9.md
   created: $'/home/drew/git-repos/drawsmcgraw/kronk/config/prompts/caf\351.md'
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      422 {"ok":false,"errors":[{"file":"config/prompts/caf\\udce9.md","line":null,"message":"unknown prompt file: no agent of that name (known: home, coordinator, assistant, coding, talkie, devops, research)"}],"kept":"last good values (files, loaded 2026-11-17 06:28:44)"}
      
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
      exit=1
   /dev/api/config -> 200 (953 bytes)
      orchestrator ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 06:28:44)
         error: {"file": "config/prompts/caf\\udce9.md", "line": null, "message": "unknown prompt file: no agent of that name (known: home, coordinator, assistant, coding, talkie, devops, research)"}
      tool_service ok=True source=files event=reload kept=None
   normal requests: GET /dev/api/status 200 in 1.447167s; tool_service-dev GET /health 200
   restart: docker compose --profile dev restart orchestrator-dev
       Container kronk-orchestrator-dev-1 Restarting 
       Container kronk-orchestrator-dev-1 Started 
      kronk-orchestrator-dev-1: healthy restarts=0
   /dev/api/config -> 200 (641 bytes)
      orchestrator ok=False source=defaults event=startup kept=built-in defaults
         error: {"file": "config/prompts/caf\\udce9.md", "line": null, "message": "unknown prompt file: no agent of that name (known: home, coordinator, assistant, coding, talkie, devops, research)"}
      tool_service ok=True source=files event=reload kept=None
   normal requests: GET /dev/api/status 200 in 1.403263s; tool_service-dev GET /health 200
   log (kronk-orchestrator-dev-1, config lines since the edit):
      config at startup rejected: config/prompts/caf\udce9.md: unknown prompt file: no agent of that name (known: home, coordinator, assistant, coding, talkie, devops, research) (running on built-in defaults)
      2026-11-17 06:28:47,866 ERROR main: config at startup rejected: config/prompts/caf\udce9.md: unknown prompt file: no agent of that name (known: home, coordinator, assistant, coding, talkie, devops, research) (running on built-in defaults)
      2026-11-17 06:28:52,104 INFO httpx: HTTP Request: GET http://tool_service-dev:8103/config/status "HTTP/1.1 200 OK"
      INFO:     172.18.0.9:33480 - "GET /api/config HTTP/1.1" 200 OK
   removed: $'/home/drew/git-repos/drawsmcgraw/kronk/config/prompts/caf\351.md'; exists now: no
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":[]}
      
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
      exit=0
=== a duplicate surrogate-escape key in config/tuning.yaml
   appended to config/tuning.yaml: "\uD800": 1 "\uD800": 2 
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":65,"message":"duplicate key \\ud800"}],"kept":"last good values (files, loaded 2026-11-17 06:28:53)"}
      
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
      exit=1
   /dev/api/config -> 200 (847 bytes)
      orchestrator ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 06:28:53)
         error: {"file": "config/tuning.yaml", "line": 65, "message": "duplicate key \\ud800"}
      tool_service ok=True source=files event=reload kept=None
   normal requests: GET /dev/api/status 200 in 1.450744s; tool_service-dev GET /health 200
   restart: docker compose --profile dev restart orchestrator-dev
       Container kronk-orchestrator-dev-1 Restarting 
       Container kronk-orchestrator-dev-1 Started 
      kronk-orchestrator-dev-1: healthy restarts=0
   /dev/api/config -> 200 (535 bytes)
      orchestrator ok=False source=defaults event=startup kept=built-in defaults
         error: {"file": "config/tuning.yaml", "line": 65, "message": "duplicate key \\ud800"}
      tool_service ok=True source=files event=reload kept=None
   normal requests: GET /dev/api/status 200 in 1.514357s; tool_service-dev GET /health 200
   log (kronk-orchestrator-dev-1, config lines since the edit):
      config at startup rejected: config/tuning.yaml:65: duplicate key \ud800 (running on built-in defaults)
      2026-11-17 06:28:56,717 ERROR main: config at startup rejected: config/tuning.yaml:65: duplicate key \ud800 (running on built-in defaults)
      2026-11-17 06:29:00,954 INFO httpx: HTTP Request: GET http://tool_service-dev:8103/config/status "HTTP/1.1 200 OK"
      INFO:     172.18.0.9:56548 - "GET /api/config HTTP/1.1" 200 OK
   restored config/tuning.yaml (cmp equal)
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":[]}
      
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
      exit=0
=== a duplicate surrogate-escape key in config/music.yaml
   appended to config/music.yaml: "\uD800": 1 "\uD800": 2 
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":[]}
      
      == tool_service (127.0.0.1:8103)
      422 {"ok":false,"errors":[{"file":"config/music.yaml","line":59,"message":"duplicate key \\ud800"}],"kept":"last good values (files, loaded 2026-11-17 06:29:02)"}
      exit=1
   /dev/api/config -> 200 (846 bytes)
      orchestrator ok=True source=files event=reload kept=None
      tool_service ok=False source=files event=reload kept=last good values (files, loaded 2026-11-17 06:29:02)
         error: {"file": "config/music.yaml", "line": 59, "message": "duplicate key \\ud800"}
   normal requests: GET /dev/api/status 200 in 1.430998s; tool_service-dev GET /health 200
   restart: docker compose --profile dev restart tool_service-dev
       Container kronk-tool_service-dev-1 Restarting 
       Container kronk-tool_service-dev-1 Started 
      kronk-tool_service-dev-1: healthy restarts=0
   /dev/api/config -> 200 (788 bytes)
      orchestrator ok=True source=files event=reload kept=None
      tool_service ok=False source=defaults event=startup kept=built-in defaults
         error: {"file": "config/music.yaml", "line": 59, "message": "duplicate key \\ud800"}
   normal requests: GET /dev/api/status 200 in 1.428163s; tool_service-dev GET /health 200
   log (kronk-tool_service-dev-1, config lines since the edit):
      INFO:     172.18.0.1:57638 - "POST /config/reload HTTP/1.1" 422 Unprocessable Entity
      INFO:     172.18.0.6:39844 - "GET /config/status HTTP/1.1" 200 OK
      ERROR:tool_service.config:config at startup rejected: config/music.yaml:59: duplicate key \ud800 (running on built-in defaults)
      INFO:     172.18.0.6:50836 - "GET /config/status HTTP/1.1" 200 OK
   restored config/music.yaml (cmp equal)
   reload (scripts/config_reload.sh dev):
      == orchestrator (kronk-orchestrator-dev-1)
      200 {"ok":true,"changed":[]}
      
      == tool_service (127.0.0.1:8103)
      200 {"ok":true,"changed":[]}
      exit=0
=== shim path: POST /dev/api/chat
   {"detail":[{"type":"model_attributes_type","loc":["body"],"msg":"Input should be a valid dictionary or object to extract fields from","input":"{\"model\":\"kronk\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"What is the capital of France? One word.\"}]}"}]}
      http 422 in 0.002294s
=== end
   /dev/api/config -> 200 (724 bytes)
      orchestrator ok=True source=files event=reload kept=None
      tool_service ok=True source=files event=reload kept=None
   normal requests: GET /dev/api/status 200 in 1.504500s; tool_service-dev GET /health 200
   git status --short:
   untracked under config/: 0
   entries in config/: 10
```

The shim request at the end of the battery was malformed by my script (no
`Content-Type`, so FastAPI answered 422 before the handler); sent again
with the header:

```
$ curl -s -H 'Content-Type: application/json' http://localhost/dev/api/chat -d '{"model":"kronk","stream":false,"messages":[{"role":"user","content":"What is the capital of France? One word."}]}'
{"model":"gemma-4-e4b","created_at":"2026-11-17T06:29:23.000000000Z","message":{"role":"assistant","content":"Paris"},"done":true,"done_reason":"stop","total_duration":0,"load_duration":0,"prompt_eval_count":10,"eval_count":1}
http 200 in 2.900983s
```

`git status --short` printed nothing, no untracked file under `config/`,
and `config/` holds its 10 entries. Both dev services end on `ok=True
source=files`.

## Tests and replay, verbatim

```
$ ./scripts/run_tests.sh -q
3003 passed, 2 skipped, 18 xfailed, 6 warnings in 22.32s
```

Baseline before this round: `2934 passed, 2 skipped, 18 xfailed`. 69 new
tests. `tests/test_m21_pins.py`: `6 passed`.

```
$ python3 scripts/voice_replay.py --label m21-fix2
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
  . pass  executed   inc-0930-search-deep-house-ytm
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
voice_replay: {'pass': 99, 'xfail': 19, 'skip': 3, 'xpass': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_063000_m21-fix2.json
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`,
row by row: 112 of the 114 baseline rows have the baseline's status, none
was not run. The two that differ are both known flappers named in the
brief: `inc-0930-search-deep-house-ytm`, fail → pass (a "YouTube Music"
search row; the same change the build's, both tests' and fix round 1's
replays showed), and `eval-shuffle-it`, xfail → xpass (Kronk answered
without calling a music tool this time; its xfail reason records the
2026-11-16 run that did). The 8 rows added since M10 (`m4b-*`): 7 pass, 1
skip. No row was skipped for player activity; the replay ran once, to the
end, and reported the players unchanged. The result file is committed with
this report.

## Not done, and why

- **The truncate-moment window of item 3.** Described above and in the
  feature doc. Closing it needs the writer to write atomically, which is
  step 2's job (its `/settings` page is the writer).
- **Error lines where both libraries refuse a text.** About 0.1% of the
  generated texts (item 4). Both refuse; only the reported line differs.
- **Finding 4 of the first test (a reload during a running request mixes
  old and new values)**, the pre-existing name lookups of the suite
  (first-test finding 6), the mutation survivors and observations 1, 6, 7,
  8 and 10: not in this round's brief.
- **The `/services` page's wording for the fallback.** It shows the
  sentence as `STATUS REJECTED — : the config status could not be
  rendered…`, the shape it already used for "tool_service config status
  unavailable". The page is unchanged ("nothing else").
- **nginx was not restarted** after the dev orchestrator rebuild (a
  production container; the brief forbids it). The dev path answered
  through it (shim check above).

## What I changed temporarily, and reverted

- In the working tree's `config/`, on dev only: created
  `config/caf\xe9.yaml` and `config/prompts/caf\xe9.md` and removed each
  with `rm -f -- "${F:?}"`; appended two lines to `config/tuning.yaml` and
  to `config/music.yaml` and restored each from a byte copy taken before
  the first edit (`cp -p`, `cmp` equal). At the end `git status --short` is
  empty.
- `docker compose --profile dev restart` by name: `orchestrator-dev` three
  times, `tool_service-dev` once; one rebuild of the dev pair by name. No
  other container was started, stopped, restarted or rebuilt.
- Scratch files (the old-tree extract, the differential script, the live
  script, backups and outputs) only in the session scratch directory. My
  first config-test run of this round, before the FIFO fix, left two FIFOs
  under `/tmp/pytest-of-drew/pytest-588/`, and my first three runs left
  three empty `kronk-taste-*` directories (`2kpl774q`, `11o780gh`,
  `p3aq8mea`, created 01:12 and 01:20 local by my runs); I removed those
  five with `rm -f` and `rmdir`. The other `kronk-taste-*` directories in
  `/tmp` (654, from runs before this round) and pytest's older numbered directories I left alone:
  not mine, and outside the repo.
- End state, checked after the replay: production's
  `kronk-orchestrator-1`, `kronk-tool_service-1`, `kronk-nginx-1` and
  `kronk-litellm-1` started 03:30Z with `restarts=0` (before this round);
  Home Assistant up 30 hours, Music Assistant 12 days; `llama-gemma4-e4b`,
  `llama-gemma3-4b`, `llama-devstral-q4`, `wyoming-whisper`, the hot tub
  monitor and the memory watchdog active; both dev containers healthy on
  the committed code, both `ok=True source=files` on `/dev/api/config`. No
  sound was made: dev runs `MUSIC_DRY_RUN=1` and the replay reported
  "players unchanged (17 checked)".
