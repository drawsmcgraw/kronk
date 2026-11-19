# M16 coordinator fixes after the fourth end-to-end test

The fourth test (`docs/sprint/M16-e2e4.md`) passed with no blocker and four
should-fix findings. These were fixed by the coordinator directly, not by a
fix agent, and were not retested by an agent. Suite before: `5653 passed, 3
skipped, 53 xfailed`; after: `5657 passed, 3 skipped, 53 xfailed`.

- **Finding 1** (a production tag that is not a local tag: `OK`, notice
  silent). The notice now says "Production tag X: not a local tag, so the
  tar cannot say which commit production runs or whether a remote has it."
  `scripts/backups.py` (`tags_unknown`); test
  `test_e2e4_the_notice_names_a_production_tag_that_is_not_a_local_tag`.
- **Finding 2** (unpushed commits on another local branch). The job counts
  `git rev-list --count --branches HEAD --not --remotes`; when that is more
  than what the bundle covers, the notice says "Other local branches: N
  commits on no remote and NOT in the tar ... push them." The bundle's scope
  is unchanged (the checked-out branch and the production tags). Test
  `test_e2e4_the_notice_counts_unpushed_commits_on_other_local_branches`.
- **Finding 3** (runbook step 5 in three repo layouts): `git bundle
  list-heads` first, and the line to use for a local-only branch, a
  tags-only bundle and a detached HEAD (`docs/runbooks/restore.md`). Text
  only; the commands are the ones the tester ran.
- **Finding 4** (retention removed the tar just written when twelve newer
  dates were present): `retention(..., protect=<name>)` never removes the
  run's own file and logs why. Test
  `test_e2e4_retention_never_removes_the_tar_the_run_just_wrote`.
- **Note 5** (the delete script's last look at the local file came before
  the read of the destination copy): the four read lines are reordered so
  the destination copy is read first and the local file's hash and stat
  last. The checks and their order of evaluation are unchanged. This is the
  one change to the delete script since the third and fourth testers cleared
  it; the pinned-text test was updated and
  `test_e2e4_the_delete_script_reads_the_local_file_last` added. The
  existing tests that run the script for real (swap, rewrite in place,
  truncation, removal, symlink) pass unchanged.

Not fixed (notes 6 to 13 of the fourth report): six mutants no test notices;
a corrupt status file set aside without a log line; a declined archive logs
no reason; a recording dated in the future is never archived and never
mentioned; a hung notifier's grandchild; the working-tree count when git
cannot read the repo; scheduling notes. Note 14 (which placement path the
Synology takes; a read-back that really comes from the server) is settled
by the watched first run on the real share.
