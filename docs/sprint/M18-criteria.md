# M18 pass criteria: an upgrade cadence

Written by the sprint coordinator on 2026-11-18 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M18. An
upgrade cadence", the M18 line of "Verified on Kronk, 2026-11-15", Drew's
decisions of 2026-11-14 and 2026-11-15) and the items promoted since. The
tester checks against this file, not against the build report.

## Drew's decisions this item carries out

- Minor upgrades: monthly, applied without asking; Drew is told afterwards
  what changed.
- Major upgrades: quarterly, never started by the system. It writes an
  impact report to disk, then tells Drew one is needed. Drew greenlights
  and declares a window, or declines.
- Music Assistant is excluded.
- The minor/major table per component, as in the plan (HA: patch releases
  within the pinned month are minor, a new monthly release is major;
  LiteLLM, SearXNG, nginx, Piper, Tempo, Grafana: patch releases are
  minor; llama.cpp: nothing is minor; Python lockfiles: patch releases,
  regenerated with hashes).

## Scope of the sprint item

Built and tested: the minor-upgrade script, the quarterly check and its
report, the runbook for major upgrades, the notices, the timers as
reference units. **No component is upgraded by the build or the test.**
The coordinator installs the timers at promotion; the first real minor
run is the timer's, a month out. The first major upgrade (HA) is not
started: its report is written, and it waits for Drew.

## Context the plan did not have

- Production runs from a tag through `scripts/deploy_tag.sh`; no compose
  command is typed in the working tree against it (M12). A pin change
  therefore reaches production as a commit, a tag and a deploy.
- Home Assistant is not deployed from tags (`docker-compose.ha.yml`,
  project `kronk-ha`). Its image is tagged `2026.5.4` locally (M12). An HA
  patch upgrade is an image pull and a recreate of HA: a restart, which
  `CLAUDE.md` says is never a side effect. Drew's M18 decision makes the
  monthly patch upgrade an approved cause; the script still picks the
  moment (criterion 6).
- Audio capture is on until 2026-12-01 and an HA restart cuts
  `voice_tracer`'s correction windows short (M12); the live config
  directories are outside the repo and a deploy validates them (M21).
- There is no backup job yet (M16 is not in the sprint). A major upgrade's
  runbook needs one; a minor upgrade rolls back by tag and image.
- Open with Drew: how a notice reaches him when HA itself is what failed.

## Criteria

### A. Check first

1. The build report gives, per component in the plan's table plus
   anything else pinned (the telemetry stack, `wyoming-whisper`'s venv,
   the dev Whisper, base images in Dockerfiles, the Python lockfiles of
   each service): the pin, where it is written, what is actually running,
   the newest patch release and the newest release of any kind, and where
   those were read. Registry and index lookups are read-only and send
   nothing about this system beyond the request itself. Any pin that does
   not match what runs is listed. `docs/plans/MA_FORK_STATUS.md` is read
   and its state recorded; Music Assistant is otherwise left alone.

### B. The minor upgrade

2. `scripts/upgrade_minor.sh` (or a Python script behind it): for each
   component it finds the newest patch release inside the pinned minor
   (the plan's table decides what "patch" means per component; the rules
   are data in one file with a comment per component, and a version
   string the rules cannot parse is skipped and reported, never guessed).
   It never crosses into a release the table calls major, never touches
   Music Assistant, llama.cpp or a model file, and never changes a pin
   that is not an exact version.
3. One component at a time, each its own commit: update the pin (and for
   Python lockfiles, regenerate with hashes, patch releases only); bring
   the new version up; verify; keep or roll back; then the next. The
   order is stated, and a failure in one does not stop the others unless
   the failure is in the rollback itself.
4. Verify means: the component's own health check; the offline corpus
   (`./scripts/run_tests.sh`); the live dry-run replay on the dev path;
   the deploy script's own checks for the Kronk stack; and for HA the
   checks of M12's runbook (satellites back, pipelines, `rest_command`,
   players as before). A known flapping replay row does not fail an
   upgrade; the list of such rows is data, not code.
5. Rollback restores the previous pin, the previous image and the running
   state, and is itself verified. For the Kronk stack that is a deploy of
   the previous tag; for HA the previous image by digest, kept locally
   until the next month's run. The script never deletes an image or a
   release directory it may need for rollback.
