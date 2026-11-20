# M16b end-to-end test report: Music Assistant's playlists and auth.db in the config tar; two chores

Verdict: PASSES

Branch `refactor`, tested at `6e4da25` (build `7d6dc5a`), 2026-11-20, by the
e2e agent (did not build it). Criteria: `docs/sprint/M16b-criteria.md`.
Builder's claims: `docs/sprint/M16b-build.md`.

No blocker. Three should-fix findings and nine notes. The `auth.db` copy
held up: 47 scripted interleavings and about 13,000 copies taken while a
second process wrote, every accepted copy equal to a committed state, the
rest refused. The one way I found to get a torn copy needs a read that
comes back short with exit status 0, which I could build only in a fake.

## Findings

### 1. should-fix: a WAL read that comes back short with rc 0 is accepted, and can give a torn database

`read_wal_pair` proves that nothing moved between reads. It does not prove
that each read is whole: its only evidence is `docker exec cat` exiting 0.
The argument ("the main file holds every frame a checkpoint took out of the
WAL read") needs the WAL read to hold every frame that was in the file. If
the first WAL read is a prefix of the file (header intact), while the main
file has been checkpointed past that prefix, SQLite replays the prefix over
newer pages.

Reproduction (fake reader, my own database): 12 transactions, a full
checkpoint (no new write, so the WAL keeps its salts), then the first WAL
read cut to its first 40 % with rc 0 and the second WAL read whole. Both
main reads identical, both headers identical: the pair is accepted on the
first try. The result passes `integrity_check` and is not a committed
state (the schema page is from the middle of the table creation: `no such
table: g4`, while the head row reads 0). With the builder's own fixture, a
WAL cut to header plus 100 bytes gives exit 0 and a tar whose `auth.db`
lacks the WAL's row, with no warning.

I could not make the real transport do this: `docker exec cat` killed or
failing gives a non-zero status, and 585 copies through real `docker exec
cat` against a scratch container were all whole. So this is not a blocker.
It is the one place the proof rests on something unchecked. Cheap guards,
either would do: ask the container for the file sizes in the same pass
(`stat -c %s` before each `cat`) and refuse a read shorter than the size;
or refuse when the second WAL read does not hold every valid frame of the
first.

### 2. should-fix: a hard-linked playlist (and any member that is not a file, directory or symlink) is dropped without a word

`add_tar_stream` (`scripts/backups.py:845`, M16 code, now also used for
`playlists/`) ends in `else: continue`. GNU tar stores the second name of a
hard-linked file as a link member, so that name is not in the tar, not in
the MANIFEST and not in the notice; the run is `ok`.

Reproduction: a `playlists/` tree with `a.m3u` and `sub/hard.m3u` hard
linked to it, plus a FIFO, read by the host's GNU tar 1.35 and fed through
the fake. Exit 0; the tar has `playlists/a.m3u` and the directory
`playlists/sub`, no `sub/hard.m3u`, no UNREADABLE line. Criterion 1 says
"every file, by name". On the real data (one playlist, no links) the
criterion is met; a skipped member should at least be listed as unreadable.

In the same run, symlinks out of the directory (relative to
`../settings.json`, absolute to a file, to a directory, dangling) were all
stored as links with size 0, never followed, and a file name with a
newline came through.

### 3. should-fix: five mutants of the new code survive the new tests

Mutants were applied to a copy of the script in scratch, with a copy of
`tests/test_m16_backups.py` (the working tree was never touched). The old
script fails 18 tests, as the builder says. Of 18 mutants, 13 are killed.
Survivors:

- `integrity_check` result ignored (`if check != ["ok"]` made false): no
  test hands `sqlite_from_pair` a pair that opens but fails the check.
- the cleanup takes directories younger than a day (the age test dropped
  for `kronk-sqlite-*`): that would delete a running job's copy.
- the cleanup's name pattern loosened to `kronk-sqlite-.*`.
- `secret=False` on the `auth.db` member: the `secret` field is read
  nowhere in the script, so it guards nothing. Not a leak (see "Secrets").
- the second WAL read used instead of the first: by my analysis this is
  also sound, so the survivor is harmless; it does mean no test pins which
  read is used.

Killed: no main-file comparison, no header comparison, WAL ignored,
`auth.db` not required, playlists required, no wait, one try only, private
copy never removed, cleanup ignoring foreign files, reason carrying
content, member mode 0644, three reads instead of four, playlists never
read.

### 4. note: a job stopped by SIGTERM or SIGKILL leaves the private copy

`on_sigterm` re-raises the signal with the default action, so
`TemporaryDirectory` never cleans up. Reproduction: a process held inside
`sqlite_from_pair`, then signalled. SIGTERM with the job's handler, SIGTERM
without it, and SIGKILL each leave `kronk-sqlite-<8>/copy.db` and
`copy.db-wal`. SIGINT cleans up. The directory is 0700 (the files inside
are 0664), in tmpfs, and holds a full copy of `auth.db`. The next config or
archive job removes it only once it is a day old, so with the weekly
archive it can sit for about a week, or until a reboot. The window is a few
milliseconds per run and the feature doc says a killed run leaves a copy of
a secret. Removing it in the SIGTERM handler would close most of it.

On every failing path that is not a kill (garbage main file, garbage both,
a main file cut mid-page) the directory was gone when the call returned.

### 5. note: the leftover cleanup removes nothing that is not its own

Seventeen look-alikes in a scratch tmp, all three days old unless said.
Removed: the two real shapes (`copy.db` + `-wal`; all four names), an
empty directory with a matching name, and a directory whose `copy.db` is a
hard link to a file elsewhere (the link went, the file stayed). Left alone:
younger than a day; 7 and 9 character suffixes; upper case; a prefixed
name; a foreign file inside; another file name; a directory named
`copy.db-wal` inside; a symlink named `copy.db`; a symlink with a matching
name; a plain file with a matching name; a bundle name holding `copy.db`;
a sqlite name holding `unpushed.bundle`. The empty directory is the only
one a strict reading would not call "its own"; removing it loses nothing.

### 6. note: the method's limits under load, and what is coming on the real database

With a writer checkpointing every few tens of milliseconds, every read is
refused (three tries, 10 s) and the tar is INCOMPLETE. That is the right
direction. The real `auth.db` sees a write a few times a day. Its WAL
holds 783 frames; SQLite's default auto-checkpoint is 1000 pages, so the
first checkpoint and WAL restart since MA started on 2026-11-04 is some
weeks away. Both were covered in the scripted runs (checkpoint and restart
at every gap between the four reads and inside a read).

### 7. note: runbook step 13 and the `MA_TOKEN` row

The table row is true as far as it was shown and says what was not shown
("not against a running MA"). Step 13's sentence "`MA_TOKEN` in the
restored `.env` works again" is flatter than the evidence; the check it
names right after is the real proof and nobody has run it. I did not start
a scratch MA either (see "Not tested").

Two things a reader of step 13 should know, neither wrong: restored files
are mode 0600 and the playlists directory 0700, root-owned (the live ones
are 0644 and 0755; MA runs as root, so it reads them); and over a used
volume the playlists are merged, a playlist already in the volume stays.

### 8. note: a second device was modified in HA's registry today, not by the build

`satellite 1` (`ac6dd1f1…`, area `office`) has `modified_at`
2026-11-20T11:32:32Z, 06:32 EST. That is before the builder's window
(06:46 to 06:52) and matches the entity registry's file time (06:32:49)
and MA's `sendspin/` directory time (06:32:39): the Satellite1
reconnecting. `b91145f1…` is the only other device modified today
(11:49:34Z).

### 9. note: MA wrote in its own `/data` during my session, before I first touched it

Between my first listing (06:58) and the listing before my dry run
(07:23): `auth.db-wal` grew by one frame at 07:02:17 (the
`kronk-music-canary` timer fired at 07:02:15), and `settings.json`,
`settings.json.backup` and the log were rewritten at 07:09 to 07:10. My
first `docker exec` into MA after the 06:58 listing was the dry run at
07:23. The listings before and after my real run are identical, and there
is no `auth.db-shm`.

### 10. note: the builder's two open items stand

The incident's utterance is not in `tests/voice_corpus.jsonl` (CLAUDE.md
asks for it with the fix; this item's limits forbid the text). The voice
replay checks and a live "Okay Nabu" test for fix 1 were not run by the
builder and not by me (no sound, no pipeline run). Coordinator's call.

### 11. note: what I did that I should not have, or that cost something

- I ran `docker compose -f docker-compose.ma.yml config --volumes` once,
  to read the volume's name. It parses the file and asks the daemon for
  nothing, but the limits say my only compose project is
  `kronk-restore-test`. No other compose command was run.
- A bug in my stress writer grew two scratch databases to about 8 GB each
  in `/tmp` (tmpfs, so memory) for about five minutes. Killed, removed by
  literal path, `/tmp` back to its size.
- The test suite leaves `kronk-m9-bytes-*` directories in `/tmp` (the
  builder's finding): 510 now, one of them newer than my start. Not
  removed, as the limits say.
- No "Status: in progress" version of this report was committed; this is
  its only commit.

### 12. note: `MANIFEST` wording

The `auth.db` line reads "docker exec cat (main file and WAL, each read
twice, unchanged)". "Unchanged" is stronger than what is checked for the
WAL (its first 32 bytes). Frames appended between the two WAL reads are
normal and the copy is still sound; the word could be "not restarted".

## 1. The consistency of the auth.db copy

**The argument, checked.** Reads are main, WAL, main, WAL; the first WAL
read is the one replayed. Sound when every read is whole (finding 1):

- A checkpoint only ever copies frames that are already in the WAL file, so
  a main file read with some pages checkpointed and others not, plus a WAL
  read taken after it in the same generation, is a state a crash could
  leave. The WAL has the newest version of every page it touched.
- The dangerous shape is a WAL prefix replayed over a main file
  checkpointed past it. Frames up to the checkpoint stay in the file until
  the WAL restarts or is truncated; either changes the header (salt 1 is
  incremented, salt 2 is random, the checkpoint counter moves) or empties
  the file, and both happen before the second WAL read, which sees them.
- A restart needs a complete checkpoint first. So an unchanged main file
  across a restart is the fully checkpointed state, and the new generation
  on top of it is right; stale frames after the new ones fail the salt
  check and stop the replay.
- A frame being appended, or a rolled-back transaction's frames being
  overwritten, fail the running checksum and stop the replay at the last
  commit before them.
- Using the first WAL read is sound; frames appended after it are simply
  not in the copy. Using the second would be sound too.
- A brand-new exclusive-mode WAL database already has a 4096-byte main
  file before its first commit, so "zero-byte main file, WAL discarded by
  SQLite" does not arise.

**Scripted runs** (`attack.py`, in scratch; the real `read_wal_pair` with
a reader that runs the writer's next move before read k or after chunk j
of read k). The oracle: transaction n sets a head row to n and every row
of group n mod 7 to n; groups sit on separate pages, so a copy is a
committed state only if every group holds exactly the value its head
implies. 47 scenarios:

| Attack | Result |
| --- | --- |
| checkpoint between main 1 and WAL 1; between WAL 1 and main 2; during main 1; during main 2 | refused, taken on the second try, committed |
| checkpoint between main 2 and WAL 2 | accepted first try, committed (WAL 1 predates it) |
| checkpoint on every try | REFUSED, "the main file changed while it was read (a checkpoint) (3 reads)" |
| checkpoint then restart at each of the three gaps, during main 1, during WAL 1 | refused, then committed |
| restart with the main file unchanged, between main 1 and WAL 1 | accepted first try, committed (new generation over the checkpointed main) |
| restart with the main file unchanged, between WAL 1 and main 2, between main 2 and WAL 2, during WAL 1 (after chunk 0 and after chunk 1) | refused ("restarted"), then committed |
| restart during WAL 2 | accepted, committed at the state of WAL 1 |
| WAL of new header, new frames, old-generation tail | committed, the new frames only |
| checkpoint(TRUNCATE) then a write, between and during reads; TRUNCATE alone; empty WAL file | refused then committed; empty WAL gives the main file's state |
| frames appended between WAL 1 and main 2, between main 2 and WAL 2 | accepted, the state at WAL 1 |
| frames appended during WAL 1, during main 1 | accepted, committed |
| WAL cut mid last frame (one or both reads); cut inside its last transaction at a frame boundary | committed at the last whole commit (same generation, main not past it) |
| WAL 1 cut to 10 bytes | refused (header differs), then committed |
| uncommitted spilled frames; rolled back and overwritten during WAL 1; between WAL 1 and main 2 | committed, the open transaction never appears |
| synthetic mix: old header with new body; new header with old body; the whole old generation in both reads with the main file past it | committed or refused; never torn |
| main file grows: checkpoint between and during reads; growth only in the WAL | refused then committed; committed |
| main file reads both cut to 60 % | sqlite error, read again, committed |
| **WAL 1 cut to 40 %, rc 0, main checkpointed to the end** | **TORN, accepted (finding 1)** |

**Two-process runs** (`stress.py` and `writer.py`): a second process
commits on an exclusive-mode WAL database with auto-checkpoint 20 to 1000
pages, plus TRUNCATE and RESTART checkpoints, growing transactions and
spilled transactions rolled back; the reader loops the real
`read_wal_pair`.

| Reader | Writer pace | Copies accepted | Refused (checkpoint / restart) | Not a committed state |
| --- | --- | --- | --- | --- |
| file reads, 64 KB chunks, 45 s | about 80 commits/s | 2,779 | 34 / 31 | 0 |
| same, seed 2 | about 80/s | 1,892 | 137 / 73 | 0 |
| same, seed 3 | about 80/s | 2,630 | 46 / 29 | 0 |
| same, seed 4 | about 340/s | 749 | 490 / 65 | 0 |
| same, seed 5 | about 17/s | 4,341 | 0 / 0 | 0 |
| real `docker exec cat`, scratch `alpine:3.20`, `--network none`, 75 s | about 3/s | 585 | 0 / 0 | 0 |

Earlier runs with a writer far faster than the reader refused every read;
they showed nothing else and are not counted.

**The private copy.** `kronk-sqlite-<8>` in the settings' temporary
directory, mode 0700, `copy.db` and `copy.db-wal` inside (0664). Gone
after a good call and after each failing one. Kill: finding 4. Cleanup:
finding 5.

## 2. The real run, into scratch

`KRONK_BACKUP_STATE=/tmp/kronk-m16b-e2e/state TMPDIR=/tmp/kronk-m16b-e2e/tmp
scripts/backup.sh --no-notify --dest /tmp/kronk-m16b-e2e/dest`, after the
same with `--dry-run`. The state directory held a copy of
`data/backups/model_sums.json`; without the variable a run writes its
status under `./data`.

- Exit 0, `ok`, `kronk-config-2026-11-20.tar.gz`, 5,563,043 bytes, mode
  0600, 487 entries, no gaps. Ceiling 50 MB.
- MANIFEST: `ma/settings.json` 63,311 by `docker exec music-assistant-server
  cat`; `ma/playlists` a directory and `ma/playlists/Hotel Costes.m3u`
  27,703 by `docker exec music-assistant-server tar`; `ma/auth.db` 94,208
  by "docker exec cat (main file and WAL, each read twice, unchanged), WAL
  replayed by SQLite in a private copy, backup API, integrity_check ok".
- `auth.db` from the tar, opened read-only: `integrity_check` ok. Rows:
  `auth_tokens` 4, `users` 1, `settings` 2, `user_auth_providers` 1,
  `join_codes` 0, `roles` 0.
- A second read with my own code (main, WAL, main, WAL by `docker exec
  cat`, recovered in scratch): same counts, same set of token ids, and the
  same digest over every row of every table. The main file alone has 3
  token rows.
- `MA_TOKEN` (read from `.env` inside the script, never printed): its JWT
  id has a row, long-lived, expires 2027-10-06, same user as the token's
  claim, the user row is there, the stored hash is the token's SHA-256,
  and the token's HS256 signature verifies with the `jwt_secret` in the
  copy.
- `playlists/`: names, types and SHA-256 equal to the container's (2
  entries: the directory and one file).
