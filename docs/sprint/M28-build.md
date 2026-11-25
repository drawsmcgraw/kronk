# M28 build: a tool for launching and checking on agents

Agent: `m28-build` (Opus 5.5), 2026-11-24.

**Guard rails broken: none.** Herdr workspaces and agents created: three,
all named `m28-probe-*` (`m28-probe-test` twice, `m28-probe-build` once,
workspaces w3M, w3N, w3P), all on `claude-haiku-5-5` at effort `low`, all
with the brief "Answer with the single word ready and stop. Run no
command.", all closed. `kronk-claude` and `m28-build` were not touched.
Live checks used a state directory in scratch; `~/.local/state/kronk-agents`
does not exist. One thing to name: to show the lock test catches a
regression, a temporary copy of the test file pointed at a mutant in scratch
was written to `tests/test_m28_mutant_tmp.py`, run, and removed with that
literal path; it was never committed.

## What was built

| File | What |
| :-- | :-- |
| `scripts/agent.py` | the tool: `launch`, `status`, `wait`, `peek`, `prompt`, `close`, `list`; standard library only |
| `tests/test_m28_agent_tool.py` | 77 tests; a stateful fake `herdr` (Python, on PATH, errors on stderr like the real one), temporary git repos with the user set per repo, no sockets (`no_network`) |
| `docs/sprint/WORKFLOW.md` | "Launching an agent in herdr" rewritten to the tool's commands |
| `docs/features/agent-tool.md` | the feature doc, with gotchas and blog hooks |

ROADMAP.md, CLAUDE.md and README.md are unchanged: the criteria call for no
ROADMAP note, and neither CLAUDE.md nor README lists scripts (README's table
lists services and timers; the tool is neither).

Commits: `a7b89b5` (tool and tests), `9dbb187` (WORKFLOW section and feature
doc), and this report.

## Plumbing points and where they are implemented

1. **One script, seven subcommands.** `scripts/agent.py`, `parser()` and
   `cmd_launch`, `cmd_status`, `cmd_wait`, `cmd_peek`, `cmd_prompt`,
   `cmd_close`, `cmd_list`. None runs the whole life of an agent.
2. **Roles.** `ROLES` (build, fix: `claude-opus-5-5`/`high`; test, review,
   investigate: `claude-fable-5-2`/`medium`), `SKIP_PERMISSIONS`; overrides
   in `cmd_launch`. Name in `agent_name()`: item lower-cased, `--round N`
   adds N from 2 on; an unknown role is refused with the list of roles.
3. **Launch refusals, changing nothing.** `cmd_launch`, in this order:
   role/item/round/effort, `HERDR_ENV`, brief missing or blank, guard rails
   section, prompt size, report path, git repo; then a state file of that
   name, a herdr agent of that name (`herdr agent list`), a herdr workspace
   labelled with it (`herdr workspace list`), another agent in the state
   directory that herdr calls `working` (skipped by `--parallel`). Only
   after all of them does `workspace create` run.
4. **Brief plus guard rails.** `guard_rails()` takes the line `## Guard
   rails in every brief` through the line before the next `## ` heading;
   missing or empty refuses. `assemble()` joins brief and section. There is
   no `--no-guard-rails` (a test sends it and sees argparse refuse it).
   The assembled prompt goes to `<state>/<name>.prompt.txt`, mode 600.
5. **Confirm working, resend once.** `send()`: herdr's `timeout` and
   `agent_prompt_stalled` are swallowed, `herdr agent get` decides; any
   other herdr error is reported (code only). The answer says
   `"sent": "once"` or `"twice"`.
6. **State.** `state_dir()` (`KRONK_AGENT_STATE` or
   `~/.local/state/kronk-agents`, mode 700), `save_state()` via
   `write_private()` (temp file, mode 600, rename). Fields: name, item,
   role, round, probe, workspace id, pane id, model, effort, repo, HEAD,
   launch time (epoch and ISO), report.
7. **status.** `status_of()` and `work_state()`: exactly the nine keys of
   the criteria. `done` = herdr status `idle` or `done`, tree clean, and
   the report (when named) committed: in HEAD (`cat-file -e`) and
   different from the launch HEAD (`diff --quiet <launch> HEAD -- <path>`).
   Every git call has `--no-optional-locks` and `GIT_OPTIONAL_LOCKS=0`.
8. **wait.** `cmd_wait`: `--interval 20`, `--timeout 10800` (seconds),
   `--idle-limit 300`; exit 0 done, 2 time limit, 3 blocked, gone, or idle
   past the limit with the work not done. Last (only) stdout line is the
   `status` JSON; a one-line reason for 2 and 3 on stderr.
