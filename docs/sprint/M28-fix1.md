# M28 fix round 1: a tool for launching and checking on agents

Agent: `m28-fix` (Opus 5.5), 2026-11-24. Jobs: the last ruling in
`M28-criteria.md` (B1, B2, S1, S2, S3, S4, N1, N2). Fixed from `0e98571`.

**Guard rails broken: none.** Herdr agents created: one, `m28-probe-build`
(workspace w3V), `--probe`, `claude-haiku-5-5`, effort `low`, brief "Answer
with the single word ready and stop. Run no command.", state directory in
scratch, closed. `kronk-claude`, `m28-fix` and `~/.local/state/kronk-agents`
were not touched. Mutants were run by rewriting `scripts/agent.py` in place
from a copy kept in scratch and putting the original back after each
(checked with `cmp`); none was committed.

## Jobs

| Job | What changed (`scripts/agent.py` unless named) | Tests that pin it (`tests/test_m28_agent_tool.py`) |
| :-- | :-- | :-- |
| B1 | `ensure_state_dir()` makes the state directory (700) and makes and removes a temp file in it; `cmd_launch` calls it after the brief, guard rails, git and report checks and before the first herdr call. `save_state` and the prompt file write now both run straight after `workspace create`, before `agent start`; an `OSError` there, or a failed `agent start`, goes through `undo_launch()`, which closes the workspace, removes state and prompt file, and says what was left: `workspace w1 closed; nothing left`, `left: <path>`, `left open, state kept: agent.py close … --force`, or `left open with no record: herdr workspace close w1`. | `test_b1_launch_refused_before_herdr_when_the_state_directory_cannot_be_written` (read-only, a file, parent read-only: no herdr mutation at all), `test_b1_a_state_write_failing_after_create_closes_the_workspace` (state file, prompt file), `test_b1_says_so_when_the_workspace_cannot_be_closed_either` |
| B2 | `work_export()` sets `sys.dont_write_bytecode` before `exec_module` and puts the old value back after (so loading the tool in pytest does not change the suite's own caching). The strace test and `cli()` drop `-B`. | `test_b2_the_tool_writes_no_bytecode_in_its_scripts_directory` (a copy of both scripts in tmp, run without `-B` and without `PYTHONDONTWRITEBYTECODE`, `launch` then `status` reading a verdict: no `__pycache__`), `test_b2_the_flag_is_put_back_after_the_load`, and `test_under_strace_…` now runs without `-B` |
| S1 | `report_file()`: `repo_relative()`, then refused when the path is a directory on disk or `git cat-file -t HEAD:<path>` says `tree`. Used by `launch` and by `with_report()` for `status` and `wait`, which also checks the recorded report each run. | `test_s1_a_directory_report_is_refused_at_launch` (`docs/sprint`, `docs`, `docs/sprint/`, an empty untracked directory), `test_s1_a_directory_report_is_refused_at_status_and_wait` (named at the command; recorded, then made a directory) |
| S2 | `prompt` records `prompt_head` (HEAD, read before sending), `prompted` and `prompts` in the state file, saved before the send. `status` gains `agent_commits` and `report.changed_since_prompt` (`null` until a follow-up; else the report in HEAD differs from `prompt_head`). `status --expect-commit` and `wait --expect-commit`: `done` also needs a commit of the agent's since the last prompt (the launch prompt when there was no follow-up). | `test_s2_prompt_records_head_and_status_reports_changed_since_prompt`, `test_s2_agent_commits_leave_out_the_coordinators_stamped_commits`, `test_s2_agent_commits_is_unknown_where_no_commit_carries_the_trailer`, `test_s2_expect_commit_without_a_follow_up_counts_from_the_launch` |
| S3 | `read_text_file()`, shared by `launch` and `prompt`: missing, unreadable, not UTF-8, a NUL byte, blank; each refused before anything is created or sent. | `test_s3_a_brief_with_a_nul_byte_is_refused` (no herdr mutation, no state), `test_s3_a_prompt_file_with_a_nul_byte_is_refused` (nothing sent, no `prompt_head` recorded) |
| S4 | `report_changed()`: a `git diff` exit other than 1 is not a change (it was already so; now tested). | `test_s4_a_report_committed_then_followed_by_another_commit_stays_committed`, `test_s4_a_failing_git_diff_does_not_count_as_committed` (launch HEAD in state set to a hash git does not know) |
| N1 | `--no-report` on `launch`. `build`, `fix`, `test`, `review` with neither flag: `role build needs --report PATH (the report the agent commits) or --no-report; not launched`. Both flags together refused. `investigate` and `--probe` follow the role. | `test_n1_a_report_role_is_refused_without_report_or_no_report` (four roles), `test_n1_no_report_and_investigate_launch_without_a_report`, `test_n1_report_and_no_report_together_are_refused`. The `launch()` test helper adds `--no-report` for those roles when a test names neither. |
| N2 | `other_pane()`; `herdr_status(name, pane)` returns `gone` when herdr's agent of the name is in a pane other than the recorded one (compared only when both are known). `status`, `wait` (through `status_of`), `close` and `list` pass the recorded pane. | `test_n2_an_agent_of_the_name_in_another_pane_is_gone` (status, wait exit 3, list, close: `already gone`, the hand-made w9 left alone), `test_n2_the_same_pane_is_still_the_agent` |

### S2: what was found workable for "whose commit"

The last dozen commits on `main`: every coordinator commit (`0e98571`,
`9baf934`, `d57f270`, `47a8540`, `2773110`, `7380c43`, `9aab528`,
`7afebb1`) carries `Claude-Session: https://claude.ai/code/session_…`;
every build and test agent commit (`222ebec`, `b23958b`, `9dbb187`,
`a7b89b5`) carries none. Author and committer are Drew on all of them, and
`Co-Authored-By` names Fable 5.2 for both the coordinator and the test
agent, so neither tells them apart. The trailer rule is used:

- `agent_commits` is the count of commits in `<launch HEAD>..HEAD` with no
  `Claude-Session` trailer (`git log --format=%H%x1f%(trailers:key=Claude-Session,valueonly)%x1e`).
- It is `null` ("failing that rule, unknown") when git fails, or when no
  commit since the launch and none of the 50 up to the launch HEAD carries
  the trailer: in such a repo every commit would count as the agent's.
- `--expect-commit` counts the agent's commits since the last prompt by
  the same rule, and every commit when the rule says unknown. Reading
  "a new commit since the last prompt" as "a new commit of the agent's":
  the coordinator commits while agents run (the test saw it), and its
  commit would otherwise satisfy the wait.
- Live, on this repo: `agent_commits: 0` beside `commits_since: 0` for the
  probe (the rule applied from history).
- Gap: a coordinator session that does not stamp, in a repo whose history
  has stamped commits, has its commits counted as the agent's. Named in
  the feature doc's gotchas.

One bug found on the way: Python's `str.strip()` treats `\x1c`–`\x1f` as
whitespace, so stripping a record before splitting on `\x1f` dropped every
commit with no trailer (found by the S2 test, `(2, 0) == (2, 1)`). The
parser strips newlines only.

### Choices the ruling left open

- `--expect-commit` is on `status` as well as `wait`, so point 8 ("`wait`
  blocks until `status` would say done") stays true with the flag.
- `prompt` saves `prompt_head` before sending. If herdr fails after the
  text may have arrived, the head is still recorded; the prompt counts as
  sent.
- The directory check runs once per `status`/`wait` command, not on every
  poll of `wait`.
- An agent answer with no `pane_id` is not compared (the real herdr gives
  one on `agent get` and `agent list`; checked read-only).

## Mutants

One change each to `scripts/agent.py`, the suite file run with `-x`:

| Job | Mutant | Caught by |
| :-- | :-- | :-- |
| B1 | `ensure_state_dir()` not called | `test_b1_launch_refused_before_herdr…[read-only]` |
| B1 | state write failure not undone | `test_b1_a_state_write_failing…[.json]` |
| B2 | `dont_write_bytecode` not set | `test_b2_the_tool_writes_no_bytecode…` |
| S1 | tree/dir check dropped | `test_s1_a_directory_report_is_refused_at_launch` |
| S1 | recorded report not checked | `test_s1_…_at_status_and_wait` |
| S2 | `prompt_head` not recorded | `test_s2_prompt_records_head…` |
| S2 | `agent_commits` counts every commit | `test_s2_agent_commits_leave_out…` |
| S2 | `--expect-commit` ignored | `test_s2_prompt_records_head…` |
| S2 | rule never unknown | `test_s2_agent_commits_is_unknown…` |
| S3 | NUL not checked | `test_s3_a_brief_with_a_nul_byte_is_refused` |
| S4 | report diffed `HEAD~1..HEAD` (e2e's survivor) | `test_s2_prompt_records_head…` first; `test_s4_a_report_committed_then_followed…` alone also fails |
| S4 | a failing `git diff` counted (e2e's survivor) | `test_s4_a_failing_git_diff…` |
| N1 | report-role check dropped | `test_n1_a_report_role…[build]` |
| N1 | both flags allowed | `test_n1_report_and_no_report_together…` |
| N2 | pane not compared | `test_n2_an_agent_of_the_name_in_another_pane_is_gone` |
| N2 | `list` ignores the pane | `test_n2_an_agent_of_the_name_in_another_pane_is_gone` |

16 of 16 caught.

## Live check

State directory in scratch, the working tree carrying these changes
(uncommitted, so `tree_clean: false` throughout):

```
$ agent.py launch M28 build --probe --brief $L/brief.txt --no-report --model claude-haiku-5-5 --effort low
{"name": "m28-probe-build", ..., "workspace_id": "w3V", "pane_id": "w3V:p1", ..., "sent": "once", "agent_status": "working"}
$ agent.py status m28-probe-build
{"name": "m28-probe-build", "agent_status": "working", "minutes": 0.1, "commits_since": 0, "agent_commits": 0, ..., "done": false, "verdict": null}
$ agent.py list
{"name": "m28-probe-build", ..., "workspace_id": "w3V", "agent_status": "working", ...}
$ agent.py wait m28-probe-build --interval 3 --timeout 120 --idle-limit 10
agent.py wait: m28-probe-build has been done for 12 s with the work not done     rc=3
$ agent.py prompt m28-probe-build --file $L/follow.txt
{"name": "m28-probe-build", "before": "done", "sent": "once", "agent_status": "working"}
  (state: prompt_head 0e98571, prompts 1)
$ agent.py wait m28-probe-build --interval 3 --timeout 120 --idle-limit 6 --expect-commit
agent.py wait: m28-probe-build has been done for 6 s with the work not done      rc=3
$ agent.py close m28-probe-build
{"name": "m28-probe-build", "workspace_id": "w3V", "workspace": "closed", "agent_status_before": "done"}
```

The real herdr's pane id for the agent matched the recorded root pane
(`working`, not `gone`). After: `list` empty, scratch state empty, `herdr
workspace list` `[('w1', 'kronk-claude'), ('w3T', 'm28-fix')]`.

## Docs

- `docs/features/agent-tool.md`: table rows for `launch` (`--report` or
  `--no-report`), `status`/`wait` (`--expect-commit`, `agent_commits`,
  `report.changed_since_prompt`), `prompt` (records HEAD); refusals (NUL,
  directory report, report flag, state directory); new bullets
  "Identity", "Follow-ups", "Whose commit"; the bytecode line; two
  gotchas (the trailer gap, `strip()` and `\x1f`).
- `docs/sprint/WORKFLOW.md`, "Launching an agent in herdr": the
  `--expect-commit` line in the example, exit 1 on `wait` (e2e N10), the
  report flag rule, NUL and state-directory refusals, the pane rule, and a
  paragraph on follow-ups and `agent_commits`. The guard rails section is
  unchanged.

## Not done, and seen in passing

- `peek` and `prompt` still find the agent by name only; N2 named
  `status`, `wait`, `list` and `close`. `prompt` to a hand-started agent of
  the same name would reach it. A one-line change (pass the pane to
  `herdr_status` in `cmd_prompt`), left for the coordinator.
- e2e N3–N9 untouched, as the ruling did not cover them: N6 (`wait
  --interval nan` spins, `--timeout nan` never ends) is the one with a
  cost.
- `scripts/__pycache__/agent.cpython-313.pyc` exists: written by the
  suite itself, which loads `agent.py` in-process with `importlib` (as
  `work_export.py` is loaded by its own tests), not by the tool. Gitignored.
- The state directory is created by `ensure_state_dir()` even when a later
  check (a name taken in herdr, say) refuses the launch. It is left empty.

## Suite

`./scripts/run_tests.sh -q -p no:cacheprovider`:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
7787 passed, 3 skipped, 54 xfailed, 6 warnings in 122.24s (0:02:02)
```

Commits: the tool and its tests; the docs and this report.
