# M1 build report: remove the health and finance features

Build agent, 2026-11-15, branch `refactor`. Criteria: `docs/sprint/M1-criteria.md`.
Commits: `6b5db69` (orchestrator, tests, benches), `a9b754f` (services,
compose, nginx, deploy script), `71ea14d` (docs), and the commit that adds
this report.

## Summary

- The `health` and `finance` agents, their four tools, `health_service`,
  `finance_service`, `retire_calc`, the `/health`, `/finances` and `/retire/`
  pages, their nginx routes and the upload forwarding are gone from the
  branch.
- The coordinator prompt says Kronk keeps no health, sleep, fitness or
  financial records. Without that line it sent "how did I sleep last night"
  to `ask_home` 8 times in 10. With it: 10/10 in the bench, 20/20 on the dev
  shims, no figures, no delegation.
- Delegation accuracy for the surviving agents is not worse: 33/36 before,
  34/36 after on the delegation probes.
- Tests: 1111 passed, 2 skipped, 18 xfailed before; 989 passed, 2 skipped,
  18 xfailed after. Every removed test is accounted for below.
- Voice replay on dev: same totals as the committed baseline (91 pass,
  20 xfail, 1 fail, 2 skip). The one fail moved between two rows of the same
  known-variance class; reruns pass and fail both ways.
- Nothing under `./data` was deleted. No production container was stopped,
  removed or recreated; only `kronk-orchestrator-dev-1` was recreated.
  Production nginx was not restarted.
- The deletion manifest and the promotion commands are below. Promotion is
  the coordinator's step.

## What was removed and ported

### Orchestrator (`6b5db69`)

| File | Change |
| --- | --- |
| `orchestrator/agents.py` | Removed `AGENTS["health"]`, `AGENTS["finance"]`, `HEALTH_AGENT_MODEL`, `FINANCE_AGENT_MODEL`, the `query_health` / `query_finances` narration lines. `ask_health` and `ask_finance` disappear with the entries (`agent_tool_defs()` derives them). Coordinator prompt: the delegable list is now "the household's own data (shopping list, home devices, music, solar, weather)"; added the no-specialist rule and the no-records sentence (see Bench). |
| `orchestrator/tools.py` | Removed the definitions, handlers, `_HANDLERS` entries and the `query_finances` timeout for `query_health`, `query_finances`, `query_bloodwork`, `search_health_data`; removed `HEALTH_SERVICE_URL`, `FINANCE_SERVICE_URL`. 21 tools remain; definitions and handlers match one to one. Pure deletions. |
| `orchestrator/main.py` | Removed the `/finances` and `/health` page routes, `_GARMIN_CSV_SIGNATURES`, `_is_garmin_csv`, `_is_garmin_export_zip`, `_is_labcorp_report`, `_forward_to_health` and the three forwarding branches in `/files`. Added: a zip (by name or `PK\x03\x04`) gets 422 "Zip archives aren't supported. Upload a UTF-8 text file or a PDF." A small stored zip decodes as UTF-8 and would otherwise land in the prompt as junk. Garmin CSVs and LabCorp PDFs take the normal text/PDF path. `/api/status` unchanged. |
| `orchestrator/static/health.html`, `finances.html` | Deleted. |
| `orchestrator/static/index.html` | Status dot polls `/api/status` (C7). Removed the `routed_to === 'health_service'` upload branch and `.zip` from the file picker's `accept`. |
| `orchestrator/static/services.html` | Removed the Health, Finances, Retirement Calc page entries and the Health Service, Finance Service rows. |
| `orchestrator/static/agents.html` | Removed the Health and Finance agent cards, their chips on the coordinator card, their probe dots and model map lines. The page is otherwise stale (Ollama-era model names); not changed here. |

### Services and wiring (`a9b754f`)

| File | Change |
| --- | --- |
| `docker-compose.yml` | Removed services `retire_calc`, `health_service`, `finance_service`; nginx `depends_on: retire_calc`; orchestrator `HEALTH_SERVICE_URL`, `FINANCE_SERVICE_URL`, `HEALTH_AGENT_MODEL`, `FINANCE_AGENT_MODEL` and `depends_on` on both services; orchestrator-dev's `HEALTH_SERVICE_URL`, `FINANCE_SERVICE_URL` and their comment. |
| `nginx/nginx.conf` | Removed `/api/health/`, `/api/finance/`, `/probe/health`, `/probe/finance`, `/probe/retire`, `/retire/`. The `client_max_body_size 500m` comment now says it was sized for the old Garmin zips; the value is unchanged. |
| `health_service/`, `finance_service/` | Deleted (22 tracked files; the only untracked content was `__pycache__`). |
| `scripts/deploy_tag.sh` | Removed the block that linked `../retirement-calc` into the releases directory. |
| `.env.dev.example` | Removed `HEALTH_AGENT_MODEL`, `FINANCE_AGENT_MODEL`. |

