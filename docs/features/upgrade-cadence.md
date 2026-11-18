# Upgrade cadence

Sprint item M18 (built 2026-11-18). Pins stay; they now move on a schedule.
A monthly run applies patch releases by itself and says what it did; a
quarterly check writes a report on major releases and asks Drew to schedule
them. Build report: `docs/sprint/M18-build.md`. Plan: section "M18. An
upgrade cadence" of `Kronk evaluation findings and implementation plan.md`.

## What is automatic and what waits for Drew

The rules live in one file, `scripts/upgrade_rules.toml`, one entry per pin
with a comment. Drew's mapping (2026-11-15):

| Component | Monthly, automatic | Quarterly report, then Drew's window |
| --- | --- | --- |
| Home Assistant | patch releases inside the pinned month (`2026.5.4` to `2026.5.5`) | a new monthly release |
| LiteLLM, Piper, Tempo, Grafana | patch releases | minor and major releases |
| SearXNG | a newer date build, at least 14 days old (it has no patch scheme; see the rules file) | none |
| nginx, Python base images | patch releases once pinned exactly; today the pins float, so they are skipped and reported | minor and major |
| llama.cpp, wyoming-faster-whisper | nothing | any new build or version, with the benches |
| Python lockfiles | nothing applied: patch releases are listed in the run's log | everything (see below) |
| Music Assistant (and its PO-token helper) | excluded | excluded, until the Pandora work is upstream |

A release younger than 14 days is not taken; a release whose date cannot be
read is not taken. A version string the rule cannot parse is skipped and
reported; a pin that is not an exact version (`nginx:1.29-alpine`,
`python:3.12-slim`, `pip install uv`) is never changed.

## The monthly run

`systemd/kronk-upgrade-minor.timer` fires at 03:30 on days 1 to 7 of each
month. The first night that is not declined does the month; later nights
find it done. Step by step (`scripts/upgrades.py`, `minor_run`):

1. Takes `data/upgrades/lock`; counts this month's attempt (at most 7).
2. Retries any notice HA did not take.
3. Plans: per component, the newest minor release old enough and present in
   the registry. Nothing to do: stops, sends nothing.
4. Declines (and tries again tomorrow) when: a voice or chat request reached
   nginx in the last 5 minutes; a satellite is not idle or changed state in
   the last 5 minutes; a media player is playing; voice_tracer holds a run;
   a deploy is running; the last rollback failed; the tree is not clean;
   the branch is not `KRONK_UPGRADE_BRANCH` (default `main`). A check that
   cannot be read (HA down, the probe silent) is a decline, not a pass.
5. Baseline: `./scripts/run_tests.sh` and the live dry-run replay on
   `kronk-dev`. If either cannot run, declines.
6. One component at a time, in the rules' `order` (telemetry, Piper,
   SearXNG, nginx, LiteLLM, base images, then Home Assistant last, because
   every other replay goes through HA):
   - per-component checks: for the Kronk stack, production runs from a tag
     and HEAD differs from that tag only in files that do not reach the
     containers (`inert_paths`), else the component is not started (an
     unattended deploy would ship undeployed work); for HA and telemetry,
     the compose file at HEAD has the config hash of the running container;
     for HA, M12's H6 checks (selects untouched for 15 minutes);
   - the pin is edited and committed: `upgrade: <name> <from> -> <to>`,
     author `Kronk upgrade_minor`;
   - brought up: Kronk stack by `git tag upgrade-<date>-<name>` and
     `scripts/deploy_tag.sh deploy`; telemetry by `compose pull` and
     `up -d --no-deps` of that service; HA by M12's runbook in code (the
     running image id tagged `kronk/ha-rollback:<from>`, pull, `docker stop
     -t 120`, `up -d`);
   - verified: the image runs, the component's health checks, the stack's
     checks (the deploy's own; M12's H8 for HA), the offline corpus, the
     replay compared with the baseline (a row that passed before and fails
     now, flappers excepted, fails the upgrade);
   - kept, or rolled back: the pin restored by a commit
     (`upgrade: roll back …`), the previous tag deployed or the previous
     image by id, and that verified. A failed rollback stops the run and
     blocks later runs until `ack`.
7. One notice through `scripts/lib/notify.sh`: what was upgraded, from and
   to, what was rolled back and why, what was skipped, where the log is.

