# M16b pass criteria: Music Assistant's playlists and login database in the config tar; two small chores

Source: Drew's decisions of 2026-11-20 after M16's install. Small item; the
M16 criteria and guard rails stand.

## Scope

1. **`playlists/` and `auth.db` join the config tar.** Both live in Music
   Assistant's `/data` (32 KB and 92 KB plus a 3.1 MB write-ahead log on
   2026-11-20). Out of the tar until now, so a restore lost MA's own
   playlists and killed `MA_TOKEN`.
2. **`setup-kronk-creds.sh`** (untracked, in the repo root, no secrets) is
   deleted.
3. **The basement Voice PE's Music Assistant device gets its area.** Device
   "basement voice pe ma" (`b91145f1a0afef63089911ffb5ba8b6b`) has no area;
   it is set to the area the satellite device "basement voice pe"
   (`1b6bd3b03f90e52f08dd98dca41eac82`) is in, "Basement Theater"
   (`docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md`, fix 1).
   This is the one change to the real Home Assistant this item allows.

## Criteria

1. The tar holds `playlists/` (every file, by name, nothing followed out of
   the directory) and a consistent copy of `auth.db`: read the way the
   other SQLite databases are read, or by a method shown to include what is
   still in the write-ahead log, without stopping or restarting MA and
   without writing in MA's `/data`. The restored copy opens, passes
   `PRAGMA integrity_check`, and holds the token rows the live one holds
   (counted, never printed).
2. Both are in the `MANIFEST` with how they were read. `auth.db` is a
   required item (unreadable makes the tar INCOMPLETE); `playlists/` absent
   or empty is not a failure. The size ceiling still holds; the new tar's
   size is in the report.
3. `auth.db` is a secret (it holds login tokens): it appears in no log
   line, notice, status file or API answer beyond its name and size; the
   feature doc's list of secrets in the tar and CLAUDE.md's secrets
   paragraph say so.
4. `docs/runbooks/restore.md`: the MA step restores both; the "needs a
   human" table no longer says `MA_TOKEN` is dead after a restore, and says
   what is true instead (shown by criterion 5, or stated as not shown).
5. Restore shown once in scratch, no sound, no LAN listener, removed
   afterwards: a scratch MA container is NOT required; opening the restored
   `auth.db` and `playlists/` from an unpacked tar is enough. If the agent
   does start a scratch MA, it follows M16's rules for scratch containers.
6. Tests for each of the above that fail on the old code; `./scripts/run_tests.sh`
   passes (before: `5660 passed, 3 skipped, 53 xfailed`).
7. The area: before and after states of both devices recorded in the
   report (area ids and names only); set through HA's own registry API
   (the WebSocket `config/device_registry/update` call), not by editing
   `.storage`, with no HA restart and no reload; the undo command is in the
   report; verified read-only afterwards that the registry shows it and
   that tool_service's player lookup for "Basement Theater" now finds the
   player (a read or dry-run path only; nothing is played). The incident
   document gets a dated line saying fix 1 is applied.
8. Production containers, units and timers unchanged (IDs and start times
   before and after); HA and MA not restarted; nothing written to the NAS
   by the build (the coordinator runs the next real backup); no
   notification sent to any phone.

Reports: `docs/sprint/M16b-build.md`, `docs/sprint/M16b-e2e.md`.
