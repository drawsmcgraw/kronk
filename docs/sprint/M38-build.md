# M38 build report: the status probe stops running a completion on every model server

Build agent, 2026-11-27. Specification: `M38-criteria.md` (plumbing 1 to
4, criteria 1 to 5; criterion 6 is the coordinator's at promotion).

## What changed

- `orchestrator/main.py` `health_probe` (`GET /api/status`):
  - LiteLLM: `GET /health/liveliness` in place of `GET /health`
    (plumbing 1). The answer (`"I'm alive!"`) is not kept; `llm_service.
    litellm` is `{"up": true, "reason": null}`, or `up: false` with the
    error.
  - Model servers: `servers.model_server_health(servers.load_catalog(),
    servers.llama_props_source())`, from `llama_props.json` with its age
    (plumbing 2). No request to a model server.
  - tool_service: `GET /health`, as before; its JSON is `tool_service`,
    so `music_dry_run` is where callers read it (plumbing 3).
  - 200 with `status: "ok"` when all are up. Otherwise 503 with
    `status: "down"`, `detail` naming each piece that is not up and why
    (`model server talkie: not answering: port 11441 is not in
    llama_props.json (written 20 s ago)`), and the same `llm_service` and
    `tool_service` keys (`tool_service` null when it did not answer). The
    old 503 body was FastAPI's `{"detail": ...}`; `detail` is kept.
  - The three checks no longer short-circuit: a LiteLLM failure still
    reports tool_service. Both HTTP calls now `raise_for_status()`; the
    old code read the JSON of a 5xx as success if it parsed.
- `orchestrator/servers.py`: `_health(port, props, pentry)` is the one
  definition of a catalog server's health (answering, not answering,
  unknown), moved out of `build_servers` unchanged, so `/api/servers` and
  `/api/status` cannot disagree. New `model_server_health(catalog,
  props)` builds `llm_service`: `servers` (per catalog model: `name`,
  `port`, `health`, `up`, `reason`), `source` (the file's `file`,
  `state`, `age_s`, `max_age_s`, `every_s`, `reason`), `healthy_count`,
  `unhealthy_count`, `down`. An empty catalog is a reason in `down`.
  `public()` became module-level `_public()`, same body.
- `orchestrator/static/index.html`: the dot's comment only (what 200
  means now, and that the poll asks no model). No script change.
- `tests/test_m38_status_probe.py` (new, 10 tests): criterion 1. An
  `httpx.MockTransport` records every request the route makes; no socket
  is opened (`no_network`). All up: 200, exactly two requests
  (`/health/liveliness`, tool_service `/health`), no model port, no
  `/v1/`, every key callers read. A server missing from the file, a
  server with an error, a stale file (both names), a missing file, an
  empty catalog, LiteLLM 500, tool_service refused, tool_service 500:
  each 503 with the name in `detail`. One test checks `/api/servers` and
  `/api/status` give the same health and reason per server. Against
  HEAD's `main.py` 9 of the 10 fail (the tenth tests `servers.py` only).
- `docs/features/resources-live-memory.md`: new section "`/api/status`
  reads the same file (M38)" with what `llm_service` now holds (table)
  and the 503 body; the gotcha "`/services`' orchestrator dot still
  costs completions" replaced by "A model server that is down turns
  `/api/status` 503"; a header line; a blog hook.
- `docs/bench/voice_replay_2026-11-27_200300_replay.json`: the
  criterion 4 `--check-only` run.

## Criterion 2: before and after, on dev

