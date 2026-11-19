# Backups: monthly config tar, weekly recordings archive

Sprint M16 (built 2026-11-19). Plan: "M16. Backups: small, monthly, config
only" in `Kronk evaluation findings and implementation plan.md`. Criteria:
`docs/sprint/M16-criteria.md`. Build report: `docs/sprint/M16-build.md`.
Restore: `docs/runbooks/restore.md`.

## What it does

Three timers, one destination (`/mnt/nas-backup`, the Synology share
`//atlas.local/kronk-backup` mounted by `/etc/fstab`):

| Job | When | What |
| --- | --- | --- |
| Config backup, `scripts/backup.sh` | the 10th of each month, 01:30 | one tar, `config/kronk-config-<YYYY-MM-DD>.tar.gz`, about 5 MB; the newest 12 kept |
| Recordings archive, `scripts/archive_recordings.sh` | Mondays, 01:00 | voice recordings older than 7 days moved from `~/kronk-voice-recordings` to `recordings/<device>/<pipeline>/<YYYY-MM>/<run>/` |
| Stale check, `scripts/backup_check.sh` | daily, 09:30 | warns when the newest config tar is over 35 days old, the archive has not finished a run in 9 days, the share is not mounted, or a job's last run failed |

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
  `/etc/kronk/` (read through a throwaway `alpine:3.20` container; root-only);
- household data: `data/solar.db`, `data/music_taste.yaml`,
  `data/music_taste.db`, `data/kronk_state.md`, `data/mm_known_hosts`,
  `data/yt-music-cookie`, `data/upgrades/status.json`, the labelled voice
  clips `~/kronk-voice-labels/` (absent until the first sheet). SQLite files
  are copied with the online backup API: a consistent copy of a live DB;
- Home Assistant: `.storage/`, the YAML files, `blueprints/`, `.HA_VERSION`
  (read live with `docker exec homeassistant tar`; no recorder DB, TTS cache
  or logs);
