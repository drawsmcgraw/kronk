# M16 fix round 1: end-to-end findings

Status: in progress

Branch `refactor`. Fixes the findings of `docs/sprint/M16-e2e.md` (verdict
FAILS) under `docs/sprint/M16-criteria.md`. Code commit `779fdf3`; this
report and the build report's Corrections section follow it.

Tests before: `5506 passed, 3 skipped, 53 xfailed` (the e2e report's run).
Tests after: `5582 passed, 3 skipped, 53 xfailed, 6 warnings in 75.18s`
(`./scripts/run_tests.sh`, 2026-11-19 11:12). `tests/test_m16_backups.py`
went from 49 to 125 tests.

The approach for the three blockers was to remove the risky code paths
rather than guard them: the archive no longer reclaims any temporary file
it did not create in the same run, no longer opens anything in either tree
by a path that the kernel resolves through symlinks, and no longer deletes
on the strength of a check made minutes earlier.

## Findings

### Blockers

**1. A source named like the temporary file was destroyed.**
- `scan_recordings` (`scripts/backups.py:1304`) reports and skips any
  source file whose name starts with `.kronk-archive`; it is never copied.
- The archive's temporary file is now `.kronk-archive.<pid>.<16 hex>.tmp`,
  created `O_EXCL` in `_copy_new` (`scripts/backups.py:1400`); a taken name
  is skipped, never removed. `_unlink_own` (`:1316`) removes a name only
  when it is still the file this run created (same device and inode).
- The old "remove a leftover temp" code is deleted. Leftovers from a killed
  run stay on the destination; the end notice and `dest_totals` count them
  (`tree_size`, `:486`; `totals_line`, `:510`).
- The copy is placed with `link(2)` (`:1464`), which cannot replace an
  existing name; a share without hard links gets a rename after a fresh
  check (residual window: between that check and the rename).
- Tests: `test_finding1_a_source_named_like_the_temporary_file_is_reported_and_never_copied`,
  `test_finding1_the_copy_never_unlinks_a_destination_file_it_did_not_create`,
  `test_finding1_the_temporary_file_is_removed_only_while_it_is_still_ours`,
  `test_fault_killed_mid_copy_the_leftover_is_kept_and_reported_never_removed`,
  `test_a_file_that_appears_under_the_final_name_during_the_copy_is_never_replaced`.

**2. A file replaced after its verify was deleted.**
- Each verified file becomes a `Verified` record (`:1224`): its size,
  mtime, device:inode and SHA-256, and the destination copy's
  device:inode.
- Deletes go in batches of 200 (`DELETE_BATCH`, `:91`) as soon as a batch
  fills and at the end (`_archive_files`, `:1651`, flush at `:1700`).
  Before each batch the host re-checks every destination copy:
  regular file, same size, same device:inode, reached without a symlink
  (`dest_copy_problem`, `:1494`, called at `:1664`).
- The container's script (`DELETE_SCRIPT`, `:244`) then checks each file
  right before its `rm`: every directory on both paths is a plain directory;
  the local file is a regular file whose SHA-256, size, mtime (seconds)
  and device:inode equal the copied file's; the destination copy is a
  regular file with that size and the verified device:inode. Only then
  `rm --` (no `-r`, no `-f`) and `rmdir --` of the run directory. Device
  and inode numbers agree between the host and `alpine:3.20` (checked:
  `46:33510319` both sides).
- A refused or vanished file is kept and reported; the run is FAILED.
- Residual window: inside the script, between the last `stat` and the `rm`
  (one shell line). Documented in the feature doc.
- Tests: `test_finding2_a_file_swapped_after_its_verify_is_kept_and_the_run_fails`,
  `test_finding2_a_destination_copy_that_changes_after_its_verify_stops_the_delete[vanished|replaced|symlinked]`,
  `test_the_delete_script_refuses_anything_but_the_verified_file` (10
  cases, run with the host's `sh` on a real tree),
  `test_the_delete_script_removes_only_the_verified_file`,
  `test_a_kill_between_verify_and_delete_is_finished_by_the_next_run`.

**3. A symlinked directory under `<dest>/recordings` made a file its own
backup.**
- Both trees are opened level by level with `O_NOFOLLOW` (`open_chain`,
  `:1247`; `O_DIR`, `:1243`); missing destination levels are made 0700 the
  same way. A symlink anywhere on either path is a conflict; nothing is
  written through it.
- A symlinked recordings root, a symlinked `<dest>/recordings`, and trees
  that contain each other are refused before anything is read
  (`trees_problem`, `:1603`).
- A destination file with the source's device and inode is a conflict,
  never "already archived" (`_already_there`, `:1377`).
- Source files are opened `O_NOFOLLOW | O_NONBLOCK`, so a FIFO swapped in
  mid-run is left alone instead of hanging the run (the old code blocks
  forever on it: found while running the new tests against the old code).
- Tests: `test_finding3_a_symlinked_destination_run_directory_never_verifies_a_file_against_itself`,
  `test_finding3_a_symlinked_device_directory_gets_nothing_written_through_it`,
  `test_finding3_samefile_is_a_conflict_never_already_archived`,
  `test_finding3_a_symlinked_recordings_root_is_refused`,
  `test_overlapping_trees_are_refused[dest-in-src|src-in-dest]`,
  `test_a_directory_swapped_for_a_symlink_mid_run_is_not_followed`,
  `test_a_fifo_swapped_in_mid_run_never_hangs_the_run`.

### Should fix

**4. `--no-notify`.** On `config`, `archive` and `check` (`main`, `:1786`;
`Ops.notify`, `:166`). It wins over `KRONK_BACKUP_NOTIFY_CMD` and over an
empty environment. The wrappers' usage blocks and the feature doc's "By
hand" section name it. Test:
`test_no_notify_sends_nothing_even_without_a_stub[config|archive|check]`
(the real `Ops.notify`, no stub in the environment, fails if `notify.sh`
runs).

**5. Incomplete tars.** Required items: HA's `/config`, MA's
`settings.json`, `.env`, `kronk-config/prod` and `dev`,
`~/.config/systemd/user` (`required_sources`, `:581`). A gap in any of them
names the tar `kronk-config-<date>[.n].incomplete.tar.gz`, titles the
notice `Kronk backup: config backup INCOMPLETE`, records result
`incomplete`, exits 4, and skips retention (`:1196`). `CONFIG_RE` does not
match the incomplete name, so retention and the stale check never count it.
The unpushed sentence is kept (`gaps +=`). The feature doc has a "Required
and optional" table. `/api/backups` and the stale check warn on
`incomplete`. Tests: `test_a_missing_required_item_makes_the_tar_incomplete`
(7 cases, 13 complete tars present, none removed),
`test_an_incomplete_tar_never_counts_toward_retention_and_a_later_good_run_keeps_twelve`,
`test_what_cannot_be_read_is_listed_in_the_manifest_and_the_notice`.

**6 to 8, 10, note 23. Runbook.** See "Runbook steps re-run" below.

**9. The runbook travels with the tar.** Members `RESTORE.md` and
`BACKUPS.md` (`file_sources`, `:552`); `repo-unpushed.bundle`, a `git
bundle` of HEAD, the branch and the production tags less everything any
remote-tracking branch has, only when something is unpushed, capped at
20 MB (`KRONK_BACKUP_BUNDLE_MAX_MB`; over it, left out and said in the
notice and `repo-state.txt`) (`unpushed_bundle`, `:775`). `repo-state.txt`
gains `unpushed bundle:` and `production tag <t> on the remote:` lines
(`tag_on_remote`, `:827`): local refs only, never `git fetch`; git keeps no
remote-tracking refs for tags, so the tag itself is "unknown" unless its
commit is on no remote branch, and the line gives the `git tag <t>
<commit>` to recreate it. The runbook's intro and step 2 carry the fstab
line and say how to get the runbook out of the newest tar from any
computer. Tests: `test_the_tar_carries_the_runbook_and_the_feature_doc`,
`test_no_bundle_when_nothing_is_unpushed_and_none_over_the_cap`,
`test_the_tag_on_the_remote_from_local_refs_only` (fails if `fetch`,
`ls-remote`, `pull` or `push` is run), and the first test's bundle and
repo-state assertions.

**11. 30,000 files.** Batches of 200 per container, paths as arguments
(10 per file). Test: `test_thirty_thousand_deletes_go_in_batches` (150
calls, every name once, each argv under 128 KiB). For real in the sweep:
30,000 files moved in 71 s.

**12. Page cache.** `sha256_fd` (`:905`) calls `posix_fadvise(DONTNEED)`
before the first read and after each chunk; the archive drops the
temporary file's pages after `fsync` (`:1430`) and reads it back through a
fresh descriptor whose inode must be the one written; the config job does
the same. The feature doc says what this proves on SMB (the bytes came back
from the NAS over the wire, not from local memory) and what it does not
(that they are on the NAS's disks), and how to confirm on the first mount
(`/proc/fs/cifs/Stats`). Test: `test_the_read_back_drops_the_page_cache_first`;
`test_fault_the_copy_reads_back_different` fails if the read-back stops
dropping the cache.

**13. Surviving mutants.** Each survivor the tester marked as a real
behaviour change is pinned:

| Mutant | Test |
| --- | --- |
| v05 size only in "already there" | `test_same_size_different_content_on_the_destination_is_a_conflict` |
| v15 non-file of the same name | `test_a_non_file_of_the_same_name_on_the_destination_is_a_conflict` |
| s06 depth rule | `test_files_at_the_wrong_depth_are_never_taken_as_recordings` |
| d02, d03 `rm -rf` | `test_the_delete_script_text_is_pinned` (exact text; the only `rm --`/`rmdir --` line), `..._refuses_anything_but_the_verified_file[dir]` |
| d10 `:ro` on the helper mount | `test_the_etc_kronk_helper_mount_is_read_only` |
| m06 `--require-mount` | `test_the_require_mount_flag_on_the_command_line` |
| r06 retention's directory | `test_keep_zero_removes_nothing_and_retention_touches_only_config` |
| r08 `KEEP=0` | the same test |
| g02, g03 size default and unit | `test_the_size_guard_default_and_unit` |
| t10, t11 archive modes | `test_old_recordings_move_keeping_names_and_times_young_ones_stay` (0600 file, 0700 at every level including `recordings/`) |
| t15 temp left after an exception | `test_an_exception_mid_write_leaves_no_temporary_file` |
| l03, l04 upgrade lock created / kept | `test_upgrade_lock_probe_never_creates_it_and_never_keeps_it` |
| n06 missing required file recorded as absent | `test_a_missing_required_item_makes_the_tar_incomplete[env-missing]` |

My own mutant pass over the new guards (29 one-line mutants of
`scripts/backups.py` and `orchestrator/backups_api.py`, each against the
M16 tests): 28 killed. The survivor is equivalent: removing the `//`
collapse in `mount_root_for` changes nothing because the `realpath` branch
collapses it too. Four earlier survivors got tests
(`test_a_same_size_rewrite_during_the_copy_is_left_for_the_next_run`,
`test_local_exists_counts_what_it_cannot_see_as_still_there`,
`test_a_destination_under_the_share_on_another_filesystem_is_refused`, and
13 complete tars in the incomplete test).

Against the old code: the new test file run against `scripts/backups.py`
and `orchestrator/backups_api.py` from `9b1a2e1` gives `85 failed, 39
passed, 1 deselected`. Many of the 85 fail on the changed interfaces (the
fake's delete signature, `build_tar`) rather than on behaviour; the
behaviour of the old code on the three blockers is what the e2e report
reproduced. The deselected test is the FIFO one: on the old code it hangs.

### Notes

- **14.** `mount_root_for` (`:337`): a destination at or under
  `/mnt/nas-backup`, lexically (`.`, `..`, doubled slashes) or through a
  symlink, needs the share mounted and must be on its filesystem
  (`dest_problem`, `:439`). Tests:
  `test_any_path_at_or_under_the_share_needs_the_mount` (8 forms),
  `test_a_symlink_to_the_share_needs_the_mount`,
  `test_a_destination_under_the_share_must_be_on_the_mounted_share`,
  `test_a_destination_under_the_share_on_another_filesystem_is_refused`.
- **15.** Not changed (no defect; the docs already say a backup waits for an
  upgrade run and not the reverse).
- **16.** Each real job waits up to 180 s for the share
  (`KRONK_BACKUP_MOUNT_WAIT_S`, polled every 10 s, `:448`). Test:
  `test_the_job_waits_a_bounded_time_for_the_mount`. By hand: `check` on the
  real default waited 20 s (`KRONK_BACKUP_MOUNT_WAIT_S=20`) and warned.
- **17.** `/api/backups`: a job with no `last_ok_at` warns "has never
  finished a good run"; no status file at all warns; a string of warnings
  is one warning; any other type is a warning
  (`orchestrator/backups_api.py:52`, `:61`, `:78`). Dev pair rebuilt; see
  below. Tests: `test_api_without_a_status_file_warns`,
  `test_api_a_job_that_never_succeeded_is_a_warning_and_a_string_is_one_warning`.
- **18.** A file that does not fit is skipped and reported; files smaller
  than the smallest one that did not fit still go (`:1689`). Tests:
  `test_a_file_that_does_not_fit_is_skipped_and_smaller_later_files_still_go`,
  `test_fault_destination_full_mid_copy`.
- **19.** The tar is built in memory (`build_tar`, `:983`) and size-checked
  (`:1167`) before anything is opened on the destination. Test:
  `test_the_size_guard_checks_before_anything_reaches_the_destination`.
- **20.** A symlinked recordings root is refused (finding 3). Number
  settings are validated with a minimum each; a bad one fails the job with
  a notice and a status line, exit 2, nothing done (`bad_settings`,
  `:1822`). Test: `test_a_bad_numeric_setting_is_a_clear_failure_with_a_notice`
  (3 jobs). `KRONK_BACKUP_KEEP=-3` is now this failure instead of "keep
  all"; `0` still keeps all. Not changed: a declined job still writes
  `status.json` without the lock (not reproduced as a lost update), and
  "remain local" still counts files at recording depth only.
- **21, 22.** CLAUDE.md's secrets paragraph says the NAS credentials file
  is pending Drew's install. `docs/sprint/M16-build.md` gains a
  "Corrections" section (29 unit files, 28 identical, rechecked; 28 GGUFs;
  the MA settings size changes).
- **23.** Runbook items below. In the code: the tar's top directory is a
  0700 member named after the file, so a same-day `.2` tar unpacks into
  `kronk-config-<date>.2/` (test
  `test_a_same_day_second_tar_unpacks_into_its_own_directory`); HA's
  `/config` is listed first, `custom_components/`, `www/`, `themes/` and
  `packages/` are copied when present, and any top-level name neither
  copied nor knowingly left out is reported as `UNKNOWN` in the MANIFEST
  and the notice (`HA_WANTED`, `:600`; test
  `test_unknown_ha_files_are_reported_and_known_ones_copied_by_name`).

Also changed: the job's lock descriptor is closed in a `finally`, so an
interrupt inside a run no longer keeps the lock for the rest of the
process (found by the kill-between-verify-and-delete test).

## Sweep

Every row of the tester's "What was tried on the archive" table, the three
blocker reproductions, and my own variations, against the committed code,
with the real delete through `alpine:3.20` (`KRONK_ARCHIVE_DELETE=docker`)
on root-owned scratch trees (built through `alpine:3.20`, `--network
none`). Every run had `no_notify` set and `KRONK_BACKUP_NOTIFY_CMD` pointed
at a stub on the same command line; the stub's log was never created.
Driver: a Python harness in the scratch directory that imports
`scripts/backups.py`; faults are injected by wrapping its functions or, for
the races, by a root container acting on the tree while the job runs in a
child process.

| Case | Result |
| --- | --- |
| Three root-owned copies of real recordings (read-only mount of `~/kronk-voice-recordings`) | PASS: moved 3, hashes equal, mtimes kept, 0600; rerun moved 0 |
| Read-back faults before the hash: temp missing, one byte short, one bit flipped, whole `recordings/` removed | PASS (4): FAILED, local kept, no temp left |
| Destination copy after its verify: removed, replaced with the same bytes, replaced by a symlink to the source, run directory swapped for a symlink to the source run | PASS (4): that file kept locally, the other moved, FAILED |
| Absolute and relative symlinked files, symlinked run and device directories | PASS: 4 reported, not followed, outside tree identical |
| Symlinked recordings root | PASS: exit 2, refused, nothing read |
| Symlinked `<dest>/recordings` | PASS: exit 2, nothing written through it |
| Symlinked destination device directory | PASS: FAILED, nothing written through it, file kept |
| Directory named like a file, files at depth 2 and 5, a FIFO, a character and a block device node | PASS: 6 reported, left alone, 0.2 s (no hang) |
| Names with a space, a newline, `:`, `$(touch PWNED)`, a leading `-rf` | PASS: moved under the same names, no `PWNED` anywhere |
| Two hard links in one run; a third link outside | PASS: both moved, outside link's content kept |
| mtime in 2031; 2 days old; 1970 | PASS: stayed; stayed; moved to `1969-12/` |
| Blocker 1: `.kronk-archive.tmp` (5,000 B) beside `01_b.wav` | PASS: the first kept locally and reported, `01_b.wav` moved, FAILED; second run the same |
| Blocker 2, timed as the tester ran it: `01_a.wav` (4,000 B) replaced by rename, as root, while a 1.5 GB file copies | PASS: replacement kept locally, the first content on the destination, delete refused ("size, mtime or inode differ from the copied file"), the big file moved, FAILED |
| Blocker 3: destination run directory symlinked to the source run (month 2026-10) | PASS: refused ("is a symlink or not a directory"), file kept |
| Blocker 3 variant: device directory symlinked outside | PASS: nothing written there |
| The destination file is a hard link of the source | PASS: conflict "the destination file is the source file itself", kept |
| Destination inside the recordings tree | PASS: exit 2 (here caught first by the writability probe on the root-owned tree; the overlap rule itself is unit-tested) |
| Destination full: 4 MB tmpfs in a throwaway `python:3.12-slim` (`--network none`, `KRONK_ARCHIVE_DELETE=direct`), a 30 GB sparse file first, then 3 small and one 3.5 MB | PASS: the sparse file skipped and reported, the other four moved, no temp left; second run the same |
| Delete container image absent | PASS: FAILED, files stayed; next run moved 3, wrote 0 |
| Rename race: 300 files, files replaced by rename as root at random moments during the run | PASS: 18 replaced mid-run; no original that was not replaced lost; no replacement lost; 17 local, 297 on the destination |
| Run directory swapped for a symlink (as root) between the scan and the copy | PASS: moved 2, the swapped file kept in both places, FAILED |
| The same, between verify and delete | PASS: same |
| SIGKILL at 0.15, 0.4, 0.7, 1.2, 2, 3, 4.5, 6 s over 501 files (550 MB) | PASS: no hash missing after any kill; final run 501 on the destination, 0 local, 1 leftover temp reported (not removed) |
| SIGKILL between verify and delete | PASS: 5 local and 5 verified copies after the kill; next run moved 5, wrote 0 |
| Two archives at once; archive and config backup at once | PASS: second archive and the config job declined (exit 3); 400 of 400 hashes on the destination |
| 30,000 files | PASS: moved 30,000 in 71 s, 150 delete containers |

Command-line rows (each with `--no-notify` and the stub set on the line):
`check` on the default destination waited 20 s and warned "not a mounted
filesystem ... (waited 20 s)", exit 1; `archive --dest
//mnt/nas-backup/kronk` exit 2, not mounted; `config --dest <scratch>
--require-mount` exit 2; `KRONK_BACKUP_KEEP=abc` exit 2 "bad setting".
Nothing was written to any of those destinations; `/mnt` holds only
`nas-music`.

Config backup for real into scratch: `kronk-config-2026-11-19.tar.gz`,
5,379,641 bytes, mode 0600, 474 entries, top directory 0700, nothing
unread, no unknown HA names; `RESTORE.md`, `BACKUPS.md` and
`repo-unpushed.bundle` (129,271 bytes, 9 commits; refs HEAD, `refactor`,
`sprint-M17`, `sprint-M18` requested, `sprint-M18` excluded by git as its
commit is on the remote). A scratch copy of the script with the HA
container renamed gave `kronk-config-2026-11-19.incomplete.tar.gz`, title
INCOMPLETE, exit 4, and left all 13 complete tars in place.

## Runbook steps re-run

In scratch, `$H` a 0700 stand-in home, from the real tar above. GitHub was
stood in for by a `--no-tags` bare clone of the local repo with its
branches reset to the local `origin/*` refs, cloned over `file://`, so it
held only what the remote had at the last fetch.

| Step | What was run | Result |
| --- | --- | --- |
| 1 (finding 10) | `python3-venv` added to the apt line | installed here (`3.13.7-1`); not run (no root) |
| 2 (finding 9) | the fstab line written into the runbook | present in `RESTORE.md` in the tar (checked); mount not run (no root, no NAS) |
| 3 (note 23) | `(umask 077 && tar -xzf ...)`, MANIFEST check, `sed -n '/REQUIRED items/,$p'`, `RESTORE.md` | all members match; top directory 0700, `.env` and `solar.db` 0600 |
| 4 (finding 6) | `sudo install -m 0600 -o root -g root $R/etc/kronk/* /etc/kronk/` | the glob expands as the user (`.../etc/kronk/nas-music.cred`); `install -m 0600` into a scratch directory as the user: 0600. The sudo form was not run |
| 5 (finding 9) | clone, `git bundle verify`, `git fetch $R/repo-unpushed.bundle '+refs/heads/*:refs/remotes/bundle/*' '+refs/tags/*:refs/tags/*'`, `git checkout -B refactor bundle/refactor` | "is okay"; HEAD `779fdf3...` equals `commit:` in `repo-state.txt`; `sprint-M18` absent after the clone, recreated with the `git tag` line `repo-state.txt` gives |
| 5 (finding 10) | `python3 -m venv tests/.venv` | made; `pip install` not run (a PyPI download) |
| 6 | the `cp` lines, `chmod 0600 .env .env.dev .env.telemetry ...` | `.env` byte-identical to the live one; `.env.dev`, `.env.telemetry`, `searxng/settings.yml`, the MagicMirror key, `music_taste.yaml` identical; the `sudo install -o 977` line not run |
| 7 | `cp -a`, `git fsck`, `git status`, `config_check.py` (with the live repo's venv, as pip was not run) | fsck clean, 0 changed paths, `valid` for prod and dev |
| 10 (note 23) | hot tub venv; the `ls default.target.wants timers.target.wants \| grep` start list on the restored units | venv made (pip not run); the list is the 8 enabled services (llama, Whisper, `kronk-bootnotify`, `kronk-hottub-monitor`, `kronk-memwatch`) and 7 timers; `systemctl start` not run |
| 11 (finding 8) | stand-ins in project `kronk-restore-test`: a "stack" file that creates `kronk-restore-test_kronk`, a "telemetry" file that joins it as external | old order: `network kronk-restore-test_kronk declared as external, but could not be found`; corrected order: both started on the network; both `down` (no `-v`), network gone |
| 12 | the step's `docker volume create` and `docker run ... cp -a /src/. /config/` into `kronk-restore-test_ha-config`; HA 2026.5.4, `pull_policy: never`, `network_mode: none`, no ports | API `RUNNING` with the restored token; 127 states; 4 pipelines, 26 devices, 395 entities, 21 config entries; no storage or JSON error in the log |
| One thing: keep the current state (note 23) | the corrected `umask 077 ... chown $(id -u):$(id -g)` command | `0600 drew` |
| One thing: single file (finding 7) | the old form: `cp: can't stat '/src/.': Not a directory`; the corrected stop, `-v .../core.entity_registry:/src/core.entity_registry:ro`, copy, start | registry SHA-256 equals the tar's; HA `RUNNING`, 395 entities |
| Teardown | `docker compose -p kronk-restore-test -f <scratch> down` (no `-v`), `docker volume rm kronk-restore-test_ha-config` | no container, volume or network named `restore-test` left |

Not re-run: steps needing root (GRUB, `/etc/kronk`, fstab, the searxng
chown, `sp5100-tco-load`), `pip install` (PyPI), the models, llama.cpp,
Whisper, the MA image, and starting units or the real stacks.

## Dev pair

`orchestrator/backups_api.py` changed, so the dev pair was rebuilt with
`docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev` (only `kronk-orchestrator-dev-1` was recreated;
`tool_service-dev` was already current). On loopback: `GET
/dev/api/backups` 200, `present: false`, warning "no backup job has
written a status file yet (the timers are not installed, or no job has
ever run)"; `POST` 405; `/dev/services` carries the card. For promotion
with the next tagged deploy.

## State before and after

Container ids and start times, 11:07 (before the dev rebuild and the sweep) and 11:26 (after the restore test teardown):

| Container | Before | After |
| --- | --- | --- |
| kronk-nginx-1 | `80588ee1b80c` 2026-11-18T17:51:31Z | same |
| kronk-orchestrator-1 | `6b3d7c97b2ec` 2026-11-18T17:51:25Z | same |
| kronk-tool_service-1 | `64fc7fe9c9c5` 2026-11-18T17:51:19Z | same |
| kronk-litellm-1 | `48da9ed1e321` 2026-11-18T17:51:19Z | same |
| kronk-searxng-1 | `5521e2425583` 2026-11-18T17:51:19Z | same |
| kronk-voice_tracer-1 | `68307c8ccdff` 2026-11-18T17:51:19Z | same |
| kronk-orchestrator-dev-1 | `b68b3d0fb452` 2026-11-19T15:11:29Z | `84da57f14fcb` 2026-11-19T16:07:52Z (rebuilt) |
| kronk-tool_service-dev-1 | `87696935e83a` 2026-11-18T02:49:34Z | same |
| homeassistant | `98f56e2549d0` 2026-11-18T00:14:34Z | same |
| wyoming-piper | `5b97cc70b9ce` 2026-11-16T01:05:02Z | same |
| kronk-telemetry-grafana-1, -tempo-1 | `6f78f54c5348`, `a44fa0d3f460` | same |
| music-assistant-server, bgutil-pot-provider | `cf6b980f6630`, `0e8f74842089` | same |
| ma-pr-test, open-webui | exited | same |

User units (`llama-*`, `wyoming-*`, `kronk-*`): the same 13, same states.
Timers: the same 11. `~/.config/systemd/user`: 33 entries before and after,
none of M16's. Docker volumes 22 and networks 6, before and after.
`~/kronk-voice-recordings`: 15 files before and after; read only (three
copied out through a read-only mount); nothing moved or deleted. `/mnt`:
`nas-music` only. `data/backups/` does not exist. No notice was sent: every
run had `--no-notify` or `no_notify`, and the stub's log was never created.

## Hygiene

- Scratch: `/tmp/kronk-m16-fix1` (0700). Contents and sizes before removal
  (`du`): the sweep trees 1.4 GB (largest part during the timed blocker-2
  case: a 1.5 GB file locally and its copy, removed through `alpine:3.20`
  after the case), the restore home with a clone and the unpacked tar
  169 MB, the config destinations 5.2 MB and 5.3 MB, the rest under 1 MB
  each (harness, compose stand-ins, mutant and old-code trees, the
  before/after lists). The 30 GB sparse file lived only in the throwaway
  container's tmpfs. It held copies of secrets (the tars, the unpacked
  tree, the restored `.env`).
- Removed by literal path: the root-owned parts through `alpine:3.20`
  (`rm -rf /c/sweep /c/home` with `/tmp/kronk-m16-fix1` mounted at `/c`),
  then `rm -rf /tmp/kronk-m16-fix1`.
- Containers started by me: throwaway `alpine:3.20` and `python:3.12-slim`
  (`--rm`, `--network none`); two `alpine:3.20` sleepers for the races
  (`--rm`, stopped); compose project `kronk-restore-test` (stand-ins and the
  scratch HA), removed with `down` without `-v` and `docker volume rm` by
  name.
- Reads of the real system: `docker exec homeassistant ls/tar`, `docker
  exec music-assistant-server cat`, `/etc/kronk` through the read-only
  helper, the recordings through a read-only mount; `GET /dev/api/backups`,
  `GET /dev/services`, `POST /dev/api/backups` on loopback.
- Not done: no unit installed, enabled or started; nothing written under
  `/mnt`, `/etc`, `./data`, `.env*` or the live config directories; no
  production route called; no LAN address contacted; no upgrade script
  run; no `git fetch`; nothing pushed or tagged.
