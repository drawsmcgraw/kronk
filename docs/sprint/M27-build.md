# M27 build report

Builder: Opus 5.5 (`m27-build`), 2026-11-24, about 18:00 to 18:40 local.
Contract: `docs/sprint/M27-criteria.md` (2740c69), with
`docs/sprint/M27-review.md` beside it. Handoff line: /work shows the
backlog, each work item under way with its stage, and what is done, read
from the files that already hold it.

The exporter was written and tested in a scratch directory
(`--repo`, `--print`, `--out`; the tests pointed at it with
`M27_WORK_EXPORT=<scratch copy>`) and moved into `scripts/` in one step
(`install -m 755`, then `cmp` against the scratch copy) once its tests
passed. Nothing was written under `data/`: `data/work.json` and
`data/dev/work.json` do not exist at the end of the build.

## What changed, file by file

| File | Change |
|---|---|
| `scripts/work_export.py` | New. The host exporter. Runs `git --no-optional-locks` with `GIT_OPTIONAL_LOCKS=0` for `rev-parse` (twice: `--absolute-git-dir --symbolic-full-name HEAD`, then `--short HEAD`; a mode flag applies to every argument after it, so one call cannot give both), the one-call `log` of the criteria, and `for-each-ref refs/tags`; `herdr agent list` once with a 5 s limit, in its own process group, killed on timeout. Reads `ROADMAP.md`, `PROMOTIONS.md`, the first 40 lines of each item file. `stage()` is the stage table as a pure function, with its text in `ROLE_STAGE`, `STAGE_WORDS` and `FILE_STAGE`. Writes `data/work.json` and `data/dev/work.json` (temp file, `os.replace`), or `--out` files, or `--print`. `schema` 1. Never raises on a file it cannot read: the file goes in `not_read`. |
| `orchestrator/work_api.py` | New router: `GET /api/work` = `{source, schema_known, work}`. `source` is `servers.read_source()` (fresh / stale / missing / unreadable, 180 s) plus a schema check: a missing, non-integer or < 1 `schema` is `unknown_schema` with the outline withheld; a higher one is read, with `schema_note`. Runs nothing, calls nothing. |
| `orchestrator/main.py` | `GET /work` serves `static/work.html`; `work_api.router` included; one import. |
| `orchestrator/static/work.html` | New page, in the look of `/resources` (shared `kronk.css`, header, section labels, cards). State line, banner, Under way, Other agents, Backlog, Done, Shipped not linked, Not read. Every repo string set with `textContent`; `` `code` `` and `**bold**` become elements whose content is set as text. Decisions in JSON literals (`SOURCE_BANNER`, `STAGE_CLASS`, `VERDICT_CLASS`, `STEP_LABEL`, `AGENT_TEXT`). `fetch('api/work')` and `static/kronk.css`, both relative. Refreshes every 30 s, keeping a Done row the reader opened. |
| `orchestrator/static/services.html` | One line in `PAGES`: Work, `/work`, no probe. |
| `systemd/kronk-work-export.service`, `.timer` | New. `Type=oneshot`, `TimeoutStartSec=30`, `ExecStart=/usr/bin/python3 -B …/scripts/work_export.py` (`-B`: no `__pycache__` write); every 60 s. Operations header: install, check, one-shot, remove. |
| `tests/test_m27_work_export.py` | New, 87 tests: verdict forms, file grammar, the stage table one row per test, a fake `herdr` on `PATH` for every status and agent case, herdr absent / exit 1 with its JSON error / not JSON / hung 30 s, backlog, links, done, the marker test, the git call spy, a run under `strace`, SIGKILL mid-write, bad files, detached HEAD and a rebase, no git repo, and three tests on the real tree. |
| `tests/test_m27_work_page.py` | New, 22 tests: `/api/work` fresh, stale at 180/181 s, missing, four unreadable files, five unknown schemas, a newer schema; it builds no httpx client and runs no command; the page's literals against the route's states and the exporter's stages; no `.innerHTML`; relative URLs; the `/services` line; the unit files. |
| `docs/features/work-page.md` | New feature doc (what, how, gotchas, operations, blog hooks), with the three notes scope 4 names. |
| `README.md` | Service map row `kronk-work-export (host timer)`; `/work` in the nginx page list. |
| `CLAUDE.md` | Topology paragraph: "the work export for `/work`" in the list of `kronk-*` host timers. |
| `kronk-context.md` | `/work` in the orchestrator's "Serves" line; `work.json` row in the data stores table. |
| `docs/sprint/WORKFLOW.md` | One paragraph after step 8: the page reads the step files and the agent names, so `<item>-<role>` naming and closing finished workspaces are load-bearing; the coordinator is the unnamed pane. |

`ROADMAP.md` and `PROMOTIONS.md` are not touched.

## Criteria

Live checks ran on the dev pair through nginx (`http://localhost/dev/…`),
the dev orchestrator recreated by the override method (below). Times are
local (EST). The real-tree export is run with `--print` or `--out` into
scratch.

