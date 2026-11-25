# Feature: scripts/agent.py, launching and checking on work-item agents

**Shipped:** work item M28 (2026-11-24 build). **Origin:** Drew, 2026-11-24,
on seeing the coordinator write a shell and Python loop for every agent:
"checking in on an agent should be a tool, right?" Criteria and evidence:
`docs/sprint/M28-criteria.md`, `M28-build.md`.

## What it does

One script the coordinator runs on the host, inside herdr, for the agents
of `docs/sprint/WORKFLOW.md`. Seven subcommands, each one step; there is no
command that runs build, test and fix in sequence (Drew ruled that out).

| Subcommand | Does | Answers |
| :-- | :-- | :-- |
| `launch <item> <role> --brief FILE` | workspace, agent, prompt; confirms `working` | name, ids, model, effort, HEAD, prompt file, `sent` once/twice |
| `status <name>` | reads herdr and git | `agent_status`, `minutes`, `commits_since`, `last_commit`, `tree_clean`, `report`, `done`, `verdict` |
| `wait <name>` | polls `status` (20 s) | exit 0 done, 2 time limit (3 h), 3 blocked, gone, or idle 5 min with the work not done; last line is the `status` JSON |
| `peek <name> [--lines N]` | `herdr agent read`, last 40 lines | text |
| `prompt <name> --file FILE` | sends, confirms `working`, resends once | `before`, `sent`, `agent_status` |
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
  a brief that is missing or blank; an unknown role, a bad item id,
  `--round` below 1, a report path outside the repo. If `agent start`
  fails after the workspace was made, the workspace is closed and the
  state removed.
- **State.** `~/.local/state/kronk-agents/` (`KRONK_AGENT_STATE`
  overrides), mode 700: `<name>.json` (item, role, ids, model, effort,
  repo, HEAD and time at launch, report path) and `<name>.prompt.txt`, the
  prompt as sent, the only place the brief's text is kept. Both mode 600;
  `close` removes both.
- **Git.** `--no-optional-locks` and `GIT_OPTIONAL_LOCKS=0` on every call,
  and only `rev-parse`, `rev-list`, `log`, `status`, `diff` (commit to
  commit) and `cat-file`. "Report committed" means the file is in HEAD and
  differs from the launch HEAD, so a report left from an earlier round
  does not count. The verdict is read from the committed file by
  `work_export.verdicts()`, so `/work` and the tool agree on it.

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