Home route through `/dev/api/chat`: the Boston follow-up (user "what's the
weather in Boston", the assistant's answer, user "what's the weather there
tonight"), three requests, ten `GET /dev/api/status`, a fourth request,
all in one clock minute. Figures are each request's first round
(`plan_1`) from the dev orchestrator's `agent_round` events: cached of
prompt tokens. Script:
`/tmp/claude-1000/-home-drew-git-repos-drawsmcgraw-kronk/5b510aca-6311-49fa-98b1-9dd9a79be237/scratchpad/crit2.py`
(scratch, not kept).

The home prompt changes on two clocks, not one: the time of day (to the
minute, token 893, M37's subject) and the weather block's "fetched N
minutes ago" (`agents.weather_context`, `age_s // 60`), which ticks on
the weather cache's own phase. My first before run lost the third
request's cache to that tick (cached 1,086, 14:53:13). The script then
starts only when neither ticks in the next 40 s. Every run below is
inside one minute and one weather minute.

Before (HEAD's image, 14:55:09 to 14:55:39):

| Request | 1 | 2 | 3 | 4 (after ten probes) |
| :-- | --: | --: | --: | --: |
| cached / prompt | 893 / 4,073 | 4,072 / 4,073 | 4,072 / 4,073 | **0 / 4,073** |
| first round | 2.73 s | 2.21 s | 1.85 s | 2.98 s |

The ten status calls took 0.407 to 0.678 s each. (The first before try,
14:53:00 to 14:53:33: requests 893, 4,072, 1,086 (weather tick), then
**0** of 4,073.)

After (this build on the dev pair):

| Run | Window | 1 | 2 | 3 | 4 (after ten probes) | 4's first round |
| :-- | :-- | --: | --: | --: | --: | --: |
| 1 | 14:58:09 to 14:58:30 | 893 / 4,073 | 4,072 | 4,072 | **4,072 / 4,073** | 2.04 s |
| 2 | 14:59:09 to 14:59:31 | 893 / 4,074 | 4,073 | 4,073 | **4,073 / 4,074** | 1.55 s |
| 3 | 15:00:09 to 15:00:32 | 890 / 4,074 | 4,073 | 4,073 | **4,073 / 4,074** | 1.94 s |
| 4 | 15:01:09 to 15:01:29 | 893 / 4,074 | 4,073 | 4,073 | **4,073 / 4,074** | 2.85 s |
| 5 | 15:02:09 to 15:02:34 | 893 / 4,074 | 4,073 | 4,073 | **4,073 / 4,074** | 3.23 s |

Every status call answered 200 in 0.009 to 0.015 s (50 calls). Request
1 of each run caches about 893: the minute changed since the run before
(M37). Criterion 2 passes: the fourth cached at least 4,000 in five of
five.

## Criterion 3: the two llama journals

Ten `GET /dev/api/status` from 15:02:47, journals read to 15:02:49:

```
== llama-gemma4-e4b: 0 lines
== llama-talkie: 0 lines
```

Across the five after-runs (14:57:00 to 15:02:50, 50 status calls)
`llama-talkie` logged 0 `launch_slot_` lines. In the before run's probe
window (14:55:20 to 14:55:34) it logged 20: ten tasks, one per probe.
Passes.

## Criterion 4: the callers

- **`voice_replay.py --check-only`**: `voice_replay: pipeline kronk-dev →
  http://localhost/dev/voice, dry-run on, 166 rows (0 not selected)`;
  `tool_service_health {'status': 'ok', 'music_dry_run': True}` read
  from `http://localhost/dev/api/status`. Counts: pass 85, skip 78,
  xfail 1, xpass 1, fail 1; executed 0. The fail is
  `inc-1119-get-a-grit-aerosmith-basement`, the same row `M36-promote.md`
  explains (HA's sentence-trigger reading; Kronk not reached). I ran it
  twice by mistake; the second file was a duplicate and is deleted.
- **`m9_lock_bench.py`** (by reading, `require_dry_run`): it wants code
  200 and `tool_service.music_dry_run is True`. Both are as before.
- **The chat page's dot** (`index.html` `checkHealth`) reads `r.ok`
  only. **`/dev/services`' Chat card** (`KronkUI.checkDots`) reads
  `r.ok` with a 3 s timeout. `/dev/api/status` answers 200 in about
  0.01 s.
- **Finding: on dev, neither of those two reaches dev's route.** The dot
  fetches the absolute `/api/status` (confirmed in the HTML `/dev/`
  serves, line 559), and `/probe/orchestrator` is nginx's proxy to
  production's orchestrator. Both read production's `/api/status`, so
  they cannot show this change until it is deployed. I checked them by
  reading and by dev's status code; I did not open either page, since
  each poll would have sent a completion through production's route
  (the brief leaves production's `/api/status` to the coordinator).
  Criterion 6 at promotion is where they are seen live. Making the dot's
  URL relative is not in this item; I did not change it.

## Criterion 5: the suite

```
FAILED tests/test_m32_step2_fix1.py::test_every_other_shipped_entry_links_what_its_lead_names
1 failed, 8591 passed, 3 skipped, 62 xfailed, 6 warnings in 218.53s (0:03:38)
```

The failure is not this change. It fails the same way on a clean
worktree of HEAD `28887d0` (`1 failed, 10 passed, 1 skipped in 0.44s`
for that file): the test pins ROADMAP's Shipped entries and does not
know the two added by `db55bca` and `28887d0`:

```
E         {'A follow-up can refer to the turn before': ['M36'],
E          '`/work` for a wall display': ['M32']}
```

That test and ROADMAP are the coordinator's; I did not touch them.
Everything else passes, `tests/test_m38_status_probe.py` included.

## Decisions and things for the coordinator

1. **A down model server now gives 503; before, it gave 200.** Plumbing 3
   says the code "stays" 200/503, but LiteLLM's `/health` answered 200
   with a dead server under `unhealthy_endpoints` (`M31-e2e.md` point
   2), so the old route said 200 unless LiteLLM itself failed. I built
   what plumbing 3 orders. The effect: with Talkie stopped, or the props
   timer stopped for more than 300 s, the chat dot goes red,
   `voice_replay.py` and `m9_lock_bench.py` refuse, and `deploy_tag.sh`'s
   check fails. A ruling is needed only if Talkie is meant to be stopped
   at times.
2. **Health is as old as the file**: up to 60 s (the props timer) behind
   a server that stops; the route used to see it at once. Stale past
   300 s is unknown and 503.
3. **Stale or missing file is 503**, both servers named (criterion 1).
4. `healthy_count` and `unhealthy_count` are kept (they were in
   LiteLLM's answer and in `M31-e2e.md`), now counted from the file;
   unknown counts as unhealthy. `healthy_endpoints` and
   `unhealthy_endpoints` are gone: no caller reads them.
5. `/api/servers` is unchanged: same health rule, now in `_health`.
6. The weather block's minute (above) moves the home prompt once a
   minute on its own phase, as the clock does. M37 moves only the clock.
   Not this item; noted for M37's criterion 2, which can lose a pair to
   it.
7. While the replay ran, `media_player.upper_shield` and
   `media_player.shield` were `playing` (the household's). The replay
   executed nothing; the weather requests play nothing.

Production not touched: no deploy, no tag, no production request. The
dev pair was rebuilt with `docker compose --profile dev up -d --build
tool_service-dev orchestrator-dev`; `tool_service-dev` was not
recreated.
The dev image was built before one edit: the module docstring of
`servers.py` (comment only). The running code is the committed code.
