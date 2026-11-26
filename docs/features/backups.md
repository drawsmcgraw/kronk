# Backups: monthly config tar, weekly recordings archive

Sprint M16 (built 2026-11-19; end-to-end fixes the same day,
`docs/sprint/M16-fix1.md`, `docs/sprint/M16-fix2.md` and `docs/sprint/M16-fix3.md`). Plan: "M16. Backups: small, monthly, config
only" in `Kronk evaluation findings and implementation plan.md`. Criteria:
`docs/sprint/M16-criteria.md`. Build report: `docs/sprint/M16-build.md`.
Restore: `docs/runbooks/restore.md`.

## What it does

Three timers, one destination (`/mnt/nas-backup`, the Synology share
`//atlas.local/kronk-backup` mounted by `/etc/fstab`):

| Job | When | What |
| --- | --- | --- |
| Config backup, `scripts/backup.sh` | the 10th of each month, 01:30 | one tar, `config/kronk-config-<YYYY-MM-DD>.tar.gz`, about 5 MB; the newest 12 dates kept |
| Recordings archive, `scripts/archive_recordings.sh` | Mondays, 01:00 | voice recordings older than 7 days moved from `~/kronk-voice-recordings` to `recordings/<device>/<pipeline>/<YYYY-MM>/<run>/` |
| Stale check, `scripts/backup_check.sh` | daily, 09:30 | warns when the last good config run (status file, its tar still on the share) is over 35 days old, the archive has not finished a run in 9 days, the share is not mounted, or a job's last run failed |

All three are `scripts/backups.py` (standard library only). Each job sends a
start and an end notice through `scripts/lib/notify.sh`; the end notice says
OK or FAILED and why, the size written, and the total on the share
(config, archive, together). The last run of each job is in
`data/backups/status.json`, shown on `/services` ("Backups" card, from
`GET /api/backups`).

### What is in the tar

What is needed to rebuild Kronk, no binaries, no models:

- secrets and local config: `.env`, `.env.dev`, `.env.telemetry`,
  `searxng/settings.yml`, `secrets/`, `ops/hosts.json` (absent today),
  `/etc/kronk/` (read through a throwaway `alpine:3.20` container; root-only),
  Music Assistant's `settings.json` (provider logins) and `auth.db` (MA's
  users, every login token including `MA_TOKEN`, and the key that signs
  them: anyone with this file can sign in to MA; since M16b);
