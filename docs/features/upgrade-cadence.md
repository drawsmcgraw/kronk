# Upgrade cadence

Sprint item M18 (built 2026-11-18; fix rounds 1 and 2 the same day,
`docs/sprint/M18-fix1.md`, `docs/sprint/M18-fix2.md`). Pins stay; they now move on a schedule.
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

A release is taken only when the project's release list (GitHub) and the
registry both have it and both dates are at least 14 days old. A registry
tag pushed more than 48 hours after its release is a moved (re-pushed) tag:
not taken, and the plan says so. A release whose date cannot be read is not
taken; a tag the registry has and the release list does not is not taken.
Docker Hub gives a push date per tag; ghcr gives none, so the image's build
time (its config's `created`) stands in. Where the registry is the release
list (SearXNG, nginx, the Python bases), there is one date. A version string the rule cannot parse is skipped and
reported (only ASCII digits count, and a part with a leading zero such as
`13.02.6` is not a version, except where a component is `zero_padded`, a
`YYYYMMDD` date); a pin that is not an exact version (`nginx:1.29-alpine`,
`python:3.12-slim`, `pip install uv`) is never changed.

## The monthly run

`systemd/kronk-upgrade-minor.timer` fires at 03:30 on days 1 to 7 of each
month. The first night that is not declined does the month; later nights
find it done. Step by step (`scripts/upgrades.py`, `minor_run`):

1. Takes `data/upgrades/lock` and writes its holder into it. While it is
   held, `scripts/deploy_tag.sh deploy` refuses and names the holder; the
   run's own deploys carry its token. The quarterly check waits for the
   same lock. A status file that cannot be parsed stops the run: it is
   never read as empty.
2. If an earlier run finished components and died before its notice, it
   sends that notice now ("… (run <time>, which ended before its
   notice)"). If an earlier run died inside a component, its intent record
   is still in the status file: this run deals with it first (below, "An
   interrupted run").
3. Outside the night window (`night_window`, default 02:00 to 05:00 local)
   it declines without counting an attempt: the timer's boot catch-up
   (`Persistent=true`) at 19:00 does nothing, and the next night's firing
   runs. A missed day 7 is next month's.
4. Counts this month's attempt (at most 7); retries any notice HA did not take.
5. Plans: per component, the newest minor release that passes the rules
   above. Every pin is compared with what its container runs; a pin that is
   not what runs is reported (`MISMATCH`, one notice and a `/services`
   warning a month), never read as `CURRENT`. Nothing to do: stops.
6. Declines (and tries again tomorrow) when the household is busy or the
   tree is not ours: see "House busy" below; the last rollback failed or a
   component was left out of step; a deploy is running; the tree is not
   clean; the branch is not `KRONK_UPGRADE_BRANCH` (default `main`). A check
   that cannot be read (HA down, the probe silent) is a decline, not a pass.
7. Baseline: `./scripts/run_tests.sh` and the live dry-run replay on
   `kronk-dev`. If either cannot run, declines.
8. One component at a time, in the rules' `order` (telemetry, Piper,
   SearXNG, nginx, LiteLLM, base images, then Home Assistant last, because
   every other replay goes through HA):
   - per-component checks: the night window is still open; no deploy has
     started; for the Kronk stack, production runs from a tag and HEAD
     differs from it only in `inert_paths`; for HA and telemetry, the
     compose file at HEAD has the running container's config hash, and no
     file the container bind-mounts from the working tree changed after
     the container started (paths the service reloads by itself, the
     rules' `live_binds`, aside); for HA, M12's H6 checks and H7b (the
     compose file renders, every bind-mounted path exists); the house busy
     check again;
   - the intent record is written to the status file before each step
     that changes git or what runs, and cleared when the component ends;
   - the pin is edited and committed: `upgrade: <name> <from> -> <to>`;
   - brought up: Kronk stack by `git tag upgrade-<run time>-<name>` and
     `scripts/deploy_tag.sh deploy`; telemetry by `compose pull` and
     `up -d --no-deps`; HA by M12's runbook in code (the running image id
     tagged `kronk/ha-rollback:<from>`, pull, H7b again right before the
     stop, `docker stop -t 120`, `up -d`). The log records the image kept
     for rollback and the digest pulled against the registry's at planning;
   - verified: the image runs, the health checks, the stack's checks (the
     deploy's own; for HA M12's H8 plus H7's mounts, StopTimeout and no
     unclean-shutdown line), the offline corpus, the replay against the
     baseline (a row that passed before and fails now, flappers excepted).
     A check whose own read fails (`docker events`, Music Assistant's start
     time) is a failed check. A replay stopped by a player (exit 3) runs
     again for the rows it did not reach, up to three times five minutes
     apart; only then is it a failed verify. HA's "players as before"
     skips the rules' `flapping_players` (the television, both Sonos
     Moves) and does not count a move between idle, off, paused, standby
     and on; any other change fails, as does a player that was available
     and is `unavailable` after the wait. A rollback's check reports a
     player difference in the notice and never fails on it;
   - kept, or rolled back. See "Outcomes".
   Each outcome is written to the status file when the component ends. A
   component not started (the house got busy, the window closed, a
   precondition) goes again the next night; the month is done when every
   planned component has another outcome, or the attempts run out, and
   then the notice lists what never started.
