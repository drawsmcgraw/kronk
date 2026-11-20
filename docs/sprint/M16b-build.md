# M16b build report: Music Assistant's playlists and auth.db in the config tar; two chores

Branch `refactor`. Criteria: `docs/sprint/M16b-criteria.md`; M16 rulings 3
to 5 stand. Built 2026-11-20 by the build agent.

## Summary

- `scripts/backups.py` puts MA's `playlists/` and a consistent copy of
  `auth.db` in the config tar. `auth.db` is required. The archive half of the
  script is untouched.
- `setup-kronk-creds.sh` deleted. It was read first and holds no secret
  value: it refers to `$KRONK_BACKUP_NAME` and `$KRONK_BACKUP_PASSWORD` from
  `.env`, and the fstab line has no password in it.
- HA device "basement voice pe ma" is now in "Basement Theater". One
  `config/device_registry/update` call, read back afterwards, and the player
  lookup now finds the player. Nothing was played.
- Tests: before `5660 passed, 3 skipped, 53 xfailed`; after
  `5677 passed, 3 skipped, 53 xfailed, 6 warnings in 80.37s`.
- New tar, read from the real host into scratch:
  `kronk-config-2026-11-20.tar.gz`, 5,543,080 bytes (5.5 MB), 487 members,
  result `ok`, no gaps. The ceiling is 50 MB. This morning's tar on the NAS
  is 5,525,606 bytes with 475 members.

## How auth.db is read, and why the copy is consistent

**What is in the way.** MA opens every SQLite database through
`helpers/database.py:246-247` in its image: `PRAGMA locking_mode=exclusive`,
then `PRAGMA journal_mode=WAL`. With exclusive mode set before the first WAL
access, SQLite keeps the WAL index in MA's heap and creates no `-shm`; on
2026-11-20 `/data` held `auth.db` (94,208 bytes, mtime 2026-11-04 08:44, when
MA started) and `auth.db-wal` (3,221,872 bytes), and no `auth.db-shm`. MA
holds the exclusive lock, so no other connection can open the database. One
that tried would first create `auth.db-shm` in MA's `/data`. That rules out
the online backup API that `solar.db` and `music_taste.db` go through.
A plain copy of the main file is not the database either: the main file
alone holds 3 token rows, while main file plus WAL holds 4.