**1. Every item.** The export of today's tree holds 30 items; `under_way`
is `["M27"]`; `done` has 30 rows for the other 29 items (M1b has two).
Hand-checked against the files and `git log`:

| Item | Title | Steps, first commit (author date) and verdict | Tag / date as written | Stage |
|---|---|---|---|---|
| M7 | M7 pass criteria: direct dispatch for regex-pinned commands | criteria 11-17 02:23; build 02:57; e2e 03:36 PASSES (line 1) | sprint-M7 / 2026-11-17 08:37 UTC | promoted |
| M9 | M9 pass criteria: narrow the global lock | criteria 11-17 11:00; build 11:09; e2e 13:43 FAILS; fix1 13:46 (last file a fix, no retest) | sprint-M9 / 2026-11-17 18:46 UTC | promoted |
| M16 | M16 pass criteria: backups (config, monthly) and the recordings archive | criteria 11-19 09:42; build 09:49; e2e 10:47 FAILS; fix1 11:34; e2e2 11:36 FAILS; fix2 12:17; e2e3 12:33 FAILS; fix3 13:20; e2e4 13:50 PASSES; fix4 14:30 | sprint-M16 / 2026-11-19 19:30 UTC; note "M16 installed (2026-11-20)" | promoted |
| M3 | M3 pass criteria: harden the read-only exec classifier | criteria 11-15 23:47; build 11-16 00:13; e2e 00:34 FAILS; fix1 11:20; e2e2 17:20 NOT DETERMINED; e2e2-rows 17:20 (same commit; a step, not a test) | sprint-M3-M4-M4b / 2026-11-16 | promoted |
| M21-step1 | M21 step 1 pass criteria: a config directory of plain files | criteria 11-16 22:32; build 23:10; e2e 23:49 FAILS; fix1 11-17 00:28; e2e2 01:06 FAILS; fix2 01:36; e2e3 02:20 PASSES | sprint-M21-step1 / 2026-11-17 07:22 UTC | promoted |
| M21-step2 | M21 step 2 pass criteria: the `/settings` page | criteria 11-17 20:58; build 21:08; e2e 23:16 PASSES | sprint-M21-step2 / 2026-11-18 04:20 UTC | promoted |
| M1b | M1b pass criteria: remove the hot tub feature | criteria 11-15 22:31; build 23:01; e2e 23:44 PASSES (line 27); restore-criteria 11-16 07:31; restore-build 07:46 | two rows: sprint-M1b / 2026-11-15, and sprint-M3-M4-M4b / 2026-11-16 ("M3, M4, M4b, M1b restore") | promoted |
| M4b | M4b pass criteria: the mirror update runs only for the pinned utterance | criteria 11-16 07:31; build 08:20; e2e 09:19 RESTORE PASSES + M4B FAILS; fix1 09:32; e2e2 10:17 PASSES | sprint-M3-M4-M4b / 2026-11-16 | promoted |
| M6 | M6 audit: what is listening on the Kronk host | audit 11-16 22:31 (no criteria file) | no tag ("none") / 2026-11-17 | promoted |
| M13 | M13 pass criteria: bias Whisper toward household vocabulary (dev path only) | criteria 11-17 19:17; build 19:23; e2e 20:53 PASSES | sprint-M13, tag only / 2026-11-18 01:00 UTC (host and HA changes made during the build) | promoted |
| M20 | M20 pass criteria: TTS trial with a go/no-go (the part that needs no clips) | criteria 11-18 08:43; build 13:03; e2e 17:10 PASSES | sprint-M20, tag only / 2026-11-18 | promoted |
| M11 | M11 pass criteria: the music decision in tool_service, built and shadowed | criteria 11-17 06:23; build 07:38; e2e 08:15 PASSES | sprint-M11-shadow / 2026-11-17 13:27 UTC | promoted |
| M16b | M16b pass criteria: Music Assistant's playlists and login database in the config tar; two small chores | criteria 11-20 06:38; build 06:54; e2e 07:31 PASSES; fix1 07:50 | no tag (the `###` heading is its only entry) / 2026-11-20 | promoted |
| M25 | M25 pass criteria: /resources shows real usage | criteria 11-24 14:04; review 14:08; build 14:46; e2e 16:50 PASSES; fix1 17:04; e2e2 17:18 FAILS; fix2 17:26; e2e3 17:40 PASSES | item-M25 / 2026-11-24 | promoted |
| M26 | M26 pass criteria: nothing depends on the Ollama install | criteria 11-24 14:47; review 14:53; build 15:10; e2e 16:02 PASSES; fix1 16:12; e2e2 16:24 PASSES | item-M26, tag only / 2026-11-24; note "M26, the removal's first step (2026-11-24)" | promoted |
| M27 | M27 pass criteria: /work, the work items in a page | criteria 11-24 17:45; review 17:53 | none | implementing, agent m27-build working |