- household data: `data/solar.db`, `data/music_taste.yaml`,
  `data/music_taste.db`, `data/kronk_state.md`, `data/mm_known_hosts`,
  `data/yt-music-cookie`, `data/upgrades/status.json`, the labelled voice
  clips `~/kronk-voice-labels/` (absent until the first sheet), and the
  voice-run record `~/.local/state/kronk-voice/voice_runs.db` as
  `voice-state/voice_runs.db` (M30 step B: every household voice run with
  what Whisper heard and what answered; `docs/features/voice-dataset.md`). SQLite files
  are copied with the online backup API: a consistent copy of a live DB.
  Only a regular file is opened (a FIFO, directory or device is "not a
  regular file"), and a database still locked after 120 s is a gap, not a
  hang;
- Home Assistant: `.storage/`, the YAML files, `blueprints/`, `.HA_VERSION`
  (read live with `docker exec homeassistant tar`; no recorder DB, TTS cache
  or logs). HA's busybox `tar` exits 1 when a file changed as it was read
  (HA saves `.storage` by temporary file and rename); the job reads
  `/config` once more after 5 s, and a second exit 1 makes the run
  INCOMPLETE (loud; the next month's run is whole);
- Music Assistant: `settings.json`, `playlists/` (the playlists MA made
  itself, every file by name; `docker exec music-assistant-server tar`,
  which stores a symlink as a symlink, so nothing is followed out of the
  directory; absent, empty or unreadable is reported, not a failure) and
  `auth.db` (since M16b; `library.db` is 44 MB, over the plan's 10 MB
  rule). `auth.db` cannot be read like the other databases: MA opens
  every database in WAL mode with `locking_mode=exclusive`, so no other
  connection can open it, its WAL index lives in MA's memory (there is no
  `auth.db-shm`), and a reader that tried would create `auth.db-shm` in
  MA's `/data`. And a plain copy of the main file is not the database: on
  2026-11-20 the WAL was 3.1 MB beside a 92 KB file and held one of the
  four token rows. So the job reads the pair the way crash recovery finds
  it, by `docker exec cat` only: the main file, the WAL, the main file
  again, the WAL again. It takes the pair when both main-file reads are
  identical (no checkpoint wrote to it in between) and both WAL reads are
  identical and end on a whole frame (the WAL did not restart, and neither
  read came back short: the only other word on that is `cat`'s exit status,
  and a WAL cut short over a main file checkpointed past the cut replays
  into a database that never existed; M16b's end-to-end test built one
  with a fake reader). A write between the reads, a few a day, is read
  again. Then SQLite itself opens the pair in a private 0700
  `kronk-sqlite-<8>` directory in the temporary directory (tmpfs here),
  replays the committed frames and drops a frame MA was still appending;
  the result goes through the backup API and `PRAGMA integrity_check`, and
  the directory is removed. Nothing is written in MA's `/data`, nothing
  is checkpointed, MA is not stopped. A job stopped by SIGTERM removes
  its private copy; one killed outright leaves it for the next job, a day
  later at the earliest. A playlist that is a hard link or a special file
  is left out and listed as not read. A pair that changed is read again
  after 5 s, three reads in all; then it is a gap (INCOMPLETE). The
  MANIFEST says how each was read;
- the live config directories `../kronk-config/prod` and `dev`, whole, with
  `.git`;
- `~/.config/systemd/user/` (units, drop-ins, the `.wants` symlinks as
  symlinks) and `~/.config/kronk/`;
- `/etc/default/grub`, `/etc/docker/daemon.json`;
- the way back: `RESTORE.md` (`docs/runbooks/restore.md`) and `BACKUPS.md`
  (this file), readable from the tar before anything is cloned;
- generated: `repo-state.txt` (commit, branch, commits not pushed,
  production tag per container and what local refs say about it on the
  remote), `repo-unpushed.bundle` (a `git bundle` of HEAD, the branch and the
  production tag less everything a remote-tracking branch has; made when
  HEAD or a production tag has commits no remote-tracking branch has, so a
  pushed HEAD does not hide an unpushed tag; left out over 20 MB,
  `KRONK_BACKUP_BUNDLE_MAX_MB`; a production tag that is not a local ref is
  left out of the bundle and named; `repo-state.txt` lists the refs the
  bundle really holds, from `git bundle list-heads`, since git drops a ref
  whose commit a remote already has). The end notice says, on any branch, a
  local-only one included: how many commits no remote-tracking branch has
  (`git rev-list --count HEAD <tags> --not --remotes`, the bundle's own
  count) and whether the bundle holds them; an `OMITTED`, `NOT WRITTEN` or
  unknown bundle note, in one line; each production tag whose commit is on
  no remote-tracking branch, bundle or not; and how many uncommitted or
  untracked paths the working tree has (they are not in the tar),
  `images.txt` (compose pins and what runs),
  `models.tsv` (every GGUF under `/opt/models` and `~/model-staging` and
  every GGUF a unit names: path, size, SHA-256), `host-facts.txt` (kernel,
  `/proc/cmdline`, the NAS lines of `/etc/fstab`), and `MANIFEST` (each
  member's SHA-256, size, how it was read and source; what could not be
  read; what was absent).

Not in it: models, llama.cpp and Whisper binaries, Tempo traces, HA's
recorder DB, `sessions.db`, metrics, MA's library and history (and its
other databases), the repo itself (it is on GitHub; only the unpushed commits are in
the bundle). The job reads remote state from local remote-tracking refs
only: it never runs `git fetch`, so "pushed" means "as of the last fetch".
Git keeps no remote-tracking refs for tags, so whether the production tag
itself is on GitHub is "unknown" unless its commit is on no remote branch.

### Required and optional

A tar is a backup only when it holds the **required** items:

| Required | Why |
| --- | --- |
| Home Assistant's `/config` (`.storage`, YAML, blueprints) | the house: devices, entities, pipelines, auth |
| Music Assistant's `settings.json` | the players and providers |
| Music Assistant's `auth.db` | MA's users and tokens: without it `MA_TOKEN` and every MA login die at a restore (M16b) |
| `.env` | every token the stack runs on |
| the live config directories `kronk-config/prod` and `dev` | prompts and tuning, with their history |
| `~/.config/systemd/user/` | the units that start everything |
| `data/solar.db`, `data/music_taste.db` | the only copies of household data in the tar (coordinator ruling, 2026-11-19) |
| the voice-run record `~/.local/state/kronk-voice/voice_runs.db` | every household voice run kept for good; its only other copy is the weekly one beside the recordings (M30, required from the first export, 2026-11-26) |

