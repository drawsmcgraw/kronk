# M16 fix round 2: second end-to-end findings

Branch `refactor`. Fixes the findings of `docs/sprint/M16-e2e2.md` (verdict
FAILS, one blocker) under `docs/sprint/M16-criteria.md`, scoped to what the
findings name. Code commit `c8f413c`; this report follows it. The archive
was not restructured.

Tests before: `5582 passed, 3 skipped, 53 xfailed, 6 warnings in 77.36s`.
Tests after: `5624 passed, 3 skipped, 53 xfailed, 6 warnings in 78.31s`
(`./scripts/run_tests.sh`, 2026-11-19, at `c8f413c`).
`tests/test_m16_backups.py` went from 125 to 167 tests.

## Findings

### Blocker

**1. A destination copy rewritten in place after its verify.**
- `DELETE_SCRIPT` (`scripts/backups.py:264`) now reads the destination
  copy's SHA-256 (`g=$(sha256sum < "$t")`, `:277`; the destination is
  mounted read-only in the container), takes its `stat` after that read,
  and refuses on a difference: "the destination copy's sha256 differs from
  the copied file" (`:282`). Checked in order: local file's size, mtime and
  inode; local hash; destination size and inode; destination hash.
- The host-side re-check (`dest_copy_problem`: size and inode only) is
  removed in favour of the container's, which checks a superset right
  before each `rm` (`flush`, `:1778`). In `KRONK_ARCHIVE_DELETE=direct`
  mode the same script runs with the host's `sh`.
- Feature doc, "The recordings archive": what is checked (both files, each
  item), and what is not: the destination copy's mtime (a copy found
  already on the share keeps its own), and anything after the check (the
  one shell line between the last read and the `rm`; a change after the
  delete). It also says the delete reads every copy back a second time.
- Tests: `test_fix2_finding1_a_destination_copy_rewritten_in_place_after_its_verify_is_kept`
  (6 cases: after `copy_verified` and right before the container's argv
  runs, each on `link(2)`, `renameat2` and the checked rename; same inode
  and size asserted at the rewrite), and the `dest-content` case of
  `test_the_delete_script_refuses_anything_but_the_verified_file`. All 7
  fail on the old code (run below). `test_finding2_a_destination_copy_that_changes_after_its_verify_stops_the_delete`
  now asserts the container's refusal text instead of the host's.

### Should fix

