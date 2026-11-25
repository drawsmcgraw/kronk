# M28 pass criteria: a tool for launching and checking on agents

Source: Drew, 2026-11-24, on seeing the coordinator write a shell and
Python loop for every agent: "are you writing python code to check in on
the agents you launch? That feels like a simple tool call that we should
write that you can use for faster and more deterministic behavior, not to
mention lower token burn." And on ordering it: "yes, implement the agent
launcher tool. I don't think we need to rely on the script to manage the
entire lifecycle, though. You'll want to check in on agents and you need
that flexibility. It's just that checking in on an agent should be a
tool, right?"

Handoff line: the coordinator launches, checks on, waits for, prompts and
closes a work-item agent with one command each, and each answers in one
line of JSON.

Plan doc: this file. No design review (small, one script, no production
code path).

## What the investigation found (coordinator, 2026-11-24)

- Every launch today (about twenty) was typed by hand from the recipe in
  `docs/sprint/WORKFLOW.md`, "Launching an agent in herdr": a Python
  one-liner to pull the pane id out of `herdr workspace create`, `herdr
  agent start` with the model and effort for the role, `herdr agent prompt
  "$(cat brief)" --wait --timeout 25000` (which returns a timeout error
  that means nothing), then a background `until` loop on three conditions
  (report committed, tree clean, agent idle or done), then `herdr
  workspace close`.
- The guard rails were copied into each brief by a Python snippet that cut
  the "Guard rails in every brief" section out of WORKFLOW.md.
