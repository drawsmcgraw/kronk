# M16 build report: config backup, recordings archive, restore runbook

Status: built 2026-11-19; not installed (timers, the NAS share and the /services promotion wait for Drew)

Branch `refactor`. Criteria: `docs/sprint/M16-criteria.md`. Plan: "M16.
Backups: small, monthly, config only" in `Kronk evaluation findings and
implementation plan.md`.

Tests before: `5457 passed, 3 skipped, 53 xfailed` (`./scripts/run_tests.sh`,
2026-11-19 09:45). Tests after: `5506 passed, 3 skipped, 53 xfailed, 6
warnings in 76.39s` (49 new, `tests/test_m16_backups.py`).

Commits: `268e64a` (check first), `ea5c1a7` (the build), this report.

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

## What changed

| File | What |
| --- | --- |
| `scripts/backups.py` (1288 lines, standard library) | the three jobs. Host access in `Ops` (`:129`; container reads `:166`, the helper container `:174`, the literal-path delete `:187`); `take_lock` `:309` (own lock; waits while `data/upgrades/lock` is held, `upgrade_lock_held` `:291` tests it without taking or creating it); `dest_problem` `:339` (mount point, probe); `collect` `:532` (what goes in), `read_sqlite` `:489` (online backup API), `repo_state` `:600`, `host_facts` `:650`, `model_list` `:692` (hash cache), `manifest` `:754`, `write_tar` `:769`, `verify_tar` `:791`, `retention` `:813`, `cmd_config`/`_config` `:830`/`:891`; `scan_recordings` `:960`, `copy_verified` `:992`, `cmd_archive`/`_archive` `:1080`/`:1137`; `stale_warnings` `:1207`, `cmd_check` `:1232` |
| `scripts/backup.sh`, `scripts/archive_recordings.sh`, `scripts/backup_check.sh` | thin wrappers, as `upgrade_minor.sh` |
| `systemd/kronk-backup.{service,timer}` | the 10th at 01:30, `Persistent`, 3 h timeout, `IOSchedulingClass=idle`; the Operations block (install, run, pause, log, remove) |
| `systemd/kronk-recordings-archive.{service,timer}` | Mondays at 01:00 |
| `systemd/kronk-backup-check.{service,timer}` | daily at 09:30; `SuccessExitStatus=1` (a warning is the check's answer) |
| `orchestrator/backups_api.py` (83 lines) | `GET /api/backups`, read-only, never a 500; `orchestrator/main.py:32`, `:254` include it |
| `orchestrator/static/services.html:160`, `:347` | the "Backups" section and `loadBackups()` (absolute `/api/backups`, like the upgrade card) |
| `tests/test_m16_backups.py` (49 tests) | below, per criterion; an autouse fixture fails any network connect |
| `docs/features/backups.md` | the feature doc, with the install block and blog hooks |
| `docs/runbooks/restore.md` | the restore runbook, corrected by the test |
| `ROADMAP.md` | Backups moved from Next to Now (built, waiting for the install) |
| `Kronk evaluation findings and implementation plan.md` | M16 status header |
| `CLAUDE.md` | secrets paragraph: `KRONK_BACKUP_NAME`/`_PASSWORD` in `.env`, `/etc/kronk/nas-backup.cred`, the NAS config folder is a secret store |

## Criteria, one line each

1. Check first: the table above, measured before any script (commit `268e64a`).
2. Unit reconciliation: the table above; no repo copy changed.
3. One file `kronk-config-<date>.tar.gz` under `<dest>/config/`, mode 0600, every "in" item, MANIFEST (source, size, SHA-256, how read, unreadable, absent), repo state (commit, branch, pushed, commits not pushed, production tag), image pins, model list, host facts. Real tar below; `test_one_dated_tar_mode_0600_with_every_item_and_a_manifest`, `test_what_cannot_be_read_is_listed_in_the_manifest_and_the_notice`.
4. Temporary name, read back (SHA-256 of the file equals the one computed while writing; `tarfile` reads to the end with the member count), rename; stale temp removed by exact pattern in `config/` only. `test_written_to_a_temporary_name_then_verified_then_renamed`, `test_a_failed_verify_leaves_no_file_and_says_so`, `test_verify_reads_the_tar_to_the_end`, `test_an_interrupted_run_leaves_only_a_temporary_name_which_the_next_run_removes`; real: GNU `tar -tzf` reads all 318 entries of the first tar.
5. Retention: newest 12 of its own name pattern in `config/` only, never after a failed run. `test_retention_keeps_the_newest_n_of_its_own_files_only` (foreign files, a directory with the name, a tar outside `config/` untouched), `test_retention_never_runs_when_the_run_failed`.
6. Size guard 50 MB, failure names the five largest members. `test_over_the_ceiling_is_a_failure_naming_the_largest_members`. First real tar: 5,224,839 bytes.
7. `--dry-run` lists with sizes, writes and sends nothing; `--dest`. `test_dry_run_lists_with_sizes_and_writes_and_sends_nothing`; real dry run against the host (313 entries, 29.8 MB raw; dest and state stayed empty).
8. Archive path `<dest>/recordings/<device>/<pipeline>/<YYYY-MM>/<run>/<file>`, names and mtimes kept; second run copies and deletes nothing; same hash not copied and deleted locally; different hash never overwritten, both kept, reported. `test_old_recordings_move_keeping_names_and_times_young_ones_stay`, `test_a_second_run_with_nothing_new_copies_and_deletes_nothing`, `test_same_hash_on_the_destination_is_not_copied_again_and_is_deleted_locally`, `test_same_name_different_hash_is_never_overwritten_both_kept_and_reported`; real run below.
9. Faults, one test each: `test_fault_destination_not_mounted`, `test_fault_destination_full_mid_copy` (ENOSPC on the second write), `test_fault_the_copy_reads_back_different`, `test_fault_the_local_delete_fails_then_the_next_run_finishes_it`, killed between copy and delete (`test_same_hash_on_the_destination_…`, which is the state such a kill leaves) and killed mid-copy (`test_fault_killed_mid_copy_the_next_run_replaces_its_own_temporary_file`), `test_fault_a_recording_being_written_is_left_for_the_next_run`, `test_fault_symlinks_are_not_followed` (a symlinked device directory and a symlinked file pointing outside; the target is untouched), `test_fault_names_with_spaces_and_newlines`; and the container delete's argv (`test_the_delete_runs_by_literal_path_in_a_container_that_sees_only_the_recordings`).
10. `test_nothing_outside_the_recordings_and_nothing_on_the_destination_is_deleted`: a sibling tree beside the recordings, a config tar, a foreign folder, an earlier archive and a lookalike temp file on the destination, through a failing delete and a second run; every file outside the recordings and every file on the destination byte-identical afterwards.
11. Start and end notices (texts below), human units, a stub notifier: `test_start_and_end_notices_with_sizes_and_totals`, `test_a_notice_that_fails_does_not_fail_the_job`, `test_the_real_notifier_runs_the_stub_command_with_tag_title_body`. Stale check with a clock: `test_stale_check_with_a_clock` (day 1 quiet; day 10 archive stale, one notice; day 11 no repeat; day 36 config stale too, a notice; day 43 weekly repeat), `test_stale_check_never_ran_and_not_mounted`.
12. Three timers, reference copies, not installed: `test_timers`, `test_the_backup_misses_the_upgrade_window`, `test_services_run_the_repo_scripts_and_wrappers_call_the_one_script`, `test_the_operations_block_has_install_run_and_remove`; `systemd-analyze --user verify` clean on all six files.
13. `data/backups/status.json` (last run per job: time, result, sizes, counts) and `GET /api/backups` with the `/services` card; built on the dev path (`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`), `/dev/api/backups` answered 200 (`present: false`: dev's `/data` is `data/dev`), `/dev/services` carries the card, the dev container's `summarize` turned the real scratch status into its warning. Production `/api/backups` is 404 until promoted (listed for Drew). API tests: `test_api_*` (5).
14. `docs/runbooks/restore.md`: bare-machine rebuild in order, each step with its command and tar member; the human table; one-thing restores (HA `.storage`, live config directories, units).
15. Tested for real (below). `.env` restored byte-identical; HA up from the restored `.storage` with its pipelines, devices and entities; live config directories `valid` under the loaders.
16. `5506 passed, 3 skipped, 53 xfailed`. The M16 tests open no socket (`socket.create_connection`, `socket.socket.connect`, `connect_ex`, `getaddrinfo` patched to fail) and write only under pytest's `tmp_path`.
17. Production unchanged (state before and after below); HA and MA not restarted; nothing written to `/mnt` or the NAS (no contact; `/mnt` holds only `nas-music`); no recording deleted from `~/kronk-voice-recordings` (13 at the start, 15 at the end: HA wrote two at 10:02 and 10:03; the three I copied are still there).
18. `docs/features/backups.md`, `ROADMAP.md`, the plan's status, CLAUDE.md's secrets paragraph. No compose file was added to the repo (the restore-test compose file lived in scratch and is gone).

## The real tar

`scripts/backups.py config --dest <scratch>/dest`, notices to a stub, state
in scratch, run at nice 19, I/O idle.

- First run (09:55): `kronk-config-2026-11-19.tar.gz`, **5,224,839 bytes**,
  318 entries, 3 min 15 s wall, 113 MB peak memory; nearly all of it hashing
  the 29 GGUFs (about 230 GB at about 1 GB/s). Nothing unreadable.
- With the hash cache: 1.7 s.
- Final format (directories stored, run 4): 5,230,750 bytes, 471 entries
  (300 files of which 299 are checksummed in the MANIFEST plus the MANIFEST
  itself, 18 symlinks, 153 directories). `sha256sum -c` of the MANIFEST
  after unpacking: all members match.

Member list of the final-format tar (directories omitted; secrets by name
only):

| Member | Size (bytes) |
| --- | --- |
| repo/.env | secret (size not shown) |
| repo/.env.dev | secret (size not shown) |
| repo/.env.telemetry | secret (size not shown) |
| repo/searxng/settings.yml | secret (size not shown) |
| repo/secrets/yt-token.txt | secret (size not shown) |
| repo/secrets/mm/kronk-mm-update | secret (size not shown) |
| repo/secrets/mm/kronk-mm-update.pub | secret (size not shown) |
| repo/data/music_taste.yaml | 1,177 |
| repo/data/music_taste.db | 380,928 |
| repo/data/solar.db | 28,504,064 |
| repo/data/kronk_state.md | 293 |
| repo/data/mm_known_hosts | 142 |
| repo/data/yt-music-cookie | secret (size not shown) |
| live-config/prod/tuning.yaml | 4,832 |
| live-config/prod/music.yaml | 6,795 |
| live-config/prod/voice.yaml | 2,781 |
| live-config/prod/lead_in.yaml | 1,933 |
| live-config/prod/prompts/assistant.md | 801 |
| live-config/prod/prompts/home.md | 4,159 |
| live-config/prod/prompts/coordinator.md | 2,928 |
| live-config/prod/prompts/coding.md | 818 |
| live-config/prod/prompts/research.md | 1,745 |
| live-config/prod/prompts/talkie.md | 1,205 |
| live-config/prod/prompts/devops.md | 1,385 |
| live-config/dev/tuning.yaml | 4,832 |
| live-config/dev/music.yaml | 6,795 |
| live-config/dev/voice.yaml | 2,781 |
| live-config/dev/lead_in.yaml | 1,933 |
| live-config/dev/prompts/assistant.md | 801 |
| live-config/dev/prompts/home.md | 4,159 |
| live-config/dev/prompts/coordinator.md | 2,928 |
| live-config/dev/prompts/coding.md | 818 |
| live-config/dev/prompts/research.md | 1,745 |
| live-config/dev/prompts/talkie.md | 1,205 |
| live-config/dev/prompts/devops.md | 1,385 |
| systemd-user/kronk-music-canary.service | 802 |
| systemd-user/llama-devstral.service | 709 |
| systemd-user/kronk-upgrade-major-check.timer | 525 |
| systemd-user/kronk-upgrade-major-check.service | 1,038 |
| systemd-user/kronk-voice-capture-check.timer | 374 |
| systemd-user/wyoming-whisper.service | 716 |
| systemd-user/gpu-mem-export.timer | 407 |
| systemd-user/llama-devstral-q4.service | 1,130 |
| systemd-user/llama-talkie.service | 635 |
| systemd-user/kronk-stt-prompt.timer | 375 |
| systemd-user/kronk-stt-prompt.service | 1,363 |
| systemd-user/kronk-music-canary.timer | 277 |
| systemd-user/kronk-upgrade-notices.timer | 252 |
| systemd-user/kronk-memwatch.service | 1,109 |
| systemd-user/llama-gemma3-4b.service | 846 |
| systemd-user/kronk-upgrade-minor.service | 3,307 |
| systemd-user/kronk-bootnotify.service | 780 |
| systemd-user/kronk-voice-capture-check.service | 1,858 |
| systemd-user/kronk-upgrade-notices.service | 1,414 |
| systemd-user/llama-mistral-nemo.service | 612 |
| systemd-user/llama-gemma4-e4b.service | 1,058 |
| systemd-user/kronk-hottub-monitor.service | 375 |
| systemd-user/kronk-upgrade-minor.timer | 694 |
| systemd-user/gpu-mem-export.service | 732 |
| systemd-user/wyoming-whisper-dev.service | 2,942 |
| systemd-user/kronk-searxng-canary.timer | 303 |
| systemd-user/kronk-searxng-canary.service | 942 |
| systemd-user/llama-bonsai.service | 573 |
| systemd-user/kronk-perfwatch.service | 770 |
| systemd-user/kronk-upgrade-major-check.service.d/branch.conf | 56 |
| systemd-user/default.target.wants/wyoming-whisper.service | link -> /home/drew/.config/systemd/user/wyoming-whisper.service |
| systemd-user/default.target.wants/llama-devstral-q4.service | link -> /home/drew/.config/systemd/user/llama-devstral-q4.service |
| systemd-user/default.target.wants/kronk-memwatch.service | link -> /home/drew/.config/systemd/user/kronk-memwatch.service |
| systemd-user/default.target.wants/llama-gemma3-4b.service | link -> /home/drew/.config/systemd/user/llama-gemma3-4b.service |
| systemd-user/default.target.wants/kronk-bootnotify.service | link -> /home/drew/.config/systemd/user/kronk-bootnotify.service |
| systemd-user/default.target.wants/llama-gemma4-e4b.service | link -> /home/drew/.config/systemd/user/llama-gemma4-e4b.service |
| systemd-user/default.target.wants/kronk-hottub-monitor.service | link -> /home/drew/.config/systemd/user/kronk-hottub-monitor.service |
| systemd-user/default.target.wants/snap.prompting-client.daemon.service | link -> /etc/systemd/user/snap.prompting-client.daemon.service |
| systemd-user/default.target.wants/wyoming-whisper-dev.service | link -> /home/drew/.config/systemd/user/wyoming-whisper-dev.service |
| systemd-user/default.target.wants/snap.snapd-desktop-integration.snapd-desktop-integration.service | link -> /etc/systemd/user/snap.snapd-desktop-integration.snapd-desktop-integration.service |
| systemd-user/timers.target.wants/snap.firmware-updater.firmware-notifier.timer | link -> /etc/systemd/user/snap.firmware-updater.firmware-notifier.timer |
| systemd-user/timers.target.wants/kronk-upgrade-major-check.timer | link -> /home/drew/.config/systemd/user/kronk-upgrade-major-check.timer |
| systemd-user/timers.target.wants/gpu-mem-export.timer | link -> /home/drew/.config/systemd/user/gpu-mem-export.timer |
| systemd-user/timers.target.wants/kronk-stt-prompt.timer | link -> /home/drew/.config/systemd/user/kronk-stt-prompt.timer |
| systemd-user/timers.target.wants/kronk-music-canary.timer | link -> /home/drew/.config/systemd/user/kronk-music-canary.timer |
| systemd-user/timers.target.wants/kronk-upgrade-notices.timer | link -> /home/drew/.config/systemd/user/kronk-upgrade-notices.timer |
| systemd-user/timers.target.wants/kronk-upgrade-minor.timer | link -> /home/drew/.config/systemd/user/kronk-upgrade-minor.timer |
| systemd-user/timers.target.wants/kronk-searxng-canary.timer | link -> /home/drew/.config/systemd/user/kronk-searxng-canary.timer |
| systemd-user/kronk-upgrade-minor.service.d/branch.conf | 56 |
| config-kronk/upgrade.env | 30 |
| etc/default/grub | 1,620 |
| etc/docker/daemon.json | 75 |
| ha/config/.storage/core.area_registry | 2,062 |
| ha/config/.storage/http.auth | secret (size not shown) |
| ha/config/.storage/assist_pipeline.pipelines | 2,152 |
| ha/config/.storage/bluetooth.passive_update_processor | 101 |
| ha/config/.storage/core.config_entries | 13,411 |
| ha/config/.storage/timer | 265 |
| ha/config/.storage/http | 300 |
| ha/config/.storage/core.restore_state | 30,654 |
| ha/config/.storage/onboarding | secret (size not shown) |
| ha/config/.storage/esphome.01KSD67PYWJ8R3SBN033QNTFNY | 4,764 |
| ha/config/.storage/core.analytics | 220 |
| ha/config/.storage/esphome.01M0B8FEJNQGDC0DV32ENME37F | 10,508 |
| ha/config/.storage/homeassistant.exposed_entities | 586 |
| ha/config/.storage/core.uuid | secret (size not shown) |
| ha/config/.storage/esphome.01M1Q546CYBEJ1K28D1DDC7TBS | 4,764 |
| ha/config/.storage/core.entity_registry | 384,408 |
| ha/config/.storage/frontend.system_data | 200 |
| ha/config/.storage/auth_provider.homeassistant | secret (size not shown) |
| ha/config/.storage/lovelace.map | 154 |
| ha/config/.storage/bluetooth.remote_scanners | 313 |
| ha/config/.storage/esphome.encryption_keys | secret (size not shown) |
| ha/config/.storage/core.config | 399 |
| ha/config/.storage/lovelace_dashboards | 316 |
| ha/config/.storage/person | 334 |
| ha/config/.storage/auth | secret (size not shown) |
| ha/config/.storage/trace.saved_traces | 84,298 |
| ha/config/.storage/core.device_registry | 18,466 |
| ha/config/.storage/repairs.issue_registry | 109 |
| ha/config/blueprints/script/homeassistant/confirmable_notification.yaml | 2,593 |
| ha/config/blueprints/automation/music-assistant/mass_assist_blueprint_en.yaml | 9,073 |
| ha/config/blueprints/automation/kronk/mass_assist_kronk.yaml | 24,635 |
| ha/config/blueprints/automation/homeassistant/notify_leaving_zone.yaml | 1,668 |
| ha/config/blueprints/automation/homeassistant/motion_light.yaml | 1,518 |
| ha/config/.HA_VERSION | 8 |
| ha/config/automations.yaml | 933 |
| ha/config/configuration.yaml | 2,140 |
| ha/config/rest_command.yaml | 1,347 |
| ha/config/scenes.yaml | 0 |
| ha/config/scripts.yaml | 0 |
| ha/config/secrets.yaml | secret (size not shown) |
| ma/settings.json | secret (size not shown) |
| etc/kronk/nas-music.cred | secret (size not shown) |
| models/opt/models/ifm/PROVENANCE.sha256 | 398 |
| repo-state.txt | 1,164 |
| images.txt | 2,259 |
| models.tsv | 4,233 |
| host-facts.txt | 931 |
| MANIFEST | 87,408 |
| live-config/prod/.git/ (37 files) | 43,834 total |
| live-config/dev/.git/ (146 files) | 102,820 total |

`repo-state.txt` said: `commit: a0530491…`, `branch: refactor`, `not pushed:
5 commits ahead of origin/refactor`, `production tag: sprint-M17 (1
containers), sprint-M18 (6 containers)` (wyoming-piper still runs from the
M17 release).

## Notice texts

From the real runs (scratch paths as they appeared; on the timer the paths
are `/mnt/nas-backup/...`):

- `Kronk backup: config backup started` / `Writing the 2026-11-19 config backup to /tmp/kronk-m16-build/dest/config.`
- `Kronk backup: config backup OK` / `kronk-config-2026-11-19.4.tar.gz, 5.2 MB, 471 entries. Repo: 5 commits not pushed (they exist only on this machine; the tar does not hold the code). On the destination: config backups 20.9 MB (4 files), recordings archive 190.8 KB (2 files), together 21.1 MB.` (the word "entries" replaced "files" after this run; the test pins it)
- `Kronk backup: recordings archive started` / `Moving recordings older than 1 days from /tmp/kronk-m16-build/rec to /tmp/kronk-m16-build/dest/recordings.`
- `Kronk backup: recordings archive OK` / `Moved 0 recording(s) (0 B); 1 remain local (135.7 KB). Written this run: 0 B. On the destination: config backups 5.2 MB (1 file), recordings archive 190.8 KB (2 files), together 5.4 MB.`
- `Kronk backup: recordings archive FAILED` / `1 problem(s): x: not at <device>/<pipeline>/<run>/<file>. Moved 2 recording(s) (190.8 KB); 1 remain local (135.7 KB). Written this run: 190.8 KB. On the destination: …` (a stray file I had created in the scratch copy; reported and left alone)
- `Kronk backup: needs a look` / `destination /tmp/kronk-m16-build/dest is not a mounted filesystem (the NAS share is not mounted: `mount /tmp/kronk-m16-build/dest` or check /etc/fstab); nothing was written there.`

From the tests (the same code paths): `Kronk backup: config backup FAILED` /
`read back differs (test); the temporary file was removed, no backup was
written. On the destination: …`; `the tar is 305.5 KB, over the 100.0 KB
ceiling (KRONK_BACKUP_MAX_MB); largest members: repo/data/music_taste.yaml
300.0 KB, repo/data/music_taste.db 8.2 KB, repo/data/solar.db 8.2 KB,
MANIFEST 7.9 KB, repo-state.txt 408 B; the temporary file was removed, no
backup was written. On the destination: …`; `Kronk backup: config backup
DECLINED` / `Declined: another backup job holds the lock (); waited 2
minutes. Run scripts/backup.sh by hand later.` (the parentheses carry the
holder's line, `<job> pid=<n> since <time>`; the test's holder wrote none); the archive's `destination full: No space left on device`,
`the copy read back different; kept locally`, `local delete failed (…); the
copy on the destination is verified, the next run deletes it`, `Left for the
next run: 1 (…: changed while it was read; left for the next run)`.

## The archive, for real

1. `scripts/archive_recordings.sh --dry-run --dest <scratch>` against the
   real `~/kronk-voice-recordings`: `0 of 15 recordings are older than 7
   days`; with `KRONK_ARCHIVE_AGE_DAYS=1`, 3 of 15 would move. Nothing
   written, deleted or sent.
2. Three recordings (two from 2026-11-18, one from 2026-11-19) copied as
   root through `docker run --rm --pull=never --network none -v
   ~/kronk-voice-recordings:/src:ro -v <scratch>/rec:/dst alpine:3.20 cp -a
   …`: root-owned, times kept, the production layout.
3. Real run, age 1 day, deletes through the alpine container: the two older
   ones moved (SHA-256 on the destination equal to the copies' before),
   mtimes kept, their run directories removed, the 2026-11-19 one stayed.
4. Second run: copied nothing, deleted nothing.
5. After removing my stray file: OK.

Defect found here: intermediate destination directories were created 0775
(`mkdir(parents=True)` applies the mode to the last level only); now every
level is 0700 (`mkdir_private`), with a test.

## The restore test, step by step

From the run-4 tar, in `/tmp/kronk-m16-build/restore` (0700). Root-only
steps (GRUB, `/etc/kronk`, fstab, the `searxng/` chown), the clone, the
models, llama.cpp, Whisper and the MA image build were not run: no root and
no second machine. They are plain commands from the existing docs.

| Step | Done | Result |
| --- | --- | --- |
| 3. Unpack, check | `tar -xzf`; the MANIFEST `awk … \| sha256sum -c --quiet` | all members match (299) |
| 6. Secrets into a checkout | the step's `cp` lines into a scratch checkout; `cmp` of 11 files against the live ones | `.env`, `.env.dev`, `.env.telemetry`, `searxng/settings.yml`, `secrets/*` (3), 4 data files identical; `solar.db` and `music_taste.db` pass `integrity_check` |
| 7. Live config directories | `cp -a`, `diff -r` against the live directories, `git fsck`, `config_check.py` | identical including `.git`; fsck ok; `valid` for prod and dev |
| 10. Units | `diff -r` of `systemd-user/` and `config-kronk/` against the live directories | identical, symlinks and drop-ins included |
| 12. Home Assistant | `docker volume create kronk-restore-test_ha-config`; the step's `docker run … alpine:3.20 sh -c 'cp -a /src/. /config/ && chown -R 0:0 /config'`; compose project `kronk-restore-test` (file in scratch: image `ghcr.io/home-assistant/home-assistant:2026.5.4`, `pull_policy: never`, `network_mode: none`, the volume `external`) | HA answered on its own loopback after 5 s; `net=none`, no ports, no new host listener |
| 12, look inside | `docker exec -i -e HA_TOKEN … python3 -` (a websocket probe; the token passed by name from `.env`, never printed) | `auth_ok` with production's token (the restored `.storage/auth` signs it); pipelines `Home Assistant`, `kronk`, `Full local assistant`, `kronk-dev`, a preferred one set; 26 devices, 395 entities, 21 config entries, 127 states; the same counts as the tar's JSON files. No storage, JSON or decode error in the log. Every ERROR line is the missing network (`[Errno 19] No such device`): zeroconf, ssdp, cloud, rest_command, ESPHome (the three satellites), Music Assistant, Sonos, cast, mobile_app notify, met, radio_browser, ollama could not even set up, which is the proof it could not act on the house |
| Teardown | `docker compose -p kronk-restore-test -f <scratch>/compose.yml down`; `docker volume rm kronk-restore-test_ha-config` | no container, volume or network named `restore-test` left |

**What did not work as first written, and the fix:**

1. Step 7 ran `python3 scripts/config_check.py`: `orchestrator: the check
   could not run (exit 1): ModuleNotFoundError: No module named 'pydantic'`,
   `INVALID`. The loaders need `tests/.venv` (so does `deploy_tag.sh`,
   `KRONK_CHECK_PYTHON`). Runbook: step 5 builds the venv; step 7 runs the
   check with it.
2. Step 7's `diff -r` showed `Only in …/prod/.git/objects: info`, `pack`,
   `refs: tags`: the tar stored files and symlinks, not directories, so
   empty directories were lost (git did not mind; fsck ok). The tar now
   stores directory entries; the rerun was identical.
3. Step 7's `diff -r` showed `.git/index` differing: my own `git status`
   had refreshed it. Runbook says to run fsck and status before any diff.
4. Step 6's data line (`cp -p $R/repo/data/* data/ 2>/dev/null; cp -rp …
   upgrades …`) worked but hid errors; replaced by `cp -rp $R/repo/data/.
   data/` and rerun (identical).
5. Step 2 pointed at a section name that does not exist ("Installing");
   fixed to "Install (Drew)".

## Defects found while building, all fixed with a test

- The model hash cache was written before the state directory existed when
  `_config` ran without the lock (tests).
- The stale check re-sent its notice every day because the warning carries a
  day count; warnings are now compared with the numbers removed.
- The start notice named `kronk-config-<date>.tar.gz` when the run then
  wrote `.2`; it now names the date.
- "Commits not pushed" read `unknown`: `refactor` has no upstream
  configured. Falls back to `origin/<branch>`.
- My own test of a file being written appended on every write, so the read
  never reached EOF (stopped; its 439 MB temp tree removed by literal path).

## Departures from the criteria, and why

- **The archive keeps the run directory**:
  `<device>/<pipeline>/<YYYY-MM>/<run>/<file>`. HA names every recording
  `01_stt-<engine>.wav`; without the run directory every file of a month
  would collide.
- **gzip** (`.tar.gz`): allowed by criterion 3; 29.9 MB raw becomes 5.2 MB.
- **A same-day rerun writes `kronk-config-<date>.<n>.tar.gz`** instead of
  overwriting; retention counts them.
- **Members beyond the plan's table** (all small): `data/music_taste.db`,
  `data/kronk_state.md`, `data/mm_known_hosts`, `data/yt-music-cookie`,
  `.env.dev`, `/etc/docker/daemon.json`, the model PROVENANCE file,
  `data/upgrades/status.json`. Out: `.env.langfuse` (retired stack).
- **The archive deletes one kind of file on the destination**: its own
  `.kronk-archive.tmp`, after a failed copy or left by a killed one, as the
  config job removes its own stale temp (criterion 4). No finished file is
  ever deleted (criterion 10's test).
- **A conflict, a stray file or a symlink in the recordings fails the
  archive run** (the notice names it), so it stays in front of a person
  until resolved.
- **The start notice comes after the lock and before the destination
  check**: a not-mounted run sends start, then FAILED. A run that declines
  on the lock sends only DECLINED.
- **The stale check's notice**: when the warnings change, repeated weekly;
  the check exits 1 on a warning (`SuccessExitStatus=1` in its unit).
- **The scratch HA had no network at all** (`network_mode: none`) rather
  than a loopback port: stricter than criterion 15's wording, as the brief
  asked; it was inspected with `docker exec`.
- **Deletes use `alpine:3.20`** (on the host, never pulled,
  `KRONK_BACKUP_HELPER_IMAGE` to change), not `docker exec homeassistant`:
  the job never touches the production HA container.

## State before and after

Before (09:44) and after (10:20), `docker inspect` id and start time:

| Container | Before | After |
| --- | --- | --- |
| kronk-nginx-1 | `80588ee1b80c` 2026-11-18T17:51:31Z | same |
| kronk-orchestrator-1 | `6b3d7c97b2ec` 2026-11-18T17:51:25Z | same |
| kronk-tool_service-1 | `64fc7fe9c9c5` 2026-11-18T17:51:19Z | same |
| kronk-litellm-1 | `48da9ed1e321` 2026-11-18T17:51:19Z | same |
| kronk-searxng-1 | `5521e2425583` 2026-11-18T17:51:19Z | same |
| kronk-voice_tracer-1 | `68307c8ccdff` 2026-11-18T17:51:19Z | same |
| kronk-orchestrator-dev-1 | `f2e89dd25efc` 2026-11-18T16:53:33Z | `b68b3d0fb452` 2026-11-19T15:11:29Z (the criterion 13 rebuild) |
| kronk-tool_service-dev-1 | `87696935e83a` 2026-11-18T02:49:34Z | same (rebuilt image, container not recreated) |
| homeassistant | `98f56e2549d0` 2026-11-18T00:14:34Z | same |
| wyoming-piper | `5b97cc70b9ce` 2026-11-16T01:05:02Z | same |
| kronk-telemetry-grafana-1 | `6f78f54c5348` 2026-11-08T22:28:30Z | same |
| kronk-telemetry-tempo-1 | `a44fa0d3f460` 2026-11-08T22:30:23Z | same |
| music-assistant-server | `cf6b980f6630` 2026-11-04T13:45:22Z | same |
| bgutil-pot-provider | `0e8f74842089` 2026-11-04T13:45:22Z | same |
| ma-pr-test, open-webui | exited | same |

User units (`llama-*`, `wyoming-*`, `kronk-*`): the same list, all active.
Timers: the same set (`kronk-upgrade-*` untouched, the hot tub monitor
running). Listeners: the same, less `127.0.0.1:33049`, an ephemeral loopback
port that closed by itself (not ours; no listener was added at any point).
Docker volumes: the same list.

## What stays on disk

Nothing outside the repo. The scratch tree `/tmp/kronk-m16-build` (0700;
109 MB at its largest: the destination with four tars and two archived
recordings 21.1 MB, the unpacked restore 29.9 MB, a scratch checkout 57.8
MB, the root-owned recordings copy 136 KB, the state with the model hash
cache) was removed by literal path, the root-owned part through `docker run
… alpine:3.20 rm -rf /s/rec`. The scratch volume and project are gone. Not
created: `~/kronk-backup-scratch`, `data/backups/`. Pytest's own
`/tmp/pytest-of-drew` keeps its usual last three runs (fake data only).

## Install block (for the coordinator and Drew)

The full sequence with commentary is `docs/features/backups.md`, "Install
(Drew)". In short:

```
# 1. The share (root). Values from .env typed in; never echoed.
sudo install -d -m 0700 /etc/kronk
sudo sh -c 'umask 077; cat > /etc/kronk/nas-backup.cred' <<'EOF'
username=<KRONK_BACKUP_NAME from .env>
password=<KRONK_BACKUP_PASSWORD from .env>
EOF
sudo cp /etc/fstab /etc/fstab.bak-$(date +%F)
sudoedit /etc/fstab     # add:
# Synology backup share for scripts/backup.sh and scripts/archive_recordings.sh (sprint M16).
# nofail: a NAS outage never blocks boot; the jobs check the mount point and fail loudly.
//atlas.local/kronk-backup  /mnt/nas-backup  cifs  rw,ip=192.168.1.14,credentials=/etc/kronk/nas-backup.cred,iocharset=utf8,uid=1000,gid=1000,file_mode=0600,dir_mode=0700,_netdev,nofail,x-systemd.mount-timeout=30  0  0
sudo mkdir -p /mnt/nas-backup && sudo systemctl daemon-reload && sudo mount /mnt/nas-backup
findmnt /mnt/nas-backup

# 2. First runs by hand, from the repo
scripts/backup.sh --dry-run && scripts/backup.sh
scripts/archive_recordings.sh --dry-run && scripts/archive_recordings.sh
scripts/backup_check.sh

# 3. The timers
cp systemd/kronk-{backup,recordings-archive,backup-check}.{service,timer} ~/.config/systemd/user/
systemd-analyze --user verify ~/.config/systemd/user/kronk-{backup,recordings-archive,backup-check}.{service,timer}
systemctl --user daemon-reload
systemctl --user enable --now kronk-backup.timer kronk-recordings-archive.timer kronk-backup-check.timer
systemctl --user list-timers 'kronk-backup*' 'kronk-recordings-*'
```

Remove: the block in the feature doc. Promotion for `/services`: a tagged
deploy that includes `ea5c1a7` (`orchestrator/backups_api.py`,
`main.py`, `services.html`).

## Needs Drew's decision

1. **Encryption of the tar.** Today every secret (HA token, MA token, the
   NAS and music share passwords, the Grafana login, the MagicMirror SSH
   key, HA's auth store, MA's provider settings) sits in the clear on the
   NAS, protected by the account and the 0600/0700 mount modes. Options: a
   Synology encrypted shared folder (no change here), or `gpg`/`age`
   symmetric encryption in the job (a new tool, and a passphrase that must
   live somewhere other than the tar). Not built.
2. **The install** (block above), including confirming the share name
   `kronk-backup` by the first mount.
3. **Promotion** of the `/services` card (`GET /api/backups`).
4. **MA extras**: `playlists/` (28 KB, playlists made in MA) and `auth.db`
   (94 KB plus a 3 MB WAL; keeps `MA_TOKEN` valid across a restore). Both
   out under the plan's "settings.json alone"; small enough to add.
5. **`.env.langfuse`** and the April system units in `/etc/systemd/system`
   (`llama-bonsai`, `llama-coordinator`, `llama-devstral`, `ollama`): out as
   retired. Delete or keep is Drew's.
6. **Unpushed work**: the tar does not hold the repo; at backup time
   `refactor` was 5 commits ahead of `origin/refactor`. The end notice says
   so each month.
7. **Model sources**: `models.tsv` has path, size and SHA-256; only the K2
   files have a recorded source (`PROVENANCE.sha256`). A source URL per
   file would make step 8 of the runbook mechanical.
8. **Build recipes as scripts** (the plan's table row): still docs
   (`docs/CHANGE_2026-06-09_gemma_vulkan_switch.md`, `docs/VOICE_SETUP.md`).
9. **Recordings on the NAS are kept forever** (Drew's "keep everything"):
   about 1.4 MB a day at today's use, 40 to 50 MB a month.
