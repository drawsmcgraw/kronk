# M33 promotion: work-item state and the queue in a database

Coordinator, 2026-11-25. Tag `item-M33` at `f3b4d4c`. No deploy: host
tools (`scripts/work.py`, the recording in `scripts/agent.py`, a backup
member) and a database outside the repo. Tier 3.

## Checks

- The suite at the code under test: `7959 passed, 3 skipped, 54 xfailed,
  6 warnings in 158.63s`.
- `KRONK_WORK_DB` unset in the coordinator's shell. The live import:
  `python3 scripts/work.py import-legacy --at HEAD --seed
  docs/sprint/M33-seed.json --compare data/work.json`, exit 0, at
  `f3b4d4c`: 37 items, 160 steps, 33 promotions (36 promotion items), 68
  matters (44 triaged, 24 untriaged older cells); 29 tags in git, 29 on a
  promotion; 161 files in `docs/sprint/`, 159 a step's report,
  PROMOTIONS.md and WORKFLOW.md not; `"notes": []`.
- `--compare` against the page's `work.json`: four lines, all expected.
  M33 `under_way` there and `done` here (the page's export was a minute
  behind the promotions entry); M28 and M31 have their tags here and none
  there (the page's parser misses a tag written mid-line; M34 ends that).
- `python3 scripts/work.py check`: nothing, exit 0.
- The first writes through the tool on the live database: one decision
  closed with Drew's answer, one asked, six leftovers added.
- WORKFLOW.md ("Start of every working session") and CLAUDE.md item 8
  rewritten by the coordinator to start from `resume`, keeping the two
  sweeps it does not cover, the escape count, and "record Drew's answer
  before acting on it".

## Not checked

- A session with no context starting from the real database (the test's
  cold-start agent used a scratch import of the same data).
- The monthly backup's own run with the new member (10 December).

Leftovers are rows: `python3 scripts/work.py matter list --open`.