Step times in the export are ISO 8601 with offset (`2026-11-24T17:45:30-05:00`).

**2. Verdicts.** All 49 test reports yield a verdict; the export carries
the line each was read from:

| File | Verdict read (as written when it names a subject) | Line |
|---|---|---|
| `M1-e2e.md` | PASSES | 39 |
| `M10-e2e.md` | PASSES | 8 |
| `M11-e2e.md` | PASSES (`M11`) | 3 |
| `M12-e2e.md` | FAILS (`M12`) | 3 |
| `M12-e2e2.md` | FAILS (`M12`) | 3 |
| `M13-e2e.md` | PASSES (`M13`) | 3 |
| `M14-e2e.md` | PASSES (`M14`) | 3 |
| `M16-e2e.md` | FAILS | 3 |
| `M16-e2e2.md` | FAILS | 3 |
| `M16-e2e3.md` | FAILS | 3 |
| `M16-e2e4.md` | PASSES | 3 |
| `M16b-e2e.md` | PASSES | 3 |
| `M17-e2e.md` | PASSES | 8 |
| `M18-e2e.md` | FAILS (`M18`) | 3 |
| `M18-e2e2.md` | FAILS (`M18`) | 3 |
| `M18-e2e3.md` | FAILS (`M18`) | 3 |
| `M18-e2e4.md` | PASSES (`M18`) | 3 |
| `M19-e2e.md` | PASSES (`M19`) | 3 |
| `M1b-e2e.md` | PASSES | 27 |
| `M2-e2e.md` | FAILS (`M2`) | 3 |
| `M2-e2e2.md` | FAILS (`M2`) | 3 |
| `M2-e2e3.md` | PASSES (`M2`) | 3 |
| `M20-e2e.md` | PASSES | 3 |
| `M21-step1-e2e.md` | FAILS (`M21 STEP 1`) | 3 |
| `M21-step1-e2e2.md` | FAILS (`M21 STEP 1`) | 3 |
| `M21-step1-e2e3.md` | PASSES (`M21 STEP 1`) | 3 |
| `M21-step2-e2e.md` | PASSES (`M21 STEP 2`) | 3 |
| `M22-e2e.md` | PASSES | 3 |
| `M23-e2e.md` | FAILS | 3 |
| `M23-e2e2.md` | FAILS (line 6, "First test (FAILS)", not read) | 3 |
| `M23-e2e3.md` | PASSES (line 6, "Earlier tests (both FAILS)", not read) | 3 |
| `M24-e2e.md` | PASSES | 3 |
| `M24-e2e2.md` | PASSES | 3 |
| `M25-e2e.md` | PASSES | 3 |
| `M25-e2e2.md` | FAILS | 3 |
| `M25-e2e3.md` | PASSES | 3 |
| `M26-e2e.md` | PASSES | 3 |
| `M26-e2e2.md` | PASSES | 3 |
| `M3-e2e.md` | FAILS | 7 |
| `M3-e2e2.md` | NOT DETERMINED (`M3`) | 3 |
| `M4-e2e.md` | FAILS | 9 |
| `M4-e2e2.md` | PASSES | 13 |
| `M4b-e2e.md` | RESTORE PASSES; M4B FAILS | 11; 13 |
| `M4b-e2e2.md` | PASSES (`M4B`) | 9 |
| `M5-e2e.md` | NOT DETERMINED (`M5`) | 3 |
| `M5-e2e2.md` | NOT DETERMINED (`M5`) | 3 |
| `M7-e2e.md` | PASSES (`M7`) | 1 |
| `M8-e2e.md` | PASSES (`M8`) | 3 |
| `M9-e2e.md` | FAILS (`M9`) | 3 |

Each matches what the file says when read (the same lines `grep -n -m3
-E 'PASSES|FAILS|NOT DETERMINED'` over the first 40 lines finds). The forms
add up as the review counted: 17 `Verdict: X`, 7 `**Verdict: ITEM X.**`,
17 `**Mnn X.**`, 4 `**M21 STEP n X.**`, 3 `NOT DETERMINED`, and `M4b-e2e.md`.
`M3-e2e2-rows.md` is a step named `e2e2-rows`, carries no verdict field,
and is in "not read". Fixtures: no verdict line; "First test (FAILS)" and
"The verdict: PASSES" in a sentence; `**M9 FAILS.**` and `Verdict: PASSES`
in the middle of a line; an indented `Verdict:`; a verdict on line 41 (not
read) beside one on line 40 (read); two subjects with a repeated subject
(first per subject kept). `tests/test_real_tree_every_test_report_has_a_verdict`
pins M4b-e2e's two verdicts with their lines.

