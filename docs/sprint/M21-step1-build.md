# M21 step 1 build report: a config directory of plain files

Build agent, 2026-11-17 (UTC), branch `refactor`. Criteria:
`docs/sprint/M21-step1-criteria.md`. Pins taken at `ff99263` (the tree
before the build; `c9cfb9e` plus the criteria commit). Commits, in order:
`4f05264`, `2a22ab4`, `f35899e`, `39a2cb9`, `38870f2`, `bee33ed`,
`fc62089`, `aea08f7`, `a266f2b`, `c8813f5`, `2a12b65`, `cfb7e5f`,
`8ce16c7`, `6c6585c`, `5f97b93`, `5f64c5d`, and the commit that adds this
report.

## Summary

- All seven prompts (six agents and the coordinator) are files under
  `config/prompts/`. Round budgets, the repeat-call nudge threshold, the
  weather-context age and the per-tool HTTP timeouts are in
  `config/tuning.yaml` (orchestrator). The playback verify window and the
  music resolver's thresholds and bonuses are in `config/music.yaml`
  (tool_service).
- Each service has a small loader (`orchestrator/config_loader.py`,
  `tool_service/config_loader.py`) with a strict pydantic schema. Errors
  name file and line; an invalid file never takes effect; `POST
  /config/reload` on each service; status on `/services`.
- No behaviour change: 3390 rendered system-prompt variants, 14 tool menus
  and every moved value are byte-identical to fixtures written before the
  first code change, from the files and from the built-in defaults.
- No new dependency. No change to routing regexes, secrets, llama units,
  `litellm/config.yaml`, `.env`, `.env.dev` or `./data`. Production was
  not touched; only the dev pair was rebuilt.

## Inventory (criterion 1)

Found by search of `orchestrator/`, `tool_service/`, `docker-compose.yml`,
`litellm/config.yaml`, `systemd/` and the live units in
`~/.config/systemd/user/` (read only), and `data/music_taste.yaml` (not
opened; location only).