9. One notice through `scripts/lib/notify.sh` when anything was changed or
   tried. A month whose last attempt ends with nothing applied (every
   component not started, or no baseline) also sends one notice and raises
   the warning, naming the reasons, as a month of declines does.

It pushes nothing. Tags and commits stay local until Drew pushes.

### House busy

"Busy" means the household: a request on production's chat and voice
routes in nginx's log in the last 5 minutes, a satellite not idle or
changed in that time, a media player `playing` or `buffering` (a paused,
idle or `on` player does not decline: Drew's ruling, D4 in the end-to-end
report), or voice_tracer holding a run from the household. The run's own
work is not counted:

- requests under `/dev/` (the dev path, where the replay's runs go);
- requests carrying the run's own User-Agent, `kronk-upgrade/<run
  time>-<random>`, which it hands to `deploy_tag.sh` (`KRONK_CLIENT_UA`)
  for its `/api/chat` check. The value is new every run and appears nowhere
  else, so a household request (HA's aiohttp, a browser, curl by hand,
  another run) never carries it;
- held runs listed in voice_tracer's state file that are on the `kronk-dev`
  pipeline (HA's `pipeline_debug/list`, read-only) or from the run's own
  replays (their `pipeline_run_id`).

A "Hey Jarvis" request by a person also goes to `/dev/`, but it changes its
satellite's state, which the satellite check counts.

### Outcomes

Each component ends one way, and the notice line puts the state first
(what git has, what runs; "Home Assistant is down" in those words when it
is) and the detail after:

- `upgraded`: kept.
- `rolled back to <from>`: verify failed after something changed; the pin
  is back by a commit, the previous tag deployed or the previous image up,
  and that verified.
- `not applied`: a step failed before anything changed what runs (a tag
  that exists elsewhere, a failed pull, a missing precondition, a tag that
  moved since planning: for the Kronk stack too, whose image is pulled and
  its digest checked before the tag and the deploy). The run reverts its
  own pin commit; nothing is restarted.
- `not started`: a precondition was not met; nothing was committed.
- `ROLLBACK FAILED`: a rollback ran and failed. The run stops; later runs
  decline until `python3 scripts/upgrades.py ack`.
- `OUT OF STEP`: git and what runs could not be put back in step (a revert
  that failed, an uncommitted change). Stops and blocks like a failed
  rollback.

### An interrupted run

A run killed inside a component (a reboot, OOM, `systemctl stop`, the
unit's 4 h timeout) leaves its intent record: the component, from and to,
the step, where HEAD was when the component started, the previous
production tag and the run's own tag (Kronk stack), the config hash before
and after the pin commit (telemetry, HA), HA's previous image id. Recovery
acts by itself only when the world is exactly what the record says.

Home Assistant down comes first, before the tree, branch, window and month
checks: a stopped `homeassistant` container is started as it is (`docker
start`, no compose, no recreate, no pin or git change), then the rest
below runs on what it finds. A missing container (a `compose up` cut
short) is created on the rollback image with `compose up` and no stop,
after M12 H7b, putting the pin back by a commit first if the compose file
says the new version and the tree allows it. No rollback ever runs
`docker stop` on a container that does not exist.

Then, in order: the working tree not clean, or HEAD carrying commits the
record does not explain (anything but the run's own pin, rollback, revert
or a quarterly report), or git at neither version: OUT OF STEP, nothing
touched.

| Component | Found | Done |
| --- | --- | --- |
| Kronk stack | production runs the run's own tag | deploy the recorded previous tag (pin back first if git has the new one), inside the night window with the house quiet, else one notice and it waits; verified |
| Kronk stack | production runs the recorded previous tag, the component runs the old version | pin put back by a commit if git has the new one; nothing restarted |
| Kronk stack | production runs the previous tag, the component runs anything else | OUT OF STEP |
| Kronk stack | production runs any other tag, or the previous tag no longer exists | OUT OF STEP: the notice names the tag that runs, the recorded tags and what to do; nothing deployed over a person's deploy |
| Telemetry, HA | the old version runs | pin put back if git has the new one; nothing restarted |
| Telemetry, HA | the new version runs, and the container is the one the run created (its config-hash label), the file at HEAD still gives the hash the record has, no bind-mounted file changed after the start, and (HA) H7b passes | `up -d` of the previous image (HA: stop, `up -d`, by the recorded image id), inside the window with the house quiet; verified |
| Telemetry, HA | the new version runs and any guard fails; a container not running (telemetry); anything else | OUT OF STEP |
| any | the rollback above fails | ROLLBACK FAILED |

OUT OF STEP and ROLLBACK FAILED block later runs until `ack`. A recovered
component is held back for the rest of the month (Drew's call). The
notice says what was found and what was done.

Between runs, the hourly `kronk-upgrade-notices` unit looks for a record
left with the lock free: it raises the `/services` warning and sends one
notice, and if the record is HA's and HA's container is stopped, it starts
that container. It does nothing else. `GET /api/upgrades` shows any
unfinished record as a warning. While the record exists,
`scripts/deploy_tag.sh deploy` refuses (the run's own deploys pass with its
lock token), naming the two ways out: `python3 scripts/upgrades.py minor
--ignore-window` (recover now) or `python3 scripts/upgrades.py ack` (drop
the record; a person takes over). `plan`, `status` and `seed` are not
affected.

Worst case for HA down after a kill: HA stopped between its stop and its
start is back within the hour (the hourly unit, `RandomizedDelaySec=5m`).
A missing HA container is created only by the monthly run (days 1 to 7;
after a kill on the 7th, the 1st of next month) or a run by hand.

### What a Kronk-stack upgrade costs

Every Kronk-stack component upgrade is a full production redeploy:
`deploy_tag.sh deploy` of a new tag changes the release directory, so every
service with a bind mount (orchestrator, tool_service, voice_tracer, nginx,
litellm, searxng) is recreated. A rollback is another. Two Kronk components
in one night can mean four restarts of production. The notice says how many
times production was redeployed.

### What the household check cannot see

- A typed chat request still streaming: nginx writes its log line when the
  request ends, and no satellite is involved.
- Production chat requests spelled oddly (`/dev/../api/chat`, `//api/chat`,
  `/./api/chat`, `/%61pi/chat`, the absolute form): nginx routes them to
  production and the check does not count them. No household client
  sends these.
