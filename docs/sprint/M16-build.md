# M16 build report: config backup, recordings archive, restore runbook

Status: in progress

Branch `refactor`. Criteria: `docs/sprint/M16-criteria.md`. Plan: "M16.
Backups: small, monthly, config only" in `Kronk evaluation findings and
implementation plan.md`.

Tests before: `5457 passed, 3 skipped, 53 xfailed` (`./scripts/run_tests.sh`,
2026-11-19 09:45).

## Check first (criteria 1 and 2)

Measured 2026-11-19 between 09:44 and 10:00 local, read-only. HA and Music
Assistant files were read with `docker exec <container> du/ls/cat/tar` (no
stop, no restart, nothing written). Sizes are bytes on disk unless marked
gzip.

### Sizes, and in / out / cannot be read

| Item | Size | Decision | Reason |
| --- | --- | --- | --- |
| HA `.storage/` (no recorder DB: `home-assistant_v2.db` lives in `/config`, not `.storage`) | 570,523 (30 files; `core.entity_registry` 384,408) | in | plan; read by `docker exec homeassistant tar` |
| HA YAML: `configuration.yaml`, `automations.yaml`, `rest_command.yaml`, `scenes.yaml`, `scripts.yaml`, `secrets.yaml`; `blueprints/`; `.HA_VERSION` | 4,581 YAML; blueprints 68,159 | in | plan ("and the YAML files"); blueprints hold the Kronk music blueprint |
| HA `.storage` + YAML + blueprints, as one gzip tar | 69,345 gzip | | |
| HA recorder DB `home-assistant_v2.db` (+ `-wal`, `-shm`) | 17,059,840 + 4,173,592 + 32,768 | out | plan: excluded |
| HA `tts/` cache, `.cache/`, `deps/`, logs, `.ha_run.lock`, `automations.yaml.bak-20260904_162955` | tts 28,375,507; .cache 6,985,588; logs 85,044 | out | plan: cache and logs excluded; the `.bak` is a hand copy, not config |
| MA `settings.json` | 63,229 (9,751 gzip) | in | plan; read by `docker exec music-assistant-server cat` |
| MA `library.db` | 44,494,848 (8,512,038 gzip), plus `-wal` 4,321,912 | out | over the plan's 10 MB rule (Drew 2026-11-14: settings.json alone; MA's metadata is recreated) |
| MA `auth.db` (94,208 + wal 3,217,752), `playlists/` (27,703), `audio_analysis.db` (474 MB), `library.db.backup` (3.36 GB) | | out | not in the plan's table. `auth.db` holds MA's users and the token behind `MA_TOKEN`: without it a restored MA needs a new token (runbook, human steps). `playlists/` is 28 KB of MA-made playlists: listed for Drew's decision |
| `data/solar.db` | 28,504,064 (4,825,972 gzip) | in | Drew 2026-11-14: include. Read with SQLite's online backup API (consistent copy of a live DB) |
| `data/music_taste.yaml` | 1,177 | in | plan |
| `data/music_taste.db` | 380,928 | in | not in the plan's table; it holds the feedback on Kronk's picks (skips, stops, "not that", Pandora thumbs) and the model-made genre tags; the profile part is rebuilt nightly, the feedback cannot be recreated (`docs/features/music-taste.md`, "Household taste"). Small. Departure, listed below |
| `data/kronk_state.md` | 293 | in | not in the plan's table; the orchestrator's shared-state file (`orchestrator/main.py:80`, facts about the household). Small |
| `data/mm_known_hosts` | 142 | in | the MagicMirror host key tool_service pins (`tool_service/main.py:993`); without it the first SSH would need a new trust decision |
| `data/yt-music-cookie`, `secrets/yt-token.txt` | 2,101; 2,143 | in (secret) | provider login material for Music Assistant (not read by Kronk code). The cookie dates from 2026-05-31 and may be stale |
| `secrets/mm/kronk-mm-update` (+ `.pub`) | 411; 97 | in (secret) | plan (`secrets/`) |
| `.env`, `.env.telemetry`, `searxng/settings.yml` | 1,207; 435; 3,948 | in (secret) | plan |
| `.env.dev` | 1,823 | in | the dev pair's knobs; gitignored, not in git |
| `.env.langfuse` | 1,190 | out | the retired Langfuse stack (not in any compose file today). Listed for Drew |
| `ops/hosts.json` | absent | in, absent | plan lists it; the file does not exist (only `hosts.example.json`). The manifest records it as absent |
| `/etc/kronk/` | cannot list as the operator (`Permission denied` on the directory) | in, through a container | ruling 4: read by a throwaway `docker run --rm --network none -v /etc/kronk:/src:ro alpine:3.20` (an image already on the host). Holds `nas-music.cred` (named in `/etc/fstab`). Not read during this check |
| `/etc/fstab` NAS lines | 1 line (`//atlas.local/music /mnt/nas-music cifs ro,…,credentials=/etc/kronk/nas-music.cred,…`) plus its comments | in (host facts) | plan. The line carries no password (it names the credentials file) |
| Kernel parameters | `/proc/cmdline` (`ttm.pages_limit=26624000 ttm.page_pool_size=26624000 …`); `/etc/default/grub` 1,620 | in (host facts; `/etc/default/grub` as a member) | plan ("GRUB kernel parameters") |
| `/etc/docker/daemon.json` | 75 (readable, mode 644) | in if readable | not in the plan's table; it sets Docker's `journald` log driver, which the telemetry and capture docs depend on. Departure |
| `../kronk-config/prod` | 73,216 (`.git` 43,834; 1 commit; clean; no remote) | in, tar of the whole directory with `.git` | see below |
| `../kronk-config/dev` | 132,202 (`.git` 102,820; 41 commits; clean; no remote) | in, same | see below |
| `~/.config/systemd/user/` | 28,080 (36 unit files, 2 drop-ins, 2 `.wants` directories of symlinks) | in | plan. Symlinks are stored as symlinks (which units are enabled), never followed |
| `~/.config/kronk/` | 30 (`upgrade.env`, one line) | in | M18's branch file |
| Labelled voice clips `~/kronk-voice-labels/` | absent (no labelling sheet has been made yet) | in, absent | Drew 2026-11-14: include. The manifest records it as absent until the first sheet exists |
| `data/upgrades/` | absent (the M18 timers are installed but have not yet written a status file) | in: `status.json` only, when present | the upgrade record (intent, blocks). The per-run logs under `runs/` are out: they grow and are not needed to rebuild |
| Hot tub data `data/hottub/` | 296 (`status.json`, rewritten every minute by `kronk-hottub-monitor.service`) | out | a live status snapshot, not configuration; the monitor's code is in git and its unit is in the units member |
| Recordings `~/kronk-voice-recordings` | 1,587,772 in 13 WAVs (2 devices, pipeline `kronk`); first file 2026-11-18 07:06 | out of the config tar; moved by the archive job | 2026-11-18: 5 files, 533,980 bytes; 2026-11-19 to 09:36: 8 files, 1,053,792 bytes. About 1.4 MB a day so far (32,000 bytes per second of speech); with capture open-ended that is roughly 40 to 50 MB a month at today's use |
| Models (`/opt/models`, `~/model-staging`) | 29 GGUFs, about 230 GB | out; listed | plan: model list with path, size, SHA-256. `/opt/models/ifm/PROVENANCE.sha256` (398 bytes) goes in as the recorded source |
| `data/sessions.db`, `data/metrics.db`, `data/dev/`, `data/generated/`, other `data/*.json` caches | | out | plan (sessions, metrics) or caches the services rewrite |

