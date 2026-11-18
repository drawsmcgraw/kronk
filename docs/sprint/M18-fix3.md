# M18 fix round 3: an upgrade cadence

Status: fix round 3 complete; waiting for the fourth end-to-end test.

Fix agent, 2026-11-18 (UTC), branch `refactor`, fix round 3 (the last one
allowed) after `docs/sprint/M18-e2e3.md` failed M18. Inputs: CLAUDE.md,
`docs/sprint/M18-criteria.md`, `docs/sprint/M18-fix2.md`,
`docs/sprint/M18-e2e3.md` (X1, X2, Y1 to Y9, the observations) and the
coordinator's rulings H1 to H9. Nothing was upgraded, pulled into a
production tag, recreated or restarted in production, or installed; no
notice was sent (every notifier stubbed; `test-notice` not run); no
`deploy_tag.sh deploy` ran against anything real. The orchestrator changed
(`upgrades_api.py`, the new `upgrade_status.py`), so the dev pair was
rebuilt by name; production was not touched.

## Commits

| Commit | Item |
| --- | --- |
| `74e4d1f` | H1 (X1): a recovery is held to the run's tree checks |
| `0835918` | H2 (X2, Y1): a failed read is unknown; blocks keep the record |
| `5bf5183` | H3 (Y2, Y3, Y6): the hourly command |
| `23693d8` | H4 (Y4): the replay's own guard; a retry must be complete |
| `17f8c94` | H5 (Y5): an absent HA is created only past the checks |
| `e56b371` | H6 (Y7): one source for the branch; the install commands |
| `608d509` | H7 (Y8): signal 9 from the stopped container's exit code |
| `a4bd6c5` | H8 (Y9): `ack` says what it leaves; refuses two cases |
| `fde54a5` | H9: one status schema (script, API, `deploy_tag.sh`) |
| `f27c035` | H9: the loader's resolved paths and folded names |
| `6cfedc3` | H9: a `.status.*` directory ignored silently |
| `e2b0181` | H9: a component without a container has its own row |
| `7ab876c` | the three browser players in `flapping_players` |
| `37870fa` | a test that kills the self-check's one survivor (`h3-transition`) |
| `d03a4af` | the feature doc, unit comments, docstring |
| this commit | this report |

## How "failed before" was shown

Each fix's tests were written first, in `tests/test_m18_fix3.py`, and run
against the code as it stood at the previous commit; the counts below are
from those runs. Only then was the fix written. A "control" passes before
and after on purpose. Test-side changes in the same commit (the fake's new
fields, an earlier test that pinned a behaviour a ruling removed) are named
under each item. All new tests use the `no_network` fixture, temporary
directories and temporary git repositories with their own user; none opens
a socket or runs docker (the two that run `deploy_tag.sh` give it a tag
that cannot exist and temporary lock, state, release and config paths: it
stops before any docker call).

## H1 (X1): a recovery is held to the run's tree checks

What changed:
- `scripts/upgrades.py:2328` `intent_facts` reads `tree_state` (`:1313`):
  HEAD detached, on another branch than the expected one, or the tree not
  clean. `:2555` `_recover_one`: any of these is OUT OF STEP, nothing
  touched, before any row that commits, deploys, recreates or restarts.
