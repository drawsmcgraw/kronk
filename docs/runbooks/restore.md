# Runbook: restore Kronk from a config backup

Sprint M16. The backup is one tar a month, written by `scripts/backup.sh`
to the NAS (`/mnt/nas-backup/config/kronk-config-<YYYY-MM-DD>.tar.gz`;
guide: `docs/features/backups.md`). This runbook rebuilds Kronk from it on a
bare machine, or restores one part of it. Tested against scratch containers
on 2026-11-19 (`docs/sprint/M16-build.md`, "Restore test"; the fixes and
re-runs in `docs/sprint/M16-fix1.md`); the steps below are the corrected
ones.

**This runbook travels inside every tar** as `RESTORE.md` (with the feature
doc as `BACKUPS.md`), so steps 1 to 5 work from the tar alone, before any
clone. No copy of it at hand? From any computer, download the newest
`config/kronk-config-*.tar.gz` from the NAS (Synology File Station, the
`kronk-backup` shared folder), unpack it, and read `RESTORE.md`; or follow
steps 1 to 3 below, which need nothing else.

Conventions: `$T` is the tar's file name without `.tar.gz` (the directory it
unpacks into: `kronk-config-2026-11-19`, or `kronk-config-2026-11-19.2` for
a second tar that day), `$R` the directory you unpacked it into, `$K` the
repo checkout.

```
T=kronk-config-2026-11-19                     # the tar's name without .tar.gz
R=~/restore/$T                                # where it is unpacked (step 3)
K=~/git-repos/drawsmcgraw/kronk               # the repo
```