Expected config tar: about 6 MB gzip, most of it `solar.db`.

**Live config directories: tar, not `git bundle`.** Both are small (73 KB
and 132 KB with history). A tar of the directory with `.git` restores
byte-identical, keeps any uncommitted edit (a bundle carries commits only),
and needs no `git clone` step in the runbook. A bundle would save a few
kilobytes.

### Units: `~/.config/systemd/user/` against `systemd/` (criterion 2)

| Unit | Finding |
| --- | --- |
| 34 of 36 unit files | identical |
| `kronk-voice-capture-check.service` | differs only by the install-time substitution the unit's own install line makes (`@END_DATE@` became `2026-12-01`, two lines). Not stale; repo copy unchanged. The installed timer is disabled (not in `timers.target.wants`) |
| `llama-props-export.service`, `llama-props-export.timer` | only in the repo (sprint M21 step 2; not installed on the host) |
| `sp5100-tco-load.service` | only in the repo as a user-unit copy; installed as a system unit, `/etc/systemd/system/sp5100-tco-load.service`, identical to the repo copy |
| drop-ins `kronk-upgrade-{minor,major-check}.service.d/branch.conf` | only on the host, by design (M18's install writes them) |
| host-only unit files | none |

Also on the host outside the user directory: system units
`/etc/systemd/system/llama-bonsai.service`, `llama-coordinator.service`,
`llama-devstral.service`, `ollama.service` (+ `.d`), dated April 2026 and
not running. Not in the repo and not in the backup (not in the plan's
table; listed for Drew).

No repo copy was changed.
