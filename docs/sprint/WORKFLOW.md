# Work-item workflow: coordinator, build agent, test agent

How the refactor sprint (2026-11-15 to 2026-11-21, items M1 to M23) was
run, revised 2026-11-24 (last section), written down so a later session
runs the next item the same way. Use it when Drew orders a piece of work
as a work item, or asks for "an Opus agent" to build or investigate
something.

"Sprint" was the name for that one run through a long backlog and is not
used for new work. A piece of work run this way is a work item. The
directory `docs/sprint/`, the item numbers and the old
`sprint-<item>` tags keep their names; new tags are `item-<item>`.

A small fix that needs neither a build agent nor a test agent does not
need this: the session makes it inline under CLAUDE.md's "Definition of
done". The rule below that the coordinator writes no code starts when an
item's criteria file is committed and ends when it is recorded as
promoted (`work.py promote`). A
fix to an item under way is never the inline kind.

## Roles

| Role | Model | Flags | Where |
|---|---|---|---|
| Coordinator | the session Drew talks to | | its own herdr pane |
| Build agent, fix agent, investigator | Opus 5.5 (`claude-opus-5-5`) | `--effort high --dangerously-skip-permissions` | its own herdr workspace |
| End-to-end test agent | Fable 5.2 (`claude-fable-5-2`) | `--effort medium --dangerously-skip-permissions` | its own herdr workspace |
| Design reviewer (large items, and every tier 3 item) | Fable 5.2 (`claude-fable-5-2`) | `--effort medium --dangerously-skip-permissions` | its own herdr workspace |

The coordinator investigates, writes the criteria and the briefs, rules on
findings, deploys, and reports to Drew. It does not build the item, and it
writes no code into the repo at any point, the time after the last test
included. Its own commits are the criteria, rulings, the promotion's checks
and docs. The test agent is always a different agent from the builder, and
a fresh one each round, so its reading is not anchored on the builder's.

One agent runs at a time. They share one working tree.

## Steps for one item

1. **Investigate.** Before any criteria, the coordinator checks the
   premise of the order against the running system: the containers and
   units (CLAUDE.md, "Verify, don't trust the docs"), the logs, the traces,
   the incident doc. The criteria file states what was checked and what
   was found. A premise that does not hold goes back to Drew before
   anything is built.
