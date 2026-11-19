# M16 pass criteria: backups (config, monthly) and the recordings archive

Source: the plan's "M16. Backups: small, monthly, config only" and Drew's
decisions of 2026-11-19. Where the two differ, the later decision wins and
is marked **(2026-11-19)**.

## Scope

Two jobs, one destination, one set of credentials.

- **Config backup.** Monthly. One dated plain tar of what is needed to
  rebuild Kronk: configuration and values, no binaries, no models. Small
  (megabytes).
- **Recordings archive (2026-11-19).** The voice audio HA records into
  `~/kronk-voice-recordings` (capture now continues with no end date) is
  moved to the NAS on a schedule, logrotate style: copied, verified on the
  NAS, and only then deleted locally. Nothing on the NAS is ever deleted by
  it ("keep everything"). This replaces the plan's "M12 audio captures
  beyond the labelled set are not backed up".
- **A restore runbook**, tested once for real against scratch containers.
  Drew rates it as important as the backup.

Not in scope: models, llama.cpp and Whisper binaries, Tempo traces, HA's
recorder database, `sessions.db`, metrics; any backup tool beyond `tar`
and coreutils; the HA major upgrade itself.

## Coordinator rulings

1. **Destination is a directory path**, `KRONK_BACKUP_DIR`, default
   `/mnt/nas-backup`. The NAS is reached the way the music share already
   is: an SMB share mounted by an `/etc/fstab` line with a root-only
   credentials file under `/etc/kronk/`. Agents have no root and do not
   contact the NAS: the build delivers the exact fstab line and credentials
   file content (with placeholders, never the password) as an install step
   for Drew, and everything is built and tested against a scratch
   directory. The scripts read no password and never see one.
2. **A destination that is not a mounted filesystem is a failure, not a
   place to write.** When `KRONK_BACKUP_DIR` is the default (or any path
   flagged as a mount), the scripts check it is a mount point and writable
   before anything else; otherwise they stop, write nothing there (a tar
   written into an unmounted `/mnt/nas-backup` would sit on the local disk
   and look like a backup), and send the failure notice.
3. **Secrets go into the tar** (`.env`, `.env.telemetry`,
   `searxng/settings.yml`, `secrets/`, the `/etc/kronk/` files readable by
   the user). The tar is therefore written mode 0600 and the write-up says
   plainly that the NAS folder holds secrets in the clear. Encryption is
   Drew's decision, listed as open; not built.
4. **Reading without root.** Files only root can read (HA's `.storage` is
   owned by the container's user; the recordings are root-owned;
   `/etc/kronk/*.cred`) are read through `docker exec`/`docker cp` of the
   running container or a throwaway `docker run --rm -v <dir>:/src:ro` of
   an image already on the host, never with sudo. What cannot be read at
   all is listed in the tar's manifest as missing and in the notice; it is
   not a silent gap.
5. **HA is not stopped or restarted** for a backup. `.storage` is copied
   live; the write-up says which files can be caught mid-write and how the
   restore runbook deals with it.
6. **Deleting local recordings** happens only for a file whose copy on the
   destination has the same size and SHA-256, read back from the
   destination after the write, and only when the file is older than the
   archive age (default 7 days, so the M12 labelling tools still find
   recent clips). The delete is by literal path through the same container
   mechanism that can read them. A file that changed during the copy is
   left for the next run.
7. **Notifications (2026-11-19):** each job sends a notice through
   `scripts/lib/notify.sh` when it starts and when it ends. The end notice
   says: success or failure and why; the size of the file(s) just written;
   the total size of everything under the destination (config backups and
   archive, each and together); for the archive, how many recordings moved
   and how many remain local. A failure to send a notice does not fail the
   job and is logged.
8. **Stale check:** a separate small timer alerts when the newest config
   backup is older than 35 days or the newest archive run older than its
   interval plus a margin, so a job that never ran is noticed.
9. **Timers are installed by the coordinator**, not the agents: the build
   delivers unit files under `systemd/` and install/remove blocks.
10. The scripts live under `scripts/`, run from the working tree like the
    upgrade scripts, and take a lock so they never overlap each other or
    the monthly upgrade run (`data/upgrades` lock is not reused; the backup
    waits or declines when an upgrade run is in progress, and the reverse
    is documented, not built).

## Criteria

**Check first**

