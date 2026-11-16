# M1 end-to-end test report: remove the health and finance features

Test agent, 2026-11-15 21:41 to 22:24 EST (02:41 to 03:24 UTC on 2026-11-16),
branch `refactor` at `e11e5f3`. Tested against `docs/sprint/M1-criteria.md`.
Every result below rests on a command run on the box in this session; the
build report was read last and used as a list of claims to check. "M10" below
means the tree at tag `sprint-M10` (`b4185bb`), exported with `git archive`
into the session scratchpad; the only commits between it and the first M1
commit are the criteria file and a plan note.

## Results by criterion

| # | Result | Evidence |
| --- | --- | --- |
| 1 | PASS | Imported `orchestrator/agents.py` at HEAD: `AGENTS` = `assistant, coding, devops, home, research, talkie`; `agent_tool_defs()` = `ask_assistant, ask_coding, ask_devops, ask_home, ask_research`; no `HEALTH_*` or `FINANCE_*` attribute on `agents` or `tools`. Coordinator prompt diff against M10: "the user's personal data (health, finances, shopping list, home devices, weather)" became "the household's own data (shopping list, home devices, music, solar, weather)", plus the no-specialist rule and the no-records sentence. |
| 2 | PASS | `tools.TOOL_DEFINITIONS` has 21 tools, none of the four; `_HANDLERS` keys equal the definitions; `TOOL_TIMEOUTS` has no `query_finances`; the two narration branches are gone from `agents.py` (diff). |
| 3 | PASS | `main.py` grep for `finances`, `"/health"`, `_forward_to_health`, `garmin`, `labcorp`: only the zip refusal remains. Live on dev (`/dev/files`): Garmin-shaped CSV 200 (ordinary context), text 200, PDF 200, stored zip 422, deflated zip 422, zip renamed `.txt` 422, all with "Zip archives aren't supported. Upload a UTF-8 text file or a PDF." No 500, no traceback in `orchestrator-dev`'s log. Details under "Uploads". |
| 4 | PASS | The criterion's grep returns 8 lines, all in `tests/test_agentic_loop.py` (116, 117, 124, 127, 128, 132, 767, 776): the two negative tests. Same list as the build report. |
| 5 | PASS | `docker compose config --services`: `litellm nginx orchestrator searxng tool_service voice_tracer wyoming-piper`; with `--profile dev` plus `orchestrator-dev tool_service-dev`. `config -q` exits 0 both ways. Rendered config has no `HEALTH_SERVICE_URL`, `FINANCE_SERVICE_URL` or `depends_on` on a removed service. `HEALTH_AGENT_MODEL` and `FINANCE_AGENT_MODEL` still reach the two dev containers from `.env.dev` (Drew's file; criterion 21); nothing reads them. |
| 6 | PASS | `nginx.conf` locations: `/api/solar/`, nine `/probe/*` (orchestrator, llm, tools, searxng, ha, ma, voice_tracer, grafana, tempo), `/devstral/`, `/dev/`, `/`. None of the six removed. `nginx -t` in a throwaway `nginx:1.29-alpine` (image `5616878291a2`, the id `kronk-nginx-1` runs): "test is successful" on `kronk_kronk` with `--add-host host.docker.internal:host-gateway`, and on `--network none` with only the five surviving upstream names resolvable. The M10 file under the same five names: `[emerg] host not found in upstream "health_service" in /etc/nginx/nginx.conf:16`. Dev URLs: criterion 23. |
| 7 | PASS | `orchestrator/static/`: `health.html` and `finances.html` absent (and 404 at `/dev/static/…`). No `href`, fetch or probe for `/health`, `/finances`, `/retire`, `/api/health/`, `/api/finance/` or `/probe/{health,finance,retire}` in any `.html` or `kronk.js`. Served `/dev/services` and `/dev/agents`: 0 matches for health service, finance, retire, Garmin. `/dev/api/agents` lists Coordinator, Research, Home, Assistant, Coding, Talkie, Devops. The file picker's `accept` no longer has `.zip`. |
| 8 | PASS | Page served at `/dev/` has `const r = await fetch('/api/status');` (line 558), no `fetch('/health')`. `GET /api/status` 200 and `GET /dev/api/status` 200. The page's URLs are absolute, so the dev page polls production's `/api/status` (the M17 known gap); both answer. |
| 9 | PASS | Inside `orchestrator-dev`: `GET /health` 404 `{"detail":"Not Found"}`, `/api/agents` 200, `/api/status` 200; container `healthy`. Compose healthchecks: orchestrator and orchestrator-dev use `/api/agents`; tool_service(-dev), voice_tracer, LiteLLM, SearXNG use their own endpoints. `/probe/orchestrator` proxies `/api/status`. `:8003/health` and `:8103/health` 200; `/probe/{tools,voice_tracer,orchestrator,llm,searxng}` 200. No script, unit or HA file in the repo calls the orchestrator's `/health`. The build report's statement matches. |
| 10 | PASS | The eight files are gone. Collected test ids, M10 against HEAD, per file: only `test_agentic_loop.py` (45 → 46), `test_coordinator_bench.py` (17 → 19) and the eight deleted files (125) differ. Six ids dropped, nine added; see "Test count". The nine loop and pipeline tests that used the health agent keep their names and still run. None of the eight deleted files imported `agents`, `main` or the loop. |
| 11 | PASS | `./scripts/run_tests.sh -rs -q` → `989 passed, 2 skipped, 18 xfailed, 3 warnings in 11.42s`. The same command in the M10 export → `1111 passed, 2 skipped, 18 xfailed, 14 warnings`. Skips: the two `KRONK_LIVE` tests (now lines 956 and 963). Xfails: 18, unchanged. |
| 12 | PASS | `docs/bench/routing_eval.jsonl` 497 → 491 lines; 0 rows with label `health` or `finance`. Three rows keep `derived: health/finance` history fields under surviving labels, as the build report says. `routing_bench.py` and `coordinator_model_bench.py`: no health or finance specialist or probe left (one comment). Corpus offline layer runs inside the 989. |
| 13 | PASS | Probes exist (`ABSENT_PROBES`, `no_data_sleep`, `no_data_networth`). My runs on dev: 40 of 40 plain "do not have" answers (10 per question per mount), every one `route=direct rule=default`, no tool call, no delegation, no digit. Bench on an isolated E4B: 5/5 and 5/5. 40 of 40 on ten paraphrases. All verbatim below. Condition for production's web UI: O1. |
| 14 | PASS, see O4 | My own before and after runs of `coordinator_model_bench.py` on an isolated E4B copy (:11497, production flags): M10 tree base 26/28, hard 16/21, delegation probes 34/36; HEAD base 27/28, hard 14/21, delegation 33/36. The whole difference is `composite_hottub` (2/3 → 0/3) and `no_spurious` (4/5 → 5/5). `composite_hottub` at 20 more runs each: M10 7/20, HEAD 10/20 and 5/20. No other delegation probe moved. Recomputed from the builder's files: 33/36 → 34/36, as reported. |
| 15 | PASS | Full replay on dev: `docs/bench/voice_replay_2026-11-16_031207_m1-e2e.json`, 90 pass, 20 xfail, 2 fail, 2 skip; 46 executed, 66 checked read-only; 17 players unchanged; same corpus hash as the baseline. One row differs in status from the baseline (`plan-orbital-station-youtube`, pass → fail), explained under "Voice replay"; five reruns of it all pass. |
| 16 | PASS | `README.md`, `kronk-context.md`, `TECH_DEBT.md`, `CLAUDE.md`: no line describes a health, finance or retirement feature as present (grep; the remaining hits are "removed 2026-11-15" notes and unrelated uses of "health"). `ROADMAP.md`: no "Financial expert, phase 2", no "Health RAG completion"; phase 1 and `query_health` appear only in Shipped as removed. `docs/plans/FINANCIAL_EXPERT_PLAN.md` → `docs/history/FINANCIAL_EXPERT_PLAN.md` (rename, with a "removed 2026-11-15" status header). No other tracked doc at M10 was named for these features. One leftover outside the criterion's list: O7. |
| 17 | PASS | `scripts/deploy_tag.sh` diff against M10 removes only the five-line `retirement-calc` link block; `grep retirement-calc` over the tree at HEAD (outside docs): nothing. |
| 18 | PASS | Every listed path exists with the stated size and file count (table under "Deletion manifest"). Docker: no named volume for the three; three containers and three `kronk-*` images as listed. `data/postgres` and `../retirement-calc` are stated as excluded and are untouched. Two images the manifest misses: O2. |
| 19 | PASS | `find data` at 21:41 and 22:24: 2188 paths both times, none removed, none added. Changed: files the running system writes (`gpu_mem.json`, `hottub/status.json`, `solar.db`, `voice_tracer_state.json`, `weather_cache.json`) and `data/dev/*`. `data/postgres`: 0 changes. All 19 containers: same id and Created at start and end; every production container's Created (20:04 EST, the M17 deploy) predates the first M1 commit (21:23) and its StartedAt is 01:05 UTC with 0 restarts. Only `kronk-orchestrator-dev-1` was created during the build (21:28). |
| 20 | PASS | The report gives deploy from the tag, `docker rm -f` of the three containers by name, the data `rm` lines, and post checks. Read and dry-run under "Promotion commands": order is right, no `--remove-orphans`, no `down -v`, HA and MA never named. Two changes recommended (O1, O3). |
| 21 | PASS | Key names only: `.env` holds `HA_TOKEN HA_URL LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY MM_SSH_TARGET SOLAR_SERIAL UNIFI_USER UNIFI_PASSWORD MA_URL MA_TOKEN PANDORA_USERNAME PANDORA_PASSWORD`: none belongs to the removed services. `.env.dev` still has `HEALTH_AGENT_MODEL` and `FINANCE_AGENT_MODEL`; the report lists both for Drew. `.env.dev.example` has neither. At M10 no removed service had an `env_file` or read a key from `.env`. `secrets/garmin.json` is listed too. |
| 22 | PASS | Dev pair runs HEAD (md5 of every tracked `.py`, `.html`, `.js`, `.css` under `orchestrator/` equals the file in `kronk-orchestrator-dev-1`; `tool_service/` has no diff since M10). Both mounts, all 200: capital of Australia → "Canberra." / "The capital of Australia is Canberra."; weather tomorrow → sunny, high near 61°F (`home/weather`); web search → research agent, `web_search` called; "play some jazz in the office" → "Dry run: would play Mushroom Jazz Radio, for jazz, on the Office speaker. Nothing was sent to the speaker." Traces under "Dev path". |
| 23 | PASS | `/dev/health`, `/dev/finances`, `/dev/retire/`, `/dev/retire`, `/dev/api/health/query`, `/dev/api/health/`, `/dev/api/finance/query`, `/dev/api/finance/`, `/dev/probe/health`, `/dev/probe/finance`, `/dev/probe/retire`, `/dev/static/health.html`, `/dev/static/finances.html`: each 404, `application/json`, body `{"detail":"Not Found"}`. `POST /dev/api/health/import/csv` and `POST /dev/api/finance/upload`: 404. No 502, no stack trace. Eleven surviving dev URLs answer 200. |

**Verdict: ITEM PASSES.** Criteria 1 to 23 pass. No criterion failed and none
was untestable. Two things must happen at promotion for the result to hold in
production (O1, O3); they are promotion steps, not build defects.

## Failures

None.

## Observations that are not failures

**O1. Production's `data/kronk_state.md` undoes the absent-data answer on the
web UI. Its two health lines must go at promotion.** The file's lines 5 and 6
say the user has a Withings scale "(body composition data available from May
2024)" and a Garmin device "(health data: sleep and activities from 2018, HRV
from Sep 2025)". `_run_pipeline` adds the file to the coordinator's context
when `include_local_context` is set, which is the `/message` path (the chat
UI), not the shims. Dev mounts `data/dev`, which has no such file, so no dev
request sees it. To measure it without touching `./data`, I ran the HEAD
coordinator (`agents.run_stream(agents.COORDINATOR, …)`) in a side process
inside `kronk-orchestrator-dev-1`, with the file's text passed as
`[Kronk shared state]` exactly as `main.py` does, 10 runs per question, and
the same without it:

| Question | With the state text | Without |
| --- | --- | --- |
| how did I sleep last night | 5 of 10 clean. 2 delegated to `ask_home` first; 1 said "I have access to your health data via your Garmin device … I will retrieve that information now."; 1 said "The health information available in my records is from 2018."; 1 recited the prompt sentence itself. | 10 of 10 clean |
| what did I weigh last week | 3 of 10 clean. 6 said they hold Withings body-composition data from May 2024; 1 delegated to `ask_home`. | 10 of 10 clean |
| what's my net worth | 10 of 10 clean | 10 of 10 clean |

Answers verbatim under "Probe answers". So after promotion, "how did I sleep"
typed into the chat UI fails criterion 13's standard about half the time
until those two lines are removed. The build report found the file and left
the edit as an optional step 6 "only with Drew's yes". It should be a
required promotion step. The file is under `./data` and is not on Drew's
deletion list, so it needs his yes; the edit is two lines and the build
report's `sed` (with the backup copy first) is correct.

**O2. The manifest misses two Garmin images.** `docker images` also shows
`assistant-garmin_setup_playwright:latest` (`e3e1c025a33a`, 3.55 GB) and
`assistant-garmin_setup:latest` (`91b23ce918c2`, 429 MB), both 7 months old,
from the same old `assistant` project as the two images the manifest does
list as optional. No container uses them. They are the Garmin login tooling
(the likely writer of `data/garmin_jwt_web.txt`, `data/garmin_login_debug.png`
and `secrets/garmin.json`). Add them to the optional `docker image rm` line.
Not health-related and not for M1: `assistant-orchestrator`,
`assistant-tool_service`, `assistant-llm_service` from the same era. Nothing
else health, finance or retirement related was found under `./data`,
`./secrets`, Docker volumes (no volume belongs to the three services; the
dangling ones are Langfuse and anonymous volumes from April and October) or
the user systemd units.

**O3. Chat pages already open keep polling `/health` after promotion.** The
old `index.html` polls `GET /health` every 30 s. After the deploy that URL is
a 404, so any tab loaded before promotion shows the status dot as offline
until it is reloaded. Harmless; reload the tabs (and expect 404s for
`/health` in the nginx log from stale tabs).

**O4. Criterion 14 in detail.** The coordinator bench has one delegation
probe that is noisy on both prompts, `composite_hottub` (needs `ask_home` for
the heater draw, then `ask_research` for the rate; 3 runs). Seen so far:
builder before 1/3, builder after 2/3; mine before 2/3, mine after 0/3. With
20 runs each on the same server: M10 prompt 7/20, HEAD prompt 10/20 and 5/20
(15/40). The failures are the same on both: the model asks the user for the
heater's power instead of asking `ask_home`. I read this as no measurable
change; on three runs the scoreboard can move by two either way. The probe
goes away with M1b. Further checks on the same server, 5 runs each on both
prompts: solar today, hot tub online, shopping list, what's playing in the
kitchen, solar savings this year → `ask_home` 25/25 before and 25/25 after;
AMD stock price, 30-year mortgage rate, Elon Musk's net worth →
`ask_research` 15/15 and 15/15. Three general-knowledge questions (healthy
resting heart rate, teenager sleep hours, what is a 401k): the old prompt
called `ask_health` 3 times in 15; the new one answers directly 14 of 15 and
calls `ask_research` once. Six mangled music sentences from the corpus, 10
runs each: `ask_home` 21/60 before, 20/60 after. So the no-records sentence
does not make the coordinator delegate less.

**O5. The sentence does not cause refusals of things Kronk can do.** 56 of 56
"must still work" requests on dev were answered or delegated correctly (14
prompts, 2 runs, both mounts): solar today, weather tomorrow, shopping list,
"are the solar panels healthy", AMD's stock price, healthy resting heart
rate, teenager sleep, "what is a 401k", mortgage rate, Elon Musk's net worth,
solar savings this year, what's playing in the office, "play some music to
help me sleep in the office" (dry run), sunset tomorrow. Note that "what's
the weather tomorrow" is caught by the `weather` routing rule and never
reaches the coordinator, so it does not exercise the sentence; the solar,
shopping, now-playing, stock and mortgage requests do.

**O6. The build report's first `nginx -t` line needs one more flag to
reproduce.** On `kronk_kronk` alone the test fails with `host not found in
upstream "host.docker.internal"`; with `--add-host
host.docker.internal:host-gateway` (what compose gives nginx) it passes. Not
an M1 matter.

**O7. Small leftovers, none in a criterion's scope.**
`orchestrator/telemetry.py:131` still lists `finances|health` in the
excluded-URL pattern for page loads (harmless: those URLs are 404 now).
`scripts/run_tests.sh:4` still names `chromadb` in its comment, and
`tests/.venv` still has the three packages dropped from
`tests/requirements.txt`. `docs/features/verbose-errors.md:18` still says
"tool_service / health_service routes". `agents.html` and `kronk-context.md`
are stale beyond M1, as the build report says.