### Tests, benches (`6b5db69`)

| File | Change |
| --- | --- |
| 8 test files | Deleted (counts below). |
| `tests/test_agentic_loop.py` | Ported, see below. Dropped the fake `HEALTH/FINANCE_SERVICE_URL` env defaults. |
| `tests/test_error_style.py` | `AGENTS["health"]` → `AGENTS["assistant"]` (the test checks the friendly-phrasing rule reaches any specialist's prompt). |
| `tests/test_telemetry.py` | Example route/status strings `health` → `research`. |
| `tests/test_error_surfacing.py`, `test_playback_control.py`, `test_coordinator_bench.py`, `test_retract.py`, `test_origin.py`, `test_now_playing.py` | Dropped the fake `HEALTH/FINANCE_SERVICE_URL` env defaults. |
| `tests/test_coordinator_bench.py` | `mortgage_rate_research` checker test no longer uses `ask_finance`; the canned-answer composite test moved from `multi_domain` to `composite_hottub`; two new tests for the absent-data checker and scoreboard section. |
| `tests/conftest.py` | Docstring no longer names the removed packages. |
| `tests/requirements.txt` | Removed `apscheduler`, `chromadb`, `openpyxl` (only the deleted tests used them). `fastembed` stays: `scripts/routing_bench.py`'s kNN arm uses it. The installed `tests/.venv` was not changed. |
| `scripts/coordinator_model_bench.py` | Removed the bench's fake service URLs, the `multi_domain` composite (ask_health) and the `ask_finance` clause of `mortgage_rate_research`. Added `ABSENT_PROBES`: `no_data_sleep` ("How did I sleep last night?") and `no_data_networth` ("What's my net worth?"), 5 runs each, scored in their own "Absent data" section so the base and hard totals stay comparable. Pass = no tool call, a plain "don't have / no access" statement, no digit anywhere, no claim to have looked anything up. |
| `scripts/routing_bench.py` | `health` and `finance` removed from `SPECIALISTS` and `ROUTE_DESCRIPTIONS`. |
| `docs/bench/routing_eval.jsonl` | Dropped the 6 rows labelled `health` (none were labelled `finance`). Kept three rows whose label is a surviving lane but whose history fields mention health/finance: "ask the health agent" (label `direct`), "Convert that to dollars…" (`direct`), "how much energy did the house use last year?…" (`home`). Their expected answer is still right. |

How the agent-loop tests were ported. Nine tests drove the loop or the
pipeline with the health agent. They keep their names:

- `run_stream` level (5): `test_run_stream_direct_answer_yields_tokens_and_done`,
  `test_run_stream_tool_then_synthesis_streams_tokens`,
  `test_run_stream_llm_error_is_surfaced`, `test_run_accumulates_run_stream_tokens`,
  `test_forced_synthesis_scrubs_leaked_tool_syntax`. Now use `_loop_agent()`,
  a neutral `AgentConfig` in the test file with the health agent's shape
  (default round budget, one non-terminal tool, `get_kronk_context`). The
  fake model calls `get_kronk_context` instead of `query_health`.
- Pipeline level (2): `test_agent_route_streams_tokens_as_they_arrive`,
  `test_specialist_failure_reaches_coordinator_labeled_as_failure`. Route to
  `assistant`, a real surviving specialist, so main.py's `AGENTS` lookup is
  exercised for real.
- Labels (1): `test_stage_and_timing_labels_authored_at_emitter` uses
  `delegate_research`.
- `test_friendly_style_adds_phrasing_rule_to_system_prompt` (in
  `test_error_style.py`) uses `assistant`.

## Test counts

`./scripts/run_tests.sh`, verbatim last line before:

```
1111 passed, 2 skipped, 18 xfailed, 14 warnings in 13.09s
```

After:

```
989 passed, 2 skipped, 18 xfailed, 3 warnings in 11.44s
```

Collected: 1131 → 1009 = 1131 − 125 − 6 + 9.

Deleted files (125):

| File | Tests |
| --- | --- |
| `tests/test_bloodwork_parser.py` | 19 |
| `tests/test_health_agent.py` | 15 (all health_service endpoints and CSV import; no generic loop tests) |
| `tests/test_health_rag.py` | 15 |
| `tests/test_finance_service.py` | 13 |
| `tests/test_positions_api.py` | 13 |
| `tests/test_positions_importer.py` | 29 |
| `tests/test_statement_pdf.py` | 11 |
| `tests/test_extractor.py` | 10 |

Removed from surviving files (6, all in `test_agentic_loop.py`, all tests of
removed code):

- `test_execute_tool_query_health_no_data`, `test_execute_tool_query_finances_no_documents`
- `test_is_garmin_csv_detects_garmin_export`, `test_is_garmin_csv_rejects_generic_csv`, `test_is_garmin_csv_rejects_non_csv_extension`
- `test_upload_garmin_csv_routes_to_health_service`

Added (9):

- `test_agentic_loop.py`: `test_health_and_finance_features_are_gone` (negative: no tools, handlers, timeouts, agents, `ask_*`, model or URL settings; the coordinator prompt names the records only as absent), `test_execute_tool_shopping_list_empty` and `test_execute_tool_removed_health_tool_is_unknown` (replace the two no-data handler tests), `test_upload_garmin_shaped_csv_is_ordinary_context`, `test_upload_zip_is_refused_with_a_clear_reason`, `test_upload_pdf_goes_to_file_contexts` (replace the four Garmin tests), `test_removed_pages_return_404`.
- `test_coordinator_bench.py`: `test_absent_data_checker`, `test_scoreboard_adds_an_absent_data_section_only_when_those_runs_exist`.

Skips unchanged: the two `KRONK_LIVE` tests in `test_agentic_loop.py`.
Xfails unchanged: 18, all voice-corpus routing rows. The 14 → 3 warnings drop
is the deleted health_service `datetime.utcnow` deprecations.

## Bench

Coordinator bench, `gemma-4-e4b`, on an isolated E4B copy started by hand on
:11497 with the production unit's flags (the bench's documented regime;
production :11438 is never benched). It imports the branch's `agents.py`, so
it measures the dev code. The copy was stopped afterwards.

