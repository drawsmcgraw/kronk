# M18 fix round 2: an upgrade cadence

Status: fix round 2 complete; waiting for the third end-to-end test.

Fix agent, 2026-11-18 (UTC), branch `refactor`, fix round 2 after
`docs/sprint/M18-e2e2.md` failed M18. Inputs: CLAUDE.md,
`docs/sprint/M18-criteria.md`, `docs/sprint/M18-fix1.md`,
`docs/sprint/M18-e2e2.md` (N1, N2, K1 to K13, O1 to O8) and the
coordinator's rulings G1 to G5. Nothing was upgraded, pulled into a
production tag, recreated in production, restarted in production or
installed; no notice was sent (every notifier stubbed; `test-notice` not
run); no `deploy_tag.sh deploy` was run against anything real. The
orchestrator's `upgrades_api.py` changed, so the dev pair was rebuilt by
name (below); production was not touched.

## Commits

| Commit | Item |
| --- | --- |
| `c4d1cb5` | G4 (N2): what runs is runs, stopped, absent or unknown |
| `4bbd65b` | G1 (N1): a recovery acts only when the world is what the record says |
| `e3ee4d6` | G2 (K1, K2): HA down first, by `docker start`; the hourly unit; the API warning |
| `151c25c` | K3: outcomes written as each component ends; the next run reports them |
| `8d13061` | K4: flapping players; a rollback reports players, never fails on them |
| `71d2f11` | K5: a replay stopped by a player during verify is retried |
| `7a7e1cc` | K6: components not started go the next night |
| `98760cd` | K7: the Kronk-stack image is pulled and checked before the tag and deploy |
| `b58c648` | K10: the quarterly check waits 20 minutes for the lock |
| `cf547c2` | K11: a status file not UTF-8 or of the wrong shape is StatusCorrupt |
| `2a2cb60` | K12: a check whose own read fails is a failed check |
| `b27ff9d` | G3: `deploy_tag.sh deploy` refuses while a record exists; `ack` drops it |
| `4994884` | O1: leading zeros and non-ASCII digits are unparseable |
| `e223fb8` | O2: port 443 only; the token stays on its host; a release by tag carries the tag |
| `4302514` | O3: the status write cleans up; leftovers older than a day are swept |
| `a1c170b` | O4: the loader strips nothing and refuses look-alikes |
| `dfcbaee` | O6: a failed status write is a clean stop with a notice attempt |
| `7e6cd20` | tests for HA's restart checks one at a time (survivors `o33` to `o37`) |
| `e72bf71` | K8, K9, K13: the feature doc, the minor unit's Operations block, the docstring |
| this commit | this report |

## How "failed before" was shown

Each fix's tests were written first and run with the touched production
files as they stood at the previous commit (`git show HEAD:<file>`
swapped in, then restored), with the new fakes in `tests/m18_helpers.py`.
The counts below are from those runs. A "control" is a test of the same
fix that passes before and after on purpose (the behaviour it guards was
already right and must stay so). New tests are in `tests/test_m18_fix2.py`
(67 test functions, 104 cases), all under the `no_network` fixture,
temporary directories and temporary git repositories with their own user.

## G4 (N2): three answers for what runs

What changed:
- `scripts/upgrades.py:1955` `container_state`: `runs` (with the version),
  `stopped`, `absent` (only when Docker says "No such container" / "No
  such object", `NO_SUCH` at `:1952`), `unknown` (the inspect itself
  failed: "unknown (cannot inspect homeassistant: <error>)"), `none` (no
  container to inspect). "Home Assistant is down" appears in the phrase
  only for `stopped` and `absent`. `what_runs` (`:1980`) is a wrapper.
- `:2198` `pin_vs_running`: a container that cannot be inspected is a
  `SKIP` ("cannot tell what <container> runs: …"), not a `MISMATCH` and no
  mismatch notice.