**What the job does** (`read_wal_pair`, `scripts/backups.py:764`). It reads the
pair the way SQLite's crash recovery would find it, with `docker exec
music-assistant-server cat` only, in this order: main file, WAL, main file,
WAL. It accepts the pair when:

1. both main-file reads are byte-identical. Only a checkpoint writes the
   main file of a WAL database, so no checkpoint wrote to it between the
   first read and the third. Every frame a checkpoint had already taken
   out of the WAL is therefore in the main file read;
2. the WAL's 32-byte header is the same in both WAL reads. The header
   carries the salts, and SQLite writes new salts whenever it restarts the
   WAL from the start. No restart happened, so the WAL read is one
   generation of frames, appended in order.

With both held, the bytes read are a state a crash could leave on disk.
SQLite's recovery then gives exactly the last transaction committed in
those bytes: it checks each frame's salts and running checksum, and it
drops a frame MA was still appending or a transaction with no commit
frame. `sqlite_from_pair` (`scripts/backups.py:821`) writes the pair into
a private `kronk-sqlite-<8>` directory (0700, in the temporary directory,
which is tmpfs here) and lets SQLite open it, which runs that recovery.
Then it copies the result to memory with the backup API, runs `PRAGMA
integrity_check` (anything but `ok` is an error), serializes it, and the
directory is removed. If a pair changed, the job waits 5 s and reads
again. After three reads it records a required gap, so the tar is
INCOMPLETE. The reason text never contains file content.

Nothing is opened or written in MA's `/data`, nothing is checkpointed and MA
is not stopped. Checked live: `ls -la /data` after the dry run, the scratch
run and the verification shows the same two files with the same sizes and
times (`auth.db` 94,208 Nov 4 08:44, `auth.db-wal` 3,221,872 Nov 20 03:30),
and still no `auth.db-shm`.

The copy is one file with the WAL format bytes (18 and 19 are 2), like the
existing `solar.db` copy. It opens on disk without a `-wal` beside it. It
does not open from memory (`deserialize`), which is why the tests open it
from a file.

## Criteria

| # | What changed | Test(s) |
| --- | --- | --- |
| 1 | `collect()` MA block, `scripts/backups.py:907-931`: `ls /data`, then `tar -cf - playlists` in the container (GNU tar 1.35 stores symlinks as symlinks), re-added under `ma/` by the existing `add_tar_stream`; `read_wal_pair` `:764`, `sqlite_from_pair` `:821`, constants `:110-117` | `test_m16b_c1_the_tar_holds_playlists_and_a_consistent_auth_db` (main file alone has 2 of 3 rows; the tar's copy has 3; order of reads; private copy gone), `..._a_playlist_symlink_is_stored_as_a_link_never_followed`, `..._a_pair_that_changed_while_read_is_read_again` (a checkpoint, then a WAL restart, third pair taken, 5 s apart), `..._no_wal_file_means_the_main_file_alone` |
| 2 | MANIFEST "how" strings (in the two readers); `required_sources` `:666-669`: `music-assistant-server:/data/settings.json` and `:/data/auth.db` replace the blanket `music-assistant-server:` (equivalent for settings.json, which was MA's only item); playlists gaps go to `absent` or `unreadable`, not required | `test_m16b_c2_the_manifest_says_how_each_was_read`, `..._playlists_absent_empty_or_unreadable_is_not_a_failure[absent,empty,unreadable]`, `..._a_pair_never_consistent_makes_the_tar_incomplete[checkpoints,restarts,garbage]`, `test_a_missing_required_item_makes_the_tar_incomplete[ma,ma-auth-missing,ma-auth-corrupt]` (MA down now counts 2 required gaps) |
| 3 | `auth.db` is a member with `secret=True`, mode 0600; reasons carry no content; `docs/features/backups.md:31-36` (the list of secrets) and the secrets paragraph after the Required table; `CLAUDE.md:232-236` | `test_m16b_c3_auth_db_reaches_no_log_notice_or_status_beyond_its_name_and_size` (logs, notices, status, dry run: name and size only), `..._a_failing_read_reports_no_content`, `..._c3_c4_the_docs_say_auth_db_is_a_secret_and_restored` |
| 4 | `docs/runbooks/restore.md:44` (the table row: MA_TOKEN stays valid, what was shown and what was not), step 13 `:309-330` (removes a stale `auth.db-wal`/`-shm`, copies `settings.json`, `auth.db`, `playlists/`) | `test_m16b_c3_c4_the_docs_say_auth_db_is_a_secret_and_restored` |
| 5 | Shown once in scratch (below) | by hand, recorded here |
| 6 | Tests fail on the old code: with `HEAD:scripts/backups.py` in place, 18 fail (the 15 new tests and the 3 changed parameter cases); with the new script, all pass | `./scripts/run_tests.sh` |
| 7 | HA registry, one call (below); incident doc status line and dated entry, `docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md:3`, `:361-376` | read back from the registry, the player lookup by hand (below) |
| 8 | Before and after state (below) | by hand, recorded here |

A killed run can leave its private copy behind. The next job removes it by
exact signature: `kronk-sqlite-<8>`, a plain directory owned by the
operator, older than a day, holding only `copy.db` and its `-wal`, `-shm`
or `-journal`. The existing bundle-directory cleanup was extended to do
this (`scripts/backups.py:1080`). Test:
`test_m16b_a_killed_runs_database_copy_is_removed_by_exact_signature_only`.

Test fixtures: `wal_pair()` and `token_ids()` (`tests/test_m16_backups.py:59`,
`:81`) build a tiny WAL-mode `auth_tokens` table per run, with made-up ids.
No real `auth.db` is in the repo. `FakeOps` now answers MA's `/data` (`:190`
and the attributes in `__init__`). The HA tar-retry test hands MA's
playlists call to the fake.

## Criterion 5: the restore, shown in scratch

No scratch MA was started. Every step ran without network:

- `backups.py config --no-notify --dest /tmp/kronk-m16b-build/dest` with
  `KRONK_BACKUP_STATE=/tmp/kronk-m16b-build/state` (seeded with a copy of
  `data/backups/model_sums.json`, read only): exit 0, `ok`, 5.5 MB, 487
  entries, no unreadable items. The dry run before it listed `ma/auth.db`
  94,208, `ma/playlists/Hotel Costes.m3u` 27,703 and `ma/settings.json` 63,311.
- MANIFEST lines (sizes, how, member, source):
  `0 docker exec music-assistant-server tar ma/playlists`;
  `27703 docker exec music-assistant-server tar ma/playlists/Hotel Costes.m3u`;
  `94208 docker exec cat (main file and WAL, each read twice, unchanged), WAL replayed by SQLite in a private copy, backup API, integrity_check ok ma/auth.db`.
- Unpacked into a 0700 directory with `umask 077`. The playlist's SHA-256 is
  the same as the live file's, checked with `sha256sum` inside MA.
- The restored `auth.db`, opened read-only: `integrity_check` `[('ok',)]`;
  `auth_tokens` 4, `users` 1, `settings` 2. MA_TOKEN was read from `.env`
  inside the check script and never printed. Its JWT id has a row with the
  same user, long-lived, `expires_at` 2027-10-06. Its user is present.
  `jwt_secret` is present, and MA_TOKEN's HS256 signature verifies with it.
  MA's own check (`authenticate_with_token`, auth.py:226-270 in the image)
  needs exactly these: the signature, the row by `jti`, the same user, not
  expired.
- Compared with the live database:
  - The live file cannot be opened while MA runs (the exclusive lock), so
    the token rows could not be counted there directly.
  - A second, independent read by the same method minutes later:
    `auth_tokens` 4, `users` 1, `settings` 2, and the same set of token ids.
  - The main file alone (a plain `docker exec cat`) has `auth_tokens` 3.
    The 4th row is only in the WAL.
  - Not done: a count through MA's own API (`auth/tokens`), which would need
    a MA_TOKEN-authenticated call to production MA.
- Runbook step 13, run for real against a throwaway volume
  `kronk-m16b-ma-test` (alpine:3.20, `--network none`, no MA). Random bytes
  were put in as a stale `auth.db-wal`, plus an empty `auth.db-shm`, and
  then the step's command ran. Exit 0. The volume then held `auth.db`,
  `settings.json` and `playlists/Hotel Costes.m3u`, owned by 0:0, with the
  stale `-wal` and `-shm` gone. The `auth.db` read back from the volume is
  byte-identical to the tar's, `integrity_check` ok, 4 token rows. The
  volume was removed (`docker volume rm kronk-m16b-ma-test`).
- Not shown: MA starting on the restored files and accepting MA_TOKEN. The
  runbook says so ("not against a running MA").

## Criterion 7: the area

The script was run from scratch (`/tmp/kronk-m16b-build/ha_area.py`,
removed with scratch). It talks to `ws://127.0.0.1:8123/api/websocket` and
reads `HA_TOKEN` from `.env` into a variable. The script first lists the
devices and areas, then checks: both ids exist, the names match the
criteria, the satellite has an area, and the MA device has none. If any
check had failed, it would have stopped before writing. Then it made one
`config/device_registry/update`, then the same reads again.