- `/devstral/` traffic; and a Kronk-stack deploy restarts nginx under it.
- Held runs beyond the ten newest of the dev pipeline: HA's
  `pipeline_debug/list` returns the last ten runs of a pipeline, so a run
  sets aside its own replay's runs by the ids in its result files, and
  another session's dev replay inside voice_tracer's correction window
  declines the night.

## The quarterly check

`systemd/kronk-upgrade-major-check.timer` fires on the 8th of January,
April, July and October at 04:30. It writes `docs/upgrades/<date>.md` (the
version table, release-note lines that mention Kronk's integration points,
the Kronk systems behind each, the lockfiles, a proposed order), commits it
when the tree is clean and on the expected branch (otherwise writes it to
`data/upgrades/reports/` and says so), then sends one notice with the
report's path. It upgrades nothing. It waits for the upgrade lock at most
`major_lock_wait_minutes` (20), under its unit's 30-minute timeout, then
gives up and says so. Majors follow
`docs/runbooks/major-upgrade.md`, one component per window. The first
report is `docs/upgrades/2026-11-18.md`.

## Notices when HA is the thing that failed

The notice goes through HA. When HA does not take it, the result is still
visible without HA: `data/upgrades/status.json` keeps the notice, the
`/services` page shows an "Upgrade cadence" card with a warning (from
`GET /api/upgrades`, production's orchestrator, read-only), and
`kronk-upgrade-notices.timer` retries the notice hourly until HA answers
(and acts on an interrupted run's record, above).
The warning stays until `python3 scripts/upgrades.py ack`. Rollbacks, failed
rollbacks, out-of-step components, interrupted runs, pins that are not what
runs, a month that never found a quiet moment and a month that applied
nothing raise the same warning. A status file that cannot be parsed shows
as a warning too (the API reads it), and every command stops on it. This is the built default; Drew has not chosen it yet (alternatives
in the build report).

## By hand

- What would happen now (any time; reads only, writes and sends nothing):
  `scripts/upgrade_minor.sh --dry-run`, `scripts/upgrade_major_check.sh --dry-run`.
- A real run outside the timer: `scripts/upgrade_minor.sh --ignore-window`
  (the night window is the timer's; the house checks still apply), with
  `--ignore-month` when this month's run is done. A rerun the same day is
  safe: tags are named after the run time, and a tag that already exists is
  reused only when it points at the same commit. A rerun does not bypass a
  failed rollback or an out-of-step block: read the log, fix it, `ack`.
- Pins, what runs, what is newest: `python3 scripts/upgrades.py inventory`.
- The status file: `python3 scripts/upgrades.py status`. Clear the warning
  (after reading it): `python3 scripts/upgrades.py ack`. `ack` also clears
  a failed-rollback or out-of-step block and drops an interrupted run's
  record: after it, no recovery acts on that record, and what runs is yours
  to put right.
- Do not deploy by hand while an interrupted run's record exists
  (`deploy_tag.sh deploy` refuses): recover with `scripts/upgrade_minor.sh
  --ignore-window`, or `ack` first.
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

## Installing the timers

Nothing is installed by the build. At 03:30 the working tree must be clean
and on the units' branch (`KRONK_UPGRADE_BRANCH`, `main` in the reference
units), or every night declines and night 7 says so. From the repo:

```
git status --porcelain                      # empty
git rev-parse --abbrev-ref HEAD             # the branch production's tags are made from
cp systemd/kronk-upgrade-{minor,major-check,notices}.{service,timer} ~/.config/systemd/user/
# only if production is not deployed from main (both units carry the branch):
sed -i 's/^Environment=KRONK_UPGRADE_BRANCH=.*/Environment=KRONK_UPGRADE_BRANCH=<branch>/' \
    ~/.config/systemd/user/kronk-upgrade-{minor,major-check}.service
systemd-analyze --user verify ~/.config/systemd/user/kronk-upgrade-*.{service,timer}
systemctl --user daemon-reload
systemctl --user enable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
systemctl --user list-timers 'kronk-upgrade-*'
python3 scripts/upgrades.py test-notice     # one real notice, marked TEST
scripts/upgrade_minor.sh --dry-run          # reads only
```

Remove:

```
systemctl --user disable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
rm ~/.config/systemd/user/kronk-upgrade-minor.service ~/.config/systemd/user/kronk-upgrade-minor.timer \
   ~/.config/systemd/user/kronk-upgrade-major-check.service ~/.config/systemd/user/kronk-upgrade-major-check.timer \
   ~/.config/systemd/user/kronk-upgrade-notices.service ~/.config/systemd/user/kronk-upgrade-notices.timer
systemctl --user daemon-reload
```

`data/upgrades/` (status, lock, logs) is left in place on removal; it is
the record of what ran.

## Gotchas

- The run tags and deploys HEAD of the expected branch. Work committed but
  not deployed blocks the Kronk-stack components (by design); deploy it or
  wait. `inert_paths` lists what does not count.
- Docker Hub dates are last-push dates; ghcr's stand-in is the image's
  build time. A re-pushed tag looks younger, never older: it is reported as
  a moved tag and not taken.
- The telemetry and HA guard compares bind-mounted files' modification times
  with the container's start. A file touched without a change declines too;
  recreate the container by hand once, or wait for the change to be what
  runs. Grafana's dashboards are `live_binds` (Grafana polls them).
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