A run that cannot read any of them (a container down, a permission, a
file missing) still writes what it read, as
`config/kronk-config-<date>.incomplete.tar.gz`, and ends **INCOMPLETE**:
its own notice title (`Kronk backup: config backup INCOMPLETE`), result
`incomplete` in the status file, exit 4, a `/services` warning. An
incomplete tar never counts toward the 12 that retention keeps, retention
does not run after it (so it never causes a complete tar to be removed),
and the stale check does not count it as a backup. Everything else
(`.env.dev`, `.env.telemetry`, `secrets/`, `/etc/kronk`, the runbook
copies, ...) is **optional**: a gap is listed in the MANIFEST
and the notice ("NOT READ"), and the run is still OK. Anything in HA's
`/config` this backup neither copies nor knowingly leaves out (a new
`zigbee.db`, say) is listed as `UNKNOWN` in the MANIFEST and the notice;
`custom_components/`, `www/`, `themes/` and `packages/` are copied when
they exist.

**The tar holds every secret in the clear** (the list above, `auth.db`
included), **and, since M30, household speech**: the voice-run record holds
the words of every household voice run, kept for good. The share already
held the speech itself (the recordings) and, since M30, holds the record
weekly too (`voice-runs/`). `auth.db` appears in no log line, notice, status file or API
answer beyond its name and size, and a failed read names the reason only.
It is mode 0600 and the share
is mounted 0600/0700 for the operator, but anyone with the NAS account or
the NAS's admin can read it. Not encrypted: Drew decided on 2026-11-21
that encryption is not needed.

## How it works

### The config backup

1. Takes `data/backups/lock` (shared by both jobs). Waits up to 30 minutes
   while the other job runs or while the monthly upgrade run holds
   `data/upgrades/lock` (tested, never taken, never created), then declines
   with a notice.
2. Start notice.
3. The destination check: `/mnt/nas-backup` (the default, or any path with
   `KRONK_BACKUP_REQUIRE_MOUNT=1`) must be a mount point and writable (a
   probe file of the run's own, `.kronk-write-probe.<pid>.<8 hex>`, created
   `O_EXCL` and removed while it is still that file; a dry run and the daily
   check make none). Otherwise the run fails, writes
   nothing there, and says so. A tar written into an unmounted
   `/mnt/nas-backup` would sit on the local disk and look like a backup.
4. Removes its own stale temporary file
   (`.kronk-config-<date>.tar.gz.tmp`, exact pattern, regular files, in
   `config/` only).
5. Reads everything and builds the tar in memory (the members already
   are; about 5 MB compressed), then checks the size ceiling (50 MB,
   `KRONK_BACKUP_MAX_MB`, decimal megabytes): over it is a failure naming the
   five largest members, and nothing is written to the destination. Then
   writes `.kronk-config-<date>.tar.gz.tmp` (0600), fsyncs, drops the file's
   pages from the page cache, reads it back (SHA-256 equal to the one
   computed while building; `tarfile` reads it to the end with the expected
   member count), then renames it. A second run the same day writes
   `kronk-config-<date>.2.tar.gz`, which unpacks into
   `kronk-config-<date>.2/`; nothing is overwritten.
6. Retention: only after a complete, good run, keeps the files named
   `kronk-config-<date>[.<n>].tar.gz` in `config/` whose date is one of the
   newest 12 dates (`KRONK_BACKUP_KEEP`; `0` keeps all). Same-day reruns
   count as one date, so runs by hand cannot push older months out. Nothing
   else is touched, `.incomplete` tars included.
7. End notice, status file.

What it could not read is listed in the MANIFEST and the notice, and
raises the `/services` warning; a required item makes the run INCOMPLETE
(above).

The model list hashes about 230 GB the first time (3 minutes 15 seconds
on 2026-11-19, 113 MB of memory, reads with `POSIX_FADV_DONTNEED` so the
page cache is not flushed), then reuses `data/backups/model_sums.json`
while a file's size and mtime are unchanged: a later run takes 2 seconds.

### The recordings archive

The recordings directory must be a real directory (a symlinked root is
refused), `<dest>/recordings` too, and neither may lie inside the other.
Every directory on the way, in both trees, is opened one level at a time
with `O_NOFOLLOW`, so no symlinked component is ever followed, even one
swapped in mid-run. For each `<device>/<pipeline>/<run>/<file>` whose
mtime is older than 7 days (`KRONK_ARCHIVE_AGE_DAYS`; the M12 labelling
script needs recent clips locally):