**O8. Images were never accepted by `/files`.** A PNG gets 422 "File must be
UTF-8 text or a PDF" on dev; the M10 code has the same branch and message, so
this is unchanged. Office files (`.docx`, `.xlsx`) start with the zip magic
bytes and now get the zip message instead of the UTF-8 one; both are 422.

**O9. The ambient date is UTC, as the build report's finding 6 says.** At
21:47 EST on Sunday the voice mount answered "Tomorrow, Tuesday, will be
sunny …" and a sunset question was answered for "Tuesday, November 17".
Pre-existing, not M1.

**O10. Other model behaviour seen, not M1.** "what time is sunset tomorrow"
gave four different answers in four runs (5:52 pm, 4:51 pm, 4:53 pm, and "I
don't have access to specific sunset times"). "what's AMD's stock price right
now" gave $488.78 twice, $404.40 once and a quote table once. "how much money
did the solar panels save us" took 22 to 52 s. The latencies in my probe
tables are inflated: the state-file probe and the bench ran at the same time
as part of the dev probes.

## Probe answers, verbatim

All through nginx on the dev path, non-streaming, one request at a time,
about 02:47 to 03:03 UTC. `events` is what `kronk-orchestrator-dev-1` logged for the
request (matched by request id).

### The two probes, 10 runs per mount (40 requests)

Every one: HTTP 200, `route=direct rule=default`, one coordinator round, no
tool call, no delegation.

```
/dev/api/chat        sleep  1  I do not have access to your sleep data.
/dev/api/chat        sleep  2  I do not have access to your personal health or sleep data.
/dev/api/chat        sleep  3  I do not have access to your health, sleep, or fitness records.
/dev/api/chat        sleep  4  I don't have access to that information.
/dev/api/chat        sleep  5  I do not have access to your sleep records. I do not keep health or personal tracking data.
/dev/api/chat        sleep  6  I do not have access to your health or sleep records.
/dev/api/chat        sleep  7  I do not have access to your sleep data. I keep no health or personal records.
/dev/api/chat        sleep  8  I do not have access to health or sleep records.
/dev/api/chat        sleep  9  I do not have access to your health or sleep records.
/dev/api/chat        sleep 10  I do not have access to your health or sleep records.
/dev/api/chat        worth  1  I do not have access to your financial records, so I cannot provide your net worth.
/dev/api/chat        worth  2  I don't have access to your personal financial records, so I cannot provide your net worth.
/dev/api/chat        worth  3  I do not have access to your financial records or net worth.
/dev/api/chat        worth  4  I do not have access to your personal financial records or net worth.
/dev/api/chat        worth  5  I do not have access to your financial records or net worth.
/dev/api/chat        worth  6  I do not have access to your personal financial records, so I cannot provide your net worth.
/dev/api/chat        worth  7  I do not have access to your financial records or net worth.
/dev/api/chat        worth  8  I do not have access to your financial records.
/dev/api/chat        worth  9  I don't have access to your personal financial records, so I cannot provide your net worth.
/dev/api/chat        worth 10  I do not have access to your personal financial records, so I cannot provide your net worth.
/dev/voice/api/chat  sleep  1  I do not have access to your health, sleep, or fitness records.
/dev/voice/api/chat  sleep  2  I don't have access to your sleep or health records.
/dev/voice/api/chat  sleep  3  I do not have access to your sleep records.
/dev/voice/api/chat  sleep  4  I do not have access to your personal health or sleep records.
/dev/voice/api/chat  sleep  5  I don't have access to your sleep data.
/dev/voice/api/chat  sleep  6  I do not have access to your health, sleep, or fitness records.
/dev/voice/api/chat  sleep  7  I don't have access to that information. I keep no health or sleep records.
/dev/voice/api/chat  sleep  8  I do not have access to your personal health or sleep records.
/dev/voice/api/chat  sleep  9  I do not have access to your health, sleep, or fitness records, so I cannot answer that question.
/dev/voice/api/chat  sleep 10  I do not have access to your sleep data.
/dev/voice/api/chat  worth  1  I don't have that information.
/dev/voice/api/chat  worth  2  I do not have access to your financial records, so I cannot tell you your net worth.
/dev/voice/api/chat  worth  3  I don't have access to your financial records, so I cannot provide your net worth.
/dev/voice/api/chat  worth  4  I do not have access to your personal financial records, so I cannot provide your net worth.
/dev/voice/api/chat  worth  5  I don't have access to your financial records.
/dev/voice/api/chat  worth  6  I don't have access to your financial records, so I cannot provide your net worth.
/dev/voice/api/chat  worth  7  I do not have access to your financial records or net worth.
/dev/voice/api/chat  worth  8  I don't have access to your financial records or personal data, so I cannot tell you your net worth.
/dev/voice/api/chat  worth  9  I do not have access to your personal financial records or net worth.
/dev/voice/api/chat  worth 10  I do not have access to your financial records or personal information.
```

### Paraphrases of absent data, 2 runs per mount (40 requests)

Every one: HTTP 200, `route=direct rule=default`, no tool call, no figure.

```
/dev/api/chat        what was my heart rate yesterday #1
    I do not have access to your health records or fitness data, including your heart rate.
/dev/api/chat        what was my heart rate yesterday #2
    I do not have access to health or fitness records.
/dev/api/chat        how much is in my 401k #1
    Kronk does not keep personal financial records like a 401k balance.
/dev/api/chat        how much is in my 401k #2
    I don't have access to financial records like your 401k balance.
/dev/api/chat        what did I weigh last week #1
    I do not keep health or fitness records. I do not have that information.
/dev/api/chat        what did I weigh last week #2
    I do not have access to your health or personal records, so I do not know what you weighed last week.
/dev/api/chat        show my bloodwork #1
    I do not have access to your health records or bloodwork.
/dev/api/chat        show my bloodwork #2
    I do not have access to your health records or bloodwork.
/dev/api/chat        how many steps did I take today #1
    I do not have access to your fitness tracking data, including the number of steps you took today.
/dev/api/chat        how many steps did I take today #2
    I do not have access to your health or fitness records, including the number of steps you took today.
/dev/api/chat        what's my HRV trend this month #1
    I do not have access to your health or fitness data, including your HRV trends.
/dev/api/chat        what's my HRV trend this month #2
    I do not have access to your health or fitness records, so I cannot provide your HRV trend.
/dev/api/chat        how much did I spend on groceries last month #1
    I do not have access to your financial or grocery spending records.
/dev/api/chat        how much did I spend on groceries last month #2
    I do not have access to your personal financial records or spending history.
/dev/api/chat        what's my checking account balance #1
    I do not have access to your financial records or account balances.
/dev/api/chat        what's my checking account balance #2
    I do not have access to your financial records.
/dev/api/chat        am I on track for retirement #1
    I do not have access to your financial records or retirement planning data.
/dev/api/chat        am I on track for retirement #2
    I do not have access to your personal financial records, so I cannot tell you if you are on track for retirement.
/dev/api/chat        what was my cholesterol on my last lab report #1
    I do not have access to personal health or medical records.
/dev/api/chat        what was my cholesterol on my last lab report #2
    I do not have access to your personal health records or lab reports.
/dev/voice/api/chat  what was my heart rate yesterday #1
    I do not have access to your health records, so I cannot tell you what your heart rate was yesterday.
/dev/voice/api/chat  what was my heart rate yesterday #2
    I do not have access to your health, sleep, fitness, or financial records.
/dev/voice/api/chat  how much is in my 401k #1
    I do not have access to your health, sleep, fitness, or financial records. I cannot tell you how much is in your 401k.
/dev/voice/api/chat  how much is in my 401k #2
    I do not have access to your financial records.
/dev/voice/api/chat  what did I weigh last week #1
    I do not have access to your personal health or fitness records.
/dev/voice/api/chat  what did I weigh last week #2
    I do not have access to your personal health records, so I cannot tell you what you weighed last week.
/dev/voice/api/chat  show my bloodwork #1
    I do not have access to your health records.
/dev/voice/api/chat  show my bloodwork #2
    I do not have access to your health or personal medical records.
/dev/voice/api/chat  how many steps did I take today #1
    I do not have access to your health or fitness records.
/dev/voice/api/chat  how many steps did I take today #2
    I do not have access to your personal health or fitness records.
/dev/voice/api/chat  what's my HRV trend this month #1
    I do not have access to your health, sleep, fitness, or financial records, including HRV data.
/dev/voice/api/chat  what's my HRV trend this month #2
    I do not have access to your health, sleep, or fitness records, including Heart Rate Variability (HRV) data.
/dev/voice/api/chat  how much did I spend on groceries last month #1
    I do not have access to your financial or grocery spending records.
/dev/voice/api/chat  how much did I spend on groceries last month #2
    I do not have access to your financial or spending records.
/dev/voice/api/chat  what's my checking account balance #1
    I don't have access to your financial records.
/dev/voice/api/chat  what's my checking account balance #2
    I do not have access to financial or personal account information.
/dev/voice/api/chat  am I on track for retirement #1
    I do not have access to your personal financial information, so I cannot tell you if you are on track for retirement.
/dev/voice/api/chat  am I on track for retirement #2
    I do not have access to your personal financial records, savings, or retirement goals, and no specialist holds that information. Therefore, I cannot tell you if you are on track.
/dev/voice/api/chat  what was my cholesterol on my last lab report #1
    I do not have access to your personal health records or lab reports. I do not keep health information.
/dev/voice/api/chat  what was my cholesterol on my last lab report #2
    I do not have access to your health records, including lab reports.
```

### Requests that must still work, 2 runs per mount (56 requests)

Answers cut at 200 characters. `tools` lists the calls in order.

```
/dev/api/chat        how much solar did we make today #1  [6.75 s, direct/default, tools=['coordinator:ask_home', 'home:solar_energy']]
    You generated 10.3 kWh of solar energy today.
/dev/api/chat        how much solar did we make today #2  [5.68 s, direct/default, tools=['coordinator:ask_home', 'home:solar_energy']]
    You have produced 10.3 kWh of solar energy today.
/dev/api/chat        what's the weather tomorrow #1  [6.62 s, home/weather, tools=[]]
    Tomorrow in Laurel, MD will be sunny with a high near 61°F and a northwest wind around 6 mph.
/dev/api/chat        what's the weather tomorrow #2  [3.91 s, home/weather, tools=[]]
    Tomorrow in Laurel, MD will be sunny with a high of 61°F and Northwest winds around 6 mph.
/dev/api/chat        what's on the shopping list #1  [10.86 s, direct/default, tools=['coordinator:ask_home', 'home:shopping_list_view']]
    Your shopping list is currently empty.
/dev/api/chat        what's on the shopping list #2  [12.36 s, direct/default, tools=['coordinator:ask_home', 'home:shopping_list_view']]
    Your shopping list is currently empty.
/dev/api/chat        are the solar panels healthy #1  [11.97 s, direct/default, tools=['coordinator:ask_home', 'home:solar_status']]
    Five inverters are currently underperforming, and two specific inverters have been confirmed as failing over extended periods.
/dev/api/chat        are the solar panels healthy #2  [12.4 s, direct/default, tools=['coordinator:ask_home', 'home:solar_status']]
    Your solar output is very low today and the system is showing health concerns. Two inverters have been confirmed failing for 122 days, and five others are currently underperforming.
/dev/api/chat        what's AMD's stock price right now #1  [17.59 s, direct/default, tools=['coordinator:ask_research', 'research:web_search', 'research:fetch_url']]
    AMD's current stock price information, based on the overnight session on the Blue Ocean ATS, shows a Previous Close of 585.30, an Open of 566.67, and a Day's Range between 520.01 and 571.12. The Bid i
/dev/api/chat        what's AMD's stock price right now #2  [6.28 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    Advanced Micro Devices, Inc. (AMD) is currently trading at $488.78.
/dev/api/chat        what is a healthy resting heart rate for an adult #1  [1.56 s, direct/default, tools=[]]
    A healthy resting heart rate for an adult typically falls between 60 and 100 beats per minute (BPM). However, highly fit athletes often have a lower resting heart rate, sometimes as low as 40 BPM.
/dev/api/chat        what is a healthy resting heart rate for an adult #2  [7.4 s, direct/default, tools=[]]
    A healthy resting heart rate for an adult is typically between 60 and 100 beats per minute (BPM). For many individuals who are physically fit, the resting heart rate may be lower, often falling in the
/dev/api/chat        how many hours of sleep does a teenager need #1  [0.45 s, direct/default, tools=[]]
    A teenager generally needs between 8 and 10 hours of sleep per night.
/dev/api/chat        how many hours of sleep does a teenager need #2  [1.21 s, direct/default, tools=[]]
    Teenagers generally need 8 to 10 hours of sleep per night.
/dev/api/chat        what is a 401k #1  [21.3 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    A 401(k) is an employer-sponsored retirement plan where employees can contribute a portion of their salary to an individual account. Key features include: * **Tax Advantages:** Employee contributions 
/dev/api/chat        what is a 401k #2  [6.3 s, direct/default, tools=[]]
    A 401(k) is a type of retirement savings plan offered by many employers. It allows employees to save and invest a portion of their paycheck on a pre-tax basis, meaning the money goes into retirement s
/dev/api/chat        what's the average 30 year mortgage rate today #1  [29.97 s, direct/default, tools=['coordinator:ask_research', 'research:web_search', 'research:fetch_url']]
    The average 30-year mortgage rate varies slightly depending on the source. According to Bankrate's national survey, the average rate is currently 7.24%. The general fixed-rate listing is 7.28%. Specif
/dev/api/chat        what's the average 30 year mortgage rate today #2  [18.32 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    The average 30-year fixed mortgage rate is currently around **7.24% to 7.50%**, depending on the specific source. Recent reports from Bankrate and Forbes Advisor cite figures in the low 7% range, whil
/dev/api/chat        what is Elon Musk's net worth #1  [15.88 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    As of November 2026, Elon Musk's net worth is approximately $887.2 billion.
/dev/api/chat        what is Elon Musk's net worth #2  [14.98 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    Elon Musk's net worth is reported to be **$887.2 billion**.
/dev/api/chat        how much money did the solar panels save us this year #1  [47.97 s, direct/default, tools=['coordinator:ask_home', 'home:solar_energy', 'coordinator:ask_research', 'research:web_search']]
    Using the average electricity rate of 16.42 cents per kWh for Laurel, MD, your solar panels have generated 2504.1 kWh. This equates to a savings of $411.46 so far this year. Note: The electricity rate
/dev/api/chat        how much money did the solar panels save us this year #2  [52.09 s, direct/default, tools=['coordinator:ask_home', 'home:solar_energy', 'coordinator:ask_research', 'research:web_search']]
    Based on the production of 2504.1 kWh since July 17, 2026, and assuming the current average residential rate of 17.91¢/kWh in Laurel, MD, the solar panels have saved approximately $448.88.
/dev/api/chat        what's playing in the office #1  [15.12 s, direct/default, tools=['coordinator:ask_home', 'home:now_playing']]
    Nothing is playing on the Office speaker.
/dev/api/chat        what's playing in the office #2  [9.18 s, direct/default, tools=['coordinator:ask_home', 'home:now_playing']]
    Nothing is playing on the Office speaker.
/dev/api/chat        play some music to help me sleep in the office #1  [11.08 s, home/music, tools=['home:play_music']]
    Dry run: would play the album Sleep Music - Help Me Sleep, Ultimate Sleep Remedy to Fall Asleep Fast by Sleep Music Academy on the Office speaker. Nothing was sent to the speaker.
/dev/api/chat        play some music to help me sleep in the office #2  [9.53 s, home/music, tools=['home:play_music']]
    Dry run: would play Lullabies & Songs For Helping Babies Sleep, for sleep, on the Office speaker. Nothing was sent to the speaker.
/dev/api/chat        what time is sunset tomorrow #1  [59.33 s, direct/default, tools=['coordinator:ask_home', 'coordinator:ask_research', 'research:web_search', 'research:fetch_url']]
    Sunset tomorrow, Tuesday, November 17, 2026, in Laurel, MD, will be at 5:52:42 pm EST.
/dev/api/chat        what time is sunset tomorrow #2  [28.83 s, direct/default, tools=['coordinator:ask_home', 'coordinator:ask_research', 'research:web_search', 'research:fetch_url']]
    Sunset tomorrow in Laurel, MD, will be at 4:51 pm.
/dev/voice/api/chat  how much solar did we make today #1  [10.38 s, direct/default, tools=['coordinator:ask_home', 'home:solar_energy']]
    You have produced 10.3 kWh of solar energy today.
/dev/voice/api/chat  how much solar did we make today #2  [13.14 s, direct/default, tools=['coordinator:ask_home', 'home:solar_energy']]
    We produced 10.3 kWh of solar energy today.
/dev/voice/api/chat  what's the weather tomorrow #1  [9.01 s, home/weather, tools=[]]
    Tomorrow will be sunny with a high of 61°F and a northwest wind around 6 mph.
/dev/voice/api/chat  what's the weather tomorrow #2  [5.13 s, home/weather, tools=[]]
    Tomorrow in Laurel, MD, will be sunny with a high near 61°F and a northwest wind of 6 mph.
/dev/voice/api/chat  what's on the shopping list #1  [9.27 s, direct/default, tools=['coordinator:ask_home', 'home:shopping_list_view']]
    The shopping list is currently empty.
/dev/voice/api/chat  what's on the shopping list #2  [9.1 s, direct/default, tools=['coordinator:ask_home', 'home:shopping_list_view']]
    Your shopping list is empty right now.
/dev/voice/api/chat  are the solar panels healthy #1  [15.49 s, direct/default, tools=['coordinator:ask_home', 'home:solar_status']]
    Two inverters have been confirmed as failing for several days, and five others are currently underperforming, resulting in a low total output from the solar system.
/dev/voice/api/chat  are the solar panels healthy #2  [13.09 s, direct/default, tools=['coordinator:ask_home', 'home:solar_status']]
    The system output is very low, and five inverters are currently underperforming. Two inverters have been failing for an extended period.
/dev/voice/api/chat  what's AMD's stock price right now #1  [5.44 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    The current stock price for AMD is $404.40.
/dev/voice/api/chat  what's AMD's stock price right now #2  [6.07 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    AMD's current stock price is $488.78.
/dev/voice/api/chat  what is a healthy resting heart rate for an adult #1  [2.16 s, direct/default, tools=[]]
    A healthy resting heart rate for an adult is typically between 60 and 100 beats per minute. Some athletes may have a lower resting heart rate.
/dev/voice/api/chat  what is a healthy resting heart rate for an adult #2  [1.45 s, direct/default, tools=[]]
    A healthy resting heart rate for an adult is typically between 60 and 100 beats per minute (bpm).
/dev/voice/api/chat  how many hours of sleep does a teenager need #1  [0.25 s, direct/default, tools=[]]
    A teenager typically needs between 8 and 10 hours of sleep per night.
/dev/voice/api/chat  how many hours of sleep does a teenager need #2  [0.62 s, direct/default, tools=[]]
    Teenagers generally need 8 to 10 hours of sleep per night.
/dev/voice/api/chat  what is a 401k #1  [1.41 s, direct/default, tools=[]]
    A 401k is a retirement savings plan sponsored by an employer. Employees can contribute a portion of their paycheck to the account, which often allows for employer matching. Contributions are typically
/dev/voice/api/chat  what is a 401k #2  [3.47 s, direct/default, tools=[]]
    A 401(k) is a tax-advantaged savings and investment plan sponsored by an employer in the United States. It allows employees to save for retirement. Contributions are typically made pre-tax, meaning th
/dev/voice/api/chat  what's the average 30 year mortgage rate today #1  [7.42 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    As of today, November 16, 2026, several financial sources report the average 30-year mortgage rate is approximately 7.24% to 7.50%. Note that rates fluctuate frequently and depend on the lender, loan 
/dev/voice/api/chat  what's the average 30 year mortgage rate today #2  [7.75 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    The average 30-year mortgage rate is currently fluctuating based on the source, with Bankrate reporting an average of 7.24% and Forbes Advisor reporting an average of 7.26%.
/dev/voice/api/chat  what is Elon Musk's net worth #1  [8.2 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    As of the recent reports from sources like Forbes and networth.com, Elon Musk's net worth is estimated to be between $845 billion and $977 billion. These figures fluctuate based on the market value of
/dev/voice/api/chat  what is Elon Musk's net worth #2  [7.75 s, direct/default, tools=['coordinator:ask_research', 'research:web_search']]
    Elon Musk's net worth was reported as about $977 billion on Forbes’ Real-Time Billionaires list on October 13, 2026.
/dev/voice/api/chat  how much money did the solar panels save us this year #1  [21.56 s, direct/default, tools=['coordinator:ask_home', 'home:solar_energy', 'coordinator:ask_research', 'research:web_search']]
    The solar panels produced 2,504.1 kWh this year. The estimated savings depend on your specific electricity rate in Laurel, MD. Based on current data for the area, the savings range from approximately 
/dev/voice/api/chat  how much money did the solar panels save us this year #2  [32.61 s, direct/default, tools=['coordinator:ask_home', 'coordinator:ask_home', 'home:solar_energy', 'coordinator:ask_research', 'research:web_search']]
    The solar panels have produced a total of 2504.1 kWh since July 17, 2026. This is not a measurement for the entire calendar year 2026. Since the local electricity rate in Laurel, MD, varies significan
/dev/voice/api/chat  what's playing in the office #1  [4.97 s, direct/default, tools=['coordinator:ask_home', 'home:now_playing']]
    Nothing is playing on the Office speaker.
/dev/voice/api/chat  what's playing in the office #2  [4.35 s, direct/default, tools=['coordinator:ask_home', 'home:now_playing']]
    Nothing is playing on the Office speaker.
/dev/voice/api/chat  play some music to help me sleep in the office #1  [5.22 s, home/music, tools=['home:play_music']]
    Dry run: would play the album Sleep Music - Help Me Sleep, Ultimate Sleep Remedy to Fall Asleep Fast by Sleep Music Academy on the Office speaker. Nothing was sent to the speaker.
/dev/voice/api/chat  play some music to help me sleep in the office #2  [4.47 s, home/music, tools=['home:play_music']]
    Dry run: would play Lullabies & Songs For Helping Babies Sleep, for sleep, on the Office speaker. Nothing was sent to the speaker.
/dev/voice/api/chat  what time is sunset tomorrow #1  [27.37 s, direct/default, tools=['coordinator:ask_home', 'home:get_weather', 'coordinator:ask_research', 'research:web_search', 'research:fetch_url']]
    Sunset tomorrow in Laurel, MD, is at 4:53 PM EST.
/dev/voice/api/chat  what time is sunset tomorrow #2  [14.5 s, direct/default, tools=['coordinator:ask_home', 'home:get_weather']]
    I don't have access to specific sunset times, but tomorrow in Laurel, MD will be sunny with a high near 61°F.
```

### With and without production's `kronk_state.md` text (O1)

HEAD coordinator run in a side process inside `kronk-orchestrator-dev-1`, 10
runs per question. Answers cut at 260 characters; `[delegated: home]` marks a
run that called `ask_home` first.

```
with_state how did I sleep last night #1
    I don't have that information.
with_state how did I sleep last night #2
    I do not have access to your personal health or sleep data.
with_state how did I sleep last night #3  [delegated: home]
    I do not have access to your health or sleep data.
with_state how did I sleep last night #4
    I have access to your health data via your Garmin device, which includes sleep and activities from 2018. To see the details of last night's sleep, I will retrieve that information now.
with_state how did I sleep last night #5  [delegated: home]
    I don't have access to your health or sleep records.
with_state how did I sleep last night #6
    I don't have your sleep data for last night. The health information available in my records is from 2018.
with_state how did I sleep last night #7
    Kronk keeps no health, sleep, fitness or financial records, and no specialist has them (home is devices, not people): for those, say plainly that you don't have that information.
with_state how did I sleep last night #8
    I don't have access to your Garmin data or personal health records.
with_state how did I sleep last night #9
    I don't have access to your personal sleep data.
with_state how did I sleep last night #10
    I don't have access to your health or sleep records.
with_state what's my net worth #1
    I do not have access to your financial records or personal banking information, so I cannot tell you what your net worth is.
with_state what's my net worth #2
    I do not have access to your financial records or net worth information.
with_state what's my net worth #3
    I do not have access to your personal financial records, so I cannot provide your net worth.
with_state what's my net worth #4
    I do not have access to your personal financial records, so I cannot provide your net worth.
with_state what's my net worth #5
    I do not have access to your financial records or net worth.
with_state what's my net worth #6
    I do not have access to your financial records or personal information. I cannot tell you your net worth.
with_state what's my net worth #7
    I don't have access to your financial records, so I cannot provide your net worth.
with_state what's my net worth #8
    I do not have access to your financial records, so I cannot tell you your net worth.
with_state what's my net worth #9
    I do not have access to your financial records or net worth.
with_state what's my net worth #10
    I do not have access to your financial records, so I cannot provide your net worth.
with_state what did I weigh last week #1
    I have body composition data from your Withings scale, but the available data only goes back to May 2024. I do not have access to your weight from last week.
with_state what did I weigh last week #2
    I do not have access to your weight data from last week. The body composition data available from your Withings scale was last updated in May 2024.
with_state what did I weigh last week #3
    I do not have access to your weight data from last week. My available health records are limited to body composition data from May 2024.
with_state what did I weigh last week #4
    I don't have access to that specific health record.
with_state what did I weigh last week #5
    I do not have your weight data from last week. I only have body composition data from May 2024.
with_state what did I weigh last week #6  [delegated: home]
    I do not have access to your personal health data or weight history.
with_state what did I weigh last week #7
    I don't have access to your health or personal records, including your weight.
with_state what did I weigh last week #8
    The shared state indicates that body composition data from the Withings scale is available from May 2024. I do not have access to a log of your historical weigh-ins for last week.
with_state what did I weigh last week #9
    I do not have access to your personal health or weight records.
with_state what did I weigh last week #10
    I don't have your weight from last week. The body composition data I have access to is only available from May 2024.
no_state   how did I sleep last night #1
    I do not have access to your sleep or health records.
no_state   how did I sleep last night #2
    I do not have access to your sleep records.
no_state   how did I sleep last night #3
    I do not have access to your sleep records.
no_state   how did I sleep last night #4
    I don't have access to your sleep records.
no_state   how did I sleep last night #5
    I do not have access to your sleep data.
no_state   how did I sleep last night #6
    I don't have that information. I do not keep health or sleep records.
no_state   how did I sleep last night #7
    I do not have access to your health or sleep records.
no_state   how did I sleep last night #8
    I don't have access to that information.
no_state   how did I sleep last night #9
    I do not have access to your personal sleep records.
no_state   how did I sleep last night #10
    I do not have access to your health, sleep, or personal records.
no_state   what's my net worth #1
    I do not have access to your personal financial records, so I cannot tell you your net worth.
no_state   what's my net worth #2
    I do not have that information.
no_state   what's my net worth #3
    I do not have access to personal financial records or information.
no_state   what's my net worth #4
    I do not have access to your financial records, so I cannot provide your net worth.
no_state   what's my net worth #5
    I do not have access to your personal financial records.
no_state   what's my net worth #6
    I do not have access to your personal financial records or net worth.
no_state   what's my net worth #7
    I do not have access to your personal financial records or net worth information.
no_state   what's my net worth #8
    I do not have access to your financial records or net worth.
no_state   what's my net worth #9
    I do not have access to your financial records or net worth.
no_state   what's my net worth #10
    I do not have access to your financial records or personal net worth information.
no_state   what did I weigh last week #1
    I do not have access to your health, sleep, fitness, or financial records.
no_state   what did I weigh last week #2
    I do not have access to your personal health or fitness records.
no_state   what did I weigh last week #3
    I do not have access to your health or fitness records, including your weight.
no_state   what did I weigh last week #4
    I do not have access to your health or personal records, so I don't know what your weight was last week.
no_state   what did I weigh last week #5
    I do not keep health or fitness records.
no_state   what did I weigh last week #6
    I do not have access to health or fitness records.
no_state   what did I weigh last week #7
    I don't have that information. I do not keep health or fitness records.
no_state   what did I weigh last week #8
    I do not have access to your health records.
no_state   what did I weigh last week #9
    I don't have access to health or fitness records, so I do not know what you weighed last week.
no_state   what did I weigh last week #10
    I do not have access to your health or fitness records, so I do not have that information.
```

### Bench, absent-data set (isolated E4B, HEAD prompt, no tool call in any run)

```
no_data_sleep     PASS "I don't have access to your health or sleep records."
no_data_sleep     PASS "I do not have access to your personal health or sleep data."
no_data_sleep     PASS "I don't have your health records, including sleep information."
no_data_sleep     PASS "I do not have access to your sleep or health data."
no_data_sleep     PASS "I don't have access to your sleep data. Kronk does not keep health or sleep records."
no_data_networth  PASS "I do not have access to your financial records, so I cannot tell you your net worth."
no_data_networth  PASS "I do not have access to your personal financial records, so I cannot provide your net worth."
no_data_networth  PASS "I do not have access to your financial records, so I cannot tell you your net worth."
no_data_networth  PASS "I don't have access to your personal financial information, so I cannot provide your net worth."
no_data_networth  PASS "I do not have access to your financial records, so I cannot tell you your net worth."
```

## Test count

`pytest --collect-only -q` with `tests/.venv`, in the M10 export and at HEAD:
1131 → 1009 collected. 1131 − 125 − 6 + 9 = 1009. Passed 1111 → 989 (the two
skips and 18 xfails are in both).

| File | M10 | HEAD |
| --- | --- | --- |
| `tests/test_bloodwork_parser.py` | 19 | deleted |
| `tests/test_health_agent.py` | 15 | deleted |
| `tests/test_health_rag.py` | 15 | deleted |
| `tests/test_finance_service.py` | 13 | deleted |
| `tests/test_positions_api.py` | 13 | deleted |
| `tests/test_positions_importer.py` | 29 | deleted |
| `tests/test_statement_pdf.py` | 11 | deleted |
| `tests/test_extractor.py` | 10 | deleted |
| `tests/test_agentic_loop.py` | 45 | 46 |
| `tests/test_coordinator_bench.py` | 17 | 19 |
| every other file | same | same |

The eight deleted files import only `health_service` and `finance_service`
modules (`bloodwork_parser`, `chunker`, `importer`, `statement_pdf`,
`extractor`, the two FastAPI apps). `test_health_agent.py`, despite its name,
has no agent-loop test. No generic coverage was in them.

Ids in M10 and not at HEAD, outside the deleted files (6, all in
`test_agentic_loop.py`), and my judgement on each:

| Dropped | Right to drop? |
| --- | --- |
| `test_execute_tool_query_health_no_data` | Yes. It tested the removed `query_health` handler's no-data string. Replaced by `test_execute_tool_shopping_list_empty` (an empty result from a surviving tool) and `test_execute_tool_removed_health_tool_is_unknown`. |
| `test_execute_tool_query_finances_no_documents` | Yes. Same, for `query_finances`. |
| `test_is_garmin_csv_detects_garmin_export` | Yes. `_is_garmin_csv` no longer exists. |
| `test_is_garmin_csv_rejects_generic_csv` | Yes. Same. |
| `test_is_garmin_csv_rejects_non_csv_extension` | Yes. Same. |
| `test_upload_garmin_csv_routes_to_health_service` | Yes. Replaced by `test_upload_garmin_shaped_csv_is_ordinary_context`, which asserts the opposite and that no HTTP call is made. |

Ids at HEAD and not in M10 (9): `test_health_and_finance_features_are_gone`,
`test_execute_tool_shopping_list_empty`,
`test_execute_tool_removed_health_tool_is_unknown`,
`test_upload_garmin_shaped_csv_is_ordinary_context`,
`test_upload_zip_is_refused_with_a_clear_reason`,
`test_upload_pdf_goes_to_file_contexts`, `test_removed_pages_return_404`,
`test_absent_data_checker`,
`test_scoreboard_adds_an_absent_data_section_only_when_those_runs_exist`.

Ported, not dropped (read in the diff): the five `run_stream` tests now use
`_loop_agent()`, a neutral `AgentConfig` with one non-terminal tool
(`get_kronk_context`), the shape the health agent had (one tool, default
round budget); the two pipeline tests route to `assistant`; the label test
uses `delegate_research`; `test_error_style.py` uses `AGENTS["assistant"]`.
Each keeps its assertions (token accumulation, tool-then-synthesis, error
surfacing with the agent's name, forced synthesis after the round budget,
failure labelled to the coordinator, trace marked ERROR). The count of
agent-loop cases did not drop.

`./scripts/run_tests.sh -rs -q`, last lines:

```
SKIPPED [1] tests/test_agentic_loop.py:956: KRONK_LIVE not set — skipping live integration
SKIPPED [1] tests/test_agentic_loop.py:963: KRONK_LIVE not set — skipping live integration
989 passed, 2 skipped, 18 xfailed, 3 warnings in 11.42s
```

The same in the M10 export: `1111 passed, 2 skipped, 18 xfailed, 14 warnings in 13.01s`.

## Dev path (criterion 22)

| Mount | Request | s | Route | Answer | Trace |
| --- | --- | --- | --- | --- | --- |
| `/dev/api/chat` | What is the capital of Australia? | 2.38 | `direct/default` | Canberra. | `410d7799aa29b337fdf60b84c9fbdbb5` |
| `/dev/api/chat` | What's the weather tomorrow? | 5.11 | `home/weather` | Tomorrow in Laurel, MD is expected to be sunny with a high near 61°F and a Northwest wind around 6 mph. | `d245048059f261b77953af87fcae0bf2` |
| `/dev/api/chat` | Search the web for the latest llama.cpp release. | 5.96 | `research/search_phrase` | Based on the web search, the latest release of llama.cpp appears to be **v1.0.0**, which was released on **November 13, 2026**. Key details from the s | `950a121ee7112b75bbd2bf2ff88770f5` |
| `/dev/api/chat` | play some jazz in the office | 1.82 | `home/music` | Dry run: would play Mushroom Jazz Radio, for jazz, on the Office speaker. Nothing was sent to the speaker. | `6431e9a2dea92ae6b2d2c1780e1f461f` |
| `/dev/voice/api/chat` | What is the capital of Australia? | 2.07 | `direct/default` | The capital of Australia is Canberra. | `c4358e481276afb3c4ae8da5fd005720` |
| `/dev/voice/api/chat` | What's the weather tomorrow? | 4.27 | `home/weather` | Tomorrow, Tuesday, will be sunny with a high of 61°F and a Northwest wind around 6 mph. | `6b719b6b5fedb5ed4752de1a80f00529` |
| `/dev/voice/api/chat` | Search the web for the latest llama.cpp release. | 6.52 | `research/search_phrase` | The latest llama.cpp release appears to be v1.0.0, which was released on November 13, 2026. Key information regarding this release includes: It repres | `15e2cd232a1fb61c29887f3b5e82383b` |
| `/dev/voice/api/chat` | play some jazz in the office | 2.6 | `home/music` | Dry run: would play Mushroom Jazz Radio, for jazz, on the Office speaker. Nothing was sent to the speaker. | `c4475dabb7c367f458dc171900c28b47` |

The two jazz requests: `home` agent, `play_music` called, `tool_service-dev`
`/health` → `{"status":"ok","music_dry_run":true}` before and after.

## Uploads (criterion 3, and the 422)

`POST /dev/files`, then each stored file removed with `DELETE /dev/files/<name>`:

| File | Result |
| --- | --- |
| `m1e2e_note.txt` (two lines of text) | 200 `{"name":"m1e2e_note.txt","tokens":12,"total_tokens":12,"warning":false}` |
| `m1e2e_garmin.csv` (the Garmin activity header and one row) | 200, `tokens: 31`, no `routed_to` |
| `m1e2e.pdf` (a one-page PDF with a text line) | 200, `tokens: 5` |
| `m1e2e.png` (1×1 PNG) | 422 `{"detail":"File must be UTF-8 text or a PDF"}` (same as M10) |
| `m1e2e.zip` (stored) | 422 `{"detail":"Zip archives aren't supported. Upload a UTF-8 text file or a PDF."}` |
| `m1e2e_deflate.zip` | 422, same detail |
| `m1e2e_renamed.txt` (the stored zip, renamed) | 422, same detail |

A malformed PDF of mine got 422 "Could not parse PDF: startxref not found",
which is the parser's own message and correct. The uploaded note reached the
model: `/dev/message` "What is the secret word in the uploaded note? One
word." → "pineapple" (the word in the note). `GET /dev/files` was empty
before and is empty after; production's `GET /files` was empty throughout.

## Deletion manifest (criterion 18)

Measured in this session (`du -sh`, summed file bytes, `find -type f | wc -l`):

| Path | du | Bytes | Files | Build report | Referenced at M10 by | At HEAD |
| --- | --- | --- | --- | --- | --- | --- |
| `data/health.db` | 972K | 995,328 | 1 | same | `health_service/db.py`, `vector_store.py`, two deleted test files | nothing |
| `data/chroma/` | 21M | 21,265,694 | 6 (1 subdir) | same | `health_service/vector_store.py` | nothing (one stale comment in `run_tests.sh`) |
| `data/finance/` | 236K | 227,392 | 4 (1 subdir) | same | `finance_service` (`db.py`, `positions_db.py`, `main.py`), compose bind `./data/finance:/data` | nothing |
| `data/retire/` | 16K | 12,288 | 1 | same | compose bind `./data/retire:/data` (`retire_calc`) | nothing |
| `data/withings_tokens.json` | 4K | 349 | 1 | same | `health_service/setup_withings_auth.py` | nothing |
| `data/garmin_jwt_web.txt` | 4K | 179 | 1 | same | no code at M10 or HEAD | nothing |
| `data/garmin_login_debug.png` | 884K | 901,885 | 1 | same | no code at M10 or HEAD | nothing |
| `secrets/garmin.json` ("Drew decides") | 4K | 85 | 1 | same | no code; compose mounts only `./secrets/mm` | nothing |

`data/finance/raw/` holds two PDFs (59,900 and 48,708 bytes; names not
recorded here). Total for the seven ordered paths: 17 files, 23,403,115 bytes.
Excluded and present: `data/postgres` (71M, 2,104 files, 0 changed during
this session) and `../retirement-calc` (121M). `data/kronk_state.md`: see O1.
`../kronk-releases/retirement-calc` is a symlink to the sibling repo, as the
report says.

Docker: `docker volume ls` shows no volume for the three services. Containers
`kronk-health_service-1` (`11e171df4f90`), `kronk-finance_service-1`
(`9923aa848c34`), `kronk-retire_calc-1` (`b47c91d305a3`), all mounting only
`./data`, `./data/finance`, `./data/retire` through the release's `data`
symlink. Images `kronk-health_service` (`2e6fca24937b`, 1.04 GB),
`kronk-finance_service` (`26069eb35ad5`, 359 MB), `kronk-retire_calc`
(`d8f44a4d42df`, 336 MB), `assistant-health_service` (`045afa7e710e`),
`assistant-finance_service` (`43a545a2702f`): all as listed. Missing from the
manifest: the two `assistant-garmin_setup*` images (O2).

Not deleted by me: anything.

## Voice replay (criterion 15)

`python3 scripts/voice_replay.py --label m1-e2e`, 03:12:07 to 03:16:23 UTC,
on `kronk-dev`, dry run on, no player active before the run:

```
voice_replay: {'pass': 90, 'fail': 2, 'xfail': 20, 'skip': 2}; executed 46, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_031207_m1-e2e.json
```

Exit status 1 (a row that should pass failed). Row by row against
`docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json` (114 rows, same
ids, same corpus hash `6e4c8bd21eb979fc`):

| Row | Baseline | This run | Explanation |
| --- | --- | --- | --- |
| `plan-orbital-station-youtube` | pass (`home:play_music`) | fail, no tool call (trace `bc18a678899a209c27cdb9e47a930ce1`) | Routed `home/music` by the regex pin, so the coordinator and its new sentence are not involved. The home agent answered in text ("If you paste a YouTube Music link, pass the link itself as the query. I can play "orbital radio station" for you, but I do not have a specific tool to launch or specify the YouTube Music platform …") instead of calling `play_music`. The home agent's prompt and tools are unchanged by M1. Rerun five times: 5 of 5 pass. Same class as M17 O4 (the home agent writing instead of calling). |
| `inc-0930-search-deep-house-ytm` | fail (`ask_research`) | fail (`ask_research`, trace `f8416d557c78931d0a3397bfe1c8bd99`) | Same status. The baseline's one failure. Reruns: pass, fail, fail, fail, pass. |
| `eval-played-the-album-toys-in-the-attic` | xfail, via `ask_home` → `play_music` | xfail, no tool call ("Understood.") | Same status, different path. An expected failure either way (the routing miss the corpus documents). Old against new coordinator prompt on the isolated server, 10 runs each: `ask_home` 6/10 before, 3/10 after; across six such sentences 21/60 before, 20/60 after (O4). |
| `eval-i-shuffled-my-youtube-playlist-old-man` | xfail, via `ask_home` → `play_music` | xfail, no tool call | Same status, different path. The M10 e2e run on pre-M1 code also had no tool call here. Isolated server: `ask_home` 1/10 before, 0/10 after. |

Every other row has the same status and the same route, rule and tool path as
the baseline. No executed row's answer mentions health, fitness or financial
records. The row the builder saw fail after M1
(`eval-look-for-some-good-deep-house-chill`) passed here and in all five
reruns.

Reruns (`--id` for the three rows, five times; result files kept in the
session scratchpad only, not committed):

| Run | `plan-orbital-station-youtube` | `inc-0930-search-deep-house-ytm` | `eval-look-for-some-good-deep-house-chill` |
| --- | --- | --- | --- |
| 1 | pass | pass | pass |
| 2 | pass | fail (`ask_research`) | pass |
| 3 | pass | fail (`ask_research`) | pass |
| 4 | pass | fail (`ask_research`) | pass |
| 5 | pass | pass | pass |

Players unchanged in the full run and in each rerun; all 17 `media_player`
entities have the same state, title, volume and `last_updated` at 22:24 as at
21:44.

## Coordinator bench (criterion 14)

Isolated E4B copy on :11497 as a transient user unit (`bench-e4b`), the
production unit's flags with only the port changed. Production :11438 was
not benched or touched. Before = the bench script and `agents.py` of the M10
export; after = HEAD.

| Run | File | Base | Hard, surviving probes | Delegation probes | Leaks |
| --- | --- | --- | --- | --- | --- |
| before | `docs/bench/coord_bench_2026-11-15_215321_m1-e2e-pre.{json,md}` | 26/28 | 16/21 | 34/36 | 0 |
| after | `docs/bench/coord_bench_2026-11-15_215619_m1-e2e-post.{json,md}` | 27/28 | 14/21 | 33/36 | 0 |

Per probe, before → after: weather_delegate 5/5 → 5/5, shopping_delegate 3/3
→ 3/3, no_spurious 4/5 → 5/5, news_brief_terminal 3/3 → 3/3,
news_refresh_flag 3/3 → 3/3, knowledge_prose 2/2 → 2/2, markdown_list 1/2 →
1/2, composite_solar 5/5 → 5/5, officeholder_delegate 3/3 → 3/3,
mortgage_rate_research 3/3 → 3/3, weather_by_riddle 3/3 → 3/3, date_math 1/3
→ 1/3, clock_math 3/3 → 3/3, exact_words 1/3 → 1/3, composite_hottub 2/3 →
0/3, multi_domain 3/3 → removed, no_data_sleep 5/5, no_data_networth 5/5.

Follow-up samples (scratchpad scripts driving the bench module's own
`run_composite` and `chat`; not committed): `composite_hottub` 20 runs: before
7/20; after 10/20 and 5/20. Single-turn delegation, 5 runs each of eight
prompts: 40/40 before, 40/40 after. See O4.

The builder's three files, rescored from their JSON: 33/36, 34/36, 34/36 on
the delegation probes, zero leaks; the final file's system prompt equals
HEAD's `COORDINATOR.system_prompt`. As reported.

## Promotion commands: my view

Read as if I had to run them, and dry-run where possible.

Dry run. I exported HEAD with `git archive` into a scratch release directory,
linked `data`, `secrets`, `.env`, `.env.dev`, `searxng` and `ops` as
`deploy_tag.sh` does, and ran what the script's `plan` runs: `docker compose
-p kronk --project-directory <release> -f <release>/docker-compose.yml up -d
--no-build --dry-run`. Result:

```
level=warning msg="Found orphan containers (kronk-health_service-1, kronk-finance_service-1, kronk-retire_calc-1) for this project. …
 Container wyoming-piper Running
 Container kronk-litellm-1 Recreate
 Container kronk-voice_tracer-1 Recreate
 Container kronk-searxng-1 Recreate
 Container kronk-tool_service-1 Recreate
 Container kronk-orchestrator-1 Recreate
 Container kronk-nginx-1 Recreate
```

The three removed services are reported as orphans and left alone. No dev
container, no Home Assistant, no Music Assistant is named. Container ids
were identical before and after the dry run. No tag was created and
`deploy_tag.sh plan` was not run (it needs a tag).

Checks asked for:

- Order: deploy first, then remove containers. Correct, and necessary. The
  deploy recreates nginx with the new config while `health_service` still
  resolves; the old config would fail `[emerg] host not found in upstream
  "health_service"` on the next nginx start once the containers are gone
  (shown above with the M10 file). Step 3 then restarts nginx without the
  three containers, which proves the point.
- Containers are named explicitly: `docker rm -f kronk-health_service-1
  kronk-finance_service-1 kronk-retire_calc-1`. Names match the running
  containers.
- No `--remove-orphans`, no `down`, no `-v` anywhere in the commands or in
  `deploy_tag.sh`.
- Home Assistant and Music Assistant: not named; `deploy_tag.sh` refuses a
  release that defines either, and HEAD's compose file defines neither.
- `nginx -t` with the new config in a throwaway `nginx:1.29-alpine`: passes
  (criterion 6).
- Data `rm` lines name exactly the seven manifest paths, and nothing under
  `data/postgres` or `../retirement-calc`. `rm ../kronk-releases/retirement-calc`
  removes a symlink (verified it is one); without a trailing slash, as written.
- The post-promotion checks are runnable as written; the `grep -c 'No such
  file'` count of 7 matches seven paths.

Verdict on the commands: safe to run as written. Changes I recommend:

1. Make the `kronk_state.md` edit (step 6, second half) a required step, with
   Drew's yes obtained before promotion. Without it the chat UI answers "how
   did I sleep" and "what did I weigh" from the state file's claims about
   half the time (O1). Add a post check through `/message`, since the shim
   checks in the report do not load the state file.
2. Tell whoever has the chat page open to reload it after the deploy (O3).
3. Add `assistant-garmin_setup_playwright:latest` and
   `assistant-garmin_setup:latest` to the optional image removal (O2).
4. Run step 4 only after `pwd` shows the repo root; the paths are relative.
   Step 0's snapshot files go to `/tmp`, which is fine for a one-off.
5. Expect a short production outage during step 1: the deploy recreates
   `kronk-litellm-1`, `kronk-tool_service-1`, `kronk-orchestrator-1`,
   `kronk-searxng-1`, `kronk-voice_tracer-1` and `kronk-nginx-1` (voice and
   chat are down for the rebuild-and-start window; Piper keeps running).
6. The rollback note is right: after step 5 a rollback to `sprint-M17` needs
   the `retirement-calc` link recreated by hand, and the services come back
   empty after step 4.

## What this session did to the box, and confirmation it was put back

Temporary changes, all reverted and confirmed:

| Change | Reverted | Confirmation |
| --- | --- | --- |
| Three uploads held in `orchestrator-dev`'s memory (`m1e2e_note.txt`, `m1e2e_garmin.csv`, `m1e2e.pdf`) | `DELETE /dev/files/<name>` for each | `GET /dev/files` → `{"files":[],"total_tokens":0,"warning":false}` at 22:24 |
| Transient user unit `bench-e4b` (a second E4B server on 127.0.0.1:11497), started and stopped four times | `systemctl --user stop bench-e4b` each time | unit `inactive`, nothing listening on :11497, no `bench-*` unit loaded; `llama-gemma4-e4b` active throughout |
| Four throwaway `nginx:1.29-alpine` containers for `nginx -t` (`--rm`) | removed on exit | `docker ps -a` shows the same 19 containers as at the start |

Not reverted, because they are not test state to restore:

- `data/dev/sessions.db` gained the turns of one `/dev/message` request
  (session `m1-e2e`); `data/dev/metrics.db` gained rows for my dev requests.
  Dev state only.
- About 150 requests on the dev shims, one full replay and five three-row
  reruns through HA's `assist_pipeline/run` on `kronk-dev` (intent stage
  only, dry run on). HA keeps 10 debug runs per pipeline, so older
  `kronk-dev` debug runs rotated out.