| Run | File | Prompt |
| --- | --- | --- |
| before | `docs/bench/coord_bench_2026-11-15_210616_m1-pre.*` | pre-M1, menu with ask_health and ask_finance |
| after (intermediate) | `docs/bench/coord_bench_2026-11-15_211110_m1-post.*` | M1 menu, generic "no specialist holds it" rule only |
| after (final) | `docs/bench/coord_bench_2026-11-15_212044_m1-post-final.*` | M1 menu plus the no-records sentence (committed) |

| Probe | before | after (intermediate) | after (final) |
| --- | --- | --- | --- |
| weather_delegate | 5/5 | 5/5 | 5/5 |
| shopping_delegate | 3/3 | 3/3 | 3/3 |
| no_spurious | 5/5 | 5/5 | 5/5 |
| news_brief_terminal | 3/3 | 3/3 | 3/3 |
| news_refresh_flag | 3/3 | 3/3 | 3/3 |
| knowledge_prose | 2/2 | 2/2 | 2/2 |
| markdown_list | 1/2 | 2/2 | 1/2 |
| composite_solar | 5/5 | 5/5 | 5/5 |
| **base total** | **27/28** | **28/28** | **27/28** |
| officeholder_delegate | 3/3 | 3/3 | 3/3 |
| mortgage_rate_research | 3/3 | 3/3 | 3/3 |
| weather_by_riddle | 2/3 | 3/3 | 2/3 |
| date_math | 1/3 | 0/3 | 1/3 |
| clock_math | 3/3 | 3/3 | 3/3 |
| exact_words | 1/3 | 1/3 | 1/3 |
| multi_domain (removed) | 3/3 | — | — |
| composite_hottub | 1/3 | 1/3 | 2/3 |
| **hard total, surviving probes** | **14/21** | **14/21** | **15/21** |
| no_data_sleep | — | 2/5 | 5/5 |
| no_data_networth | — | 5/5 | 5/5 |

Delegation probes only (weather, shopping, no_spurious, both news, both
composites, officeholder, mortgage, riddle): 33/36 before, 34/36 final. Not
worse. Zero leaks in every run. Median generation 114.2 → 114.8 tok/s.

Why the prompt names the records. In the intermediate run every sleep
failure was `ask_home`; the model reasoned that "sleep tracking is generally
considered a home-monitoring function". One variable at a time, ad hoc, 10
runs per probe against the same server (files
`docs/bench/coord_prompt_trial_2026-11-15_m1-v{0,1,2}.json`):

| Wording after "never ask an unrelated specialist." | sleep | resting HR | net worth | solar, hot tub, weather, shopping → ask_home; Denver → none |
| --- | --- | --- | --- | --- |
| v0: nothing more | 2/10 | 3/10 | 10/10 | 10/10 each |
| v1: "Kronk keeps no health, sleep, fitness or financial records, and no specialist has them (home is devices, not people): for those, say plainly that you don't have that information." | 10/10 | 10/10 | 10/10 | 10/10 each |
| v2: v1 without "(home is devices, not people)" | 9/10 | 10/10 | 10/10 | 10/10 each |

