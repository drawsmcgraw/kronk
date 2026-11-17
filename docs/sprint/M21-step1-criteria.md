# M21 step 1 pass criteria: a config directory of plain files

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M21. One
place to see and edit prompts and adjustments", step 1, and the M21 line of
"Verified on Kronk, 2026-11-15"). Step 2, the `/settings` page, is a later
sprint item and is out of scope. The tester checks against this file, not
against the build report.

## What step 1 is

Prompts and tuning values move out of Python strings, constants and compose
env lines into plain files under `config/`, loaded through a validated
schema, with a reload endpoint. The move itself must change no behaviour:
every prompt and value in effect after it is the one in effect before it.

## Criteria

### A. Inventory

1. The build report carries an inventory, by search, of every place a
   prompt, model-facing text or tuning value is defined: agent and
   coordinator prompts, tool descriptions (`tools.py`), `_terminal_speech`
   templates, `errors.py` sentences, routing regexes, thresholds, timeouts,
   round budgets, model assignments, env vars in `docker-compose.yml`,
   `tool_service` values (verify timeout, resolver thresholds, the news
   brief and genre tagging prompts), `litellm/config.yaml`, the llama unit
   flags (sampling, context size, reasoning budget), `data/music_taste.yaml`.
   Each row says: moved to `config/` in this step, left where it is and
   why, or deferred to a named later item.
2. Left where they are, by the plan: routing regexes (code, with their unit
   tests); secrets (`.env`); llama unit flags and `litellm/config.yaml`
   (listed in the inventory, not moved). The report notes the `ctx_k`
   disagreement between `litellm/config.yaml` and the units and does not
   change it.

### B. The directory

3. `config/` exists in the repo with: one markdown file per agent prompt
   under `config/prompts/` (every agent in `AGENTS`, and the coordinator),
   and YAML files for tuning (round budgets, timeouts, verify windows) and
   music policy as far as those values exist today. Files for values that
   do not exist yet (Whisper prompt sources for M13, lead-in phrases for
   M19, M11's marker words) are not invented; at most a commented stub
   saying which item fills it.
4. Every YAML key carries a comment saying what it does. Every prompt file
   starts with a short header comment (what the prompt is for, which agent)
   that is stripped before use and is not part of the prompt.
5. No secret is in `config/`: no token, key, password or login. A test
   scans the directory for the names of the secrets in `.env.example` and
   for token-shaped strings.
6. `config/` is bind-mounted read-only into `orchestrator`,
   `orchestrator-dev`, `tool_service` and `tool_service-dev`. Production
   and dev read the same files unless the report gives a reason for dev to
   differ.

### C. Loading, validation, reload

7. Values load at startup through a validated schema: unknown keys, wrong
   types, out-of-range numbers, a missing prompt file and an empty prompt
   are errors that name the file and, for YAML, the line.
8. An invalid file never takes effect. At startup with a bad file the
   service starts on built-in defaults equal to today's values and reports
   the error; after a bad reload the last good values stay in effect.
9. `POST /config/reload` exists on the orchestrator and on `tool_service`,
   reloads from disk, and answers with what changed or with the error. It
   is not routed through nginx to the LAN: reachable from the host and the
   compose network only.
10. The error from criteria 7 and 8 is visible on `/services` (the status
    page data), with file and line, and in the logs.
11. Precedence is stated and tested where a value can come from more than
    one place (file, env var, code default). An env var that exists today
    keeps working.
12. One source of truth: after a value moves, the old constant or string is
    gone from the code, or is the documented built-in default the loader
    falls back to and nothing else reads it.

### D. No behaviour change

13. For every agent and the coordinator, a test proves the rendered system
    prompt is byte-identical before and after the move, in every variant
    the code can render (voice and non-voice, each error style, with and
    without origin stamp, any `system_extra`). "Before" is pinned from the
    commit before the build, not regenerated from the new files.
14. The tool menus the models receive, and every tuning value in effect,
    are identical before and after; a test pins the values.
15. The migration is piece by piece, starting with the `home` agent prompt,
    each piece its own commit with its test, so a regression can be
    bisected.
16. `./scripts/run_tests.sh` passes: at least `2694 passed, 2 skipped,
    18 xfailed` plus the new tests. Unit tests do not reach the network.
17. The voice replay on the dev path matches the M10 baseline row by row,
    apart from the known flapping rows.
18. Live on dev: a prompt file edited in a scratch way (one harmless word
    added to one prompt), reloaded, is seen in effect on the next request
    and is then reverted and reloaded; a deliberately invalid YAML value is
    rejected on reload with file and line, shown on dev's `/services`, and
    the old value stays in effect. `config/` in the repo is byte-identical
    at the end.

### E. Docs and safety

19. `README.md` and a `docs/features/` doc describe the directory, the
    schema, precedence, reload and the failure behaviour. `CLAUDE.md`'s
    "Read these" list and its "one source of truth" tenet mention
    `config/` where they name `agents.py` as the home of prompts, without
    rewriting the file.
20. Production is untouched by the build; no HA or Music Assistant
    restart; no sound.

## Reports

Build: `docs/sprint/M21-step1-build.md`. End to end:
`docs/sprint/M21-step1-e2e.md`, one line per criterion with pass, fail or
not testable, and the evidence.

## Coordinator rulings (2026-11-17)

- First-test finding 4 (a reload during a running request can mix old and
  new values within that request) is a requirement for step 2, not a
  failure of step 1. So is atomic writing of config files by step 2's
  editor.
- An unknown `prompts/*.md` is an error, not a warning: accepted.
- The pre-existing name lookups in the test suite are recorded, not M21's
  to fix.
- **Materiality, from the second retest on.** The files in `config/` are
  written by the operator and, after step 2, by an editor behind a
  passphrase. Criteria 7 to 10 fail only on an input that takes a service
  down, keeps it from starting, puts a wrong value into effect, or hides
  the error from both `/services` and the logs. An input that does none of
  those (for example a wrong line number, an unhelpful message, a
  difference between the two YAML libraries that the schema then rejects)
  is an observation.
