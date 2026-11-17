# Feature: the config/ directory (prompts and tuning as plain files)

**Shipped:** 2026-11-17 (sprint M21 step 1, branch `refactor`) · **Plan:**
`Kronk evaluation findings and implementation plan.md`, section M21 ·
**Criteria:** `docs/sprint/M21-step1-criteria.md` · **Build report:**
`docs/sprint/M21-step1-build.md`. Step 2 (the `/settings` page) is not
built yet.

## What it does

Every agent prompt and the tuning values that were Python constants now
live in plain files under `config/` in the repo, so one place shows what
the models are told and how the loop is tuned:

| File | Read by | Holds |
|---|---|---|
| `config/prompts/<name>.md` | orchestrator | The system prompt of each agent in `agents.AGENTS` (research, home, assistant, coding, talkie, devops) and of the coordinator |
| `config/tuning.yaml` | orchestrator | Round budgets per agent, the repeat-call nudge threshold, the weather-context age, per-tool HTTP timeouts |
| `config/lead_in.yaml` | orchestrator | The words of the "ask Kronk to" lead-in: verbs, names, leading fillers (sprint M19, `docs/features/lead-in.md`) |
| `config/music.yaml` | tool_service | The playback verify window and the music resolver's thresholds and bonuses |

The move changed no behaviour: `tests/test_m21_pins.py` renders 3390
system-prompt variants, 14 tool menus and every moved value and compares
them with fixtures written from the tree before the move.

## How it works