9. **peek, prompt, close, list.** `cmd_peek` (`herdr agent read --source
   recent-unwrapped --lines N --format text`, last N lines, default 40);
   `cmd_prompt` (file, confirm, resend once); `cmd_close` (only the
   recorded workspace; refuses `working` without `--force`; checks with
   `workspace get` that it is gone; removes the state and prompt files);
   `cmd_list` (one JSON line per state file, live status from one
   `herdr agent list`).
10. **Exit codes, no traceback.** `main()`: `Refusal` (including
    `HerdrError`) is one stderr line and exit 1; `Parser.error` turns
    argparse's usage errors into the same; anything unforeseen is caught as
    "internal error (Type: message)" on one line.
11. **Docs.** WORKFLOW.md section and `docs/features/agent-tool.md`.

## Choices the criteria left open

1. **`--probe`.** The criteria's names are `<item>-<role>[N]`, and this
   build's brief allowed live agents named `m28-probe-*` only. `--probe`
   names an agent `<item>-probe-<role>[N]`; `/work`'s parser does not read
   it, so a probe shows under "Other agents" and never as a stage of a real
   item. That is what the live check used.
2. **Round 1 has no number.** `--round` defaults to 1, which adds nothing;
   2 and up are appended; 0 and below are refused. So fix round 1 is
   `m28-fix`, round 2 `m28-fix2`. WORKFLOW.md's naming paragraph still
   cites the historical `m27-fix1`; `/work` parses both. The coordinator
   may want fix rounds numbered from 1 to match `M28-fix1.md`; that would
   be a criteria change.
3. **The name is checked against `/work` at launch.** `agent_name()` loads
   `scripts/work_export.py` from beside the tool and refuses a name that
   `agent_for()` would not read back as the same item and role. One source
   of truth for the name rule; the cost is that a `work_export.py` that
   cannot load refuses every launch, with that reason.
4. **Who counts as "another agent working".** Only agents in the tool's
   state directory whose herdr status is `working`. `blocked`, `idle` and
   `done` do not block a launch (the criteria say `working`). The
   coordinator's own pane is unnamed and not in the state directory, so it
   never counts; nor does an agent started by hand with raw `herdr`,
   whatever its name. During this build `m28-build` (me) was working and
   was not counted, because it was not launched by the tool.
5. **An existing state file of that name refuses a launch**, with "close
   it first", even when herdr no longer knows the agent: `close` cleans up
   a gone agent's state and reports `already gone`.
6. **Rollback.** If `agent start` fails after the workspace was created,
   the tool closes that workspace and removes the state, and says so. If
   the close fails too, the state is kept and the message says to
   `close --force`.
7. **How the prompt is sent.** `herdr agent prompt --wait --until working
   --until blocked --timeout 25000`, so a normal launch returns as soon as
   the agent works (about 4.6 s live) instead of waiting out a timeout.
   `blocked` after a prompt counts as arrived (a turn started and stopped
   at a question); `wait` will then exit 3 for it.
8. **A prompt that starts with `-`** gets `Brief:` (launch) or `Prompt:`
   (prompt) and a blank line in front: herdr's parser read `-…` text as an
   option, and herdr does not accept `--` before the text (checked:
   `unknown option`).
9. **Prompt size.** Refused above 120 000 bytes, below Linux's 128 KiB cap
   on one argv string. The text passes through herdr's argv; it is not
   written anywhere by the tool except the prompt file.
10. **Follow-up prompts** carry no guard rails (they were sent with the
    brief) and are not stored. `prompt` refuses an agent that is `blocked`
    (herdr would reject it, and it needs a look first) or gone, before
    sending. Its answer adds `before`, the status before sending: if that
    was already `working`, "working" after cannot prove arrival.
11. **wait details.** The idle clock starts at the first settled poll of
    that `wait` run; herdr's `done` counts as idle; a status of `unknown`
    keeps waiting; three herdr errors in a row end the wait with exit 1
    (one is tolerated as a blip). Only the final JSON line is printed.
12. **`last_commit`** is HEAD's last commit as `"<short hash> <subject>"`,
    the subject clipped to 120 characters, whether or not it is since the
    launch (`commits_since` says that).
13. **`verdict`** is read from the committed report with
    `work_export.verdicts()` (first 40 lines, its anchored forms), the
    item's own verdict first; the word (`PASSES`, `FAILS`,
    `NOT DETERMINED`) or null. `/work` and the tool cannot disagree.
