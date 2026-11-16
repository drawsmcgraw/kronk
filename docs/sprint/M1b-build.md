# M1b build report: remove the hot tub feature

Build agent, 2026-11-15 22:30 to 23:00 EST (03:30 to 04:00 UTC on
2026-11-16), branch `refactor`. Criteria: `docs/sprint/M1b-criteria.md`.
Commits: `0583239` (tool, route, benches, tests), `855a9d9` (delete
`hottub_monitor/`), `489e306` (docs), `faf37aa` (coordinator sentence), and
the commit that adds this report.

## Read this first: the monitor was working

`TECH_DEBT.md` HOTTUB-01 and the plan say the monitor never worked. Its
journal says otherwise for the last three weeks:

- 2026-08-23 (start of the retained journal) to 2026-10-24 13:50: the spa
  module did not answer. About 10,400 "No response" / offline lines, one
  connect attempt every 5 minutes. This is what HOTTUB-01 describes.
- 2026-10-24 13:50: `HOT TUB BACK ONLINE — 32.0°F (set: 101.0°F)`.
- 2026-11-04 08:45 (after a reboot) to 2026-11-15 22:31: connected the whole
  time, `101.0°F (set: 101.0°F)`, no offline event. 20 non-fatal geckolib
  errors in that span ("Cannot get reminders, protocol retry time
  exceeded", and "Network is unreachable" during the 2026-11-04 boot).
- Last `data/hottub/status.json`, written 22:30 EST: `"online": true`,
  `"temperature_f": 101.0`, `"last_check": "2026-11-16T03:30:36Z"`.

Alerting was never built, so "never worked" may mean the alert, not the
readings. Drew's decision is binding and the build carries it out. Every
step is reversible: the code is in git (tag `sprint-M1`), and the whole
`hottub_monitor/` directory, virtualenv included, is in
`~/backups/hottub/hottub_monitor-2026-11-15.tar.gz` (5,340,012 bytes, sha256
`49a0d5e0b0375be1ead54c3d5500bbd7291d48e8a4a674e35aad0a676316e77a`). The
coordinator may want to show Drew this section before running the
unit-file and `data/hottub/` deletions, which are the only steps git cannot
undo.

Second point for the coordinator: production still runs `sprint-M1`, which
has `query_hottub`. With the unit stopped, `GET localhost:8003/hottub`
returns the frozen 22:30 file (`online: true`, 101.0°F) as if current
(checked read-only at 03:51 UTC). Until promotion a household member asking
"is the hot tub online" gets a stale "yes, 101°F". Promotion fixes it.

## Summary

- `query_hottub`, the home agent's hot tub duties, `GET /hottub`,
  `HOTTUB_STATUS_FILE`, `hottub_monitor/` (tracked file, `.venv`,
  `__pycache__`), the `composite_hottub` bench probe, the hot tub
  routing-eval row and HOTTUB-01 are gone from the branch.
- With the tool gone, the coordinator still sent every hot tub question to
  `ask_home` (30/30 on the bench server, 12/12 on dev); the home agent then
  refused. One prompt sentence makes the coordinator answer directly: 30/30
  on the bench server, 30/30 on the dev shims, no tool call, no
  delegation. A generic wording moved 1/30. Prompt text, not a routing
  rule; `routing.py` is unchanged.
- Tests: `989 passed, 2 skipped, 18 xfailed` before; `992 passed,
  2 skipped, 18 xfailed` after. One test removed, four added.
- Home agent still does its other jobs: 16 of 16 checks on dev (8 kinds,
  both mounts), each through the right tool.
- Voice replay on dev: before 91 pass / 20 xfail / 1 fail / 2 skip (same
  totals as the committed baseline); after 90 / 20 / 2 / 2. The fails are
  two known-variance rows; five reruns: 5/5 and 4/5 pass.
- Nothing under `./data` or `~/.config/systemd` was deleted. The unit stayed
  stopped and disabled. No production container was stopped, removed or
  recreated. HA and Music Assistant were not restarted.

## What was removed, by file

| File | Change | Commit |
| --- | --- | --- |
| `orchestrator/tools.py` | Removed the `query_hottub` definition, `_tool_query_hottub`, its `_HANDLERS` entry and its `TOOL_TIMEOUTS` entry. 20 tools remain. No narration line existed (checked). | `0583239` |
| `orchestrator/agents.py` | `home`: `query_hottub` out of `tool_names`; "hot tub status" out of `description`; "hot tub, spa" out of `routing_hint`; the two `query_hottub` sentences out of `system_prompt`. | `0583239` |
| `orchestrator/agents.py` | Coordinator prompt: one sentence added after the M1 no-records sentence (see "The coordinator sentence"). | `faf37aa` |
| `tool_service/main.py` | Removed `GET /hottub` and `HOTTUB_STATUS_FILE`. No `geckolib` in `tool_service` (checked; no lockfile change). | `0583239` |
| `hottub_monitor/` | Deleted: `monitor.py` (tracked, 8,847 B), `.venv/` (untracked, 21,584,436 B, 1,379 files, `geckolib==1.0.15` on Python 3.13.7), `__pycache__/` (12,339 B). Tarball first (above). | `855a9d9` |
| `scripts/coordinator_model_bench.py` | Removed `_hottub_cost` and `composite_hottub`, the only hard composite; `HARD_COMPOSITES` is now `[]` (the machinery and scoreboard still take specs). Added `no_device_hottub` to `ABSENT_PROBES`: "Is the hot tub online?", 5 runs, same pass rule as M1's probes (no tool call, plain statement, no digit, no claimed lookup). The plain-statement matcher also accepts `check`, `monitor`, `status`. | `0583239` |
| `scripts/routing_bench.py` | "the hot tub" out of the home lane description. | `0583239` |
| `docs/bench/routing_eval.jsonl` | Dropped the one hot tub row ("Is the hot tub online?", label `home`, `bench: false`). 491 → 490 lines. | `0583239` |
| `tests/test_agentic_loop.py` | Added `test_hot_tub_feature_is_gone` and `test_execute_tool_removed_hottub_tool_is_unknown`. | `0583239`, `faf37aa` |
| `tests/test_coordinator_bench.py` | Removed `test_hard_composite_answer_checkers`; ported the composite machinery test to a local spec; added `test_absent_device_checker`; the absent scoreboard test counts probes instead of hard-coding `2/2`. | `0583239` |
| `tests/test_error_surfacing.py` | `test_non_terminal_delegation_still_synthesizes` used a hot tub question as example content; now a solar one. Same assertions. | `0583239` |
| `tests/test_tool_service_routes.py` | New: `test_hottub_route_is_gone` (`GET /hottub` 404, no `HOTTUB*` name in the module). | `0583239` |
| `README.md` | Hot tub out of the summary, the `tool_service` row, the `home` agent row and the tool list; "Last updated" line. | `489e306` |
| `TECH_DEBT.md` | HOTTUB-01 removed. | `489e306` |
| `docs/HISTORY.md` | New section: what existed and the journal's track record. | `489e306` |

Checked and found nothing to change: `ROADMAP.md` (no hot tub entry),
`kronk-context.md`, `orchestrator/static/` (`services.html`, `agents.html`,
`index.html`), `nginx/`, `docker-compose.yml`, `systemd/`, `ha/`,
`routing.py`, `tests/voice_corpus.jsonl` (no hot tub rows, so nothing to
re-expect), `docs/features/`, `docs/plans/` (no hot tub plan or feature
doc). Left as written, because they are dated records: the 2026-07-05 and
2026-10-09 reviews, `docs/VOICE_SETUP.md:189` (build journal listing the
unit as active), `docs/plans/SOLAR_MONITOR_PLAN.md:83` and
`MODEL_BENCH_GEMMA4_26B_PLAN.md` (`composite_hottub` results).
`data/kronk_state.md` has no hot tub text (M1's lesson; checked, not
edited); `data/dev/` has no state file.

## Criterion 4 grep

```
grep -rniE 'hot ?tub|hottub|gecko' orchestrator/ tool_service/ nginx/ \
  docker-compose.yml scripts/ tests/ static/ systemd/ kronk-context.md
```

There is no top-level `static/` (the pages are in `orchestrator/static/`,
which the `orchestrator/` path covers). Remaining hits, each justified:

- `orchestrator/agents.py`, the coordinator prompt: "Kronk has no
  connection to the hot tub or spa: for its status or temperature, say
  plainly that you can't check it." This is what makes criterion 7 pass
  (below). Not in the home agent.
- `tool_service/main.py:446`: `(KHTML, like Gecko)`, part of the browser
  User-Agent string `fetch_url` sends. Unrelated to the spa.
- `scripts/coordinator_model_bench.py:344–346`: the `no_device_hottub` probe.
- `tests/test_agentic_loop.py` (`test_hot_tub_feature_is_gone`,
  `test_execute_tool_removed_hottub_tool_is_unknown`),
  `tests/test_coordinator_bench.py` (`test_absent_device_checker`),
  `tests/test_tool_service_routes.py` (`test_hottub_route_is_gone`):
  negative tests.

Compose: `docker compose --profile dev config` has no `hottub`, `hot_tub`
or `gecko` anywhere (0 lines). No hot tub env var reaches any service.

## Test counts

`./scripts/run_tests.sh -q`, last line, before (HEAD `3338452`):

```
989 passed, 2 skipped, 18 xfailed, 3 warnings in 11.42s
```

After (`faf37aa`):

```
992 passed, 2 skipped, 18 xfailed, 3 warnings in 11.47s
```

Collected ids, diffed: 1009 → 1012 = 1009 − 1 + 4.

- Removed: `tests/test_coordinator_bench.py::test_hard_composite_answer_checkers`.
  It tested `_hottub_cost`, the answer checker of the removed probe.
- Added: `test_agentic_loop.py::test_hot_tub_feature_is_gone`,
  `test_agentic_loop.py::test_execute_tool_removed_hottub_tool_is_unknown`,
  `test_coordinator_bench.py::test_absent_device_checker`,
  `test_tool_service_routes.py::test_hottub_route_is_gone`.
- Changed in place, same names: `test_hard_composite_uses_its_own_canned_answers_and_needs`
  (local spec, the bench has no hard composite now),
  `test_scoreboard_adds_an_absent_data_section_only_when_those_runs_exist`
  (counts `ABSENT_PROBES`), `test_non_terminal_delegation_still_synthesizes`
  (example text).

Skips unchanged: the two `KRONK_LIVE` tests. Xfails unchanged: 18, the
voice-corpus routing rows. The corpus offline layer runs inside the 992.

## The coordinator sentence

Same lesson as M1: removing the tool did not stop delegation. The home
agent's description still says "home devices", and the coordinator's
prompt lists "home devices" as delegable. Trials on an isolated E4B copy
(:11497, production unit's flags, `bench-e4b` transient unit, stopped
afterwards), one variable at a time, the branch's `agents.py` with the
sentence appended after M1's no-records sentence. 10 runs per hot tub
question, 5 per control. Files:
`docs/bench/coord_prompt_trial_2026-11-15_m1b-v{0,1,2}.json`.

| Variant | online? | how hot? | spa heater? | controls (solar ×2, shopping, now playing, weather → `ask_home`; Denver time zone → none) |
| --- | --- | --- | --- | --- |
| v0: nothing added | 0/10 (all `ask_home`) | 0/10 | 0/10 | 30/30 |
| v1: "A home device that the home specialist's description does not name is not connected to Kronk: say plainly that you can't check it, and do not ask home." | 0/10 (all `ask_home`) | 1/10 | 0/10 | 30/30 |
| v2: "Kronk has no connection to the hot tub or spa: for its status or temperature, say plainly that you can't check it." | 10/10 | 10/10 | 10/10 | 30/30 |

v2 is committed (`faf37aa`). One of the controls is "Are the solar panels
online?", to check the sentence does not spread to other "online"
questions: 5/5 `ask_home` under every variant.

Under v0 the answers were already plain (the home agent, with no tool,
said "I do not have a tool to check the status of the hot tub" 12 of 12 on
dev), so v0 would arguably meet criterion 7 as worded. It cost a
delegation hop: 5.6 to 12.3 s per answer on dev against 0.2 to 3.0 s with
v2, and the plan asks for the refusal from the coordinator. Hence v2.

## Probe answers, verbatim (criterion 7)

Dev path after `faf37aa`, 30 requests: three phrasings × two mounts × 5
runs, all HTTP 200. orchestrator-dev's log for the window: 30 `request`,
30 `route` (all `route=direct rule=default`), 30 `agent_round`,
30 `request_complete`, zero `tool_call`.

```
22:47:54 /dev/api/chat        1  3.0s | Is the hot tub online? | I cannot check the status of the hot tub.
22:47:57 /dev/api/chat        2  1.0s | Is the hot tub online? | I can't check the hot tub's status.
22:47:58 /dev/api/chat        3  1.6s | Is the hot tub online? | I can't check the hot tub or spa status.
22:47:59 /dev/api/chat        4  0.9s | Is the hot tub online? | I cannot check the hot tub or spa status or temperature.
22:48:00 /dev/api/chat        5  1.9s | Is the hot tub online? | I can't check the hot tub or spa status.
22:48:02 /dev/voice/api/chat  1  0.7s | Is the hot tub online? | I can't check the status or temperature of the hot tub.
22:48:03 /dev/voice/api/chat  2  1.0s | Is the hot tub online? | I can't check the hot tub's status or temperature.
22:48:04 /dev/voice/api/chat  3  0.2s | Is the hot tub online? | I can't check the hot tub status.
22:48:04 /dev/voice/api/chat  4  2.4s | Is the hot tub online? | I can't check the hot tub's status or temperature.
22:48:06 /dev/voice/api/chat  5  2.6s | Is the hot tub online? | I can't check the hot tub's status.
22:48:09 /dev/api/chat        1  0.9s | How hot is the hot tub right now? | I can't check the hot tub's status or temperature.
22:48:10 /dev/api/chat        2  1.8s | How hot is the hot tub right now? | I cannot check the hot tub's status or temperature.
22:48:12 /dev/api/chat        3  1.0s | How hot is the hot tub right now? | I can't check the hot tub status or temperature.
22:48:13 /dev/api/chat        4  0.7s | How hot is the hot tub right now? | I cannot check the hot tub's temperature.
22:48:13 /dev/api/chat        5  2.3s | How hot is the hot tub right now? | I can't check the hot tub's status or temperature.
22:48:16 /dev/voice/api/chat  1  1.8s | How hot is the hot tub right now? | I can't check the hot tub's status or temperature.
22:48:18 /dev/voice/api/chat  2  2.4s | How hot is the hot tub right now? | I can't check the hot tub or spa.
22:48:20 /dev/voice/api/chat  3  0.9s | How hot is the hot tub right now? | I can't check the hot tub's status or temperature.
22:48:21 /dev/voice/api/chat  4  0.2s | How hot is the hot tub right now? | I can't check the hot tub's temperature or status.
22:48:21 /dev/voice/api/chat  5  0.9s | How hot is the hot tub right now? | I cannot check the hot tub status or temperature.
22:48:22 /dev/api/chat        1  1.5s | Is the spa heater running? | I can't check the status of the spa or the temperature.
22:48:24 /dev/api/chat        2  2.7s | Is the spa heater running? | I can't check the status or temperature of the hot tub or spa.
22:48:26 /dev/api/chat        3  1.7s | Is the spa heater running? | I can't check the status of the hot tub or spa.
22:48:28 /dev/api/chat        4  0.2s | Is the spa heater running? | I can't check the status or temperature of the hot tub or spa.
22:48:28 /dev/api/chat        5  0.9s | Is the spa heater running? | I cannot check the status or temperature of the spa.
22:48:29 /dev/voice/api/chat  1  2.1s | Is the spa heater running? | I cannot check the status or temperature of the hot tub or spa.
22:48:31 /dev/voice/api/chat  2  1.9s | Is the spa heater running? | I can't check the status or temperature of the hot tub or spa.
22:48:33 /dev/voice/api/chat  3  1.7s | Is the spa heater running? | I can't check the hot tub or spa status or temperature.
22:48:35 /dev/voice/api/chat  4  2.1s | Is the spa heater running? | I cannot check the status or temperature of the spa.
22:48:37 /dev/voice/api/chat  5  2.9s | Is the spa heater running? | I can't check the hot tub or spa status.
```

For comparison, dev with the tool removed but no sentence (v0), 12 requests,
each `ask_home` then a home agent round with no tool call:

```
/dev/api/chat        Is the hot tub online?            I do not have a tool that provides the status of a hot tub.
/dev/api/chat        Is the hot tub online?            I do not have a tool to check the status of the hot tub.
/dev/voice/api/chat  Is the hot tub online?            I do not have a tool to check the status of the hot tub.
/dev/voice/api/chat  Is the hot tub online?            I do not have the capability to check the status of the hot tub.
/dev/api/chat        How hot is the hot tub right now? I do not have a sensor that tracks the hot tub temperature. I can check the weather or help with other home systems like the solar panels and music.
/dev/api/chat        How hot is the hot tub right now? I do not have the current temperature of the hot tub.
/dev/voice/api/chat  How hot is the hot tub right now? I do not have access to the hot tub's specific temperature sensor.
/dev/voice/api/chat  How hot is the hot tub right now? I do not have access to the hot tub temperature.
/dev/api/chat        Is the spa heater running?        I do not have access to information about the spa heater.
/dev/api/chat        Is the spa heater running?        I do not have access to the status of specific home devices like the spa heater.
/dev/voice/api/chat  Is the spa heater running?        I do not have access to a device monitor that can tell me if the spa heater is running.
/dev/voice/api/chat  Is the spa heater running?        I do not have the capability to check the status of the spa heater.
```

Bench probe `no_device_hottub`, final run (no tool call in any run):

```
PASS "I can't check the hot tub's status."
PASS "I cannot check the hot tub status."
PASS "I cannot check the hot tub's status."
PASS "I cannot check the hot tub status."
PASS "I can't check the status or temperature of the hot tub."
```

## The home agent's other jobs (criterion 8)

Dev after `faf37aa`, one run per mount, 16 requests, all HTTP 200. Tools
from orchestrator-dev's log. Players snapshotted before (9 idle, 3 off,
5 unavailable) and unchanged after; `MUSIC_DRY_RUN` on.