- MA's `/data`: `ls -la` with full timestamps identical before and after
  the run; no `auth.db-shm`. MA's container id and start time unchanged.
- NAS: `/mnt/nas-backup/config/` listing identical before and after (one
  file, 5,525,606 bytes, 06:24:36). No `kronk-sqlite-*` or `kronk-bundle-*`
  left in `/tmp` or in my `TMPDIR`; no helper container left.

## 3. Secrets

Every text value of 8 characters or more in the restored `auth.db`, the
user fields, and `MA_TOKEN` with its signature and id (37 strings; dates
left out) searched for in the run log, the dry-run log and the status
file: no hit on a secret. The three matches are `int` (a type name),
`settings` (a table name inside `settings.json`'s path) and the user name,
which is the home directory's name in every path. The run log does not
name `auth.db`; the dry run names it once with its size. The status file
carries the tar's name, size, hash, counts and gaps, nothing of a member.
`GET /api/backups` (`orchestrator/backups_api.py`, read; not called on
production) passes the status file's `last` entry through and adds
warnings built from `detail` and `unreadable`; a failed `auth.db` read
puts only the reason there. Reasons seen in the failure runs: `docker exec
cat failed (1): …`, `missing: no such file`, `the main file changed…`,
`sqlite: …`. With the stub notifier, the notice bodies carry the same
reasons and no content. The feature doc's list of secrets, its paragraph
after the Required table and CLAUDE.md's secrets paragraph name `auth.db`
and say what it holds.

## 4. Failure paths (fake only; the real MA was not stopped)

My own tests, in scratch, on the builder's `house` and `FakeOps`:

- MA not running (every `docker exec` answers "container … is not
  running"): exit 4, INCOMPLETE, two required gaps (`settings.json`,
  `auth.db` "(3 reads)"), `playlists` reported and not required, 10 s
  waited.
- `auth.db` permission denied: exit 4, "docker exec cat failed (1): cat:
  /data/auth.db: Permission denied (3 reads)" in the status and notice.
- `auth.db` missing with its WAL present: exit 4, "missing: no such file".
- WAL read failing with an I/O error: exit 4.
- Never consistent, playlists absent or empty: the builder's tests, which
  pass and whose mutants die.
- Links in `playlists/`: finding 2.

## 5. Runbook step 13

As written, with the volume name replaced by `kronk-m16b-e2e-ma-test`
(the real name would write into the live volume), `alpine:3.20`,
`--network none`, no MA started. `$R/ma` came from the scratch tar,
unpacked with `umask 077` (the `ma/` subtree only, so no symlink to unit
files landed in scratch).

- Empty volume: exit 0; `auth.db`, `settings.json`,
  `playlists/Hotel Costes.m3u`, owned 0:0, SHA-256 of all three equal to
  the unpacked files.
- Used volume (a valid stale `auth.db-wal` and `-shm` from another
  database, and an extra playlist put in first): exit 0; the `-wal` and
  `-shm` gone, `auth.db` hash equal to the tar's, the extra playlist still
  there. `auth.db` copied back out: `integrity_check` ok, 4 token rows, 1
  user.
- Volume removed by name; the volume list equals the list before.

## 6. The area (read only; no write to HA, nothing played)

Read from `/config/.storage` through `docker exec homeassistant python3`:

| Device | Area id, name | `modified_at` |
| --- | --- | --- |
| `1b6bd3b03f90e52f08dd98dca41eac82` "basement voice pe" | `basement_theater`, Basement Theater | 2026-09-04T21:36:07Z (unchanged) |
| `b91145f1a0afef63089911ffb5ba8b6b` "basement voice pe ma" | `basement_theater`, Basement Theater | 2026-11-20T11:49:34Z |

Its two entities keep their ids and have no area of their own.

Player lookup: HA rendered tool_service's own `_PLAYERS_TEMPLATE` (`POST
/api/template`, a render; the request production makes on every play),
with the satellite as origin. tool_service's `parse_player_payload` and
`resolve_players` were lifted from the source by name and run on the
answer (no import of `tool_service/main.py`). Eight players;
`media_player.home_assistant_voice_0ab117` is in Basement Theater and is
the satellite's own device.

- spoken "Basement Theater": that player, "the Basement Theater speaker".
- nothing spoken, heard by the basement satellite: the same.
- "Kitchen": the kitchen player. "Garage": the 400, with "Rooms with a
  speaker: Basement Theater, Kitchen, Office".

The incident document has the dated line (2026-11-20, fix 1 applied, what
was read back, what was not run) and its status header says fixes 2 to 5
are open.

## 7. Tests

`./scripts/run_tests.sh`: `5677 passed, 3 skipped, 53 xfailed, 6 warnings
in 80.25s`, the builder's number. Old script against the new tests (scratch
copy): `18 failed, 202 passed`. Mutants: finding 3. The test module's
autouse fixture fails any test that connects or resolves a name, and
`FakeOps.run` raises on any command it does not know.

## 8. `setup-kronk-creds.sh`

Not in the working tree, not ignored, in no commit on any ref or reflog
(`git rev-list --all --reflog --objects`), no stash. Named only in the two
M16b sprint documents.

## 9. State before and after my run

Recorded at 06:58 and again at the end; `diff` empty for each:

- Containers (name, id, start time, status), 16 of them, the list in the
  build report. HA started 2026-11-18T00:14:34Z, MA 2026-11-04T13:45:22Z.
- Units `llama-*`, `wyoming-*`, `kronk-*`: id, main PID, active since,
  state. Unit files for `kronk-*`. The timer set (14). No backup, archive
  or upgrade timer fired; `kronk-upgrade-notices` and `kronk-music-canary`
  fired on their own schedule.
- Docker volumes (22). The NAS listing. `data/backups/` untouched (times
  06:18 to 06:33).
- My containers (`kronk-m16b-e2e-wal`, `kronk-m16b-e2e-cp`) and volume
  (`kronk-m16b-e2e-ma-test`) are removed. Nothing was built or deployed.
- Every run of the script carried `--no-notify` and `--dest` under
  scratch. No notice was sent.

## What I ran

- `attack.py`, `writer.py`, `stress.py`, `privdir.py`, `lookalike.py`
  (the WAL attacks, the private directory, the cleanup).
- `scripts/backup.sh --dry-run --no-notify --dest …` and `scripts/backup.sh
  --no-notify --dest …`, once each.
- `verify.py` (the tar, the second read, the token, the playlists, the
  secret search), `lookup.py` (the player lookup).
- `test_e2e_fail.py` under the repo's test venv (failure paths, GNU tar
  links); `mutate.py` (18 mutants and the old script).
- Runbook step 13 twice on a throwaway volume.
- `./scripts/run_tests.sh`, once.

## Not tested, and why

- **MA starting on the restored files and accepting `MA_TOKEN`.** Not
  required by criterion 5. A second MA with the household's
  `settings.json` is a container I did not want to start on my own
  judgement, even with no network. The claim stays "shown on the files".
- **The real MA stopped, or a real checkpoint of the real `auth.db`.**
  Not allowed, and not to be forced. Fakes and my own databases only.
- **A short read from the real `docker exec`** (finding 1). I found no way
  to cause one without breaking the daemon's connection.
- **`GET /api/backups` on production after an M16b run.** Production's
  status file is from the coordinator's run with the old script; I read
  the route's code and the status file my run wrote instead.
- **The WebSocket registry read.** I read the registry files through
  `docker exec` and the template render; both show the area. No WebSocket
  client was used.
- **Voice checks for fix 1.** No sound, no pipeline run.

## Scratch

`/tmp/kronk-m16b-e2e` (0700), apparent sizes in bytes, 103,757,738 in all:

| Path | Size | Notes |
| --- | --- | --- |
| `dest/` | 5,563,043 | the scratch tar (every secret) |
| `restore/` | 185,222 | the tar's `ma/` subtree (secret) |
| `unpack-ma/`, `independent/`, `back.db` | 154,679, 188,416, 94,208 | copies of `auth.db` (secret) |
| `state/`, `tmp/` | 6,389, 0 | scratch status, lock, model hash cache copy; the run's `TMPDIR` |
| `stale/` | 53,352 | a made-up database for the stale-WAL case |
| `wal/` | 94,496,279 | the harness and its made-up databases |
| `mut/` | 2,919,176 | copies of `scripts/`, `systemd/`, the test file, three docs |
| `e2e_tests/`, `verify.py`, `lookup.py` | 27,553, 6,534, 2,800 | my tests and check scripts; none holds a token |
| logs and before/after lists | about 60 KB | |

Two symlinks, both in the cleanup look-alike set (to `/etc/hostname` and
to a sibling scratch directory). Removed at the end with
`rm -rf /tmp/kronk-m16b-e2e`; no other `/tmp` directory was removed.