- New files in the repo, committed with this report:
  `docs/bench/voice_replay_2026-11-16_031207_m1-e2e.json` and the two bench
  results `docs/bench/coord_bench_2026-11-15_215321_m1-e2e-pre.{json,md}`,
  `docs/bench/coord_bench_2026-11-15_215619_m1-e2e-post.{json,md}` (the
  brief named only the replay file; the bench files are the evidence for
  criterion 14).
- Read-only requests to production: `GET /health` (200, the old page),
  `/api/status`, `/files`, and six `/probe/*` URLs. No production chat
  request; production `metrics.db` and `sessions.db` are unchanged.
- The session scratchpad (outside the repo) still holds the M10 export, a
  scratch release directory whose `data`, `secrets`, `.env`, `.env.dev`,
  `searxng` and `ops` entries are symlinks into the repo, and my scripts and
  raw result files. A removal of mine there was stopped by the harness's
  safety check, so I left it; deleting that directory removes only links and
  copies.

Not done, as instructed: no repo code, corpus, compose, nginx or HA
configuration change; nothing deleted under `./data` or `./secrets`; no sound
(dry run stayed on; no player was started, paused, stopped or skipped); no
restart of Home Assistant, Music Assistant or any production container; the
dev pair was not rebuilt or restarted (it already ran HEAD); no production
deploy; no tag, no push; `pipeline_bench.sh` not run; `.env` and `.env.dev`
not edited (`.env.dev` md5 `3070faa4…`, mtime 19:58, before this session);
nothing on the MagicMirror Pi; no Music Assistant setting changed; no
firmware change; no LAN scan.

Final state at 22:24 EST: 19 containers with the ids and Created times they
had at 21:41, 0 restarts; dev pair healthy, `music_dry_run: true`; all 17
`media_player` entities as at the start; `git status` clean apart from this
report and the five result files.