14. **`--report`** is stored repo-relative; an absolute path inside the
    repo is converted; `..` or a path outside is refused. Naming it at
    `status` or `wait` updates the state file.
15. **`close`** also removes the prompt file, so the brief's text does not
    outlive the agent. It verifies with `herdr workspace get` that the
    workspace is gone, and keeps the state if it is not.
16. **`list`** with herdr unreachable prints each row with
    `agent_status: null` and exits 1 with the reason.
17. **Usage errors exit 1**, not argparse's 2, which would collide with
    `wait`'s exit 2.
18. **`--repo`** is a hidden launch option (tests point it at temporary
    repos); the repo is stored in the state, and `status` reads git there.
19. **`minutes`** is a float to one decimal.

## Criteria

1. **Each subcommand on a real agent: PASSES** (live output below).
   launch, status, peek, wait (exit 3 after a short idle limit, and exit 0
   for an agent with no report), prompt, close, list; workspaces gone
   after. Also shown live: the `--parallel` refusal, a name that already
   exists in the state directory, and one that exists only in herdr.
2. **Name rule against `/work`: PASSES by test.**
   `test_names_parse_back_to_item_and_role_by_work_exports_parser` and
   `test_launched_agents_show_on_their_item_with_the_stage_word` (launches
   one agent per role through the fake herdr, reads them back with
   `work_export.herdr_agents()`, `agent_for()` and `stage()` and checks each
   stage word: implementing, testing, reviewing, fixing). Live, `/dev/api/work`
   put the two probes under "Other agents" (`"why": "name is not
   <item>-<role>"`), and showed M28 as `implementing, agent m28-build
   working` from this build agent's own `<item>-<role>` name.
3. **Every refusal: PASSES by test.** Outside herdr (unset and `0`),
   agent name exists, workspace label exists, state file exists, another
   tool agent working (and `--parallel` lets it through; idle, done or
   blocked agents and untracked agents do not count), brief missing, brief
   blank, guard rails section missing, empty, at the wrong heading level,
   WORKFLOW.md absent; plus role, item, round, effort, report path and an
   over-long name. Each asserts rc 1, one stderr line, no mutating herdr
   call, and an empty state directory.
4. **status and wait: PASSES by test.** Done (for `idle` and `done`); not
   done for each single missing condition (working; untracked file;
   modified tracked file; report not committed); `blocked` and gone (exit
   3); idle not done (exit 3); idle clock reset by a working poll; time
   limit (exit 2); a report from before the launch not "committed" until
   changed; report named at `status` and at `wait`; three herdr errors.
