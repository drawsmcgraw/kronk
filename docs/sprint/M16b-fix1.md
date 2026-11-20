# M16b: coordinator fixes after the end-to-end test

The test passed (`docs/sprint/M16b-e2e.md`: no blocker, three should-fix,
nine notes). Fixed by the coordinator on 2026-11-20, not retested by an
agent.

| Finding | Change | Test |
| --- | --- | --- |
| 1: a WAL read that comes back short with rc 0 is accepted | `read_wal_pair` takes a pair only when both WAL reads are the same bytes and end on a whole frame (`wal_is_whole_frames`). A short read would have to stop at the same frame boundary twice. Not done: asking the container for the file's size, which would need a second transport call per read. | `test_m16b_e2e1_*` (two) |
| 2: a hard-linked playlist is dropped without a word | `add_tar_stream` lists a member that is not a file, directory or symlink as not read ("left out: a hard link to ..."). It also covers HA's stream, which uses the same function. | `test_m16b_e2e2_*` |
| 3: five surviving mutants | Tests for the ignored integrity check, the cleanup's age test and its name pattern. Not pinned: which of the two WAL reads is replayed (they are now identical), and `secret=True` on the member (read nowhere; left). | `test_m16b_e2e3_*` (two) |
| 4 (note): SIGTERM leaves the private copy | The SIGTERM handler removes the copies being made. SIGKILL still leaves one for the next job. | `test_m16b_e2e4_*` |
| 12 (note): MANIFEST said "unchanged" | Now "identical", which is what is checked. | the manifest test |

Suite: `5683 passed, 3 skipped, 53 xfailed`.

## Scratch restore from the tar on the NAS (coordinator, 2026-11-20)

Drew asked whether a fresh HA and MA had been started from the backup. MA
never had; HA once, from a scratch tar. Done now from
`/mnt/nas-backup/config/kronk-config-2026-11-20.2.tar.gz` (the first tar
with `auth.db` and `playlists/`; 5.6 MB, 487 entries), unpacked in a 0700
scratch directory. Both containers `--network none`, no ports, throwaway
volumes, production images, removed afterwards with their volumes and the
scratch directory. Production containers untouched; nothing played.

**Music Assistant** (`kronk/music-assistant:2.11.0b5-kronk.9`), the
runbook's step 13 copy into a new volume, then the container.

- It does not start with no network as it is: zeroconf fails with
  `[Errno 19] No such device` and MA exits. A small wrapper gave the
  container's own loopback the multicast flag and a `224.0.0.0/4` route
  (`--cap-add NET_ADMIN`, inside the container's empty network namespace
  only). Then it started and reached `running`.
- Asked from inside the container, `POST /api` on its loopback, `MA_TOKEN`
  read from `.env` and passed on stdin, never printed: `config/providers`
  200 with 27 provider entries; `auth/tokens` 200 with 4 tokens (the count
  the build read); `auth/me` 200, role admin; `players/all` 200 with 7
  players known from the settings. The same token with its first
  character changed: 401. No token: 401.
- Pandora and the NAS folder failed to load, as they must with no network
  and no mount. Provider logins working is therefore not shown.
- `playlists/Hotel Costes.m3u` is in the volume; MA's library list of
  playlists was empty (the library database is not in the tar by design,
  and nothing could sync). Not shown: that it reappears after a sync.

**Home Assistant** (`ghcr.io/home-assistant/home-assistant:2026.5.4`),
the runbook's step 12 copy, then the container.

- A WebSocket probe from inside, `HA_TOKEN` on stdin: `auth_ok`; pipelines
  `Full local assistant`, `Home Assistant`, `kronk`, `kronk-dev`, a
  preferred one set; 26 devices, 395 entities, 5 areas, 127 states (the
  M16 build's scratch run saw the same 26, 395 and 127). The basement MA
  device is in `basement_theater`: this morning's change is in the backup.
  An altered token: `auth_invalid`.
- 23 ERROR lines, every one a missing network or an integration that needs
  one (ssdp, zeroconf, cloud, sonos, music_assistant, ollama, met,
  radio_browser).

A safety check refused the step's `rm -f /data/auth.db-wal
/data/auth.db-shm` line in the coordinator's shell; the volume was new, so
the copy ran without it. The e2e agent ran that line as written twice.

Not shown by any of this: a restore on a second machine; provider logins;
the pucks pairing again.

## Left

- The incident's utterance is still not in `tests/voice_corpus.jsonl`.
  Adding the row fails `test_pin_holds_no_snapshot_misses`: the recorded
  Music Assistant snapshot has no answer for its reads, so it needs a
  snapshot refresh, which is its own small job.
- `tests/fixtures/m9/replies.py` now removes its temporary directory. The
  511 `kronk-m9-bytes-*` directories already in `/tmp` are left for Drew's
  word (or the next reboot: `/tmp` is tmpfs).
