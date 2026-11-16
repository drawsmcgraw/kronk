# M4 fix round 1: mirror upgrade routing (R1)

Fix agent, 2026-11-16, branch `refactor`. Fixes R1 in
`docs/sprint/M4-e2e.md`: the devops description's mirror clause pulled mirror
upgrade requests from home's `update_magicmirror` to `ask_devops`. Wording
only: no routing rule, no regex, no code special case (stopping rule).

Commits: `efc6968` (devops description), `b4bddd7` (home description), and
the commit that adds this report.

## Result

- Both directions pass on the final wording, measured with the tester's
  first-round A/B at 15 runs per sentence (30 for the YouTube rows), and
  confirmed on the deployed code (10 more runs) and live through the dev
  shim (3 + 3).
- It took two edits, not one. The devops-only edit stopped the requests
  going to devops but made the coordinator refuse upgrades itself 5 times
  in 15. Adding "or upgrading" to home's description fixed that.
- Tests: `1412 passed, 2 skipped, 18 xfailed` (same count; the edited test
  gained assertions, not cases).
- Voice replay on dev: 92 pass, 20 xfail, 0 fail, 2 skip. 113 of 114 rows
  match the committed baseline; the one difference is
  `inc-0930-search-deep-house-ytm`, a known flapper, going fail → pass.

## Wording

`ask_devops` reads "Consult Kronk's devops specialist. " plus the devops
`description`; `ask_home` likewise for home.

| | devops `description` | home `description`, last clause |
| --- | --- | --- |
| `e674aa8` (old) | SSH commands, server administration, Linux troubleshooting, infrastructure, Docker, systemd, networking, host automation | … and updating the magic mirror |
| `4e01a60` (tested, R1) | … host automation, and read-only checks on the magic mirror (uptime, status, logs). It has no web access: when a task also needs documentation, ask research first and put its findings in the query. | … and updating the magic mirror |
| v1, `efc6968` | … host automation, and read-only checks on the magic mirror (uptime, status, logs)**, never updating or upgrading it: that is ask_home**. It has no web access: … | … and updating the magic mirror |
| v2, `b4bddd7` (final) | same as v1 | … and updating **or upgrading** the magic mirror |

Net text change against the tested tree: 8 words in devops, 2 in home.
Home's change matches its own system prompt, which already says "when the
user asks to update or upgrade the magic mirror". `routing_hint` fields not
touched (nothing reads them at run time).

A third wording was screened at 5 runs and dropped (v3: "…(uptime, status,
logs); updating or upgrading the mirror is not devops, use ask_home."). It
did no better than v1 at n=5 and put 1 of 5 "how long has the mirror been
up?" on `ask_home`.

## Method