v1 is committed. It is prompt text, not a routing rule; `routing.py` is
unchanged.

Routing bench, regex arm (`docs/bench/routing_bench_2026-11-15_212056_m1-check.*`):
118 test items (unchanged; the 6 dropped rows were all pre-split exemplars,
363 → 357), wrong-lane 1, named wrong 0. Same as `router-gate1` on 2026-10-05.

## The two probes' answers, verbatim

Bench, final run (no tool calls in any run):

```
no_data_sleep    PASS "I don't have access to your sleep records."
no_data_sleep    PASS "I do not have access to your sleep data. Kronk does not keep health records."
no_data_sleep    PASS "I do not have access to your sleep data. I do not keep health or sleep records."
no_data_sleep    PASS "I do not have access to your health or sleep records."
no_data_sleep    PASS "I do not have access to your sleep records."
no_data_networth PASS "I don't have access to your financial records, so I cannot provide your net worth."
no_data_networth PASS "I do not have access to your financial records or net worth."
no_data_networth PASS "I don't have your financial records."
no_data_networth PASS "I do not have access to your personal financial records, so I cannot provide your net worth."
no_data_networth PASS "I do not have access to your personal financial records, so I cannot provide your net worth."
```

Dev path, through the full pipeline (`/dev/api/chat` and
`/dev/voice/api/chat`, 5 runs each, all HTTP 200). orchestrator-dev's log
for the window: 20 `request`, 20 `route`, 20 `agent_round`,
20 `request_complete`, zero tool calls or delegations.

```
/dev/api/chat        sleep 1  I do not have access to your health or sleep records.
/dev/api/chat        sleep 2  I don't have access to your personal health, sleep, or fitness records.
/dev/api/chat        sleep 3  I do not have access to your sleep records. Kronk does not keep health or fitness data.
/dev/api/chat        sleep 4  I don't have information about your sleep. I do not keep health or sleep records.
/dev/api/chat        sleep 5  I do not have access to your health, sleep, or fitness records.
/dev/api/chat        worth 1  I do not have access to your financial records. Kronk does not keep health, sleep, fitness, or financial records, and no specialist has access to them.
/dev/api/chat        worth 2  I do not have access to your financial records, so I cannot tell you your net worth.
/dev/api/chat        worth 3  I do not have access to your financial records, so I cannot provide your net worth.
/dev/api/chat        worth 4  I do not have access to your financial records or personal information.
/dev/api/chat        worth 5  I do not have access to your personal financial records.
/dev/voice/api/chat  sleep 1  I don't have access to that information. I do not keep records of health, sleep, or fitness data.
/dev/voice/api/chat  sleep 2  I do not have access to your health or sleep records.
/dev/voice/api/chat  sleep 3  I do not have access to your health, sleep, or fitness records.
/dev/voice/api/chat  sleep 4  I do not have access to health, sleep, fitness, or financial records, nor does any specialist possess that information.
/dev/voice/api/chat  sleep 5  I do not have access to your sleep records.
/dev/voice/api/chat  worth 1  I do not have access to your personal financial records, so I cannot tell you what your net worth is.
/dev/voice/api/chat  worth 2  I don't have that information.
/dev/voice/api/chat  worth 3  I don't have access to your financial records, so I cannot provide your net worth.
/dev/voice/api/chat  worth 4  I do not have access to your personal financial records.
/dev/voice/api/chat  worth 5  I do not have access to your financial records or personal data, so I cannot provide your net worth.
```

Two answers repeat the prompt's own phrasing ("no specialist has access to
them", "nor does any specialist possess that information"). Plain and
correct, slightly mechanical.

## Voice replay (M10 layer two, dev path)

| Run | File | pass | xfail | fail | skip |
| --- | --- | --- | --- | --- | --- |
| committed baseline | `voice_replay_2026-11-16_012824_m10-baseline.json` | 91 | 20 | 1 | 2 |
| before (dev still on pre-M1 code) | `voice_replay_2026-11-16_022403_m1-pre.json` | 91 | 20 | 1 | 2 |
| after (dev rebuilt from branch) | `voice_replay_2026-11-16_023124_m1-post.json` | 91 | 20 | 1 | 2 |

All runs: 46 rows executed, 66 checked read-only, 17 players unchanged, dry
run on. Rows that differ from the baseline:

| Row | baseline | before | after | Explanation |
| --- | --- | --- | --- | --- |
| `inc-0930-search-deep-house-ytm` | fail (coordinator → `ask_research`) | fail (same) | pass (`ask_home` → `play_music`) | The coordinator's research-or-home choice on a "search YouTube Music for …" request; the variance the corpus doc names. |
| `eval-look-for-some-good-deep-house-chill` | pass | pass (via `ask_research` then `ask_home`) | fail | Coordinator delegated to `ask_home` correctly; the home agent then said "Playing a deep house chill radio playlist on YouTube Music." without calling `play_music` (trace `abde75f02fdf0bf63d063506edf6cd49`, rid `98f2bf09`). The home agent's prompt and tools are unchanged by M1. |

