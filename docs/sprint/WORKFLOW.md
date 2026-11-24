# Work-item workflow: coordinator, build agent, test agent

How the refactor sprint (2026-11-15 to 2026-11-21, items M1 to M23) was
run, revised 2026-11-24 (last section), written down so a later session
runs the next item the same way. Use it when Drew orders a piece of work
as a work item, or asks for "an Opus agent" to build or investigate
something.

"Sprint" was the name for that one run through a long backlog and is not
used for new work. A piece of work run this way is a work item. The
directory `docs/sprint/`, the item numbers (M24 is next) and the old
`sprint-<item>` tags keep their names; new tags are `item-<item>`.

A small fix that needs neither a build agent nor a test agent does not
need this: the session makes it inline under CLAUDE.md's "Definition of
done". The rule below that the coordinator writes no code starts when an
item's criteria file is committed and ends with its promotions entry. A
fix to an item under way is never the inline kind.

## Roles

| Role | Model | Flags | Where |
|---|---|---|---|
| Coordinator | the session Drew talks to | | its own herdr pane |
| Build agent, fix agent, investigator | Opus 5.5 (`claude-opus-5-5`) | `--effort high --dangerously-skip-permissions` | its own herdr workspace |
| End-to-end test agent | Fable 5.2 (`claude-fable-5-2`) | `--effort medium --dangerously-skip-permissions` | its own herdr workspace |
| Design reviewer (large items only) | Fable 5.2 (`claude-fable-5-2`) | `--effort medium --dangerously-skip-permissions` | its own herdr workspace |

The coordinator investigates, writes the criteria and the briefs, rules on
findings, deploys, and reports to Drew. It does not build the item, and it
writes no code into the repo at any point, the time after the last test
included. Its own commits are the criteria, rulings, the promotions entry
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

   A small fix after a passing test is still a fix agent's, never the
   coordinator's, and a fresh test agent checks it. That test is narrow:
   the diff, the criteria it touches, and the suite. A should-fix finding
   that is not worth that round is left over, and the promotions entry
   says so.
7. **Promote.** The coordinator runs the suite, tags `item-<item>`, runs
   `scripts/deploy_tag.sh plan <tag>` then `deploy <tag>`, verifies live
   (CLAUDE.md, "Definition of done"), and adds the entry to
   `docs/sprint/PROMOTIONS.md`. If Drew orders something shipped that no
   test agent saw, the entry names it as untested by an agent.
8. **Close.** Each agent's workspace is closed once its report is
   committed. Only the coordinator's workspace stays.

## Start of every working session

Before taking up an item the coordinator reads the "Held or left over"
entries of `docs/sprint/PROMOTIONS.md` that are still open and, in
Grafana, the voice runs marked as corrected since the last session
(`docs/features/voice-capture.md`). Each one is fixed (inline when it is
a small fix to nothing under way, by a fix agent otherwise), proposed to
Drew for `ROADMAP.md`, or struck with a dated line. A list nobody reads is
worse than no list.

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

The coordinator must be inside herdr (`test "${HERDR_ENV:-}" = 1`). The
brief goes in a file in the session scratchpad, not on the command line.

```bash
S=<scratchpad directory>
P=$(herdr workspace create --label m24-build --cwd "$PWD" --no-focus \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
herdr agent start m24-build --kind claude --pane "$P" --timeout 60000 -- \
  --model claude-opus-5-5 --effort high --dangerously-skip-permissions
herdr agent prompt m24-build "$(cat $S/m24-build.txt)" --wait --timeout 25000
herdr agent get m24-build    # agent_status should be "working"
```

For a test agent the last line of `agent start` is
`--model claude-fable-5-2 --effort medium --dangerously-skip-permissions`.

Things that went wrong, and what to do:

- `agent prompt --wait --timeout 25000` returns a timeout error on a long
  job. That is expected. Check that the status is `working`.
- Wait for the agent with a background loop on three conditions together:
  the report file is committed, `git status --porcelain` is empty, and the
  agent's status is `idle` or `done`. Do not poll by hand.
- A prompt sent to an agent that has sat idle for a long time may not
  arrive (it was compacted). After any follow-up prompt, check the status
  moved to `working`; if not, send it again from a file.
- Read the agent's report from the committed file, not from the pane.
- Close with `herdr workspace close <workspace id>`, only workspaces the
  coordinator created.

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
rulings, promotions entries, docs, and changes to this file and to
CLAUDE.md that Drew has agreed (Drew, 2026-11-24). Since the merge of 2026-11-21 the working tree stays
on `main`, clean: the monthly patch run expects that branch
(`~/.config/kronk/upgrade.env`) and declines on a dirty tree. Nobody but
Drew pushes. Commits made by the coordinator or an agent end with the
attribution lines the session is given.

## Reporting to Drew

Each item ID gets a one-sentence description beside it; Drew has not
memorised the table. Failures are quoted, not summarised. What is Drew's
to do (a push, a spoken test, a decision) is listed last.