| Device | Before (area id, name) | After |
| --- | --- | --- |
| `1b6bd3b03f90e52f08dd98dca41eac82` "basement voice pe" | `basement_theater`, Basement Theater | `basement_theater`, Basement Theater (not modified; `modified_at` unchanged) |
| `b91145f1a0afef63089911ffb5ba8b6b` "basement voice pe ma" | none, none | `basement_theater`, Basement Theater |

- Read back: through the WebSocket registry list, and later in
  `/config/.storage/core.device_registry` once HA had flushed it (read
  with `docker exec homeassistant python3`, read-only). Both devices are
  in `basement_theater`.
- Entities: the MA device's two entities keep their entity ids
  (`media_player.home_assistant_voice_0ab117`,
  `button.home_assistant_voice_0ab117_favorite_current_song`) and have no
  area of their own, so they follow the device. No rename was made.
- Player lookup, read-only:
  - Tool_service's own `_PLAYERS_TEMPLATE` was rendered by HA's `POST
    /api/template` (a render, no state change), and the repo's
    `parse_player_payload` and `resolve_players("Basement Theater", None,
    players, "")` ran offline on the result. The import used a scratch
    taste DB, the repo's `config/` and telemetry off.
  - Before: `HTTPException 400: I don't know a speaker or room called
    'Basement Theater'`, the incident's answer. Rooms with a player:
    Kitchen and Office.
  - After: `['media_player.home_assistant_voice_0ab117'] | the Basement
    Theater speaker`. Rooms with a player: Basement Theater, Kitchen and
    Office.
  - Nothing was sent to tool_service or MA, and nothing was played.
- Side effect, as the incident doc said beforehand: Basement Theater now
  holds two media players (the ESPHome one and the MA one), as Kitchen and
  Office already do.
- No HA restart, no reload, no pipeline run.

**Undo:** the same call with `area_id` null, on the same WebSocket after
`auth`:

```
{"id": 1, "type": "config/device_registry/update", "device_id": "b91145f1a0afef63089911ffb5ba8b6b", "area_id": null}
```

Or in HA's UI: Settings, Devices, "basement voice pe ma", Area, clear it.

## Criterion 8: production before and after

Containers: name, id and start time, before (06:46) and after (06:52). The
lists were the same (`diff` empty):

