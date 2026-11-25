# M33 step 1 build report: work-item state and the queue in a database

Builder: `m33-build` (Opus 5.5), 2026-11-24, from `4eee8fd`. Step 1 only:
the store and the tools. `/work` and `scripts/work_export.py` are not
touched.

**Guard rails: none broken.** The live database
(`~/.local/state/kronk-work/`) was never created, opened or named by any
command of mine; it still does not exist. Every `work.py` run named
`KRONK_WORK_DB` in the session scratchpad. No herdr was started; the only
herdr calls were read-only `herdr agent list` from `work.py resume`/`check`
in the trial (the same call the M27 timer makes each minute) and none from
the tests (fake on PATH). No backup was run by hand. No push, no tag.

## What was built

| File | What |
| :-- | :-- |
| `scripts/work.py` (new) | The one writer: schema, every command, `check`, `resume`, the dump, `restore-dump`, `import-legacy`, and the three functions `agent.py` calls. Standard library. |
| `scripts/agent.py` | `launch` starts a step, `status`/`wait` finish it when done, `close` finishes or stops it. Calls `work.py`'s `agent_started`, `agent_finished`, `agent_closed`; no SQL (a test greps for it). Answers and exit codes unchanged. |
| `scripts/backups.py` | Member `work-state/work.db`, kind `sqlite`, not secret, not in `required_sources`. Path: `KRONK_WORK_DB`, else `HOME/.local/state/kronk-work/work.db` (the rule `work.py` uses). |
| `tests/test_m33_work_state.py` (new) | 61 tests. |
| `tests/test_m28_agent_tool.py` | The `repo` fixture makes a scratch database for the temporary repo with M9 active, so every M28 test runs with recording on; 23 new recording tests; `test_b2` copies `work.py` too. |
| `tests/test_m16_backups.py` | The `house` fixture has a work database; three tests (member present, missing is OK, `KRONK_WORK_DB` followed) and a restore round trip. |
| `tests/conftest.py` | `KRONK_WORK_DB` forced to a nonexistent path in a temporary directory for the whole run; the run fails if the live file, its `-wal`, `-shm` or dump changed. |
| `docs/features/work-state.md` (new) | The feature doc, with Blog hooks. |
| `docs/features/agent-tool.md` | One bullet: what `agent.py` now records. |
| `docs/runbooks/restore.md` | Step 14: restoring the work database (remove `-wal`/`-shm` first, `install -m 600`, `work.py dump`, `resume`). |

## Plumbing points (Step 1)

1. **Where.** `default_db()`: `--db`, else `KRONK_WORK_DB`, else
   `~/.local/state/kronk-work/work.db`. `create()` makes the directory 700
   and the file 600 (`O_EXCL`), WAL; `-wal`/`-shm` come out 600 too
   (checked in the trial). Only `init`, `import-legacy` and
   `restore-dump` create; `connect()` refuses a missing file.
   `meta.repo` records the repo; one database for dev and production.
2. **Schema.** `DDL` in `work.py`: Appendix A's seven tables, two columns
   added (below). Item states `queued held active promoted dropped`; no
   stage column (`test_stage_is_not_a_column`). Stage computed by
   `item_stage()` → `work_export.stage()` from finished step rows, the
   promotion row, `item-<id>` in git, herdr. Leftovers, decisions,
   escapes: `open_thing.kind`. Log: one row per command (`Tx.__exit__`):
   time, who (`who()`), command line, changed rows as JSON; append-only
   triggers.
3. **One writer, one path.** `Tx`: `BEGIN IMMEDIATE`, busy 10 s
   (`BUSY_S`); agent calls use 2 s (`AGENT_BUSY_S`). `agent.py` loads
   `work.py` as it loads `work_export.py` (no bytecode) and calls three
   functions. The path never comes from the script's location;
   additionally a write happens only when `meta.repo` is the agent's
   launch repo (`guard_repo()`), proved by
   `test_m33_a_launch_in_a_temp_repo_leaves_the_live_path_alone`.
4. **Evidence copied.** `report_evidence()`: the report must be in HEAD;
   commit = `git log -1 HEAD -- <report>`; verdict by
   `work_export.verdicts()` (test steps). No `--verdict` or `--commit`
   option exists. `promote` checks the tag with `for-each-ref` (or
   `--no-tag`) and needs `docs/sprint/<item>-promote.md` committed. `add`
   allocates; dropped numbers stay taken. What/why/Drew's words are not
   columns: `show` reads `handoff_of()` and the `Source:` paragraph from
   the criteria file. Rulings: `grade` sets `step.graded`; `note --next`.
   Tier is the row's (`add --tier`, `note --tier`).
