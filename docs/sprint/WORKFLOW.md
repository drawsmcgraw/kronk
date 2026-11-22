# Sprint workflow: coordinator, build agent, test agent

How the refactor sprint (2026-11-15 to 2026-11-21, items M1 to M23) was
run, written down so a later session runs the next item the same way. Use
it when Drew orders a piece of work as a sprint item, or asks for "an Opus
agent" to build or investigate something. A small fix that needs neither a
build agent nor a test agent does not need this.

## Roles

| Role | Model | Flags | Where |
|---|---|---|---|
| Coordinator | the session Drew talks to | | its own herdr pane |
| Build agent, fix agent, investigator | Opus 5.5 (`claude-opus-5-5`) | `--effort high --dangerously-skip-permissions` | its own herdr workspace |
| End-to-end test agent | Fable 5.2 (`claude-fable-5-2`) | `--effort medium --dangerously-skip-permissions` | its own herdr workspace |

The coordinator writes the criteria and the briefs, rules on findings,
deploys, and reports to Drew. It does not build the item itself. The test
agent is always a different agent from the builder, and a fresh one each
round, so its reading is not anchored on the builder's.

One agent runs at a time. They share one working tree.

## Steps for one item

1. **Pass criteria.** The coordinator writes `docs/sprint/<item>-criteria.md`:
   source (Drew's words, the incident doc), scope, what is out of scope,
   numbered pass criteria, and any condition Drew set, quoted. Commit it.
2. **Build.** A build agent in a new workspace. Its brief names the files
   to read first, the jobs in order, the guard rails (below), and the
   report it writes: `docs/sprint/<item>-build.md`. It commits its work
   and its report and leaves the tree clean.
3. **Test.** A test agent in a new workspace. Its brief gives it the
   criteria and the build report, not the coordinator's opinion of the
   build. It tests on the dev pair and by reading production; it writes
   `docs/sprint/<item>-e2e.md` with a verdict per criterion and commits it.
4. **Fix loop.** On a failure: a fix agent (`<item>-fix1.md`), then a new
   test agent (`<item>-e2e2.md`). At most three failed fix rounds, then
   stop and report to Drew. The coordinator may rule on a finding instead
   of sending it back (ship a feature switched off, accept a stated
   limit); the ruling goes in the criteria file and the next brief.
5. **Promote.** The coordinator runs the suite, tags `sprint-<item>`, runs
   `scripts/deploy_tag.sh plan <tag>` then `deploy <tag>`, verifies live
   (CLAUDE.md, "Definition of done"), and adds the entry to
   `docs/sprint/PROMOTIONS.md`. Anything the coordinator changed after the
   last test is named as untested by an agent in the report to Drew.
6. **Close.** Each agent's workspace is closed once its report is
   committed. Only the coordinator's workspace stays.

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
(CLAUDE.md, "Git"). Since the merge of 2026-11-21 the working tree stays
on `main`, clean: the monthly patch run expects that branch
(`~/.config/kronk/upgrade.env`) and declines on a dirty tree. Nobody but
Drew pushes. Commits made by the coordinator or an agent end with the
attribution lines the session is given.

## Reporting to Drew

Each item ID gets a one-sentence description beside it; Drew has not
memorised the table. Failures are quoted, not summarised. What is Drew's
to do (a push, a spoken test, a decision) is listed last.