Tests (4 of 4 failed before):
`test_what_runs_has_three_answers_and_says_does_not_exist_only_when_docker_does[ha|kronk]`,
`test_a_failed_inspect_never_reads_as_home_assistant_down` (the tester's
input: inspect failing from HA's bring-up on; title now `Kronk upgrade: 1
not applied`, line `running is unknown (cannot inspect homeassistant:
injected failure)`), `test_a_pin_whose_container_cannot_be_inspected_is_skipped_not_a_mismatch`.

## G1 (N1): the recovery table

What changed:
- The intent record (`:2048`, `:2064`) also carries `head` (HEAD before
  the pin commit) and, for telemetry and HA, `hash_from` (the config hash
  of what ran, which the run's guard had just matched) and `hash_to` (the
  config hash at the pin commit). A config hash that cannot be read after
  the pin commit is "not applied".
- `:1407` `config_hash`, `:1417` `label_hash` (split out of
  `compose_hash_ok`).
- `:2222` `unexplained_commits`: HEAD must descend from `head`, and every
  commit since must be the upgrade author's (`upgrade_minor@kronk.invalid`)
  with a subject for this component (`upgrade: <name> `, `upgrade: roll
  back <name> `, `upgrade: not applied, <name> `) or a quarterly report
  (`upgrade report: `). Anything else is named.
- `:2387` `_recover_one`, `:2428` `_recovery_guards`, `:2457`
  `_pin_back_only`, `:2479` `_finish_rollback`: the table below. Every OUT
  OF STEP detail ends "nothing was touched; put what should run back by
  hand, make git's pin match it, then `python3 scripts/upgrades.py ack`".
- `tests/m18_helpers.py`: compose's config hash per service (the file's
  pin for that service plus `config_extra`), container labels set by `up`,
  start times set by `up`.

Tests (11 failed before, 3 controls passed): the tester's four variants
`test_a_tag_a_person_deployed_after_the_kill_is_never_deployed_over[HEAD|HEAD~1 × verify|deploy]`
(no deploy, no mutating call, HEAD unchanged, OUT OF STEP naming `sprint-Y`,
`sprint-X` and the run's tag, one notice `Kronk upgrade: OUT OF STEP
(litellm)`), `test_a_previous_tag_that_no_longer_exists_is_out_of_step`,
`test_a_commit_the_record_does_not_explain_is_out_of_step`,
`test_a_component_running_neither_version_while_production_runs_the_previous_tag_is_out_of_step`,
`test_a_telemetry_recovery_passes_the_config_hash_guard_or_touches_nothing`
(an env-file change), `test_a_telemetry_recovery_passes_the_bind_mount_guard_or_touches_nothing`
(the tester's provisioning file), `test_a_telemetry_container_recreated_by_a_person_is_not_rolled_back`,
`test_a_stopped_telemetry_container_is_out_of_step_not_started`. Controls:
`test_the_runs_own_tag_in_production_still_finishes_the_rollback`,
`test_the_previous_tag_running_puts_the_pin_back_and_restarts_nothing`,
`test_a_clean_telemetry_recovery_still_rolls_back`.

Mutant `i6` (the production-tag clause) no longer exists as written; its
two successors are killed (Mutation check, below).

## G2 (K1, K2): Home Assistant down first

What changed:
- `:2294` `recover` calls `_ha_down_first` (`:2322`) before the facts,
  the tree check and the table, for a record whose component deploys as
  `ha`. The monthly run calls `recover` before the window and month checks
  (as before), so this is also before them.
  - container stopped: `:2309` `start_stopped_ha`: `docker start
    homeassistant` (the same container; no compose, no recreate, no pin or
    git change), then HA's health checks (the `ha-api` check, retried 5
    minutes). Then the rest of the recovery runs on fresh facts: pin back
    by a commit when the tree allows, else OUT OF STEP with HA running. The
    outcome's state starts "Home Assistant was down (homeassistant was
    stopped); the same container was started again; …".
  - container absent: the compose file on disk must say `from`, or (it
    says `to`, HEAD is the pin commit, the tree is clean, no unexplained
    commits) the pin is put back by a commit first; M12 H7b
    (`ha_files_ok`); the recorded image id tagged `<image>:<from>` if
    needed; `compose up -d homeassistant` with no stop; health. Any of
    these failing: HA stays down, ROLLBACK FAILED (or OUT OF STEP for a
    pin that cannot be put back), blocked, notice and warning.
  - start or create failing: ROLLBACK FAILED, "Home Assistant is down".
- `:1930` `ha_rollback`: no `docker stop` when the container is absent
  (also the ordinary rollback's path).
- The hourly unit (`main`, `:3542`; `:3466` `hourly_start_ha`, `:3479`
  `hourly_notice`): when the status file has an intent record and the lock
  is free, it starts a stopped HA container (the record must be HA's), then
  retries pending notices, then raises the `interrupted` warning and sends
  one notice (`Kronk upgrade: a run was interrupted (<name>)`, with ":
  Home Assistant was down; started again" when it started HA). It does
  nothing else: no deploy, no compose, no pin or git change. While a run
  holds the lock it does nothing. `systemd/kronk-upgrade-notices.service`'s
  comment says so.
- `orchestrator/upgrades_api.py:31`: an unfinished record is the first
  warning, from the record itself, and the response carries `intent`. The
  `/services` card shows `warnings` as before; its code did not change.

Tests (9 failed before, 2 controls passed):
`test_a_stopped_ha_is_started_first_even_with_a_dirty_tree` (the tester's
K1: HA started, no stop, no compose up, HEAD unchanged, OUT OF STEP with HA
running), `test_a_stopped_ha_is_started_then_the_pin_is_put_back_by_a_commit`,
`test_a_missing_ha_container_is_created_on_the_rollback_image_without_a_stop`,
`test_a_missing_ha_container_is_not_created_when_h7b_fails`,
`test_a_rollback_never_stops_a_container_that_does_not_exist`,
`test_the_dry_run_says_it_would_start_ha_and_changes_nothing`,
`test_the_hourly_unit_starts_a_stopped_ha_and_says_so_within_the_hour`
(killed on the 7th at 03:30 local, the hourly 50 minutes later: HA
started, warning, one notice; the next hour nothing), 
`test_the_hourly_unit_only_warns_about_a_record_of_another_component`,
`test_the_api_shows_an_unfinished_record_as_a_warning`. Controls:
`test_ha_is_started_even_at_seven_in_the_evening_with_the_house_busy`,
`test_the_hourly_unit_does_nothing_while_a_run_holds_the_lock`. The fake
now fails `docker stop` and `docker start` of a missing container as
Docker does.

### The new worst case for HA down after a kill

- HA stopped (killed between the upgrade's or a rollback's `docker stop`
  and its `compose up`): back within the hour, any day of the month. The
  hourly timer fires on the hour with `RandomizedDelaySec=5m`; HA's own
  start time comes on top. Worst case about 1 h 05 min plus HA's start.
- What remains uncovered:
  - HA's container absent (a `compose up` cut between removing the old
    container and creating the new one). The hourly unit does not create
    containers (ruling: start only). The next monthly firing does it (days
    1 to 7 at 03:30); after a kill on the 7th, the 1st of next month: up to
    about 25 days, as the second report measured. A person can run
    `scripts/upgrade_minor.sh --ignore-window`. Whether compose can leave
    this state on a real container was not tried (the tester did not
    either).
  - `docker start` that fails, or an HA that starts and does not answer
    within 5 minutes: ROLLBACK FAILED, the warning on `/services`; the
    phone notice cannot arrive (it goes through HA).
  - The hourly unit itself not running (not installed, disabled, the user
    manager down).
  - A kill of the hourly unit between `docker start` and its notice: HA
    runs, the notice comes the next hour.

## G3: the "must know" list

### K3: outcomes a killed run never reported

`:2838` to `:2845`: after each component that ended any way but `not
started`, the status file's `unreported` holds the run's finished outcomes
(and its redeploy count) and is saved. `:2867`: cleared once the run's
notice is sent or kept pending. `:2888` `report_unreported`, called at
`:2688` at the start of the next run (any hour, before recovery): one
notice, `Kronk upgrade: <counts> (run <time>, which ended before its
notice)`, with the usual lines. Tests (2 failed before, 1 control):
`test_upgrades_kept_before_a_kill_are_reported_by_the_next_run` (tempo and
grafana kept, killed in litellm's verify: the next run reports both, then
recovers litellm), `test_a_kill_right_before_the_notice_is_reported_the_next_night`
(and once only); control `test_a_run_that_sends_its_notice_leaves_nothing_unreported`.

### K4: players as before

`scripts/upgrade_rules.toml:112` `flapping_players` (with its comment):
`media_player.xbr_49x800d` (the television), `media_player.sonos_move_derp`,
`media_player.sonos_move_derp_2`. `scripts/upgrades.py:1891` `_ha_diff`
returns (differences, player differences): listed players are skipped; a
move with both states in `RESTING_PLAYER` (`:1888`: idle, off, paused,
standby, on) is not a difference; any other change of an unlisted player,
including available to `unavailable` after the wait, is. `:1824`
`ha_checks(players_fail=…)`: the upgrade's verify fails on players; the
rollback's and the recovery's verify (`full=False`) put them in
`ctx.player_notes`, and the outcome's detail ends "after the rollback,
players differ (reported, not a failure): …". Tests (9 of 9 failed
before): `test_a_player_that_goes_unavailable_fails_the_upgrade_but_not_its_rollback`
(the tester's K4 shape: `rolled back`, not ROLLBACK FAILED),
`test_a_listed_flapping_player_does_not_fail_the_upgrade[×3]`,
`test_a_player_moving_between_resting_states_does_not_fail_the_upgrade[×4]`,
`test_the_flapping_players_are_data_in_the_rules`.
`tests/test_m18_minor.py::test_ha_diff_names_what_did_not_come_back` was
updated for the two-list return.

### K5: a stopped replay is retried

`:1555` `REPLAY_RETRIES, REPLAY_RETRY_WAIT_S = 3, 300`; `:1565`
`run_replay(retry=True)` from verify (`:1628`): on exit 3 the rows done so
far are kept, the rest (`Ops.corpus_ids`, the corpus in order, what
`voice_replay.py` selects by default) are replayed with `--id` after 5
minutes, up to three times; then "replay stopped by a player 4 times over
15 minutes". Rows from every part count, and their run ids are the run's
own. The baseline's stopped replay still declines the night. Tests (2
failed before, 1 control): `test_a_replay_stopped_once_during_verify_is_retried_for_the_remaining_rows`,
`test_a_replay_stopped_four_times_is_a_failed_verify`; control
`test_a_stopped_baseline_still_declines_the_night`.
`tests/test_m18_minor.py::test_a_replay_that_cannot_complete_after_the_change_rolls_back_unverified`
now expects "replay wrote no readable result … exit 3": its stub returns
exit 3 without the result file voice_replay always writes, so there is
nothing to retry from.

### K6: not started goes again the next night

`:2838` `months.<month>.components[<name>] = {result, to}` for every
outcome but `not started`; `:2737` planning skips those for the month
(`done this month: <result> (<to>)`; not with `--ignore-month`); `:2858`
`m["done"] = not pending or last_try`. On the last attempt with something
still pending: if earlier nights applied something, one notice `Kronk
upgrade: this month's patch run ended with N not started (attempts ran
out)` with the usual lines (`:2868`) and the warning; if nothing was ever
applied, F7's "applied nothing" notice as before. Wider than the ruling's
two reasons, on purpose: every `not started` is retried (a deploy in
progress, HEAD not deployed, a hash or bind-mount guard, HA's H6 checks),
because F7's month-end notice depends on it
(`test_seven_nights_of_not_started_end_with_one_notice_and_the_warning`)
and these preconditions can change by the next night as much as a busy
house. After a block (ROLLBACK FAILED, OUT OF STEP) the month is no
longer closed; later nights decline on the block until `ack`, then go on.
Tests (3 failed before): `test_components_not_started_because_the_house_got_busy_go_the_next_night`
(the tester's K6), `test_the_window_closing_between_components_leaves_the_rest_for_the_next_night`,
`test_when_attempts_run_out_the_notice_lists_what_never_started`.

### K7: a moved tag on the Kronk stack

`:1674` `kronk_up`: `docker pull <image>:<to>` and `check_pulled` before
`git tag` and `deploy_tag.sh deploy`; a moved tag or a failed pull is "not
applied" (no tag made, nothing deployed). The check after the deploy is
removed. The dry run's line says so. Tests (2 of 2 failed before):
`test_a_moved_tag_on_a_kronk_stack_component_is_not_applied_and_nothing_is_deployed`,
`test_the_kronk_stack_pull_comes_before_the_tag`.
`tests/test_m18_fix1.py::test_a_tag_that_exists_elsewhere_is_not_applied_and_git_is_put_back`
now expects the pull among the calls (it comes before the tag check).

### K8, K9, K13: documented

`docs/features/upgrade-cadence.md`: "What the household check cannot see"
(a streaming chat request; the five odd path spellings; `/devstral/` and
the deploy's nginx restart; HA's ten-run list and another session's dev
replay inside the correction window); "Installing the timers" (the tree
clean and on the units' branch at 03:30; the complete commands); "An
interrupted run" (no hand deploy while a record exists, the two ways out).
`systemd/kronk-upgrade-minor.service`'s Operations block carries the same.

### `deploy_tag.sh deploy` and the record

`scripts/deploy_tag.sh:270` to `:293` `upgrade_lock_check` (still called
for `deploy` only): lock held by the caller's token, pass (the run
itself); lock held otherwise, refused as before; lock free and
`status.json` holding an `intent`, refused: "refused: an upgrade run was
interrupted (litellm v1.88.1 -> v1.88.6, step verify, run …; the record is
in …/status.json). … Either let it recover now: python3
scripts/upgrades.py minor --ignore-window (scripts/upgrade_minor.sh
--ignore-window); or drop the record and take over by hand: python3
scripts/upgrades.py ack". A status file that cannot be read is refused too
("cannot tell whether an upgrade run was interrupted"). `ack`
(`scripts/upgrades.py:3566`) moves the record into `acknowledged` and says
so. Tests (3 failed before, 1 control): `test_deploy_refuses_while_an_unfinished_record_exists_and_names_the_ways_out`
(also: `plan` not refused, the call line unchanged),
`test_deploy_refuses_when_the_status_file_cannot_be_read`,
`test_ack_drops_the_record_and_deploy_goes_ahead`; control
`test_the_run_itself_deploys_past_its_own_record`. The real script runs
with a tag that cannot exist and temporary lock and state directories: it
stops at `no such tag` before any docker call.

### K10: the quarterly check's lock wait

`scripts/upgrade_rules.toml:80` `major_lock_wait_minutes = 20` (was
`lock_wait_minutes = 240`), read at `scripts/upgrades.py` `major_run`.
The unit's `TimeoutStartSec=30m` is unchanged. Test (failed before):
`test_the_quarterly_check_waits_twenty_minutes_at_most_under_its_unit_timeout`
(20 sleeps of 60 s, then the give-up notice; the unit's timeout at least
25 minutes).

### K11: status files of the wrong encoding or shape

`:869` `load_status` decodes UTF-8 strictly; `:891` `status_shape` checks
once at load: `months` an object of objects with integer `attempts`,
boolean `done`, objects `held_back` and `components`; `intent`,
`rollback_failed`, `out_of_step`, `last_run`, `major`, `acknowledged`,
`unreported` objects or absent; `intent`'s component, from, to and step
strings; `attention` and `pending_notices` lists of objects with string
fields; `unreported.outcomes` objects with only Outcome's fields. Unknown
keys are left alone. Tests (11 of 11 failed before, 1 control): the
tester's list as `test_a_status_file_of_the_wrong_encoding_or_shape_is_corrupt_for_every_command[0-10]`
(binary, UTF-16, `months` a list, a month without `attempts`, `attempts`
a string, `intent` a string, a numeric component, `rollback_failed:
"yes"`, `attention: [1]`, a numeric title, `unreported.outcomes` a
string; `minor`, `notices`, `ack`, `status` each exit 2, the file
untouched, one notice from `minor`); control
`test_a_status_file_the_run_itself_writes_passes_the_shape_check`.

### K12: failed reads are failed checks

`:1836`: `docker events` that cannot be read fails HA's verify ("cannot
tell whether HA was killed with signal 9"). `:2033`: Music Assistant's
start time unreadable before the restart declines HA's restart (`not
started`, no stop). `:1913`: unreadable after the restart fails the
verify. In a recovery there is no "before" snapshot, so there is no
comparison (as before). Tests (3 of 3 failed before):
`test_a_failed_docker_events_read_fails_the_upgrade`,
`test_a_failed_read_of_music_assistants_start_time_before_the_restart_declines_it`,
`test_a_failed_read_of_music_assistants_start_time_after_the_restart_fails_the_upgrade`.

## G5: the observations

- O1. `:200` `vkey`: ASCII only (`isascii`, `re.ASCII`); a compared group
  with a leading zero is no version unless the component is
  `zero_padded` (`scripts/upgrade_rules.toml:507`, ma-blueprint's
  `YYYYMMDD`; documented in the field list). Tests (8 of 8 failed before):
  `test_a_leading_zero_or_a_non_ascii_digit_is_unparseable[×6]`
  (`13.02.6`, `2026.05.6`, `13.2.06`, Arabic-Indic and fullwidth digits),
  `test_ha_takes_the_real_patch_not_a_zero_padded_one` (2026.5.5 over
  2026.05.6), `test_zero_padded_parts_parse_only_where_the_rules_say_so`.
- O2. `:368` `_port_443`; `Web.get` and `:375` `_GuardedRedirects` refuse
  any port but 443; a redirect to another host drops `Authorization` (and
  from `unredirected_hdrs`); `:445` `github_release` refuses an answer
  whose `tag_name` is not the tag asked for (the component's skip). Tests
  (6 failed before, 3 controls): `test_a_redirect_to_another_port_is_refused[×3]`,
  `test_the_registry_token_is_not_sent_on_to_another_host`,
  `test_a_lookup_on_another_port_is_refused`,
  `test_a_release_looked_up_by_tag_must_carry_that_tag`.
- O3. `:955` `save_status` removes its temporary file on any failure;
  `:977` `sweep_status_temps` removes `.status.*` files (the literal
  pattern, `:981`) older than a day from the state directory, at the start
  of the monthly run (`:2654`) and the quarterly check (`:3415`), never in
  a dry run. Tests (2 failed before, 1 control):
  `test_a_status_write_that_fails_midway_leaves_the_old_file_whole` (also
  kills mutant `l13`, shown separately: the write-in-place mutant fails
  it), `test_leftover_status_temporaries_older_than_a_day_are_swept_at_the_start_of_a_run`;
  control `test_the_dry_run_sweeps_nothing`.
- O4. `scripts/upgrades.py:156` to `:191`: surrounding whitespace in a
  name, project, file, service, container or deploy is refused; a case
  variant of `kronk` or `kronk-ma` is refused; file paths are compared
  after `os.path.normpath` (`./docker-compose.ma.yml` is the MA stack;
  `docker-compose.yml` under `deploy = compose|ha` is refused whatever the
  project); `night_window` times with an offset are refused. Tests (8 of
  8 failed before, 1 control): `test_the_loader_refuses_look_alikes[×8]`,
  `test_the_shipped_rules_still_load`.
- O6. `:949` `StatusWriteFailed` (not an OSError, so no step handler
  takes it as its own failure) from `save_status`; `minor_run` (`:2657`)
  stops there, logs, tries one notice `Kronk upgrade: STOPPED, the status
  file cannot be written`: "Nothing was changed." before any component or
  recovery step; after one, where HEAD stands and that the next run
  recovers from the record on disk. `RunLog` keeps going on stdout when
  its file cannot be written. Tests (3 of 3 failed before):
  `test_a_failed_status_write_before_any_change_stops_cleanly`,
  `test_a_failed_intent_write_stops_before_the_pin_commit`,
  `test_a_failed_status_write_after_a_change_is_not_a_traceback_and_says_what_moved`.
  The quarterly check, `notices` and `ack` still end with a traceback on a
  failed write (not in the ruling).
- O5, O7, O8: left as they are, as ruled. O5: the lock file is mode
  0664 and holds the token while the run lives. O7: a recovery exits 1;
  a blocked month counts attempts and night 7 has no notice of its own.
  O8: nginx error-log lines naming `POST /api/chat` are counted.

## The surviving mutants

`tests/test_m18_fix2.py::test_each_of_has_restart_checks_fails_the_upgrade_on_its_own`
(StopTimeout 10, signal 9, `--check-only` exit 1, voice_tracer not
connected, voice_tracer `too_old`) and `test_has_state_diff_fails_the_upgrade_on_its_own`
(a select that changed). The fake gained `stop_timeout`, `kill_signals`,
`check_only_rc`, `vt_probe`. Mutation check, one string replacement at a
time in `scripts/upgrades.py`, the M18 tests run with `-x`, the file
restored after each (run from the scratch directory):

| Mutant | Result |
| --- | --- |
| `o33` StopTimeout dropped from the verdict | killed (`…[o33-stop_timeout-10-…]`) |
| `o34` signal 9 dropped | killed (`…[o34-kill_signals-9-…]`) |
| `o35` HA's state diff dropped | killed |
| `o36` `--check-only` dropped | killed (`…[o36-check_only_rc-1-…]`) |
| `o37` voice_tracer reconnect dropped | killed |
| `i6`′ any production tag read as the run's own | killed |
| `i6`″ any production tag read as the recorded previous | killed (`…never_deployed_over[HEAD-verify]`) |
| `l13` the status file written in place | killed (`test_a_status_write_that_fails_midway_leaves_the_old_file_whole`) |
| G1: unexplained commits ignored; guards dropped; previous tag existence ignored; component version ignored | each killed by its own test |
| G2: `docker start` skipped; stop of a missing container; absent HA without H7b | killed |
| G4: a failed inspect read as absent | killed |
| K4 (2), K6, K7, K11, K12, O1, O2, O3 | killed |

25 mutants, 25 killed. The other survivors of the second report (`o2`,
`o3`, `o9`, `o10`, `o12`, `o13`, `i9`, `i4`, `i7`, `i28`, `o44`, `o28`,
`o43`, `b*`, `w1`, `w2`, `r*`, `o8`, `o17`, `i20`) were not asked for and
were not re-run.

## The recovery table as it now stands

Order: (1) HA's record and HA stopped or absent: brought back first (G2).
(2) The tree not clean, HEAD with commits the record does not explain, or
git at neither version: OUT OF STEP. (3) The table:

| Component | Production / container | git | Done |
| --- | --- | --- | --- |
| Kronk stack | production on the run's own tag, the recorded previous tag exists | to or from | inside the night window, house quiet: pin back by a commit if git has `to`, deploy the previous tag, verify; otherwise one notice and the warning, wait for the next night |
| Kronk stack | production on the run's own tag, the previous tag gone | any | OUT OF STEP |
| Kronk stack | production on the recorded previous tag, the component on `from` | to | pin back by a commit; nothing restarted |
| Kronk stack | the same | from | record cleared; "nothing was out of step" |
| Kronk stack | production on the previous tag, the component on anything else | any | OUT OF STEP |
| Kronk stack | production on any other tag (or none readable) | any | OUT OF STEP naming the tag that runs, the recorded tags, what to do |
| Telemetry, HA | `from` runs | to / from | pin back, nothing restarted / record cleared |
| Telemetry, HA | `to` runs; its config-hash label is the record's `hash_to`; the file at HEAD gives `hash_to` (git `to`) or `hash_from` (git `from`); no bind-mounted file newer than the start; HA: H7b passes | to or from | inside the window, house quiet: pin back if git has `to`, `up -d` (HA: stop, `up -d`, by the recorded image id), verify (players reported, not failed); otherwise wait as above |
| Telemetry, HA | `to` runs and any guard fails | any | OUT OF STEP |
| Telemetry | not running, or another version | any | OUT OF STEP |
| HA | stopped | any | `docker start`, health, then this table again |
| HA | absent | disk `from`; or `to` with a clean tree and HEAD the pin commit | (pin back,) H7b, `compose up` without a stop, health, then this table again |
| HA | absent, otherwise | any | OUT OF STEP, HA down |
| any | the recovery's rollback or HA's start fails | any | ROLLBACK FAILED |

OUT OF STEP and ROLLBACK FAILED touch nothing further, block later runs
until `ack`, raise the warning and send one notice. A recovered component
is held back for the month. The hourly unit, between runs: the warning,
one notice, and `docker start` of a stopped HA; nothing else.
`deploy_tag.sh deploy` refuses while the record exists.

## Install and uninstall, for the coordinator

Before: the working tree clean, and on the branch production's tags are
made from (the reference units say `main`; today the tree is on
`refactor`, and every night would decline until that is so).

```
cd ~/git-repos/drawsmcgraw/kronk
git status --porcelain                      # must print nothing
git rev-parse --abbrev-ref HEAD             # the branch production's tags are made from
cp systemd/kronk-upgrade-{minor,major-check,notices}.{service,timer} ~/.config/systemd/user/
# only if that branch is not main (both units carry it):
sed -i 's/^Environment=KRONK_UPGRADE_BRANCH=.*/Environment=KRONK_UPGRADE_BRANCH=<branch>/' \
    ~/.config/systemd/user/kronk-upgrade-{minor,major-check}.service
