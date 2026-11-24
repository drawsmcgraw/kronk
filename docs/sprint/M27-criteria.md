# M27 pass criteria: /work, the work items in a page

Source: Drew's order of 2026-11-24: "i want to be able to track this work
in a UI. A backlog, currently working (planning/reviewing/implementing/
testing/etc), what's already been worked, and so forth. I should able to
visit kronk/work and the page displays. Add this to the backlog to happen
after M26."

Handoff line: /work shows the backlog, each work item under way with its
stage, and what is done, read from the files that already hold it.

## What the investigation found (coordinator, 2026-11-24)

- The state of the work is already written down, in the working tree:
  - `ROADMAP.md`: sections "Now — in flight", "Next — agreed, not
    started", "Later — wanted", "Chores", "Shipped" (62 entries, each
    starting `- **Title**`). Some entries name their item ("work item
    M27").
  - `docs/sprint/<item>-criteria.md`, `-review.md`, `-build.md`,
    `-e2e.md`, `-e2e2.md` …, `-fix1.md` …: one file per step of an item,
    committed when the step ends. 29 items so far (M1 to M26, with M1b,
    M4b, M16b, M21-step1, M21-step2; no M15).
  - `docs/sprint/PROMOTIONS.md`: one entry per promoted item (a table
    row for M17 to M20, a `## Mxx (…), date` heading since M22).
  - Git tags `sprint-<item>` (to M23) and `item-<item>` (since M24).
- The shapes are not uniform. A criteria file's first line is `# Mxx pass
  criteria: <title>`. The handoff line exists only since M24. Verdict
  lines come as `Verdict: PASSES`, `**Verdict: ITEM PASSES.**`, `**M9
  FAILS.**`, `**M7 PASSES.**`, and `NOT DETERMINED` (M3 and M5 retests).
- **The production orchestrator cannot see any of it.** Its image holds
  `*.py`, `static/` and the corpus; it mounts `data/`, `kronk-context.md`
  and the LiteLLM catalog. It runs from a release directory, which is a
  snapshot of a tag: an item under way exists only in the working tree.
  The repo's answer to this seam is a host exporter on a timer that
  writes a JSON file under `data/` (`scripts/gpu_mem_export.py`,
  `scripts/llama_props_export.py`).
- Which agent is running now is not in any file. herdr knows it: `herdr
  workspace list` and `herdr agent get <name>` answer from a plain user
  systemd unit (checked with `systemd-run --user`), and the coordinator
  names each workspace and agent `<item>-<role>` in lower case
  (`m25-build`, `m25-test2`, `m26-review`, `m26-fix1`). The coordinator's
  own workspace is `kronk-claude`.
- Pages are routes of the orchestrator (`@app.get("/resources", …)`
  serving `static/resources.html`); nginx sends everything not matched
  elsewhere to it, so `/work` needs no nginx change. `/services` carries
  the list of pages.

## Plumbing, decided by the coordinator

- **One source, no second store.** A host exporter,
  `scripts/work_export.py`, run by a user timer every 60 s, reads the
  working tree and writes `data/work.json` and `data/dev/work.json`
  atomically, as the two existing exporters do. The orchestrator serves
  it at `/api/work` and the page at `/work`. Nothing is entered on the
  page and nothing is written back.
- **What is exported is the outline, not the documents**: titles,
  handoff lines, item ids, step names, verdict words, dates, counts. No
  body text of a criteria file, a report or an incident. (The reports
  quote household requests in places; the page is readable by anyone on
  the LAN.) A `ROADMAP.md` entry's own text is exported: it is written
  for that purpose.
- **Three groups on the page**, in Drew's words:
  - *Backlog*: `ROADMAP.md`'s Now, Next, Later and Chores entries that
    have no item under way, under those headings, in file order.
  - *Under way*: every item with a criteria file and no promotions entry.
  - *Done*: promoted items, newest first, and `ROADMAP.md`'s Shipped
    entries that belong to no item.
- **An item's steps come from its files**, in commit order: criteria,
  review, build, test 1 (with its verdict), fix 1, test 2 … promoted
  (tag, date). Each step shows the date and time of its file's first
  commit.
- **An item's stage is one of**: planning (criteria not yet committed
  but an agent or a workspace exists for it, or criteria committed and
  nothing after), reviewing, implementing, testing, fixing, waiting for
  the coordinator, promoted. It is decided by two things only: the last
  step file committed, and whether herdr shows an agent for that item
  now. A running agent named `<item>-review*`, `-build*`, `-test*`,
  `-fix*` gives reviewing, implementing, testing, fixing. No agent
  running gives "waiting for the coordinator", with what came last
  ("test 2: FAILS"). The page says which of the two facts a stage rests
  on.
- **A verdict is read, never guessed.** The exporter knows the forms
  listed above. A test report whose verdict it cannot read shows "verdict
  not read", and the exporter's output lists the file.
- **An item is linked to its backlog entry** when the entry names it
  ("work item M27", "sprint M12", "M16 and M16b"). An entry that names no
  item stands alone.
- **herdr is optional.** If herdr cannot be reached, stages fall back to
  the file facts alone and the page says the agent state is not known.
  The exporter never fails because herdr is absent.
- **Staleness as on /resources**: the file carries its time; older than
  180 s the page says STALE with the age and keeps showing the outline
  (it is still the last known state, unlike a memory figure), marked as
  such.
- **Read only on the host**: `git log`, `git tag`, reading files, `herdr
  … list|get`. No network. No write but the two output files.
- **Relative URLs on the page**, and a line for it in `/services`' list
  of pages.
- **The coordinator installs the timer** (`systemd/kronk-work-export.
  service` and `.timer`, written by the build) before the test. Agents
  install nothing. The exporter is developed in scratch and moved into
  `scripts/` in one step; `--print` and `--out` as the other two have.

## Scope

1. `scripts/work_export.py` with its unit and timer files.
2. `/api/work` and `/work` (`orchestrator/static/work.html`), in the look
   of the existing pages.
3. The page's link in `/services`' list of pages.
4. `docs/features/work-page.md` (what, how, gotchas, blog hooks);
   `README.md`'s service map and page list; `docs/sprint/WORKFLOW.md`:
   one paragraph saying the page reads the files and the agent names, so
   the naming `<item>-<role>` is now load-bearing.

Out of scope: editing anything from the page; the text of the reports;
a launcher for agents; history charts; notifications; changing the
shape of `ROADMAP.md`, `PROMOTIONS.md` or the report files to suit the
exporter (it reads what is there; a shape it cannot read is listed, not
fixed).

## Criteria

1. **Every item.** For each of the 29 items in `docs/sprint/` today the
   export has its id, title, its steps in commit order with dates, each
   test's verdict, and whether it was promoted, with tag and date. Shown
   against a hand-checked table for at least eight items chosen to cover
   every shape: M7 (one test, passes), M9 (fails, a coordinator fix, no
   second test), M16 (four tests, four fix files), M3 (a NOT DETERMINED
   retest, a rows appendix file), M21-step1 and M21-step2 (two steps of
   one item), M1b (a restore build and criteria), M6 (an audit only),
   M13 and M20 (tag only, not deployed), M25 (a review, a failed second
   test), M26.
2. **Verdicts.** Every `-e2e*.md` file in the repo yields PASSES, FAILS
   or NOT DETERMINED equal to what a person reads in the file; the report
   lists all of them with the line each was read from. A file with no
   readable verdict yields "verdict not read" (shown with a fixture), not
   a guess. `M3-e2e2-rows.md` is not a test.
3. **Under way.** With a fixture working tree holding an item at each
   point (criteria only; criteria and review; build committed, no test;
   a failed test, no fix; a fix, no retest; a passed test, not promoted)
   and a fake herdr answering with and without an agent for that item,
   the stage is the one the plumbing section defines, and the export
   says which fact it rests on. With herdr absent, erroring, or slow
   (over 5 s), the export is still written, within 10 s, with agent state
   "not known".
4. **Live.** While this item is being tested, `/dev/work` shows M27
   under way with stage "testing" resting on the running agent
   `m27-test`, and its steps so far; once the agent stops, within two
   exporter cycles, "waiting for the coordinator".
5. **Backlog.** Every entry of `ROADMAP.md`'s Now, Next, Later and Chores
   sections appears once, under its section, in file order, with its
   title and text; an entry whose item is under way appears with the
   item, not in the backlog. The count of entries on the page equals the
   count in the file (shown).
6. **Done.** The promoted items, newest first by promotion date, each
   with its title, tag and date; Shipped entries with no item after them.
   M17 to M20's table rows and the later headings of `PROMOTIONS.md` are
   both read.
7. **Nothing but the outline leaves the repo.** A test builds the export
   from a fixture tree whose report bodies hold a marker string, and
   asserts the marker is nowhere in the output. On the real tree the
   export holds no line of any `-build`, `-e2e`, `-fix`, `-review` or
   incident file other than a title, a handoff line and a verdict word
   (shown by a check, not by reading).
8. **The exporter is safe to run every minute.** Read only (shown by
   `strace -f -e trace=file,network` or an equivalent: no write but the
   two output files and their temp files, no socket but herdr's); a kill
   in mid-write leaves the old file whole; a run on the real tree takes
   under 3 s; it never fails on a file it cannot parse (fixture: an empty
   file, a binary file, a criteria file with no title line, a report
   with two verdict lines).
9. **Staleness.** With `work.json` older than 180 s, missing, or
   unreadable, `/api/work` says which and the page says so with the age;
   a stale file's outline is still shown, marked stale. Tests with
   fixtures, and once live on dev by the compose override method
   (`M25-criteria.md`).
10. **The page.** `/dev/work` rendered in a headless browser: the three
    groups; every item, step, verdict and date on the page equals
    `/dev/api/work` fetched at the same moment; all its requests are
    under `/dev/`; nothing reads "undefined", "null" or "NaN"; it is
    readable at 1280 px and at a phone's width (390 px). The page's
    state-to-text decisions are in small functions the suite checks (as
    far as the repo can without running the page's script; say what is
    not covered).
11. `/dev/api/work` answers in under 300 ms at the median of 20 requests
    and causes no model request.
12. Docs changed as scope 4 says.
13. `./scripts/run_tests.sh` passes (before: `7504 passed, 3 skipped, 54
    xfailed`).
14. Nothing is installed, started, stopped or deployed by the build or
    the test, apart from the dev pair. No chat request. `data/` is
    written only by the installed timers. Production unchanged; HA and MA
    untouched; no sound; no notification.

Reports: `docs/sprint/M27-review.md` (design review, before the build),
`docs/sprint/M27-build.md`, `docs/sprint/M27-e2e.md`.