| Request | Path to the answer | `/dev/api/chat` | `/dev/voice/api/chat` |
| --- | --- | --- | --- |
| What's the weather tomorrow? | `weather` rule → home, cached NWS block | Tomorrow will be sunny with a high of 61°F and a light northwest wind around 6 mph. | Tomorrow in Laurel, MD will be sunny with a high of 61°F and a 6 mph northwest wind. |
| How's my solar doing today? | coordinator → `ask_home` → `solar_energy` | Your solar panels have produced 10.3 kWh today. | You have produced 10.3 kWh of solar energy today. |
| Are the solar panels online? | `ask_home` → `solar_status` | The solar panels are producing very little power. Two inverters have been confirmed failing for many days, and five others are experiencing momentary dips. | Your solar panels are partially operational, but the system is underperforming with only 0.02 kW of output. Two inverters have been confirmed as failing for extended periods. |
| How much energy have the solar panels produced this month? | `ask_home` → `solar_energy` | The solar panels have produced 504.1 kWh of energy this month. | The solar panels have produced 504.1 kWh this month so far. |
| Which solar inverters are underperforming? | `ask_home` → `solar_detail` | inv …003219 failing for over 122 days; inv …006899 and inv …002146 below expected (markdown list) | One inverter, inv …003219, has a confirmed failure … 122 bad days. Inverters inv …006899 and inv …002146 are currently underperforming … |
| What's on the shopping list? | `ask_home` → `shopping_list_view` | The shopping list is currently empty. | The shopping list is currently empty. |
| What's playing in the office? | `ask_home` → `now_playing` | Nothing is playing on the Office speaker. | Nothing is playing on the Office speaker. |
| play some jazz in the office | `music` rule → home → `play_music` (dry run) | Dry run: would play Kruder & Dorfmeister, for jazz, on the Office speaker. Nothing was sent to the speaker. | Dry run: would play Mushroom Jazz Radio, for jazz, on the Office speaker. Nothing was sent to the speaker. |

