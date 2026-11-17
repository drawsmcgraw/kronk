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

## Addendum, fix round 1 (2026-11-17): inventory completion (criterion 1)

Added by the fix agent for `docs/sprint/M21-step1-e2e.md` finding 5. The
table above is left as written. These rows are the values the tester found
missing, plus what a fresh search of `orchestrator/` and `tool_service/`
found (module-level constants and env reads, and literal `timeout=`,
`sleep(`, `max_tokens`, `temperature` and length caps in calls). No value
was found half moved, so none is moved in this round.

| What | Where | Outcome |
|---|---|---|
| Solar inverter-health thresholds: `SOLAR_PRODUCING_FLOOR` 0.03, `SOLAR_FAIL_RATIO` 0.40, `SOLAR_MARGINAL_BAND` 0.15, `SOLAR_MIN_SAMPLES` 8, `SOLAR_BAD_FRACTION` 0.70, `SOLAR_CONFIRM_DAYS` 3, `SOLAR_POLL_MIN` 15, `SOLAR_TZ` | `tool_service/solar.py:37-44` (env with defaults) | **Left where it is**: already env-tunable without a rebuild, like the orchestrator's env knobs in the row above; moving them adds a file-versus-env precedence rule for no edit anyone asked for. Not a named later item; step 2 can show them read-only |
| PVS and HA client timeouts 10 s | `tool_service/solar.py:86`, `:625` | **Left**: per-call infrastructure timeouts, not tuning |
| `WEATHER_REFRESH_SEC` 3600 | `tool_service/main.py:60`; set in `docker-compose.yml:73`, `:312` | **Left**: an env knob compose already sets for both services; same reason as the solar row |
| `MUSIC_TASTE_BUILD_AT` 03:30, `MUSIC_TASTE_TZ`, `MUSIC_TASTE_TAG_TOP_N` 300 | `tool_service/taste.py:271-275` (env) | **Left**: env knobs of the nightly taste build (schedule and batch size), not model-facing |
| `HISTORY_LIMIT` 500, `CONCURRENCY` 8 | `tool_service/taste.py:276`, `:278` | **Deferred to M11**, with `HISTORY_DECAY` and the taste weights in the row above: they shape the same profile M11 reworks |
| `MUSIC_GENRE_ARTIST_RADIO` (off) | `tool_service/taste.py:659` (env) | **Deferred to M11**: a genre-pick policy switch, part of the genre picks M11 reworks |
| `GENERIC_WORDS`, `BUILTIN_GENRES` (the genre list) | `tool_service/taste.py:560`, `:563` | **Deferred to M11**: the "genre list" the row above names for M11; listed here by name |
| `_VOICE_ANOTHER` ("something else", "another one", …) | `tool_service/main.py:1619` | **Deferred to M11**: music marker words, which M11 owns |
| `RESOLVE_TYPES` (artist, album, track, playlist), `_LIBRARY_SCORED` | `tool_service/main.py:1984`, `:2022` | **Left**: the media types the resolver searches and the MA endpoints that list them; protocol, not tuning. M11's single-search design may change them |
| Genre-tagging call timeout 120 s; taste build start delay 120 s | `tool_service/taste.py:371`, `:542` | **Left**: per-call and startup infrastructure; the delay lets MA and the LLMs settle after a restart |
| News: `NEWS_WINDOW_H` 24, `NEWS_PER_FEED` 6, `NEWS_MAX_ITEMS` 36, `NEWS_CHECK_S` 60, `NEWS_TZ` | `tool_service/news.py:38-44` (env) | **Left**: covered by "`NEWS_*` env" in the row above; named here. Env knobs already |
| News: `EDITION_HOURS` (6, 12, 18), `EDITION_LABELS`, `DEFAULT_FEEDS` | `tool_service/news.py:49-61` | **Left**: the edition label is written into the brief's prompt by `build_prompt` (same contract as the news prompt row above), and the feed list is already a data file (`NEWS_FEEDS_FILE`, `DEFAULT_FEEDS` only when it is absent) |
| News: feed fetch timeout 15 s, summarisation timeout 120 s, item summary caps 400 and 250 chars | `tool_service/news.py:134`, `:212`, `:108`, `:117`, `:196` | **Left**: the caps shape the prompt `build_prompt` assembles (same contract); the timeouts are per-call infrastructure |
| tool_service per-call HTTP timeouts: NWS 15, SearXNG 10, `fetch_url` 15, `dot` 30, HA REST 10 (five call sites), MA websocket open/greeting 5, HA websocket close 5 | `tool_service/main.py:275`, `:402`, `:487`, `:708`, `:1445`, `:1951`, `:2273`, `:2430`, `:2528`, `:2192-2193`, `:882` | **Left**: each sits under the orchestrator's per-tool timeout for the same call, which is in `config/tuning.yaml`; listed for step 2 |
| Playback poll interval 1 s | `tool_service/main.py:1740`, `:1877`, `:2317`, `:2478` | **Left**: the step of the verify loop whose window (`music.yaml verify.timeout_s`) is in `config/`; the interval is how the window is measured, not a policy |
| `DNS_TIMEOUT_S` 5.0, `MAX_REDIRECTS` 5 | `tool_service/netguard.py:27-28` | **Left**: covered by "netguard limits" in the row above (M4 safety bounds); named here |
| `MAX_COMMAND_LEN` 4000, `RECURSIVE_ROOT_MIN_DEPTH` 3, `REMOTE_KILL_AFTER_S` 2, ops exec output cap 8000 chars | `tool_service/ops.py:50`, `:122`, `:1464`; `tool_service/main.py:1134` | **Left**: safety bounds of the audited exec path (M5), not tuning |
| `MUSIC_DEFAULT_PLAYER`, `HOME_LOCATION`, `ANNOUNCE_SATELLITE`, `HA_NOTIFY_SERVICE`, `MUSIC_DRY_RUN` | `tool_service/main.py:59`, `:838`, `:1199`, `:1215`, `:2167` (env) | **Left**: wiring (which device, which location, which notify target) and a test switch; env vars, like the model assignments |
| `TEXT_PREVIEW_CHARS` 500 | `orchestrator/telemetry.py:43` | **Left**: how much text a trace records; telemetry, not model-facing |
| `/api/status` and `/api/shopping_list` probe timeouts 5 s, `/api/config` tool_service status timeout 3 s | `orchestrator/main.py:211`, `:1092`; `orchestrator/config_api.py:42` | **Left**: UI and status probes, not tuning; same as the `servers probe 10` row above |
| `_DELEGATE_FLAVOR` ("research agent searching...") | `orchestrator/main.py:268` | **Left**: UI text, with `_STAGE_LABELS` in the row above |
| Log and trace truncations (`[:120]` to `[:2000]` in `agents.py`, `errors.py`, `tools.py`, `telemetry.py`) | throughout | **Left**: log hygiene, not tuning; not listed one by one |
| The caps of `config/` itself: 64 KiB per file, nesting depth 10, 80-char quotes, 50 problems | `orchestrator/config_files.py` = `tool_service/config_files.py` (added in fix round 1) | **Left in code on purpose**: a cap the file could raise is no cap |

