# M30 promotion checks

Coordinator, 2026-11-25 09:43 EST (step A) to 2026-11-26 03:50 EST.

## Step A, production Whisper offline (2026-11-25 09:43 EST)

Unit installed and restarted by the coordinator: ready in 1.2 s, no
request out in the journal, both settings in the process, `refs/main`
unchanged, dev Whisper untouched; 25 test clips against the M26 run, 0
of 25 texts differ. (`M30-criteria.md`, "Rulings".)

## Step B, the record (2026-11-26 01:04 EST)

| Check | Result |
| :-- | :-- |
| `voice_runs_export.py --init` | 113 runs (110 household, 3 dev), 71 with text, 66 recordings joined, 46 not local, 1 none; 1.2 s |
| Modes | record 600 in a 700 directory outside the repo; both copies 600; `git status` clean |
| Units | `kronk-voice-runs-export.{service,timer}` installed, timer enabled; first run 01:05:30, nothing new, no copy written |
| B10, `--check-now` 01:05:45 | Tempo lists 113, the record holds 113, 0 differing, 0 missing |
| `integrity_check`, schema | ok, 2 |

## Step D, the early runs' words (2026-11-26 03:47 EST)

The journal of both Whisper units saved once to a 700 scratch directory
(600, 21 MB), given to both commands, removed after.

| Check | Result |
| :-- | :-- |
| Dry run | would fill 40; already with text 71, the journal agrees on 71; left 2, no stored count; 0.17 s |
| Every fill | `-0.00 s from the stt span's end`, `wyoming-whisper.service`, journal length equal to the row's |
| Audio | 46 rows stored not tried; would join 5 certain; 41 with no recording on the NAS either |
| `--apply` | filled 40, joined 5 |
| Second `--apply` | filled 0, joined 0 |
| The record after | 111 of 113 with text (`tempo` 71, `whisper_journal` 40, none 2); audio certain 71, none 1, not tried 41; `integrity_check` ok; max `seq` 153; `run_changes` 0 |
| The timer's next run | `copies written 2 of 2`; status `ok`, no error |

The two rows left: `01M7E9AS69…` (2026-11-14 19:22) and `01M85DRVA0…`
(2026-11-23 19:02), no stored count and no transcript near them.

## Step C and the deploy (2026-11-26 03:49 EST)

`scripts/deploy_tag.sh deploy item-M30`: `/api/status` and the
`/api/chat` shim ok; six Kronk containers on `item-M30`; Home Assistant
(up 8 days) and Music Assistant (up 3 weeks) not touched.

| Check on production | Result |
| :-- | :-- |
| `/work`, `/api/work`, `/resources`, `/` | 200; `/work` holds one `id="heard"` |
| `/api/voice_runs?limit=0` | status file fresh (18.5 s), `ok`, no error; household: 3 runs in 24 h, 0 wrong, 0 error |
| `/api/voice_runs?limit=500` | 110 household rows from a copy of 113; 108 with text, 2 "not recorded"; 68 from Tempo, 40 from the journal; 3 flagged |
| `/voice/api/chat`, "what time is it", 03:49:32 EST | "It is 3:49 AM Eastern Standard Time in Laurel, Maryland." |
| `data/` | no `-wal`, `-shm` or `-journal` beside the copy |
| The export timer | running, last run 03:49:13 |

Not checked by the coordinator: the page in a browser on production
(the test agent's browser runs were on dev, `M30-e2e6.md`, `M30-e2e7.md`);
a spoken request since the Whisper restart; the first monthly tar and the
first weekly copy with the record in them (the 10th and Monday 01:00).

Suite at the tag: `8400 passed, 3 skipped, 61 xfailed` (`M30-e2e9.md`).