- **Mount.** `./config:/config:ro` in `orchestrator`, `orchestrator-dev`,
  `tool_service` and `tool_service-dev`. Production and dev read the same
  files. `KRONK_CONFIG_DIR` overrides the path (tests point it at the
  repo's `config/`).
- **Loaders.** `orchestrator/config_loader.py` (PyYAML) and
  `tool_service/config_loader.py` (ruamel.yaml). Two small modules rather
  than a shared package: each service builds its own image from its own
  directory, and each already pinned a different YAML library. No new
  dependency. Same rules in both. What the two must agree on lives in
  `config_files.py`, which is **byte-identical** in `orchestrator/` and
  `tool_service/` (a test compares them): bounded reading, and the YAML
  dialect.
- **One YAML dialect.** Neither library's type resolution is used. Each
  library only tokenises; `config_files.build()` walks its parse events
  and decides what a value is: `null`/`~`, `true`/`false`, decimal
  integers without leading zeros, and decimal floats. Everything else is
  a string, so YAML 1.1 surprises (`2:00:00`, `0o17`, `017`, `0x1f`,
  `7_200`, `yes`, `on`, `.inf`) reach the schema as strings and a numeric
  field rejects them. Quoted scalars are always strings. Anchors,
  aliases, merge keys (`<<`) and explicit tags (`!!int`) are errors;
  config files need none of them. The two libraries do not tokenise every
  text alike, so what they disagree on is refused before either sees the
  text: control characters other than newline and tab (U+0085 included),
  U+2028 and U+2029, lone surrogates, U+FFFE and U+FFFF, each named with
  its line and byte offset (an ESC after `max_age_s: 7200`:
  `config/tuning.yaml:32: control character U+001B at byte 1627 is not
  allowed in a YAML file (only newline and tab)`; a CRLF line end counts as
  one byte). An empty key (`? ` alone) is an error, and tool_service
  runs ruamel's scanner in YAML 1.1 mode so `{a?b: 1}` is refused by both.
  A shared table and 2,000 generated texts run through both loaders in
  the tests; both accept or both refuse, with the same data and lines
  when accepted. A YAML error the library cannot place says `line
  unknown`.
- **Bounds.** Each file is checked with `lstat` before a byte is read: it
  must be a regular file (not a directory, pipe or device), no path
  component under `config/` may be a symlink, and it must be at most
  64 KiB (`MAX_YAML_BYTES`, `MAX_PROMPT_BYTES`; the largest file today is
  4.2 kB). The read itself is capped at the same size. Nesting deeper
  than 10 collections is an error, raised before the parser reads further.
  A rejected value is quoted in the error up to 80 characters, and a load
  reports at most 50 problems plus a count of the rest. Under these caps
  the worst parse measured is 0.3 s, which bounds how long a reload holds
  the service (reload runs on the event loop).
- **No file takes a load down.** Any exception at all while loading a
  file (a parser `RecursionError`, a `MemoryError`, a library bug) becomes
  a config error for that file. `start()` never raises; `reload()` always
  returns.
- **Every answer can be encoded.** A file name, key, value or parser
  message can hold text JSON cannot carry (a name that is not UTF-8
  decodes to lone surrogates; so does a `"\uD800"` key). Each `Problem`
  passes its file and message through `config_files.safe()` when it is
  built: lone surrogates, control and format characters are escaped
  visibly (`config/caf\udce9.yaml`) and the length is capped; key names
  are cut to 80 characters like values. As a second layer, both services'
  `POST /config/reload` and status endpoints check the answer encodes and,
  if not, answer JSON saying the config status could not be rendered (and,
  for a reload, whether it took effect), never a bare 500. So the reload
  endpoint answers 200 or 422.
- **A file saved while it is read.** An editor that saves in place
  truncates and then writes. Each file's size and mtime are compared
  before and after the read, and with the number of bytes read; if they
  differ the file is read once more, and a second change is the error
  `file changed during read`. A read that falls wholly inside the moment
  between the truncate and the first write sees a steady empty file and
  loads it (with the warning `all defaults (file has no keys)`); only an
  atomic write (write a new file, rename it into place) closes that.
- **Schema.** pydantic models with `extra="forbid"` and `strict=True`:
  an unknown key, a wrong type (`"8"` is not `8`, `true` is not `1`) and a
  number outside its bounds are errors. Round budgets and per-tool
  timeouts are one field per agent or tool, so a misspelt name is an
  unknown key; tests check the field names against `AGENTS` and
  `tools.TOOL_DEFINITIONS`. Duplicate YAML keys are errors. A key left out
  takes its built-in default.
- **Prompt files.** Each starts with a `<!-- … -->` header (what the
  prompt is for, which agent), which is stripped. The rest, minus leading
  and trailing blank lines, is the prompt, byte for byte. Missing file,
  missing header, empty prompt (also: only further `<!-- -->` comments
  after the header), a control character other than newline and tab
  (NUL, ESC, form feed, …) and a `.md` file in `prompts/` that names no
  agent are errors. One placeholder exists: `{{max_rounds}}`, filled with
  the agent's round budget (the research prompt states its budget this
  way, so the prompt and the loop cannot disagree). Any other `{{name}}` is
  an error.
- **Built-in defaults.** The pydantic `Field` defaults and
  `orchestrator/prompt_defaults.py` hold the values as they were before
  the move. They are read only when `config/` fails validation at startup.
  Editing a value means editing its file, never the defaults.