The shopping list is empty in fact: `GET localhost:8103/shopping_list` →
`{"items":[],"updated_at":null}`. Weather has no `tool_call` event because
the home agent answers from the cached weather block (`agents.py:27–44`),
as designed. It was 22:49 EST, after sunset, hence 0.02 kW.

## Coordinator bench

Same isolated server. "Before" is tag `sprint-M1` exported with `git archive`
plus the new bench script, so the new probe runs against the old menu
(`ask_home` still claiming hot tub status). "After" is `faf37aa`. Files:
`docs/bench/coord_bench_2026-11-15_223851_m1b-pre.*`,
`docs/bench/coord_bench_2026-11-15_225125_m1b-post.*`.

| Probe | before | after |
| --- | --- | --- |
| weather_delegate | 5/5 | 5/5 |
| shopping_delegate | 3/3 | 3/3 |
| no_spurious | 5/5 | 5/5 |
| news_brief_terminal | 3/3 | 3/3 |
| news_refresh_flag | 3/3 | 3/3 |
| knowledge_prose | 2/2 | 2/2 |
| markdown_list | 2/2 | 1/2 |
| composite_solar | 5/5 | 5/5 |
| **base** | **28/28** | **27/28** |
| officeholder_delegate | 3/3 | 3/3 |
| mortgage_rate_research | 3/3 | 3/3 |
| weather_by_riddle | 2/3 | 3/3 |
| date_math | 1/3 | 1/3 |
| clock_math | 3/3 | 3/3 |
| exact_words | 0/3 | 0/3 |
| **hard** (composite_hottub removed) | **12/18** | **13/18** |
| no_data_sleep | 5/5 | 5/5 |
| no_data_networth | 5/5 | 5/5 |
| no_device_hottub | 0/5 (all `ask_home`) | 5/5 |