**2. The rename fallback, tested.**
- Tests: `test_fix2_finding2_without_hard_links_the_copy_is_still_placed`
  (`EPERM`, `EOPNOTSUPP`, `ENOSYS`, each on `renameat2` and on the checked
  rename: placed, content, mtime, 0600, no temporary file left, the
  placement log line), `..._a_final_name_that_exists_is_never_replaced`
  (both paths), `..._the_placed_file_must_be_the_one_written` (all three
  paths: the name swapped right after placement gives "the archived copy
  is not the file that was written; kept locally"), and
  `..._other_link_errors_keep_the_file` (`EACCES`, `EIO`).
- The three named mutants are killed: v14 (check before the rename
  removed) by `..._final_name_that_exists_is_never_replaced[rename]`; v15
  (`EPERM` dropped) and x11 (rename removed) by the finding-1 and
  `..._still_placed` tests.

**Note 8: `renameat2(RENAME_NOREPLACE)` first.**
- `rename_noreplace` (`:1349`) calls libc's `renameat2` through `ctypes`
  (resolved once, `RENAMEAT2`, `:1346`). `EEXIST` is a conflict, both
  kept; `EINVAL`, `ENOSYS`, `EOPNOTSUPP` (or no symbol in libc) fall back
  to the old rename after a fresh check; any other error keeps the file
  ("cannot put the copy in place"). Used in `_copy_new` at `:1600` only
  when `link(2)` is refused.
- Placement log line, once per run (`:1824`): `placement this run:
  link(2) N, renameat2(RENAME_NOREPLACE) N, rename after a check N`.
- Tests: `test_fix2_note8_renameat2_refuses_a_name_that_appears_after_every_check`
  (the name created inside the call, after every check: kept, conflict),
  `..._a_filesystem_without_renameat2_falls_back_to_the_checked_rename`
  (`EINVAL`, `ENOSYS`, `EOPNOTSUPP` through a stand-in that sets the
  ctypes errno), `test_fix2_rename_noreplace_itself`.
- Residual: on a filesystem without `RENAME_NOREPLACE` the old window
  between the check and the rename remains, as documented.

**3. `--mount type=bind` for every helper container.**
- `Ops.helper` (`:207`) takes `(source, target, read-only)` and passes
  `--mount type=bind,source=…,target=…[,readonly]`; `-v` is gone. The
  delete mounts the recordings read-write and `<dest>/recordings`
  read-only; `/etc/kronk` is read-only (`helper_tar`, `:221`). A path with
  `,`, `"` or a newline is refused before Docker sees it.
- Tests: `test_the_delete_runs_by_literal_path_in_a_container_that_sees_only_the_two_trees`
  (exact argv), `test_the_etc_kronk_helper_mount_is_read_only`,
  `test_fix2_finding3_every_helper_mount_is_a_bind_mount_that_needs_its_source`.
- Real run (sweep, below): `<dest>/recordings` renamed away before the
  container, on each placement path: `docker: Error response from daemon:
  invalid mount config for type "bind": bind source path does not exist`,
  both recordings kept, FAILED, and `<dest>/recordings` not recreated.

**4. A production tag that is not a local ref.**
- `unpushed_bundle` (`:800`) runs `git rev-parse --verify --quiet
  refs/tags/<t>` for each tag; a missing one is left out of the bundle and
  named ("left out, not a local tag: …"); present ones go in as
  `refs/tags/<t>`.
- Errors that reach a notice are one line: `one_line` (`:123`) for the
  bundle failure, the delete container's error (`:243`), the `docker exec`
  and helper failures in `collect`, and `git()`'s failure text.
- Tests: `test_fix2_finding4_a_production_tag_that_is_not_a_local_ref_is_left_out_and_named`,
  `..._a_bundle_error_in_the_notice_is_one_line` (git's usage text not in
  the body, no newline), `test_fix2_finding4_one_line`, and
  `test_fix2_finding4_and_note13_a_real_bundle_holds_the_refs_it_names`
  (real `git` in a temporary repository with its own user,
  `GIT_CONFIG_GLOBAL=/dev/null`; a tag that exists and one that does not).

**5. Defaults and three behaviours pinned.**
- `test_fix2_finding5_the_defaults` (7 days, 12 tars, 35 stale days, both
  from `Settings.load` and the dataclass),
  `..._a_symlinked_directory_in_a_tree_is_stored_not_followed`,
  `..._a_symlink_in_has_tar_stream_is_kept`,
  `..._sqlite_is_read_through_the_online_backup` (rows held in an
  un-checkpointed WAL: a raw copy of the file has no tables, the tar's
  copy has all 100 rows).

**6. Runbook.** Step 4 sets `T`, `R`, `K` again after `sudo reboot`; step
16 removes `~/restore/ha-storage-before-restore.tgz`; "Restore one thing"
now says step 16 removes it. Test: `test_fix2_finding6_the_runbook`.

### Notes

- **7. Leftovers outside the destination.** `clean_own_leftovers`
  (`:839`), called after the lock in `cmd_config` (`:1170`) and
  `cmd_archive` (`:1674`), never in a dry run, never failing the job. It
  removes only: containers from `docker ps -a --filter
  label=kronk.backup.helper=1 --filter status=created` (ids checked as hex,
  `docker rm` without `-f`); every helper `docker run` now carries `--label
  kronk.backup.helper=1`; and `kronk-bundle-<8 chars>` directories in the
  job's temporary directory (`Settings.tmp`, `:314`, also where the bundle
  is made) that are plain directories owned by the user, older than a day,
  holding only `unpushed.bundle` or its `.lock` (files unlinked, then
  `rmdir`). Tests: `test_fix2_note7_a_job_removes_its_own_leftovers_by_exact_signature_only`
  (both jobs: exact argv; a young directory, one with another file, a
  short name, upper case, a symlink and a plain file all left alone; ids
  `--force` and `abc;rm` never passed), `..._nothing_to_remove_runs_no_rm_and_a_dry_run_looks_at_nothing`,
  `..._a_failing_leftover_check_never_fails_the_job`. Not testable here: a
  directory owned by another user (no root).