- **Errors.** Every problem found, not just the first, with file and
  1-based line (`config/tuning.yaml:32: weather_context.max_age_s: Input
  should be a valid integer (got '60')`). Logged at ERROR, emitted as a
  `config_error` event, and shown on `/services` (Config section, data
  from `GET /api/config`, which merges the orchestrator's status with
  tool_service's `GET /config/status`).
- **Warnings.** Two things load but are reported as warnings, on
  `/services` (the card's badge says `warning`) and in the reload answer
  (`"warnings": [...]`): a YAML file with no keys (empty or comments only;
  every value at its default) as `all defaults (file has no keys)`, and
  any entry under `config/` or `config/prompts/` that no service reads (a
  misspelt `tunning.yaml`, a `home.md.bak`). The orchestrator scans the
  whole directory once, so a stray shows once; tool_service does not
  scan.
- **An invalid file never takes effect.** Loads are all or nothing. At
  startup a bad file means the service runs on the built-in defaults and
  says so; after a bad reload the last good values stay in effect.
- **Reload.** `POST /config/reload` on each service re-reads the files and
  answers 200 with what changed (`tuning.yaml:agent_loop.max_rounds.research:
  8 -> 6`, or `prompts/home.md: changed (3637 -> 3645 chars)`) or 422 with
  the errors and what stayed in effect. No file watching. From the host:
  `scripts/config_reload.sh dev|prod` (the orchestrator has no host port,
  so it goes through `docker exec`; tool_service is on its loopback port);
  it prints `<HTTP code> <body>` for each service.
- **Not on the LAN.** nginx returns 404 for `/config/` and `/dev/config/`.
  The orchestrator's handler also refuses (403) any request carrying
  `X-Real-IP` or `X-Forwarded-For`, which nginx sets on everything it
  proxies; that covers the time before nginx is restarted with the deny
  rule. tool_service's port is bound to 127.0.0.1 and nginx proxies only
  `/api/solar/` to it.
- **How values reach the code.** The prompts and round budgets are
  copied onto the agent registry by `agents.apply_config`, at import and
  after every successful reload, so `run_stream` reads
  `agent.system_prompt` and `agent.max_rounds` as before. Everything else
  is read at use: `config_loader.current().tuning…` in the orchestrator,
  `config_loader.current().music…` and `music_resolve.policy()` in
  tool_service.

## Precedence

Only one moved value had another source: `WEATHER_CTX_MAX_AGE_S`. For it,
env var > `tuning.yaml` > built-in default (`config_loader.ENV_OVERRIDES`).
An invalid env value is a config error naming the variable (it used to
crash the import). No new env vars were added. Model assignments,
`ERROR_STYLE`, `HISTORY_MAX_MESSAGES`, `LOCATION` and the other env knobs
stay env vars.

## What is not in config/

Routing regexes (`routing.py`, with their unit tests; since M19 the
lead-in's words are in `lead_in.yaml`, escaped into its pattern, so no file
holds a regex), secrets (`.env`;
`tests/test_config_loader.py` scans `config/` for secret names and
token-shaped strings), llama unit flags and `litellm/config.yaml`, tool
descriptions, `_terminal_speech` templates, `errors.py` sentences, the
loop's guardrail texts and the two tool_service LLM prompts. The build
report has the full inventory and the reason for each.

## Gotchas

- **The pins guard the migration, not the future.** `test_m21_pins.py`
  compares the shipped files with the pre-move rendering. The first real
  edit to a prompt or a moved value (by hand, or by step 2's `/settings`)
  makes it fail by design; that edit must retarget the pin (see the build
  report). `test_builtin_defaults_render_the_pinned_prompts_and_values`
  keeps guarding the defaults.
- **A restart reloads too.** Reload is the only *runtime* trigger, but a
  container restart reads the files fresh. With `config/` linked from the
  working tree into a release, an uncommitted edit reaches production on
  its next restart.
- **Cross-file coupling.** The orchestrator's `tool_timeouts` for the
  music tools must stay above `music.yaml verify.timeout_s`; no loader
  checks across files.
- **Symlinks are refused on purpose.** Linking a config file to a copy
  elsewhere is an error, not a shortcut: in a container the link target
  usually does not exist, and a target that does exist would be read from
  outside the reviewed directory. The `config/` directory itself may be a
  symlink (a release that links it); only what is under it is checked.
- **Caps live in code.** The size and depth limits are in
  `config_files.py`, not in `config/`: a cap the file could raise is no
  cap.
- **Prompt cache.** A prompt edit invalidates llama.cpp's cached prefix
  once; the next request pays for it.

## Blog hooks

- Moving 7 prompts out of Python without changing a byte: pin 3390
  rendered variants first, then move one piece per commit.
- "Strict" means strict: why `"3"`, `true` and a misspelt key must be
  errors in a file a human edits from a phone.
- An invalid file never takes effect: all-or-nothing loads, built-in
  defaults at startup, last good values on reload.
- Measuring a one-word prompt edit live with nothing but prompt token
  counts (2016 → 2018 → 2016).