Delegation probes (weather, shopping, no_spurious, both news, composite_solar,
officeholder, mortgage, riddle): 32/33 before, 33/33 after. `markdown_list`
is formatting, not delegation, and has read 1/2 and 2/2 across M1's runs on
the same prompt. Zero leaks in both runs. The two-domain hard composite is
gone; `composite_solar` in the base set is the remaining two-domain probe.

## Voice replay (criterion 10)

| Run | File | pass | xfail | fail | skip |
| --- | --- | --- | --- | --- | --- |
| committed baseline | `voice_replay_2026-11-16_012824_m10-baseline.json` | 91 | 20 | 1 | 2 |
| before (dev on M1 code) | `voice_replay_2026-11-16_033433_m1b-pre.json` | 91 | 20 | 1 | 2 |
| after (dev on `faf37aa`) | `voice_replay_2026-11-16_035122_m1b-post.json` | 90 | 20 | 2 | 2 |

All runs: 46 executed, 66 checked read-only, 17 players unchanged, dry run
on. Rows whose status differs across the baseline, M1's e2e run, before and
after:

| Row | baseline | M1 e2e | before | after | Explanation |
| --- | --- | --- | --- | --- | --- |
| `inc-0930-search-deep-house-ytm` | fail | fail | pass | fail | Coordinator's research-or-home choice on "search … on YouTube Music": after, `ask_research` → `web_search` (trace `08a68d051b2d43c383a045b2a433cbf1`). The variance the corpus doc names; M1 saw it both ways. |
| `plan-orbital-station-youtube` | pass | fail | pass | fail | `music` rule pins it to home; the home agent answered "I can play "Orbital Radio Station" for you, but I do not have a direct integration with YouTube Music…" without calling `play_music` (trace `7fa8c0b278b80e5a23b4e1fca311a850`). Failed the same way in M1's e2e run on pre-M1b code. |
| `eval-look-for-some-good-deep-house-chill` | pass | pass | fail | pass | The one fail in "before" (home agent, no tool call). Pre-M1b code. |