5. **Commands.** All in `parser()`: `add show list queue start move hold
   unhold drop after`, `step add|start|finish|stop|refresh`, `grade`,
   `note`, `promote`, `matter add|close|list`, `log`, `check`, `resume`,
   `dump`, `restore-dump`, `import-legacy`, plus `init`. JSON lines except
   `resume`.
6. **`check`.** `findings()`. Appendix C rows 1 to 8 each have a test
   (`test_c1_…` to `test_c8_…`); also state file with no step, step file
   with no row, promoted with no Shipped line, queue numbering, dump
   stale or missing, another repo, herdr unreachable. Each finding
   carries the fix command; the tests run that command and check the
   finding is gone.
7. **`resume`.** `c_resume()`, sections in the order given
   (`test_resume_prints_the_sections_in_order`). Exits 2 on missing,
   empty, corrupt, newer, locked; prints nothing on stdout then
   (`test_resume_on_a_broken_database_exits_2_…`).
8. **`agent.py` recording.** `record()`/`step_of()`. `launch` after the
   prompt is sent; `status` and `wait` on `done`; `close` before the
   state file is removed (finished if the report is committed, else
   `stopped` with why). Idempotent; exit 2/3 leaves it started; a
   follow-up reopens nothing and the next commit moves the row. None for
   `--probe`, `investigate`, `--no-report`. A failure is one stderr line,
   within 2 s of waiting; `check` names the hole, `step start --at`
   closes it.
9. **History and loss.** `write_dump()` inside the transaction, after the
   log row, before `COMMIT`, so the dump is written under the write lock
   (temp, rename, 600). `restore-dump` builds a temp file, runs
   `integrity_check` and `foreign_key_check`, renames it in; refuses an
   existing target. No `.bak` copies. Backup member as in the table.
10. **Migration.** `import-legacy --at <sha> [--seed] [--compare]`: files
    read with `git ls-tree`/`git cat-file --batch` at the commit into a
    temporary directory, then `work_export.scan_items()` and
    `parse_promotions()`; tags `--merged=<sha>`. Seed applied as below.
    Older cells: `older_cells()`.
11. **Docs.** `docs/features/work-state.md` written. WORKFLOW.md and
    CLAUDE.md untouched; sentences below.
12. **No agent against the live database.** Kept (top of this file).

## Step 1 criteria and evidence

1. **Cold start.** Not mine to run (a separate agent and the test
   brief's key). What it will read is the `resume` output below.
2. **The import.** Reproducible: `test_import_is_reproducible_…` (two
   imports, equal rows). Tags: 28 in git, 28 on a promotion row, none
   unlisted. Files: 157 `.md` in `docs/sprint/` at `4eee8fd`, 155 a
   step's report, the other two `PROMOTIONS.md` and `WORKFLOW.md`; plus
   M29's criteria, read at `7380c43`. M24 to M31 and M29 against the
   seed: `test_the_real_import_at_the_build_start` pins tags (M28 and
   M31 from the seed), deployed (24 1, 25 1, 26 0, 27 1, 28 0, 31 1),
   M29 dropped, M30/M32 queued, M33 active. Against today's `work.json`
   (made at `4eee8fd` with herdr off the PATH): two differences, both
   expected, the M28 and M31 tags. Matters: 43 from the seed in the
   seed's state, 24 untriaged older cells, each "see
   PROMOTIONS.md:<line>" (lines 7 to 26 and 37: 21 cells; 48, 66, 79:
   the three "Not done" bullets).
3. **A full life.** `test_a_full_life_of_a_made_up_item`: added, queued
   behind another, moved ahead, criteria step, build launched with
   `agent.py` through the fake herdr, waited, closed, test 1 FAILS, fix
   (closed without a status: close finished it), test 2 PASSES, a
   leftover and a decision opened and closed, promoted. `resume` checked
   after each step; the log holds each command; `check` clean at the end
   once a Shipped line names the item.
4. **Two writers.** `test_two_writers_200_read_then_write_commands_…`:
   two processes, 200 `add` (read the max number, insert) and 200 `note`
   (read the row, update) each: 400 log rows, 201 distinct items, the
   last note is `b199`, queue 1 to 201, no "locked" on stderr. About 1 s.