- `tree_state` names a detached HEAD as such ("HEAD is detached, expected
  branch 'main'").
- The one exception stays: `_ha_down_first`'s `docker start` of a stopped
  HA comes before the facts and needs no tree.

Tests (4 of 4 failed before, 1 control):
`test_a_recovery_off_the_expected_branch_commits_deploys_and_restarts_nothing[litellm|homeassistant × --detach|-b work]`
(the tester's X1 inputs: exit 2, no mutating call, every ref unchanged,
OUT OF STEP naming the detached HEAD or the branch); control
`test_a_stopped_ha_is_still_started_off_the_expected_branch`.

## H2 (X2, Y1): a read that fails is unknown, never a fact

What changed:
- `:890` `ReadUnknown(what, err)`. Raised in a recovery by: `:1364`
  `production_tag(strict=True)` when `deploy_tag.sh status` fails;
  `intent_facts` when `docker inspect` of the component's container fails,
  when a git read fails (`head_pin(strict=True)` `:2061`,
  `tree_state(strict=True)`, a tag lookup exiting other than 0 or 1);
  `:2303` `unexplained_commits` when `git merge-base --is-ancestor` exits
  other than 0 or 1; `:2592` `_recovery_guards` when the config-hash label,
  `compose config --hash`, the start time and mounts
  (`bind_files_unreleased(strict=True)` `:1398`) or `compose config`
  (`ha_files_ok(strict=True)` `:1808`) cannot be read; `_ha_down_first`
  (`:2443`) when its own inspect fails. `recover` (`:2365`) turns any of
  these, and any other `StepFailed`/`OSError` of the reads, into the
  outcome `unknown` (`:2413`).
- `:2686` `_recovered`, `unknown`: nothing more is done, the record is
  kept, the `/services` warning and one notice (`notice_once`, `:2417`,
  keyed by what could not be read) say "could not read <what>: <error>;
  will look again", exit 1. The next monthly firing looks again.
- `:2578`: "someone else deployed in between" only when production was
  read running another tag. Production read and on no tag: OUT OF STEP
  "production's orchestrator runs no tag (…)".
- Blocks keep the record. `:1001` `block()` marks the record `blocked`;
  `_recovered` drops the record only for `recovered` (a finished recovery
  or "nothing was out of step"); `:2074` `apply_one` keeps the record of a
  run's own OUT OF STEP or ROLLBACK FAILED. `:2376` `recover` does nothing
  for a blocked record (the run then declines on the block);
  `:1343` `run_declines_blocked` also declines on a blocked record alone.
  The hourly command starts a stopped HA behind a block (H3).
- `orchestrator/upgrades_api.py:28` `summarize`: a blocked record's
  warning says OUT OF STEP / ROLLBACK FAILED and `ack`, not "the next
  monthly run recovers it". `scripts/deploy_tag.sh:298`, `:301`: a blocked
  record is refused with the `ack` way out only.

Tests (8 of 9 failed before; the ninth pins mutant `r11`'s sentence, which
was already right): `test_an_unreadable_production_tag_is_unknown_not_someone_elses_deploy`
(the tester's X2 input: exit 1, no mutating call, record kept and not
blocked, no block, the notice "could not read production's tag
(deploy_tag.sh status): Cannot connect to the Docker daemon …; will look
again"; the next night the read works and the rollback is finished by
deploying `sprint-X`), `test_production_on_no_tag_is_out_of_step_without_blaming_a_deploy`,
`test_a_failed_inspect_of_a_stopped_ha_is_unknown_and_the_hourly_start_still_comes`
(the tester's Y1 first input), `test_a_failed_docker_start_in_the_monthly_recovery_is_retried_by_the_hourly_command`
(Y1 second input: ROLLBACK FAILED, record blocked, the hourly command
starts HA, the next night declines and touches nothing),
`test_a_git_read_that_fails_is_unknown`, `test_a_failed_inspect_with_production_on_the_previous_tag_is_unknown`,
`test_a_failed_rollback_in_a_run_keeps_the_record_so_a_stopped_ha_is_started`,
`test_a_blocked_record_is_shown_as_needing_ack_by_the_api_and_deploy`;
`test_someone_elses_deploy_is_said_only_when_another_tag_was_read` (kills
`r11`, below). Changed for the ruling:
`tests/test_m18_fix2.py::test_a_tag_a_person_deployed_after_the_kill_is_never_deployed_over`
now expects the record kept, blocked.

## H3 (Y2, Y3, Y6): the hourly command

What changed:
- `:1865` `_ha_steps`: the run's own `docker stop` of HA writes
  `ha_stopped: true` into the record (the upgrade's stop, the rollback's,
  a recovery's rollback). `:3652` `hourly_start_ha` starts a stopped HA
  only for HA's record with that flag; for any other record it does
  nothing but warn. An inspect that fails is the state "unknown".
- `:3682` `hourly_notice` with `:2417` `notice_once`: at most one notice
  per record per distinct state (`interrupted`, `ha-started`,
  `ha-start-failing`, `unknown`), the keys kept in the record's `notices`.
  Every state's notice counts as the `interrupted` one; a start that works
  after failing, or fails after working, is reported once more. A start
  that keeps failing is tried every hour and reported once.
- `:3649` `HOURLY_START_TIMEOUT_S, HOURLY_HA_WAIT_S = 120, 240`;
  `:2427` `start_stopped_ha(timeout, wait_s, started)`: the hourly command
  writes the status (`ha_started_at` and the warning "the hourly command
  started it at … and is waiting for it to answer") between the start and
  the wait. Worst case with every ha-api GET taking its full 15 s: 120 +
  25 × 15 + 24 × 10 = 735 s. `systemd/kronk-upgrade-notices.service:25`
  `TimeoutStartSec=20m` (was 5m): 465 s of margin.
- `_recovered`'s "rollback waiting" notice has its own key, `deferred`.

Tests (5 of 5 failed before or kill a mutant):
`test_the_hourly_command_does_not_start_an_ha_the_run_never_stopped` (the
tester's Y3: record at `ha-pull`, a person stops HA, three hours: no start,
one notice), `test_a_start_that_keeps_failing_is_tried_every_hour_and_reported_once_then_once_when_it_works`
(Y2: five hours failing, one notice; then one "started again"),
`test_the_hourly_wait_for_ha_fits_inside_its_unit_and_the_status_is_written_first`
(the arithmetic against the unit file, and a kill in the wait leaves
`ha_started_at` and the warning), `test_the_nightly_rollback_waiting_notice_is_sent_after_the_hourly_one`
(Y6), and `test_a_start_that_fails_again_after_working_is_reported_again`
(added after the self-check; passes on the code, kills `h3-transition`).
Changed: `tests/test_m18_fix2.py::test_the_hourly_unit_starts_a_stopped_ha_and_says_so_within_the_hour`
reads `notices` instead of the old `noticed` timestamp.

## H4 (Y4): the replay's own guard

What changed: `:1594` `run_replay`: a row that comes back with
`player_change` (what `voice_replay.py` writes on the row after which a
player changed, then exit 3) is a failed verify at once, no retry. The
retry is only for a stop with no such row; a retry counts as complete only
when every row it selected has a result. `:2173`: a kept upgrade whose
replay was stopped and retried carries "the live replay was stopped by a
player N time(s) and retried for the rows it had not reached" in its
outcome, so in the notice line.

Tests (4 of 4 failed before):
`test_a_row_that_changed_a_player_fails_verify_at_once[litellm|homeassistant]`
(the tester's input: rolled back, no five-minute sleep),
`test_a_retry_that_wrote_only_some_of_its_rows_is_not_complete` ("wrote 1
of 3 selected row(s)"), `test_a_kept_upgrade_whose_replay_was_stopped_and_retried_says_so`.

## H5 (Y5): an absent HA

What changed: `:2443` `_ha_down_first`, absent container, before anything
is done: the run's tree checks (`tree_state`: branch, detached, clean),
`unexplained_commits`, git at `from` or `to`, the config hash the record
has for that pin (`hash_to` or `hash_from`) against `compose config
--hash` of the file at HEAD, no repo file bind-mounted by HA's compose
file changed since the run started (the record's `run` time; HA binds no
repo file today), and H7b; whatever the pin on disk says. Any of the first
four failing: HA stays absent, OUT OF STEP, "Home Assistant is down",
"<container> was not created; nothing was touched". H7b failing: ROLLBACK
FAILED as before. Only then the pin-back commit (wrapped: a failure there
is OUT OF STEP, not a traceback) and the create. `:2534` `_oos_todo`:
"nothing was touched" only when true of the whole recovery; after HA was
started it reads "nothing else was touched".

Tests (5 of 5 failed before, 1 control):
`test_an_absent_ha_is_not_created_from_a_file_the_run_never_checked[commit|uncommitted|env file|detached]`
(the tester's Y5 with `privileged: true` committed or not, an env change
only the hash sees, a detached HEAD: no `compose up`, HA absent, OUT OF
STEP, record blocked, title `Kronk upgrade: OUT OF STEP (homeassistant):
Home Assistant is down`), `test_nothing_was_touched_is_said_only_when_nothing_was`;
control `test_a_clean_absent_ha_is_still_created`.

## H6 (Y7): one source for the branch

What changed:
- `:156` `load_rules`: `expected_branch` from the rules file (`main`,
  unchanged), overridden by `KRONK_UPGRADE_BRANCH` from the environment,
  else from `~/.config/kronk/upgrade.env` (`:145` `branch_file`,
  `$XDG_CONFIG_HOME` honoured; one `KRONK_UPGRADE_BRANCH=<name>` line, a
  pair of quotes stripped as systemd would). `:187`: the name must pass
  `git check-ref-format --branch` and come back unchanged, so `/` works and
  `HEAD`, `-x`, spaces, `..`, `@{-1}`, a newline are refused.
- `systemd/kronk-upgrade-minor.service`, `kronk-upgrade-major-check.service`:
  no `Environment=KRONK_UPGRADE_BRANCH` line. The install writes the file
  and one drop-in per unit, `kronk-upgrade-<u>.service.d/branch.conf`,
  holding `EnvironmentFile=%h/.config/kronk/upgrade.env`.
- `scripts/upgrade_rules.toml` (the `expected_branch` comment), the
  feature doc's "Installing the timers", the minor unit's Operations block.

Two choices inside the ruling, stated for the coordinator: the drop-in
points at the file instead of repeating the name, so the branch is written
in one place and systemd's `%` specifiers and quoting never touch it (git
allows `%` in a branch name); and `scripts/upgrades.py` reads the file
itself rather than the wrappers sourcing it, so `python3
scripts/upgrades.py minor --ignore-window` (which `deploy_tag.sh`'s refusal
names) agrees with `scripts/upgrade_minor.sh` and the timer, and the file
is parsed as one line, never run as shell.

Tests (10 of 10 failed before):
`test_the_branch_comes_from_the_rules_the_file_outside_the_repo_or_the_variable`,
`test_a_branch_name_git_would_refuse_is_refused[×7]`,
`test_a_by_hand_dry_run_agrees_with_the_timer_through_the_file` (a repo on
`feature/m18`, the file saying so: "a real run now would go ahead", no
branch reason), `test_the_units_carry_no_branch_and_the_drop_in_points_at_the_one_file`.
Changed: `tests/test_m18_units.py::test_services_run_the_repo_scripts`
asserts the units carry no branch.

The install commands were run as written on copies, with `HOME` set to a
scratch directory (no `systemctl` call against the user manager): `git
check-ref-format --branch refactor` printed `refactor`; the file and both
drop-ins were written; `systemd-analyze --user verify` on the six copied
units with their drop-ins: exit 0 (with the two `ExecStart` targets
stubbed as plain files, since `%h` was the scratch directory); a bogus key
added to a drop-in was reported (`branch.conf:3: Unknown key 'BogusKey'`),
so verify reads the drop-ins; `/usr/bin/python3` loading the rules under
that `HOME` read `refactor`. The removal commands then left no file under
the scratch `~/.config`, and the rules read `main` again. The real
`~/.config/systemd/user` holds no `kronk-upgrade-*` file and
`~/.config/kronk` does not exist, before and after.

## H7 (Y8): signal 9 from the stopped container

What changed: `:1883` `ha_stopped_cleanly`, called by `_ha_steps` right
after `docker stop -t 120` and before `compose up`: `docker inspect -f
'{{.State.ExitCode}} {{.State.OOMKilled}}'`; 137 or `true` is a failed
stop ("HA did not shut down inside the 120 s grace period (exit code 137,
OOMKilled false): killed with signal 9"); a read that fails or does not
parse is a failed check. The `docker events` read in `:1901` `ha_checks`
is removed; the unclean-shutdown log line check stays. `:1995`
`ha_rollback` no longer stops a container that is already stopped (its
exit code is the earlier stop's, already judged), so a failed stop rolls
back without a second verdict on the same exit code.

Tests (4 of 4 failed before):
`test_ha_killed_at_the_end_of_its_grace_period_fails_the_stop_before_the_new_one_is_created[137|OOM]`
(rolled back, the upgrade's `compose up` never ran, one `docker stop`, no
`docker events`), `test_a_stop_whose_exit_code_cannot_be_read_is_a_failed_check`,
`test_a_clean_stop_passes_and_no_docker_events_is_read`. The fake reports
a stopped container's exit code (`stop_exit`, `oom_killed`; `kill_signals`
removed); `tests/test_m18_fix2.py`'s `o34` case and
`test_a_failed_docker_events_read_fails_the_upgrade` are replaced by these.

## H8 (Y9): `ack`

What changed: `:3712` `ack_leaves`, `:3812`: with a record, `ack` prints
the record, what git has, what runs and, for a Kronk-stack record,
production's tag. Without `--yes-leave-as-is` it refuses (exit 1, nothing
written) for HA's record while HA is stopped, absent or cannot be
inspected, and for a Kronk-stack record while production runs the run's
unverified tag or its tag cannot be read. With the flag it clears and ends
"left as is: <what>". The check on HA is made for HA's record only: for
another component's record, `ack` changes nothing that would bring HA
back.

Tests (3 of 3 failed before):
`test_ack_refuses_while_ha_is_stopped_and_says_what_it_leaves_with_the_flag`,
`test_ack_refuses_while_production_runs_the_runs_unverified_tag`,
`test_ack_with_nothing_dangerous_clears_and_still_says_what_runs`.

## H9: the observations ruled on

- Schema. `orchestrator/upgrade_status.py` (new, standard library only):
  the type of every field the script or the API reads, counts
  non-negative, outcomes closed to `Outcome`'s fields, the record's
  component, from, to and step required, depth at most 8, measured without
  recursion. `scripts/upgrades.py:112` loads it by path (no `sys.path`
  change) and `:923` `load_status` uses it (`status_shape` is gone; a JSON
  nesting error is corrupt, not a traceback).
  `orchestrator/upgrades_api.py:77`: any file off the schema is one warning
  "upgrade status file unreadable: …", and the route never raises.
  `scripts/deploy_tag.sh:292`: the same module, so `{"intent": {}}` is
  refused there too. Tests (12 of 12 failed before, 1 control):
  `test_a_status_file_off_the_schema_is_corrupt_for_the_script_and_one_warning_for_the_api[0-10]`
  (the report's residue: a `components` entry that is a string,
  `intent.head` a number, an outcome's `detail` a number,
  `rollback_failed.detail` a number, `attempts: -5`, `{"intent": {}}`,
  `attention` strings, a negative notice count, an outcome with an extra
  key, 3000 nested lists, a JSON list; `minor`, `notices`, `ack`, `status`
  exit 2 with the file untouched; the API one warning),
  `test_an_empty_intent_is_corrupt_for_deploy_tag_too`; control
  `test_every_file_the_script_writes_fits_the_schema`.
- Loader. `:136` `_fold_project`, `:140` `_same_file`: project names
  compared with case and `_`/`-` folded, compose files by resolved path,
  case-insensitively. Tests (6 of 6 failed before):
  `test_the_loader_folds_case_separators_and_paths[×6]` (an absolute path
  to the Kronk stack's file, `Docker-Compose.yml`, `DOCKER-COMPOSE.MA.YML`,
  `kronk_ma`, `Kronk_MA`, `Docker-Compose.MA.yml` with a deploy).
- `:991` `sweep_status_temps` skips directories. Test (failed before):
  `test_a_status_temp_directory_is_ignored_silently`.
- `:2548` `_recover_one`: a component with no container (the Python base
  images) is recovered with nothing to do while git has either of the
  record's versions, OUT OF STEP only when it has neither. Tests (2 of 3
  failed before, the third a control):
  `test_a_record_for_a_component_without_a_container[3.12.8-slim|3.12.7-slim|3.12.9-slim]`.
- The player list: `scripts/upgrade_rules.toml:124` the three browser
  players the report names, with a comment; the rest unchanged. Test (3
  of 3 failed before):
  `test_a_browser_player_coming_online_during_the_restart_does_not_fail_the_upgrade[×3]`.

Left as they are, as ruled: a kill between a notice and its status write
sends that notice again (K3); a pulled image with no `RepoDigests` passes
the digest check unseen; the fake's `deploy_tag deploy` does not create a
missing container; the O6 disk-full residue (the hourly command's own
write still ends in a traceback on a full disk); a recovery exits 1 and
ends the night's run (now said in the feature doc).

## Mutant `r11` and the self-check

`r11` (the sentence "someone else deployed in between" removed) is killed
by `test_someone_elses_deploy_is_said_only_when_another_tag_was_read`. The
third report's table ranks no survivor above `r11` (it is the first row),
so no other survivor was in scope.

The self-check, as the testers work, on the code of this round, all in the
session's scratch directory with the fakes of `tests/m18_helpers.py`,
sockets refused in-process and real git in temporary repositories:

1. States (`sweep.py`). Seeds: litellm killed in verify and before the
   deploy; grafana in verify; HA in verify, after the stop, with the
   container gone after the stop, and after the pull. Crossed with git
   (as left, detached, another branch, an untracked file, a person's
   commit, an uncommitted pin edit), production's tag for the Kronk stack
   (as left, the previous tag, a third tag, unreadable always, unreadable
   once) and the container (as left, from, to, stopped, absent, inspect
   failing always, once): 630 states, each through a night, an hourly
   firing, a night, an hourly firing. Checked after each firing: a deploy
   only of the previous tag while production ran the run's own; off the
   expected tree no ref moves and nothing but `docker start homeassistant`;
   "someone else deployed" only with a third tag read; "nothing was
   touched" never after a mutating call; the record dropped only by a
   recovery; an `unknown` outcome keeps it; the hourly start only after the
   run's own stop; no "does not exist", "is stopped" or "Home Assistant is
   down" for a running container whose read failed; at most one hourly
   notice a firing and none repeated; HA not left stopped behind its
   record. Result: 0 problems. The same harness on round 2's code
   (`1d12f55`) finds 595 states with problems (74 ref moves and 50 actions
   off the expected tree, 84 "someone else deployed" without a third tag,
   18 "nothing was touched" after a start, 525 records dropped), so it can
   see what it checks.
2. Failed reads and kills (`sweep2.py`). For every seed, each of the
   first 400 calls of the night and the hourly firing after it: the call
   (if a read: docker inspect, image inspect, logs, exec, `compose config`,
   `deploy_tag.sh status`, git, HA's API, the probes) failing once, failing
   always, and a kill before it and after it; then the remaining firings.
   11200 scenarios: 2 flagged, both the same call, `git rev-parse -q
   --verify refs/tags/sprint-X` given exit 1. Exit 1 is git's own answer
   for "no such tag" (a failure exits 128), and the code reads it as that
   answer on purpose: a harness artifact, not a fault. Rerun of the failed
   reads with git failing at 128 (`sweep3.py`): 5600 scenarios, 0
   problems. No traceback in any scenario.
3. Mutation (`mutate.py`, a `git archive` copy): 36 mutants, one per new
   guard of this round and `r11`, the M18 test files with `-x`: 35 killed
   at first; the survivor `h3-transition` (a second failure after a start
   that worked not reported again) is killed by the test added in
   `37870fa`. 36 of 36.

What the self-check found that is not a failure but worth knowing:
- "Look again at the next firing" for a recovery means the monthly timer's
  firings, days 1 to 7. An `unknown` on the 7th waits for the 1st of next
  month or a run by hand; the warning and one notice stand meanwhile, and
  the hourly command still starts a stopped HA the run had stopped.
- The monthly recovery's HA-down-first step still starts a stopped HA for
  HA's record at any step (G2, unchanged); H3's step condition was ruled
  for the hourly command and is applied there only.

## The recovery table as it now stands

Order. (0) A blocked record: nothing (the run declines on the block; only
the hourly start of an HA the run stopped acts behind it). (1) HA's record
and HA's container: stopped, `docker start` (no tree needed); absent,
created only past the tree checks, the config hash, the bind-mount guard
and H7b, else OUT OF STEP with HA down; the inspect failing, unknown.
(2) Any read below failing: unknown. (3) No container: recovered (git at
either version) or OUT OF STEP. (4) HEAD detached, another branch, the tree
not clean, unexplained commits, git at neither version: OUT OF STEP.
(5) The table:

| Component | Production / container | git | Done |
| --- | --- | --- | --- |
| no container | — | from or to | recovered: nothing to recover |
| no container | — | neither | OUT OF STEP |
| Kronk stack | production on the run's own tag, the recorded previous tag exists | to or from | inside the night window, house quiet: pin back by a commit if git has `to`, deploy the previous tag, verify; otherwise one notice ("rollback waiting", once) and the warning, wait for the next night |
| Kronk stack | production on the run's own tag, the previous tag gone | any | OUT OF STEP |
| Kronk stack | production on the recorded previous tag, the component on `from` | to | pin back by a commit; nothing restarted |
| Kronk stack | the same | from | recovered, "nothing was out of step" |
| Kronk stack | production on the previous tag, the component on anything else | any | OUT OF STEP |
| Kronk stack | production read on another tag | any | OUT OF STEP, "someone else deployed in between" |
| Kronk stack | production read on no tag | any | OUT OF STEP, "production's orchestrator runs no tag" |
| Telemetry, HA | `from` runs | to / from | pin back, nothing restarted / "nothing was out of step" |
| Telemetry, HA | `to` runs; label hash is the record's `hash_to`; the file at HEAD gives `hash_to` (git `to`) or `hash_from` (git `from`); no bind-mounted file newer than the start; HA: H7b | to or from | inside the window, house quiet: pin back if git has `to`, `up -d` (HA: stop, exit code checked, `up -d`, by the recorded image id), verify; otherwise wait as above |
| Telemetry, HA | `to` runs and any guard fails | any | OUT OF STEP |
| Telemetry | not running, or another version | any | OUT OF STEP |
| any | production's tag, an inspect, git, a hash, mounts or `compose config` cannot be read | any | unknown: nothing done, the record kept, one notice, look again next firing |
| any | the recovery's rollback, HA's start or HA's create fails | any | ROLLBACK FAILED |

OUT OF STEP and ROLLBACK FAILED keep the record, blocked, raise the
warning, send one notice and block later runs until `ack`. The record is
dropped only by `recovered` or by `ack`. A recovered component is held
back for the month. `deploy_tag.sh deploy` refuses while any record
exists. The hourly command, between runs: the warning; one notice per
state; `docker start` of a stopped HA only for HA's record after the run's
own stop, blocked or not; nothing else.

## The new worst case for HA down

- HA stopped by the run (from its `docker stop` in the upgrade, the
  rollback or a recovery's rollback, until its verified start): the next
  hourly firing starts it, blocked or not, any day of the month. The timer
  fires on the hour with `RandomizedDelaySec=5m`: about 65 minutes at
  worst, plus HA's own start. The hourly start then gives `docker start`
  2 minutes and HA 4 more to answer: 735 s at worst with every GET timing
  out, inside the unit's 20 minutes.
- A `docker start` that fails: tried every hour, reported once (and once
  more when it works). In the monthly recovery it is ROLLBACK FAILED with
  the record kept, so the hourly command retries it.
- A read of HA's container that fails: unknown, nothing done; the hourly
  command looks again the next hour.
- What remains uncovered, as before: HA's container absent (a `compose up`
  cut between removing the old container and creating the new one) is
  created only by the monthly run (days 1 to 7; after a kill on the 7th,
  the 1st of next month, about 25 days) or a run by hand, and now only past
  the tree checks and guards; HA stopped by a person while the record's
  step was before the run's own stop is not started by the hourly command;
  the hourly unit itself not running.

## Install and uninstall, for the coordinator

Before: the working tree clean and on the branch production's tags are
made from (today `refactor`: `sprint-M21-step2` is not an ancestor of
`main`). Production's `GET /api/upgrades` answers 404 until a tag with M18
is deployed; until then the `/services` warning is not there, and the
Kronk-stack components are `not started` every night (HEAD has undeployed
changes).

```
cd ~/git-repos/drawsmcgraw/kronk
B=refactor
git check-ref-format --branch "$B"          # prints the name back, or refuses it
git status --porcelain                      # empty
git rev-parse --abbrev-ref HEAD             # prints the same name as $B
mkdir -p ~/.config/kronk
printf 'KRONK_UPGRADE_BRANCH=%s\n' "$B" > ~/.config/kronk/upgrade.env
cp systemd/kronk-upgrade-{minor,major-check,notices}.{service,timer} ~/.config/systemd/user/
for u in minor major-check; do
  mkdir -p ~/.config/systemd/user/kronk-upgrade-$u.service.d
  printf '[Service]\nEnvironmentFile=%%h/.config/kronk/upgrade.env\n' \
    > ~/.config/systemd/user/kronk-upgrade-$u.service.d/branch.conf
done
systemd-analyze --user verify ~/.config/systemd/user/kronk-upgrade-*.{service,timer}
systemctl --user daemon-reload
systemctl --user show -p EnvironmentFiles kronk-upgrade-minor.service   # names upgrade.env
systemctl --user enable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
systemctl --user list-timers 'kronk-upgrade-*'
python3 scripts/upgrades.py test-notice     # the one real notice, marked TEST
scripts/upgrade_minor.sh --dry-run          # reads only; its DECLINE line names no branch
```

The same block serves `main`: the file then says `main`. To change the
branch later, edit the one line in `~/.config/kronk/upgrade.env`.

Remove:

```
systemctl --user disable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
rm ~/.config/systemd/user/kronk-upgrade-minor.service ~/.config/systemd/user/kronk-upgrade-minor.timer \
   ~/.config/systemd/user/kronk-upgrade-major-check.service ~/.config/systemd/user/kronk-upgrade-major-check.timer \
   ~/.config/systemd/user/kronk-upgrade-notices.service ~/.config/systemd/user/kronk-upgrade-notices.timer
rm ~/.config/systemd/user/kronk-upgrade-minor.service.d/branch.conf \
   ~/.config/systemd/user/kronk-upgrade-major-check.service.d/branch.conf
rmdir ~/.config/systemd/user/kronk-upgrade-minor.service.d ~/.config/systemd/user/kronk-upgrade-major-check.service.d
rm ~/.config/kronk/upgrade.env
systemctl --user daemon-reload
```

`data/upgrades/` (status, lock, run logs) stays on removal. After an
interrupted run, do not deploy by hand: `scripts/upgrade_minor.sh
--ignore-window` recovers it (not a blocked one); `python3
scripts/upgrades.py ack` drops the record, and says what it leaves.

## Still Drew's

Unchanged, as ruled: the absent-container gap for the hourly command; the
48-hour grace; a recovered component held back for the month; the
modification-time guard; the player list's contents (three entries added
as ruled); F11. Also open, as before: how a notice reaches him when HA is
what failed (the `/services` warning is the built default); whether K6's
retry should be narrowed to the two reasons the ruling named; and, new,
whether the monthly recovery should hold HA's `docker start` to the same
step condition as the hourly command (above).

## Live work

### Dev pair (the orchestrator changed)

`docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev`, 16:53:31 to 16:53:33 UTC (their `depends_on` stays
inside the pair: checked with `compose config` first):
`kronk-orchestrator-dev-1` recreated (new id `f2e89dd25efc…`, started
16:53:33); `kronk-tool_service-dev-1` not recreated (same id), its image
tag moved to a cached rebuild (`ba87bdf64805`), as in round 2.
`GET /dev/api/upgrades`: HTTP 200,
`{"file":"/data/upgrades/status.json","present":false,"warnings":[],"last_run":null,"note":"no upgrade run has written a status file yet"}`.
In the container, `upgrade_status` imports and `upgrades_api` uses it.
`GET /dev/services`: 200, 13631 bytes. `GET /api/upgrades` (production):
404, as before promotion. nginx was not restarted. The schema and the
blocked-record warning were shown with the real module and temporary files
in the tests; no status file was written under `data/`.

### The rehearsal

`scripts/upgrade_rehearsal.py` read first (unchanged this round: compose
project `m18-rehearsal`, busybox, a scratch repository and state
directory), then run once, 16:53:57 to 17:04:55 UTC, in
`/tmp/claude-1000/-home-drew-git-repos-drawsmcgraw-kronk/e68b264f-b4be-4c86-b5c0-22307641e011/scratchpad/rehearsal`; neither busybox image nor the project existed before:

- A: dry run first (`a real run now would go ahead …`, `state dir exists
  after dry run: False`), then `committed 03b64ce: upgrade: busybox-scratch
  1.36.0 -> 1.36.1`, `kept for rollback: busybox:1.36.0 = sha256:9e2bbca0…`,
  `image pulled: busybox:1.36.1 = sha256:73aaf090…`, `verified:
  busybox-scratch 1.36.1 kept`, stub notice `Kronk upgrade: 1 upgraded`,
  exit 0.
- B: `FAILED: check … failed (exit 1)` at 16:59:32, `rolled back and
  verified: busybox-scratch 1.36.0` at 16:59:42, stub notice `Kronk
  upgrade: 1 rolled back`, exit 1, `busybox:1.36.0` running.
- C: `ROLLBACK FAILED: compose up of 1.36.0 failed: … No such image:
  busybox:1.36.0`, stub notice `ROLLBACK FAILED; running is 1.36.1; git has
  1.36.0; needs you: …`, exit 2. The next run: `an earlier run's record
  (busybox-scratch 1.36.0 -> 1.36.1, step 'rollback') is blocked
  (rollback_failed): no recovery acts on it until …`, then `declined: the
  last rollback failed (…)`, exit 0 (the record kept, H2). `ack`: `the
  record: busybox-scratch 1.36.0 -> 1.36.1, step rollback, run
  20261118T165943Z, blocked (rollback_failed)`, `cleared: 1 warning(s), the
  failed-rollback block, the interrupted run's record …`; `rollback_failed
  after ack: None`. (`ack` loads the shipped rules, which do not list the
  rehearsal's component, so it prints no git or running line and has
  nothing to refuse.)
- Cleanup: `busybox:1.36.0 absent`, `busybox:1.36.1 Untagged`, `left:
  nothing`.

Real: Docker Hub lookups and pulls of busybox, git in a scratch
repository, the lock, status and log in a scratch state directory, compose
`config --hash`, `pull`, `up`, `inspect`, the `exec` health check. Stubbed:
HA's states, the voice_tracer probe, nginx's log, the corpus, the replay,
the notifier. No interrupted run and no HA stop is rehearsed (those paths
are in the unit tests and the self-check).

### The dry run

`scripts/upgrade_minor.sh --dry-run`, once, 17:05:04 to 17:05:17 UTC, exit
0; 76 lookups to `api.github.com`, `ghcr.io`, `hub.docker.com`, `pypi.org`;
`data/upgrades` absent before and after; `git status` unchanged (this
report, uncommitted then). Nothing is installed, so no `upgrade.env`
exists and the rules' `main` stands: the branch reason is the expected one
until the install's file says `refactor`. Last ten lines:

```
17:05:17Z would do, in order (each its own commit; verify = image, health, corpus, replay, stack checks):
17:05:17Z   grafana: docker-compose.telemetry.yml: 13.2.4 -> 13.2.5; commit 'upgrade: grafana 13.2.4 -> 13.2.5'; compose -p kronk-telemetry pull grafana; up -d --no-deps grafana (rollback: pin back, up -d with the local previous image)
17:05:17Z   litellm: docker-compose.yml: v1.88.1 -> v1.88.6; commit 'upgrade: litellm v1.88.1 -> v1.88.6'; docker pull ghcr.io/berriai/litellm:v1.88.6 (digest checked); git tag upgrade-<run time>-litellm; deploy_tag.sh deploy it (a full production redeploy; rollback: pin back, deploy sprint-M21-step2, another)
17:05:17Z then: one notice through scripts/lib/notify.sh with what changed; the status file data/upgrades/status.json for /services
17:05:17Z production runs tag: sprint-M21-step2
17:05:17Z HEAD vs sprint-M21-step2: undeployed changes in orchestrator/main.py, orchestrator/static/services.html, orchestrator/upgrade_status.py, orchestrator/upgrades_api.py (Kronk-stack components would be declined)
17:05:17Z docker-compose.telemetry.yml vs running kronk-telemetry-grafana-1: matches (config hash)
17:05:17Z bind-mounted files of kronk-telemetry-grafana-1: none changed since it started
17:05:17Z a real run now would DECLINE: outside the night window 02:00-05:00 local (now 12:05): the timer tries the next night; on branch 'refactor', expected 'main' (KRONK_UPGRADE_BRANCH / expected_branch); working tree not clean (1 path(s): docs/sprint/M18-fix3.md); media_player.home_assistant_voice_0ac919_pe_01 is playing; media_player.home_assistant_voice_0ac919_media_player is playing
17:05:17Z dry run: nothing written, nothing changed, nothing sent
```

## Before and after

Before (16:52 UTC, after the code commits, before any live step): every
container's name, id, image, start time and status; `docker images`;
`docker compose ls -a`; `git tag`; the files in `~/.config/systemd/user`;
`data/upgrades` and `data/dev/upgrades` (both absent), recorded in the
scratch directory.

After (17:05 UTC, after the rehearsal's cleanup and the dry run): the same
records differ only in `kronk-orchestrator-dev-1` (recreated by the dev
rebuild) and the two dev image tags. Every production container, Home
Assistant, Music Assistant and the telemetry stack: same id, same start
time. Compose projects, git tags (no tag added) and the user unit
directory: identical. `~/.config/kronk` does not exist. `data/upgrades`
absent. No symlink in the scratch directory; the scratch install home was
removed; no background job running. Internet hosts contacted: those of the
dry run above, and Docker Hub for the rehearsal's lookups and pulls. No LAN
address, no `.local` name, nothing to production's chat or music routes,
nothing through HA, no notice.

## Tests

`./scripts/run_tests.sh -q -p no:cacheprovider` at `cf42bd5` (the code of
this round; this report changes no code):

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
5421 passed, 3 skipped, 53 xfailed, 6 warnings in 76.71s (0:01:16)
```

Before this round: `5351 passed, 3 skipped, 53 xfailed`. 72 new cases in
`tests/test_m18_fix3.py`, two fix-2 cases removed as replaced by H7's: 70
more in all. The six warnings are the
`PydanticDeprecatedSince20` lines of the earlier reports.