The home agent's prompt is the one thing M1b changed that these rows go
through (two hot tub sentences removed), so I reran all three five times
after (`voice_replay_2026-11-16_0357{01,24,48}_m1b-rerun{1,2,3}.json`,
`…_035814_m1b-rerun4.json`, `…_035833_m1b-rerun5.json`):

| Row | reruns passed |
| --- | --- |
| `plan-orbital-station-youtube` | 5/5 |
| `inc-0930-search-deep-house-ytm` | 4/5 (run 5: coordinator answered "I do not have the capability to search YouTube Music…" with no tool, trace `8520cba68ae73e9d1a5740815b99d994`) |
| `eval-look-for-some-good-deep-house-chill` | 5/5 |

Read: run-to-run variance in the same rows as M1, both before and after the
change. No row moved status for a reason tied to M1b. The home agent
declining or claiming without a tool call is the class M1 flagged for M11.

## Dev path

Rebuilt with
`docker compose --profile dev up -d --build --no-deps tool_service-dev orchestrator-dev`,
then `orchestrator-dev` alone after `faf37aa`. Both recreated
(`kronk-tool_service-dev-1` `f8b8ddc35645` → `0189cf3b736b`,
`kronk-orchestrator-dev-1` `3369c00e0194` → `a75a0d70d023` → `d030deedfac8`).
md5 of `orchestrator/tools.py`, `orchestrator/agents.py` and
`tool_service/main.py` equals the file inside each container. Inside
orchestrator-dev: `AGENTS` = `assistant, coding, devops, home, research,
talkie`; `query_hottub` in neither `home.tool_names` nor `tools._HANDLERS`;
20 tool definitions. `GET localhost:8103/hottub` → 404 `{"detail":"Not
Found"}`; `/health` → `{"status":"ok","music_dry_run":true}`.