5. **No git lock: PASSES.**
   `test_under_strace_status_opens_no_lock_and_writes_nothing_in_the_repo`
   runs `status` under `strace -f` (M27's method) on a temporary repo with
   a clean tree and a committed report: no path containing `.lock`, and
   no open-for-write, rename, unlink or mkdir under the repo; it also
   asserts git files were traced at all. It ran here (not skipped). A
   mutant with `--no-optional-locks` and `GIT_OPTIONAL_LOCKS=0` removed
   fails it and `test_git_is_called_read_only_and_lockless`:
   `assert not [l for l in lines if ".lock" in l]`, "2 failed".
6. **Brief text in one file only: PASSES by test.**
   `test_the_brief_is_kept_only_in_the_prompt_file`: a marker in the brief;
   launch, status, wait, list, prompt and a failing prompt print nothing
   with it; in the state directory only `m9-build.prompt.txt` holds it;
   every file is mode 600, the directory 700; after close the directory is
   empty. Exception, by design: `peek` relays the pane, and would show
   brief text if the agent printed it.
7. **Suite: PASSES.** `./scripts/run_tests.sh -q`, last lines:

   ```
   -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
   7761 passed, 3 skipped, 54 xfailed, 6 warnings in 118.57s (0:01:58)
   ```

## Live check

Scratch: `S=/tmp/claude-1000/-home-drew-git-repos-drawsmcgraw-kronk/d8aa0fe1-a052-4a01-b4ae-7f317e703000/scratchpad`,
`L=$S/m28live`, `brief.txt` = "Answer with the single word ready and stop.
Run no command.", `followup.txt` = "Answer with the single word again and
stop. Run no command." `A=scripts/agent.py`. Long `prompt_file` paths are
shortened to `$L/...` below, some status lines are cut with `...`, and the
pane's prompt and status-bar lines are summarised in parentheses.

### Run 1 (tree dirty: the tool was not yet committed), `KRONK_AGENT_STATE=$L/state`

```
$ time python3 $A launch M28 test --probe --brief $L/brief.txt --model claude-haiku-5-5 --effort low
{"name": "m28-probe-test", "item": "M28", "role": "test", "workspace_id": "w3M", "pane_id": "w3M:p1", "model": "claude-haiku-5-5", "effort": "low", "head": "7380c43", "report": null, "prompt_file": "$L/state/m28-probe-test.prompt.txt", "sent": "once", "agent_status": "working"}
real	0m4.620s
rc=0
-rw------- 1 drew drew  385 Nov 24 21:28 m28-probe-test.json
-rw------- 1 drew drew 2378 Nov 24 21:28 m28-probe-test.prompt.txt
$ status
{"name": "m28-probe-test", "agent_status": "done", "minutes": 0.2, "commits_since": 0, "last_commit": "7380c43 M29, M30: criteria (tests that run the pages' scripts in headless Firefox; what Whisper heard, folded at the to…", "tree_clean": false, "report": null, "done": false, "verdict": null}
$ peek --lines 8
● ready

✻ Churned for 1s · done 9:28 PM
(prompt and status-bar lines)
$ launch again (same name)
agent.py launch: m28-probe-test is already in $L/state: `agent.py close m28-probe-test` first; not launched
rc=1
$ wait --interval 5 --idle-limit 20
{"name": "m28-probe-test", "agent_status": "done", "minutes": 0.5, ..., "tree_clean": false, "report": null, "done": false, "verdict": null}
agent.py wait: m28-probe-test has been done for 20 s with the work not done
real	0m20.091s
rc=3
$ launch from a second state dir (herdr has the name)
agent.py launch: an agent named m28-probe-test exists in herdr; not launched
rc=1
$ prompt m28-probe-test --file $L/followup.txt
{"name": "m28-probe-test", "before": "done", "sent": "once", "agent_status": "working"}
$ close   (at once, while working)
agent.py close: m28-probe-test is working; --force to close it anyway; not closed
rc=1
$ close   (after it settled)
agent.py close: herdr workspace get: no JSON in the answer (exit 1)
rc=1
```

That last line was a bug, found here: herdr writes its error object to
stderr, and the tool read stdout only. The workspace had been closed
(`herdr workspace list` showed only `kronk-claude` and `m28-build`); the
check after it could not read `workspace_not_found`. Fixed before the
first commit (`herdr()` reads stdout, then stderr), and the fake herdr in
the tests writes errors to stderr so the suite would catch it again. Then:

```
$ status
{"name": "m28-probe-test", "agent_status": "gone", "minutes": 1.1, ..., "done": false, "verdict": null}
$ list
{"name": "m28-probe-test", "item": "M28", "role": "test", "workspace_id": "w3M", "agent_status": "gone", "minutes": 1.1, "report": null}
$ close
{"name": "m28-probe-test", "workspace_id": "w3M", "workspace": "already gone", "agent_status_before": "gone"}
rc=0
(state directory empty)
```

### Run 2 (clean tree at `9dbb187`), `KRONK_AGENT_STATE=$L/state3`

```
$ agent.py list
rc=0
$ agent.py launch M28 test --probe --brief brief.txt --report docs/sprint/M28-criteria.md --model claude-haiku-5-5 --effort low
{"name": "m28-probe-test", "item": "M28", "role": "test", "workspace_id": "w3N", "pane_id": "w3N:p1", "model": "claude-haiku-5-5", "effort": "low", "head": "9dbb187", "report": "docs/sprint/M28-criteria.md", "prompt_file": "$L/state3/m28-probe-test.prompt.txt", "sent": "once", "agent_status": "working"}
rc=0
$ agent.py launch M28 build --probe --brief brief.txt --model claude-haiku-5-5 --effort low   (no --parallel)
agent.py launch: another agent the tool launched is working (m28-probe-test); --parallel to run beside it; not launched
rc=1
$ agent.py launch M28 build --probe --parallel ...
{"name": "m28-probe-build", "item": "M28", "role": "build", "workspace_id": "w3P", "pane_id": "w3P:p1", "model": "claude-haiku-5-5", "effort": "low", "head": "9dbb187", "report": null, "prompt_file": "$L/state3/m28-probe-build.prompt.txt", "sent": "once", "agent_status": "working"}
rc=0
$ agent.py list
{"name": "m28-probe-build", "item": "M28", "role": "build", "workspace_id": "w3P", "agent_status": "working", "minutes": 0.1, "report": null}
{"name": "m28-probe-test", "item": "M28", "role": "test", "workspace_id": "w3N", "agent_status": "done", "minutes": 0.2, "report": "docs/sprint/M28-criteria.md"}
rc=0
$ agent.py status m28-probe-test
{"name": "m28-probe-test", "agent_status": "done", "minutes": 0.2, "commits_since": 0, "last_commit": "9dbb187 M28: WORKFLOW.md launch section rewritten to scripts/agent.py; feature doc", "tree_clean": true, "report": {"path": "docs/sprint/M28-criteria.md", "committed": false}, "done": false, "verdict": null}
rc=0
$ agent.py peek m28-probe-test --lines 6
✻ Churned for 2s · done 9:36 PM
(prompt and status-bar lines)
rc=0
$ agent.py wait m28-probe-build --interval 5
{"name": "m28-probe-build", "agent_status": "done", "minutes": 0.2, "commits_since": 0, "last_commit": "9dbb187 M28: WORKFLOW.md launch section rewritten to scripts/agent.py; feature doc", "tree_clean": true, "report": null, "done": true, "verdict": null}
rc=0
$ agent.py wait m28-probe-test --interval 5 --idle-limit 30
{"name": "m28-probe-test", "agent_status": "done", "minutes": 0.8, "commits_since": 0, "last_commit": "9dbb187 M28: WORKFLOW.md launch section rewritten to scripts/agent.py; feature doc", "tree_clean": true, "report": {"path": "docs/sprint/M28-criteria.md", "committed": false}, "done": false, "verdict": null}
agent.py wait: m28-probe-test has been done for 30 s with the work not done
real	0m30.142s
rc=3
$ curl -s localhost/dev/api/work   (after the next export)
herdr: {"known": true, "seen": 4, "reason": null, "elsewhere": 0, "skipped": 0, "coordinator": {"status": "idle", "word": "idle", "reason": null, "shown": "waiting on an agent"}}
other_agents: [{"name": "m28-probe-test", "status": "done", "why": "name is not <item>-<role>"}, {"name": "m28-probe-build", "status": "done", "why": "name is not <item>-<role>"}]
M28 stage: {"stage": {"stage": "implementing", "words": "implementing", "detail": "implementing, agent m28-build working", "blocked": false, "by": "agents"}, "words": null, "detail": null}
$ agent.py prompt m28-probe-test --file followup.txt
{"name": "m28-probe-test", "before": "done", "sent": "once", "agent_status": "working"}
rc=0
$ agent.py wait m28-probe-test --interval 3 --idle-limit 6
{"name": "m28-probe-test", "agent_status": "done", "minutes": 1.2, ..., "report": {"path": "docs/sprint/M28-criteria.md", "committed": false}, "done": false, "verdict": null}
agent.py wait: m28-probe-test has been done for 6 s with the work not done
rc=3
$ agent.py close m28-probe-test
{"name": "m28-probe-test", "workspace_id": "w3N", "workspace": "closed", "agent_status_before": "done"}
rc=0
$ agent.py close m28-probe-build
{"name": "m28-probe-build", "workspace_id": "w3P", "workspace": "closed", "agent_status_before": "done"}
rc=0
$ agent.py list
rc=0
$ herdr workspace list (labels)
['kronk-claude', 'm28-build']
$ herdr agent list (names)
[None, 'm28-build']
(both state directories empty)
```

The report named in run 2, `docs/sprint/M28-criteria.md`, existed before
the launch and was not touched, so `committed` stayed false: the
criterion-4 case, seen live.

## Not done, and found in passing

- **Not shown live:** `blocked` (a haiku probe told to run no command does
  not stop at a question), the resend path (each live prompt reached
  `working` on the first send), and `wait`'s exit 2. All three are tested
  against the fake.
- **`work_export.py` reads herdr's errors from the wrong stream.** Its
  `run()` sends stderr to `/dev/null`, and herdr writes
  `{"error":{"code":…}}` to stderr, so with herdr down `/work` will say
  "herdr agent list answered no JSON (exit 1)" instead of the error code;
  its test fake prints errors on stdout, so the suite cannot see it.
  Cosmetic (the reason string only; agent state is still "not known").
  Not changed: the criteria put `/work` out of scope.
- **Fix-round naming** (choice 2): `m28-fix`, `m28-fix2` from the tool
  against `m27-fix1` in WORKFLOW.md's naming paragraph. Both show on
  `/work`; the coordinator decides whether to number fix agents from 1.
- The coordinator's briefs no longer need the guard rails pasted in; a
  brief that still carries them will send them twice.