Reruns of those two rows, three times after M1
(`voice_replay_2026-11-16_0236{18,35,48}_m1-rerun{1,2,3}.json`): run 1 both
pass, run 2 both pass, run 3 `inc-0930` fails (`ask_research` again) and
`eval-look` passes. Both rows pass and fail run to run on both builds.

Other rows show different tool paths but the same status (xfail rows whose
coordinator answer varies, e.g. `eval-played-the-album-toys-in-the-attic`,
and `inc-1021-ninetys-no-verb` going to research); none changed status.

Note on `eval-look-…`: a home agent claiming a play it never made is the
invented-success class tenet 5 is about. Under dry run nothing played. It is
not caused by M1; it is worth a look in M11.

## Dev path end to end

The dev pair was rebuilt with
`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`.
`tool_service-dev` code is unchanged, so its rebuild hit cache and the
container was left running; `kronk-orchestrator-dev-1` was recreated
(`3cbe46155d90` → `3369c00e0194`). `tool_service-dev` `/health`:
`{"status":"ok","music_dry_run":true}`. orchestrator-dev's `AGENTS`:
`['assistant', 'coding', 'devops', 'home', 'research', 'talkie']`.

Shims (criterion 22), all 200:

| Path | Request | s | Answer |
| --- | --- | --- | --- |
| `/dev/api/chat` | What is the capital of Peru? | 1.9 | The capital of Peru is Lima. |
| `/dev/api/chat` | What is the weather forecast for tomorrow? | 5.3 | Tomorrow, Tuesday, is expected to be Sunny with a high near 61°F and a northwest wind of about 6 mph. |
| `/dev/api/chat` | Search the web for the latest ROCm release. | 6.3 | Based on the search results, the latest official releases mentioned are: ROCm 10.1.0 … |
| `/dev/api/chat` | play some jazz in the office | 0.4 | Dry run: would play Parov Stelar, for jazz, on the Office speaker. Nothing was sent to the speaker. |
| `/dev/voice/api/chat` | What is the capital of Peru? | 0.7 | Lima. |
| `/dev/voice/api/chat` | What is the weather forecast for tomorrow? | 4.2 | Tomorrow, Tuesday, is expected to be sunny with a high near 61°F … |
| `/dev/voice/api/chat` | Search the web for the latest ROCm release. | 10.3 | AMD has released ROCm 10. … |
| `/dev/voice/api/chat` | play some jazz in the office | 3.4 | Dry run: would play Kruder & Dorfmeister, for jazz, on the Office speaker. Nothing was sent to the speaker. |

Removed paths on dev (criterion 23): `/dev/health`, `/dev/finances`,
`/dev/retire/`, `/dev/api/health/query`, `/dev/api/finance/query`,
`/dev/probe/health`, `/dev/probe/finance`, `/dev/probe/retire` all answer 404
`{"detail":"Not Found"}`. `/dev/`, `/dev/agents`, `/dev/services`,
`/dev/api/status` answer 200. The served `/dev/agents` and `/dev/services`
contain no health, finance or retirement probe, tool or link.

Uploads on dev: a Garmin-shaped CSV → 200
`{"name":"g.csv","tokens":13,…}` (context; removed again afterwards); a
stored zip → 422 `{"detail":"Zip archives aren't supported. Upload a UTF-8
text file or a PDF."}`.

Status dot (criteria 8, 9): the page served at `/dev/` contains
`fetch('/api/status')` (line 558). The page's URLs are absolute (the M17
known gap), so under `/dev/` it polls production's `/api/status`; both
`/api/status` and `/dev/api/status` answer 200.

What `GET /health` returns now and what depends on it:

- Orchestrator (dev now, production after promotion): 404
  `{"detail":"Not Found"}`. Nothing depends on it. The compose healthchecks
  of `orchestrator` and `orchestrator-dev` use `/api/agents`; nginx
  `/probe/orchestrator` uses `/api/status`; `deploy_tag.sh verify` uses
  `/api/status` and `/api/chat`; the host units (`memwatch`, `perfwatch`,
  `boot_notify`, the canaries) do not call it; HA's YAML does not reference
  it. The nginx log's 52,802 `/health` hits were all the status dot.
- `/health` on `tool_service`, `voice_tracer`, LiteLLM and the llama servers
  is each service's own liveness endpoint and is untouched.
- Production `/health` still serves the old page until promotion (checked:
  200 HTML).

## nginx validation