**3. Stage is the table.** One test per row in
`tests/test_m27_work_export.py`: an agent `working` for each role (4), one
`blocked` ("testing, agent m9-test BLOCKED", `blocked: true`), two
`working` (both shown), `idle`/`done`/`unknown` deciding nothing (3), the
six file rows plus "verdict not read" and "files written, not committed",
each with herdr not reachable and with no agent; tag without promotion is
"promoting" with an agent working; a promotions entry is "promoted". With
a fake `herdr` on `PATH` (`test_live_agents_through_a_fake_herdr`): the
coordinator's unnamed pane, an item's test agent working, its build agent
`done` ("m9-build: done, workspace still open"), an agent for a promoted
item, an agent for no item, an unparseable name, an agent whose `cwd` is
another repo (counted in `elsewhere`, its name nowhere in the export);
each coordinator status; zero agents ("herdr: 0 agents seen", known);
two unnamed panes (coordinator "not known"). herdr absent, exit 1 with
`{"error":{"code":"server_not_running",…}}`, not JSON, no agents list:
`known: false`, the reason, stages from the files. herdr sleeping 30 s:
the script, run as a process, writes its file in 5.0 to 5.1 s with
"herdr agent list: no answer within 5 s" (`test_hung_herdr_…`, asserts
`4.5 <= took < 10`).

**4. Live, testing.** The test agent's. What the build saw live in its own
stage, rendered on `/dev/work` at 18:19: the top line "export made Nov 24,
06:19 PM, 2 s ago, from main @ 2740c69 · coordinator: idle · herdr: 2
agents seen"; Under way: M27, "M27 pass criteria: /work, the work items in
a page", stage **implementing**, "implementing, agent m27-build working",
the handoff line, steps "criteria Nov 24, 05:45 PM M27-criteria.md" and
"review Nov 24, 05:53 PM M27-review.md", and the ROADMAP entry "`/work`: the
work items in a page" under it. The test agent must be named `m27-test`
(or `m27-test2`, …) for its row to say "testing": the role pattern has
`test`, not `e2e`.

