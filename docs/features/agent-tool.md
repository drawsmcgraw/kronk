# Feature: scripts/agent.py, launching and checking on work-item agents

**Shipped:** work item M28 (2026-11-24 build). **Origin:** Drew, 2026-11-24,
on seeing the coordinator write a shell and Python loop for every agent:
"checking in on an agent should be a tool, right?" Criteria and evidence:
`docs/sprint/M28-criteria.md`, `M28-build.md`, `M28-e2e.md`, `M28-fix1.md`.

## What it does

One script the coordinator runs on the host, inside herdr, for the agents
of `docs/sprint/WORKFLOW.md`. Seven subcommands, each one step; there is no
command that runs build, test and fix in sequence (Drew ruled that out).

| Subcommand | Does | Answers |
| :-- | :-- | :-- |
| `launch <item> <role> --brief FILE (--report PATH \| --no-report)` | workspace, agent, prompt; confirms `working` | name, ids, model, effort, HEAD, prompt file, `sent` once/twice |
| `status <name> [--expect-commit]` | reads herdr and git | `agent_status`, `minutes`, `commits_since`, `agent_commits`, `last_commit`, `tree_clean`, `report` (`path`, `committed`, `changed_since_prompt`), `done`, `verdict` |
| `wait <name> [--expect-commit]` | polls `status` (20 s) | exit 0 done, 2 time limit (3 h), 3 blocked, gone, or idle 5 min with the work not done; last line is the `status` JSON |
| `peek <name> [--lines N]` | `herdr agent read`, last 40 lines | text |
| `prompt <name> --file FILE` | records HEAD, sends, confirms `working`, resends once | `before`, `sent`, `agent_status` |
| `close <name> [--force]` | closes the recorded workspace, checks it is gone, removes the state | `workspace` closed / already gone |
| `list` | one line per agent in the state directory | name, item, role, workspace, live status |

Exit 1 is a refusal or a herdr error, always one line on stderr, never a
traceback.

## How

- **Roles.** `build`, `fix`: `claude-opus-5-5`, effort `high`. `test`,
  `review`, `investigate`: `claude-fable-5-2`, effort `medium`. All with
  `--dangerously-skip-permissions`. `--model`/`--effort` override.
- **Names.** `<item>-<role>`, the item lower-cased, `--round N` appending
  N from 2 on. At launch the name is read back through
  `scripts/work_export.py`'s own `agent_for()`, the parser `/work` uses,
  and refused if it would not come back as the same item and role.
  `investigate` and `--probe` (`<item>-probe-<role>`) names are not
  attributed to an item; `/work` shows them under "Other agents".
- **Prompt.** The brief, a blank line, then the "Guard rails in every
  brief" section of `docs/sprint/WORKFLOW.md` from its heading to the next
  `## ` heading. A missing or empty section refuses the launch. The prompt
  goes through `herdr agent prompt --wait --until working --until blocked
  --timeout 25000`; herdr's `timeout` and `agent_prompt_stalled` answers
  are swallowed and `herdr agent get` decides. A text that starts with `-`
  gets a `Brief:` line in front, since herdr would read it as an option.
  More than 120 000 bytes is refused (one argv string is capped at 128 KiB).
- **Refusals before any change.** Not in herdr; an agent or a workspace
  label of that name in herdr, or a state file of that name; another agent
  in the state directory that herdr calls `working` (unless `--parallel`);
  a brief that is missing, blank or holds a NUL byte; an unknown role, a
  bad item id, `--round` below 1, a report path outside the repo or that
  is a directory (on disk or a tree in HEAD); `build`, `fix`, `test` or
  `review` with neither `--report` nor `--no-report`; a state directory
  that cannot be written (it is made and test-written before anything is
  created in herdr). If `agent start` or a state write fails after the
  workspace was made, the workspace is closed and the state removed; the
  message says what, if anything, was left.
- **State.** `~/.local/state/kronk-agents/` (`KRONK_AGENT_STATE`
  overrides), mode 700: `<name>.json` (item, role, ids, model, effort,
  repo, HEAD and time at launch, report path; after a follow-up, HEAD when
  it was sent and the count of follow-ups) and `<name>.prompt.txt`, the
  prompt as sent, the only place the brief's text is kept. Both mode 600;
  `close` removes both. Follow-ups are not stored.
- **Identity.** An agent is its name and the pane recorded at launch.
  `status`, `wait`, `list` and `close` call an agent of that name in
  another pane `gone` (it was started by hand after the tool's closed).
  `peek` and `prompt` still go by name.
- **Git.** `--no-optional-locks` and `GIT_OPTIONAL_LOCKS=0` on every call,
  and only `rev-parse`, `rev-list`, `log`, `status`, `diff` (commit to
  commit) and `cat-file`. "Report committed" means the file is in HEAD and
  differs from the launch HEAD, so a report left from an earlier round
  does not count; a git error (a launch HEAD git no longer knows) is not
  committed. The verdict is read from the committed file by
  `work_export.verdicts()`, so `/work` and the tool agree on it.
  `work_export.py` is loaded with `sys.dont_write_bytecode` set, so no
  `__pycache__` is written beside it.
- **Follow-ups.** `prompt` records HEAD as it sends. `status` then gives
  `report.changed_since_prompt` (the report in HEAD differs from that
  HEAD; `null` until a follow-up is sent). `done` is unchanged by a
  follow-up: an old committed report still satisfies it. `--expect-commit`
  on `status` or `wait` makes `done` also need a commit of the agent's
  since the last prompt (the launch prompt if no follow-up was sent).
- **Whose commit.** The coordinator's session stamps its commits with a
  `Claude-Session:` trailer; the agents' commits carry none.
  `agent_commits` counts the commits since launch without it. It is
  `null` when no commit since the launch, nor any of the 50 up to it,
  carries the trailer: in that repo the rule cannot tell the two apart,
  and `--expect-commit` then counts every commit.

## Gotchas

- herdr writes its error object (`{"error":{"code":…}}`, exit 1) to
  **stderr**, not stdout. The tool reads both.
- An agent whose name herdr no longer knows is `gone`: its process exited
  or its workspace was closed by hand. `close` still works on it and
  reports `already gone`.
- The idle clock in `wait` starts at the first idle poll of that `wait`
  run, not when the agent went idle.
- The one-at-a-time check sees only agents in the tool's state
  directory. The coordinator's pane, and any agent started by hand with
  raw `herdr`, are invisible to it.
- `peek` relays the pane as herdr reads it. If the agent echoes its brief
  to the screen, `peek` shows that; nothing else the tool prints carries
  the brief.
- `wait` sleeps in the foreground of its own process; the coordinator runs
  it in the background with its shell tool and is told when it exits.
- `agent_commits` trusts the trailer. A coordinator session that does not
  stamp its commits, in a repo whose history has stamped ones, has its
  commits counted as the agent's.
- Python counts `\x1c`–`\x1f` as whitespace: `str.strip()` on a
  `git log` record split on `\x1e` eats the `\x1f` field separator of a
  commit with no trailer. The parser strips newlines only.

## Blog hooks

- The cost of a wait loop typed by hand: an inline Python return code
  read as an agent failure, a `blocked` agent indistinguishable from a
  working one until the loop's limit, and `git status` taking
  `.git/index.lock` under a committing agent. One script removed all
  three.
- "Checking in should be a tool": one JSON line per check is cheaper in
  tokens than reading a pane, and deterministic where a model's reading
  of a screen is not.
- Guard rails appended by code, with no flag to skip them, against
  guard rails pasted into each brief by a snippet.
