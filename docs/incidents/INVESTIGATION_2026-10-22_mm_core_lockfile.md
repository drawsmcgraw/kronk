# Investigation 2026-10-22 — mirror update refused: core package-lock.json drift

**Status:** fixed 2026-10-22, operator authorized both Pi-side steps. The
lockfile reset was applied, and the tree was verified clean before and
after. The `mm-update.sh` core install now uses `npm ci`. The misleading
spoken failure is fixed in tool_service. Pending: the next operator-requested
update proves it end to end.

## Symptom

Kitchen Voice PE, 16:27 UTC: "Update the magic mirror." Kronk acknowledged.
About 6 s later the announce said: "The magic mirror update failed at the
preflight step. I kept a backup and left it as it is — ask me to roll it
back when you want." The mirror stayed on 2.38.0.

## Evidence

- orchestrator rid `26d21556`: `route_shortcut mm_update` → home →
  `update_magicmirror` (1.57 s).
- tool_service: `KRONK-FAIL update step=preflight tracked local changes in
  /home/pi/MagicMirror — resolve by hand first (git status)`.
- Read-only via `/ops/exec`:
  - `git status --short` → ` M package-lock.json`, nothing else.
  - `git diff --stat` → `package-lock.json | 60 -----`. Every hunk removes
    a `"libc": ["glibc"|"musl"]` field from an optional platform binary.
  - `package-lock.json` mtime 2026-10-05 08:15:57 local, three minutes
    after the last update's backup `mm-backup-20261005-081240-4b4a5953`
    (the 2.37.0 → 2.38.0 update, HEAD `f138f5b6`).
  - Pi npm 10.9.9.
  - MagicMirror's `install-mm` script is `npm install --no-audit --no-fund
    --no-update-notifier --only=prod --omit=dev`.

## Root cause

The 2026-08-14 RAIN-MAP feedback loop, this time in MagicMirror core:

1. Upstream's 2.38.0 lockfile was written by a newer npm that records
   `libc` on platform-specific optional packages.
2. The 2026-10-05 update ran `node --run install-mm`, which is `npm
   install`. The Pi's npm 10.9.9 rewrote the tracked lockfile without
   those fields.
3. The next update's preflight guard ("tracked local changes — never
   clobber operator work") treats that drift as operator work and refuses.
   Nothing ever un-dirties it, so every future update fails the same way.

The August fix switched *modules* to `npm ci` (never rewrites the lockfile)
but left core on upstream's `install-mm`.

## Second bug: the announce claimed a backup that didn't exist

`_mm_update_speech` said "I kept a backup … ask me to roll it back" for
every failure. Preflight runs before the backup and changes nothing.
Fixed: a preflight failure now says "The magic mirror update didn't start:
the mirror has local file changes that need a look first. Nothing was
changed." Test: `test_mm_update_speech_preflight_failure_claims_no_backup`.

## Fix (operator authorized 2026-10-22)

1. **One-time reset on the Pi — applied:** `git checkout -- package-lock.json` in
   `/home/pi/MagicMirror`, over SSH with tool_service's key. `git status`
   before: ` M package-lock.json`; after: clean. The diff is only npm-generated `libc` removals,
   and the 10-05 backup holds the tree either way.
2. **Stop re-creating it (`magicmirror/mm-update.sh`) — applied:** core installs
   with `npm ci --omit=dev --no-audit --no-fund`, falling back to
   `install-mm` only if `npm ci` fails (lockfile out of sync with
   `package.json`), then restore the tracked lockfile. tool_service stages
   the repo copy of the script on every run, so the change reaches the Pi
   on the next update the operator asks for.

## Workaround found on the way

The `/ops/exec` classifier rejects `git -C <dir> …` because it reads `-C`'s
value as the subcommand. `git --git-dir=<dir>/.git --work-tree=<dir> status`
passes. Worth fixing in `ops.classify_readonly`.

## What would have caught it sooner

A post-update check that the core tree is clean (`git diff --quiet`),
reported in the KRONK-OK line. The 10-05 update reported success while
setting up this failure.

## Follow-up: the retry failed at the backup step (16:34 UTC)

After the lockfile reset, "update the magic mirror" failed again:
`KRONK-FAIL update step=backup tar failed (disk full?)`. The announce again
said "I kept a backup … roll it back".

- **Not disk:** the Pi had 100 GB free (`df -h`: 117G, 11% used).
- **tar's real error** (tool_service log): `tar: MagicMirror/.git/objects:
  file changed as we read it` and `tar: MagicMirror/.git: file changed as
  we read it`. GNU tar exits 1 for that. The script treated any non-zero
  exit as fatal and guessed "disk full?".
- **The writer:** `.git/index` and `.git/objects` mtimes are 12:34:07
  local, three seconds into the backup (stamp 12:34:04). A concurrent git
  process touched the repo; MagicMirror's `updatenotification` module is
  the likely one. Unconfirmed: the mirror's journal has nothing for that
  window.
- **Left behind:** a 400 MB archive, `mm-backup-20261022-123404-f138f5b6.tar.gz`.
  It may hold a half-read file. Rotation (keep 3) would count it as a good
  backup.

Fix:

- `mm-update.sh`: a tar exit 1 retries once. Exit 2 (fatal) and a second
  exit 1 fail with tar's last line ("tar exit 1: …"), not a guess. A failed
  archive is deleted. Exercised with a fake tar for sequences 1→0 (two
  calls, one archive kept), 1→1 and 2 (fail, none kept).
- tool_service: a backup-step failure is spoken like preflight, as "didn't
  start: the backup failed. Nothing was changed." Test:
  `test_mm_update_speech_backup_failure_claims_no_backup`.