- not on the share: copied to a temporary file of this run's own,
  `.kronk-archive.<pid>.<16 hex>.tmp` (created `O_EXCL`; a name already
  taken is skipped, never removed), mtime set, fsynced, page cache dropped,
  read back (size and SHA-256), then linked into place with `link(2)`,
  which never replaces an existing name. A share without hard links
  (`link` fails with `EPERM`, `EOPNOTSUPP`, `ENOSYS`) gets
  `renameat2(RENAME_NOREPLACE)`, which never replaces one either; only where
  libc, the kernel or the filesystem lacks that (`EINVAL`, `ENOSYS`,
  `EOPNOTSUPP`) a plain rename after a fresh check that the name is free
  (residual window: between the check and the rename). Each run logs which
  path it used: `placement this run: link(2) N, renameat2(RENAME_NOREPLACE)
  N, rename after a check N`;
- on the share with the same SHA-256 (a run killed between copy and
  delete): not copied again;
- on the share with a different SHA-256, or something that is not a
  file, or the very same file (same device and inode: the trees are
  linked): never overwritten, never "already archived"; both kept; the run
  fails so a person looks;
- changed while it was read (size, mtime or inode moved): the temporary
  copy is removed and the file is left for the next run;
- does not fit (disk full): the temporary copy is removed, the file is
  reported and skipped, and smaller files after it still go;
- a source file whose name starts with `.kronk-archive` is never copied:
  reported, so a person renames it;
- an mtime Python cannot date (a year before 1 or after 9999; `touch -d
  @-70000000000`): reported and skipped, kept locally; the run goes on and
  ends FAILED, and `--dry-run` lists it as skipped.

The job removes only the temporary file it made in this run, checked by
device and inode just before the unlink. A temporary file left by a killed
run is never removed by a later run: the end notice counts them, and they
are deleted by hand (`find /mnt/nas-backup/recordings -name
'.kronk-archive.*.tmp'`).

Verified files are deleted locally in batches of 200 by a throwaway
`alpine:3.20` container (`--network none`, label `kronk.backup.helper=1`,
named `kronk-backup-delete-<job pid>-<8 hex>`;
two bind mounts and nothing else: the recordings directory read-write and
`<dest>/recordings` read-only, both as `--mount type=bind`, which fails when
the source is missing instead of creating it). The container's script
(`DELETE_SCRIPT` in `scripts/backups.py`, its text pinned by a test) checks
each file right before its `rm`:

- every directory on both paths is a plain directory, not a symlink;
- the local file is a regular file with the SHA-256, size, mtime (seconds)
  and device:inode of the file that was copied;
- the destination copy is a regular file with the copied size, the
  device:inode recorded at its verify, and the copied SHA-256: it is read
  again from the share here, and its device:inode is taken after that read,
  so a copy rewritten in place, truncated, swapped or removed is caught.

Only then `rm --` (no `-r`, no `-f`), then `rmdir --` of the run directory,
which only removes it when empty. Anything that fails a check is kept
locally and reported, and the run is not OK. Not checked: the destination
copy's mtime (a copy found already on the share keeps its own, so it is not
evidence), and anything after the check: the script reads the destination
copy first and the local file last (its hash, then its size, mtime and
inode), so the one shell line between that last read and the `rm` is the
residual window, and a change to the copy
after the delete is beyond what any archive job can see. The delete reads
every copy back from the share a second time. Paths are arguments, never
part of the script.