- What went wrong with that, today: one wait loop reported "failed with
  exit 1" because of an inline Python return code, not the agent; the
  loops could not tell an agent that had stopped to ask something
  (`blocked`) from one still working, so that case would have run to the
  loop's time limit; `git status` in a loop takes `.git/index.lock` unless
  `GIT_OPTIONAL_LOCKS=0` is set, and an agent's commit in that instant
  fails (found by M27's review, for the exporter).
- `/work` (M27) reads an agent's stage from its herdr name:
  `<item>-<role>` with role `review`, `build`, `test` or `fix`, and a
  number after it for a later round (`m27-test2`). A wrong name puts the
  agent under "Other agents".
- herdr has `agent list|get|read|prompt|wait|start` and `workspace
  create|close`. The tool is a wrapper over those; it adds no daemon.

## Plumbing (coordinator's decisions)

1. One script, `scripts/agent.py`, standard library only, run on the host
   by the coordinator. Subcommands, each a single step the coordinator
   chooses to take; none of them runs the whole life of an agent:

   | Subcommand | What it does |
   | :-- | :-- |
   | `launch <item> <role> --brief FILE` | workspace, agent, prompt; confirms the agent is working |
   | `status <name>` | one JSON object: where the agent and its work stand |
   | `wait <name>` | blocks until `status` would say done, blocked, gone, or the time limit |
   | `peek <name>` | the last lines of the agent's pane |
   | `prompt <name> --file FILE` | a follow-up prompt; confirms it arrived |
   | `close <name>` | closes the workspace the tool created |
   | `list` | every agent the tool launched and still knows, one line each |

2. Roles and what they launch with. `build`, `fix`: `claude-opus-5-5`,
   effort `high`. `test`, `review`, `investigate`: `claude-fable-5-2`,
   effort `medium`. All with `--dangerously-skip-permissions`. `--model`
   and `--effort` override. `--round N` gives `<item>-<role>N` for N of 2
   or more. The item is lower-cased (`M28` gives `m28-build`). Any other
   role is refused with the list of roles.
3. `launch` refuses, changing nothing: outside herdr (`HERDR_ENV` not 1);
   when an agent or workspace of that name exists; when another agent the
   tool launched is still `working` (one agent at a time; `--parallel`
   allows it, for an investigation beside a build); when the brief file is
   missing or empty.
4. The prompt sent is the brief followed by the "Guard rails in every
   brief" section of WORKFLOW.md, whole, read at launch. If the section is
   not found the launch is refused. `--no-guard-rails` does not exist.
   The assembled prompt is written beside the state file so the
   coordinator can read what was sent.
5. After the prompt, `launch` checks the status moved to `working`, sends
   once more if it did not, and reports which happened. The 25 s timeout
   error from herdr is swallowed; any other herdr error is reported.
6. State: one JSON file per agent under
   `~/.local/state/kronk-agents/` (override: `KRONK_AGENT_STATE`), outside
   the repo: name, item, role, workspace id, pane id, model, effort, HEAD
   at launch, launch time, the report path if given (`--report PATH`,
   repo-relative, at launch or at `wait`/`status`).
7. `status` prints exactly one line of JSON on stdout: `name`,
   `agent_status` (herdr's word, or `gone`), `minutes`, `commits_since`
   (count since the launch HEAD) and `last_commit` (short hash and
   subject), `tree_clean`, `report` (path, and `committed` true or false:
   the file exists in HEAD and was added or changed since launch), `done`
   (true only when the agent is `idle` or `done`, the tree is clean, and
   the report, when one is named, is committed), and `verdict` (the
   report's `Verdict:` line when it has one). Git is read with
   `GIT_OPTIONAL_LOCKS=0`; nothing is written to the repo.
8. `wait` polls `status` (default every 20 s, limit 3 h, both options) and
   exits 0 when done, 2 on the time limit, 3 when the agent is `blocked`
   or `gone` or has sat `idle` for five minutes with the work not done
   (that is an agent that stopped early; the coordinator decides what
   next). Its last line of stdout is the same JSON as `status`. It is
   meant to be run in the background by the coordinator's own tool.
9. `peek` prints the last N lines (default 40) of the pane through
   `herdr agent read`, as text. `prompt` sends a file, confirms `working`,
   resends once. `close` closes only a workspace recorded in the tool's
   state, refuses while the agent is `working` unless `--force`, and
   removes the state file. `list` prints one JSON line per known agent.
10. Exit codes: 0 success; 1 a refusal or a herdr error, with one line on
    stderr saying which; 2 and 3 as in point 8. No traceback reaches the
    coordinator for a case listed here.
11. `docs/sprint/WORKFLOW.md`, "Launching an agent in herdr", is rewritten
    by the build agent to the tool's commands (the raw herdr recipe is
    dropped; the "things that went wrong" list keeps what is still true).
    A short `docs/features/agent-tool.md`. CLAUDE.md and README mention it
    where they list scripts, if they do.

## Criteria

1. Each subcommand does what its row says, shown on a real agent in a
   real herdr workspace: launched, seen `working`, peeked, waited for
   (a brief that commits one small file under the scratch path named in
   the brief is not possible, since the tree must stay clean: use a brief
   that asks the agent to answer "ready" and stop, and show `wait` exits
   3 for it after the idle limit, set short by option), prompted, closed.
   The workspace is gone after.
2. The name rule holds: `/api/work` on dev shows the launched agent under
   its item with the right stage word for each of the four roles `/work`
   knows (unit-tested against `scripts/work_export.py`'s own name parser,
   not a copy of it).
3. Every refusal in point 3 and the missing guard rails section, by test.
4. `status` and `wait` on a temporary git repo with a fake `herdr` on
   `PATH`: done; not done for each single missing condition; `blocked`;
   `gone`; idle-not-done; the time limit; a report that existed before the
   launch and was not touched is not "committed".
5. No git lock: `strace` (or the method M27's test used) shows `status`
   opens no `.lock` path in `.git`.
6. Nothing the tool prints or stores holds the brief's text other than
   the one assembled prompt file under the state directory, mode 600.
7. The suite passes; its last lines are quoted.

## Out of scope

- A wrapper that runs build, test and fix in sequence. Drew ruled that
  out.
- Worktrees per agent, the land gate, auto permission mode (ROADMAP,
  "Build workflow").
- Any change to `/work`.

## Rulings

- 2026-11-24, coordinator, on the first test (`M28-e2e.md`, FAILS; all
  seven criteria met; two blockers by the brief's grading). The tool was
  used for real on that test: `launch` in one command, `wait` returned 0
  on the committed report. Fix round 1, all of these:
  - B1: nothing is created in herdr before the state directory is made
    and proven writable; if a state write fails after `workspace create`,
    the workspace is closed and the message says what was left, if
    anything.
  - B2: the tool writes no bytecode into the repo
    (`sys.dont_write_bytecode` before any import of a repo module); the
    test drops `-B`.
  - S1: a report path that is a directory (a tree in HEAD, or on disk)
    is refused at `launch`, `status` and `wait`.
  - S2: `prompt` records HEAD at the moment it is sent. `status` gains
    `report.changed_since_prompt` (null when no follow-up was sent) and
    `agent_commits` (commits since launch whose author is not the
    coordinator's session: decided by the commit's `Claude-Session`
    trailer being absent or, failing that rule, reported as unknown; the
    fix agent says what it found workable). `done` after a follow-up
    still follows point 7; `wait --expect-commit` makes it also require
    a new commit since the last prompt.
  - S3: a brief or prompt file with a NUL byte is refused with the other
    brief checks, before anything is created.
  - S4: tests for a report committed and then followed by another
    commit, and for a failing `git diff` not counting as committed.
  - N1: `launch` for build, fix, test and review refuses without
    `--report` unless `--no-report` is given.
  - N2: `status`, `wait`, `list` and `close` compare the recorded pane id
    with the agent herdr returns for the name; a different pane is
    `gone`.
  Fix rounds are named `m28-fix` (round 1 has no number), as the tool
  does it; `/work` reads both forms.
- 2026-11-24, coordinator, on the second test (`M28-e2e2.md`, FAILS by
  the brief's grading; every job of fix round 1 done, seven criteria
  hold, suite passes). Tier 1 under WORKFLOW.md, "How much testing an
  item gets" (agreed with Drew the same day, after this test was
  launched): the coordinator grades. B3 (a launch killed or failed inside
  `herdr workspace create` leaves a workspace the tool has no record of)
  needs a signal inside about a second or herdr answering wrongly; the
  next `launch` of the name refuses and names the label. Graded
  should-fix and left over, with S5 (tests that miss some mutants) and
  N11 (`prompt` and `peek` go by name). Promote. No third round.