5. **Broken databases.** `work.py`: `test_resume_on_a_broken_database_…`
   (missing, empty, corrupt, newer: one line, exit 2, under 12 s),
   `test_a_database_locked_for_30_s_is_said_within_12_s` (a writer and
   `resume` against an exclusive lock held 30 s: both "locked: still
   locked after 10 s", exit 2, 10.1 s). `agent.py`:
   `test_m33_a_database_it_cannot_write_never_stops_the_agent_command`
   (missing, empty, corrupt, newer, another repo, no item: launch, wait,
   close all exit 0 with the usual JSON and one stderr line each; the file
   unchanged; a missing one not created) and
   `test_m33_a_locked_database_costs_launch_wait_and_close_under_3_s`.
6. **Appendix C.** As plumbing 6. A step left started by a dead agent:
   `step stop` (one command); by a failed write at launch: `step start
   --at <launch time>` (one command, the time from the state file).
7. **The live database's mtime.** `conftest.py` fails the run if it
   changes; the file does not exist and the suite did not make it.
8. **Backup and loss.**
   `test_m33_restored_from_the_tar_the_work_database_passes_and_resumes_the_same`:
   a real `work.py` database, the config tar made while a reader holds the
   WAL open, the member restored with `-wal`/`-shm` removed:
   `integrity_check` ok, `resume` equal to the live one but for the log
   tail (and for a write made after the tar). `restore-dump`:
   `test_every_write_rewrites_the_dump_…`. A by-hand
   `backups.py config --dest <scratch> --no-notify` is the test agent's;
   it can point the member at a scratch database with `KRONK_WORK_DB`.
9. **`/work` unchanged.** `git diff 4eee8fd HEAD -- scripts/work_export.py
   orchestrator/` is empty.
10. **The suite.** Last lines below.

## Choices the criteria left open

- **Two schema columns added.** `item.held_why` (with a CHECK: set when
  and only when held): "held with why" has no column in Appendix A.
  `promotion.note`: the seed's `deploy_note` and an imported tag written
  as text ("none") needed a place.
- **Exit codes.** 1 refusal or usage, 2 database unusable (missing, empty,
  corrupt, locked, newer), 3 `check` found something. `resume` exits 0
  with findings (it prints them first).
- **`init`.** A command to make an empty database; nothing else creates
  one by accident, `agent.py` never.
- **The repo guard.** The database records the repo it describes;
  `agent.py` writes only for that repo. Without it, a temporary repo's
  launch (each M28 test, or any hand trial) with `KRONK_WORK_DB` unset
  writes production's queue.
- **`move` while `after` is not promoted is refused outright**, as written,
  not only "moved ahead of the other". So M32 cannot move at all until
  M33 is promoted; `after M32 --clear` lifts it.
- **`start` is the coordinator's.** `step start` (and so `agent.py
  launch`) refuses on a queued item: "M30 is queued, not active
  (`work.py start M30` first)". The launch still happens; the stderr line
  says why nothing was recorded.
- **`step finish` with no row inserts the step finished** (an agent
  launched before this tool, or a failed start): evidence wins.
  `close` with no row and no committed report inserts it `stopped`.
- **Verdicts are copied for test steps only**, as `/work` reads them.
- **`grade`** defaults to the last finished test. Not in the plumbing's
  list by that name but in its text ("`grade`").
- **Times** are stored in UTC (`…Z`); `resume` shows decision dates in
  local time. Promotion `at` for imported rows is as written.
- **Who.** `KRONK_WORK_WHO`, else `session <CLAUDE_CODE_SESSION_ID>`, else
  `pid <n> started <time>`. The commit trailer's `Claude-Session` URL is
  not in the environment; the session id is. Rows written by `agent.py`
  read `agent.py (<who>)`.
- **`--db` and `--repo`** are accepted before or after the command.
- **Ordering of steps for the stage** is `(ended_at, step_rank, id)`.
- **Imported step authorship.** `criteria` and names ending in
  `criteria` (M1b's `restore-criteria`) are the coordinator's; every other
  imported step is an agent's. Imported steps have no start time.
- **Seed `graded` (free text)** is applied to the item's last finished
  test step: `graded = 'pass'` when the item was promoted, the text in
  `note`. M25 and M28 get it.
- **Seed `note`** (M30) is appended to `next_step` ("design review, then
  build; runs on the tool between M33's two steps").
- **Older cells**: a table row's fifth cell with text, and a line
  starting `- Not done:`; the matter's item is the row's first id or the
  nearest heading above.

## The seed format

Taken as given; no fact edited. Nothing was missing. Two awkward fields
were read by rule rather than changed: `graded` is prose (see above), and
`note` on M30 has no column. If a structured form is wanted later:
`"graded": {"round": 2, "grade": "pass", "why": "…"}`. `load_seed()`
refuses unknown keys, so a typo in the coordinator's seed is a refusal,
not a silent drop.

## The trial import (scratch, from `4eee8fd`)

```
KRONK_WORK_DB=<scratch>/trial/work.db python3 scripts/work.py import-legacy \
    --at 4eee8fd --seed docs/sprint/M33-seed.json --compare <scratch>/we-now.json
```

```
{"import": "4eee8fd", "counts": {"item": 36, "step": 156, "promotion": 32, "promotion_item": 35, "open_thing": 67},
 "matters": {"triaged": 43, "untriaged_older_cells": 24},
 "tags": {"in_git": 28, "on_a_promotion": 28, "not_on_a_promotion": []},
 "files": {"md_in_docs_sprint": 157, "a_steps_report": 155, "not_a_report": ["PROMOTIONS.md", "WORKFLOW.md"]},
 "not_read_by_the_parser": [M1b-restore-build.md, M1b-restore-criteria.md, M3-e2e2-rows.md, M5-change1.md,
                            M6-audit.md: "not a known step (listed under its name)"], "notes": []}
{"compare": "tag", "item": "M31", "json": null, "db": "item-M31"}
{"compare": "tag", "item": "M28", "json": null, "db": "item-M28"}
```

(The five files the parser does not know are imported as `other` steps
under their names; they are reports, so the file count includes them.)

`check`, at HEAD `9753043`, exit 3:

```
{"check": "agent-no-step", "item": "M33", "says": "agent m33-build is working in herdr; no step row",
 "fix": "work.py step start M33 build --round 1 --agent m33-build --at 2026-11-24T23:06:30-0500 --model claude-opus-5-5 --report docs/sprint/M33-build.md"}
```

That is this agent, launched by the old `agent.py`. On a copy of the
trial database the fix line was run: `check` then exits 0, and `resume`
shows `started: build round 1 by m33-build at 2026-11-25T04:06:30Z`.
The live import will say the same while `m33-build` is open; after it is
closed and its report committed, it will say `file-no-step` for
`M33-build.md` instead (fix: `work.py step add M33 build`), unless the
import commit already holds the report.

`resume`, at HEAD `9753043`, exit 0 (0.1 s):

```
Work state: <scratch>/trial/work.db (schema 1), at 2026-11-25T04:35:01Z

== Check: 1 finding(s) (rows against git, herdr, the files)
- [agent-no-step] agent m33-build is working in herdr; no step row
    fix: work.py step start M33 build --round 1 --agent m33-build --at 2026-11-24T23:06:30-0500 --model claude-opus-5-5 --report docs/sprint/M33-build.md

== Under way: 1
M33  tier 3  work-item state and the queue in a database
    stage: implementing (implementing, agent m33-build working)
    agent: m33-build working, 28.5 min since launch
    last step: review round 1 (docs/sprint/M33-review.md, 2026-11-25T04:04:10Z)
    next step: step 1 build
    waits on: -

== Queue: 2
1. M30  tier 3  what Whisper heard, on /work  <- next
    next step: design review, then build; runs on the tool between M33's two steps
    waits on: -
2. M32  tier 2  /work for a wall display: live, foldable, with what and why
    next step: design review, then build
    waits on: -
    after: M33 (M32 follows M33, which is active, not promoted)

== Owed by Drew: 8 open decision(s), oldest first
- #3 M24, 2026-11-24: 'walkie talkie range in the woods' reaches Talkie: the rule matches 'talkie' and a space. Change the rule or accept?
- #4 M24, 2026-11-24: Talkie is handed 'ask talkie' with the question (2 of 50 replies were five-word fragments). Strip the address or accept?
- #15 M25, 2026-11-24: What /resources still does not show: activity is this orchestrator's calls only; GPU busy is one instant; nothing shows what a server is doing now though GET /slots answers; Whisper has no usage count; no timing figures. Worth an item?
- #16 M25, 2026-11-24: /services' orchestrator dot and deploy_tag.sh's check run a completion on every model server through /api/status (Talkie's on the CPU included). A candidate for its own item
- #22 M27, 2026-11-24: What /work shows the LAN: open ROADMAP entries whole (three mis-heard utterances with a run id; that agents run with permissions skipped; the HA version; what is down). Say if any should stay off
- #35 M31, 2026-11-24: Voice can wait behind a coding or devops answer on Gemma's lock: a typed coding answer has no token limit (up to about 268 s); a voice request has 25 s. Cap those agents' answers, or accept? (coordinator recommends a cap)
- #38 (no item), 2026-11-24: Should the two databases (work state, voice runs) be copied to the NAS daily? Today's config backup is monthly, on the 10th
- #40 (no item), 2026-11-24: Kronk-side music weaknesses from the GRiZ investigation (a false 'Playing…' with no tool call; play_music's 20 s limit against about 21 s; empty error text; a misleading 'could not be found'; 'part' against 'pt'): which become items?

== Open leftovers: 29 triaged, 24 not yet triaged
drew: 7
- #1 M24: Ollama's 241 GB of blobs, its unit, its user, /usr/local/lib/ollama.removed and three old system unit files: the deletes are Drew's (sudo block given 2026-11-24)
- #2 M24: A spoken 'Okay Nabu, ask talkie …' test
- #8 M24: .env.dev still sets ROUTER_MODEL (nothing reads it) and, lines 26-27, the two devstral variables (compose overrides them)
- #21 M25: Look at /resources on production in a browser (tests rendered dev's)
- #29 M27: Look at /work on production in a browser
- #36 M31: The devstral model file (14.3 GB) and template are on disk as the way back; deleting them is Drew's
- #42 (no item): Push: main is ahead of origin by every commit since d4c0ff3
coordinator: 22
- #6 M24: A two-word opener can be spoken alone when the next sentence is cut; a cut word can be spoken after lines not ending on a full stop (M24-e2e2.md should-fix 1, 2; 0 of 63 live)
- #7 M24: Code defaults in orchestrator/agents.py name retired models: COORDINATOR_MODEL still defaults to mistral-nemo:12b (M31 moved coding and devops to inherit it)
- #9 M24: The shim reports model gemma-4-e4b for a reply Talkie wrote
- #10 M24: Talkie's context is 2,048 tokens
- #12 M26: Rebuilding ~/services/rocm-7.2 needs a pull of a ten-month-old image by digest
- #13 M26: build_rocm_libs.sh: build can be raced between check and write; verify checks lib/ only and is not a signature; eight mutants survive (M26-e2e2.md notes)
- #14 M26: Production Whisper has not been restarted since the Ollama directory was renamed; its next restart is its own test (M30 restarts it)
- #17 M25: Under the memory bar: 'which the kernel counts as available' would be false for a server started with --mlock; 'includes memory-mapped model files' is true of three cards of five (M25-e2e3.md S1, S2)
- #19 M25: On /dev/services the probe dots are production's
- #20 M25: README's model table no longer carries memory
- #25 M27: An agent name with invalid Unicode stops the export's write (S1); a row label's right boundary has no test (S3); a promotions table straight after a heading is read as its body; a '### Mxx' heading inside another entry promotes Mxx
- #26 M27: The 'promoting' stage shows 'tag item-<id> exists' with the placeholder unfilled (M33 step 2 or M32 touches this)
- #27 M27: kronk-context.md says /api/servers probes LiteLLM's health
- #28 M27: An edit to scripts/work_export.py or to a file it reads is live on production's page within a minute
- #30 M28: A launch killed or failed inside 'herdr workspace create' leaves a workspace the tool does not know; if a launch dies read 'herdr workspace list' first
- #31 M28: After a follow-up prompt wait with --expect-commit; plain wait returns at once on the first report
- #32 M28: agent_commits and --expect-commit rest on the Claude-Session trailer on the coordinator's commits
- #33 M28: prompt and peek find the agent by name, not by pane
- #34 M28: Some of the tool's tests do not fail when their job is undone (M28-e2e2.md S5)
- #37 M31: On dev the devops answer said the mirror was 'unreachable' where the tool said the host was not configured (one sample; tenet 7)
- #41 (no item): Music corpus row owed for the GRiZ album incident (tests/voice_corpus.jsonl)
- #43 (no item): YouTube Music is down in Music Assistant until upstream ships the yt-dlp fix (ROADMAP entry; check yt-dlp/ejs#80 at each session start)
not triaged: 24 (older PROMOTIONS.md cells: `work.py matter list --open` lists them; close each, or split it into triaged ones)

== Escapes within the last 10 promoted items (M31, M28, M27, M25, M26, M24, M23, M22, M16b, M16): 0

== Tree: branch main, HEAD 9753043, clean

== Agents scripts/agent.py knows: 1
- m33-build (M33 build): working, 28.5 min since launch, report docs/sprint/M33-build.md

== Last five writes
- 2026-11-25T04:35:01Z session cf287019-d0c5-48bf-88ad-fbe116096542: work.py import-legacy --at 4eee8fd --seed docs/sprint/M33-seed.json --compare <scratch>/we-now.json

== Not covered here; the session still does these by hand:
- the voice runs marked as corrected in Grafana since the last session (docs/features/voice-capture.md);
- every ROADMAP.md entry that waits on something outside the repo (an upstream release, a merge),
  checked against its source.
```

The escape window lists M26 after M25 because PROMOTIONS.md has M26's
entry above M25's (file order is promotion order for imported rows).

## For the coordinator: the live import and first use

```
python3 scripts/work.py import-legacy --at <sha> --seed docs/sprint/M33-seed.json --compare data/work.json
python3 scripts/work.py check            # then each fix line, then:
python3 scripts/work.py resume
```

Into a missing database it makes `~/.local/state/kronk-work/` (700) and
the file (600). Expected findings: `agent-no-step` or `file-no-step` for
this build (above), the same for the M33 test agent if one is open. M33's
own promotion is still the last PROMOTIONS.md entry (step 1's rule), so
it reaches the database by the import, or by `work.py promote M33 …`
with an `M33-promote.md`; not both.

## Sentences WORKFLOW.md and CLAUDE.md need (the coordinator's to write)

WORKFLOW.md, "Start of every working session", first paragraph:

> Run `scripts/work.py resume` first. It prints, in this order, what
> `check` found (rows against git, herdr and the files), what is under way
> with its computed stage and agent, the queue, the decisions Drew owes,
> the open leftovers, the escape count within the last ten promoted
> items, the tree, the agents `agent.py` knows, and the last five writes.
> Settle each `check` finding with the command it prints before anything
> else. `resume` does not cover two things, which the session still does:
> the voice runs marked as corrected in Grafana since the last session,
> and every ROADMAP.md entry that waits on something outside the repo,
> checked against its source. When Drew answers a decision, record the
> answer (`work.py matter close <id> --how "Drew, <date>: …"`) before
> acting on it.

WORKFLOW.md, the steps:

> 2. … Commit it, then `work.py add --title … --tier N` (the number is
> allocated; the criteria file uses it) and `work.py step add <item>
> criteria`. The tier is the row's; the criteria file no longer names it.
> The queue is changed only through the tool (`move`, `hold`, `unhold`,
> `drop --why`, `after`, `note --next/--waits`).
> 4. Before the first agent of an item: `work.py start <item>`. `agent.py`
> records each agent's step itself.
> 6. A ruling on a test: the ruling in the criteria file, and `work.py
> grade <item> pass|fail` plus `work.py note <item> --next …`.
> 7. … Write the checks to `docs/sprint/<item>-promote.md`, commit it,
> then `work.py promote <item> --tag item-<item> --deployed yes|no
> [--untested …]`. PROMOTIONS.md is no longer appended to after M33 step
> 1. Leftovers: `work.py matter add leftover --owner drew|coordinator
> --item <item> --text …`. An escape: `work.py matter add escape --item
> <promoted item> …`.

WORKFLOW.md, the first paragraph: strike "(M24 is next)": `work.py add`
allocates the number.

WORKFLOW.md, "Reporting to Drew": add "Decisions asked of Drew are
recorded as they are asked (`work.py matter add decision --owner drew …
[--blocks]`)."

CLAUDE.md, item 8, the last bullet:

> At the start of the session you run `scripts/work.py resume`, settle
> what its `check` section names, and report what needs a decision; then
> the two sweeps it does not cover (the corrected voice runs, ROADMAP
> entries waiting on something outside) and the escape count it prints.

CLAUDE.md, "Ground rules", Secrets: the work database is not a secret but
`resume` prints its text to a session that runs agents with permissions
skipped; it lives outside every container's mount for that reason
(`docs/features/work-state.md`).

## Not done

- Step 2 (`/work` from the database): not mine.
- Criterion 1's cold start and the by-hand backup run: the test agent's.
- `work.py` has no `matter` edit (owner or text): an untriaged row is
  closed, or closed and split into new ones, as the review says.
- No `log` filter by who.
- The review's concern about two coordinator sessions at once is covered
  only by `resume`'s last five writes, as the review proposed.

## The suite

Run before the last commit (this report and one test edit):

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
7884 passed, 3 skipped, 54 xfailed, 6 warnings in 152.18s (0:02:32)
```