```
bgutil-pot-provider 0e8f74842089 2026-11-04T13:45:22.436374106Z running
homeassistant 98f56e2549d0 2026-11-18T00:14:34.556202059Z running
kronk-litellm-1 6670c428c111 2026-11-19T19:30:38.938408036Z running
kronk-nginx-1 ffdfb203c050 2026-11-19T19:30:50.488428036Z running
kronk-orchestrator-1 7484442cc70d 2026-11-19T19:30:44.558140759Z running
kronk-orchestrator-dev-1 84da57f14fcb 2026-11-19T16:07:52.908470571Z running
kronk-searxng-1 a8a051d1e78f 2026-11-19T19:30:38.937310078Z running
kronk-telemetry-grafana-1 6f78f54c5348 2026-11-08T22:28:30.647456834Z running
kronk-telemetry-tempo-1 a44fa0d3f460 2026-11-08T22:30:23.087882723Z running
kronk-tool_service-1 0f82a6e14d18 2026-11-19T19:30:38.93802779Z running
kronk-tool_service-dev-1 87696935e83a 2026-11-18T02:49:34.183319208Z running
kronk-voice_tracer-1 e48aab8a859e 2026-11-19T19:30:38.937674644Z running
ma-pr-test cac357f8a81b 2026-10-16T21:44:59.951948859Z exited
music-assistant-server cf6b980f6630 2026-11-04T13:45:22.470812618Z running
open-webui 99dcba2b49ed 2026-03-31T23:59:27.254265466Z exited
wyoming-piper 5b97cc70b9ce 2026-11-16T01:05:02.386451415Z running
```

- Units (`llama-*`, `wyoming-*`, `kronk-*`): main PID and the time each
  became active, identical before and after. For example
  `kronk-hottub-monitor` 1365776, `llama-gemma4-e4b` 2828,
  `wyoming-whisper` 2809, `kronk-memwatch` 2824. The timer set is
  identical too, `kronk-backup*`, `kronk-recordings-archive` and
  `kronk-upgrade-*` included. No timer fired a backup service during the
  build. The next is `kronk-backup-check` at 09:30.
- HA and MA were not restarted (start times above). Nothing was built or
  deployed, and the dev pair was not rebuilt (no criterion needed it). No
  helper container was left (`label=kronk.backup.helper=1`: 0).
- NAS: nothing written. `/mnt/nas-backup/config/` holds only the
  coordinator's `kronk-config-2026-11-20.tar.gz`, 5,525,606 bytes,
  06:24:36. Every run went to `--dest /tmp/kronk-m16b-build/dest`.
- Notices: every run carried `--no-notify`; nothing was sent to a phone.

## Not done, and why

- **The corpus row in the incident doc.** CLAUDE.md wants a music
  incident's utterance in `tests/voice_corpus.jsonl` with the fix. This
  item's limits forbid household transcript text in fixtures beyond the
  sentences already in the corpus, and this sentence is not there. The row
  is still in the incident doc, ready. Coordinator's call.
- **Fix 1's voice checks.** `scripts/voice_replay.py --check-only --tag
  music` and `--answers` on the basement rows were not run, and there was
  no live "Okay Nabu" test. That needs Drew and sound. Left for the e2e
  and Drew. The incident doc says so.
- **MA started on the restored files.** Not required by criterion 5, and
  not done.
- **A finding in passing, not fixed (out of scope, and this item may not
  remove other /tmp directories):** `tests/fixtures/m9/replies.py:143`
  makes a `kronk-m9-bytes-*` directory (a `metrics.db` and a `sessions.db`)
  that no run removes. `/tmp` held 508 of them today, and each of this
  build's test runs added one.

## Scratch

`/tmp/kronk-m16b-build` (0700). Its contents, apparent sizes in bytes:

| Path | Size | Notes |
| --- | --- | --- |
| `dest/` | 5,543,080 | the scratch tar (every secret, `auth.db` included) |
| `unpack/` | 30,568,700 | the tar unpacked (secrets); removed early, before this report; 21 symlinks inside, all to unit files under `~/.config/systemd/user` and `/etc/systemd/user` |
| `back.db` (+ `-shm` 32,768, `-wal` 0) | 94,208 | `auth.db` read back from the throwaway volume (secret); removed early |
| `state/` | 6,411 | scratch status file, lock, model hash cache copy |
| `backups.py.new` | 110,042 | the new script, kept while the old one was checked out for the fail-on-old run |
| `verify.py`, `ha_area.py`, `lookup.py` | 3,473, 3,002, 1,484 | the check scripts; none holds a token |
| `before-*.txt`, `after-*.txt` | about 14 KB | container, unit and timer lists |
| `taste/` | 0 | the scratch taste DB path for the tool_service import (nothing written) |

Removed at the end with `rm -rf /tmp/kronk-m16b-build`; checked gone, with
no `kronk-sqlite-*` directory, no `kronk-m16b-ma-test` volume and no helper
container left. No other `/tmp` directory was touched.

## Commits

- `7d6dc5a` M16b: the script, the tests, the feature doc, the runbook,
  CLAUDE.md, the incident doc, this report in progress.
- This report, final (the last commit). A test run after `7d6dc5a`, with
  everything committed: `5677 passed, 3 skipped, 53 xfailed, 6 warnings in
  81.09s`. Tree clean; no push, no tag.
