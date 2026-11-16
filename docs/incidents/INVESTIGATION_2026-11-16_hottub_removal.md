# Investigation 2026-11-16 — a working feature removed on a stale tech-debt entry

**Status:** closed. The removal (sprint item M1b) was reversed on
2026-11-16 and the feature restored (`docs/sprint/M1b-restore-build.md`).
Doc written during the restore, from the M1b build and test reports, the
unit's journal, and git history.

## Symptom

The hot tub feature (`query_hottub`, `tool_service` `GET /hottub`, the
`kronk-hottub-monitor` host unit) was removed from Kronk as "never worked".
It had been working for three weeks. From the `sprint-M1b` promotion
(2026-11-15, about 23:46 EST) until the restore is promoted, production
answers "is the hot tub online" with "I can't check the hot tub", and the
coordinator sentence added for the removal also made general hot tub
questions ("what temperature should a hot tub be kept at") get "I can't
check" in about 1 of 4 requests (`docs/sprint/PROMOTIONS.md`, M1b row).

## Timeline

- 2026-05-01: `kronk-hottub-monitor.service` installed (user unit, no copy
  in `systemd/`).
- 2026-06-12: `TECH_DEBT.md` HOTTUB-01 written in a codebase cleanup
  (`d2569a8`): "Hot tub monitor non-functional. Parked. The geckolib monitor
  can't reach the spa pack (connection retry loop)." True at the time.
- 2026-08-23 (start of the retained journal) to 2026-10-24 13:50: the spa
  module does not answer; about 10,400 "No response" / offline lines, one
  attempt every 5 minutes.
- 2026-10-09: the consolidation review (`docs/REVIEW_2026-10-09_consolidation.md`,
  §1 and I6) finds the unit "enabled and running against a feature TECH_DEBT
  calls parked" and recommends "add to `systemd/` or disable". Correct on
  that date: the process ran, the spa did not answer.
- **2026-10-24 13:50: `HOT TUB BACK ONLINE — 32.0°F (set: 101.0°F)`.** From
  here the monitor reports readings. Nothing outside the journal and
  `data/hottub/status.json` records it: no alert exists, and no step updates
  HOTTUB-01.
- 2026-11-04 08:45: host reboot; the monitor reconnects and stays connected.
- 2026-11-14: the evaluation plan's M1b is approved: "the hot tub monitor
  never worked, so the feature is removed. `TECH_DEBT.md` (HOTTUB-01) already
  records it as non-functional."
- 2026-11-15, plan's check-first pass (`2fca205`): "The unit
  `kronk-hottub-monitor.service` is live and wrote `data/hottub/status.json`
  today." The check confirmed the mechanics of removal (no narration line,
  no `geckolib` in `tool_service`, `data/hottub/` 8K), not the premise.
- 2026-11-15 22:31:52: the coordinator stops and disables the unit before
  the build.
- 2026-11-15 22:30–23:00: the M1b build reads the journal, finds the monitor
  working since 2026-10-24, and puts it first in its report ("Read this
  first: the monitor was working"). It carries out the removal as ordered,
  tars `hottub_monitor/` (virtualenv included) to
  `~/backups/hottub/hottub_monitor-2026-11-15.tar.gz`, and leaves the unit
  file and `data/hottub/` for the coordinator.
- 2026-11-15 23:02–23:43: the M1b test confirms the journal reading (O4) and
  checks the restore path.
- 2026-11-15, about 23:46: `sprint-M1b` promoted, code only; the unit file,
  `data/hottub/` and the tarball held for Drew.
- 2026-11-16: Drew reverses M1b. 07:32–07:34 the four M1b commits are
  reverted; 07:33:41 the unit is re-enabled and started; 07:33:47 it logs
  `HOT TUB BACK ONLINE — 101.0°F (set: 101.0°F)`.

## Hypotheses

1. The monitor never worked (HOTTUB-01, the plan). **Wrong since
   2026-10-24.** Right for the period HOTTUB-01 was written about.
2. "Never worked" meant the alert, not the readings. **Partly.** Alerting was
   never built, and is still open; but the plan removed the readings, the
   tool and the route, not just a missing alert.
3. The unit was running but failing (the October review's reading).
   **Right on 2026-10-09, wrong a fortnight later.**

## Root cause

A tech-debt entry recorded a runtime state ("can't reach the spa pack") with
no date of last verification and no way to re-check it, and nothing changed
it when the state changed. The monitor's recovery produced no signal anywhere
a person looks: no alert (never built), no dashboard, only the journal and a
status file read through chat. The plan took its premise from the doc, which
is the case CLAUDE.md's "verify, don't trust the docs" warns about, and its
check-first pass looked at the live status file only to size the deletion.

## Fix

- The four M1b code commits reverted on `refactor`; the feature is as it was
  at `sprint-M1` apart from M4's later wording on the same line.
- Virtualenv restored from the tarball; unit enabled and running; a
  reference copy added to `systemd/`.
- HOTTUB-01 rewritten: what works, and what is open (no alerting, stale
  readings served as current, virtualenv not reproducible from the repo).
- Production is fixed by promoting a tag that contains the restore.

## What would have caught it sooner

- **Check the premise, not just the mechanics.** For an item justified by
  "X doesn't work", the check-first pass reads the evidence that X doesn't
  work. Here `cat data/hottub/status.json` (`"online": true`, a reading from
  that day) or one `journalctl --user -u kronk-hottub-monitor` would have
  shown it on 2026-11-15 before the build.
- **Runtime-state claims in docs carry the check.** A tech-debt entry that
  says "service X fails" should name the command that shows it and the date
  it was last run, so a reader can re-run it in seconds instead of trusting
  it.
- **A recovery signal.** HOTTUB-01's open alerting item, built to fire on
  both offline and back-online, would have told the operator on 2026-10-24.

What went right: the build agent read the journal before deleting, said so
at the top of its report, and the plan's guard rail held the irreversible
steps (unit file, `data/hottub/`) while a tarball covered the rest. The
reversal cost a revert and a `tar -x`.

