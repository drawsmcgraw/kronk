# M18 fix round 1: an upgrade cadence

Fix agent, 2026-11-18 (UTC), branch `refactor`, fix round 1 after
`docs/sprint/M18-e2e.md` failed M18. Inputs: CLAUDE.md, `M18-criteria.md`,
`M18-build.md`, `M18-e2e.md`, the coordinator's fix list F1 to F14 with its
rulings. Nothing was upgraded, pulled into a production tag, recreated,
restarted or installed; no pin was changed in the repo outside tests; no
notice was sent (every notifier stubbed; `test-notice` not run); no
`deploy_tag.sh deploy` was run. Orchestrator code did not change, so the
dev pair was not rebuilt.

## Commits

| Commit | Fix |
| --- | --- |
| `067539c` | F1: the run's own replays and deploy checks are not the household being busy |
| `8cb5a8e` | F2, F4: outcomes say what happened; a rollback undoes only what was done |
| `5668649` | F3: the intent record; recovery; pin against what runs at planning |
| `24f654c` | F5: HA's restart re-checks M12 H7b and H7 |
| `76d4655` | F6: malformed registry and index answers skip the component |
| `515d917` | F7: a month that ends with nothing applied notifies and warns |
| `f3b4bd0` | F8: the night window; user-unit directives that did nothing |
| `27b0eb6` | F9: one lock for both runs; corrupt status stops; deploys respect the lock |
| `a922571` | F10: loader guards; bind-mounted files count |
| `c0953fc` | F12: release list and registry, both 14 days old; moved tags; digests |
| `ff49005` | F10 follow-up: bind paths a service reloads by itself (found by the live dry run) |
| `1a486d5` | Rehearsal runs by day (`ignore_window`); a blocked run's reason separates state and detail |
| `caea274` | F11, F13, F14: docs, runbook, first report, build-report note, probe replay files |
| this commit | this report |

`4750aca` (M20 pass criteria) in the same range is the coordinator's, not
this round's.

## The fixes

Every "would have caught it" test below was run against the script as it
stood before that fix (the previous commit's `scripts/upgrades.py`, and for
F9 `scripts/deploy_tag.sh`, swapped in, then restored) and failed there.
Counts: F1 10 of 14 tests failed before the fix; F2/F4 9 of 9; F3 10 of 10;
F5 7 of 7; F6 27 of 27 (two of those shapes were already handled; they fail
before only because `safe_plan` did not exist, and stay as controls); F7 3
of 3; F8 9 of 10 (the unit test passes on the edited units either way); F9 8
of 8; F10 9 of 10 (the "binds outside the repo" test guards against
overreach and passed before too); F12 11 of 11. All in
`tests/test_m18_fix1.py` (68 test functions; with parameters, 109 new
cases) unless named otherwise.

### F1 (B1): the run's own work is not the household

