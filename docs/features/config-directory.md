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
  dependency. Same rules in both.
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
  missing header, empty prompt and a `.md` file in `prompts/` that names no
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
- **An invalid file never takes effect.** Loads are all or nothing. At
  startup a bad file means the service runs on the built-in defaults and
  says so; after a bad reload the last good values stay in effect.
- **Reload.** `POST /config/reload` on each service re-reads the files and
  answers 200 with what changed (`tuning.yaml:agent_loop.max_rounds.research:
  8 -> 6`, or `prompts/home.md: changed (3637 -> 3645 chars)`) or 422 with
  the errors and what stayed in effect. No file watching. From the host:
  `scripts/config_reload.sh dev|prod` (the orchestrator has no host port,
  so it goes through `docker exec`; tool_service is on its loopback port).
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

Routing regexes (`routing.py`, with their unit tests), secrets (`.env`;
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