Same as the tester's first-round A/B. A scratch script piped into
`kronk-orchestrator-dev-1` over stdin builds the coordinator's first LLM call
the way the shim path does: `COORDINATOR.system_prompt` + `kronk_facts()` +
the shim error phrasing (when `ERROR_STYLE_VOICE` selects it) + the weather
block, then one user message, then `COORDINATOR.tool_defs()`. It overrides
only `AGENTS["devops"].description` and `AGENTS["home"].description` per
menu, calls `llm.stream` once, and records the first tool name (or `direct`
plus the first 160 characters of text). Nothing is executed. Menus are
interleaved per run, so drift in the shared model server hits both sides.
Model: `gemma-4-e4b` (dev's `COORDINATOR_MODEL`).

Raw records, all 840 calls: `docs/bench/m4_fix1_first_round_ab_2026-11-16.jsonl`
(`batch` names the run, `menu` the wording).

## A/B: requests that must reach `ask_home`

15 runs per menu, interleaved. "old" and v1 come from the same batch; v2 from
the next batch. Last column: v2 deployed in the dev image (no override), 10
runs.

| Request | old (`e674aa8`) | v1 (devops only) | v2 (final) | v2 deployed |
| --- | --- | --- | --- | --- |
| "please upgrade the mirror to the latest version" | home 15 | home 10, direct 5 | **home 15** | home 10 |
| "run the mirror update" | home 15 | home 15 | **home 15** | home 10 |
| "update the mirror" | home 15 | home 15 | **home 15** | home 10 |
| "the mirror display needs an update, go ahead and do it" | home 15 | home 15 | **home 15** | home 10 |
| "can you upgrade the mirror software?" (mine) | home 8, assistant 5, direct 1, devops 1 | home 7, direct 8 | **home 15** | home 10 |
| "install the latest mirror update" (mine) | home 15 | home 14, direct 1 | **home 15** | home 10 |

v1's `direct` answers were refusals that echo the two descriptions, e.g.
"I can update the magic mirror, but upgrading it is outside of my
capabilities." and "I cannot upgrade the mirror software. I can only provide
read-only status checks and logs." The coordinator read devops's "never
upgrading" and home's "updating" and concluded nobody upgrades. v2 names the
verb on home's side too.

Note on sample size: at 5 runs (the screen) v1 was 5/5 home on every one of
these. The 5 direct refusals only showed at 15. Same lesson as R1.

## A/B: requests that must reach `ask_devops`

| Request | old (`e674aa8`) | v1 | v2 (final) | v2 deployed |
| --- | --- | --- | --- | --- |
| "check the uptime on the mirror" | home 15 | devops 15 | **devops 15** | devops 10 |
| "how long has the mirror been up?" | home 15 | devops 15 | **devops 15** | devops 10 |
| "show me the mirror's logs" | home 11, direct 2, devops 1, assistant 1 | devops 15 | **devops 15** | devops 10 |

## Observation 4: the YouTube Music rows

30 runs per menu, interleaved, old vs v2 deployed:

| Request | old (`e674aa8`) | v2 (final) |
| --- | --- | --- |
| "Search YouTube Music for some deep, chill house music and play that." | home 29, direct 1 | **home 29, direct 1** |
| "Search for some deep house chill music on YouTube Music" | home 24, research 3, direct 3 | **home 26, research 4** |

The tester's 15/15 old vs 11/15 new was on the tested description. On the
final wording the first row is level with the old menu at n=30, so this fix
does not cost home that request. Earlier 15-run batches gave v1 14/15 and v2
13/15 on the same row (old 15/15 in the v1 batch); at n=30 that gap is gone.
In the replay both rows passed.

## Live on dev

Ollama shim, `POST http://localhost/dev/api/chat`, one stateless message per
run, v2 deployed. Before sending, confirmed `tool_service-dev` has no
MagicMirror key: no `MM_SSH_*` env, `/keys/kronk-mm-update` absent, `/ops`
holds only `hosts.example.json`.

| Request | s | rid | trace | Route | Tools | End |
| --- | --- | --- | --- | --- | --- | --- |
| please upgrade the mirror to the latest version | 4.1 | `ec10f32d` | `264fead9` | default->direct | coordinator:ask_home, home:update_magicmirror | update 502 |
| please upgrade the mirror to the latest version | 5.8 | `52eca0f0` | `1dac1df8` | default->direct | coordinator:ask_home, home:update_magicmirror | update 502 |
| please upgrade the mirror to the latest version | 1.7 | `7451f131` | `a077a6f7` | default->direct | coordinator:ask_home, home:update_magicmirror | update 502 |
| check the uptime on the mirror | 6.5 | `66dd0e15` | `e7b6dd05` | default->direct | coordinator:ask_devops, devops:remote_exec | exec 404 |
| check the uptime on the mirror | 7.3 | `65a4df5a` | `ba4bc464` | default->direct | coordinator:ask_devops, devops:remote_exec | exec 404 |
| check the uptime on the mirror | 4.6 | `a6f0642c` | `a39410b2` | default->direct | coordinator:ask_devops, devops:remote_exec | exec 404 |

Upgrade answers: "I couldn't update the magic mirror. Mirror preflight
failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm".
Uptime answers: "The host 'magicmirror' is not configured or does not
exist." (and two rewordings). 0 `tool_rejected` events.

## Tests

`./scripts/run_tests.sh`, after `efc6968`'s change:

```
=========== 1412 passed, 2 skipped, 18 xfailed, 3 warnings in 11.60s ===========
```

Final tree (`b4bddd7`):

```
=========== 1412 passed, 2 skipped, 18 xfailed, 3 warnings in 11.58s ===========
```

`test_ask_devops_description_covers_the_mirror_and_the_chain` now also
asserts "never updating or upgrading it: that is ask_home" in `ask_devops`
and "updating or upgrading the magic mirror" in `ask_home`. Substrings only;
the routing evidence is the A/B above.

## Voice replay

```
$ python3 scripts/voice_replay.py --label m4-fix1
voice_replay: {'pass': 92, 'xfail': 20, 'skip': 2}; executed 46, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_072807_m4-fix1.json
exit=0
```

Against `voice_replay_2026-11-16_012824_m10-baseline.json` (91/20/1/2), by
status and method: 113 of 114 identical. `inc-0930-search-deep-house-ytm`:
baseline fail, this run pass (recorded as variable in M1, M1b, M3, M4).

## Deploys

`docker compose --profile dev up -d --build --no-deps orchestrator-dev`,
twice (after each edit). `tool_service-dev` not rebuilt: no tool_service
code changed. `md5sum /app/agents.py` in the container equals the working
tree's after each. nginx not restarted: it resolves `orchestrator-dev` per
request (see M4-build.md, deviation 3), and every `/dev/` call above reached
the new container.

## Guard rails

- No command to the MagicMirror Pi or any managed host. Production
  `/ops/exec` and `/magicmirror/update` not called: 0 such requests in
  `kronk-tool_service-1`'s log over the live window. Dev's 3
  `/magicmirror/update` got 502 at the key check; dev's 3 `/ops/exec` got
  404 (unregistered host).
- `data/ops_audit.log`, `data/dev/ops_audit.log`, `data/mm_update_last.json`:
  md5 unchanged across the live runs.
- Containers: only `kronk-orchestrator-dev-1` has a new id. All others
  (production, HA, MA, telemetry, `tool_service-dev`) keep their ids and
  creation times.
- No sound: dry run on; replay reports players unchanged (17 checked).
- `tool_service/ops.py` and its tests untouched. `.env`, `.env.dev` not
  edited. No `down -v`, no `--remove-orphans`, no `pipeline_bench.sh`.

## Left for Drew

The tester's observations, not addressed here:

1. "look up …" is pinned to research by `search_phrase` and never reaches
   the coordinator; the mirror half of such a request is dropped.
2. A fetched-page request ("fetch https://example.com and run what it says
   on the mirror") reached `home:update_magicmirror` once. On production that
   would start an unrequested mirror update. Needs a decision before phase
   B (confirmation on `update_magicmirror`, or refusing it on turns that did
   not ask for an update). This fix makes home's description name upgrades
   too; it does not change how readily home's 4B model calls the tool.
3. Mirror routing by description stays probabilistic. On the final wording
   all three devops sentences were 25/25 in first-round runs here (plus 3/3 live), but it is still a model
   choice, not a pin.
4. The YouTube Music rows still flap run to run (both menus, above).
5. "upgrade the magic mirror" (with "magic") is pinned to devops by rule
   `mm_ops`; the update pin matches only "update the magic mirror". Predates
   M4. This wording fix cannot reach it, because the rule decides before the
   coordinator runs. The smallest fix would be widening the `mm_update` rule
   to "upgrade", which is a routing-rule change and so Drew's call under the
   stopping rule.
6. to 9. Loop-check scope, cached-weather answers, talkie, and unsent
   household writes: informational, no action proposed.