## Manifest (for the coordinator, at promotion)

Measured 2026-11-15 22:35 EST. Nothing here was deleted by the build.

### The host unit

| Item | Value |
| --- | --- |
| Path | `/home/drew/.config/systemd/user/kronk-hottub-monitor.service` (375 B, 2026-05-01, owner `drew`) |
| `ExecStart` | `/home/drew/git-repos/drawsmcgraw/kronk/hottub_monitor/.venv/bin/python /home/drew/git-repos/drawsmcgraw/kronk/hottub_monitor/monitor.py` |
| Other settings | `Restart=on-failure`, `RestartSec=15`, journal output, `WantedBy=default.target`; no `Environment`, no `EnvironmentFile`, no `WorkingDirectory`, no drop-ins |
| State | `disabled`, `inactive (dead)` since 2026-11-15 22:31:52 (the coordinator's stop). No `default.target.wants` link remains. Unchanged at the end of the build. |
| Reference copy in `systemd/` | none, ever |

Full unit file, for a rollback:

```ini
[Unit]
Description=Kronk hot tub monitor (Gecko in-touch via geckolib)
After=network-online.target

[Service]
ExecStart=/home/drew/git-repos/drawsmcgraw/kronk/hottub_monitor/.venv/bin/python \
    /home/drew/git-repos/drawsmcgraw/kronk/hottub_monitor/monitor.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

### Data

| Path | Size | Files | Last write |
| --- | --- | --- | --- |
| `data/hottub/` | 8.0K on disk (`du -sh`), 296 B of content (`du -sb`) | 1: `status.json` | 2026-11-15 22:30 EST, by the monitor; unchanged since |

The monitor read `HOTTUB_STATUS_FILE` from the environment (unset in the
unit) and otherwise wrote only that file. It held no credentials; the spa's
LAN address and identifier were constants in `monitor.py`.

### Owned by the monitor, outside the repo

- Nothing else. No other unit, timer, cron entry, `~/.local` or home-dir
  file names it; no Docker image or volume (it was never a container).
- Created by the build: `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz`
  (rollback copy, see top). Drew decides when it goes.
- `../kronk-releases/sprint-M1/hottub_monitor/monitor.py`: part of the
  exported M1 release (no virtualenv). Rollback material for `sprint-M1`;
  leave it.

### `.env` keys for Drew

None. `.env` and `.env.dev` have no hot tub, spa or Gecko key (names
checked only); `.env.dev.example` neither. Nothing to remove.

## Promotion commands

Run from `/home/drew/git-repos/drawsmcgraw/kronk` after the end-to-end test
passes and the item is tagged (`sprint-M1b` assumed). Order: the unit is
already stopped and disabled, so the directory it ran from could go first;
the deploy goes first anyway so production stops serving the stale status
file before the file goes.

```bash
cd /home/drew/git-repos/drawsmcgraw/kronk
# 0. Snapshot, for the post checks
docker ps --format '{{.Names}} {{.ID}}' | sort > /tmp/m1b-promote-before.txt
systemctl --user status kronk-hottub-monitor.service --no-pager | head -3

# 1. Deploy production from the tag (orchestrator and tool_service change;
#    the script recreates the kronk project's containers and nginx; it never
#    touches HA, MA or the dev pair). No --remove-orphans: M1b removes no
#    service.
scripts/deploy_tag.sh plan sprint-M1b
scripts/deploy_tag.sh deploy sprint-M1b

# 2. The unit: stop and disable (both no-ops today, kept for safety), then
#    remove the file and reload.
systemctl --user stop kronk-hottub-monitor.service
systemctl --user disable kronk-hottub-monitor.service
rm /home/drew/.config/systemd/user/kronk-hottub-monitor.service
systemctl --user daemon-reload
systemctl --user reset-failed kronk-hottub-monitor.service 2>/dev/null || true

# 3. Data (Drew's order, M1b). Exact paths only; check pwd first.
pwd   # /home/drew/git-repos/drawsmcgraw/kronk
rm data/hottub/status.json
rmdir data/hottub
```

## Post-promotion checks

```bash
scripts/deploy_tag.sh status                  # kronk containers "tag sprint-M1b"
systemctl --user list-unit-files 'kronk-hottub*' --no-pager   # 0 unit files listed
systemctl --user status kronk-hottub-monitor.service 2>&1 | head -1   # "could not be found"
ls -d data/hottub 2>&1                         # No such file or directory
curl -s -o /dev/null -w '%{http_code}\n' localhost:8003/hottub   # 404
docker exec kronk-orchestrator-1 python -c "import agents,tools;print(sorted(agents.AGENTS), 'query_hottub' in tools._HANDLERS, 'query_hottub' in agents.AGENTS['home'].tool_names)"
    # ['assistant', 'coding', 'devops', 'home', 'research', 'talkie'] False False
for p in /api/chat /voice/api/chat; do for q in "Is the hot tub online?" "How hot is the hot tub right now?" "What's on the shopping list?" "Are the solar panels online?"; do
  curl -s localhost$p -H 'Content-Type: application/json' \
    -d "{\"model\":\"kronk:latest\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"$q\"}]}" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"]["content"])'; done; done
    # two "I can't check the hot tub…" answers per path, then the list and a solar answer
docker logs --since 5m kronk-orchestrator-1 2>&1 | grep -c '"tool": "ask_home"'   # 4 (the list and solar questions only)
docker ps --format '{{.Names}}' | grep -- -dev   # dev pair still running
ls -la ~/backups/hottub/                         # rollback tarball still there
```

`data/kronk_state.md` has no hot tub text, so the chat UI's `/message`
path needs no extra check for this item.

Rollback: `scripts/deploy_tag.sh deploy sprint-M1` restores the code. To
bring the monitor back as well:
`tar -xzf ~/backups/hottub/hottub_monitor-2026-11-15.tar.gz -C /home/drew/git-repos/drawsmcgraw/kronk`
(restores `hottub_monitor/` with its virtualenv at the same absolute path),
recreate the unit file from the copy above, then
`systemctl --user daemon-reload && systemctl --user enable --now kronk-hottub-monitor.service`.
It recreates `data/hottub/status.json` on its first poll.

## Production and data untouched during the build

- Containers: id, created, started and restart count of all 16 containers
  compared at 22:33 and 22:59. Identical except `kronk-orchestrator-dev-1`
  and `kronk-tool_service-dev-1` (dev rebuilds, `--no-deps`). Production
  nginx was not restarted. HA and Music Assistant were not touched.
- Unit: `disabled`, `inactive` at the start and at the end; the file is in
  place. Not started, enabled or edited.
- `./data` (excluding `data/postgres`): same 37 paths at 22:38 and 22:59.
  `data/hottub/status.json` mtime still 22:30.
- `~/.config/systemd`: nothing deleted or edited. The build started and
  stopped one transient unit, `bench-e4b` (:11497); it is inactive and
  nothing listens on :11497. `llama-gemma4-e4b` (:11438) was not touched.
- Production requests: one read-only `GET localhost:8003/hottub`, and
  `deploy_tag.sh status`. No production chat request.
- No sound: dry run stayed on, players unchanged in every replay and in the
  snapshot around the dev checks. No player was started, paused, stopped or
  skipped. `pipeline_bench.sh` not run. No LAN probe of the spa.
- `.env`, `.env.dev`: read for key names only, not edited.
- Created outside the repo: `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz`.

## Deviations from the plan, and findings

1. **The monitor was working** (top of this report). The plan's premise for
   M1b is out of date since 2026-10-24. Carried out as ordered, with a
   tarball so the only irreversible steps left are the coordinator's.
2. **Coordinator prompt names the hot tub.** Plan step 6 wants "I can't
   check that" from the coordinator. Without a sentence the coordinator
   delegated every time; a generic sentence did not help; a named one
   did. Prompt text, measured, no routing rule. Same pattern as M1's
   no-records sentence.
3. **Corrections confirmed:** no narration line for `query_hottub`, no
   `geckolib` in `tool_service`, no hot tub entry in `kronk-context.md` or
   `services.html`.
4. **No corpus row to re-expect.** `tests/voice_corpus.jsonl` has no hot
   tub row. None was added: the bench probe and the negative tests cover
   the behaviour.
5. **The bench lost its only hard composite.** `HARD_COMPOSITES` is empty;
   a replacement two-domain hard probe is not in M1b's scope.
6. **Stale production answer until promotion** (top of this report).
7. Criterion 4 lists a top-level `static/`; there is none. The pages live
   in `orchestrator/static/`, covered by the `orchestrator/` path.

## Left for Drew

- Read "The monitor was working" and confirm the removal still stands
  before the unit file and `data/hottub/` are deleted.
- Decide when `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz` goes.
- No `.env` keys to remove.
