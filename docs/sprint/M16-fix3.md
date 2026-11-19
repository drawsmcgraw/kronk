# M16 fix round 3: third end-to-end findings

Branch `refactor`. Fixes the findings of `docs/sprint/M16-e2e3.md` (verdict
FAILS, one blocker) under `docs/sprint/M16-criteria.md`. Code commit
`dba5677`; this report follows it. The archive's copy, verify, place and
delete code (`copy_verified`, `_already_there`, `_copy_new`, `open_chain`,
`DELETE_SCRIPT`, `flush`) has no change in this round; the archive changes
are the two the brief names: a recording that cannot be dated is skipped
(note 6) and the delete container has a name (note 8).

Tests before: `5624 passed, 3 skipped, 53 xfailed, 6 warnings in 77.63s`.
Tests after: `5653 passed, 3 skipped, 53 xfailed, 6 warnings in 80.47s`
(`./scripts/run_tests.sh`, 2026-11-19, the code of `dba5677`).
`tests/test_m16_backups.py` went from 167 to 196 tests.

Line numbers are `scripts/backups.py` at `dba5677`.

## Findings

### Blocker

**1. An unpushed production tag left out when HEAD is pushed.**
- `unpushed_bundle` (`:871`) verifies the production tags first, then
  counts `git rev-list --count HEAD <tag refs> --not --remotes` (`:892`).
  A pushed HEAD no longer hides an unpushed tag: the bundle is made and
  holds the tag. It now also returns the count (`(bundle, note, count)`).
  "None needed" says the tags were counted.
- `_config` computes `tag_on_remote` once per tag (`:1363`), passes it to
  `repo_state`, and records every tag whose answer is "no" (`:1418`). The
  end notice carries one sentence for them (`:1316`), bundle or not:
  `Production tag sprint-T1: its commit is on no remote-tracking branch (in
  the tar's repo-unpushed.bundle).`, or `(NOT in the tar: push it).` when
  the bundle is over its cap or failed.
- `repo-state.txt` gains `on no remote-tracking branch (HEAD and the
  production tags): N commit(s)`.