What changed:
- `scripts/upgrades.py:1061` `CHAT_ROUTE` captures the path; `UA_FIELD`
  the User-Agent (the last quoted field of nginx's combined log).
- `scripts/upgrades.py:1066` `Own`: the run's User-Agent and the HA run ids
  of its own replays. `minor_run` makes one per run:
  `kronk-upgrade/<run time>-<12 random hex>`.
- `scripts/upgrades.py:1073` `house_busy`: lines under `/dev/` and lines
  with the run's own User-Agent are set aside; `:1131` `_held_not_ours`:
  of the runs voice_tracer holds (ids from its state file,
  `data/voice_tracer_state.json`, `:702`), those on a dev pipeline (HA's
  `assist_pipeline/pipeline_debug/list` for `kronk-dev`, read-only
  websocket on loopback, `:711`) or from the run's replays are set aside.
  What was set aside is logged. Unreadable still means busy.
- `scripts/upgrades.py:623` `client_env`: `deploy_tag` gets
  `KRONK_CLIENT_UA`; `scripts/deploy_tag.sh:256` sends it as the
  User-Agent of its `/api/status` and `/api/chat` checks (unset: curl's
  own, as before).
- `run_replay` records the `pipeline_run_id` of every replayed row.
- Rules: `dev_pipelines`, `voice_tracer_state` (`scripts/upgrade_rules.toml:94`).
- The fake (`tests/m18_helpers.py`, `FakeOps`) now leaves the run's traces
  as the real house does: each replay row is a run on `kronk-dev` that
  reaches nginx as `POST /dev/voice/api/chat` with HA's client and that
  voice_tracer holds (until the end of the test); each deploy leaves
  `GET /api/status` and `POST /api/chat` with the User-Agent it was handed;
  `docker logs --since` honours the window.

How the run tells its own requests from the household's, and why that
cannot hide a real one. The request address cannot do it: HA runs on host
networking and every host-side client reaches nginx as `172.18.0.1`, the
same as the run (seen in nginx's log). A time window per step would hide a
household request made during the deploy. The run uses a User-Agent it
makes up per run: `kronk-upgrade/<UTC run time>-<12 random hex>`. It exists
only in the run's memory and in the environment of the `deploy_tag.sh` it
starts; it is not in the repo, the status file or any shared place. A
household request comes from HA's aiohttp client, a browser, curl by hand,
or another run (a different random part), and is counted unless it carries
that exact string. The other run request to production is
`voice_replay.py --check-only`'s `GET /api/status`, which is not a chat
route and never counted. Requests under `/dev/` are not counted by the
coordinator's ruling: they are the dev path. A person saying "Hey Jarvis"
also reaches `/dev/`, but that request changes its satellite's state, and
the satellite check counts it. Held runs are set aside only when HA itself
lists them on `kronk-dev` or the run's own replay produced them; when HA's
list cannot be read, only the run's own are set aside.

Tests that would have caught it:
`test_the_runs_own_replays_and_deploy_checks_do_not_stop_any_component`
(24 replay requests, 2 deploy checks, 24 held runs left behind; all five
components start and are kept),
`test_seven_nights_with_the_runs_own_traffic_apply_on_the_first` (the
tester's case: night 1 applies, one notice, nights 2 to 7 do nothing),
`test_a_household_request_before_the_run_still_declines`,
`test_a_household_request_between_components_stops_the_rest`,
`test_a_production_chat_request_with_any_other_user_agent_counts` (curl,
another run's marker, a browser), `test_a_held_household_run_still_declines_between_components`,
`test_dev_path_requests_and_dev_held_runs_are_not_a_decline`,
`test_when_ha_cannot_list_dev_runs_only_the_runs_own_are_set_aside`,
`test_an_unreadable_state_file_with_runs_held_is_not_quiet`,
`test_ops_hands_deploy_tag_the_marker_and_the_script_uses_it`. The
tester's surviving mutant d21 (the per-component busy check removed) now
fails `test_a_household_request_between_components_stops_the_rest`.
`tests/test_m18_minor.py`: the `/dev/voice/api/chat` decline case was
removed (the ruling reverses it); `_held` now holds a household run id.

Evidence, live: dry run 2 below, made inside the 5-minute window of a
two-row replay on `kronk-dev`: `voice_tracer: set aside 1 held run(s) on
kronk-dev or from this run's replay`, `nginx: set aside 2 request(s) on the
dev path (/dev/)`, and no chat/voice or held-run reason in the decline. The
held run `01M7QF8J3702AMEZCP7X1JBZY3` is in HA's `kronk-dev` list and not in
the `kronk` pipeline's (read-only check through `Ops.dev_pipeline_run_ids`).

### F2 (B2): an outcome says what happened

What changed (`scripts/upgrades.py`):
- `Outcome` carries `state`, the fact that matters (what git has, what
  runs), alongside `detail`. `:1715` `what_runs` reads the container's
  image and whether it runs ("nothing (homeassistant is stopped; Home
  Assistant is down)").
- `_apply_one` tracks `ctx.touched`: set right before the first step that
  changes what runs (`deploy_tag.sh deploy`, compose `up`, `docker stop`).
  A failure before it is `not_applied` (`:1848`): the run reverts its own
  pin commit (`git revert --no-commit` plus a commit `upgrade: not applied,
  <name> back <to> -> <from>`) and says `not applied; git is at <from>
  again (revert <sha>), running is <version>`. A failed pin commit puts the
  file back (`:1831`). A revert that fails is `out_of_step` with exactly
  what is where, which stops the run and blocks later runs until `ack`
  (`block`, `run_declines_blocked` `:1191`). An error escaping a component
  is described from git and what runs (`:1909` `error_outcome`); "ROLLBACK
  FAILED" only from `rollback` (`:1872`), when a rollback ran and failed.
- Tags: `upgrade-<YYYYMMDDTHHMMSSZ>-<name>` (the run time; one run per
  second under the lock). `kronk_up` (`:1481`) reuses an existing tag only
  when it points at the pin commit; elsewhere, `not applied`.
- Notices (`:2144` `notice_text`): each line is `<name> <from> -> <to>:
  <state>`, then for blocking outcomes `needs you: later runs wait until …`,
  then the detail cut at 300 characters with "…". The title ends with
  `: Home Assistant is down` when a state says so.
- Blocks are checked before planning, so a stray pin cannot read as CURRENT
  behind a block.

Tests: `test_the_testers_same_day_rerun_now_works` (the tester's input:
litellm rolled back, rerun the same day with `--ignore-month`: upgraded, two
tags, no "ROLLBACK FAILED"), `test_a_tag_that_exists_elsewhere_is_not_applied_and_git_is_put_back`,
`test_a_tag_that_already_points_at_the_pin_commit_is_reused`,
`test_a_failed_pin_commit_puts_the_file_back`,
`test_a_revert_that_fails_says_exactly_what_is_out_of_step_and_blocks`,
`test_an_error_before_any_change_is_not_called_a_rollback` (mutant v28's
path), `test_notice_lines_put_the_state_first_and_cut_only_the_detail`;
`tests/test_m18_notices.py::test_when_ha_is_what_failed_the_result_is_still_on_services`
now asserts `Kronk upgrade: ROLLBACK FAILED (homeassistant): Home Assistant
is down` and a body starting with the state.

Evidence: rehearsal C below (`ROLLBACK FAILED; running is 1.36.1; git has
1.36.0; needs you: …`).

### F3 (B3): a run killed mid-component is seen by the next run

The intent record (`scripts/upgrades.py:1320` `set_intent`, `step`,
`clear_intent`): before the pin commit the run writes `intent` into the
status file: component, from, to, deploy kind, step, the previous
production tag, HA's previous image id, the run's stamp, the time. Each
later step updates `step` (and `commit`, `tag`, `ha_old_id`) and saves:
`pin-commit`, `bring-up`, `tag`, `deploy`, `pull`, `up`, `ha-tag`, `ha-pull`,
`ha-stop`, `ha-up`, `verify`, `rollback`, `revert`. `apply_one` clears it
when the component ends, whichever way. A process that dies leaves it.

What the next run does (`:1966` `recover`, `:2000` `_recover_one`), before
the night window check, the month's bookkeeping and planning. It reads git
at HEAD, the working tree, the tag, production's tag (Kronk stack) and what
the container runs, then:

| Found | Done |
| --- | --- |
| working tree not clean | out of step: stops, blocks, notice and warning; touches nothing |
| git has neither from nor to, or the container runs neither | out of step, as above |
| git and running both at from | clears the record; notice "nothing was out of step" |
| git at to, from runs (killed before the bring-up changed anything) | pin put back by a commit; nothing restarted |
| to runs unverified (or production runs another tag than the recorded one) | pin back, then the previous tag deployed / `up -d` of the previous image / HA's rollback by the recorded id; verified. Only inside the night window with the house quiet; otherwise one notice and the warning, and it waits for the next night |
| the component down (HA stopped between its stop and start) | brought back on the previous image at once, at any hour, pin put back; verified; notice says "Home Assistant was down" |
| the rollback above fails | ROLLBACK FAILED: blocks, notice |

A recovered component is held back for the rest of the month
(`months.<month>.held_back`), and the run stops after the recovery; the
next night plans as usual. Planning (`:1925` `pin_vs_running`) compares
every exact pin with what its container runs, every run; a difference is
`MISMATCH`, not CURRENT, with one notice and a warning a month
(`:2113` `mismatch_notice`). The dry run prints what a real run would do
with an unfinished record and changes nothing.

Tests, one per row of the tester's table, each killing a real run at the
step (a `BaseException` no handler catches, so nothing after it runs) and
running the next night on the same house:
`test_killed_after_the_pin_commit_before_the_tag`,
`test_killed_after_the_tag_before_the_deploy`,
`test_killed_during_verify_finishes_the_rollback`,
`test_killed_during_verify_waits_for_a_quiet_house_to_roll_back`,
`test_grafana_killed_after_the_pin_commit_before_the_pull`,
`test_ha_killed_after_the_pin_commit_before_the_pull`,
`test_ha_killed_after_the_stop_is_brought_back_on_the_previous_image` (with
no patch left to plan, so "nothing to upgrade" cannot hide it),
`test_killed_in_the_rollback_with_the_pin_restored_but_not_committed`; and
`test_a_pin_ahead_of_what_runs_is_never_current`,
`test_dry_run_reports_an_unfinished_intent_and_changes_nothing`.
Found on the way: HA's H8 diff compared `rest_command` against a missing
"before" snapshot and failed every recovery; it now compares only when
there is a snapshot.

### F4 (K1): a failure before HA's stop does not restart HA

`rollback` runs only when `ctx.touched` is set; for HA that is set at the
`docker stop`. A failed `docker tag` or `docker pull` is `not applied`: the
pin commit reverted, no stop, no `up`. The `kronk/ha-rollback:<from>` tag a
successful `docker tag` left stays (no image is ever deleted). Test:
`test_a_failure_before_the_stop_does_not_touch_ha[pull]`, `[tag]`.

### F5 (K2): M12's H7b and H7 around HA's restart

`scripts/upgrades.py:1556` `ha_files_ok`: `docker compose … config --format
json` must render, and every bind source it gives HA must exist (today
`/etc/localtime` and `/home/drew/kronk-voice-recordings`, the latter
`create_host_path: false`). Checked before the pin commit (`not started`)
and again in `ha_up` (`:1572`) right before the stop, with
`<image>:<to>` present locally and `kronk/ha-rollback:<from>` equal to the
running image id; anything missing there is `not applied`, never a stop.
After the start, `ha_checks` (`:1620`): StopTimeout 120 (unreadable now
counts), the container's mounts match the compose file's binds, and HA's
log since the restart has no "was shutdown cleanly" line (HA's "could not
validate that the sqlite3 database … was shutdown cleanly"). Tests:
`test_ha_is_not_started_without_the_recordings_directory`,
`test_ha_is_not_started_when_its_compose_file_does_not_render`,
`test_a_precondition_lost_right_before_the_stop_is_never_a_stop[directory|image|rollback-tag]`,
`test_a_mount_missing_after_the_start_rolls_back`,
`test_an_unclean_shutdown_line_after_the_start_rolls_back`. The compose
render was read once, read-only, on the real file (the three volumes as
expected).

### F6 (K3): malformed answers skip the component

`scripts/upgrades.py:353` `_shape` checks every parsed answer (lists,
objects, strings, dates); `_date` refuses a non-string; `Web.get` turns
`http.client.HTTPException` (`IncompleteRead`) and decode errors into
`LookupFailed`; `ghcr_tags` refuses non-JSON and non-string tags; a draft
release by tag is not a release. `plan_component` catches anything else as
a skip; `:1026` `safe_plan` wraps it in the run; the lockfile plan catches
per package; `gather_majors` per component. Test: the tester's list as
`test_a_malformed_answer_skips_that_component_with_a_reason` (14 shapes:
Hub tag list as a list, as a string, `results` not a list, a numeric name;
GitHub list of strings, numeric `tag_name`, numeric `published_at`; ghcr
token answer a list, tag list HTML, numeric tags; a release-by-tag that is
a list; a body cut short (`IncompleteRead` from `read()`); and the two
already handled, HTML from Hub and GitHub's rate-limit object), and
`test_the_run_goes_on_past_a_malformed_answer` (12 shapes: tempo still
upgraded, the broken component in the notice's Skipped line, no traceback),
`test_a_malformed_lockfile_answer_is_a_note`.

### F7 (K4): a month that applied nothing says so

`scripts/upgrades.py:2397` `month_ended_empty`: on the month's last
attempt, a run whose components were all not started, or that could not
take its baseline, sends one notice ("this month's patch run applied
nothing", with the reasons) and raises the `declined:<month>` warning.
Tests: `test_seven_nights_of_not_started_end_with_one_notice_and_the_warning`
(the tester's K4: undeployed `orchestrator/main.py` at HEAD; one notice on
night 7 naming it), `test_seven_nights_without_a_baseline_end_with_one_notice`,
`test_a_month_of_mixed_declines_and_not_started_ends_with_one_notice`.

### F8 (K6): the night window

`settings.night_window` (`scripts/upgrade_rules.toml:69`, default
`["02:00", "05:00"]`, local time, may cross midnight; the loader refuses
anything but two times). `scripts/upgrades.py:1154` `outside_window`.
Started outside it, the run logs `declined: outside the night window …`,
writes `last_run`, counts no attempt and plans nothing; between
components it checks again (`:1757`). Recovery of a component that is down
runs at any hour; a recovery that redeploys waits for the window.
`--ignore-window` for a run by hand. Removed: `After=docker.service
network-online.target` from `kronk-upgrade-minor.service` and
`After=network-online.target` from `kronk-upgrade-major-check.service` (a
user manager cannot order against system units); timer comments say what a
boot catch-up does. `systemd-analyze --user verify` on copies of the six
units: exit 0, no output. Tests: `test_a_boot_catch_up_at_seven_in_the_evening_does_nothing_and_counts_nothing`,
`test_the_window_closing_between_components_leaves_the_rest`,
`test_a_run_by_hand_can_ignore_the_window_but_not_the_house`,
`test_a_window_may_cross_midnight`, `test_the_rules_refuse_a_window_that_is_not_two_times`,
`test_a_down_ha_is_brought_back_at_any_hour_but_a_redeploy_waits_for_the_night`,
`test_user_units_carry_no_ordering_on_system_units`. The fake's local time
is UTC-5, as America/New_York in December.

### F9 (K7, K8): one lock; corrupt status; deploys

- The quarterly check takes the same lock (`scripts/upgrades.py:2471`
  `wait_lock`, waiting up to `lock_wait_minutes`, 240), then writes
  (`:2904` `_major_write`). Still held after the wait: no report, a notice,
  exit 1.
- `:815` `StatusCorrupt`: a status file that cannot be read or parsed, or
  is not an object, stops `minor`, `major`, `notices`, `ack` and `status`
  with the reason; the file is left as it is; `minor` and `major` try one
  notice; the hourly `notices` does not (it would every hour).
- `:2456` `take_lock` writes the holder (`pid=`, `run=`, and for the
  monthly run `token=<32 random hex>`) into the lock file.
  `scripts/deploy_tag.sh:270` `upgrade_lock_check`: `deploy` refuses while
  `flock -n` cannot take that file, naming the holder (without the token),
  unless `KRONK_UPGRADE_TOKEN` matches the file's token: the run's own
  deploys pass it through `client_env`. `plan`, `status` and `seed` are
  unaffected.
- The run re-checks for a running `deploy_tag.sh` before each component
  (`:1760`).

Tests: `test_the_quarterly_check_waits_for_the_lock_then_runs`,
`test_the_quarterly_check_gives_up_on_a_held_lock_and_says_so`,
`test_a_corrupt_status_file_stops_every_command_and_is_never_read_as_empty`
(`{not json`, `[]`, empty), `test_a_deploy_started_during_the_run_stops_the_next_component`,
`test_the_run_hands_deploy_tag_its_lock_token`,
`test_deploy_tag_refuses_while_the_upgrade_lock_is_held_and_says_who` (runs
the real script's `deploy` with a tag that cannot exist and the lock state
in a temporary directory: refused with the holder; with the wrong token
refused; with the right token, and once free, it goes past the check and
stops at `no such tag`, before any docker call).

### F10 (K9): the loader and bind-mounted files

- `scripts/upgrades.py:165-174`: `deploy = "compose"` or `"ha"` with project
  `kronk` (or none) is refused; any component whose file is
  `docker-compose.ma.yml` or whose project is `kronk-ma` may only be
  `excluded` or `none`, with no deploy, whatever its name.
- `:1239` `bind_files_unreleased`, for telemetry and HA, after the config
  hash: which of the two the ruling offered: "compare with what the running
  container was started with". Every file the container bind-mounts from
  inside the repo is compared, by modification time, with the container's
  `State.StartedAt`; a file changed after the start declines that
  component ("a recreate would load it unreleased"). Binds outside the repo
  (HA's recordings, `/etc/localtime`) do not count. A touched but unchanged
  file declines too (errs toward not starting). The other option, "HEAD's
  last deployed state", has no record for telemetry or HA: neither is
  deployed from tags.
- Follow-up found by the live dry run: Grafana's dashboards were written at
  22:32:46Z on 2026-11-08, four minutes after the container started
  (22:28:30Z), and committed the next morning. Grafana's dashboard provider
  polls them every 30 s (`telemetry/grafana/provisioning/dashboards/kronk.yaml`),
  so the running Grafana has them, yet the guard would have held Grafana
  back until someone recreated it by hand. The component's `live_binds`
  (`scripts/upgrade_rules.toml:188`, `telemetry/grafana/dashboards`) sets
  that path aside; provisioning, loaded at start only, still counts.

Tests: `test_the_rules_refuse_a_compose_command_against_the_kronk_project`
(3), `test_the_rules_refuse_anything_in_the_music_assistant_stack` (2),
`test_a_component_in_project_kronk_ma_is_refused_whatever_its_name`,
`test_a_bind_mounted_file_changed_after_the_start_declines_that_component`,
`test_a_changed_file_in_a_bind_mounted_directory_counts`,
`test_binds_outside_the_repo_do_not_count`,
`test_the_real_ops_finds_files_changed_after_a_time`,
`test_dashboards_grafana_reloads_by_itself_do_not_hold_it_back`.

### F11 (K5): a full redeploy per Kronk-stack component

Not changed, documented: `docs/features/upgrade-cadence.md`, "What a
Kronk-stack upgrade costs"; the dry run's plan line says "a full production
redeploy; rollback: … another"; the notice adds "Production (the Kronk
stack) was redeployed N time(s) by this run" (`ctx.redeploys`, counted at
each `deploy_tag.sh deploy`, recovery included). Tested in
`test_killed_during_verify_finishes_the_rollback` ("redeployed 1 time(s)").

Proposal, not built. Group a night's Kronk-stack components into one
deploy while keeping one commit per component and a per-component rollback:

1. For each Kronk-stack component in order, run its per-component checks,
   edit and commit its pin (one commit each, as now), recording each in the
   intent record as one group.
2. Tag the last commit; one `deploy_tag.sh deploy`.
3. Verify each component's image and health checks separately, then the
   corpus and the replay once.
4. A component whose own image or health check fails: revert its commit
   (the others stay), tag, deploy again, verify. A corpus or replay
   regression cannot be attributed: deploy the previous tag (all back) and
   mark the group's components for one-at-a-time next night.

It saves one redeploy per extra component on a clean night and costs a
second deploy on a failed one, the same as today. It touches `minor_run`'s
loop, `_apply_one` (split into checks, commit, bring-up, verify) and the
intent record (a group, not one component), and recovery (a group to
finish). It is not small: an estimate of 150 to 200 lines and their tests.
Today at most two Kronk-stack components move in a month (LiteLLM and, by
interpretation, SearXNG; Piper and nginx rarely), so the gain is at most
two restarts a month. Recommendation: leave it.

### F12 (D1, D2): the release list and the registry

`Release` carries `listed`, `reg_date` and `digest`. `image_releases`
keeps the two sources apart: a tag the registry has and the release list
does not is `listed=False`, and the registry's date never fills a missing
release date (that fill was D1's first case). Planning
(`plan_component`): a candidate is looked up by tag on GitHub when unlisted
or undated (`fill_date`; a draft is not a release); then taken only when
listed, not a pre-release, dated, at least 14 days old, present in the
registry (`:536` `registry_info`), and, where the release list and the
registry are separate sources, with a registry date at least 14 days old
and not more than `moved_tag_grace_hours` (48, `scripts/upgrade_rules.toml:55`)
after the release. A later one is `MOVED TAG … not taken`, in the plan's
notes. Docker Hub gives `tag_last_pushed` and `digest` per tag. ghcr gives
no push date: `:567` `ghcr_created` reads the tag's manifest (its
`Docker-Content-Digest`), the linux/amd64 image's manifest and its config
blob's `created`; the blob GET redirects to
`pkg-containers.githubusercontent.com`, now the one extra allowed host, and
`:343` `_GuardedRedirects` follows redirects only to allowed hosts over
https (the tester's observation that redirects bypassed the allow-list).
The 48-hour grace is an interpretation: images are often pushed hours
before or after the GitHub release; "later than its release" read
literally would refuse most. At bring-up `:1467` `check_pulled` logs the
local image id and RepoDigests after the pull against the registry's digest
at planning; a different digest (the tag moved in between), or the image
missing, is `not applied` before anything restarts; `log_kept` logs the
image kept for rollback (for HA, the running image id and its digests).

Tests: `test_the_testers_divergent_cases_are_not_taken` (no date on
GitHub; registry only; a draft; a moved tag; a registry tag too young),
`test_a_registry_tag_pushed_within_the_grace_of_its_release_is_taken`,
`test_a_moved_ghcr_tag_is_not_taken`,
`test_the_log_records_the_digest_pulled_and_the_one_kept_for_rollback`,
`test_a_tag_that_moved_between_planning_and_the_pull_is_not_applied[grafana|homeassistant]`,
`test_a_redirect_is_followed_only_to_an_allowed_host`. The fake registries
(`tests/m18_helpers.py`: `hub_tag_lookup`, `ghcr_registry`, `fake_digest`)
answer per tag as the real ones do. Live: grafana 13.2.5 `released
2026-10-27, 22 days; registry 2026-10-27`; litellm v1.88.6 `released
2026-08-11, 98 days; registry 2026-08-09` (built two days before its
release: not moved). The rehearsal's log shows the digests recorded.

### F13 (D4)

No change. `playing` and `buffering` decline; a paused, idle or `on` player
does not. Stated in the feature doc, "House busy".

### F14: docs

- `docs/features/upgrade-cadence.md`: the monthly run rewritten as built
  (the old step 4/6 described a run B1 prevented); new sections House
  busy, Outcomes, An interrupted run, What a Kronk-stack upgrade costs; the
  release rule; "by hand" no longer suggests `--ignore-month` as if a
  same-day rerun were harmless: it says what makes it safe now, and that it
  does not bypass a block; gotchas for moved tags and the mtime guard.
- `docs/runbooks/major-upgrade.md` (the tester's seven points): `D` set once
  with a literal date and written down at H1; the archive tested
  (`gzip -t`, `tar tzf`) before R2 empties the volume, and again inside R2's
  own command; the compose edit and commit (now H3) before the stop and
  backup (now H4); the LiteLLM scratch container on host networking; the
  stop-and-start backups inside the window, with the restart after Grafana
  and Tempo; the telemetry rollback restores the volume when the new
  version migrated it; the M12 runbook's path (`docs/sprint/M12-fix1.md`,
  corrections in `M12-fix2.md`); H2's pass criterion: the new image's
  `music_assistant_client.constants.API_SCHEMA_VERSION` at least MA's
  `min_supported_schema_version` from `localhost:8095/info` (28 and 28
  today, read read-only from the running HA and MA); the backup's reason
  corrected (below).
- `docs/upgrades/2026-11-18.md`: a Corrections section and in-place fixes:
  Ollama "risk: low" withdrawn (six Ollama changes in the unread "All
  changes" lists; `ollama` 0.5.1 to 0.6.2); hassil 3.5.0 to 3.12.1, with
  3.6.0 and 3.7.0 in 2026.6.3 and the fuzzy matcher removed in 2026.6; no
  recorder migration (schema 53 in both), the backup kept for 2026.11's
  `.storage` entity-name migration; the bundled MA client 1.3.5 to 1.6.0;
  LiteLLM's notes read reach v1.100, not v1.103; nginx's date is a push
  date.
- `docs/sprint/M18-build.md`: a note at the top on what is superseded.
- `scripts/upgrades.py` docstring and the minor unit's Operations block.

## Both dry runs

`scripts/upgrade_minor.sh --dry-run`, real system, read-only. Hosts
contacted: `api.github.com`, `ghcr.io`, `hub.docker.com`, `pypi.org`, 76
requests each. The count includes litellm v1.88.6's manifest chain on
ghcr (the tag's manifest, the amd64 manifest, the config blob); ghcr
answers the blob GET with a redirect to `pkg-containers.githubusercontent.com`,
which was followed (that is where the `registry 2026-08-09` date comes
from) but is not in the printed list, because `Web.hosts` records the host
asked. After each: `data/upgrades` absent, `git status` unchanged by it.

### 1. As is (13:58:06Z, tree clean)

```
13:58:06Z upgrade_minor DRY RUN 2026-11-18T13:58:06+00:00 repo /home/drew/git-repos/drawsmcgraw/kronk branch rule 'main'
13:58:06Z lock /home/drew/git-repos/drawsmcgraw/kronk/data/upgrades/lock: free (dry run takes no lock)
13:58:14Z plan:
13:58:14Z   tempo                      3.1.1                  CURRENT   no newer release inside the minor line
13:58:14Z   grafana                    13.2.4                 UPGRADE   13.2.4 -> 13.2.5 (released 2026-10-27, 22 days; registry 2026-10-27)
13:58:14Z                                                               . 13.2.6: 7 days old, under 14
13:58:14Z   wyoming-piper              2.2.2                  CURRENT   no newer release inside the minor line
13:58:14Z   searxng                    2026.11.9-57ff753ad    CURRENT   newer minor releases exist, none taken yet
13:58:14Z                                                               . 2026.11.18-b867c56a5: 0 days old, under 14
13:58:14Z                                                               . 2026.11.17-b99cf7496: 1 days old, under 14
13:58:14Z                                                               . 2026.11.17-c9c5d1b9b: 1 days old, under 14
13:58:14Z                                                               . 2026.11.17-d3a35cf45: 1 days old, under 14
13:58:14Z                                                               . 2026.11.17-cf3c20177: 1 days old, under 14
13:58:14Z                                                               . 2026.11.15-2d500cb6b: 3 days old, under 14
13:58:14Z   nginx                      1.29-alpine            SKIP      pin '1.29-alpine' (docker-compose.yml:12) is not an exact version under the rule; never changed
13:58:14Z   litellm                    v1.88.1                UPGRADE   v1.88.1 -> v1.88.6 (released 2026-08-11, 98 days; registry 2026-08-09)
13:58:14Z   python-base-orchestrator   3.12-slim              SKIP      pin '3.12-slim' (orchestrator/Dockerfile:1) is not an exact version under the rule; never changed
13:58:14Z   python-base-tool_service   3.12-slim              SKIP      pin '3.12-slim' (tool_service/Dockerfile:1) is not an exact version under the rule; never changed
13:58:14Z   python-base-voice_tracer   3.12-slim              SKIP      pin '3.12-slim' (voice_tracer/Dockerfile:1) is not an exact version under the rule; never changed
13:58:14Z   uv                         uv                     SKIP      pin 'uv' (orchestrator/Dockerfile:13) is not an exact version under the rule; never changed
13:58:14Z   git-orchestrator           -                      NONE      a Debian package series pin; no registry to read
13:58:14Z   homeassistant              2026.5.4               CURRENT   no newer release inside the minor line
13:58:14Z   lock-orchestrator          41 packages            REPORT    12 patch release(s) available; report only
13:58:14Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               > charset-normalizer 3.4.7 -> 3.4.9
13:58:14Z                                                               > click 8.3.2 -> 8.3.3
13:58:14Z                                                               > fastapi 0.115.0 -> 0.115.14
13:58:14Z                                                               > googleapis-common-protos 1.75.0 -> 1.75.5
13:58:14Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
13:58:14Z                                                               > python-multipart 0.0.9 -> 0.0.33
13:58:14Z                                                               > pyyaml 6.0.2 -> 6.0.3
13:58:14Z                                                               > typing-inspection 0.4.2 -> 0.4.4
13:58:14Z   lock-tool_service          55 packages            REPORT    13 patch release(s) available; report only
13:58:14Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               > charset-normalizer 3.4.7 -> 3.4.9
13:58:14Z                                                               > click 8.3.2 -> 8.3.3
13:58:14Z                                                               > dateparser 1.4.0 -> 1.4.3
13:58:14Z                                                               > fastapi 0.115.0 -> 0.115.14
13:58:14Z                                                               > lxml 6.1.1 -> 6.1.3
13:58:14Z                                                               > lxml-html-clean 0.4.5 -> 0.4.6
13:58:14Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
13:58:14Z                                                               > soupsieve 2.8.3 -> 2.8.4
13:58:14Z                                                               > typing-inspection 0.4.2 -> 0.4.4
13:58:14Z   lock-voice_tracer          22 packages            REPORT    5 patch release(s) available; report only
13:58:14Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               . opentelemetry-semantic-conventions==0.66b1: not an exact X.Y[.Z] version; skipped
13:58:14Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
13:58:14Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
13:58:14Z   llama.cpp                  -                      NONE      llama.cpp builds have no stable scheme; any new build waits for the quarterly report and the model benches
13:58:14Z   wyoming-faster-whisper     -                      NONE      not in the minor/major table; a GPU (ROCm) build in a host venv, not a pin in the repo
13:58:14Z   ma-blueprint               -                      NONE      a hand-merged fork of the upstream blueprint
13:58:14Z   music-assistant            -                      EXCLUDED  Music Assistant is excluded (Drew 2026-11-14): local fork with the Pandora patch
13:58:14Z   bgutil-pot-provider        -                      EXCLUDED  part of the Music Assistant stack, excluded with it
13:58:14Z lookups: 76 request(s) to api.github.com, ghcr.io, hub.docker.com, pypi.org
13:58:14Z would do, in order (each its own commit; verify = image, health, corpus, replay, stack checks):
13:58:14Z   grafana: docker-compose.telemetry.yml: 13.2.4 -> 13.2.5; commit 'upgrade: grafana 13.2.4 -> 13.2.5'; compose -p kronk-telemetry pull grafana; up -d --no-deps grafana (rollback: pin back, up -d with the local previous image)
13:58:14Z   litellm: docker-compose.yml: v1.88.1 -> v1.88.6; commit 'upgrade: litellm v1.88.1 -> v1.88.6'; git tag upgrade-<run time>-litellm; deploy_tag.sh deploy it (a full production redeploy; rollback: pin back, deploy sprint-M21-step2, another)
13:58:14Z then: one notice through scripts/lib/notify.sh with what changed; the status file data/upgrades/status.json for /services
13:58:14Z production runs tag: sprint-M21-step2
13:58:14Z HEAD vs sprint-M21-step2: undeployed changes in orchestrator/main.py, orchestrator/static/services.html, orchestrator/upgrades_api.py (Kronk-stack components would be declined)
13:58:14Z docker-compose.telemetry.yml vs running kronk-telemetry-grafana-1: matches (config hash)
13:58:14Z bind-mounted files of kronk-telemetry-grafana-1: none changed since it started
13:58:14Z a real run now would DECLINE: outside the night window 02:00-05:00 local (now 08:58): the timer tries the next night; on branch 'refactor', expected 'main' (KRONK_UPGRADE_BRANCH / expected_branch); media_player.home_assistant_voice_0ac919_pe_01 is playing; media_player.home_assistant_voice_0ac919_media_player is playing
13:58:14Z dry run: nothing written, nothing changed, nothing sent
```

### 2. Right after a two-row dev replay

The replay: `m14-lookalike-decimal` and `m14-lookalike-time` first (the
rows the tester used): both were skipped by the replay's own guard, because
music was playing on the kitchen puck and both have a fuzzy local match, so
they reached nothing:

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 2 rows (158 not selected)
  s skip  skipped    m14-lookalike-decimal
  s skip  skipped    m14-lookalike-time
voice_replay: {'skip': 2}; executed 0, checked read-only 0; players unchanged (17 checked)
  skipped m14-lookalike-decimal: fuzzy local match (HassTimerStatus) while a player is active; not executed
  skipped m14-lookalike-time: fuzzy local match (HassLightSet) while a player is active; not executed
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-18_135831_m18-fix1-probe.json
```

Then two rows with no local match that touch no music, web search or
mirror, as the office satellite, not the playing one:

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 2 rows (158 not selected)
  . pass  executed   m19-ask-kronk-alone
  . pass  executed   m14-lookalike-am
voice_replay: {'pass': 2}; executed 2, checked read-only 0; players unchanged (17 checked)
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-18_135906_m18-fix1-probe.json
```

The replay ended 13:59:20Z. A dry run at 13:59:28Z saw the two `/dev/`
requests set aside but nothing held yet: voice_tracer holds a run only
after its Kronk join, which it logged at 13:59:42Z (`voice run
01M7QF8J3702AMEZCP7X1JBZY3 held for the correction window`). At 13:59:52Z
the probe said `held 1` and the state file listed that id. Dry run 2, at
13:59:52Z:

```
13:59:52Z upgrade_minor DRY RUN 2026-11-18T13:59:52+00:00 repo /home/drew/git-repos/drawsmcgraw/kronk branch rule 'main'
13:59:52Z lock /home/drew/git-repos/drawsmcgraw/kronk/data/upgrades/lock: free (dry run takes no lock)
13:59:58Z plan:
13:59:58Z   tempo                      3.1.1                  CURRENT   no newer release inside the minor line
13:59:58Z   grafana                    13.2.4                 UPGRADE   13.2.4 -> 13.2.5 (released 2026-10-27, 22 days; registry 2026-10-27)
13:59:58Z                                                               . 13.2.6: 7 days old, under 14
13:59:58Z   wyoming-piper              2.2.2                  CURRENT   no newer release inside the minor line
13:59:58Z   searxng                    2026.11.9-57ff753ad    CURRENT   newer minor releases exist, none taken yet
13:59:58Z                                                               . 2026.11.18-b867c56a5: 0 days old, under 14
13:59:58Z                                                               . 2026.11.17-b99cf7496: 1 days old, under 14
13:59:58Z                                                               . 2026.11.17-c9c5d1b9b: 1 days old, under 14
13:59:58Z                                                               . 2026.11.17-d3a35cf45: 1 days old, under 14
13:59:58Z                                                               . 2026.11.17-cf3c20177: 1 days old, under 14
13:59:58Z                                                               . 2026.11.15-2d500cb6b: 3 days old, under 14
13:59:58Z   nginx                      1.29-alpine            SKIP      pin '1.29-alpine' (docker-compose.yml:12) is not an exact version under the rule; never changed
13:59:58Z   litellm                    v1.88.1                UPGRADE   v1.88.1 -> v1.88.6 (released 2026-08-11, 98 days; registry 2026-08-09)
13:59:58Z   python-base-orchestrator   3.12-slim              SKIP      pin '3.12-slim' (orchestrator/Dockerfile:1) is not an exact version under the rule; never changed
13:59:58Z   python-base-tool_service   3.12-slim              SKIP      pin '3.12-slim' (tool_service/Dockerfile:1) is not an exact version under the rule; never changed
13:59:58Z   python-base-voice_tracer   3.12-slim              SKIP      pin '3.12-slim' (voice_tracer/Dockerfile:1) is not an exact version under the rule; never changed
13:59:58Z   uv                         uv                     SKIP      pin 'uv' (orchestrator/Dockerfile:13) is not an exact version under the rule; never changed
13:59:58Z   git-orchestrator           -                      NONE      a Debian package series pin; no registry to read
13:59:58Z   homeassistant              2026.5.4               CURRENT   no newer release inside the minor line
13:59:58Z   lock-orchestrator          41 packages            REPORT    12 patch release(s) available; report only
13:59:58Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               > charset-normalizer 3.4.7 -> 3.4.9
13:59:58Z                                                               > click 8.3.2 -> 8.3.3
13:59:58Z                                                               > fastapi 0.115.0 -> 0.115.14
13:59:58Z                                                               > googleapis-common-protos 1.75.0 -> 1.75.5
13:59:58Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
13:59:58Z                                                               > python-multipart 0.0.9 -> 0.0.33
13:59:58Z                                                               > pyyaml 6.0.2 -> 6.0.3
13:59:58Z                                                               > typing-inspection 0.4.2 -> 0.4.4
13:59:58Z   lock-tool_service          55 packages            REPORT    13 patch release(s) available; report only
13:59:58Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               > charset-normalizer 3.4.7 -> 3.4.9
13:59:58Z                                                               > click 8.3.2 -> 8.3.3
13:59:58Z                                                               > dateparser 1.4.0 -> 1.4.3
13:59:58Z                                                               > fastapi 0.115.0 -> 0.115.14
13:59:58Z                                                               > lxml 6.1.1 -> 6.1.3
13:59:58Z                                                               > lxml-html-clean 0.4.5 -> 0.4.6
13:59:58Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
13:59:58Z                                                               > soupsieve 2.8.3 -> 2.8.4
13:59:58Z                                                               > typing-inspection 0.4.2 -> 0.4.4
13:59:58Z   lock-voice_tracer          22 packages            REPORT    5 patch release(s) available; report only
13:59:58Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               . opentelemetry-semantic-conventions==0.66b1: not an exact X.Y[.Z] version; skipped
13:59:58Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
13:59:58Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
13:59:58Z   llama.cpp                  -                      NONE      llama.cpp builds have no stable scheme; any new build waits for the quarterly report and the model benches
13:59:58Z   wyoming-faster-whisper     -                      NONE      not in the minor/major table; a GPU (ROCm) build in a host venv, not a pin in the repo
13:59:58Z   ma-blueprint               -                      NONE      a hand-merged fork of the upstream blueprint
13:59:58Z   music-assistant            -                      EXCLUDED  Music Assistant is excluded (Drew 2026-11-14): local fork with the Pandora patch
13:59:58Z   bgutil-pot-provider        -                      EXCLUDED  part of the Music Assistant stack, excluded with it
13:59:58Z lookups: 76 request(s) to api.github.com, ghcr.io, hub.docker.com, pypi.org
13:59:58Z voice_tracer: set aside 1 held run(s) on kronk-dev or from this run's replay
13:59:58Z nginx: set aside 2 request(s) on the dev path (/dev/) and 0 of this run's own (User-Agent kronk-upgrade/20261118T135952Z-c5b24c359e55)
13:59:58Z would do, in order (each its own commit; verify = image, health, corpus, replay, stack checks):
13:59:58Z   grafana: docker-compose.telemetry.yml: 13.2.4 -> 13.2.5; commit 'upgrade: grafana 13.2.4 -> 13.2.5'; compose -p kronk-telemetry pull grafana; up -d --no-deps grafana (rollback: pin back, up -d with the local previous image)
13:59:58Z   litellm: docker-compose.yml: v1.88.1 -> v1.88.6; commit 'upgrade: litellm v1.88.1 -> v1.88.6'; git tag upgrade-<run time>-litellm; deploy_tag.sh deploy it (a full production redeploy; rollback: pin back, deploy sprint-M21-step2, another)
13:59:58Z then: one notice through scripts/lib/notify.sh with what changed; the status file data/upgrades/status.json for /services
13:59:58Z production runs tag: sprint-M21-step2
13:59:58Z HEAD vs sprint-M21-step2: undeployed changes in orchestrator/main.py, orchestrator/static/services.html, orchestrator/upgrades_api.py (Kronk-stack components would be declined)
13:59:58Z docker-compose.telemetry.yml vs running kronk-telemetry-grafana-1: matches (config hash)
13:59:58Z bind-mounted files of kronk-telemetry-grafana-1: none changed since it started
13:59:58Z a real run now would DECLINE: outside the night window 02:00-05:00 local (now 08:59): the timer tries the next night; on branch 'refactor', expected 'main' (KRONK_UPGRADE_BRANCH / expected_branch); working tree not clean (2 path(s): docs/bench/voice_replay_2026-11-18_135831_m18-fix1-probe.json, docs/bench/voice_replay_2026-11-18_135906_m18-fix1-probe.json); media_player.home_assistant_voice_0ac919_pe_01 is playing; media_player.home_assistant_voice_0ac919_media_player is playing
13:59:58Z dry run: nothing written, nothing changed, nothing sent
```

The decline is real, not the run's own traffic: outside the night window,
the branch (`refactor`, not `main`), the two replay result files just
written, and music playing in the kitchen. Before the fix, the tester's
dry run 114 s after a replay printed `voice_tracer holds 2 run(s) in the
correction window; 2 chat/voice request(s) in the last 5 minutes`.
Players before and after the replay: unchanged (the replay checks 17).

## The rehearsal

`scripts/upgrade_rehearsal.py` with this round's code, 13:46 to 13:57Z,
each scenario in its own scratch directory. Its only change: every run
passes `ignore_window=True`. Before: no busybox image, no `m18-rehearsal`
project or container. It touched compose project `m18-rehearsal` only
(`busybox:1.36.0`/`1.36.1`, `network_mode: none`, no volumes).

A, kept:
```
13:46:17Z   committed d496f4a: upgrade: busybox-scratch 1.36.0 -> 1.36.1
13:46:17Z   kept for rollback: busybox:1.36.0 = sha256:9e2bbca079387d7965c3a9cee6d0c53f4f4e63ff7637877a83c4c05f2a666112 (busybox@sha256:9e2bbca079387d7965c3a9cee6d0c53f4f4e63ff7637877a83c4c05f2a666112)
13:46:18Z   docker compose -p m18-rehearsal pull box: exit 0
13:46:18Z   image pulled: busybox:1.36.1 = sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 (busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662); the registry gave sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 at planning
13:46:28Z   docker compose -p m18-rehearsal up -d --no-deps box: exit 0
13:46:28Z   verified: busybox-scratch 1.36.1 kept
[stub notifier] Kronk upgrade: 1 upgraded
13:46:28Z done: upgraded
exit 0
--- running: busybox:1.36.1 true
```

B, a real health-check failure, rolled back:
```
13:51:59Z   FAILED: check `sh -c busybox | head -1 | grep -qv 'v1.36.1 '` in m18-rehearsal-box-1 failed (exit 1)
13:51:59Z   rolling back busybox-scratch to 1.36.0
13:52:09Z   rolled back and verified: busybox-scratch 1.36.0
[stub notifier] Kronk upgrade: 1 rolled back
    busybox-scratch 1.36.0 -> 1.36.1: rolled back to 1.36.0 (check `sh -c busybox | head -1 | grep -qv 'v1.36.1 '` in m18-rehearsal-box-1 failed (exit 1))
13:52:09Z notice sent: Kronk upgrade: 1 rolled back
13:52:09Z done: rolled_back
exit 1
--- running: busybox:1.36.0 true
    "state": "rolled back to 1.36.0",
  "summary": "Kronk upgrade: 1 rolled back"
   "text": "Kronk upgrade: 1 rolled back. Details: /tmp/claude-1000/-home-drew-git-repos-drawsmcgraw-kronk/09c4fdb3-10cd-4eb6-b1fa-c2b72530c821/scratchpad/rehearsal/B-forced-failure-rollback/state/runs/20261118T134647Z-minor.log"
```

C, the rollback fails (the previous image removed by the harness), the
next run declines, `ack`:
```
13:57:27Z   ROLLBACK FAILED: compose up of 1.36.0 failed: Container m18-rehearsal-box-1 Recreate Container m18-rehearsal-box-1 Error response from daemon: No such image: busybox:1.36.0 Error response from daemon: No such image: busybox:1.36.0
13:57:27Z stopping: busybox-scratch rollback failed; the remaining components wait
[stub notifier] Kronk upgrade: ROLLBACK FAILED (busybox-scratch)
    busybox-scratch 1.36.0 -> 1.36.1: ROLLBACK FAILED; running is 1.36.1; git has 1.36.0; needs you: later runs wait until `python3 scripts/upgrades.py ack` (upgrade failed: check `sh -c busybox | head -1 | grep -qv 'v1.36.1 '` in m18-rehearsal-box-1 failed (exit 1); rollback failed: compose up of 1.36.0 failed: Container m18-rehearsal-box-1 Recreate Container m18-rehearsal-box-1 Error response from daemon: No such image: busybox:1.36.0 Error response f…)
13:57:27Z notice sent: Kronk upgrade: ROLLBACK FAILED (busybox-scratch)
13:57:27Z done: rollback_failed
exit 2
--- running: busybox:1.36.1 true
    "state": "ROLLBACK FAILED; running is 1.36.1; git has 1.36.0",
  "summary": "Kronk upgrade: ROLLBACK FAILED (busybox-scratch)"
  "state": "ROLLBACK FAILED; running is 1.36.1; git has 1.36.0"
   "text": "Upgrade rollback FAILED for busybox-scratch: ROLLBACK FAILED; running is 1.36.1; git has 1.36.0. upgrade failed: check `sh -c busybox | head -1 | grep -qv 'v1.36.1 '` in m18-rehearsal-box-1 failed (exit 1); rollback failed: compose up of 1.36.0 fa
13:57:27Z declined: the last rollback failed (busybox-scratch: ROLLBACK FAILED; running is 1.36.1; git has 1.36.0 upgrade failed: check `sh -c busybox | head -1 | grep -qv 'v1.36.1 '` in m18-rehearsal-box-1 failed (exit 1); rollback failed: compose up of 1.36.0 failed: Cont); fix it, then `python3 scripts/upgrades.py ack`
exit 0
last_run: declined (attempt 2 of 7): the last rollback failed (busybox-scratch: ROLLBACK FAILED; running is 1.36.1; git has 1.36.0 upgrade failed: check `sh -c busybox | head -1 | grep -qv 'v1.36.1 '` in m18-rehearsal-box-1 failed (exit 1); rollback failed: compose up of 1.36.0 failed: Cont); fix it, then `py
cleared: 1 warning(s), the failed-rollback block
exit 0
rollback_failed after ack: None
```

Cleanup: `busybox:1.36.0 absent`, `busybox:1.36.1 Untagged`, `left:
nothing`. Afterwards every container's name, image and start time, `docker
images`, networks, volumes and `docker compose ls -a` are identical to the
record taken before (diffs empty). Real and stubbed as in the build: the
Docker Hub lookups and pulls, git, the lock, status and log, compose
`config --hash`, `pull`, `up`, `inspect`, the `exec` health check are real;
HA's states, the voice_tracer probe and nginx's log (quiet), the corpus,
the replay and the notifier are stubbed. So the rehearsal still does not
exercise B1's territory; `tests/test_m18_fix1.py` does, with a fake that
records the run's own traffic, and dry run 2 shows it live.

## Install and uninstall (for the coordinator)

Before installing: production must run a tag that contains M18
(`/api/upgrades` answers on production); deploy it the usual way
(`scripts/deploy_tag.sh plan <tag>`, then `deploy`). The units changed in
this round (the `After=` lines), so copy them again even if an earlier copy
exists. `scripts/deploy_tag.sh`'s lock check and User-Agent run from the
working tree, as the run executes it from HEAD.

```
cp systemd/kronk-upgrade-{minor,major-check,notices}.{service,timer} ~/.config/systemd/user/
# only if production's tags are still made on refactor (Drew's decision, below):
sed -i 's/^Environment=KRONK_UPGRADE_BRANCH=main$/Environment=KRONK_UPGRADE_BRANCH=refactor/' \
    ~/.config/systemd/user/kronk-upgrade-minor.service ~/.config/systemd/user/kronk-upgrade-major-check.service
systemctl --user daemon-reload
systemd-analyze --user verify ~/.config/systemd/user/kronk-upgrade-*.service ~/.config/systemd/user/kronk-upgrade-*.timer
systemctl --user enable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
systemctl --user list-timers 'kronk-upgrade-*'      # next: 2026-12-01 03:30, 2027-01-08 04:30, the next hour
scripts/upgrade_minor.sh --dry-run                   # reads only; by day it declines on the night window, as it should
python3 scripts/upgrades.py test-notice              # the one real test notice: "Kronk: TEST upgrade notice"
```

Uninstall:

```
systemctl --user disable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
rm ~/.config/systemd/user/kronk-upgrade-{minor,major-check,notices}.{service,timer}
systemctl --user daemon-reload
```

A run by hand at any hour: `scripts/upgrade_minor.sh --ignore-window`
(add `--ignore-month` when the month is done). It holds the lock, so a
`deploy_tag.sh deploy` started meanwhile is refused with the holder named.

## Still Drew's decision

1. The branch and the commits (D3, left as built): the units say `main`;
   production's tags are made on `refactor` during the sprint. Whether the
   run commits unattended at all, or only reports.
2. The notice path when HA is what failed (as in the build report). Related
   gap: a run killed on the last night of the window (day 7) is seen only
   by next month's first run; if it left HA stopped, HA stays down until
   then, with the warning on `/services` and the notice pending (it goes
   through HA). A small change would let the hourly `notices` unit notice
   an unfinished intent record with the lock free and raise the warning
   and notice within the hour, or bring a stopped HA back itself. Not
   built: the ruling has the next run do it.
3. The 48-hour moved-tag grace, and ghcr's image build time standing in
   for a push date (F12's interpretations).
4. The modification-time guard for telemetry: a touched but unchanged file
   holds the component back until it is recreated by hand; `live_binds`
   lists what reloads by itself (Grafana's dashboards).
5. Before the HA window: the corrected report says the Ollama risk is not
   known; the "All changes" lists (six Ollama changes) need reading.
6. As in the build report: SearXNG's rule, lockfiles report only, exact
   pins for nginx, the Python bases and uv, the first major upgrade (HA).
7. F11's grouping proposal: recommended against, above.

## Before and after

Nothing in production, Home Assistant, Music Assistant, the pucks, the
Whisper or llama units changed. Every container's name, image and start
time at the end equals the record at the start of the rehearsal and the
end of the session (diff empty); `docker images` likewise. No
`kronk-upgrade-*` unit in `~/.config/systemd/user`; no `upgrade-*` git tag;
`data/upgrades` absent; no symlink in the scratch directory; no background
job. Dev pair untouched and healthy: `kronk-orchestrator-dev-1` with
`TELEMETRY_RECORD_TEXT=true`, `MODEL_LOCK=server`, `VOICE_STREAM=on`,
`MUSIC_DRY_RUN=1`, `SETTINGS_PASSPHRASE_HASH=` (empty);
`kronk-tool_service-dev-1` with `MUSIC_DRY_RUN=1`; `/dev/api/status`
`music_dry_run: True`. Read-only reads made beyond the dry runs: HA's
`kronk-dev` and `kronk` pipeline run lists (websocket), the HA compose
render, `docker exec homeassistant` reading the MA client's constant,
MA's `/info`, telemetry containers' mounts and start times, nginx's log,
voice_tracer's probe, log and state file.

## Tests

`./scripts/run_tests.sh -q -p no:cacheprovider` at the commit before this
report. Build: `5138 passed, 3 skipped, 53 xfailed`. Now:

```
=============================== warnings summary ===============================
orchestrator/main.py:814
tests/test_config_fix2.py::test_a_stray_file_whose_name_is_not_utf8_still_answers
  /home/drew/git-repos/drawsmcgraw/kronk/orchestrator/main.py:814: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class _OpenAIChatRequest(BaseModel):

orchestrator/main.py:1044
tests/test_config_fix2.py::test_a_stray_file_whose_name_is_not_utf8_still_answers
  /home/drew/git-repos/drawsmcgraw/kronk/orchestrator/main.py:1044: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class _OllamaMessage(BaseModel):

orchestrator/main.py:1052
tests/test_config_fix2.py::test_a_stray_file_whose_name_is_not_utf8_still_answers
  /home/drew/git-repos/drawsmcgraw/kronk/orchestrator/main.py:1052: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class _OllamaChatRequest(BaseModel):

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
5247 passed, 3 skipped, 53 xfailed, 6 warnings in 69.73s (0:01:09)
```

109 more passed; the M18 tests alone: `258 passed` (build: 149). The six
warnings are the same `PydanticDeprecatedSince20` lines as the build's.
