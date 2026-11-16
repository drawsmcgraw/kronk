# M1b restore build report: the hot tub feature stays

Build agent, 2026-11-16 07:30 to 07:50 EST, branch `refactor`. Criteria:
`docs/sprint/M1b-restore-criteria.md`. Drew reversed M1b on 2026-11-16 (plan,
"Decisions by Drew, 2026-11-16").

Commits, in order:

| Commit | What |
| --- | --- |
| `ea0db68` | Revert `faf37aa` (coordinator "can't check the hot tub" sentence and its test). The three prompt-trial files `faf37aa` added are kept. |
| `768d9ea` | Revert `489e306` (docs). Clean. |
| `91f9ab3` | Revert `855a9d9` (`hottub_monitor/monitor.py`). Clean. |
| `89be6b4` | Revert `0583239` (tool, route, benches, tests). One conflict, `orchestrator/agents.py`. |
| `2b90b1b` | Reference copy of `kronk-hottub-monitor.service` in `systemd/`. |
| `6388c56` | `docker-compose.yml`: `tool_service-dev` mounts `./data/hottub` read-only (dev only). |
| `d49328a` | Docs: HOTTUB-01, README, HISTORY, plan status note, investigation note. |
| this commit | This report and the replay result file. |

## Summary

- The feature is back as it was at `sprint-M1`. Every hot tub file is
  byte-identical to the tag; `orchestrator/agents.py` differs only by M4's
  changes (one of them on the home agent's description line, merged).
- M3 and M4: `tool_service/ops.py`, `tests/test_ops_exec.py`,
  `tests/test_agent_tool_separation.py` and `ops/hosts.example.json` have the
  same sha256 before and after. The devops entry, the loop's `offered` check
  and both M4 description changes are present unchanged.
- Monitor: virtualenv restored from the tarball, `monitor.py` identical to
  the tracked file, unit enabled and running since 07:33:41 with 0 restarts.
  It reconnected in 6 s (`HOT TUB BACK ONLINE — 101.0°F (set: 101.0°F)`) and
  wrote the status file at 07:33:47, 07:38:47 and 07:43:47.
- Dev: "is the hot tub online" 10/10 (5 per mount) called `query_hottub` and
  answered 101°F from the file. "What temperature should a hot tub be kept
  at" 10/10 answered normally.
- Tests: `1412 passed, 2 skipped, 18 xfailed` before, `1409 passed,
  2 skipped, 18 xfailed` after: M1b's four negative tests out, the probe
  checker back.
- Voice replay on dev: 91 pass / 20 xfail / 1 fail / 2 skip, the committed
  M10 baseline's totals, every row with the baseline's status.
- Production: untouched, still `sprint-M1b`. It needs a tag deploy and
  nothing else at promotion.

## Reverts and conflicts

`git revert` of the four M1b code commits, newest first, one commit each.

- `faf37aa`: applied cleanly. The revert also deleted
  `docs/bench/coord_prompt_trial_2026-11-15_m1b-v{0,1,2}.json`; I restored
  them in the same commit, because `docs/sprint/M1b-build.md` cites them and
  the M1b reports stay as history. Net change: the coordinator sentence
  ("Kronk has no connection to the hot tub or spa: …") and the five lines of
  its test out.
- `489e306`: clean. It brought back HOTTUB-01 as written on 2026-06-12
  ("non-functional") and removed the HISTORY section; both were wrong after
  the revert and are corrected in `d49328a` (below).
- `855a9d9`: clean. `hottub_monitor/monitor.py` is back.
- `0583239`: one conflict, `orchestrator/agents.py`, the home agent's
  `description` / `routing_hint` lines:

  ```
  <<<<<<< HEAD (M4, b4bddd7)
  description="Weather lookups, shopping list management, solar panel health and energy production (kWh), playing music and controlling playback (stop, pause, resume, skip, volume), and updating or upgrading the magic mirror",
  routing_hint="weather, forecast, shopping list, solar, solar panels, … updating the magic mirror",
  =======  (sprint-M1)
  description="Weather lookups, shopping list management, hot tub status, solar panel health … and updating the magic mirror",
  routing_hint="weather, forecast, shopping list, hot tub, spa, solar, solar panels, … updating the magic mirror",
  >>>>>>>
  ```

  Resolved by keeping both: the `sprint-M1` text with "hot tub status" and
  "hot tub, spa", plus M4's "updating or upgrading the magic mirror". The
  routing hint was the same on both sides apart from "hot tub, spa".

The first revert auto-committed with git's default message; I amended that
commit (local, never pushed) to restore the trial files and add the message
and trailer. The other three used `--no-commit`.

## Diff against `sprint-M1`, hot tub pieces (criterion 1)

`git diff sprint-M1 HEAD -- <file>`, changed lines:

| File | Changed lines vs `sprint-M1` | Note |
| --- | --- | --- |
| `orchestrator/tools.py` (`query_hottub` definition, `_tool_query_hottub`, `_HANDLERS`, `TOOL_TIMEOUTS`) | 0 | |
| `tool_service/main.py` (`GET /hottub`, `HOTTUB_STATUS_FILE`) | 0 | |
| `hottub_monitor/monitor.py` | 0 | sha256 `4bd748c9…dd509`, same as `main` |
| `scripts/coordinator_model_bench.py` (`composite_hottub`, `_hottub_cost`; `no_device_hottub` gone) | 0 | |
| `scripts/routing_bench.py` (home lane "the hot tub") | 0 | |
| `docs/bench/routing_eval.jsonl` ("Is the hot tub online?" row) | 0 | 491 lines again |
| `tests/test_agentic_loop.py`, `tests/test_coordinator_bench.py`, `tests/test_error_surfacing.py` | 0 | |
| `tests/test_tool_service_routes.py` | file absent, as at `sprint-M1` | M1b created it for one negative test |
| `orchestrator/agents.py` | M4 only | see below |

`orchestrator/agents.py` against `sprint-M1` is exactly the M4 diff that was
on the branch before the restore (`git diff sprint-M1b 97dfdc4 --
orchestrator/agents.py`): the home description's "updating or upgrading",
the devops description and comment, devops `tool_names=["remote_exec"]`
and its comment, the removed devops web sentence, and the `offered` /
`tool_rejected` loop check. The hot tub wording, `query_hottub` in
`tool_names`, and the two `query_hottub` sentences in the home system prompt
are as at `sprint-M1`. Criterion 2: the coordinator sentence is gone.

Whole tree after the four reverts, `git diff --stat sprint-M1 89be6b4`
outside `docs/sprint/` and `docs/bench/`: the same ten files and line counts
as `git diff --stat sprint-M1b 97dfdc4` (M3, M4 and sprint notes).

## Nothing from M1 back, nothing from M3/M4 lost (criterion 3)

- `git ls-files | grep -iE 'health|financ|retire|garmin|withings'`: only
  `docs/history/FINANCIAL_EXPERT_PLAN.md`, which M1 moved there and which is
  present at `sprint-M1b` too. No health, finance or retirement code, test or
  route came back (none of the four M1b commits touched them).
- sha256 before and after, all `OK`:

  ```
  558091375502db4bc44f633f1ca517d10edf158a6b3ceec94554bc5cf63692e4  tool_service/ops.py
  bcd5779c6150e20de80c1610f172b983a92cc43e9f5cf7524a2ef9e72608d910  tests/test_ops_exec.py
  ebcc26b84ca2d57fdbf59bb0621c7ab4e7da10c1b581fb57082a2dc4ab52f8fb  tests/test_agent_tool_separation.py
  3513491be07a8e127aba65053965e4b61619036a027b4535c82fae3b5c61d2d3  ops/hosts.example.json
  ```

- `orchestrator/agents.py`: devops `tool_names`, the `offered` check and
  both M4 description lines byte-identical to `97dfdc4` (the merged home
  description line contains M4's text unchanged).

## Monitor restore (criterion 4)

Before: unit `disabled`, `inactive (dead)`; unit file 375 B, mtime
2026-05-01; `hottub_monitor/` absent; host `/usr/bin/python3` → 3.13.7.

Tarball `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz`: 5,340,012 B,
sha256 `49a0d5e0…6316e77a` (the build report's), `gzip -t` clean. Listed
before extracting: 1,518 entries, all under `hottub_monitor/`, no absolute
or `..` paths, four symlinks (`.venv/bin/python` → `python3`,
`python3.13` → `python3`, `python3` → `/usr/bin/python3`, `lib64` → `lib`).

Extracted only the virtualenv, at the repo root, never overwriting:

```
tar -xzf ~/backups/hottub/hottub_monitor-2026-11-15.tar.gz \
    -C /home/drew/git-repos/drawsmcgraw/kronk --keep-old-files hottub_monitor/.venv
```

Result: 1,514 paths, 21,584,436 B (the build report's size), `pyvenv.cfg`
`version = 3.13.7`, `geckolib 1.0.15` importable. Ignored by `.gitignore:20`
(`.venv/`), so untracked as before; `git status` clean. `monitor.py`
(tracked, from the revert), the tarball's copy and `sprint-M1:` all hash to
`4bd748c93c060344df6cfcacd3dfe9bd6a05a7174591dc7c9c885ae84a5dd509`. The
tarball's `__pycache__/` was not extracted (regenerated by Python). The
tarball is still in place, sha256 unchanged.

Unit:

```
$ systemctl --user enable --now kronk-hottub-monitor.service
Created symlink '/home/drew/.config/systemd/user/default.target.wants/kronk-hottub-monitor.service' → '/home/drew/.config/systemd/user/kronk-hottub-monitor.service'.
```

State at 07:44:18, 10 min 37 s after start:

```
MainPID=1365776
NRestarts=0
ActiveState=active
SubState=running
UnitFileState=enabled
ActiveEnterTimestamp=Mon 2026-11-16 07:33:41 EST
```

Journal for the whole window (`journalctl --user -u kronk-hottub-monitor
--since 07:33:00`, 18 lines, verbatim):

```
2026-11-16T07:33:41-05:00 kronk systemd[2731]: Started kronk-hottub-monitor.service - Kronk hot tub monitor (Gecko in-touch via geckolib).
2026-11-16T07:33:41-05:00 kronk python[1365776]: 2026-11-16 07:33:41,335 INFO Kronk hot tub monitor starting — 192.168.1.87, status updates every 300s, retry after connect failure: 300s, retry after disconnect: 60s
2026-11-16T07:33:41-05:00 kronk python[1365776]: 2026-11-16 07:33:41,335 INFO Attempting connection to spa pack at 192.168.1.87...
2026-11-16T07:33:47-05:00 kronk python[1365776]: 2026-11-16 07:33:47,355 INFO HOT TUB BACK ONLINE — 101.0°F (set: 101.0°F)
2026-11-16T07:36:07-05:00 kronk python[1365776]: 2026-11-16 07:36:07,110 INFO Status block changed value of CheckFlo from False to True
2026-11-16T07:36:07-05:00 kronk python[1365776]: 2026-11-16 07:36:07,266 INFO Status block changed value of RhWaterTemp from 100.6 to 100.4
2026-11-16T07:37:48-05:00 kronk python[1365776]: 2026-11-16 07:37:48,256 INFO Status block changed value of inFloRatio from 18 to 17
2026-11-16T07:38:14-05:00 kronk python[1365776]: 2026-11-16 07:38:14,738 INFO Status block changed value of inFloRatio from 17 to 18
2026-11-16T07:38:14-05:00 kronk python[1365776]: 2026-11-16 07:38:14,908 INFO Status block changed value of MSTR_HEATER from OFF to ON
2026-11-16T07:38:14-05:00 kronk python[1365776]: 2026-11-16 07:38:14,908 INFO Status block changed value of Heating from  to Heating
2026-11-16T07:38:14-05:00 kronk python[1365776]: 2026-11-16 07:38:14,908 INFO Status block changed value of CheckFlo from True to False
2026-11-16T07:38:14-05:00 kronk python[1365776]: 2026-11-16 07:38:14,908 INFO Status block changed value of RhFloDetected from False to True
2026-11-16T07:38:18-05:00 kronk python[1365776]: 2026-11-16 07:38:18,210 INFO Status block changed value of SideHeatingDegG from 0 to 1
2026-11-16T07:38:20-05:00 kronk python[1365776]: 2026-11-16 07:38:20,211 INFO Status block changed value of SideHeatingDegG from 1 to 2
2026-11-16T07:38:23-05:00 kronk python[1365776]: 2026-11-16 07:38:23,210 INFO Status block changed value of SideHeatingDegG from 2 to 3
2026-11-16T07:38:24-05:00 kronk python[1365776]: 2026-11-16 07:38:24,208 INFO Status block changed value of SideHeatingDegG from 3 to 4
2026-11-16T07:38:30-05:00 kronk python[1365776]: 2026-11-16 07:38:30,214 INFO Status block changed value of SideHeatingDegG from 4 to 5
2026-11-16T07:38:32-05:00 kronk python[1365776]: 2026-11-16 07:38:32,213 INFO Status block changed value of SideHeatingDegG from 5 to 6
```

One connection, no retry, no error, no "No response". The status-file
writes every 300 s are silent by design; their times come from the file.
The heater came on at 07:38:14.

`data/hottub/status.json` before (written by the monitor before the
2026-11-15 stop, mtime 22:30:36 EST, 296 B):

```json
{
  "online": true,
  "spa_name": "68:27:19:be:cd:08",
  "spa_ip": "192.168.1.87",
  "temperature_f": 101.0,
  "set_temperature_f": 101.0,
  "last_seen": "2026-11-16T03:30:36.327993+00:00",
  "last_check": "2026-11-16T03:30:36.328019+00:00",
  "offline_since": null,
  "consecutive_failures": 0
}
```

After (mtime 07:43:47 EST, 296 B; `last_check` is 10 min after the 07:33:41
start):

```json
{
  "online": true,
  "spa_name": "68:27:19:be:cd:08",
  "spa_ip": "192.168.1.87",
  "temperature_f": 101.0,
  "set_temperature_f": 101.0,
  "last_seen": "2026-11-16T12:43:47.362446+00:00",
  "last_check": "2026-11-16T12:43:47.362470+00:00",
  "offline_since": null,
  "consecutive_failures": 0
}
```

The first write after the restart was at 12:33:47Z (read at 07:34).

## Reference unit copy (criterion 5)

`systemd/kronk-hottub-monitor.service` is a byte copy of the live unit
(`cmp` clean), matching how every other unit in `systemd/` is kept (all 14
with a live counterpart are byte-identical). Same text as the M1b build
report's manifest.

## Docs (criterion 6)

- `TECH_DEBT.md` HOTTUB-01: moved from under "Considered and rejected" to
  "Open Issues" and rewritten. It says the monitor works (spa unreachable
  until 2026-10-24 13:50, connected since) and lists what is open: no
  alerting; stale readings served as current (`/hottub` returns the file
  as is, nothing checks `last_check` against the clock); the virtualenv is
  untracked with no requirements file and depends on host Python 3.13.
- `README.md`: the hot tub is back in the summary, the `tool_service` row,
  the `home` row and the tool list (from the revert); new service-map row
  for the `kronk-hottub-monitor` host unit; "Last updated" notes the
  reversal.
- `docs/HISTORY.md`: one section, "Hot tub monitor (removed 2026-11-15,
  restored 2026-11-16)", replacing the removal section the revert took out.
- Plan: the M1b section opens with a status note ("reversed by Drew,
  2026-11-16 … kept as written"); the status table row says the same.
- `docs/incidents/INVESTIGATION_2026-11-16_hottub_removal.md`: timeline
  from the 2026-06-12 entry to the restore, root cause (a runtime-state
  claim with no verification date, no recovery signal, a plan premise taken
  from the doc and a check-first pass that sized the deletion instead of
  testing the premise), and what would have caught it.
- Kept as history, unedited: `docs/sprint/M1b-criteria.md`, `M1b-build.md`,
  `M1b-e2e.md`, the M1b bench and replay files, `PROMOTIONS.md`.

## Dev path (criteria 7 and 8)

How dev sees the status file: `tool_service-dev` mounts `./data/dev` at
`/data` and production's `./data` read-only at `/shared`, but
`tool_service` reads the hot tub file at the fixed path
`/data/hottub/status.json` (no env override; criterion 1 keeps that code as
at `sprint-M1`). So dev answered "no status file" until `6388c56` added a
read-only mount `./data/hottub:/data/hottub:ro` to `tool_service-dev`
only. Every non-dev service's resolved `docker compose config` is identical
before and after (hashed per service). Side effect: Docker created the
empty mountpoint `data/dev/hottub/` (root-owned).

Rebuilt with
`docker compose --profile dev up -d --build --no-deps tool_service-dev orchestrator-dev`
(`kronk-tool_service-dev-1` `f73a2d553a97` → `b47515fdb02e`,
`kronk-orchestrator-dev-1` `fe1a2edd0fe4` → `e44b26dc80fe`). md5 of
`orchestrator/tools.py`, `orchestrator/agents.py`, `tool_service/main.py`
equal to the files in the containers. `GET localhost:8103/hottub` returned
the live file (`last_check` 12:33:47Z); `/health` →
`{"status":"ok","music_dry_run":true}`. nginx resolves `orchestrator-dev`
per request, so it was not restarted.

"is the hot tub online", 10 runs, verbatim, all HTTP 200. Every run in
orchestrator-dev's log: `route=direct rule=default`, coordinator `ask_home`,
home `query_hottub`, `tool_complete`. The file said 101.0°F, set 101.0°F.

```
07:35:35 /dev/api/chat        1  5.8s | Yes, the hot tub is online and currently at 101.0°F.
07:35:40 /dev/api/chat        2  4.3s | Yes, the hot tub is online and the water temperature is 101°F.
07:35:45 /dev/api/chat        3  5.5s | Yes, the hot tub is online and the temperature is 101.0°F.
07:35:50 /dev/api/chat        4  7.6s | Yes, the hot tub is online and the water temperature is currently 101.0°F.
07:35:58 /dev/api/chat        5  8.8s | Yes, the hot tub is online and currently set to 101.0°F.
07:36:07 /dev/voice/api/chat  1  5.8s | Yes, the hot tub is online and currently set to 101.0°F.
07:36:12 /dev/voice/api/chat  2  5.1s | Yes, the hot tub is online and currently at 101.0°F.
07:36:17 /dev/voice/api/chat  3  8.3s | Yes, the hot tub is online. The current temperature is 101.0°F, which is set to maintain.
07:36:26 /dev/voice/api/chat  4  7.7s | Yes, the hot tub is online, currently at 101°F.
07:36:33 /dev/voice/api/chat  5  5.1s | Yes, the hot tub is online and currently at 101.0°F.
```

10/10 from the file. No invented value: every number is 101 or 101.0, which
is both `temperature_f` and `set_temperature_f`. Runs 5 and 6 say "set to"
for the current reading; the two are equal, so the statement is true, if
loose. 4.3 to 8.8 s (one delegation hop plus a tool call).

"what temperature should a hot tub be kept at", 10 runs, verbatim, all HTTP
200. 8 answered by the coordinator directly, 2 (`/dev/api/chat` run 5,
`/dev/voice/api/chat` run 1) through `ask_research` → `web_search`. None
called `query_hottub`; none refused.

```
07:36:39 /dev/api/chat        1  3.7s | Most hot tubs are best kept between 100°F and 104°F (38°C - 40°C) for comfortable use by adults. If the water is too cold, it won't provide the relaxing heat intended for therapeutic benefits.
07:36:42 /dev/api/chat        2  1.8s | Hot tubs should generally be kept between 100 and 104°F for comfort.
07:36:44 /dev/api/chat        3  1.5s | Most hot tubs are recommended to be kept between 100°F and 104°F (38°C–40°C).
07:36:45 /dev/api/chat        4  1.1s | A hot tub temperature should generally be kept between 100°F and 104°F (37.8°C and 40°C).
07:36:47 /dev/api/chat        5 12.3s | The recommended temperature for a hot tub is generally between 100°F and 102°F for healthy adults. For children, the recommended temperature is 85°F (35°C).
07:36:59 /dev/voice/api/chat  1 10.7s | For general therapeutic use by healthy adults, the recommended temperature is between 100°F and 104°F. If you are soaking for long periods, 98°F to 102°F is often cited for optimal comfort. Temperatures should be limited to a maximum of 37°C (99°F) for pregnant individuals and 35°C (95°F) for children.
07:37:10 /dev/voice/api/chat  2  2.1s | A typical temperature range for a hot tub is between 100°F and 104°F.
07:37:12 /dev/voice/api/chat  3  3.2s | A standard temperature for a hot tub is typically between 100°F and 104°F (38°C and 40°C).
07:37:15 /dev/voice/api/chat  4  3.0s | Most manufacturers recommend keeping a hot tub temperature between 100°F and 104°F (38°C to 40°C).
07:37:18 /dev/voice/api/chat  5  2.7s | A hot tub should typically be kept between 100°F and 104°F (38°C and 40°C).
```

10/10 normal answers. One content error, not a refusal: `/dev/api/chat`
run 5 says "85°F (35°C)"; 35°C is 95°F.

## Tests (criterion 9)

`./scripts/run_tests.sh -q`, last line, before (`97dfdc4`):

```
1412 passed, 2 skipped, 18 xfailed, 3 warnings in 11.50s
```

After (`89be6b4`, and again after `d49328a`):

```
1409 passed, 2 skipped, 18 xfailed, 3 warnings in 11.53s
```

Collected ids diffed, 1432 → 1429 = 1432 − 4 + 1:

- Out (M1b's negative tests):
  `test_agentic_loop.py::test_execute_tool_removed_hottub_tool_is_unknown`,
  `test_agentic_loop.py::test_hot_tub_feature_is_gone`,
  `test_coordinator_bench.py::test_absent_device_checker`,
  `test_tool_service_routes.py::test_hottub_route_is_gone` (the file is gone
  with it; M1b created it).
- Back: `test_coordinator_bench.py::test_hard_composite_answer_checkers`
  (the `_hottub_cost` checker of `composite_hottub`).
- Back to their `sprint-M1` text, same names:
  `test_hard_composite_uses_its_own_canned_answers_and_needs`,
  `test_scoreboard_adds_an_absent_data_section_only_when_those_runs_exist`,
  `test_non_terminal_delegation_still_synthesizes`.

Skips unchanged (the two `KRONK_LIVE` tests); xfails unchanged (18 voice
corpus rows).

## Voice replay (criterion 10)

`python3 scripts/voice_replay.py --label m1b-restore` on dev after the
rebuild: `docs/bench/voice_replay_2026-11-16_123734_m1b-restore.json`.

| Run | pass | xfail | fail | skip |
| --- | --- | --- | --- | --- |
| committed baseline `…_012824_m10-baseline.json` | 91 | 20 | 1 | 2 |
| M4 e2e pass 2 `…_112724_m4-e2e2.json` | 90 | 20 | 1 | 2 (+1 xpass) |
| this restore | 91 | 20 | 1 | 2 |

46 executed, 66 checked read-only, 17 players unchanged, dry run on. Every
row has the baseline's status. Rows that differ across the three runs:

| Row | baseline | M4 e2e2 | restore |
| --- | --- | --- | --- |
| `inc-0930-search-deep-house-ytm` | fail | fail | fail |
| `eval-shuffle-it` | xfail | xpass | xfail |

`inc-0930…` fails the same way as the baseline: the coordinator sent
"Search for some deep house chill music on YouTube Music" to
`ask_research` → `web_search` instead of `play_music` (trace
`30812b1ead9854ad05ee276d9eb36fa4`). Both rows are the known flappers.

## Production at promotion

Production runs `sprint-M1b` (`deploy_tag.sh status`) and was not touched:
every production container has the same id, created time, `StartedAt` and 0
restarts as at 07:32. Until promotion it answers "I can't check the hot
tub", and `GET localhost:8003/hottub` is 404.

At promotion, nothing outside the tag deploy:

- `deploy_tag.sh deploy <tag>` rebuilds `orchestrator` and `tool_service`
  (and restarts nginx). Production's `tool_service` mounts `./data` at
  `/data` (linked into the release), so it reads the live
  `data/hottub/status.json` with no compose change. The dev mount in
  `6388c56` does not affect production.
- The monitor is a host unit running from the live checkout
  (`hottub_monitor/` on `refactor`, identical on `main`), not from the
  release. It is already enabled and running; no host step is left.
- Check after: `curl localhost:8003/hottub` shows a `last_check` within
  5 minutes, and "is the hot tub online" through `/api/chat` and
  `/voice/api/chat` names the reading.
- `PROMOTIONS.md`'s M1b row lists the unit file, `data/hottub/` and the
  tarball as held for Drew; those deletions are now cancelled. The row is
  the coordinator's to update.

## Box state

Checked at 07:32 and 07:44.

| Thing | Result |
| --- | --- |
| Production containers, HA, Music Assistant | Ids, created times, `StartedAt` and restart counts identical. Not restarted. |
| Dev pair | Rebuilt once (above). |
| `./data` | `find data`: nothing removed; one path added, `data/dev/hottub/` (empty mountpoint). `data/hottub/status.json` rewritten by the monitor. |
| Backup tarball | Present, sha256 unchanged. |
| `.env`, `.env.dev` | md5 unchanged; not edited. |
| LAN | Only the monitor's own connection to the spa pack at the address in `monitor.py`. No scan, no other host contacted, no `/ops/exec`. |
| Sound | None. Replay dry run on, players unchanged. |
| Background jobs | The ten-minute watch finished; nothing of mine is running. |

## Left for Drew

1. **Promotion.** Production keeps saying "can't check the hot tub" until a
   tag containing this restore is deployed.
2. **HOTTUB-01's open items**, now stated in `TECH_DEBT.md`: alerting (never
   built), stale readings served as current, and a virtualenv that is not
   reproducible from the repo (no tracked requirements file; tied to host
   Python 3.13.7).
3. **The reported temperature.** `temperature_f` was 101.0 at the
   2026-11-15 22:30 write, ten minutes after the journal logged the raw
   `RhWaterTemp` block going 102.4 → 90.0 → 100.7 (22:18–22:20), and 101.0
   again today while `RhWaterTemp` read 100.4–100.6 and the heater switched
   on at 07:38. The monitor reports geckolib's
   `water_heater.current_temperature`, not `RhWaterTemp`. Whether that value
   tracks the water or sits at the set point is not established; I did not
   investigate further.
4. **Voice corpus.** No row added: nobody asked the hot tub question by
   voice during the removal, so there is no transcribed utterance. A row for
   "is the hot tub online" expecting `query_hottub` would guard this path in
   the replay; it would change the replay's row count against the M10
   baseline.
5. `data/dev/hottub/` is an empty root-owned mountpoint created by Docker;
   harmless, and it goes away only with `sudo rmdir` if the mount is ever
   dropped.