- Tests: `test_fix3_finding1_head_pushed_and_the_production_tag_unpushed_real_git`
  (the tester's layout in a real temporary repository with its own user:
  c1, c2 on `refs/remotes/origin/main`, the tag on c3, HEAD on c2; the
  bundle's only head is `refs/tags/sprint-T1`), and
  `test_fix3_finding1_the_notice_names_an_unpushed_production_tag_bundle_or_not`
  (the whole job: bundle in the tar, the two sentences; over the cap, the
  tag sentence and the `OMITTED` note on one line). Both fail on the old
  code.

### Should fix

**2. The notice on a local-only branch; omitted and failed bundles.**
- The notice's count is the bundle's own `--not --remotes` count
  (`run["unpushed"]`, `:1416`), not the `origin/<branch>..HEAD` line that
  fails on a local-only branch. An `OMITTED`, `NOT WRITTEN` or unknown note
  always reaches the notice in one line (`:1314`); unknown reads `Repo:
  unpushed commits unknown ((git rev-list ... failed: <first line>)).`
- Docs: the feature doc's bundle paragraph and the runbook's step 5
  sentence now say what the notice says, on any branch.
- Note 12: the notice counts uncommitted and untracked paths, count only
  (`:1322`): `Working tree: 2 uncommitted or untracked paths, not in the tar.`
- Tests: `test_fix3_finding2_a_local_only_branch_and_every_bundle_note_reach_the_notice`
  (local-only branch with a bundle; over the cap; the count failing, no
  newline, no second line of git's error), `test_fix3_note12_the_notice_counts_uncommitted_and_untracked_paths`.
  Both fail on the old code.

**3. `status.json` read-modify-write.**
- `update_status` (`:417`): opens `status.lock` in the state directory,
  takes `flock` exclusive (non-blocking tries every 50 ms; after 60 s it
  writes anyway and says so on stderr, so a stopped process cannot wedge
  every job), reads the file, applies the change, writes it atomically,
  releases. Held for that read and write only. Used by `save_run` (`:442`;
  every job's end and every declined job), `cmd_check` (`:2078`) and
  `bad_settings` (`:2157`).
- `cmd_check` no longer keeps a copy across its wait: `stale_warnings`
  (`:2028`) reads the status file after the wait for the mount, and the
  check writes only its own entry, re-read under the lock.
- Tests: `test_fix3_finding3_a_check_waiting_for_the_mount_never_writes_back_a_stale_archive_entry`
  (the tester's interleaving: a check with `--require-mount` on a
  directory that is not a mount, an archive run's `save_run` during its
  wait; afterwards the archive entry is there and the check's warnings do
  not say "never finished a run"), and
  `test_fix3_finding3_every_status_write_is_read_modify_write_under_the_status_lock`
  (`save_run`, `check`, the bad-setting check: each blocks while the test
  holds `status.lock`, another job's entry written meanwhile survives). All
  four fail on the old code.

**4. Mutants no test noticed.** Each pinned by a test, each killed (run
below):

| Mutant (third report) | Test |
| --- | --- |
| l01 the backup lock shared | `test_fix3_finding4_the_backup_lock_is_exclusive` (`take_lock` twice) |
| k07 cleanup follows a symlink named like a bundle directory; k13 unlinks a non-file inside one | `test_fix3_finding4_the_cleanup_never_follows_a_symlink_or_unlinks_a_non_file` |
| d13, s08 a dry run and the check probe the destination | `test_fix3_finding4_and_note14b_no_probe_in_a_dry_run_or_the_check_and_a_run_removes_only_its_own` |
| c06 temporary tar 0644; c08 `config/` 0755 | `test_fix3_finding4_the_temporary_tar_is_0600_and_config_0700` (umask 022) |
| c36 a final name that appears during the run overwritten | `test_fix3_finding4_a_final_tar_name_that_appears_during_the_run_is_never_overwritten` |
| a10 "does not fit" keeps the largest | `test_fix3_finding4_does_not_fit_keeps_the_smallest_size_that_failed` |
| s07 a failed notice recorded as sent | `test_fix3_finding4_a_check_notice_that_fails_is_recorded_as_not_sent` |
| cn04 a replaced temporary file unnoticed before the read-back | `test_fix3_finding4_a_temporary_file_replaced_before_the_read_back_is_noticed` (same bytes, new inode: left alone, nothing placed) |
| a07 a file at the wrong depth taken | `test_fix3_finding4_only_a_file_at_the_fourth_level_is_a_recording` (depths 1, 2, 3 and a directory at 4) |

The existing note-7 cleanup test also passed with k07 because its symlink
target survived while its files were unlinked; the new test checks the
file. Note 14b: the probe (`:537`) is now `.kronk-write-probe.<pid>.<8
hex>`, created `O_EXCL | O_NOFOLLOW`, and unlinked only while it is the
inode it created; a file named `.kronk-write-probe` is never touched (the
old code unlinked it).

**5. A FIFO where a SQLite file is expected.**
- `read_sqlite` (`:719`) `lstat`s first: anything but a regular file is
  "not a regular file" and, for `solar.db` and `music_taste.db`, makes the
  run INCOMPLETE. The read has a deadline: `SQLITE_READ_S` (120 s, `:105`)
  checked by the backup's progress callback, which runs after each step,
  busy retries included; the busy timeout is `min(5, SQLITE_READ_S)`, so a
  database still locked is a gap after at most 125 s. Found while building
  it: with the old 30 s busy timeout and Python's backup loop, a database
  held locked is retried forever.
- Tests: `test_fix3_finding5_a_sqlite_source_that_is_not_a_regular_file_is_a_gap_not_a_hang`
  (FIFO, directory) and `test_fix3_finding5_a_locked_sqlite_source_is_a_gap_after_the_deadline`
  (`BEGIN EXCLUSIVE` held, deadline 0.5 s). On the old code: the FIFO case
  hangs (killed by `timeout 45`), the directory case fails ("unable to open
  database file", not "not a regular file"), and the old `read_sqlite` on a
  locked database was still blocked when `timeout 75` killed it.
- Residual: a file swapped for a FIFO between the `lstat` and SQLite's own
  `open` (microseconds) can still block. Opening it ourselves and handing
  SQLite `/proc/self/fd/N` would close that, but SQLite would then look for
  the WAL beside `/proc/self/fd/N` and miss uncheckpointed rows (the second
  report's WAL test). Not done.

### Notes

- **6. An undatable mtime.** `month_of` (`:1845`) returns None for a year
  before 1 or after 9999; the archive reports `<path>: its mtime cannot be
  dated (out of range); skipped, kept locally` and goes on (`:1951`); the
  run ends FAILED. `--dry-run` lists it as skipped (`:1789`). Test:
  `test_fix3_note6_an_mtime_python_cannot_date_is_reported_and_skipped`
  (`os.utime` to `-70000000000`: the other recording moves). Fails on the
  old code (`ValueError: year -249 is out of range`).
- **7. HA's `tar` exiting 1.** `collect` (`:801`) reads `/config` once more
  after 5 s (`HA_TAR_RETRY_S`); a second exit 1 keeps the INCOMPLETE
  result. Feature doc, "What is in the tar". Test:
  `test_fix3_note7_ha_tar_exit_1_is_read_once_more_after_a_few_seconds`.
- **8. The delete container dies with the job.** The form chosen: each
  delete container is named `kronk-backup-delete-<job pid>-<8 hex>`
  (`:258`) and is `Ops.current_helper` while it runs (`:230`). `main`
  installs a SIGTERM handler around a real config or archive run only
  (`:2128`, restored after): it runs `docker rm -f <that name>`, then
  restores the default action and re-raises the signal, so the job still
  dies of SIGTERM as before (`on_sigterm`, `:2089`). For a job killed
  outright, the existing leftover cleanup now also lists running
  containers with the label (`:951`) and removes, by id, only those whose
  name is exactly that form and whose pid no longer exists (`pid_alive`,
  `:922`: `ProcessLookupError` means gone; a pid owned by another user
  counts as alive). The job pid, not the backup lock, is the test: it
  holds across state directories (a by-hand run with another
  `KRONK_BACKUP_STATE` never removes a live job's container). Stopping a
  container mid-batch is safe: every `rm` it made was checked, and what it
  did not reach stays local for the next run. Residual: a recycled pid
  makes the cleanup leave an orphan alone (it finishes its batch, as
  before). Tests: `test_fix3_note8_the_delete_container_is_named_and_current_while_it_runs`,
  `..._sigterm_removes_the_running_delete_container_by_its_name`,
  `..._main_holds_the_handler_only_around_a_real_run`,
  `..._the_next_job_removes_a_running_delete_container_whose_job_is_gone`;
  the argv tests pin `--name`. Real runs in the sweep below.
- **9. The stale check.** `config_age` (`:2003`): the newest good config
  run in the status file (`last_ok_at`; `save_run` now also records
  `last_ok_path`), while that tar is a regular file in `<dest>/config`.
  When the two disagree (no good run recorded, the tar gone, or it went to
  another destination) it goes by the date in the newest tar's name
  (`newest_config`, `:1984`, by name and `.n`, never mtime) and logs that
  it did; a newer tar the status file does not know is logged and the
  status file still wins. Feature doc, "The stale check". Test:
  `test_fix3_note9_the_stale_check_goes_by_the_status_file_and_the_tar_name_not_the_mtime`
  (the tester's two cases, the tar gone, a disagreement both ways).
- **10. Retention by dates.** `retention` (`:1227`) keeps every tar whose
  date is one of the newest N dates. Test:
  `test_fix3_note10_retention_keeps_the_newest_n_dates_not_files` (`KEEP=3`,
  two runs today, a `.10` from 2025-12: only 2025-10-10 is removed).
- **14.** (a) Housekeeping says a leftover `.kronk-archive.*.tmp` is either
  a partial copy of a recording still local or a second hard link of a
  verified copy, harmless to delete either way. (b) The "never delete"
  sentence lists what the jobs do delete on the share (retention, the
  config job's own stale `.tmp`, the run's own probe). (c) Finding 2.
- **15.** (a) Runbook step 4 installs every `etc/kronk/` file except
  `nas-backup.cred` and says why, with the one-line command that takes the
  tar's copy and says it overwrites the typed one. (b) Step 8 creates
  `/opt/models`. (c) Step 10 mentions the snap `*.wants` links.
- **16.** One sentence in the feature doc's Gotchas.
- Test for 14, 15 and the finding-2 sentences:
  `test_fix3_notes14_15_and_finding2_the_doc_and_runbook_wording`.

## Against the old code and mutants

The new test file run against the old `scripts/backups.py` (an archive of
`f3ccc01`, the code and docs before this round, in scratch): of the 29
new tests, 20 fail (17 behaviour tests, the doc test, the SQLite directory
case on its text, the locked case for want of `SQLITE_READ_S`, the old read
itself shown blocked past 75 s above) and the FIFO case hangs (killed at
45 s). The 8 finding-4 pins pass on the old code by design: they pin
behaviour that was already right, and are shown by mutants instead.

31 one-line mutants of the new `scripts/backups.py`, each against the whole
M16 test file (`-x`, `timeout 240`): 31 killed (two by hanging until the
timeout: the SQLite type check and the deadline removed). 14 for finding
4 (l01, k07, k13, d13 on each dry run, s08, the fixed probe name, c06, c08,
c36, a10, s07, cn04, a07) and 17 on this round's code: tags not
counted, the tag sentence dropped, the `OMITTED`/unknown note dropped, the
dirty count dropped, the status lock not taken, the check reading the
status before its wait, both SQLite guards, the undated file not skipped,
HA's tar not retried, the container unnamed, SIGTERM removing nothing, the
cleanup ignoring the pid or matching the name loosely, the stale check by
mtime or ignoring the status path, retention by file. One survived on the
first pass (the status path ignored: my test's status and name agreed);
the test gained a case where they disagree and it is killed.

## Sweep

Driver and job runner in `/tmp/kronk-m16-fix3/harness`; each run a fresh
process (`python3 -I`) that imports the repo's `scripts/backups.py` and
calls its `main(["archive", "--dest", …, "--src", …, "--no-notify"])`,
with the real `Ops`, the real delete through `alpine:3.20`
(`KRONK_ARCHIVE_DELETE` unset), `KRONK_BACKUP_NOTIFY_CMD=/bin/false`, and
state, temporary and upgrade directories inside the case. Recordings trees
root-owned (built through `alpine:3.20`, `--network none`). Hooks fire for
`01_a.wav` after its `copy_verified` or right before the delete container;
root-side changes through `alpine:3.20`. The fallback forced by a shim in
the job's process: `os.link` raising `EPERM` (renameat2 path), plus
`RENAMEAT2 = None` (checked-rename path). Each placement confirmed by the
run's own log line, or for a killed run by counters printed before the
kill.

| Case | `link(2)` | `renameat2` | checked rename |
| --- | --- | --- | --- |
| Second report 1: `01_a.wav`'s copy rewritten in place (same inode, size) after its verify | PASS | PASS | PASS |
| Second report 1: the same, right before the container | PASS | PASS | PASS |
| First report 1: `.kronk-archive.tmp` (5,000 B) beside `01_b.wav`, two runs | PASS | PASS | PASS |
| First report 2: `01_a.wav` replaced by rename as root after its verify (same size, mtime) | PASS | PASS | PASS |
| First report 2, other direction: the destination copy removed after its verify | PASS | PASS | PASS |
| First report 3: the destination run directory a symlink to the source run | PASS | PASS | PASS |
| SIGKILL between verify and delete, then a normal run | PASS | PASS | PASS |

What PASS means, as the third tester saw it: second report 1, the local
`01_a.wav` keeps its SHA-256, the destination holds the rewritten bytes,
`01_b.wav` moved, exit 1, "the destination copy's sha256 differs from the
copied file", "Moved 1 recording(s)". First report 1: the lookalike kept
with its content and never copied, both recordings moved, exit 1 both
runs. First report 2: the replacement kept locally, the first content on
the destination, "size, mtime or inode differ from the copied file"; other
direction: kept, "the destination copy is gone". First report 3: both kept,
nothing written into the source run, "is a symlink or not a directory".
Kill: after the kill both recordings local and both copies on the
destination with their hashes; the next run moved 2, wrote 0 B, 0 local.
In every case a canary outside the recordings root and one already on the
destination were unchanged, and no labelled container was left. 21 of 21;
no recording lost.

Note 8 for real (link path; two 600 MB recordings and one of 6 KB, so the
container is mid-hash when the signal lands one second into it):

| Case | Result |
| --- | --- |
| SIGTERM to the job | job exit -15; log `stopped by signal 15: removed the delete container kronk-backup-delete-<pid>-<hex>`; 0.5 s later the container is gone and no `docker` process names it; all three recordings still local; the next run moved 3, 0 local, hashes equal |
| SIGKILL to the job | job exit -9; 0.5 s later `kronk-backup-delete-<pid>-<hex>` still running (the old behaviour); the next job logged `removed 1 delete container(s) still running after their job ended: kronk-backup-delete-<pid>-<hex>`, moved 3, 0 local, hashes equal; no labelled container left |

23 of 23.

Live repo, read only (the bundle written into scratch and removed with
its directory): 19 commits on no remote-tracking branch; refs in the
bundle `HEAD, refs/heads/refactor, refs/tags/sprint-M17`; both production
tags' commits are on `origin` branches, so no tag sentence fires and
today's tar is whole.

## State before and after

At 13:43 (before the sweep) and 13:47 (after), by `cmp` of saved listings:
container names, ids and states (16), `docker inspect` ids and start
times (16), the `llama-*`/`wyoming-*`/`kronk-*` user units (20 lines), the
11 timers, a long listing of `~/.config/systemd/user` (36 lines), the 22
volumes, the 6 networks, `/mnt` (only `nas-music`), `~/.config/kronk`
(only `upgrade.env`): identical. `~/kronk-voice-recordings`: 37 entries
(16 files) before and after; no entry missing (capture is on, so the
listing is compared for loss, not for equality; it did not change); never
read by the sweep. `data/backups/` does not exist; no `/tmp/kronk-bundle-*`;
no container labelled `kronk.backup.helper=1`. No image built or pulled;
the dev pair was not rebuilt (`orchestrator/backups_api.py` unchanged, and
the status file's new `last_ok_path` and `status.lock` need nothing from
it).

## Not fixed, and why

- Note 11 (the checked-rename window), note 13, note 17: not to be changed
  (brief). Note 16: one sentence only.
- Finding 5's lstat-to-open window and note 8's recycled pid: residuals,
  above.
- Third report note 18 (the API's non-numeric limits, a future
  `last_ok_at`): not in this round's brief; unchanged.

## Hygiene

- Scratch: `/tmp/kronk-m16-fix3` (0700, tmpfs), 2.4 GB at the end: the
  sweep's case trees 2.3 GB (synthetic recordings; the two note-8 cases
  hold 600 MB files twice each), the mutant tree 33 MB and the old-code
  tree 33 MB (tracked repo files only, no `.env`), experiments 212 KB, the
  old script 236 KB, listings 88 KB, harness 28 KB, an empty directory
  where the live-repo bundle was made and removed. No secrets were copied
  into it: no config tar was made from the real sources. Removed by
  literal path: the root-owned case trees through `alpine:3.20` (`rm -rf
  /c/sweep` with the scratch directory mounted at `/c`), then `rm -rf
  /tmp/kronk-m16-fix3`; checked gone. No other `/tmp` directory touched.
- Containers started by me: throwaway `alpine:3.20` (`--rm`,
  `--pull=never`, `--network none`) to build, change and remove root-owned
  trees and as the job's own delete. None left.
- Reads of the real system: `docker ps`/`inspect`, `systemctl --user`
  listings, local `git` in the repo for the bundle note, the listing of the
  recordings directory. Nothing else.
- Not done: no unit installed, enabled or started; nothing written under
  `/mnt`, `/etc`, `./data`, `.env*` or the live config directories; no
  production route called; no LAN address contacted; no `git fetch`; no
  upgrade script run; no notice sent (every run carried `--no-notify` on
  its command line, and the notifier was `/bin/false`); dry-run never
  turned off; nothing pushed or tagged.