2. **Pass criteria.** The coordinator writes `docs/sprint/<item>-criteria.md`:
   source (Drew's words, the incident doc), what the investigation found,
   scope, what is out of scope, numbered pass criteria, and any condition
   Drew set, quoted with its date. The criteria file is the item's plan
   doc in the sense of `ROADMAP.md`'s docs lifecycle; the feature doc in
   `docs/features/` is still written or updated when the item ships, and
   a criterion says so. It ends with a handoff line: one plain
   sentence that becomes the subject of the build commit and the first
   words of the promotions entry. Commit it.

   The coordinator decides plumbing (names, storage, which module, which
   test) and records each decision in the criteria file. A question goes to
   Drew only when two readings of his order lead to different features, or
   when the step is his by rule (CLAUDE.md, "Ground rules"). It is asked
   in plain words with a recommendation.
3. **Design review, for a large item.** Large means any of: it touches the
   pipeline, routing, the shims or the agent loop; it deletes or moves
   data; it installs a host unit or timer; it needs an HA or MA change;
   the coordinator expects more than one build session. A fresh reviewer
   reads the criteria and the code they name, and writes
   `docs/sprint/<item>-review.md`: criteria that cannot be checked,
   criteria that contradict each other or Drew's conditions, cases the
   scope leaves undecided, a simpler design if it sees one. It changes
   nothing else. The coordinator checks each claim against the code and
   revises the criteria before the build. A small item skips this step.
4. **Build.** A build agent in a new workspace. Its brief names the files
   to read first, the jobs in order, the guard rails (below), and the
   report it writes: `docs/sprint/<item>-build.md`. It commits its work
   and its report and leaves the tree clean.

   One build session has one narrow goal and one clear stop. An item with
   separable parts is split into steps, each with its own criteria, build
   and test (M21 step 1 and step 2 were run this way). Agents ask Drew nothing; a
   question goes in the report and the agent stops.
5. **Test.** A test agent in a new workspace. Its brief gives it the
   criteria and the build report, not the coordinator's opinion of the
   build. It tests on the dev pair and by reading production; it writes
   `docs/sprint/<item>-e2e.md` with a verdict per criterion and commits it.
6. **Fix loop.** On a failure: a fix agent (`<item>-fix1.md`), then a new
   test agent (`<item>-e2e2.md`). At most three failed fix rounds, then
   stop and report to Drew. The coordinator may rule on a finding instead
   of sending it back (ship a feature switched off, accept a stated
   limit); the ruling goes in the criteria file and the next brief.

   A fix, however small, is still a fix agent's, never the coordinator's.
   Whether a fresh test agent checks it depends on the item's tier
   (below). A should-fix finding that is not worth a round is left over,
   and it is recorded as a leftover (`work.py matter add leftover`).
7. **Promote.** The coordinator runs the suite, tags `item-<item>`, runs
   `scripts/deploy_tag.sh plan <tag>` then `deploy <tag>`, verifies live
   (CLAUDE.md, "Definition of done"), writes the checks it made to
   `docs/sprint/<item>-promote.md`, commits it, and records the promotion
   with `work.py promote <item> --tag … --deployed yes|no` (the checks'
   path is its `--verified`). A deploy with no item is recorded with
   `work.py promote --label "…" --tag … --verified PATH`. What is left
   over goes in as leftovers and decisions. If Drew orders something
   shipped that no test agent saw, `--untested` names it. Since work
   item M34 (2026-11-26) `/work` shows promotions from the database;
   `docs/sprint/PROMOTIONS.md` is frozen and nothing is added to it.
8. **Close.** Each agent's workspace is closed once its report is
   committed. Only the coordinator's workspace stays.

## How much testing an item gets (Drew, 2026-11-24)

The suite (`./scripts/run_tests.sh`, about two minutes) runs on every
change, by every agent, in every tier. What is sized to the item is the
work around it. The coordinator names the tier in the criteria file, by
the widest thing the item touches.

| Tier | What it is | Review | Test | After a fix round |
|---|---|---|---|---|
| 1 | Tooling, docs, a config or model swap; nothing a request to production runs through | none | one test agent | no second test agent: the coordinator reads the fix's diff against the ruling and the suite's result, and says so in the promotions entry |
| 2 | A page or a feature on production that is not in tier 3 | when step 3 calls it large | one test agent | a fresh narrow test only if the first test found a blocker; otherwise as tier 1 |
| 3 | The pipeline, routing, the shims, the agent loop, voice, or anything that moves, migrates or deletes stored data | always | a test agent | always a fresh test agent, narrow: the diff, the criteria it touches, the suite |

In tier 1 and 2 the coordinator grades a test's findings itself; the
brief's "blocker" list is a guide, and a finding with no effect in use is
a should-fix whatever the list says.

**The watch on this rule.** Drew: "let's be observant going forward. If
we create a lot of bugs, we'll need to go back to full testing for
everything". An *escape* is a fault found after promotion, in an item
that was promoted under tier 1 or 2, that a second test round or a design
review of that item would plausibly have caught. Each one is recorded
where the item's promotion is recorded, with what would have caught it.
At the second escape within any ten promoted items, or at the first one
that broke voice, chat or stored data, the coordinator stops using the
tiers, runs every item as tier 3, and tells Drew in the same report. Only
Drew turns the tiers back on. The count of escapes is part of the
start-of-session report.

Why: M28 (a 730-line helper that no production request touches) got a
build, a full test, a fix round and a second full test; its two
"blockers" were a workspace left behind when a disk is full and a
gitignored cache file.

`/work` (work item M27, `docs/features/work-page.md`) shows this workflow's
state, read every minute from the step files in `docs/sprint/`, the
promotions log, git, and the names and statuses `herdr agent list` gives.
Two habits are therefore load-bearing: an agent is named `<item>-<role>`
(`m27-build`, `m27-test2`, `m27-fix1`; review, build, test or fix), or it
is shown under "Other agents" and not on its item; and a finished agent's
workspace is closed, or the item shows it as "done, workspace still
open". The coordinator's own pane is the one unnamed agent in the repo;
it is shown as "coordinator: working" or "idle".

## Start of every working session

Run `python3 scripts/work.py resume` first (work item M33,
`docs/features/work-state.md`). The state of every item, the ordered
queue, the leftovers and the decisions owed to Drew are in one SQLite
database outside the repo (`~/.local/state/kronk-work/work.db`), written
only through `scripts/work.py`; `scripts/agent.py` records each agent's
step itself. `resume` prints, in this order: what `check` found (rows
against git, herdr and the files), what is under way with its computed
stage and agent, the queue, the decisions Drew owes, the open leftovers,
the escape count within the last ten promoted items, the tree, the agents
`agent.py` knows, and the last five writes.

Settle each `check` finding with the command it prints before anything
else. Then do the two sweeps `resume` does not cover: in Grafana, the
voice runs marked as corrected since the last session
(`docs/features/voice-capture.md`); and every `ROADMAP.md` entry that
waits on something outside the repo (an upstream release, a merge),
checked against its source, saying what moved. Report to Drew what needs
a decision, with the escape count.

Each open leftover is fixed (inline when it is a small fix to nothing
under way, by a fix agent otherwise), proposed to Drew for `ROADMAP.md`,
or closed with how (`work.py matter close <id> --how …`). A list nobody
reads is worse than no list.

**The queue and the state are changed only through the tool**, as things
happen, not at the end of a session: a session can stop at any moment
(on 2026-11-24 an API error stalled the coordinator for eight hours) and
the next one starts from what is recorded.

- An order from Drew: write and commit the criteria file, then `work.py
  add` (it allocates the number) and `work.py step add <item> criteria`.
  Order: `move`, `hold`, `unhold`, `drop --why`, `after`.
- Before an item's first agent: `work.py start <item>`.
- After each step: `work.py note <item> --next "…"` with what the next
  session should do, and `--waits` when it waits on Drew, on another
  item or on something outside.
- A ruling on a test: the ruling in the criteria file, and `work.py
  grade` when the coordinator grades against the report's verdict.
- A question asked of Drew: `work.py matter add decision --owner drew …`
  when it is asked. **When Drew answers, record the answer (`work.py
  matter close <id> --how "Drew, <date>: …"`) before acting on it.**
- A promotion: the checks in `docs/sprint/<item>-promote.md`, committed,
  then `work.py promote <item> --tag item-<item> --deployed yes|no`.
  Leftovers: `work.py matter add leftover --owner drew|coordinator`. An
  escape: `work.py matter add escape --item <the promoted item>`.
- Never paste multi-line text into the tool; it refuses a newline.

Since work item M34 (2026-11-26) `/work` is drawn from the database's
dump (`scripts/work_export.py` reads `work.db.dump.sql`, which every
write refreshes), so what the tool records is on the page within a
minute and nothing is written twice.

## Where the steps came from

The steps for investigation and design review, the one-goal rule for a
build session, the handoff line, the session-start sweep, and the rule
that the coordinator writes no code, were added 2026-11-24 from a
comparison with the build workflow of another system (Aurora, a personal
assistant built on Claude Code). The receipts in this repo:

- M1b removed the hot tub monitor on a plan that said it was broken. It
  had been working since 2026-10-24. Drew reversed the removal the next
  day. (Step 1.)
- M16 and M18 each took four tests and three fix rounds. Design problems
  were found in test rounds. (Step 3.)
- On M9, M12, M13, M16, M18, M22 and M23 the coordinator changed code
  after the last test. Each change was named as untested, and stayed
  untested. (The coordinator rule; step 6.)
- The "Held or left over" column holds dozens of notes with no owner.
  (The session-start sweep.)

Considered in the same comparison and not adopted:

- **A context cap in every brief** (the agent stops at about 200K tokens
  of context). Cut by Drew, 2026-11-24: the number was the other system's,
  no build session here has been measured, and it is not known that an
  agent can read its own context size. The one-goal rule stands in its
  place.
- **A separate attack agent for guards.** The test agent already attacks
  (M3), and the coordinator grades what it finds. The other system's own
  trial found that a cold attacker always finds something.
- **A drop box the assistant writes defect notes into.** It needs a model
  that can write a useful defect note; the local models cannot. The
  code-written signals (the corrected-run mark, failed traces) are read in
  the session-start sweep instead.

Four changes from the comparison need a build. Drew admitted all four to
`ROADMAP.md` on 2026-11-24 ("Build workflow: agent isolation and code
gates", under Next), where their order and open questions are kept: auto
permission mode in place of skipped permissions, a git worktree per
agent, a land gate in code, and release as code. Until each one ships,
the steps above stand as written.

What this workflow has that the other system lacked, and should keep: the
fresh test agent every round, criteria committed before the build, the
cap on fix rounds, and the promotions log. At least 9 of the 24 tested
items of the 2026-11 run failed their first independent test with a green suite.

## Launching an agent in herdr

Use `scripts/agent.py` (work item M28, `docs/features/agent-tool.md`).
Each subcommand is one step the coordinator chooses to take; none of them
runs the whole life of an agent. Each answers in one line of JSON on
stdout (`peek` prints the pane as text); a refusal or a herdr error is one
line on stderr and exit 1. The coordinator must be inside herdr. The brief
goes in a file in the session scratchpad, not on the command line, and
does not carry the guard rails: `launch` appends the section below, whole,
read from this file at launch, and there is no option to leave it out.

```bash
S=<scratchpad directory>
A=scripts/agent.py
$A launch M24 build --brief $S/m24-build.txt --report docs/sprint/M24-build.md
$A status m24-build           # where the agent and its work stand
$A wait m24-build             # run in the background; exit 0 done, 2 time limit, 3 stopped, 1 error
$A peek m24-build             # the last 40 lines of its pane
$A prompt m24-build --file $S/m24-followup.txt
$A wait m24-build --expect-commit   # after a follow-up that asks for a commit
$A close m24-build            # once its report is committed
$A list                       # every agent the tool launched and still knows
```

The role sets the model: `build` and `fix` run Opus 5.5 at effort high;
`test`, `review` and `investigate` run Fable 5.2 at effort medium; all
with permissions skipped. `--model` and `--effort` override. The name is
`<item>-<role>`, with `--round N` adding the number from round 2 on
(`m24-test2`; fix round 1 is `m24-fix`, round 2 `m24-fix2`), which is the
name `/work` reads a stage from. An `investigate` agent is shown under
"Other agents". `--probe` names an agent `<item>-probe-<role>`, which
`/work` does not put on the item, for trying the tool itself.

`build`, `fix`, `test` and `review` launch with `--report PATH` (the
file the agent commits; not a directory) or `--no-report`; without either
the launch is refused. `launch` refuses, changing nothing, when an agent
or workspace of that name exists, when another agent the tool launched is
`working` (`--parallel` allows it, for an investigation beside a build),
when the brief or the guard rails section is missing or empty, when the
brief holds a NUL byte, and when its state directory cannot be written.
The coordinator's own pane and agents started by hand are not in the
tool's state and do not count. An agent of the tool's name in a pane
other than the one it launched is `gone` to `status`, `wait`, `list` and
`close`.

`done` in `status` and `wait` is: herdr says `idle` or `done`, `git status
--porcelain` is empty, and the report named with `--report` (at launch,
`status` or `wait`) is in HEAD and was added or changed since the launch.
`wait` exits 3 when the agent is `blocked` or gone, or has sat idle for
five minutes (`--idle-limit`) with that not true: an agent that stopped
early or stopped to ask. The coordinator then peeks and decides. The
default time limit is 3 h (`--timeout`, seconds). Exit 1 is a refusal or
herdr failing three polls running. Git is read with optional locks off,
so a poll never makes an agent's commit fail.

A follow-up does not reset `done`: a report committed in the first round
still counts. `prompt` records HEAD as it sends; `status` then shows
`report.changed_since_prompt`, and `wait --expect-commit` waits for a
commit of the agent's since the last prompt. `agent_commits` is the
commits since launch without a `Claude-Session:` trailer, which the
coordinator's commits carry and the agents' do not; `commits_since`
counts both. `agent_commits` is `null` where no recent commit carries the
trailer.

Things that went wrong, and what to do:

- A prompt sent to an agent that has sat idle for a long time may not
  arrive (it was compacted). `prompt` checks the status moved to
  `working` and sends once more if not; its answer says `"sent": "once"`
  or `"twice"`. If it is still not working after two, peek before sending
  anything else.
- Read the agent's report from the committed file, not from the pane.
- `close` closes only the workspace the tool recorded at launch, and
  refuses while the agent is `working` unless `--force`.

## Guard rails in every brief

Agents run with permissions skipped, so the brief is the only fence. Every
brief carries these, in full:

- No deletion of data outside the repo unless Drew ordered it for this
  item. No changes to Music Assistant's settings. No firmware changes to
  pucks. No change on the MagicMirror Pi or any managed host. Stop and ask
  when a step is not reversible.
- Never `docker compose down -v`; never `--remove-orphans`. No
  `docker compose` typed in the working tree against the production
  `kronk` project. Agents do not run `scripts/deploy_tag.sh deploy|seed`,
  `scripts/upgrade_minor.sh` or `scripts/upgrades.py`; the coordinator
  deploys.
- No restart of Home Assistant, Music Assistant, production containers,
  llama units, LiteLLM or the wyoming units. Install, enable or start no
  systemd unit; touch no timer and nothing under `~/.config/kronk`.
- Never scan or discover on the LAN; no `.local` lookups; no request to
  any LAN address other than this host's loopback and the Docker network.
- No request to production chat routes, production tool_service music
  routes, HA's `kronk` pipeline, or production's `/ops/exec`,
  `/magicmirror/update`, `/fetch`, `/http_check`, `/config/reload`; no
  state-changing request to production `/settings`. Test on the dev pair.
- Make no sound. Music Assistant read commands only. Before any dev music
  request, check that nothing is playing and that `MUSIC_DRY_RUN=1`; never
  turn dry-run off. No STT or wake-stage run (audio capture is on). Do not
  run `scripts/pipeline_bench.sh`.
- Send no notification to anyone's phone. A by-hand backup run carries
  `--no-notify` and `--dest <scratch>`; `/mnt/nas-backup` is really
  mounted.
- Do not edit `.env`, `.env.dev`, `./data` or the live config directories
  by hand. Never print a secret (the HA token, the MA token, the Grafana
  login, `KRONK_BACKUP_PASSWORD`, anything from `auth.db`). No household
  transcript text beyond the corpus and the incident being worked.
- Literal paths in any `rm`. No symlinks to repo data or secrets in
  scratch. Tests open no sockets. Temporary git repos set the user per
  repo. Finish background jobs before replying. Leave the git tree clean.
- No new special-case routing rule unless captured data shows the same
  failure several times.

## Git

Agents commit and tag locally when Drew has ordered the item run this way
(CLAUDE.md, "Git"). The coordinator commits its own files: criteria,
rulings, promotion checks, docs, and changes to this file and to
CLAUDE.md that Drew has agreed (Drew, 2026-11-24). Since the merge of 2026-11-21 the working tree stays
on `main`, clean: the monthly patch run expects that branch
(`~/.config/kronk/upgrade.env`) and declines on a dirty tree. Nobody but
Drew pushes. Commits made by the coordinator or an agent end with the
attribution lines the session is given.

## Reporting to Drew

Each item ID gets a one-sentence description beside it; Drew has not
memorised the table. Failures are quoted, not summarised. What is Drew's
to do (a push, a spoken test, a decision) is listed last.
