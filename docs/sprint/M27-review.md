# M27 design review: /work, the work items in a page

Reviewer: Fable 5.2, 2026-11-24, about 17:50 to 18:25 local. Read:
`M27-criteria.md` (9ccd6d6), `WORKFLOW.md`, `ROADMAP.md`, `PROMOTIONS.md`,
the first line of all 138 item files, every `-e2e*.md` verdict line, both
exporters and their units, `orchestrator/servers.py` (`read_source`), the
page routes, `services.html`, `M25-review.md`, the M25 test findings, and
herdr 0.9.5 through `--help`, `workspace list`, `agent list`, `agent get`.
Nothing was changed.

Not observed, so stated from herdr's help and one error answer only: an
agent in `idle` or `blocked`, and a closed workspace. Seen live: `working`
(this agent) and `done` (the coordinator's pane, waiting on this review).
Whether the GitHub repository is public was not checked (no outside
request); point 6 depends on it.

Grades: **must** change before the build, **should** change, **note**.

| # | Grade | Where | Point |
|---|---|---|---|
| 1 | must | plumbing "stage"; criteria 3, 4 | "herdr shows an agent" is not "an agent is working". An agent whose report is committed sits `done` in an open workspace until the coordinator closes it; by existence the item reads "testing" with its verdict already on the page. |
| 2 | must | plumbing "Under way"; criteria 1, 6 | `PROMOTIONS.md` has more forms than the two named. Read as written, M16b never gets an entry and stands under way for good; a row label is not an item id in 10 of 21 rows. |
| 3 | must | plumbing "linked"; criterion 5 | Linking an entry to every item it names is wrong in the real file: 19 of 60 entries name an `Mnn`, and in 8 of them the item is not the entry's own. An entry that mentions an item under way would leave the backlog. |
| 4 | must | plumbing "steps"; criteria 1, 2, 8 | The file-name grammar and the verdict rule are not decided. Five file shapes fall outside `criteria/review/build/e2eN/fixN`; one test report holds two verdicts for two items; five lines carry a verdict word in capitals and are not the file's verdict, 163 if `FAILED`, `PASSED` or "Verdict" also count. |
| 5 | must | criterion 4 | It cannot be checked by the agent it names: `m27-test` cannot look at the page after it has stopped. No criterion sees "reviewing", "implementing" or "fixing" live. |
| 6 | must | plumbing "outline"; criterion 7 | The claim behind exporting `ROADMAP.md` text does not hold: entries quote household speech and state where the secrets sit unencrypted. Criterion 7's marker test leaves out `PROMOTIONS.md`'s cells and the criteria bodies. herdr's answer carries fields that must not be exported. |
| 7 | must | plumbing "read only"; criterion 8 | The obvious git calls fail the 3 s limit or take the index lock agents need. Measured below. |
| 8 | should | "What the investigation found"; criterion 5 | "62 entries" is `grep -c '^- \*\*'`, which counts two bullets of the Conventions section. The five sections hold 60: Now 2, Next 7, Later 5, Chores 1, Shipped 45. Word criterion 5 as: 15 open entries today, each once, counted per section between its `## ` heading and the next. |
| 9 | should | stage names | Three states get a wrong or empty name. Review committed, no agent: the coordinator is revising the criteria; that is planning, not "waiting". Last test passes, no agent: "to promote". A tag `item-<id>` exists and no promotions entry (M25 from 17:40 to 17:43 today): "promoting". The tag is a third fact; name it. |
| 10 | should | "waiting for the coordinator" | The coordinator's own state is readable: workspace `kronk-claude` reports `working` or `done`. Show it once at the top ("coordinator: working" / "idle, waiting for Drew"), not per item (it cannot be tied to an item). It is the only signal for the coordinator working with no agent, which was most of M26's switch (15:10 to 15:40). |
| 11 | should | Under way | An agent that matches no item under way has no place: an investigator (WORKFLOW's roles table), an agent for a promoted item (a fix after promotion; M26's removal step had none, a later one may), a name the pattern does not parse. List them in one line "other agents: name, status", for agents whose `cwd` is the repo; count the rest without names. |
| 12 | should | Backlog; Drew's words | Both "Now — in flight" entries wait on Drew and belong to no item under way, so the page files ROADMAP's "in flight" under Backlog beside its own "Under way". Keep ROADMAP's section names verbatim as sub-headings and say in one line that Under way lists work items only. Nothing on the page shows what waits on Drew: the "For Drew" lines of `PROMOTIONS.md` are body text. A count per done item ("3 lines for Drew", no text) would answer it within the outline rule. |
| 13 | should | criterion 6 | "Newest first by promotion date" has ties and mixed clocks: three items on 2026-11-24, two on 2026-11-21, four date-only rows on 2026-11-15; 14 rows carry UTC times, headings carry local dates, and M12's row is dated the next day in UTC. The file is appended in order. Decide: Done is `PROMOTIONS.md` in reverse file order, and the date is shown as written. |
| 14 | should | dates; criterion 10 | Step times: git author date, ISO 8601 with offset (today author and committer dates are equal in all 140 files; a rebase moves only the committer's). Date-only values from `PROMOTIONS.md` and `ROADMAP.md` are shown as text: `new Date('2026-11-15')` is UTC midnight and prints 11-14 in this house. Add to criterion 10: one step time and one date-only value checked against the file as displayed. |
| 15 | should | plumbing "reads the working tree" | Undecided: a step file written and not yet committed (it has no date), and a half-edited `ROADMAP.md`. Decide: steps are the files git has a first commit for; an untracked item file is listed as "written, not committed"; `ROADMAP.md` and `PROMOTIONS.md` are read from the tree. Export the branch and short HEAD so a detached HEAD or a rebase in progress is visible, not a silent loss of steps for a minute. |
| 16 | should | plumbing, last bullet | The installed timer runs the working tree's script and production reads the file (M25 review, point 8). The scratch rule covers the build; say it for fix rounds too, add a `schema` number to the file, and state that keys are only added once `item-M27` is deployed. |
| 17 | should | unit files | `Type=oneshot` has no start timeout by default: a hung child blocks every later firing. Add `TimeoutStartSec=30` beside the 5 s limit in code. The two existing exporters are named without `kronk-`; the criteria's `kronk-work-export` follows CLAUDE.md's wording for timers. Either, but `README.md`, `kronk-context.md`, `tests/test_m24_catalog.py` and the feature docs name the existing units; the build greps for lists it must join. |
| 18 | should | criterion 3, "herdr absent" | "Not known" and "no agent" can be told apart only in part. A failed call prints `{"error":{"code":"server_not_running"…}}` on stdout and exits 1 (so does `agent get` for an unknown name). A herdr started with `--session <name>` answers on another socket: the default one is then absent or lists nothing, and "no agent" is wrong. Export the count herdr returned and show "herdr: 2 agents seen" so an empty answer is visible. |
| 19 | should | the page | Text from the repo goes into the page as text (`textContent`), never as markup: ROADMAP entries hold backticks, `*(…)*`, one markdown link and `<room>`-style angle brackets ("The <room> has no…", line 202). Fixture: a title holding `<img onerror>`; the rendered page shows it literally. |
| 20 | should | criterion 10 | "Readable at 390 px" is not checkable. Word it: no horizontal scroll at 390 px and no text clipped (`scrollWidth <= clientWidth` on the body and on each row). |
| 21 | should | criteria 1, 3 | Counts: 29 items is right without M27 (M6 counted, M1b's restore inside M1b); with M27 the export holds 30. Add to criterion 1: on today's tree Under way is exactly M27 and Done holds the other 29. Add to criterion 3's fixtures the cases of points 1, 9 and 11. |
| 22 | note | herdr from a unit | Confirmed: `systemd-run --user --wait --pipe --quiet herdr workspace list` answers the same JSON; PATH there holds `~/.local/bin`; `HERDR_ENV` is unset and not needed; the call makes two connects to `~/.config/herdr/herdr.sock` and no other socket; 2 ms. With a wrong socket path it does not start a server. |
| 23 | note | stage flapping | WORKFLOW treats `idle` or `done` as "finished". An agent waiting on a background job may report `done` between turns; the page would show "waiting" for a cycle. Not measured. Accept it and say so in the feature doc. |
| 24 | note | `/services` | `PAGES` in `services.html` uses absolute hrefs (`/resources`). The new line will open production's `/work` from `/dev/services`, which is 404 until the deploy. Same as the existing lines. |
| 25 | note | later | ROADMAP's "a git worktree per agent" changes what this exporter can see: step files reach `main` only at merge, so a stage would rest on the agent alone. Name the page in that item when it is written. |
| 26 | note | checked and holding | nginx needs no change (`location /` and `/dev/`). `data/` is ignored by git, so the exporter cannot dirty the tree the monthly patch run checks. The model holds for two items at once and for a build landing while another item's agent runs: each item is decided from its own files and its own agents. A ruling commit adds no file (it edits the criteria file), so between a test and a fix the item reads "waiting, test N: verdict", which is true. |

## Must-change points

**1. An agent counts only while it works.** `herdr agent list` returns
every agent in one call with `agent_status` (`idle`, `working`,
`blocked`, `done`, `unknown`), `name`, `cwd` and `workspace_id`; the
coordinator's pane has no `name`. A closed workspace drops out of the list.
Decide: a stage rests on an agent only when its status is `working`;
`blocked` is its own stage text ("testing: agent blocked"), since a
stalled unattended agent is the thing Drew most needs to see; `idle`,
`done` and `unknown` count as no agent, and the page may say "m27-test:
done, workspace still open". With two agents of one item (an old test
pane left open beside a fix agent) the working one decides; two working
is shown as both. Parse the role from the agent's `name`, falling back to
the workspace label: `^(m\d+[a-z]?(-step\d+)?)-(review|build|test|fix)\d*$`,
anything else to point 11. Criterion 3's fake herdr then answers each
status, not only "with and without".

**2. Every form in `PROMOTIONS.md`, and one criterion that shows it.**
The file holds: two tables with 21 rows for 24 items (one row is "M3, M4,
M4b, M1b restore"); row labels with a gloss ("M14 (gate only)", seven rows)
or spaces where the file name has a hyphen ("M21 step 1", two rows); tag
cells that are not `sprint-<id>` (`sprint-M3-M4-M4b`, `sprint-M11-shadow`,
`none` for M6, "tag only" text after the tag for M13 and M20); five `## Mxx
(…), date` headings; three `###` headings, of which `### M16b (2026-11-20):
…` is M16b's only entry (no tag, no deploy) and two are later notes on a
promoted item ("M16 installed", "M26, the removal's first step"); and one
`##` heading that is no item ("After the sprint"). Decide: an entry is a
table row or a `##`/`###` heading that starts with an item id; the ids in
a row label are every `M\d+[a-z]?( step \d)?` before the first parenthesis;
the tag is read from the cell, never built from the id; a second entry for
an id already promoted is a note on it, and the page may list its heading.
M1b has two real promotions (removed, then restored in another row); show
both.

**3. Link an entry to an item only where the entry says it is that
item.** Measured: the `/work` entry names M27 and "after M26"; "Build
workflow" names M5, M16 and M20 as breaches; "Voice accuracy", "SearXNG"
and "MagicMirror" mention items that built part of them; "Refactor sprint"
names 15; "sprint M21" matches no item id (the ids are `M21-step1` and
`M21-step2`); five Shipped entries carry the old numbering ("item 11",
"item 3", "item 2", "item 1", "item 19"), which a loose pattern reads as
M1, M2 and M3; and in four entries the reference is broken across a line
("sprint\n  M21", "work item\n  M24"). Decide: join the entry's lines
first; a link is `work item Mxx`, `sprint Mxx` or `sprint item Mxx` inside
the entry's leading `*( … )*`, plus "and Mxx" there ("sprint M16 and
M16b"); a mention in the body is not a link; `M21` links both steps. With
that rule the open entries link to M27 and M20 only. Also decide what a
linked Shipped entry does: I would show its title on the item's Done row
and not repeat it below, and criterion 6 should say so.

**4. One grammar for file names, one rule for verdicts.** Files outside
the five named shapes: `M6-audit.md` (an item with no criteria file),
`M5-change1.md`, `M3-e2e2-rows.md`, `M1b-restore-criteria.md` and
`-build.md` (added in one commit with `M4b-criteria.md`), and the
`M21-stepN-` prefix. Decide: the id is `M\d+[a-z]?(-step\d+)?`; a suffix
the exporter does not know is listed as a step under its own name and
named in the export's "not read" list, never dropped; M1b's restore files
are steps of M1b. Verdicts in the 49 test reports (`-rows` left out): 17
`Verdict: X`; 7 `**Verdict: ITEM X.**` with text after it on the line; 17
`**Mnn X.**` (one written `**M4B PASSES.**`, upper case, with text after);
4 `**M21 STEP n X.**`; 3 `**Mn NOT DETERMINED.**` (M3's retest and both of
M5's tests, not "M3 and M5 retests"); and `M4b-e2e.md`, which reads
`**RESTORE PASSES.**` on line 11 and `**M4B FAILS.**` on line 13, one
verdict for M1b's restore and one for M4b. The verdict line is line 3 in
39 files, and line 1, 7, 8, 9, 11, 13, 27 or 39 in the rest. The words
PASSES, FAILS and NOT DETERMINED in capitals are on 54 lines: the 49
verdicts, three more in `M4b-e2e.md`, and two back-references in M23's
retests ("First test (FAILS)"). Widened to `FAILED`, `PASSED` or
"Verdict", 212 lines match (pytest output, table headers). Decide: the verdict is the
first line in the first 40 that matches one of the anchored forms; a file
with verdicts for two subjects yields both, each with its subject, and
`M4b-e2e.md` is the real case criterion 8's "two verdict lines" fixture
stands for, with the expected output written into criterion 2.

**5. Criterion 4, reworded.** While the test runs, the test agent shows
`/dev/work` with M27 as "testing, resting on agent m27-test (working)" and
the steps so far. The second half is the coordinator's: after the report
is committed and before the workspace is closed, it fetches
`/dev/api/work` twice a minute apart and records in the promotions entry
that M27 left "testing". The other three agent stages are shown by
criterion 3's fake herdr, and the criterion says so, so nobody reads
"Live" as covering them.

**6. What leaves the repo.** Titles (138 first lines), the four handoff
lines and the verdict words carry no household speech; the nearest are
"the Hey Jarvis wake word", "remove the hot tub feature" and "the whole
shopping-list item is masked". `ROADMAP.md` does: "Voice accuracy" holds
three mis-heard household utterances with a run id; Shipped holds a spoken
request ("put on some aerosmith" from the office) and a reply spoken at
"pause"; "Backups" says the tar with every secret is unencrypted on a
named NAS share; "`/settings`" says editing has no passphrase set. The
page puts that before anyone on the LAN. Decide it, do not assume it: I
would export the full text of the 15 open entries (it is what a backlog
is) and, for the 45 Shipped entries, the title and the leading `*( … )*`
only, which also cuts the exported text from about 27 KB to about 11 KB; and tell
Drew in the report that "Voice accuracy" shows three utterances. Extend
criterion 7: the marker goes into `PROMOTIONS.md`'s fourth and fifth
cells and its entry bodies, into criteria bodies, and into `-audit`,
`-change` and `-rows` files; the real-tree check compares lines of 20
characters or more. From herdr export `name`, `agent_status` and nothing
else: `terminal_title` is model-written from the conversation,
`agent_session` is a session id, and `cwd` names other projects when
Drew has other workspaces open.

**7. Git, as measured on this tree** (502 commits, 140 files, loose
objects only). First-commit dates by one `git log` per file: 2.56 s, and
7.06 s with `--follow`; criterion 8 allows 3 s for the whole run. One
call, `git log --no-renames --diff-filter=A --name-only
--format='@%H%x09%aI' --reverse -- docs/sprint`: 0.03 s, 140 files, no
file listed twice (nothing in the directory was ever renamed or deleted).
`git for-each-ref refs/tags`: 2 ms. Under `strace`, that `git log`,
`for-each-ref` and `rev-parse` each open one file for writing (the one
inspected was `/dev/null`) and touch no lock. `git status --porcelain`
creates and removes `.git/index.lock` (two lock calls traced); an agent's
`git add` or `git commit` in that instant fails with "Unable to create
… index.lock: File exists", and an unattended agent may stop on it.
`git --no-optional-locks status` takes no lock. Decide: the exporter runs
exactly those three read commands, sets `GIT_OPTIONAL_LOCKS=0` in its
environment for good measure, and never runs `status`, `diff`, `add` or
`stash`; criterion 8 names the one-call form, lowers the limit to 1 s,
and its trace asserts that no path ending `.lock` is opened. A rebase, a
detached HEAD or a dirty tree cannot break those commands; they change
what HEAD holds, which point 15 makes visible.

## Counts from the scan

| What | Count |
|---|---|
| Item files in `docs/sprint/` (without `PROMOTIONS.md`, `WORKFLOW.md`) | 138 |
| Items by the grammar of point 4 | 30 (29 and M27) |
| Items with a criteria file | 29 (M6 has none) |
| Review files | 2 (M25, M26); this one is the third |
| Test reports / of them not a test | 50 / 1 (`M3-e2e2-rows.md`) |
| Items whose last file is a fix with no retest | 5 (M9, M12, M16, M18, M16b) |
| Commits that added more than one file here | 3 |
| `ROADMAP.md` entries, open / Shipped | 15 / 45 |
| Entries on one line / spanning lines | 1 / 59 |
| Entries with a blank line before the next entry in the same section | 4 |
| Entries with a nested numbered list (four sub-items, bold titles) | 1 ("Build workflow") |
| Title followed by `*( … )*` / by `( … )` / by a dash / other | 40 / 5 / 12 / 3 |
| `*( … )*` that wraps a line | 9 |
| Titles holding backticks | 4 |
| Entries naming at least one `Mnn` / an old "item N" | 19 / 5 (4 of them with no `Mnn`) |
| Promotion table rows / items they cover | 21 / 24 |
| Rows with a date only / with a UTC time | 7 / 14 |
| `##` item headings / `###` headings / `##` not an item | 5 / 3 / 1 |
| Tags: annotated / lightweight | 4 / 21 |
| Promoted items with no tag of their own name | 6 (M3, M4, M4b share one; M11 `-shadow`; M6 and M16b none) |

## A simpler design

Keep the timer and the file; they are the repo's pattern and nothing
cheaper sees an item under way. Inside it: one `herdr agent list` (drop
`workspace list` and every `agent get`); one `git log` and one
`for-each-ref`; no "planning before criteria" state (no fact supports it:
the coordinator's pane carries no item, and an agent named for an item
with no criteria file has never existed); stage as a pure function of
(step files, tag present, promotions entry present, agents with status)
with the table of its cases in the criteria, so criterion 3 is that table.

## Risks not fenced

- The index lock (point 7) is the only way this item can break another
  agent's work.
- Household text on a LAN page (point 6).
- The timer is installed before the test and runs the tree's script every
  minute from then on, through every later item: a later edit of
  `scripts/work_export.py` or of a file shape it reads is live in
  production's `data/work.json` within a minute. It never fails loud; the
  page goes STALE. Acceptable, and the feature doc should say that a
  STALE `/work` after an edit to `docs/sprint/` means the exporter threw.
- No sound, no HA or MA contact, no notification and no model request
  follow from the design as written.