- **9.** `data/solar.db` and `data/music_taste.db` are required
  (`required_sources`, `:604`): unreadable or missing makes the tar
  INCOMPLETE. Feature doc table updated. Tests: the `solar-corrupt` and
  `taste-db-missing` cases of `test_a_missing_required_item_makes_the_tar_incomplete`.
- **13.** The bundle note names the refs from `git bundle list-heads`.
  On the live repo (bundle written into scratch only): `repo-unpushed.bundle:
  14 commit(s) no remote-tracking branch has, 159640 bytes; refs in it:
  HEAD, refs/heads/refactor, refs/tags/sprint-M17; left out, not a local
  tag: sprint-NOPE` (`sprint-NOPE` added by me; `sprint-M18` is dropped by
  git, its commit being on the remote, and is no longer claimed).
- **14.** "1 commit not pushed" (`:1198`; test `test_fix2_note14_one_commit_not_pushed`).
  The feature doc's mount sentence now says read-write and read-only, and
  the finding-1 sentence says exactly what is checked.
- **10, 11, 12, 16.** Not changed (rulings). The feature doc gains
  "Housekeeping on the NAS": the `.kronk-archive.*.tmp` leftovers and the
  `.incomplete` tars, and how to remove each by hand.
- **15.** Feature doc, "First run on the real share (coordinator)": one
  watched run on the oldest recording (`KRONK_ARCHIVE_AGE_DAYS` set so only
  it is due), with the placement log line, `fchmod`/`utime` on CIFS,
  `serverino` inode stability across a remount, and `/proc/fs/cifs/Stats`
  reads before and after.

## Against the old code and mutants

The new test file run against `scripts/backups.py` at `574b082` (the code
before this round; the fixture's new `tmp` field shimmed so interface
errors do not hide behaviour): `43 failed, 124 passed`. The behaviour
failures include all six finding-1 cases and the `dest-content` delete
case, the two new required items, the tag and bundle-error cases, "1
commit", the leftover cleanup, the bind mounts, and `renameat2`. Passing
on the old code by design: the pinning tests of finding 5, the `link(2)`
case of the placed-file check, and `EACCES`/`EIO`.

33 one-line mutants of the new `scripts/backups.py`, each against the
M16 tests: 33 killed. They cover the tester's v14, v15, x11, v12, a05, r07,
n14, t14, t25, t26, t22, and my own on the new code: renameat2 never used,
`RENAME_NOREPLACE` set to 0, `EEXIST` treated as unsupported, the
destination hash compare removed or hashing the local file, `-v` instead
of `--mount`, either mount not read-only, the label dropped, tags not
verified, the bundle error not one line, the note naming the refs asked
for, "1 commits", each SQLite file made optional, and six on the leftover
cleanup (the `created` filter, `rm -f`, the age, other content, the id
filter, the call itself) plus the placement log line.

## Sweep

Driver and job runner in `/tmp/kronk-m16-fix2`; each run a fresh process
importing `scripts/backups.py` with the real `Ops`, the real delete through
`alpine:3.20` (`KRONK_ARCHIVE_DELETE` unset), `no_notify=True` and
`KRONK_BACKUP_NOTIFY_CMD=/bin/false`, `TMPDIR` inside the case. Recordings
trees root-owned, built through `alpine:3.20` (`--network none`). The
fallback forced with my own shim: `os.link` raising `EPERM` in the job's
process (renameat2 path), plus `RENAMEAT2 = None` (checked-rename path).
Each placement confirmed by the run's own log line.