Throwaway containers, image `nginx:1.29-alpine` (production's tag, image
`sha256:5616878291a2…`), config bind-mounted read-only:

- On the `kronk_kronk` network: `nginx: configuration file /etc/nginx/nginx.conf test is successful`.
- On `--network none` with only `orchestrator`, `tool_service`, `searxng`,
  `voice_tracer` and `host.docker.internal` resolvable: new file
  `test is successful`; the old file (HEAD before M1)
  `nginx: [emerg] host not found in upstream "health_service" in /etc/nginx/nginx.conf:16`.

So production nginx must get the new config before the three containers go,
or a later nginx restart fails. `deploy_tag.sh deploy` does that in the right
order.

`docker compose config -q` passes with and without `--profile dev`. Services:
`litellm nginx orchestrator searxng tool_service voice_tracer wyoming-piper`
(+ `orchestrator-dev tool_service-dev` with the profile).

## Criterion 4 grep

```
grep -rniE 'health_service|finance_service|retire_calc|query_health|query_finances|query_bloodwork|search_health_data|ask_health|ask_finance' \
  orchestrator/ tool_service/ nginx/ docker-compose.yml scripts/ tests/ litellm/ kronk-context.md
```

Remaining hits, all negative tests in `tests/test_agentic_loop.py`:

- lines 116–117, 124, 127–128, 132: `test_health_and_finance_features_are_gone` asserts the names are absent;
- lines 767, 776: `test_execute_tool_removed_health_tool_is_unknown` asserts a model emitting `query_health` / `query_finances` gets `[Unknown tool: …]`.

`scripts/coordinator_model_bench.py:312` mentions "the health and finance
agents are gone" in a comment, which the grep pattern does not match.

## Deletion manifest (for the coordinator, at promotion)

Measured 2026-11-15 on the live tree. Nothing here was deleted. All files
are owned by `drew`; no root needed.

### Data the plan orders deleted (Drew, 2026-11-14 and C8)

| Path | Size (du -sh / bytes) | Files | What it is | Written by |
| --- | --- | --- | --- | --- |
| `data/health.db` | 972K / 995,328 | 1 | Garmin/Withings SQLite | `health_service` (`HEALTH_DB_PATH`, default `/data/health.db`) |
| `data/chroma/` | 21M / 21,265,694 | 6 (1 subdir) | Health vector store (`chroma.sqlite3` 10,063,872 B + one HNSW collection) | `health_service/vector_store.py` |
| `data/finance/` | 236K / 227,392 | 4 (1 subdir) | `finances.db` 69,632 B, `positions.db` 49,152 B, `raw/` two statement PDFs 108,608 B | `finance_service` (bind `./data/finance:/data`) |
| `data/retire/` | 16K / 12,288 | 1 | `retirement.db` | `retire_calc` (bind `./data/retire:/data`) |
| `data/withings_tokens.json` | 349 B | 1 | Withings OAuth tokens (credential) | `health_service/setup_withings_auth.py` (`TOKEN_FILE`) |
| `data/garmin_jwt_web.txt` | 179 B | 1 | Garmin web JWT (credential), 2026-04-19 | Garmin auth code that predates the repo history; no code in git ever referenced it; named as a health credential leftover in `docs/REVIEW_2026-07-05_codebase.md` |
| `data/garmin_login_debug.png` | 884K / 901,885 | 1 | Garmin login screenshot (may show credentials), 2026-04-19 | same as above; the 2026-07-05 review item 11 said verify and delete |

Total: 17 files, about 24 MB.

### Found by the build, not in Drew's order: Drew decides

| Path | Size | What it is | Evidence |
| --- | --- | --- | --- |
| `secrets/garmin.json` | 85 B, 1 file | Garmin login: keys `email`, `password` (values not read). Gitignored. 2026-04-19. | No code in git history ever read it; the 2026-07-05 review lists it as gitignored config. Only Garmin sync, now removed, could have used it. Recommend deleting with the rest; it is a plaintext password. |
| `data/kronk_state.md`, lines 5–6 | 2 lines | `- Uses a Withings scale (body composition data available from May 2024)` and `- Uses a Garmin device (health data: sleep and activities from 2018, HRV from Sep 2025)`. | The surviving orchestrator injects this file into the coordinator's context on the web UI path (`_load_state()`, `main.py`). Those two lines tell the model health data exists, which works against the absent-data answer on `/message`. Edit, not delete; the rest of the file stays. Dev mounts `data/dev`, which has no such file, so the dev checks above did not see it. |

### Docker (no named volumes; all three used bind mounts only)