The delete container dies with its job. A SIGTERM to the job (`systemctl
--user stop`, the unit's timeout) removes the running container by its name
(`docker rm -f`), then the job dies of the signal. A job killed outright
(SIGKILL, a crash) leaves its container running to the end of its batch;
the next job, after it takes the lock, removes a running container with the
label and that exact name whose job pid no longer exists. Stopping one
mid-batch is safe: every delete it made was checked first, and what it did
not reach stays local for the next run.

The run directory is part of the archive path because HA names every
recording `01_stt-<engine>.wav`; the directory (HA's monotonic clock) is
what tells them apart.

What the read-back proves on the SMB share: after `fsync`,
`posix_fadvise(POSIX_FADV_DONTNEED)` drops the file's clean pages from this
machine's page cache, so the read-back hash comes from bytes the NAS sent
back over the wire, not from local memory. It does not prove the bytes are
on the NAS's disks: the Synology can answer from its own cache, and its RAID
and checksumming are its business. Confirm on the first real mount that
`cat /proc/fs/cifs/Stats` shows reads during an archive run. Deletes compare
inode numbers across the share, which needs the default `serverino` mount;
if the server does not provide inode numbers, the kernel falls back to
`noserverino` (a line in `dmesg`), inode numbers can change, and deletes
are refused (files stay local, reported) rather than done blind.

### The weekly copy of the voice-run record (M30 step B)

The monthly tar alone would lose up to a month of rows with the disk, while
the recordings older than a week are already on the NAS. So the recordings
archive, in the same run, under the same lock, writes
`<dest>/voice-runs/voice_runs-<YYYY-MM-DD>.db` after it moves the
recordings: the record read by the online backup API, `integrity_check`
and the row counts, written to `.voice_runs-<date>.db.tmp` (`O_EXCL`),
fsynced, read back (SHA-256, then `integrity_check` and the counts again),
renamed to a free name (a same-day run by hand is `.2`; an existing copy is
never overwritten), mode 0600 in a 0700 directory. A run removed from the
record later (`voice_runs_export.py --remove`) stays in the copies made
before it, up to eight weeks, and in the monthly tars, up to a year
(`voice-dataset.md`, "Removing a run"). The copies of the
newest 8 dates are kept (`KRONK_VOICE_COPY_KEEP`) and older ones removed,
by exact name, in `voice-runs/` only: the one place the archive deletes on
the destination. The notice says `Voice-run record: voice_runs-<date>.db
(N runs, N labels, size) written and read back`. It is made when only the
recordings directory is unusable (the move fails, exit 2, the record still
leaves the box) and not when the destination is. No record yet (before
the exporter's first run) is said in the notice and is not a failure; a
record that will not read fails the run. Restore: `restore.md` step 14.

### The stale check

The config backup's age is the newest good config run in the status file
(`last_ok_at`), while the tar it wrote (`last_ok_path`) is still in
`<dest>/config`; never a tar's mtime. When the two disagree (no good run
recorded, that tar gone, or it went to another destination), the check goes
by the date in the newest `kronk-config-<date>[.<n>].tar.gz` name and logs
that it did; a newer tar the status file does not know is logged too, and
the status file still wins. The archive's age is its last good run from the
status file. The status file is read after the wait for the mount, so a run
that finishes during the wait is seen. One notice when the set of
warnings changes, repeated weekly while it holds (day counts do not count as
a change). `/services` computes the same ages from the status file, so a
check that never runs still shows a stale job; a job that has never
finished a good run, and no status file at all, are warnings too.

Since M30 it also warns when the voice-run export's last good read
(`export_state.last_ok_at` in the record) is older than 7 days
(`KRONK_VOICE_STALE_DAYS`), or when the record is gone while
`data/voice_runs.json` exists. Since M30 fix round 1 the exporter does not
make a lost record again (only `--init` makes one), so that warning holds
until the record is restored. An exporter that died quietly costs nothing
for 90 days and then costs rows; nothing else would say so. No record and
no status file (not installed yet) is not a warning.

### Settings, mounts, exit codes

- Any destination at or under `/mnt/nas-backup` (lexically, with `.`,
  `..` and doubled slashes, or through a symlink) must be on the mounted
  share; `KRONK_BACKUP_REQUIRE_MOUNT=1` or `--require-mount` adds the check
  to any other path. A job waits up to 180 s (`KRONK_BACKUP_MOUNT_WAIT_S`)
  for the share before failing, so a timer's `Persistent=true` catch-up at
  boot does not lose the month to a slow `nofail` mount.
- A number setting that is not a whole number or is out of range
  (`KRONK_BACKUP_KEEP=abc`, `KRONK_ARCHIVE_AGE_DAYS=0`) fails the job before
  it does anything, with a notice and a status line, exit 2.
- Exit codes: 0 ok; 1 failed; 2 nothing done (destination, recordings
  directory or a setting); 3 declined (lock); 4 config tar INCOMPLETE. The
  check exits 1 when it warns.

## Install (Drew)

Nothing is installed by the build (installed on kronk 2026-11-20; result
below). Run from the repo.

**1. The share.** The credentials file, root-only, with the account's
values from `.env` (`KRONK_BACKUP_NAME`, `KRONK_BACKUP_PASSWORD`) typed in:

```
sudo install -d -m 0700 /etc/kronk
sudo sh -c 'umask 077; cat > /etc/kronk/nas-backup.cred' <<'EOF'
username=<KRONK_BACKUP_NAME from .env>
password=<KRONK_BACKUP_PASSWORD from .env>
EOF
sudo ls -l /etc/kronk/nas-backup.cred          # -rw------- root root
```

The fstab line (next to the music share's; `ip=` for the same reason, no
mDNS at boot; read-write, owned by the operator, private modes):

```
# Synology backup share for scripts/backup.sh and scripts/archive_recordings.sh (sprint M16).
# nofail: a NAS outage never blocks boot; the jobs check the mount point and fail loudly.
//atlas.local/kronk-backup  /mnt/nas-backup  cifs  rw,ip=192.168.1.14,credentials=/etc/kronk/nas-backup.cred,iocharset=utf8,uid=1000,gid=1000,file_mode=0600,dir_mode=0700,_netdev,nofail,x-systemd.mount-timeout=30  0  0
```

```
sudo cp /etc/fstab /etc/fstab.bak-$(date +%F)
sudoedit /etc/fstab                            # add the two lines above
sudo mkdir -p /mnt/nas-backup
sudo systemctl daemon-reload && sudo mount /mnt/nas-backup
findmnt /mnt/nas-backup                        # cifs, //atlas.local/kronk-backup
```

The share name is "nearly certain" (Drew, 2026-11-19); a failing mount
(`No such file or directory`) means the name differs: fix it in the line.

**2. First runs by hand.**

```
scripts/backup.sh --dry-run                    # what goes in; writes nothing
scripts/backup.sh                              # the first tar; two notices on the phone (the real run: no --no-notify)
scripts/archive_recordings.sh --dry-run        # what would move
scripts/archive_recordings.sh                  # moves recordings older than 7 days
scripts/backup_check.sh                        # "ok: ..." 
ls -l /mnt/nas-backup/config/
```

**3. The timers.**

```
cp systemd/kronk-{backup,recordings-archive,backup-check}.{service,timer} ~/.config/systemd/user/
systemd-analyze --user verify ~/.config/systemd/user/kronk-{backup,recordings-archive,backup-check}.{service,timer}
systemctl --user daemon-reload
systemctl --user enable --now kronk-backup.timer kronk-recordings-archive.timer kronk-backup-check.timer
systemctl --user list-timers 'kronk-backup*' 'kronk-recordings-*'
```

Optional overrides (`KRONK_BACKUP_KEEP=24`, `KRONK_ARCHIVE_AGE_DAYS=14`,
...) go in `~/.config/kronk/backup.env`, one `NAME=value` a line, read by
all three units.

**4. `/services`.** The Backups card needs the orchestrator change
(`orchestrator/backups_api.py`) in production: in production since `sprint-M16`
(2026-11-19).

**Remove:**

```
systemctl --user disable --now kronk-backup.timer kronk-recordings-archive.timer kronk-backup-check.timer
rm ~/.config/systemd/user/kronk-backup.service ~/.config/systemd/user/kronk-backup.timer \
   ~/.config/systemd/user/kronk-recordings-archive.service ~/.config/systemd/user/kronk-recordings-archive.timer \
   ~/.config/systemd/user/kronk-backup-check.service ~/.config/systemd/user/kronk-backup-check.timer
systemctl --user daemon-reload
```

`data/backups/` (status, lock, model hash cache) and the share's contents
stay; the fstab line and `/etc/kronk/nas-backup.cred` are removed by hand.

## By hand

- Before a risky change (an HA UI change, an upgrade window):
  `scripts/backup.sh`. It does not wait for the 10th.
- Another destination (a USB disk): `scripts/backup.sh --dest /media/usb`
  (no mount check unless `--require-mount`).
- A test run (scratch destination, a change to the script): always
  `--no-notify`, on all three commands, so nothing reaches the phone
  whatever the shell's environment holds:
  `scripts/backup.sh --dest /tmp/x --no-notify`.
- The last runs: `python3 scripts/backups.py status`.
- Logs: `journalctl --user -u kronk-backup.service -n 100` (and
  `kronk-recordings-archive`, `kronk-backup-check`).

## Gotchas

- **HA is copied live.** It is not stopped. Each `.storage` file is written
  whole by HA (temporary file, then rename), so none is half-written; two
  files can be from moments apart (a device in one registry a second before
  its entities in another). The restore test started HA cleanly from such a
  copy. The runbook says how to take one file from an older tar.
- **`Persistent=true` catch-up at boot** is not ordered after Docker or
  HA: a job that starts before them ends INCOMPLETE (config) or with failed
  deletes (archive), loudly, and is not retried before its next date.
- **`status.json`** is read, changed and written back under
  `data/backups/status.lock`, held for that read and write only, by every
  command (a declined job and the check included), so one job never writes
  another's entry back stale.
- **The share must be mounted** or the jobs fail. With `nofail`, a NAS that
  was down at boot leaves `/mnt/nas-backup` an empty local directory; the
  jobs catch that (mount point check), the stale check reports it daily.
- **The upgrade run does not wait for a backup.** A backup waits for an
  upgrade run (its lock); the reverse is not built. The timers do not
  overlap (backup the 10th at 01:30, upgrades days 1 to 7 from 02:00), so
  only a run by hand can collide; a backup mid-upgrade copies config that
  is mid-change.
- **The recordings are root-owned** (HA writes them as root). Reading works
  as the operator; deleting goes through the container. If `alpine:3.20` is
  removed from the host (`docker image prune -a`), deletes fail (reported;
  files stay local) until it is back: `KRONK_BACKUP_HELPER_IMAGE` names
  another image with `sh`, `rm`, `rmdir` and `tar`. It is never pulled.
- **A stray file in the recordings directory** (not at
  `<device>/<pipeline>/<run>/<file>`, or named `.kronk-archive*`) fails each
  archive run until it is removed or renamed. Deliberate: nothing writes
  there but HA.
- **Leftover `.kronk-archive.*.tmp` files on the share** come only from a
  killed run, and stay until deleted by hand (the end notice counts them).
  Removing them automatically would mean deleting a destination file the
  job cannot prove it made. See "Housekeeping on the NAS".
- **Leftovers on this machine** are removed by the next job (config or
  archive, after it takes the lock), by exact signature only: a helper
  container in state `created` carrying the label `kronk.backup.helper=1`
  (a run killed while `docker run` was creating it; never started, so
  `--rm` never fired), removed with `docker rm` (no `-f`); and a
  `kronk-bundle-<8 characters>` directory in the temporary directory, owned
  by the operator, older than a day, holding only `unpushed.bundle` or its
  `.lock` as regular files (a symlink, named like one or inside one, is never
  followed); a `kronk-sqlite-<8 characters>` directory of the same kind
  holding only `copy.db` and its `-wal`, `-shm` or `-journal` (a run killed
  while it replayed `auth.db`; it holds a copy of a secret, in tmpfs); and
  a running delete container whose job is gone (above).
  Anything else of a similar name is left alone and logged.
- **`MA_TOKEN` survives a restore** since M16b, if MA's `auth.db` from the
  tar goes back in place of the old one with no stale `auth.db-wal` or
  `auth.db-shm` beside it (SQLite would replay an old WAL onto the restored
  file). Shown on the 2026-11-20 tar without starting MA: the restored copy
  passes `integrity_check`, holds MA_TOKEN's row (by its JWT id), its user
  and the signing key, and MA_TOKEN's signature verifies with that key
  (`docs/sprint/M16b-build.md`). Not shown against a running MA. Before
  M16b the tar had no `auth.db` and the token died.
- **MA's databases cannot be opened while MA runs** (exclusive locking
  mode). Any check of `auth.db` goes through a copy, never the live file.
- **The plan's build recipes** (llama.cpp for gfx1151, the Whisper wheel)
  are docs, not scripts: `docs/CHANGE_2026-06-09_gemma_vulkan_switch.md` and
  `docs/VOICE_SETUP.md`. The runbook points at them.

## Housekeeping on the NAS

On the share the jobs delete only: retention's old config tars, the config
job's own stale `.kronk-config-<date>[.n].tar.gz.tmp` in `config/`, and the
probe file each run creates and removes in the destination root (only the
file it created). Two things accumulate there and are removed by hand:

- `recordings/**/.kronk-archive.<pid>.<hex>.tmp`: one per archive run
  killed at the wrong moment. Counted in the archive's end notice. Each is
  either a partial copy of a recording that is still local (killed before
  the copy was verified, so never deleted), or, killed between the link and
  the unlink, a second hard link of a finished, verified copy whose
  recording may already have left this machine. Deleting it is harmless
  either way. List them, check no archive run is in progress, then delete:

  ```
  find /mnt/nas-backup/recordings -name '.kronk-archive.*.tmp' -type f -ls
  systemctl --user is-active kronk-recordings-archive.service   # inactive
  find /mnt/nas-backup/recordings -name '.kronk-archive.*.tmp' -type f -delete
  ```

- `config/kronk-config-<date>[.n].incomplete.tar.gz`: one per INCOMPLETE
  config run. Retention never removes them. Once a later complete tar
  exists, delete them by name:
  `ls -l /mnt/nas-backup/config/*.incomplete.tar.gz`, then `rm` each one.

## First run on the real share (coordinator)

Four things the scratch tests cannot settle. Settle them with one watched
archive run, on one old recording, after the share is mounted and before
the timers are installed:

1. Make only the oldest recording due: find its age
   (`find ~/kronk-voice-recordings -type f -printf '%TY-%Tm-%Td %p\n' | sort | head -3`),
   pick `A` = its age in whole days, then
   `KRONK_ARCHIVE_AGE_DAYS=$A scripts/archive_recordings.sh --dry-run`
   lists only the oldest (or the few from its day; that is fine).
2. Before: `cat /proc/fs/cifs/Stats > /tmp/cifs-before`.
3. Run: `KRONK_ARCHIVE_AGE_DAYS=$A scripts/archive_recordings.sh` (the real
   run sends its notices).
4. Which placement path ran: the log line `placement this run: link(2) N,
   renameat2(RENAME_NOREPLACE) N, rename after a check N`
   (`journalctl --user -u kronk-recordings-archive.service` for a timer
   run, the terminal for a run by hand).
5. `fchmod` and `utime` on CIFS: the copies are `-rw-------` and carry the
   recordings' mtimes (`ls -l --time-style=full-iso` on both sides). A
   failure there fails every copy loudly ("copy failed: ...").
6. Inode stability (`serverino`): `stat -c %i` of an archived file, twice,
   a minute apart, and again after `sudo umount /mnt/nas-backup && sudo
   mount /mnt/nas-backup`; and `dmesg | grep -i serverino` is empty. A
   refused delete ("the destination copy is not the verified one") on
   every file means inode numbers are not stable: deletes are refused,
   not blind, and the files stay local.
7. The read-back went over the wire: `cat /proc/fs/cifs/Stats` again; the
   share's `Reads` count grew during the run (the copy is read back once
   after its write and once more by the delete).

