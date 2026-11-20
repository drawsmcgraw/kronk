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

Left as they are: note 7 (step 13's sentence on `MA_TOKEN`: settled by the
scratch start of Music Assistant from the NAS tar, next), note 10 (the
corpus row: added by the coordinator with this commit's successor).