A tar named `kronk-config-<date>.incomplete.tar.gz` is missing a required
item (its MANIFEST's `REQUIRED` lines say which): prefer the newest tar
without `.incomplete`, and take only what you need from an incomplete one.

The tar holds secrets in the clear. Unpack it into a 0700 directory with
`umask 077` (step 3) and remove that directory when done (last step).

## What needs a human, and what to have ready

| What | Why it cannot come from the tar | Have ready |
| --- | --- | --- |
| The NAS backup password | the tar is on the NAS; its credentials file is inside the tar | the Synology account `KRONK_BACKUP_NAME` and its password |
| GitHub access | the repo comes from the remote | an SSH key on the GitHub account (`git@github.com:drawsmcgraw/kronk.git`) |
| Model files | not backed up (230 GB); the tar has the list with SHA-256 | Hugging Face access; `models.tsv` names each file |
| Music Assistant image | `kronk/music-assistant:2.11.0b5-kronk.9` is built locally, not on a registry | the MA fork and `ma/Dockerfile` (`docs/plans/MUSIC_ASSISTANT_PLAN.md`, `MA_UPSTREAM_LESSONS.md`) |
| `MA_TOKEN` in `.env` | restored in `.env`; it stays valid because MA's `auth.db` (its row, its user and the key that signs it) is restored too, step 13 (since M16b; shown 2026-11-20 on a scratch MA started from the tar on the NAS: the token accepted, an altered one refused) | nothing; if `auth.db` is not restored, or the token has expired (one year: 2027-10-06), create a long-lived token in MA's UI (Settings, then your profile) and put it in `.env` |
| Music Assistant provider logins | `settings.json` carries most provider settings; cookies and OAuth can be stale | YouTube Music cookie, Pandora and other provider logins, in MA's UI |
| HA mobile app on the phone | the app trusts the server it paired with | the phone; it reconnects by itself when the HA URL is the same |
| Voice PE pucks and the Satellite1 | the ESPHome encryption keys are in `.storage` and come back; a puck with a new IP or a reset puck must be re-added | the pucks on the same Wi-Fi; HA, Settings, Devices, ESPHome |
| `SETTINGS_PASSPHRASE_HASH` | in `.env`, restored; the passphrase itself is only in Drew's head | the `/settings` passphrase |
| Grafana admin login | in `.env.telemetry`, restored | nothing |
| `HA_TOKEN` | restored in `.env`; it stays valid because HA's `.storage/auth` (its signing keys) is restored too (tested) | nothing; if HA's `.storage` is not restored, make a new long-lived token in HA (profile, Security) |

## Rebuild from a bare machine, in order

### 1. OS and kernel parameters

Install Ubuntu (25.10 at the time of writing; `host-facts.txt` names the
version), user `drew`, hostname `kronk`. Then the kernel parameters, from
member `etc/default/grub` (and the running ones in `host-facts.txt`,
`/proc/cmdline`):

```
sudo apt install docker.io docker-compose-v2 git cifs-utils jq curl python3 python3-venv
sudo usermod -aG docker drew                  # log out and in again
sudo loginctl enable-linger drew              # user units start at boot without a login
```

The GRUB file is restored in step 4, after the tar is reachable.

### 2. Mount the NAS backup share

The credentials file is inside the tar, so type it once (this is the
human step). The fstab line (the same as `docs/features/backups.md`,
"Install (Drew)"; `ip=` because mDNS is not up at boot):

```
# Synology backup share for scripts/backup.sh and scripts/archive_recordings.sh (sprint M16).
# nofail: a NAS outage never blocks boot; the jobs check the mount point and fail loudly.
//atlas.local/kronk-backup  /mnt/nas-backup  cifs  rw,ip=192.168.1.14,credentials=/etc/kronk/nas-backup.cred,iocharset=utf8,uid=1000,gid=1000,file_mode=0600,dir_mode=0700,_netdev,nofail,x-systemd.mount-timeout=30  0  0
```

```
sudoedit /etc/fstab                           # add the two comment lines and the line above
sudo install -d -m 0700 /etc/kronk
sudo install -m 0600 /dev/null /etc/kronk/nas-backup.cred
sudoedit /etc/kronk/nas-backup.cred           # username=<KRONK_BACKUP_NAME> / password=<the password>
sudo mkdir -p /mnt/nas-backup && sudo systemctl daemon-reload && sudo mount /mnt/nas-backup
ls /mnt/nas-backup/config/                    # the tars, newest last
```

### 3. Unpack the tar and check it

```
mkdir -p -m 0700 ~/restore
(umask 077 && tar -xzf /mnt/nas-backup/config/$T.tar.gz -C ~/restore)
cd $R
awk -F'\t' '$1 ~ /^[0-9a-f]{64}$/ {print $1 "  " $4}' MANIFEST | sha256sum -c --quiet && echo all members match
sed -n '/REQUIRED items/,$p' MANIFEST          # what this backup does NOT have
cat repo-state.txt                            # commit, branch, not pushed, the bundle, production tag
less RESTORE.md                               # this runbook, as it was when the tar was taken
```

`umask 077` makes every unpacked file and directory private to you (the
tar's own modes are kept only where they are stricter); the top directory
is 0700 in the tar too.

`sha256sum -c` prints only failures. A member whose name has a control
character is JSON-escaped in the MANIFEST and fails the check by name only;
none exist today.

### 4. Host files: GRUB, Docker, /etc/kronk, fstab

```
sudo install -m 0644 $R/etc/default/grub /etc/default/grub && sudo update-grub
sudo install -m 0644 $R/etc/docker/daemon.json /etc/docker/daemon.json && sudo systemctl restart docker
for f in $R/etc/kronk/*; do [ "${f##*/}" = nas-backup.cred ] || sudo install -m 0600 -o root -g root "$f" /etc/kronk/; done
sed -n '/network filesystem lines/,$p' $R/host-facts.txt   # the NAS lines to add to /etc/fstab
sudoedit /etc/fstab                           # add the music share line (the backup line is already there)
sudo mkdir -p /mnt/nas-music && sudo mount -a
sudo reboot                                   # for the kernel parameters; then, logged in again:
T=kronk-config-2026-11-19                     # the variables (Conventions) are gone after the reboot: set them again
R=~/restore/$T
K=~/git-repos/drawsmcgraw/kronk
cat /proc/cmdline                             # has ttm.pages_limit=... ttm.page_pool_size=...
```

The loop copies every file of `etc/kronk/` except `nas-backup.cred`: the
one typed in step 2 is current, and the tar's copy holds the password as it
was when the tar was taken. To take the tar's copy anyway, install it by
name: `sudo install -m 0600 -o root -g root $R/etc/kronk/nas-backup.cred /etc/kronk/`
(this overwrites the one typed in step 2).

### 5. Clone the repo

```
mkdir -p ~/git-repos/drawsmcgraw && cd ~/git-repos/drawsmcgraw
git clone git@github.com:drawsmcgraw/kronk.git
cd $K && git checkout <branch from repo-state.txt>
```

When `repo-state.txt` names `repo-unpushed.bundle` (commits no remote had
when the tar was taken, the production tag among them when it was not on
the remote either), bring them in from the tar:

```
git bundle verify $R/repo-unpushed.bundle     # "is okay": the clone has what it builds on
git bundle list-heads $R/repo-unpushed.bundle # what it holds: decides the next lines
git fetch $R/repo-unpushed.bundle '+refs/heads/*:refs/remotes/bundle/*' '+refs/tags/*:refs/tags/*'
git checkout -B <branch> bundle/<branch>      # only when list-heads shows refs/heads/<branch>
```

Three layouts need a different line (tested 2026-11-19, `M16-e2e4.md`
finding 3):

- **The branch in `repo-state.txt` is not on GitHub** (a local-only
  branch): the `git checkout <branch>` above fails with "pathspec did not
  match". Skip it; the bundle block creates the branch.
- **`list-heads` shows only tags** (the branch was pushed, the production
  tag was not): run the `fetch`, skip `checkout -B`. The tag is now local.
- **`list-heads` shows only `HEAD`** (the tar was taken on a detached
  HEAD): the refspecs above match nothing. Use
  `git fetch $R/repo-unpushed.bundle HEAD && git checkout FETCH_HEAD`, then
  `git switch -c restored` to keep it on a branch.

Then:

```
git log -1 --format=%H                        # compare with "commit:" in repo-state.txt
git rev-parse --verify <production tag>       # if missing: the "production tag ... on the remote" line gives `git tag <tag> <commit>`
python3 -m venv tests/.venv && tests/.venv/bin/pip install -r tests/requirements.txt
```

`tests/.venv` is not optional: the config check (step 7) and
`scripts/deploy_tag.sh` (step 11) run the services' loaders with it, and
system `python3` lacks their packages (tested: `ModuleNotFoundError: No
module named 'pydantic'`).

`repo-state.txt` says how many commits no remote-tracking branch had when
the tar was taken (HEAD and the production tags) and whether they are in
the bundle; over its size cap the bundle is left out, and that month's
notice said so, on any branch. The bundle holds only what no
remote-tracking branch had: it needs the clone from GitHub first.

### 6. Secrets and local config into the repo

```
cd $K
cp -p $R/repo/.env $R/repo/.env.dev $R/repo/.env.telemetry .
cp -rp $R/repo/secrets .
mkdir -p data && cp -rp $R/repo/data/. data/
sudo install -o 977 -g 977 -m 0664 $R/repo/searxng/settings.yml searxng/settings.yml
chmod 0600 .env .env.dev .env.telemetry secrets/mm/kronk-mm-update
[ -e $R/repo/ops/hosts.json ] && cp -p $R/repo/ops/hosts.json ops/
cmp $R/repo/.env .env && echo .env restored
```

`searxng/` belongs to the container's user (uid 977), hence `install -o 977`.
`data/solar.db` and `data/music_taste.db` are SQLite copies taken with the
online backup API: consistent, ready to use.

### 7. The live config directories

Each is its own git repository, next to the repo:

```
mkdir -p ~/git-repos/drawsmcgraw/kronk-config
cp -a $R/live-config/prod $R/live-config/dev ~/git-repos/drawsmcgraw/kronk-config/
for i in prod dev; do
  git -C ~/git-repos/drawsmcgraw/kronk-config/$i fsck --no-progress && git -C ~/git-repos/drawsmcgraw/kronk-config/$i status --short
  $K/tests/.venv/bin/python $K/scripts/config_check.py --code $K --dir ~/git-repos/drawsmcgraw/kronk-config/$i
done
```

`config_check.py` runs both services' loaders on the directory and prints
`valid` (exit 0) when both load. Run `fsck` and `status` before anything
else touches the directory: `git status` refreshes `.git/index`, so a
`diff -r` against another copy afterwards shows the index as changed.

### 8. Models

Not in the tar. Fetch each file named in `models.tsv` to the path given
there, then check them all:

```
sudo install -d -o drew -g drew /opt/models   # its parent is root's; the tar has no models
awk -F'\t' '$1 ~ /^[0-9a-f]{64}$/ {print $1 "  " $4}' $R/models.tsv | sha256sum -c
```

The tar does not record a download URL per file (only
`models/opt/models/ifm/PROVENANCE.sha256` for the K2 files); search Hugging
Face by file name and compare the SHA-256. The fifth column names the unit
that loads the file; files with `-` are loaded by none and can wait.

### 9. llama.cpp and Whisper

Not in the tar (binaries). The units name the versions:

- llama.cpp: the Vulkan release named in each `llama-*.service`
  (`ExecStart=…/llama-cpp/llama-b9611-vulkan/llama-server`). Download that
  release's Ubuntu Vulkan build from the llama.cpp GitHub releases and unpack
  it at the same path; `docs/CHANGE_2026-06-09_gemma_vulkan_switch.md` has
  the layout and the `LD_LIBRARY_PATH` line.
- wyoming-faster-whisper with CTranslate2 built for HIP (gfx1151):
  `docs/VOICE_SETUP.md`, "Phase 2" (CTranslate2 4.7.2 from source, the wheel,
  `~/services/wyoming-whisper/.venv`). The model (`large-v3-turbo`) downloads
  on first start.

### 10. User units

The hot tub monitor's unit runs from its own venv, which is not in the tar:

```
cd $K && python3 -m venv hottub_monitor/.venv && hottub_monitor/.venv/bin/pip install geckolib==1.0.15
```

Then the units:

```
mkdir -p ~/.config/systemd/user ~/.config/kronk
cp -a $R/systemd-user/. ~/.config/systemd/user/
cp -a $R/config-kronk/. ~/.config/kronk/
systemd-analyze --user verify ~/.config/systemd/user/*.service ~/.config/systemd/user/*.timer
systemctl --user daemon-reload
cd ~/.config/systemd/user && systemctl --user start $(ls default.target.wants timers.target.wants | grep -E '^(llama|wyoming|kronk|gpu)-.*\.(service|timer)$')
sudo cp $K/systemd/sp5100-tco-load.service /etc/systemd/system/ && sudo systemctl enable --now sp5100-tco-load
```

The copy also brings back `*.wants` links to snap units (`snap.*`); the
start line's pattern skips them, and they are harmless when snap is not
installed. The `*.target.wants` symlinks come back with the copy, so what was enabled
is enabled again at the next boot; the `start` line starts the same set now
(the llama and Whisper units, the `kronk-*` services such as the hot tub
monitor, and every enabled timer). `verify` warns about units whose binary
is not there yet (step 9).

### 11. Compose stacks, in dependency order

```
cd $K
mkdir -p ~/kronk-voice-recordings           # HA refuses to start without it (create_host_path: false)
scripts/deploy_tag.sh deploy <production tag from repo-state.txt>   # Kronk stack first: it creates the kronk_kronk network
docker compose -f docker-compose.telemetry.yml --env-file .env.telemetry up -d   # joins kronk_kronk (external)
```

Then Home Assistant (step 12) and Music Assistant (step 13), then the dev
pair: `docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`.

### 12. Home Assistant `.storage` and YAML

The volume is `external: true` in `docker-compose.ha.yml`; create it, fill
it, then start HA:

```
docker volume create kronk_ha-config
docker run --rm --pull=never --network none -v kronk_ha-config:/config \
  -v $R/ha/config:/src:ro alpine:3.20 sh -c 'cp -a /src/. /config/ && chown -R 0:0 /config'
docker compose -f docker-compose.ha.yml up -d
journalctl CONTAINER_NAME=homeassistant -n 200 | grep -iE 'error|invalid|corrupt' | head
```

(On a bare machine pull `alpine:3.20` first, or use any image with `sh`
and `cp`.) The copy is live (HA is not stopped for a backup), so one
`.storage` file can be a moment newer than another. HA writes each file
whole (a temporary file, then a rename), so no file is half-written; a
registry that refers to something another file does not have yet shows as
one missing or orphaned entity, which HA reports under Repairs. If HA says a
`.storage` file is invalid and starts that part empty, stop HA, take that one
file from the previous month's tar (same command, one file), start HA.

### 13. Music Assistant

Build the image first (see the table above). Then the settings, the
logins (`auth.db`) and the playlists MA made itself; the library re-syncs
from the NAS and providers. MA must not be running. An `auth.db-wal` or
`auth.db-shm` left in the volume goes first: SQLite would replay an old
WAL onto the restored `auth.db` (the tar's copy is one whole file, no WAL):

```
docker volume create kronk-ma_ma-config
docker run --rm --pull=never --network none -v kronk-ma_ma-config:/data \
  -v $R/ma:/src:ro alpine:3.20 sh -c 'rm -f /data/auth.db-wal /data/auth.db-shm \
    && cp /src/settings.json /src/auth.db /data/ && chown 0:0 /data/settings.json /data/auth.db \
    && if [ -d /src/playlists ]; then cp -a /src/playlists /data/ && chown -R 0:0 /data/playlists; fi'
docker compose -f docker-compose.ma.yml up -d
```

`MA_TOKEN` in the restored `.env` works again (table above; shown on a
scratch MA, 2026-11-20): check with tool_service's Pandora thumbs, or MA's
UI, Settings, your profile, where the token is listed. A playlist file
restored into `playlists/` is on disk but was not in MA's library list in
that scratch run (the library database is not in the tar, and the scratch
MA had no network to sync); whether it comes back by itself after a sync
is not shown. Lost by design (Drew, 2026-11-14): MA's library
metadata, play history and favourites. Then redeploy the Kronk stack's
tool_service with the tag.

### 14. Household data

Already done in step 6 (`solar.db`, the taste file and database, the
MagicMirror host key). The labelled voice clips:

```
[ -d $R/voice-labels ] && cp -a $R/voice-labels ~/kronk-voice-labels
```

The archived voice recordings stay on the NAS
(`/mnt/nas-backup/recordings/`); nothing needs to come back for Kronk to
run.

### 15. Verify

- `/services` (`http://kronk/services`): every probe green, the Backups and
  Upgrade cadence cards without a warning.
- `./scripts/run_tests.sh` passes (the M10 corpus is in it).
- `curl -s localhost/api/chat -d '{"model":"kronk","messages":[{"role":"user","content":"what time is it"}],"stream":false}'`
  answers (the shim HA uses).
- `python3 scripts/voice_replay.py --label restore` (dry run on the dev path).
- One spoken request per puck ("Okay Nabu, what time is it").
- Then the backup itself: `scripts/backup.sh --dry-run`, and install the
  timers (`docs/features/backups.md`).

### 16. Clean up

```
rm -rf ~/restore/kronk-config-2026-11-19    # type the directory's name: it holds every secret in the clear
rm -f ~/restore/ha-storage-before-restore.tgz   # left by "Restore one thing only" (HA's auth store), if made
```

## Restore one thing only

All three start from step 3 (unpack and check), and end with step 16.

**HA `.storage` (after a bad change through HA's UI).** HA restarts: pick
the moment (CLAUDE.md, HA restarts are never a side effect). Keep the
current state first, then replace only `.storage`:

```
cd $K
docker run --rm --pull=never --network none -v kronk_ha-config:/config:ro -v ~/restore:/out alpine:3.20 \
  sh -c "umask 077 && tar -czf /out/ha-storage-before-restore.tgz -C /config .storage && chown $(id -u):$(id -g) /out/ha-storage-before-restore.tgz"
docker compose -f docker-compose.ha.yml stop homeassistant
docker run --rm --pull=never --network none -v kronk_ha-config:/config -v $R/ha/config/.storage:/src:ro alpine:3.20 \
  sh -c 'cp -a /src/. /config/.storage/ && chown -R 0:0 /config/.storage'
docker compose -f docker-compose.ha.yml start homeassistant
```

The "before" tar holds HA's auth store: it is written 0600 and owned by
you, inside the 0700 `~/restore`, and step 16 removes it.

To restore a single file (`core.entity_registry`, say), mount that file
under its own name and copy just it:

```
docker compose -f docker-compose.ha.yml stop homeassistant
docker run --rm --pull=never --network none -v kronk_ha-config:/config \
  -v $R/ha/config/.storage/core.entity_registry:/src/core.entity_registry:ro alpine:3.20 \
  sh -c 'cp -a /src/core.entity_registry /config/.storage/core.entity_registry && chown 0:0 /config/.storage/core.entity_registry'
docker compose -f docker-compose.ha.yml start homeassistant
```

**The live config directories.** The dev or production prompts and tuning
as they were:

```
mv ~/git-repos/drawsmcgraw/kronk-config/prod ~/git-repos/drawsmcgraw/kronk-config/prod.before-restore
cp -a $R/live-config/prod ~/git-repos/drawsmcgraw/kronk-config/prod
tests/.venv/bin/python scripts/config_check.py --code $K --dir ~/git-repos/drawsmcgraw/kronk-config/prod
scripts/config_reload.sh prod
```

Often a `git -C ~/git-repos/drawsmcgraw/kronk-config/prod log` and a
`git revert` in the live directory is the smaller fix; the tar is for a
directory that is gone.

**The user units.**

```
diff -r $R/systemd-user ~/.config/systemd/user     # what changed since the tar
cp -p $R/systemd-user/<unit> ~/.config/systemd/user/<unit>
systemctl --user daemon-reload && systemctl --user restart <unit>
```