6. It picks its moment and can decline: it does nothing while a voice
   request is in flight or was in the last five minutes, while any media
   player is playing, while `voice_tracer` holds a run, while the git
   tree is not clean or not on the expected branch, while a deploy or
   another run is in progress (a lock), or while the last run's rollback
   failed. Declining is reported, and the timer tries again later a
   bounded number of times.
7. Everything it does is in one log per run under `docs/upgrades/` (or a
   path outside the repo if the report argues it), and the pin changes
   are commits with a message naming component, from and to. It pushes
   nothing. `--dry-run` shows what it would do and changes nothing; the
   tests and the tester use only that and scratch targets.
8. The notice (criterion 12) is sent after every run that changed or
   tried to change something: what was upgraded, from and to, what was
   rolled back and why. A run with nothing to do sends nothing.

### C. The quarterly check and the first report

9. A quarterly check finds releases the table calls major and writes
   `docs/upgrades/<date>.md`: for each component the version change, the
   release-note items that touch Kronk's integration points (the Ollama
   conversation integration, Assist pipelines and satellites, Wyoming,
   conversation triggers, the Music Assistant integration, Matter), the
   Kronk systems affected, and a proposed order. Release notes are
   fetched read-only from the projects' own pages; what could not be
   fetched is said, not invented. It then tells Drew a major upgrade
   needs scheduling and where the report is. It upgrades nothing.
10. The first report is written by the build and committed: Home
    Assistant from `2026.5.4` to the current monthly release, and every
    other component with a major release available, read from real
    release notes. It says what M8 and M15 would gain, and what the
    upgrade puts at risk (the pucks' firmware pairing, the Ollama
    integration, the conversation triggers the blueprint uses, the
    Wyoming entries, the debug-recording option M12 uses).
11. `docs/runbooks/major-upgrade.md`, modelled on the existing
    `searxng-bump.md`: backup (naming what must be backed up and that the
    job does not exist until M16), the new version on the dev path or a
    scratch container, the corpus, the window, the upgrade, verification,
    rollback by image and volume restore. One component per window. An HA
    section with the specifics.

### D. Notices

12. Notices go through the existing `scripts/lib/notify.sh` to Drew's
    phone. When HA is the component that failed, or the notify call
    fails, the result is still visible without HA: a status file that the
    web UI's `/services` page shows as a warning until it is acknowledged,
    and the notice is retried when HA answers again. The build report
    states this as the default and lists the alternatives for Drew. No
    notice is sent during the build or the test (the notifier is
    stubbed); one real test notice is the coordinator's at install.

### E. Timers

13. Reference units in `systemd/` for the monthly run and the quarterly
    check (user timers, `Persistent=true`, a time of day when the house
    is asleep), with install and uninstall commands for the coordinator.
    Nothing is installed by the build.

### F. Regression, safety, docs

14. Tests: the version rules for every component with real version
    strings and awkward ones; the decline conditions; the order; verify
    and rollback with every step stubbed, including a rollback that
    fails; the notice text; the report writer with stubbed release notes.
    `./scripts/run_tests.sh` passes: at least `4989 passed, 3 skipped,
    53 xfailed` plus the new tests. New tests do not reach the network.
15. One end-to-end rehearsal on a scratch target that cannot touch
    production: a scratch compose project with a harmless pinned image
    (or stubs for docker and deploy), taken through upgrade, verify,
    forced failure and rollback. The report says exactly what was real
    and what was stubbed.
16. The build changes nothing in production, Home Assistant, Music
    Assistant, the pucks, the Whisper units or the llama units; pulls no
    image into the production tags; upgrades nothing; makes no sound.
17. Docs: `docs/features/` for the cadence (what is automatic, what
    waits for Drew, how to pause it, how to run it by hand, how to read a
    run's log), `CLAUDE.md` (tenet 3's "upgrade deliberately" now has a
    schedule; the HA-restart rule names the monthly patch run as an
    approved cause), `ROADMAP.md`, the plan's open item on the notice
    path.

## Reports

Build: `docs/sprint/M18-build.md`. End to end: `docs/sprint/M18-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