### Result of the first run (2026-11-20)

The share is `//atlas.local/kronk-backup` (a Synology, SMB 3.1.1,
`serverino`), mounted at `/mnt/nas-backup`, 3.3 TB free.

- Placement: `link(2)` for every file (6 of 6). The share supports hard
  links; the two fallbacks did not run.
- Read-back over the wire: `/proc/fs/cifs/Stats` went from 0 reads and 0
  writes to 10 reads and 5 writes for five files: one write, one read-back
  and one read by the delete, each.
- Modes: `-rw-------` on every copy. Hashes equal to the ones taken from
  the local files before the run.
- **Times: not kept by the first run. Fixed.** Every copy carried the time
  of the run. An SMB server stamps a file when the handle that wrote it
  closes and discards a time set through that handle before the close
  (three orders of `fsync` and `utime` tried on the share; none held). A
  time set after the close holds, through a path or a read-only handle.
  The archive now sets the time again through the read-back handle,
  checks it (2 s slack; the share keeps 100 ns) and fails the copy, keeping
  the recording, on a share that does not keep it. The five copies of the
  first run had their times set back by hand from the list taken before the
  run, to the second (the fractions were not recorded). A sixth recording
  archived with the fixed script kept its time to 100 ns.
- Inodes: unchanged over fifteen minutes and across the time repair; no
  `serverino` message in the kernel log. Not checked across an unmount and
  mount (needs root); a change there refuses deletes, it does not lose
  files.