1. Before any script is written, sizes measured and recorded in the build
   report: HA `.storage` without the recorder database; Music Assistant's
   `settings.json` and `library.db` (include `library.db` only if it is
   10 MB or smaller, the plan's rule); `solar.db`; `data/music_taste.yaml`;
   the live config directories `../kronk-config/prod` and `/dev` (git
   repositories: bundle or tar, justified); `~/.config/systemd/user/`;
   `~/.config/kronk/`; the labelled voice clips; `data/upgrades/`;
   the hot tub data; the recordings directory and its growth per day so
   far. Each item of the plan's table is listed as in, out, or cannot be
   read, with the reason.
2. A reconciliation of `~/.config/systemd/user/` against `systemd/` in the
   repo: units that exist only on the host, only in the repo, or differ.
   Reported; the repo copies are not changed unless trivially stale.

**Config backup**

3. `scripts/backup.sh` (thin wrapper is fine) produces one file
   `kronk-config-<YYYY-MM-DD>.tar` (gzip allowed) under
   `<dest>/config/`, mode 0600, containing every "in" item from criterion
   1, a `MANIFEST` (each member's source path, size, SHA-256, and how it
   was read; every item that could not be read), the repo's commit and
   branch and whether it is pushed, the production tag, the image pins, a
   model list (each GGUF's path, size, SHA-256 or the recorded source), and
   the host facts the plan names (the fstab lines for the NAS, kernel
   parameters from `/proc/cmdline`).
4. Written to a temporary name on the destination, verified (`tar -t`
   reads it to the end; SHA-256 of the file read back equals the one
   computed while writing), then renamed. An interrupted run leaves no
   file that looks like a finished backup, and the next run removes its
   own stale temporary file by literal pattern inside `<dest>/config/`
   only.
5. Retention: keeps the newest N config tars (default 12) and removes
   older ones, only files matching its own name pattern in
   `<dest>/config/`, never anything else on the destination, and never
   when the run that would do it failed.
6. Size guard: a config tar larger than a set ceiling (default 50 MB) is a
   failure with the largest members named, not a silent upload. The first
   real tar's size is in the report.
7. `--dry-run` lists what would go in with sizes and writes nothing;
   `--dest <dir>` overrides the destination for tests and by-hand runs.

**Recordings archive**

8. `scripts/archive_recordings.sh` moves recordings older than the archive
   age into `<dest>/recordings/<device>/<pipeline>/<YYYY-MM>/`, keeping
   file names and modification times, by ruling 6. A second run with
   nothing new copies nothing and deletes nothing. A file already on the
   destination with the same hash is not copied again and is deleted
   locally; one with the same name and a different hash is never
   overwritten: both are kept and it is reported.
9. Fault cases, each with a test: destination not mounted; destination
   full mid-copy; the copy reads back different; the local delete fails;
   the run is killed between copy and delete (next run finishes it);
   a recording being written while the run reads it; a symlink in the
   recordings directory (not followed); a file name with spaces or
   newlines.
10. Nothing outside `~/kronk-voice-recordings` is ever deleted by the
    archive, and nothing on the destination: shown by test with a
    directory tree around both.

**Notices and scheduling**

11. Start and end notices by ruling 7, with the exact texts in the report;
    sizes in human units; tested with a stub notifier. The stale check by
    ruling 8, tested with a clock.
12. Unit files: `kronk-backup.timer` (monthly, at night, not inside the
    upgrade run's 02:00 to 05:00 window on the 1st to 7th),
    `kronk-recordings-archive.timer` (weekly), `kronk-backup-check.timer`
    (daily). Reference copies under `systemd/`, with install and remove
    blocks in the feature doc. Not installed by the build.
13. State for `/services`: the last run of each job (time, result, sizes)
    in a small status file, and a warning line on `/services` when a job
    failed or is stale, in the same style as the upgrade status. If
    wiring `/services` needs an orchestrator change, it is built on the
    dev path and listed for promotion.

**Restore**

14. `docs/runbooks/restore.md`: a rebuild from a bare machine, in order,
    each step naming the exact command and the tar member it uses; what
    needs a human (provider logins, pairing, the HA token, the MA token)
    with what to have ready; how to restore one thing only (HA `.storage`,
    the live config directories, the units).
15. The runbook is tested once for real, without touching production:
    the tar restored into a scratch directory and scratch containers
    (compose project `kronk-restore-test`, loopback ports, no LAN
    listener, no sound, removed afterwards): HA started from the restored
    `.storage` on a spare loopback port with its integrations'
    outbound connections not required to work, far enough to show the
    pipelines, devices and entity registry are present; the live config
    directories restored and validated by the config loader; the restored
    `.env` byte-identical to the live one. Every step that did not work as
    written is fixed in the runbook and listed.

**Safety and hygiene**

16. `./scripts/run_tests.sh` passes; new tests open no socket and touch
    only temporary directories.
17. Production containers, units and timers are unchanged (IDs and start
    times before and after); HA and MA were not restarted; nothing was
    written to the NAS or to `/mnt`; no recording was deleted from the
    real `~/kronk-voice-recordings` by the build or its tests.
18. Docs: `docs/features/backups.md` (what, how, gotchas, install, a
    "Blog hooks" section), `ROADMAP.md`, the plan's M16 status, the
    `# Operations:` style block for any compose file added, CLAUDE.md's
    secrets paragraph if a new secret location exists.

Reports: `docs/sprint/M16-build.md`, `docs/sprint/M16-e2e.md`.