| Case | `link(2)` | `renameat2` | checked rename |
| --- | --- | --- | --- |
| Finding 1: `01_a.wav` copy rewritten in place (same inode, size) after its verify | PASS | PASS | PASS |
| Finding 1: the same, right before the container starts | PASS | PASS | PASS |
| First report 1: `.kronk-archive.tmp` (5,000 B) beside `01_b.wav`, two runs | PASS | PASS | PASS |
| First report 2: `01_a.wav` replaced by rename as root after its verify (same size, mtime) | PASS | PASS | PASS |
| First report 2, other direction: destination copy removed after its verify | PASS | PASS | PASS |
| First report 3: destination run directory symlinked to the source run | PASS | PASS | PASS |
| Finding 3: `<dest>/recordings` renamed away before the container | PASS | PASS | PASS |
| SIGKILL between verify and delete, then a normal run | PASS | PASS | PASS |

What PASS means per row: finding 1, the local `01_a.wav` keeps its
original SHA-256, the destination holds the rewritten bytes, `01_b.wav`
moved, exit 1, "local delete refused (the destination copy's sha256
differs from the copied file)", "Moved 1 recording(s)". First report 1:
the lookalike kept with its content and never copied, `01_b.wav` moved,
FAILED both runs. First report 2: the replacement kept locally, the first
content on the destination, "size, mtime or inode differ from the copied
file"; other direction: kept, "the destination copy is gone". First report
3: kept, "is a symlink or not a directory". Finding 3: both recordings
kept, `<dest>/recordings` not recreated, the Docker error one line in the
notice. Kill: after the kill both recordings local and both copies on the
destination; the next run moved 2, wrote 0 B, 0 local. 24 of 24; no
recording lost.

Note 7 for real: a `created` container with the label, a `created` one
with another label, and a running one with the label; an archive run
removed only the first (`removed 1 helper container(s) a killed run left
in state created: d6ad1ff556f5`), and of two bundle directories in its
temporary directory only the one aged two days. I removed the other two
containers by id.

## State before and after

At 12:15 (before any change) and after the sweep, by `diff` of the saved listings: container names, ids
and states (16), `docker inspect` ids and start times (16), the 13
`llama-*`/`wyoming-*`/`kronk-*` user units, the 11 timers, a long listing
of `~/.config/systemd/user` (36 lines, none of M16's), the 22 volumes, the
6 networks: identical. `~/kronk-voice-recordings`: 15 files, the same
listing hash (name, size, mtime) before and after; never read by the
sweep. `/mnt` holds only `nas-music`; `data/backups/` does not exist;
`~/.config/kronk` holds only `upgrade.env`; no `/tmp/kronk-bundle-*`; no
container labelled `kronk.backup.helper=1` left. No image built or pulled;
the dev pair was not rebuilt (`orchestrator/backups_api.py` unchanged).

## Hygiene

- Scratch: `/tmp/kronk-m16-fix2` (0700), 5.4 MB at the end: the mutant
  tree 2.9 MB and the old-code tree 2.1 MB (tracked repo files only), the
  sweep's case trees 492 KB (synthetic recordings), the before/after
  listings 64 KB, harness files under 20 KB, an empty directory where the
  live-repo bundle was made and removed. No secrets were copied into it.
  Removed by literal path: the root-owned part through `alpine:3.20` (`rm
  -rf /c/sweep` with the scratch directory mounted at `/c`), then `rm -rf
  /tmp/kronk-m16-fix2`; checked gone. No other `/tmp` directory touched.
- Containers started by me: throwaway `alpine:3.20` (`--rm`,
  `--pull=never`, `--network none`) to build and remove root-owned trees
  and as the job's own delete; for note 7, `docker create` twice and one
  `docker run -d --rm … sleep 120`, all removed or stopped by id.
- Reads of the real system: `docker ps`/`inspect` (production tags), local
  `git` in the repo for the bundle note, the listing of the recordings
  directory. Nothing else.
- Not done: no unit installed, enabled or started; nothing written under
  `/mnt`, `/etc`, `./data`, `.env*` or the live config directories; no
  production route called; no LAN address contacted; no `git fetch`; no
  upgrade script run; no notice sent (every run had `--no-notify` or
  `no_notify`); dry-run never turned off; nothing pushed or tagged.