| What | Where (before) | Outcome |
|---|---|---|
| Agent system prompts: research, home, assistant, coding, talkie, devops | `orchestrator/agents.py` `AGENTS[...]`.system_prompt | **Moved** to `config/prompts/<name>.md`, one commit each |
| Coordinator prompt | `agents.py` `COORDINATOR.system_prompt` | **Moved** to `config/prompts/coordinator.md` |
| Research round budget stated in its prompt (f-string of `RESEARCH_MAX_ROUNDS`) | `agents.py` | **Moved**: `{{max_rounds}}` placeholder, filled from the agent's budget |
| `MAX_TOOL_ROUNDS` 3, `RESEARCH_MAX_ROUNDS` 8 | `agents.py` | **Moved** to `tuning.yaml` `agent_loop.max_rounds` (default, research) |
| `REPEAT_TOOL_NUDGE_AT` 3 | `agents.py` | **Moved** to `tuning.yaml` `agent_loop.repeat_tool_nudge_at` |
| `WEATHER_CTX_MAX_AGE_S` 7200 (env var with default) | `agents.py` | **Moved** to `tuning.yaml` `weather_context.max_age_s`; the env var still wins |
| `TOOL_TIMEOUT_DEFAULT` 15, `TOOL_TIMEOUTS` (10 tools) | `orchestrator/tools.py` | **Moved** to `tuning.yaml` `tool_timeouts` |
| `MUSIC_VERIFY_TIMEOUT_S` 8 (the verify window) | `tool_service/main.py` | **Moved** to `music.yaml` `verify.timeout_s` |
| Resolver: `PLAY_MIN_SCORE` 0.75, `COVERAGE_MIN` 0.80, `PROVIDER_BONUS` 0.05, `LIBRARY_BONUS` 0.03, `_TYPE_BONUS` | `tool_service/music_resolve.py` | **Moved** to `music.yaml` `resolver` |
| `RESOLVE_HINT_MIN_SCORE` 0.5 | `tool_service/main.py` | **Moved** to `music.yaml` `resolver.hint_min_score` |
| Routing regexes: `_URL_RE`, `_MUSIC_URL_RE`, `_TALKIE_PHRASES`, `CLEAR_HISTORY_RE`, `_DIRECT_OVERRIDE`, `_SEARCH_PHRASES`, `_WEATHER_RE`, `_MM_RE`, `_MM_UPDATE_RE`, `_PLAYBACK_RE`, `_MUSIC_RE`, `_RATE_RE`, `_ANOTHER_RE`, `_TASTE_RE`, `_NOW_PLAYING_RE` | `orchestrator/routing.py` | **Left**: the plan keeps them in code with their unit tests (criterion 2) |
| Secrets: `HA_TOKEN`, `MA_TOKEN`, `SOLAR_SERIAL`, `LANGFUSE_*`, `UNIFI_*`, `PANDORA_*`, Grafana admin | `.env`, `.env.telemetry` | **Left**: secrets stay in `.env` (criterion 2); a test scans `config/` |
| llama unit flags: `--ctx-size` (gemma4-e4b 32768, gemma3-4b 8192, devstral-q4 131072, devstral 32768, mistral-nemo 16384, bonsai 32768, talkie 2048), `--reasoning-budget 256` (gemma4-e4b), `--cache-reuse`, `--swa-full`, draft model; no sampling flags are set on any unit (llama.cpp and GGUF defaults) | `~/.config/systemd/user/llama-*.service`, copies in `systemd/` | **Left** (criterion 2). Not edited |
| `litellm/config.yaml` (model names, api_base, display metadata `ctx_k`) | `litellm/config.yaml` | **Left** (criterion 2). Not edited. See the `ctx_k` note below |
| Model assignments: `COORDINATOR_MODEL`, `RESEARCH_AGENT_MODEL`, `CODING_AGENT_MODEL`, `DEVOPS_AGENT_MODEL`, `TALKIE_MODEL`, `ROUTER_MODEL` | env vars in `docker-compose.yml` and `.env.dev`, read in `agents.py` / `servers.py` | **Left**: they name LiteLLM model entries and differ per experiment in `.env.dev` (M17); moving them alone would split one fact across `config/` and `litellm/config.yaml`. Step 2 shows them read-only |
| Other orchestrator env knobs: `ERROR_STYLE`, `ERROR_STYLE_VOICE`, `LOCATION`, `HISTORY_MAX_MESSAGES` 40, `ASSISTANT_PROMPT_CHARS` 1500, `SESSION_IDLE_PRUNE_DAYS` 30, telemetry vars | `docker-compose.yml`, `errors.py`, `sessions.py`, `telemetry.py` | **Left**: already env-tunable without a rebuild, and dev sets several differently in `.env.dev`; moving them adds a precedence rule for no edit anyone asked for |
| Tool descriptions and parameter schemas (22 tools, 45 descriptions) | `orchestrator/tools.py` `TOOL_DEFINITIONS` | **Left**: each description is one contract with its parameter schema and handler; an edit to the text alone can break the call shape. Pinned in `menus.json` |
| `ask_*` tool descriptions (from each agent's `description`) and the agents' `description` / `routing_hint` | `agents.py` | **Left**: `description` is also the `/api/agents` roster text, and the coordinator's delegation was tuned word by word against it (devops/home wording, `docs/sprint/M4-fix1.md`); it stays with the registry. Pinned in `menus.json` |
| `ESCALATE_TOOL`, `ESCALATION_GUIDANCE` | `agents.py` | **Left**: the escalation mechanism's text, coupled to the tool and to `allow_escalation` in the loop (tenet 5) |
| Loop guardrail texts: tool rejection, duplicate-call note, repeat-call stop order, budget-exhausted note, forced-synthesis scrub sentence | `agents.py` `run_stream` | **Left**: structural guardrails, each written with the code path that triggers it (tenet 5) |
| `kronk_facts()` ambient facts, weather-context block | `agents.py` | **Left**: built per request from live values (date, location, forecast) |
| Pipeline system_extra blocks: shared state, attached file, escalation note | `orchestrator/main.py` `_run_pipeline` | **Left**: built per request; pinned |
| `_terminal_speech` / `_news_speech` templates (15 result-line prefixes) | `agents.py` | **Left**: each maps a tool_service result line to speech; prefix-coupled to tool_service's code and pinned by M2 tests |
| `errors.py` sentences (9 friendly sentences, `specialist_failed_block`, `FRIENDLY_TOOL_PHRASING`, `_SERVICE_BY_TOOL`) | `orchestrator/errors.py` | **Left**: the M2 contract ("pinned by tests and the operator greps for them"); rendering rules live beside them |
| Stage labels, narration strings | `main.py` `_STAGE_LABELS`, `agents._tool_narration` | **Left**: UI text, not model-facing |
| `TOKEN_WARNING_THRESHOLD` 2000, `GPU_MEM_MAX_AGE_S` 120, LLM stream timeouts (connect 10, read 600, write 30, pool 10; 300), servers probe 10, weather fetch 2 | `main.py`, `servers.py`, `llm.py`, `agents.py` | **Left**: infrastructure limits, not tuning anyone asked to edit; listed for step 2 |
| News brief prompt (`news.build_prompt`), `max_tokens` 1000, `temperature` 0.4, `NEWS_MODEL`, `NEWS_*` env | `tool_service/news.py` | **Left**: the prompt is assembled per edition around the item list (date, edition label, items) in the function that builds it; moving only its static lines would split one string across two places. Env knobs for model and windows already exist. Not a named later item; step 2 can show it read-only |
| Genre tagging prompt, `temperature` 0.1, `max_tokens` 1500, `MUSIC_TASTE_TAG_MODEL` | `tool_service/taste.py` `tag_artists` | **Left**: it asks for "ONLY a JSON object mapping each artist name", which the same function extracts and parses; the instruction and the parser are one contract. Not a named later item |
| Taste weights (`W_HISTORY`, `W_STATION`, `W_LIBRARY`, `LOVE_BONUS`, `HISTORY_DECAY`, `STATION_BASE`, `PLAYLIST_BASE`, `SEED_BASE`), genre picks (`GENRE_SEARCH_TERMS`, `GENRE_TRIES`, `PICK_MEMORY_S`), feedback (`FEEDBACK_*`, `WATCH_*`) | `tool_service/taste.py`, `tool_service/main.py` | **Not moved this step**: music policy that M11 is about to rework (provider order, marker words, genre list); moving them now would move them twice. Named for M11 |
| Resolver search limits and timeouts (`RESOLVE_SEARCH_LIMIT` 10, `RESOLVE_ARTIST_LIMIT` 50, `RESOLVE_SEARCH_TIMEOUT_S` 8, `RESOLVE_RADIO_LIBRARY_LIMIT` 500), `MA_STATION_LOOKUP_TIMEOUT_S` 10, `MA_LIKE_TIMEOUT_S` 180 (env) | `tool_service/main.py` | **Not moved this step**: latency-coupled to M11's single-search design; named for M11 |
| `HTTP_CHECK_TIMEOUT_S` 5, `OPS_EXEC_TIMEOUT_S` 30, `REMOTE_TIMEOUT_S` 25, `MM_UPDATE_TIMEOUT_S` 600, `HA_WS_TIMEOUT_S` 20, `FETCH_TOKEN_LIMIT`, netguard limits, ops allow-list | `tool_service/main.py`, `ops.py`, `netguard.py` | **Left**: safety bounds (M4, M5), matched to the host-side forced command and the audited exec path; not tuning |
| `data/music_taste.yaml` | `./data` | **Left**: household data with its own loader and writer (`taste.py`); `./data` is out of bounds for this build |
| Whisper prompt sources (M13), marker words and provider order (M11), lead-in phrases (M19), TTS choice (M20) | do not exist yet | **Not invented** (criterion 3). No stub files: `config/` has only files a loader reads |

**`ctx_k` disagreement (criterion 2, not changed).** `litellm/config.yaml`
carries display metadata `ctx_k` that does not match the live units:
`gemma-4-e4b` 128 against `--ctx-size 32768` (32k); `gemma-3-4b` 128
against 8192 (8k). The other entries agree (`devstral-2512-q4` 128 =
131072, `devstral-2512` 32, `mistral-nemo` 16, `bonsai-8b` 32, `talkie`
2). The `/services` page shows the `ctx_k` value, so it currently
overstates two context windows. Left as is.

**`kronk-context.md` and the assistant agent (plan, "check first").** The
assistant reads `kronk-context.md` through `get_kronk_context`. It does
not read `config/`. This build added one sentence to `kronk-context.md`
saying where prompts and tuning live. Having the assistant read the
values themselves (so "what is my current setting for X" works by voice)
would be a new tool; not built, not invented.

## The schema

`config/prompts/<name>.md`, one per name in `prompt_defaults.PROMPTS`
(= `AGENTS` plus `coordinator`): must start with `<!--`, header closed by
`-->`, stripped. Body = the rest with leading and trailing newlines
removed, byte for byte. Placeholders: `{{max_rounds}}` only.

`config/tuning.yaml` (`orchestrator/config_loader.py`, `Tuning`):

```
agent_loop:
  max_rounds:            default: int 3; coordinator, research (8), home,
                         assistant, coding, talkie, devops: int | null; 1..20
  repeat_tool_nudge_at:  int 3; 2..20
weather_context:
  max_age_s:             int 7200; 60..86400  (env WEATHER_CTX_MAX_AGE_S wins)
tool_timeouts:
  default_s:             float 15; >0..600
  per_tool:              one field per tool in tools.TOOL_DEFINITIONS,
                         float | null; >0..600 (ten set, as before)
```

`config/music.yaml` (`tool_service/config_loader.py`, `Music`):

```
verify:
  timeout_s:             int | float 8; >0..60
resolver:
  play_min_score:        float 0.75; 0..1
  coverage_min:          float 0.80; 0..1
  provider_bonus:        float 0.05; 0..0.2
  library_bonus:         float 0.03; 0..0.2
  type_bonus:            artist 0.02, album 0.015, playlist 0.01,
                         track 0.0, radio 0.0; each 0..0.2
  hint_min_score:        float 0.5; 0..1
```

Every model: `extra="forbid"`, `strict=True`. Field defaults are the
built-in defaults and equal the values before the move. Duplicate keys,
YAML syntax errors, a non-mapping document and a missing file are errors.
Loads are all or nothing.

## What moved, commit by commit (criterion 15)

| Commit | Piece | Pins |
|---|---|---|
| `4f05264` | Pinning tests and fixtures, from `ff99263`, before any code change | written; 5 green on the old tree |
| `2a22ab4` | Loader, `/config/reload`, `/api/config`, `/services` panel, compose mounts, nginx deny, `scripts/config_reload.sh`; **home prompt** | green |
| `f35899e` | coordinator prompt | green |
| `39a2cb9` | assistant prompt | green |
| `38870f2` | coding prompt | green |
| `bee33ed` | talkie prompt | green |
| `fc62089` | devops prompt | green |
| `aea08f7` | research prompt (`{{max_rounds}}`) | green |
| `a266f2b` | round budgets and repeat-call nudge → `tuning.yaml` | green |
| `c8813f5` | weather-context age → `tuning.yaml`, env precedence | green |
| `2a12b65` | per-tool HTTP timeouts → `tuning.yaml` | green |
| `cfb7e5f` | tool_service loader and endpoints; verify window → `music.yaml` | green |
| `8ce16c7` | resolver thresholds and bonuses → `music.yaml` | green |
| `6c6585c` | tool_service loads at startup (was first use); orchestrator logs the outcome | green |
| `5f97b93` | log wording fix ("kept kept", seen on dev) | green |
| `5f64c5d` | docs | green |

Each prompt commit ran `./scripts/run_tests.sh` (2723 passed each time)
and the pins file alone (6 passed) before committing.

## Evidence by criterion

| # | Evidence |
|---|---|
| 1 | Inventory table above, by search; every row says moved, left and why, or not moved and the later item |
| 2 | Routing regexes, secrets, llama unit flags and `litellm/config.yaml` are listed and untouched (`git diff ff99263 -- orchestrator/routing.py litellm/ systemd/` is empty). The `ctx_k` disagreement is noted above and not changed |
| 3 | `config/prompts/{coordinator,research,home,assistant,coding,talkie,devops}.md`; `config/tuning.yaml` (round budgets, nudge, weather age, timeouts); `config/music.yaml` (verify window, resolver). No files for M11, M13, M19 values; no stubs |
| 4 | Every YAML key has a comment (`test_every_yaml_key_has_a_comment`). Every prompt file starts with a `<!-- -->` header naming the agent and purpose, stripped before use (`test_every_prompt_file_has_a_header_that_is_not_sent`, `test_prompt_body_is_exact_minus_surrounding_blank_lines`) |
| 5 | `test_config_directory_holds_no_secrets`: names from compose `${NAME}` interpolations, every `.env*.example` key, and the `.env` key names (listed by name only); token shapes: JWT, 32+ hex, `sk-`/`pk-` keys, PEM private keys, `password=`/`token:` assignments |
| 6 | `./config:/config:ro` in `orchestrator`, `orchestrator-dev`, `tool_service`, `tool_service-dev` (`test_config_is_mounted_read_only_into_all_four_services`). Production and dev read the same files from the working tree; see "Promotion" for the release case |
| 7 | Unknown key (`config/tuning.yaml:4: agent_loop.max_rounds.reserch: unknown key`), wrong type (`'3'`, `true`, `'8'`), out of range, missing prompt file, empty prompt, missing header, unknown placeholder, unknown prompt file, duplicate key, YAML syntax error, missing YAML file: one test each in `tests/test_config_loader.py` and `tests/test_tool_service_config.py`, each asserting file and line |
| 8 | `test_bad_file_at_startup_runs_on_builtin_defaults` (both services), `test_bad_reload_keeps_the_last_good_values`, `test_bad_tuning_reload_keeps_budget`, `test_builtin_defaults_render_the_pinned_prompts_and_values` (defaults equal the pins). Live on dev: below |
| 9 | `POST /config/reload` on the orchestrator (`config_api.py`) and tool_service; 200 with changes or 422 with errors (`test_reload_endpoint_*`, `test_reload_and_status_endpoints`). nginx: `location ^~ /config/` and `^~ /dev/config/` return 404 (needs the nginx restart at promotion; checked with `nginx -t` in a throwaway container). The orchestrator also refuses proxied requests: live, `POST http://localhost/dev/config/reload` through today's nginx answered `403 {"detail":"/config/reload is not served through nginx; call it from the host (scripts/config_reload.sh) or the compose network"}`. tool_service: port bound to 127.0.0.1, nginx proxies only `/api/solar/` |
| 10 | `GET /api/config` merges both services' status; `/services` has a Config section that fetches `api/config` relative, so `/dev/services` shows dev. Live dev output below. Errors are logged at ERROR and emitted as `config_error` |
| 11 | Only `WEATHER_CTX_MAX_AGE_S` had two sources: env > file > default, tested (`test_env_var_wins_over_the_file`, `test_file_wins_over_the_default_when_env_is_unset`, `test_bad_env_var_is_an_error_naming_the_variable`, `test_builtin_defaults_keep_a_valid_env_var`) |
| 12 | Every moved constant is gone: `grep` for `MAX_TOOL_ROUNDS`, `RESEARCH_MAX_ROUNDS`, `REPEAT_TOOL_NUDGE_AT`, `WEATHER_CTX_MAX_AGE_S` (code), `TOOL_TIMEOUT`, `MUSIC_VERIFY_TIMEOUT_S`, `PLAY_MIN_SCORE`, `COVERAGE_MIN`, `PROVIDER_BONUS`, `LIBRARY_BONUS`, `_TYPE_BONUS`, `RESOLVE_HINT_MIN_SCORE` in `orchestrator/ tool_service/ tests/ scripts/` finds none outside comments and the pin accessors. Prompt texts are only in their files and in `prompt_defaults.py`, the documented default read only on a startup failure (`test_moved_prompts_are_gone_from_agents_py`) |
| 13 | `tests/test_m21_pins.py`: 3360 `run_stream` variants (7 agents × error style debug/friendly/derived × voice × origin stamp × escalation × 5 `system_extra` blocks × weather × context) plus 30 `_run_pipeline` captures (direct, voice with origin, web UI local context, home ok, escalated, failed in debug and friendly). Fixtures written at `ff99263` before any code change; green after every commit, from the files and from the built-in defaults |
| 14 | `menus.json` (14 menus: every agent and the coordinator, with and without escalation) and `values.json` (round budgets, terminal tools, tool lists, nudge, weather age, all 22 tool timeouts, verify window, resolver values) unchanged and green |
| 15 | Commit table above: home first, one piece per commit, pins run each time |
| 16 | `2757 passed, 2 skipped, 18 xfailed, 6 warnings in 17.47s` (baseline 2694 + 63 new). The new tests use fakes for every HTTP client; the live checks were not unit tests |
| 17 | Replay below: every baseline row matches |
| 18 | Live on dev, below |
| 19 | `README.md` (Prompts and tuning section, runbook line, loop diagram), `docs/features/config-directory.md`, `CLAUDE.md` Read-these item 3 and tenet 8 (two edits, nothing else), `kronk-context.md` one sentence |
| 20 | Production containers kept their uptime (created before the build, from release `sprint-M2`, no `/config` mount); only `tool_service-dev` and `orchestrator-dev` were rebuilt, by name. No HA or MA restart (HA up 28 h, MA 12 days). No sound: dry run on dev, replay "players unchanged (17 checked)" |

## Criterion 18, live on dev

Coordinator prompt tokens from dev's own `data/dev/metrics.db` (read
only), one request each through `/dev/api/chat` ("What is the capital of
France? One word."):

```
baseline: reply='Paris' in 1.8s; rows=[(9120, '2026-11-17 04:02:02', 'coordinator', 'gemma-4-e4b', 2016)]
baseline2: reply='Paris' in 0.1s; rows=[(9121, '2026-11-17 04:02:07', 'coordinator', 'gemma-4-e4b', 2016)]
```

Scratch edit: " Indeed." appended to the last line of
`config/prompts/coordinator.md`, then `scripts/config_reload.sh dev`:

```
== orchestrator (kronk-orchestrator-dev-1)
{"ok":true,"changed":["prompts/coordinator.md: changed (2339 -> 2347 chars)"]}

== tool_service (127.0.0.1:8103)
{"ok":true,"changed":[]}
exit=0
after-edit: reply='Paris.' in 1.0s; rows=[(9122, '2026-11-17 04:02:08', 'coordinator', 'gemma-4-e4b', 2018)]
```

Reverted (byte copy of the original), reloaded:

```
{"ok":true,"changed":["prompts/coordinator.md: changed (2347 -> 2339 chars)"]}
after-revert: reply='Paris.' in 0.6s; rows=[(9123, '2026-11-17 04:02:16', 'coordinator', 'gemma-4-e4b', 2016)]
```

Invalid values: `weather_context.max_age_s: '60'` (a string) in
`tuning.yaml` and `verify.timeout_s: eight` in `music.yaml`, reloaded:

```
== orchestrator (kronk-orchestrator-dev-1)
422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":32,"message":"weather_context.max_age_s: Input should be a valid integer (got '60')"}],"kept":"last good values (files, loaded 2026-11-17 04:02:16)"}

== tool_service (127.0.0.1:8103)
{"ok":false,"errors":[{"file":"config/music.yaml","line":19,"message":"verify.timeout_s: Input should be a number (got 'eight')"}],"kept":"last good values (files, loaded 2026-11-17 04:02:16)"}
exit=1
```

Dev's `/services` data (`http://localhost/dev/api/config`) at that moment:

```
orchestrator ok= False event= reload source= files kept= last good values (files, loaded 2026-11-17 04:02:16)
    {'file': 'config/tuning.yaml', 'line': 32, 'message': "weather_context.max_age_s: Input should be a valid integer (got '60')"}
tool_service ok= False event= reload source= files kept= last good values (files, loaded 2026-11-17 04:02:16)
    {'file': 'config/music.yaml', 'line': 19, 'message': "verify.timeout_s: Input should be a number (got 'eight')"}
```

Old value still in effect: the next request still carried the weather
block (`after-bad-reload: ... 2016`). For contrast, a *valid*
`max_age_s: 60` took effect and dropped the 34-minute-old forecast from
the prompt (`valid-60: ... 1361`); restoring both files and reloading
gave `2016` again. Log lines on dev:

```
2026-11-17 04:02:37,673 ERROR config_loader: config on reload rejected: config/tuning.yaml:32: weather_context.max_age_s: Input should be a valid integer (got '60') (kept last good values (files, loaded 2026-11-17 04:02:16))
ERROR:tool_service.config:config on reload rejected: config/music.yaml:19: verify.timeout_s: Input should be a number (got 'eight') (kept last good values (files, loaded 2026-11-17 04:02:16))
```

(The doubled "kept" in the `config_error` event seen here was fixed in
`5f97b93`; the dev pair was rebuilt after it.) At the end, `sha256sum` of
every file under `config/` equals the committed files, `git status
config/` is clean, and dev's `/api/config` reports both services `ok`,
`source files`.

## Tests and replay, verbatim

```
$ ./scripts/run_tests.sh -q
2757 passed, 2 skipped, 18 xfailed, 6 warnings in 17.47s
```

```
$ python3 scripts/voice_replay.py --label m21-build
voice_replay: {'pass': 99, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_040407_m21-build.json
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`
(baseline `{'pass': 91, 'xfail': 20, 'fail': 1, 'skip': 2}`), row by row:
all 114 baseline rows have the same status except
`inc-0930-search-deep-house-ytm` fail → pass (one of the known "YouTube
Music" search flappers). The 8 rows added to the corpus since M10
(`m4b-*`) are new: 7 pass, 1 skip. No row failed, no row was skipped for
player activity, and the replay ran once.

## Departures from the criteria, and why

- **The orchestrator's endpoints live in `config_api.py`, not `main.py`.**
  The reload refusal reads a request header, and
  `tests/test_voice_signal.py::test_the_mount_is_the_only_producer` (M2)
  forbids raw `Request` and `.headers` in `main.py`, so voice can never be
  derived from request content. The router keeps that guard meaningful.
- **Two layers keep reload off the LAN.** nginx's deny rule only takes
  effect when nginx restarts (a production container; not done in this
  build), so the handler also refuses requests nginx proxied. That is
  what made criterion 9 testable on dev now.
- **Prompt defaults duplicate the shipped prompts.** Criterion 8 asks for
  built-in defaults equal to today's values; for prompts that means a
  copy in code (`prompt_defaults.py`), read only on a startup failure.
  A baked copy of `config/` in the image was considered and rejected: it
  needs a build-context change and interacts badly with linking `config/`
  into releases.
- **Invalid `WEATHER_CTX_MAX_AGE_S` behaves differently.** Before, a
  non-integer crashed the orchestrator's import; now it is a config error
  naming the variable and the service runs on the defaults. Valid values
  behave as before.
- **Model assignments are not in the value pins.** They are env vars that
  were not moved, and their test-time value depends on import order.
- **Registry hydration.** `AgentConfig.system_prompt` defaults to `""` and
  `max_rounds` to the configured default; `agents.apply_config` sets both
  on every registry agent at import and after each reload. Existing tests
  that read a removed constant (`test_agentic_loop.py`,
  `test_music_resolve.py`) or patched `MUSIC_VERIFY_TIMEOUT_S` (six files,
  35 sites) now read or patch the config value; the change is mechanical
  and no assertion changed.

## Not moved, and why

Listed in the inventory. In short: routing regexes, secrets, llama flags
and `litellm/config.yaml` by the plan; model assignments and the other
env knobs because they are already env-tunable and differ per experiment;
tool descriptions, terminal-speech templates, `errors.py` sentences,
escalation and loop guardrail texts because each is one contract with the
code beside it; the two tool_service LLM prompts because each is built
with, or parsed by, the code beside it; taste weights, genre and feedback
timings and resolver search limits for M11, which reworks them; safety
bounds (exec, fetch, http_check timeouts) as not tuning. No value is half
moved: each moved value has exactly one live source and one documented
default.

## Promotion: what the coordinator must do

1. Deploy the tag with `scripts/deploy_tag.sh deploy <tag>`. It rebuilds
   `orchestrator` and `tool_service` (new code, new `/config` mount) and
   restarts nginx, which loads the `/config/` deny rule. No HA or MA
   restart is involved.
2. Verify: `scripts/config_reload.sh prod` answers `{"ok":true,
   "changed":[]}` for both; `curl -s -o /dev/null -w '%{http_code}' -X
   POST http://localhost/config/reload` answers `404` (nginx);
   `http://localhost/api/config` shows both services `ok`, `source
   files`; `/services` shows the Config section; the `/api/chat` shim
   answers (the script's own check).
3. **`config/`: take it from the tag for this promotion; link it when
   step 2 ships.** `config/` is tracked, so `git archive` carries it and
   the release's `./config` mount reads the tag's copy with no script
   change. That is right for step 1: production runs the prompts and
   values that were pinned and reviewed at the tag, and an uncommitted
   edit on `refactor` cannot reach production on its next restart.
   Criterion 6's "same files" then holds by content at the tag, not by
   path. Step 2 is different: `/settings` writes `config/`, reloads, and
   commits in the live checkout. A release copy is not a git checkout and
   is replaced on the next deploy, so edits made there would be lost.
   When step 2 is promoted, add `config` to `LINKS` in
   `scripts/deploy_tag.sh` (and decide how the writer gets write access;
   today's mount is read-only).
4. When the first real edit to a prompt or a moved value lands (by hand
   or by step 2), `tests/test_m21_pins.py`'s file-based pins will fail by
   design. Retarget them then: keep the defaults pin, and pin the files
   against the edited text, or drop the file pins. Step 2 should plan for
   this.