It pushes nothing. Tags and commits stay local until Drew pushes.

## The quarterly check

`systemd/kronk-upgrade-major-check.timer` fires on the 8th of January,
April, July and October at 04:30. It writes `docs/upgrades/<date>.md` (the
version table, release-note lines that mention Kronk's integration points,
the Kronk systems behind each, the lockfiles, a proposed order), commits it
when the tree is clean and on the expected branch (otherwise writes it to
`data/upgrades/reports/` and says so), then sends one notice with the
report's path. It upgrades nothing. Majors follow
`docs/runbooks/major-upgrade.md`, one component per window. The first
report is `docs/upgrades/2026-11-18.md`.

## Notices when HA is the thing that failed

The notice goes through HA. When HA does not take it, the result is still
visible without HA: `data/upgrades/status.json` keeps the notice, the
`/services` page shows an "Upgrade cadence" card with a warning (from
`GET /api/upgrades`, production's orchestrator, read-only), and
`kronk-upgrade-notices.timer` retries the notice hourly until HA answers.
The warning stays until `python3 scripts/upgrades.py ack`. Rollbacks, failed
rollbacks and a month that never found a quiet moment raise the same
warning. This is the built default; Drew has not chosen it yet (alternatives
in the build report).

## By hand

- What would happen now (any time; reads only, writes and sends nothing):
  `scripts/upgrade_minor.sh --dry-run`, `scripts/upgrade_major_check.sh --dry-run`.
- A real run outside the timer: `scripts/upgrade_minor.sh` (or
  `--ignore-month` when this month's is done).
- Pins, what runs, what is newest: `python3 scripts/upgrades.py inventory`.
- The status file: `python3 scripts/upgrades.py status`. Clear the warning
  (after reading it): `python3 scripts/upgrades.py ack`.
- Pause: `systemctl --user stop kronk-upgrade-minor.timer` (until the next
  boot), or `disable --now` (until enabled). To keep one component still,
  set its `minor = "none"` in the rules file and commit.

## Reading a run's log

One file per run: `data/upgrades/runs/<UTC time>-minor.log` (and `-major.log`),
with the replay results beside it (`-replay-<name>.json`). The log is outside
git on purpose: a log written into the repo would leave the tree dirty and
the next run would decline. The `plan:` block lists every component and
what the rules decided (`UPGRADE`, `CURRENT`, `SKIP` with why, `REPORT`,
`NONE`, `EXCLUDED`; lines with `.` are notes, `>` lockfile patches). Then
either `declined: …` or one `── <name>: <from> -> <to>` block per component
ending in `verified: … kept`, `rolled back and verified`, or `ROLLBACK
FAILED`. `journalctl --user -u kronk-upgrade-minor.service` has the same lines.

## Gotchas

- The run tags and deploys HEAD of the expected branch. Work committed but
  not deployed blocks the Kronk-stack components (by design); deploy it or
  wait. `inert_paths` lists what does not count.
- Docker Hub dates are last-push dates. A re-pushed tag looks younger, never
  older, so the age rule errs on the side of waiting.
- GitHub's anonymous API allows 60 requests an hour; one monthly run uses
  about 10, a quarterly check about 15. A failed lookup is a skip.
- The offline corpus must not pin a version the run may move:
  `tests/test_m12_ha_files.py` checks HA's pin by shape since M18.
- Python lockfiles are report only: the corpus runs in `tests/.venv`, which
  is not built from the locks; regenerating takes whatever PyPI serves with
  fresh hashes and nobody reads the diff; voice_tracer has no dev twin. The
  report lists the patches; apply them in a window.
- The HA patch run is the one approved cause of an unannounced HA restart
  (CLAUDE.md). It still declines while anything is playing or a puck is busy.

## Blog hooks

- "Pin everything" was half a policy: the other half is a schedule, or the
  pins rot (`nginx` sat on a dead mainline, uv drifted across three images).
- A boring decline list is the real safety feature: not knowing whether
  music is playing counts as music playing.
- Unattended commits need a branch rule and a "nothing undeployed" rule, or
  a 3 a.m. patch bump ships someone's half-finished work.
- When HA carries the alert, HA failing needs a second channel: a status
  file and a page that does not depend on HA.