| Object | Id | Size on disk | Notes |
| --- | --- | --- | --- |
| container `kronk-health_service-1` | `11e171df4f90` | | from tag sprint-M17 |
| container `kronk-finance_service-1` | `9923aa848c34` | | from tag sprint-M17 |
| container `kronk-retire_calc-1` | `b47c91d305a3` | | from tag sprint-M17 |
| image `kronk-health_service:latest` | `2e6fca24937b` | 1.04 GB | |
| image `kronk-finance_service:latest` | `26069eb35ad5` | 359 MB | |
| image `kronk-retire_calc:latest` | `d8f44a4d42df` | 336 MB | |
| image `assistant-health_service:latest` | `045afa7e710e` | 429 MB | 7 months old, an earlier compose project name; no container uses it. Same service, older build. |
| image `assistant-finance_service:latest` | `43a545a2702f` | 354 MB | same |

`docker volume ls` shows no volume for any of the three.

### Outside `./data`

- `../kronk-releases/retirement-calc`: a symlink to `../retirement-calc`,
  created by the M17 deploy. Remove the link only (`rm` without a trailing
  slash); the target is never touched.

### Excluded, untouched

- `data/postgres` (71M, owner unknown): not touched, not for deletion.
- `../retirement-calc` (the sibling repo): not touched, not for deletion.

## Promotion commands

Run from `/home/drew/git-repos/drawsmcgraw/kronk` after the end-to-end test
passes and the item is tagged (`sprint-M1` assumed below). Order matters: the
new nginx config must be live before the three containers go.

```bash
# 0. Snapshot, for the post checks
docker ps --format '{{.Names}} {{.ID}}' | sort > /tmp/m1-promote-before.txt
ls -la data/ > /tmp/m1-data-before.txt

# 1. Preview, then deploy from the tag. up -d --build replaces the kronk
#    production containers from the release; the three removed services are
#    left running as orphans (compose warns; do NOT add --remove-orphans,
#    which would also take the dev pair). nginx restarts with the new config.
scripts/deploy_tag.sh plan sprint-M1
scripts/deploy_tag.sh deploy sprint-M1

# 2. Remove the three stale containers by name, then their images.
docker rm -f kronk-health_service-1 kronk-finance_service-1 kronk-retire_calc-1
docker image rm kronk-health_service:latest kronk-finance_service:latest kronk-retire_calc:latest
# Optional, same services from an older project name, unused:
docker image rm assistant-health_service:latest assistant-finance_service:latest

# 3. Prove nginx starts without them (it fails hard on a missing upstream).
docker compose -p kronk --project-directory ../kronk-releases/sprint-M1 \
  -f ../kronk-releases/sprint-M1/docker-compose.yml restart nginx

# 4. Delete the data (Drew's order, 2026-11-14; C8). Exact paths only.
rm -f  data/health.db data/withings_tokens.json data/garmin_jwt_web.txt data/garmin_login_debug.png
rm -rf data/chroma data/finance data/retire

# 5. The releases-dir symlink (link only, no trailing slash).
rm ../kronk-releases/retirement-calc

# 6. Only with Drew's yes (see manifest, "Drew decides"):
#    rm -f secrets/garmin.json
#    cp data/kronk_state.md ~/backups/kronk_state.md.pre-M1 && \
#    sed -i '/^- Uses a Withings scale/d; /^- Uses a Garmin device/d' data/kronk_state.md
```

The deploy rebuilds the production orchestrator from the tag, so it builds
the same `orchestrator/` the dev pair runs. The production `tool_service`
image is rebuilt too (no code change in M1).

## Post-promotion checks

```bash
scripts/deploy_tag.sh status                     # every kronk container "tag sprint-M1";
                                                 # no health_service, finance_service, retire_calc row
docker ps -a --format '{{.Names}}' | grep -E 'health_service|finance_service|retire_calc'   # nothing
docker ps --format '{{.Names}} {{.Status}}' | grep -E 'nginx|orchestrator|tool_service'      # Up, healthy
for p in / /api/status /services /agents; do echo "$p $(curl -s -o /dev/null -w '%{http_code}' localhost$p)"; done   # all 200
for p in /health /finances /retire/ /api/health/query /api/finance/query /probe/health /probe/finance /probe/retire; do
  echo "$p $(curl -s -o /dev/null -w '%{http_code}' localhost$p)"; done                      # all 404
curl -s localhost/ | grep -c "fetch('/api/status')"                                          # 1
for p in /api/chat /voice/api/chat; do for q in "What is the capital of Peru?" "How did I sleep last night?" "What's my net worth?"; do
  curl -s localhost$p -H 'Content-Type: application/json' \
    -d "{\"model\":\"kronk:latest\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"$q\"}]}" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"]["content"])'; done; done
    # Lima; then plain "I don't have that" answers, no figures
docker exec kronk-orchestrator-1 python -c "import agents;print(sorted(agents.AGENTS))"
    # ['assistant', 'coding', 'devops', 'home', 'research', 'talkie']
ls data/health.db data/chroma data/finance data/retire data/withings_tokens.json data/garmin_jwt_web.txt data/garmin_login_debug.png 2>&1 | grep -c 'No such file'   # 7
ls -ld data/postgres ../retirement-calc          # both still present, unchanged
docker ps --format '{{.Names}}' | grep -- -dev   # dev pair still running
```

