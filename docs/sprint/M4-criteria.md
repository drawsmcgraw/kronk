# M4 pass criteria: separate web tools from remote exec

Written by the sprint coordinator on 2026-11-16, before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M4. Separate
web tools from remote exec", decided by Drew 2026-11-09: option A; and the M4
line of "Verified on Kronk, 2026-11-15"). The tester checks against this
file, not against the build report.

Note on promotion: M3 failed its end-to-end test and its fix round is waiting
on Drew, so its unpassed classifier is on this branch. M4 is built and tested
on the dev path now and promoted only once M3 passes, or once Drew says to
ship it ahead.

## Criteria

1. The `devops` agent's `tool_names` is exactly `["remote_exec"]`. Its
   prompt no longer tells it to look things up on the web. No other agent's
   tools changed: `research` and `coding` keep `web_search` and
   `fetch_url`; `coding` does not gain `remote_exec`.
2. No agent holds both a web tool and `remote_exec`. A unit test asserts
   this for every entry in `AGENTS`, so a future edit that recombines them
   fails the suite.
3. The devops loop cannot call a web tool even if the model emits one: a
   `web_search` or `fetch_url` call inside the devops agent's loop is
   rejected as not available to that agent, with a test.
4. The coordinator can still get documentation into a devops task by
   calling `ask_research` and then `ask_devops`. The coordinator prompt or
   the devops agent's description says so in one sentence if that is needed
   for the model to do it. Shown on the dev path, at least five runs of a
   request that needs both ("look up what the pm2 flag for non-streaming
   logs is, then tell me how you would check the mirror's logs"): record
   which tools were called each run. No run shows a web tool call from
   inside the devops agent. Dev has no SSH key, so `remote_exec` cannot
   reach a host; a refusal or missing-key error from it is the expected
   end of the chain.
5. A plain devops request ("check the uptime on the mirror") still routes
   to `ask_devops` on the dev path.
6. `scripts/devops_model_bench.py` is unaffected (it carries its own
   prompt and tools). Stated with evidence; re-run only if it can run
   without sending commands to a managed host. If it cannot, say so and do
   not run it.
7. README's agent table and any feature doc that lists the devops tools are
   corrected. The residual risk from the plan (command output is untrusted
   text; keep the confirmation gate for phase B) is recorded where the
   mirror plan or the ops docs will carry it.
8. `./scripts/run_tests.sh` passes. Baseline before M4:
   `1396 passed, 2 skipped, 18 xfailed`.
9. The M10 live replay on dev matches the committed baseline apart from
   known run-to-run variance.
10. No command was sent to the MagicMirror Pi or any managed host;
    production's `/ops/exec` was not called; production containers, HA and
    Music Assistant untouched.

## Reports

Build: `docs/sprint/M4-build.md`. End to end: `docs/sprint/M4-e2e.md`.