**5. Backlog.** Today's `ROADMAP.md`: Now 2, Next 7, Later 5, Chores 1 =
15 open entries, counted per section between its `## ` heading and the
next. On the page: "Now — in flight" 2, "Next — agreed, not started" 6,
"Later — wanted" 5, "Chores" 1, and the 15th ("`/work`: the work items in
a page", linked to M27) under M27 in Under way. Each once, in file order,
section names verbatim, text joined. Fixtures: an entry on one line; a
blank line between entries; the nested numbered list; a `*( … )*` that
wraps a line; a title with backticks; the Conventions bullets (not
entries); a column-0 paragraph after a blank line ends an entry; a
section the exporter does not know ("Parked") is not exported and is
named in "not read".

**6. Done.** `PROMOTIONS.md` in reverse file order (30 rows):

| # | Item | Tag | Date as written | For Drew | Notes | Shipped entry on the row |
|---|---|---|---|---|---|---|
| 1 | M25 | item-M25 | 2026-11-24 | 2 | | /resources shows real usage |
| 2 | M26 | item-M26 (tag only) | 2026-11-24 | 2 | M26, the removal's first step (2026-11-24) | Kronk's own ROCm libraries |
| 3 | M24 | item-M24 | 2026-11-24 | 1 | | Reclaim storage and memory; Talkie kept |
| 4 | M23 | sprint-M23 | 2026-11-21 | 0 | | The queue incident |
| 5 | M22 | sprint-M22 | 2026-11-21 | 0 | | The basement hand-off |
| 6 | M16b | no tag | 2026-11-20 | 0 | | Backups |
| 7 | M16 | sprint-M16 | 2026-11-19 19:30 UTC | 1 | M16 installed (2026-11-20) | Backups |
| 8 | M20 | sprint-M20 (tag only) | 2026-11-18 | 1 | | |
| 9 | M18 | sprint-M18 | 2026-11-18 17:51 UTC | 1 | | Upgrade cadence |
| 10 | M21-step2 | sprint-M21-step2 | 2026-11-18 04:20 UTC | 0 | | `/settings` page and live config directories |
| 11 | M13 | sprint-M13 (tag only) | 2026-11-18 01:00 UTC (host and HA changes made during the build) | 0 | | |
| 12 | M12 | sprint-M12 | 2026-11-18 00:13 UTC (2026-11-17 19:13 local) | 1 | | |
| 13 | M9 | sprint-M9 | 2026-11-17 18:46 UTC | 1 | | |
| 14 | M8 | sprint-M8 | 2026-11-17 16:05 UTC | 0 | | |
| 15 | M11 | sprint-M11-shadow | 2026-11-17 13:27 UTC | 0 | | |
| 16 | M14 | sprint-M14 | 2026-11-17 11:20 UTC | 1 | | |
| 17 | M19 | sprint-M19 | 2026-11-17 09:55 UTC | 1 | | |
| 18 | M7 | sprint-M7 | 2026-11-17 08:37 UTC | 1 | | |
| 19 | M21-step1 | sprint-M21-step1 | 2026-11-17 07:22 UTC | 0 | | `/settings` page and live config directories |
| 20 | M6 | no tag ("none") | 2026-11-17 | 0 | | |
| 21 | M2 | sprint-M2 | 2026-11-17 03:25 UTC | 0 | | |
| 22 | M5 | sprint-M5 | 2026-11-17 00:02 UTC | 0 | | |
| 23–26 | M3, M4, M4b, M1b | sprint-M3-M4-M4b | 2026-11-16 | 0 | | |
| 27 | M1b | sprint-M1b | 2026-11-15 | 0 | | |
| 28 | M1 | sprint-M1 | 2026-11-15 | 0 | | Financial expert, phase 1 |
| 29 | M10 | sprint-M10 | 2026-11-15 | 0 | | |
| 30 | M17 | sprint-M17 | 2026-11-15 | 0 | | |

"For Drew" is counted as each "For Drew" / "for Drew" not followed by
"'s" in the entry (the row's fourth and fifth cells; a heading entry's
body), plus its notes. Hand count: M24 1 (line 129, `**For Drew:**
Ollama's…`); M25 2 (line 274, "For Drew to look at."; line 276, `**For
Drew:** what the page…`); M26 2 (line 189, `**For Drew, the removal, in two
steps**`; line 199, `**For Drew:** a production Whisper restart…`; its note
has none). Excluded by the `'s` rule: M6's "wait for Drew's go" and the
After-the-sprint "for Drew's spoken test" (not an entry anyway). The rows
counted 1 by the rule: M7, M19, M14 ("Known regression, for Drew:"), M9,
M12, M18, M20, M16. Shipped entries linked to an item appear on its row
only; the other 36 follow under "Shipped, not linked to a work item",
title and leading `*( … )*` only.

**7. Links.** Open entries: "Kronk's voice: TTS trial" → M20 (`*(sprint
M20, …)*`), "`/work`: …" → M27 (`*(work item M27, ordered 2026-11-24; after
M26)*`: "after M26" is not a link); no other open entry links. M20 is
promoted, so that entry stays in the backlog. "Refactor sprint" (lead
`2026-11-15 to 2026-11-21`) links nothing; "Model bench" (`item 11`),
"Timers" (`item 3`), "Verbose error reporting" (`item 2`), "Docs
reorganization" (`item 1`) and "Leaked thinking never reaches the user"
(item 19 in its body) link nothing. Shipped links found: M25, M26, M24,
M23, M22, M16 and M16b (Backups, "sprint M16 and M16b"), M18, M21 →
M21-step1 and M21-step2 (`/settings`, a reference broken across a line),
M1 (Financial expert, "removed 2026-11-15, sprint M1"). Fixtures: "sprint\n
M7", "work item\n  M8", "sprint M2 and M2b", a body mention of "sprint M2",
"item 11", and `resolve_links` for a bare step id.

**8. Nothing but the outline leaves the repo.**
`test_marker_in_every_body_is_nowhere_in_the_export` puts a marker in a
criteria body (before and after the handoff line), a review, build, test
report body and its verdict line, `-audit`, `-change1`, `-e2e2-rows` and
`-fix1` files, `PROMOTIONS.md`'s fourth and fifth cells, a `###` heading's
body, a `##` heading's body (on the `- Tag` line too), a note's body,
Shipped bodies, and herdr's `terminal_title`, `agent_session`,
`foreground_cwd` and `cwd`; the marker is nowhere in the export, and the
title and handoff are. `test_only_name_and_status_leave_herdr` does the
same for herdr alone. On the real tree, `test_real_tree_outline_only`:
every line of 20 characters or more from an item file's body (line 1 and
the handoff paragraph left out), `PROMOTIONS.md`'s non-heading lines and
cells 4 and up, and the Shipped section's lines, against every string in
the export except the fields that carry the outline on purpose (`title`,
`handoff`, `lead`, and the open entries' `text`): none found. Without
that exclusion it finds nine lines, all of them `PROMOTIONS.md` body
lines that repeat a handoff line (WORKFLOW: the handoff line is "the first
words of the promotions entry"). A tag cell with no backticked tag is
exported only when it is one bare word ("none"). What leaves, by file:
`ROADMAP.md`'s 15 open entries whole (the review's point 6: "Voice
accuracy" shows three mis-heard household utterances and a run id; that
is Drew's call to know about, below).

**9. Safe to run every minute.** Under `strace -f` on the real tree, real
herdr, `--out` into scratch (the final script, 18:23):
execve of exactly `git --no-optional-locks rev-parse --absolute-git-dir
--symbolic-full-name HEAD`, `git --no-optional-locks rev-parse --short
HEAD`, the `git log` of the criteria, `git --no-optional-locks
for-each-ref refs/tags --format=%(refname:short)`, `herdr agent list`;
paths containing `.lock`: 0; opens for writing other than `/dev/null`:
the two `.work.json.*` temp files; renames: the two temp files onto their
targets; unlinks: 0; sockets: `AF_UNIX` to `~/.config/herdr/herdr.sock`,
twice, from the herdr process, and no other. The same, on a fixture tree
with a fake herdr, is a suite test
(`test_under_strace_no_lock_no_other_write_no_socket`, asserts no socket
at all). `test_git_commands_are_the_three_and_lockless` spies on every
command: git subcommands are `{rev-parse, log, for-each-ref}`, each with
`--no-optional-locks` and `GIT_OPTIONAL_LOCKS=0`, the `log` argv exactly
the criteria's. SIGKILL in mid-write leaves the old file whole
(`test_kill_in_the_middle_…`). Run time on the real tree: 0.09 to 0.10 s
wall (five runs, `/usr/bin/time`), `run_s` 0.068 s inside. It does not
fail on: an empty file ("empty"), a binary file ("binary"), a criteria
file with no title line ("no title line"; the item takes the next titled
file's title), a missing `ROADMAP.md`, a binary `PROMOTIONS.md`, a file
name that is not an item's ("not an item file name"), a detached HEAD
with `.git/rebase-merge` present (`detached: true`, `rebase: true`, steps
as of that HEAD), a directory that is not a git repo (`repo.error`,
`errors`, every file "written, not committed"), an untracked step file
(listed under `uncommitted`, `committed_at: null`; an item with only
untracked files is under way as "planning: files written, not
committed").

**10. Staleness.** Tests: fresh; stale at 400 s with the outline still
returned; 180 s fresh and 181 s stale; missing; four unreadable files
(bad JSON, a list, no `ts`, a non-number `ts`); five unknown schemas
(absent, 0, `"1"`, `true`, 1.5); schema 2 read with its note. Live on
dev, override method, each fetched from `/dev/api/work` and rendered:

- stale (`ts` set 400 s back): API `stale 400.0 | stale: work.json was
  written 400 s ago (the exporter runs every 60 s; older than 180 s is
  stale)`; page banner "STALE: work.json was written 402 s ago (the
  exporter runs every 60 s; older than 180 s is stale). Shown below: the
  last known state.", with M27 and the 30 Done rows below it.
- missing: API `missing | work.json does not exist: the host exporter
  (systemd/kronk-work-export.timer) is not installed or has not run`;
  page "MISSING: …" the same, "No outline to show.", sections empty.
- unknown schema (`"schema": "one"`): API `unknown_schema | work.json has
  schema 'one', which this orchestrator does not know (it reads schema
  1)`; page "UNKNOWN SCHEMA: …" the same, no outline.

Unreadable was checked by the route's tests only, not live.

**11. The page.** Rendered in headless snap Firefox 159 driven by
`geckodriver` (WebDriver, `127.0.0.1:4459`), a fresh profile in
`~/snap/firefox/common/m27-render` with a `user.js` sending everything but
loopback to a dead proxy (port 9) and update, telemetry, safebrowsing and
captive-portal checks off. Text read from the DOM 4 s after load; then
`/dev/api/work` fetched. A comparison script checked, field by field,
every Under way card and every Done row against the API: 31 item rows,
144 step rows (label, time as displayed, each verdict, file name), 51
verdicts, 31 stages, the backlog per section, the 36 unlinked Shipped
entries, the 5 "not read" lines: **0 mismatches**, at 1280 px and at 390
px. Displayed against the file: M27-criteria's step time
`2026-11-24T17:45:30-05:00` shows "Nov 24, 05:45 PM"; M1's date-only
`2026-11-15` shows "2026-11-15" (not the 14th), M25's `2026-11-24` shows
"2026-11-24". Requests the page made (Resource Timing):
`/dev/work`, `/dev/static/kronk.css`, `/dev/api/work`, and the browser's own
`/favicon.ico`. "undefined" and "NaN": none. "null": once, inside the
ROADMAP text "`2>/dev/null` and `2>&1` are accepted" (the MagicMirror
entry); nothing the page wrote. Markup: an export made from a fixture tree
whose criteria title is `M99 pass criteria: <img src=x
onerror="document.title='pwned'">`, whose handoff line is
`<script>document.title="pwned2"</script> stays text.`, and whose ROADMAP
entry holds `<b>bold</b>`, `` `<img src=x onerror=alert(2)>` `` and
`<room>`: all shown literally; the document has 0 `img` elements and its
title stayed "Kronk — Work". The same render showed a blocked agent as
"testing, agent m98-test BLOCKED" (red) and the coordinator as "working".
At 390 px: Firefox will not size a window under 500 CSS px (a
`devicePixelsPerPx` scale did not lower it), so the page was loaded again
inside a same-origin `<iframe>` 390 px wide on `/dev/work` and read from
its document: `innerWidth` 390, `documentElement` and `body`
`scrollWidth` 378 = `clientWidth` 378 (390 less the scrollbar), and
`scrollWidth <= clientWidth` on every card, Done row, step row, entry,
meta line, item head, stage line, banner and summary (0 clipped). At
1280: 1268 = 1268, 0 clipped. The browser and geckodriver were killed at
the end of each run (`pgrep -a -f 'firefox|geckodriver'` empty after
each); the profile directory is removed (below).

What of the page's script no test runs: all of it. The suite has no
JavaScript runtime. `tests/test_m27_work_page.py` reads the five JSON
literals and checks their keys against the route's states and the
exporter's `STAGE_WORDS`, and checks the script as text (no `.innerHTML`,
`.outerHTML`, `insertAdjacentHTML` or `document.write`; `fetch('api/work')`;
no absolute `href`, `src` or `fetch`). The functions `fill`, `stepLabel`,
`verdictText`, `mdText`, `fmtTime`, `verdictTrail`, every `render*`
function and the 30 s refresh ran only in the renders above. The stage and
its words are computed in Python (`stage()`), so the decision the page
most needs to get right is tested; the page prints `stage.words` and
`stage.detail` as given.

**12.** `/dev/api/work`, 20 requests with the real-tree export mounted:
median 5.1 ms, max 7.1 ms, 62,573 bytes each. `journalctl --user` for
`llama-gemma4-e4b`, `llama-devstral-q4`, `llama-talkie` over the window
(18:20:31 to 18:20:35): 0 lines of `POST /v1/chat/completions`,
`params_from_` or `launch_slot_` each. The route's test builds no httpx
client and runs no `subprocess.run`/`Popen`.

**13. Docs.** `docs/features/work-page.md` (what, how, gotchas,
operations, blog hooks; it says that a STALE `/work` after an edit under
`docs/sprint/` means the exporter threw, that an agent between turns can
read `done` for a cycle, and that the worktree-per-agent item changes what
the exporter can see); `README.md`'s service map and page list;
`docs/sprint/WORKFLOW.md`'s paragraph. Also `CLAUDE.md`'s topology
paragraph and `kronk-context.md`, the other lists the grep found.

**14. Suite.** `./scripts/run_tests.sh`: see "Suite" at the end.

**15. Nothing installed, started, stopped or deployed.** No unit file
copied to `~/.config/systemd/user/`, no `systemctl` beyond `list-*`/
`journalctl` reads, no timer touched. `data/` not written (the exporter
ran only with `--print` or `--out` into scratch; `ls data/work.json
data/dev/work.json`: both absent). herdr: `agent list` and `--help` only
(and the exporter's own `agent list`). No chat request; no request to
production; no request to port 8002; `/dev/services` not loaded. Only the
dev orchestrator was recreated (override, three times, `--no-deps`), then
restored with the plain build line. HA, MA untouched; no sound; no
notification. Production `kronk-orchestrator-1` "Up 38 minutes" at the end
(as it was at the start of the live work).

## Live on dev: the override and the restore

Override file in scratch, dev orchestrator only:

```yaml
services:
  orchestrator-dev:
    environment:
      - WORK_PATH=/m27/work.json
    volumes:
      - <scratch>/live:/m27:ro
```

`docker compose -f docker-compose.yml -f <scratch>/override.yml --profile
dev up -d --build --no-deps orchestrator-dev` (rebuilt once more after a
page fix). The scratch `work.json` was written by
`python3 scripts/work_export.py --out <scratch>/live/work.json` just before
each render (the fixture and stale cases by hand, as above).

Restored at 18:20:45 (container created `2026-11-24T23:20:45Z`) with `docker compose --profile dev up -d --build
tool_service-dev orchestrator-dev`. `docker inspect
kronk-orchestrator-dev-1` after: mounts `kronk-context.md:/kronk-context.md`,
`litellm/config.yaml:/litellm-config.yaml`, `kronk-config/dev:/config`,
`data/dev:/data`; no `/m27` mount; 0 `WORK_PATH` entries in its
environment. `/dev/api/work` then answers `missing` (no timer yet).
`tool_service-dev` was not recreated ("Up 6 hours").

## The export on the real tree

81,877 bytes written (`indent=1`), 62,573 bytes as `/api/work` serves it.
0.09 to 0.10 s per run. "Not read" today:

- `M1b-restore-build.md`: step 'restore-build' is not a known step (listed under its name)
- `M1b-restore-criteria.md`: step 'restore-criteria' is not a known step (listed under its name)
- `M3-e2e2-rows.md`: step 'e2e2-rows' is not a known step (listed under its name)
- `M5-change1.md`: step 'change1' is not a known step (listed under its name)
- `M6-audit.md`: step 'audit' is not a known step (listed under its name)

## What I did not do

- Install, enable or run the timer; write `data/`. The coordinator's
  (below).
- Run criterion 4 as the test agent; render production (it has no `/work`
  until the deploy).
- Check `unreadable` live (route tests only).
- Check that `herdr` is on the unit's `PATH` under systemd. The review
  (point 22) saw `~/.local/bin` there with `systemd-run`; I may not start
  units. If it is not, the first export says "herdr is not on PATH" and the
  page says agent state is not known.
- Remove the stray temp file a SIGKILL would leave: an exporter killed
  mid-write leaves one `.work.json.*` beside the target in `data/` (as the
  other two exporters do). Nothing reads it.

## Found that the criteria did not foresee

1. **The coordinator cannot be read by its workspace label.** `herdr agent
   list` carries `workspace_id` (`w1`) but no label; `kronk-claude` is in
   `workspace list`, which the criteria leave out. The exporter takes the
   coordinator to be the one agent in this repo with no `name` (today
   `w1:p1`, `done` → "coordinator: idle"). Two unnamed panes in the repo
   make it "not known", none "not seen". Named in the feature doc and
   WORKFLOW's paragraph.
2. **The test agent's name decides criterion 4.** The role pattern takes
   `test`, not `e2e`: a test agent named `m27-e2e` lands in "Other agents".
3. **A Shipped entry about a removal links to the item that removed it.**
   "Financial expert, phase 1" leads with `*(2026-07-10; removed
   2026-11-15, sprint M1)*` and so shows on M1's Done row. That is what the
   rule says; it reads oddly.
4. **Handoff lines are in `PROMOTIONS.md`'s body too**, by WORKFLOW's rule
   that the handoff is the first words of the promotions entry. The
   criterion 8 real-tree check has to leave out the fields that carry the
   outline on purpose, or it reports nine false leaks.
5. **"null" is in the repo's own text** (`2>/dev/null` in an open ROADMAP
   entry). Criterion 11's "nothing reads null" holds only read as "nothing
   the page wrote".
6. **A last step of an unknown kind** (`-restore-build`, `-audit`) has no
   row in the stage table. The exporter says "waiting for the
   coordinator: last file <name>". Today every such item is promoted, so
   it is never shown.
7. **Headless Firefox has a 500 CSS px window floor**; 390 px was checked
   inside a same-origin iframe (above). A tester using the window rect
   alone would get 500 and believe it is 390.
8. **geckodriver ignores `moz:firefoxOptions.prefs` when `-profile` is
   given**: my first renders ran without the dead-proxy prefs (the page's
   own requests were all loopback; the browser's background traffic in
   those runs is not known). From the 18:17 run on, the prefs went into
   the profile's `user.js` and were seen to apply (`devicePixelRatio`
   moved when a scale pref was set). M25's renders may have had the same
   gap.
9. `kronk-context.md` still says `/api/servers` "probes LiteLLM `/health`";
   false since M25. Not changed (outside this item).

## Install and deploy, for the coordinator

Before the test (criteria: the coordinator installs the timer):

```bash
cp systemd/kronk-work-export.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kronk-work-export.timer
systemctl --user list-timers kronk-work-export.timer
journalctl --user -u kronk-work-export.service -n 5 --no-pager
python3 -c 'import json; d=json.load(open("data/work.json")); print(d["herdr"], d["under_way"], d["errors"])'
#   herdr "known": true means herdr was on the unit's PATH.
```

Remove:

```bash
systemctl --user disable --now kronk-work-export.timer
rm ~/.config/systemd/user/kronk-work-export.{service,timer}
systemctl --user daemon-reload
```

(`data/work.json` and `data/dev/work.json` stay until removed by hand;
the page then reads them as stale.)

Deploy: tag `item-M27`, `scripts/deploy_tag.sh plan item-M27`, `deploy
item-M27`. No nginx, compose or HA/MA change. Production gets `/work` and
`/api/work` with the orchestrator image; it reads `/data/work.json`,
which the timer writes into `data/` from the working tree. Name the test
agent `m27-test`.

## Questions

None blocking. One for Drew, through the coordinator (the review's point
6): the backlog section shows the 15 open ROADMAP entries whole on a LAN
page, and "Voice accuracy" holds three mis-heard household utterances and
a run id.

## Suite

`./scripts/run_tests.sh -q -p no:warnings` on the final tree (before:
`7504 passed, 3 skipped, 54 xfailed`), last lines:

```
......................................                                   [100%]
7613 passed, 3 skipped, 54 xfailed in 108.08s (0:01:48)
```

109 new tests (87 + 22). Ten mutants of the exporter were run against the
two M27 test files: eight killed (a `git status` call, `terminal_title`
exported, "for Drew's" counted, `done` deciding a stage, a body mention
linking, a 50 s herdr limit, Done not reversed, the tag row dropped); two
survived and are equivalent (an entry body added to an internal dict
that is never copied out; `^` dropped while `re.match` still anchors). A
third, unanchored `search`, was killed after two mid-line cases were added
to the verdict fixtures.

The browser profile `~/snap/firefox/common/m27-render`, which this build
created, was removed at the end; `pgrep -a -f 'firefox|geckodriver'`
printed nothing.