- First config tar: `kronk-config-2026-11-20.tar.gz`, 5.5 MB, 475 members,
  written and verified in 3 minutes (nearly all of it hashing 230 GB of
  models); its `.env` member is byte-identical to the live file. Its notice
  called `sprint-M17` (still run by `wyoming-piper`; a local tag whose
  commit is on `origin/refactor`) "not a local tag". `repo-state.txt` was
  right; the notice's sentence now covers only a tag git cannot resolve.
- A stray `#recycle` directory on the share is the Synology's recycle bin.
  The jobs read and write only `config/` and `recordings/`, so it is not in
  the notices' totals. The backup account cannot list it (permission
  denied), but its time moved at each run, so it very likely receives the
  temporary name of every archived file when the archive unlinks it (a
  second name for the same data, no extra space while the copy exists).
  Drew's decision (2026-11-21): the recycle bin stays on.

## Blog hooks

- The database you cannot open: Music Assistant holds an exclusive lock on
  its SQLite files, the WAL is thirty times the database, and the backup is
  crash recovery on purpose: read the pair twice, take it only when nothing
  moved, let SQLite replay it somewhere private.

- The NAS that changed the time: `utime` before `close` is discarded by an
  SMB server, and no test against a local directory can show it. Found by
  the one run the tests could not do.
- "Small, monthly, config only": what it takes to rebuild a home AI box is
  5 MB, most of it a solar history database; the 230 GB of models is a list
  of checksums.
- An unmounted mount point is the most convincing fake backup there is: the
  check that refuses to write into it.
- logrotate for a voice assistant's ears: copy, read back, then delete,
  through a container, because the files belong to root.
- The end-to-end test found three ways to lose a recording that the happy
  path never shows: a file named like the job's own temp file, a file
  swapped between verify and delete, and a symlink that made a file its
  own backup. The fix was to delete code: no reclaiming of temp files, no
  path-based opens, no trusting a check made minutes earlier.
- Testing the restore for real against a Home Assistant with no network at
  all: integrations fail to set up, and that is the proof it cannot touch
  the house.
