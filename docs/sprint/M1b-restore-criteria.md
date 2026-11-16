# M1b restore pass criteria: the hot tub feature stays

Written by the sprint coordinator on 2026-11-16. Drew reversed M1b that day:
the plan's premise (HOTTUB-01, "the monitor never worked") was wrong; the
monitor had been connected and reporting since 2026-10-24. The tester checks
against this file.

State before the restore: the M1b removal is on `refactor` (commits
`0583239`, `855a9d9`, `489e306`, `faf37aa`) and in production (tag
`sprint-M1b`). The user unit `kronk-hottub-monitor.service` is stopped and
disabled; its unit file and `data/hottub/` are intact; the monitor directory
with its virtualenv is in `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz`.

## Criteria

1. The feature is back on the branch as it was at tag `sprint-M1`:
   `query_hottub` (definition, handler, dispatch, timeout), the `home`
   agent's hot tub wording and tool, `tool_service`'s `/hottub` route and
   `HOTTUB_STATUS_FILE`, and `hottub_monitor/monitor.py`. A diff of those
   pieces against `sprint-M1` shows only differences caused by later sprint
   items (M3, M4), each explained.
2. The coordinator sentence M1b added ("can't check the hot tub") is gone,
   and so are the M1b negative tests and the `no_device_hottub` probe. The
   hot tub bench probe and routing-eval row removed by M1b are back.
3. Nothing from M1 came back: no health, finance or retirement code, test,
   route or doc. Nothing from M3 or M4 was lost: `tool_service/ops.py`, its
   tests, the devops tool list, the loop check and the M4 description
   wording are byte-identical to before the restore.
4. `hottub_monitor/.venv` is restored from the tarball (untracked, as
   before) and `monitor.py` matches the tracked file. The unit is enabled
   and active, has stayed up for at least ten minutes without a restart,
   and `data/hottub/status.json` has a `last_check` newer than the restart.
   The journal shows a connection, not a retry loop. If the spa cannot be
   reached, that is reported with the journal lines, not hidden.
5. A reference copy of the unit file is added to `systemd/` (the
   verification pass found it had none), matching the live unit.
6. Docs say the truth: `TECH_DEBT.md` HOTTUB-01 is corrected (the monitor
   works since 2026-10-24; what remains open, such as alerting, is stated),
   README lists the feature, `docs/HISTORY.md` records the removal and the
   reversal in a few lines, and the plan's M1b section carries a status
   note that it was reversed. An incident or investigation note in
   `docs/incidents/` records how a working feature came to be removed on a
   stale tech-debt entry and what would have caught it.
7. On the dev path, "is the hot tub online" calls `query_hottub` and
   answers from the status file with the current reading, at least 8 of 10
   runs across both mounts; no answer invents a value that is not in the
   file. Dev mounts shared data read-only; say how dev sees the status
   file, and if it cannot, test the tool at unit level and through
   `tool_service-dev` directly and say so.
8. General questions are not refused: "what temperature should a hot tub be
   kept at" gets a normal answer in 10 of 10 runs.
9. `./scripts/run_tests.sh` passes. Before the restore:
   `1412 passed, 2 skipped, 18 xfailed`. The count after is explained test
   by test (M1b's four negative tests out, the removed probe checker back).
10. The M10 live replay on dev matches the committed baseline apart from
    known run-to-run variance (`eval-shuffle-it` and the "search YouTube
    Music" rows are known to flap).
11. Production containers, HA and Music Assistant untouched; nothing under
    `./data` deleted; the backup tarball left in place.

## Reports

Build: `docs/sprint/M1b-restore-build.md`. End to end: tested together with
M4b in `docs/sprint/M4b-e2e.md`.