- Music Assistant: `settings.json` only (`library.db` is 44 MB, over the
  plan's 10 MB rule);
- the live config directories `../kronk-config/prod` and `dev`, whole, with
  `.git`;
- `~/.config/systemd/user/` (units, drop-ins, the `.wants` symlinks as
  symlinks) and `~/.config/kronk/`;
- `/etc/default/grub`, `/etc/docker/daemon.json`;
- generated: `repo-state.txt` (commit, branch, commits not pushed,
  production tag per container), `images.txt` (compose pins and what runs),
  `models.tsv` (every GGUF under `/opt/models` and `~/model-staging` and
  every GGUF a unit names: path, size, SHA-256), `host-facts.txt` (kernel,
  `/proc/cmdline`, the NAS lines of `/etc/fstab`), and `MANIFEST` (each
  member's SHA-256, size, how it was read and source; what could not be
  read; what was absent).

Not in it: models, llama.cpp and Whisper binaries, Tempo traces, HA's
recorder DB, `sessions.db`, metrics, MA's library, history, auth and
playlists, the repo itself (it is on GitHub; `repo-state.txt` and the end
notice say how many commits were not pushed).

**The tar holds every secret in the clear.** It is mode 0600 and the share
is mounted 0600/0700 for the operator, but anyone with the NAS account or
the NAS's admin can read it. Encryption is Drew's decision, not built.

## How it works

### The config backup

1. Takes `data/backups/lock` (shared by both jobs). Waits up to 30 minutes
   while the other job runs or while the monthly upgrade run holds
   `data/upgrades/lock` (tested, never taken, never created), then declines
   with a notice.
2. Start notice.
3. The destination check: `/mnt/nas-backup` (the default, or any path with
   `KRONK_BACKUP_REQUIRE_MOUNT=1`) must be a mount point and writable (a
   probe file is created and removed). Otherwise the run fails, writes
   nothing there, and says so. A tar written into an unmounted
   `/mnt/nas-backup` would sit on the local disk and look like a backup.
4. Removes its own stale temporary file
   (`.kronk-config-<date>.tar.gz.tmp`, exact pattern, regular files, in
   `config/` only).
5. Reads everything, writes `.kronk-config-<date>.tar.gz.tmp` (0600),
   fsyncs, reads it back (SHA-256 equal to the one computed while writing;
   `tarfile` reads it to the end with the expected member count), checks the
   size ceiling (50 MB, `KRONK_BACKUP_MAX_MB`: over it is a failure naming
   the five largest members), then renames it. A second run the same day
   writes `kronk-config-<date>.2.tar.gz`; nothing is overwritten.
6. Retention: only after a good run, keeps the newest 12
   (`KRONK_BACKUP_KEEP`) files named `kronk-config-<date>[.<n>].tar.gz` in
   `config/`; nothing else is touched.
7. End notice, status file.

What it could not read (HA down, a permission) is listed in the MANIFEST
and the notice ("NOT READ"), and raises the `/services` warning. The run
still writes the rest.

The model list hashes about 230 GB the first time (3 minutes 15 seconds
on 2026-11-19, 113 MB of memory, reads with `POSIX_FADV_DONTNEED` so the
page cache is not flushed), then reuses `data/backups/model_sums.json`
while a file's size and mtime are unchanged: a later run takes 2 seconds.

### The recordings archive

For each `<device>/<pipeline>/<run>/<file>` under the recordings directory
whose mtime is older than 7 days (`KRONK_ARCHIVE_AGE_DAYS`; the M12
labelling script needs recent clips locally):

- not on the share: copied to `.kronk-archive.tmp` in the target run
  directory, fsynced, read back (size and SHA-256), mtime set, renamed;
- on the share with the same SHA-256 (a run killed between copy and
  delete): not copied again;
- on the share with a different SHA-256: never overwritten; both kept; the
  run fails so a person looks;
- changed while it was read (size, mtime or inode moved): the temporary
  copy is removed and the file is left for the next run;
- disk full: the temporary copy is removed and the run stops.

Then one throwaway `alpine:3.20` container (`--network none`, only the
recordings directory mounted) deletes the verified files by literal path
(passed as arguments, never part of the script) and removes run
directories left empty. Each file is checked to be gone afterwards; one
that is not is reported and finished by the next run. Nothing on the share
is ever deleted except the job's own `.kronk-archive.tmp`. Symlinks are not
followed, and anything not at recording depth is reported and left alone.

The run directory is part of the archive path because HA names every
recording `01_stt-<engine>.wav`; the directory (HA's monotonic clock) is
what tells them apart.

### The stale check

Reads the newest `kronk-config-*.tar.gz` on the share (its mtime) and the
archive's last good run from the status file. One notice when the set of
warnings changes, repeated weekly while it holds (day counts do not count as
a change). `/services` computes the same ages from the status file, so a
check that never runs still shows a stale job.

## Install (Drew)

Nothing is installed by the build. Run from the repo.

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
scripts/backup.sh                              # the first tar; two notices on the phone
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
(`orchestrator/backups_api.py`) in production: it ships with the next
tagged deploy. Until then `/api/backups` is 404 on production and the card
says "could not load".

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
- The last runs: `python3 scripts/backups.py status`.
- Logs: `journalctl --user -u kronk-backup.service -n 100` (and
  `kronk-recordings-archive`, `kronk-backup-check`).

## Gotchas

- **HA is copied live.** It is not stopped. Each `.storage` file is written
  whole by HA (temporary file, then rename), so none is half-written; two
  files can be from moments apart (a device in one registry a second before
  its entities in another). The restore test started HA cleanly from such a
  copy. The runbook says how to take one file from an older tar.
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
  `<device>/<pipeline>/<run>/<file>`) fails each archive run until it is
  removed. Deliberate: nothing writes there but HA.
- **`MA_TOKEN` does not survive a restore** (MA's `auth.db` is not backed
  up); make a new one.
- **The plan's build recipes** (llama.cpp for gfx1151, the Whisper wheel)
  are docs, not scripts: `docs/CHANGE_2026-06-09_gemma_vulkan_switch.md` and
  `docs/VOICE_SETUP.md`. The runbook points at them.

## Blog hooks

- "Small, monthly, config only": what it takes to rebuild a home AI box is
  5 MB, most of it a solar history database; the 230 GB of models is a list
  of checksums.
- An unmounted mount point is the most convincing fake backup there is: the
  check that refuses to write into it.
- logrotate for a voice assistant's ears: copy, read back, then delete,
  through a container, because the files belong to root.
- Testing the restore for real against a Home Assistant with no network at
  all: integrations fail to set up, and that is the proof it cannot touch
  the house.