`scripts/`, `voice_tracer/`, `hottub_monitor/` and `ha/` were not searched,
as before: criterion 1 names the orchestrator, tool_service, compose,
LiteLLM, the llama units and the taste data, and none of those directories
holds a value the two loaders read.

## Addendum, fix round 2 (2026-11-17): the two classes still without a row (criterion 1)

Added by the fix agent for `docs/sprint/M21-step1-e2e2.md` finding 2. The
tables above are left as written. A row may group values that share a
location and an outcome; every value of the two classes is covered by a
row below. No value moved.

### Tool-result sentences the model reads back

Found by search of `orchestrator/tools.py` (every `return failure(…)`,
`_fail(…)`, `ToolResult(…)` and string-returning handler) and of
`tool_service/` (the sentence helpers, every `HTTPException(detail=…)`,
and the result text each endpoint returns).

| What | Where | Outcome |
|---|---|---|
| The failure framing every handler shares: `failure(text)` and `_fail(action, resp)` → `[<action> failed (HTTP <code>): <detail>]` | `orchestrator/tools.py:505-524` | **Left where it is**: the bracketed prefix is how `errors.py` and the loop recognise a failed tool result (`ToolResult.failed`, M2/M3); the detail is tool_service's own sentence, passed through. Pinned by the M2 tests |
| Handler results and failures for weather, web search, fetch, the four shopping-list tools (`_fail` with each action name; `[Could not fetch <url>: …]`) | `orchestrator/tools.py:527-607` | **Left**: built per call from the live result in the handler that made the call; the action name is the only static part |
| `get_kronk_context` and `generate_diagram` results (`[Could not read context file: …]`, `[Diagram generation failed: …]`) | `orchestrator/tools.py:610-631` | **Left**: same reason |
| Solar status, detail and energy results (`[Solar energy — <period>] no figure available: …`) | `orchestrator/tools.py:634-713` | **Left**: each formats live inverter figures; the sentence and the figures are one string |
| Hot tub results (`[Hot tub status unknown: …]`, `[Hot tub status unavailable]`) | `orchestrator/tools.py:716-736` | **Left**: same reason |
| Music results and argument errors: `play_music`, `control_music`, `rate_music`, `remember_music_preference`, `now_playing` (`[play_music error: query is required]` and the failure texts at `:795`, `:822`, `:849`, `:868`, `:891`) | `orchestrator/tools.py:739-894` | **Left**: the result lines are tool_service's, prefix-coupled to `agents._terminal_speech` (row in the main table); the argument errors are guardrails written with the check that raises them (tenet 5) |
| `remote_exec` and `http_check` results (`… That command was refused (read-only mode). Try a …`, `[http_check <url>: FAILED (<cause>) after …]`) | `orchestrator/tools.py:897-940` | **Left**: safety-path text (M5), written with the check |
| `MM_UPDATE_REFUSAL` and the magic-mirror update results | `orchestrator/tools.py:943-973` | **Left**: fixed text that tells the user the exact sentence `routing._MM_UPDATE_RE` accepts, and `agents.py:171` speaks it by its prefix; the two must change together (M4b). Pinned by `tests/test_spoken_failures.py` |
| `NEWS_REFRESH_FAILED`, `NEWS_UNAVAILABLE` and the news brief result | `orchestrator/tools.py:976-1040` | **Left**: `agents.py:107-112` matches the result by these prefixes to choose `errors.FRIENDLY_NEWS_*`; prefix and match are one contract. Pinned by `tests/test_spoken_failures.py` |
| `[Unknown tool: <name>]`, `[Tool <name> error: <exception>]` | `orchestrator/tools.py:1043-1058` | **Left**: dispatch guardrails (tenet 5) |
| `_not_found_sentence`, `_failure_sentence`, `_no_start_sentence` (the resolver's "closest I found" sentence, a playback failure, "did not start") | `tool_service/main.py:1598-1671`; used at `:1519`, `:1572-1573`, `:1862` | **Left**: each names the most specific cause from the live result (tenet 7), and the "closest I found" threshold and the verify window they name are already in `config/music.yaml`. The wording is pinned by `tests/test_music_resolve.py`, `tests/test_music_players.py` and `tests/test_error_style.py` |
| `HTTPException` details, 60 sites (missing token, bad argument, upstream failure, refused host or command); the orchestrator passes each through `_fail` | `tool_service/main.py:265-2539` (the sites: 265, 283, 292, 390, 410, 416, 424, 450, 687, 711, 713, 716, 731, 741, 754, 784, 797, 1097, 1102, 1105, 1119, 1127, 1131, 1141, 1162, 1335, 1346, 1354, 1379, 1396, 1399, 1413, 1416, 1424, 1440, 1442, 1456, 1519, 1572, 1573, 1826, 1837, 1901, 2119, 2121, 2137, 2263, 2266, 2280, 2290, 2312, 2339, 2424, 2427, 2444, 2449, 2468, 2526, 2534, 2539) | **Left**: each detail is the cause at the point it is known (tenet 7); there is no static sentence to move without splitting it from its cause |
| Result text of the music endpoints: `/music`, `/music/voice`, `/music/taste*`, `/music/control`, `/music/rate`, `/music/now_playing` (the lines `agents._terminal_speech` maps to speech) | `tool_service/main.py:1437-1596`, `:1641-2105`, `:2108-2258`, `:2260-2404`, `:2406-2521`, `:2523-2547` | **Left**: prefix-coupled to `_terminal_speech` (row in the main table); M11 reworks the music path and owns any change to them |
| Result text of the other endpoints: weather, search, fetch and `http_check` (with `netguard.py`'s refusal reasons), shopping list, diagram, solar (with `solar.py`), news (with `news.py`), hot tub, `/ops/exec` (with `ops.py`'s refusal reasons), magic mirror | `tool_service/main.py:377-1165`; `netguard.py`, `solar.py`, `news.py`, `ops.py` | **Left**: built per call from live data; the refusal reasons are the safety bounds' own explanations (M4, M5), written with the check |

### Wiring and on/off env vars in `docker-compose.yml`

Every name set under an `environment:` block in `docker-compose.yml` (42
names), by group. The rows above that already named some of them are
noted.

| What | Where | Outcome |
|---|---|---|
| Service addresses: `HA_URL`, `MA_URL`, `SEARXNG_URL`, `LLM_SERVICE_URL`, `TOOL_SERVICE_URL`, `TEMPO_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT` | `docker-compose.yml:69`, `:77`, `:81`, `:115`, `:163`, `:165`, `:169`, `:210-211`, `:238`, `:310`, `:313`, `:315`, `:332`, `:360-361`, `:364` | **Left where it is**: wiring that differs between production and dev (the dev services point at each other), which is what compose is for; not tuning |
| Telemetry: `TELEMETRY_ENABLED`, `TELEMETRY_RECORD_TEXT`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_BSP_SCHEDULE_DELAY` | `docker-compose.yml:113-117`, `:166-170`, `:235-240`, `:330-334`, `:362-366` | **Left**: named here; the "telemetry vars" of the main table. On/off and exporter settings per service, set differently for dev |
| Magic mirror and ops wiring: `MM_SSH_TARGET`, `MM_SSH_KEY`, `MM_SCRIPT`, `OPS_HOSTS` | `docker-compose.yml:103-106`, `:328` | **Left**: which host, which key, which forced command, which registry (M4, M5); safety wiring, and dev deliberately has no `MM_*` |
| Data paths: `SOLAR_HOST`, `SOLAR_DB`, `MUSIC_TASTE_DB`, `MUSIC_TASTE_FILE`, `NEWS_FILE` | `docker-compose.yml:86`, `:317`, `:320`, `:322-323`, `:325` | **Left**: where each service keeps its data; dev points them at `data/dev/` |
| On/off switches: `MUSIC_TASTE_ENABLED`, `SOLAR_POLL_ENABLED`, `NEWS_ENABLED` | `docker-compose.yml:321`, `:324`, `:326` (dev only) | **Left**: they keep dev from running production's background jobs (the nightly taste build, the inverter poll, the news editions) against shared hardware; a deployment choice, not tuning |
| `VOICE_TRACER_AUTOMATIONS` | `docker-compose.yml:171` | **Left**: which HA automations the voice tracer follows; wiring |
| Already in the main table: `COORDINATOR_MODEL`, `RESEARCH_AGENT_MODEL`, `CODING_AGENT_MODEL`, `DEVOPS_AGENT_MODEL`, `TALKIE_MODEL`, `ROUTER_MODEL`, `ERROR_STYLE`, `LOCATION`, `HISTORY_MAX_MESSAGES`; secrets `HA_TOKEN`, `MA_TOKEN`, `SOLAR_SERIAL` | `docker-compose.yml:78`, `:82`, `:87`, `:164`, `:212-218`, `:226`, `:229`, `:314`, `:316`, `:318` | **Left**, for the reasons in those rows |
| Already in the fix-round-1 addendum: `WEATHER_REFRESH_SEC`, `MUSIC_DEFAULT_PLAYER`, `HOME_LOCATION`, `ANNOUNCE_SATELLITE`, `HA_NOTIFY_SERVICE` | `docker-compose.yml:72-73`, `:95`, `:98`, `:109`, `:311-312`, `:327`, `:329` | **Left**, for the reasons in those rows |