systemd-analyze --user verify ~/.config/systemd/user/kronk-upgrade-*.{service,timer}
systemctl --user daemon-reload
systemctl --user enable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
systemctl --user list-timers 'kronk-upgrade-*'
python3 scripts/upgrades.py test-notice     # the one real notice, marked TEST
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

`data/upgrades/` (status, lock, run logs) stays on removal. After an
interrupted run, do not deploy by hand: `scripts/upgrade_minor.sh
--ignore-window` recovers it, `python3 scripts/upgrades.py ack` drops the
record. `systemd-analyze --user verify` on copies of the six units in the
scratch directory: exit 0, no output.

## Still Drew's

Unchanged, as ruled: the branch (unit default `main`); the 48-hour grace;
a recovered component held back for the month; an unavailable satellite
declining; the modification-time guard; F11's grouping. Also open: how a
notice reaches him when HA is what failed (the `/services` warning is the
built default); whether the hourly unit should also create a missing HA
container (the remaining long gap above); whether K6's retry should be
narrowed to the two reasons the ruling names.

## Live work

### Dev pair (orchestrator changed)

`docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev` at 15:17 UTC: `kronk-orchestrator-dev-1` recreated (new
id `58fab27b…`, started 15:17:43); `kronk-tool_service-dev-1` not
recreated (its code did not change; same id, started 02:49:34). Both dev
image tags point at new ids (`kronk-orchestrator-dev` `200666a916b5`,
`kronk-tool_service-dev` `8f1fc00faa00`, the latter a cached rebuild whose
image config is unchanged, created 2026-11-18 02:49 UTC). `GET
/dev/api/upgrades`: HTTP 200,
`{"file":"/data/upgrades/status.json","present":false,"warnings":[],"last_run":null,"note":"no upgrade run has written a status file yet"}`;
the new code is in the container (`intent` in its `upgrades_api.py`).
`GET /dev/services`: 200. `GET /api/upgrades` (production): 404, as
before promotion. nginx was not restarted (it is production's); the dev
route answered without it. The warning path for a record is tested in
`test_the_api_shows_an_unfinished_record_as_a_warning`; no status file was
written under `data/` to show it live.

### The rehearsal

Run once, 15:17:58 to 15:29:03 UTC, in
`/tmp/claude-1000/-home-drew-git-repos-drawsmcgraw-kronk/ca65a2c1-265e-40bd-8f16-cd1f0a3e247b/scratchpad/rehearsal`
(compose project `m18-rehearsal` only; neither busybox image nor the
project existed before):

- A: dry run first (`state dir exists after dry run: False`), then
  `committed bad53e9: upgrade: busybox-scratch 1.36.0 -> 1.36.1`, `kept
  for rollback: busybox:1.36.0 = sha256:9e2bbca0…`, `image pulled:
  busybox:1.36.1 = sha256:73aaf090… ; the registry gave sha256:73aaf090… at
  planning`, `verified: busybox-scratch 1.36.1 kept`, stub notice `Kronk
  upgrade: 1 upgraded`, exit 0.
- B: `FAILED: check … failed (exit 1)` at 15:23:37, `rolled back and
  verified: busybox-scratch 1.36.0` at 15:23:47, stub notice `Kronk
  upgrade: 1 rolled back`, exit 1, `busybox:1.36.0` running.
- C: `ROLLBACK FAILED: compose up of 1.36.0 failed: … No such image:
  busybox:1.36.0`, stub notice `ROLLBACK FAILED; running is 1.36.1; git has
  1.36.0; needs you: …`, exit 2; the next run `declined: the last rollback
  failed (…)`; `ack`: `cleared: 1 warning(s), the failed-rollback block`,
  `rollback_failed after ack: None`.
- Cleanup: `busybox:1.36.0 absent`, `busybox:1.36.1 Untagged`, `left:
  nothing`; `docker compose ls -a` shows no `m18` project.

Real: Docker Hub lookups and pulls of busybox, git in a scratch
repository, the lock, status and log in a scratch state directory, compose
`config --hash` (now also recorded after the pin commit), `pull`, `up`,
`inspect`, the `exec` health check. Stubbed: HA's states, the voice_tracer
probe, nginx's log, the corpus, the replay, the notifier. No interrupted
run is rehearsed (the kill paths are in the unit tests).

### The dry run

`scripts/upgrade_minor.sh --dry-run`, once, 15:29:12 to 15:29:24 UTC, exit
0; 76 lookups to `api.github.com`, `ghcr.io`, `hub.docker.com`, `pypi.org`;
`data/upgrades` absent before and after; `git status` unchanged. Last ten
lines:

```
15:29:24Z would do, in order (each its own commit; verify = image, health, corpus, replay, stack checks):
15:29:24Z   grafana: docker-compose.telemetry.yml: 13.2.4 -> 13.2.5; commit 'upgrade: grafana 13.2.4 -> 13.2.5'; compose -p kronk-telemetry pull grafana; up -d --no-deps grafana (rollback: pin back, up -d with the local previous image)
15:29:24Z   litellm: docker-compose.yml: v1.88.1 -> v1.88.6; commit 'upgrade: litellm v1.88.1 -> v1.88.6'; docker pull ghcr.io/berriai/litellm:v1.88.6 (digest checked); git tag upgrade-<run time>-litellm; deploy_tag.sh deploy it (a full production redeploy; rollback: pin back, deploy sprint-M21-step2, another)
15:29:24Z then: one notice through scripts/lib/notify.sh with what changed; the status file data/upgrades/status.json for /services
15:29:24Z production runs tag: sprint-M21-step2
15:29:24Z HEAD vs sprint-M21-step2: undeployed changes in orchestrator/main.py, orchestrator/static/services.html, orchestrator/upgrades_api.py (Kronk-stack components would be declined)
15:29:24Z docker-compose.telemetry.yml vs running kronk-telemetry-grafana-1: matches (config hash)
15:29:24Z bind-mounted files of kronk-telemetry-grafana-1: none changed since it started
15:29:24Z a real run now would DECLINE: outside the night window 02:00-05:00 local (now 10:29): the timer tries the next night; on branch 'refactor', expected 'main' (KRONK_UPGRADE_BRANCH / expected_branch); media_player.home_assistant_voice_0ac919_pe_01 is playing; media_player.home_assistant_voice_0ac919_media_player is playing
15:29:24Z dry run: nothing written, nothing changed, nothing sent
```

## Before and after

Before (15:16 UTC, after the code commits, before any live step): every
container's name, id, image, start time and status; `docker images`;
`docker compose ls -a`; networks; volumes; `git tag`; the files in
`~/.config/systemd/user` recorded in the scratch directory. No
`kronk-upgrade-*` unit loaded; `data/upgrades` absent.

After (15:29 UTC, after the rehearsal's cleanup and the dry run): the
same records differ only in `kronk-orchestrator-dev-1` (recreated by the
dev rebuild) and the two dev image tags (above). Every production
container, Home Assistant, Music Assistant, the telemetry stack: same id,
same start time. Networks, volumes, compose projects, git tags and the
user unit directory: identical. `data/upgrades` absent. No symlink in the
scratch directory. No background job running.

Changes to earlier tests, each for a ruling: `tests/test_m18_fix1.py`
(`test_ha_killed_after_the_stop_is_brought_back_on_the_previous_image`
expects `docker start` and the pin put back, G2;
`test_dry_run_reports_an_unfinished_intent_and_changes_nothing` expects
the start line, G2; `test_dashboards_grafana_reloads_by_itself_do_not_hold_it_back`
uses a modification time after the run's restart, since `up` now sets the
fake's start time, G1; `test_a_tag_that_exists_elsewhere_is_not_applied_and_git_is_put_back`
expects the pull, K7), `tests/test_m18_minor.py` (the two named under K4
and K5).

## Tests

`./scripts/run_tests.sh -q -p no:cacheprovider` at `e72bf71` (the code
of this round; this report changes no code):

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
5351 passed, 3 skipped, 53 xfailed, 6 warnings in 72.50s (0:01:12)
```

Before this round: `5247 passed, 3 skipped, 53 xfailed`. 104 new cases.
The six warnings are the `PydanticDeprecatedSince20` lines of the earlier
reports.