Rollback: `scripts/deploy_tag.sh deploy sprint-M17`. Two caveats. The data
is gone after step 4, so the restored services start empty. And the current
`deploy_tag.sh` no longer links `../retirement-calc`, so recreate that link
by hand first (`ln -s ../retirement-calc ../kronk-releases/retirement-calc`)
or the `retire_calc` build fails.

## `.env` keys for Drew to remove

- `.env.dev`: `HEALTH_AGENT_MODEL`, `FINANCE_AGENT_MODEL`. Nothing reads
  them now (`.env.dev.example` already dropped them).
- `.env`: none. The removed services read no key from `.env` (their only
  settings, `HEALTH_DB_PATH`, `FINANCE_DB_PATH`, `POSITIONS_*`,
  `LLM_SERVICE_URL`, `MAPPING_MODEL`, `EXTRACTION_MODEL`, came from compose
  or defaults). `.env`'s keys were listed by name only.
- Not an env key but the same kind of thing: `secrets/garmin.json` (above).

## Production and data untouched during the build

- Container ids, created and started times of all 19 containers: before the
  build and at the end, identical except `kronk-orchestrator-dev-1`
  (recreated by the dev rebuild). Production nginx was not restarted.
- `./data` (excluding `data/dev` and `data/postgres`): same 52 paths before
  and after. Changed sizes or mtimes only on files the running system writes:
  `gpu_mem.json`, `hottub/status.json`, `metrics.db` (one production
  `/api/chat` request, below), `solar.db`, `voice_tracer_state.json`.
- Production requests made: one `/api/chat` "What is the weather forecast
  for tomorrow?" to compare against dev (the Ollama shim writes no chat
  history). No `pipeline_bench.sh`.
- The bench server on :11497 was started and stopped by the build;
  `llama-gemma4-e4b` (:11438) was not touched.

## Deviations from the plan, and findings

1. **Coordinator prompt names the absent records.** The plan says to edit
   the prompt so health and finances are not delegable; that alone left
   sleep questions going to `ask_home`. The committed line states the
   records do not exist. Prompt text, measured, no routing rule.
2. **Zip uploads are refused explicitly** (422 with the reason). Without the
   check, a small stored zip decodes as UTF-8 and lands in context.
3. **`multi_domain` bench probe dropped, not replaced.** It needed
   `ask_health`. Two-domain coverage remains in `composite_solar` and
   `composite_hottub`.
4. **`kronk_state.md` still tells the coordinator about Garmin and Withings**
   on the web UI path. It lives under `./data`, so it is a promotion step
   for Drew's yes, not a build change.
5. **`secrets/garmin.json`** holds a plaintext Garmin password. Not in
   Drew's order; recommended for deletion.
6. **Found in passing, not M1: the orchestrator's ambient date is UTC.**
   `kronk_facts()` in both production and dev at 21:30 EST Sunday said
   "Today is Monday, November 16, 2026; the local time is 2:30 AM UTC". So
   after 19:00 Eastern, "tomorrow" is the day after tomorrow; dev answered
   "Tomorrow, Tuesday". Production gave the same forecast without naming the
   day. Likely the container has no `TZ`. One env line to fix; it affects
   every evening date question.
7. **Home agent invented a play once** (`eval-look-for-some-good-deep-house-chill`,
   above). Not M1; relevant to M11.
8. Left as found: `README.md` still says `fetch_url` uses `verify=False` (M5's
   item); `kronk-context.md` and `agents.html` are stale beyond M1 (the LLM
   router and Ollama-era models). Only their M1 parts were edited.
9. Docs moved to history: `docs/plans/FINANCIAL_EXPERT_PLAN.md` →
   `docs/history/FINANCIAL_EXPERT_PLAN.md` with a removed status header.
   There was no health plan doc and no feature doc for either feature.
   `docs/HISTORY.md` has a new section on what existed, and the Garmin
   manual-import design decision moved there from the README. `CLAUDE.md`
   had no mention of the removed features; unchanged.

## Left for Drew

- Remove `HEALTH_AGENT_MODEL` and `FINANCE_AGENT_MODEL` from `.env.dev`.
- Say yes or no to deleting `secrets/garmin.json` and to the two-line
  `kronk_state.md` edit (commands in step 6).
- The UTC ambient-date finding (item 6).
- `data/postgres` remains open, as before.
