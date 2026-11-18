# M18 build report: an upgrade cadence

Build agent, 2026-11-18 (UTC), branch `refactor`. Inputs: CLAUDE.md, the
plan's section "M18. An upgrade cadence" and its M18 verification line,
`docs/sprint/M18-criteria.md`, `scripts/deploy_tag.sh`,
`docs/runbooks/searxng-bump.md`, `docs/plans/MA_FORK_STATUS.md`,
`docs/sprint/M12-fix1.md` and `M12-fix2.md`, `docs/features/config-directory.md`,
`scripts/lib/notify.sh`, every compose file, the Dockerfiles, the lockfiles
and `systemd/`. No component was upgraded. Nothing was installed. No notice
was sent.

## Commits

| Commit | Step |
| --- | --- |
| `0b6fc30` | M18 step 1: upgrade inventory (pins, what runs, what is newest) and read-only lookups |
| `6933a1b` | M18 step 2: the version rules as data, with tests |
| `16e2bac` | M18 step 3: the minor-upgrade script: plan, --dry-run, apply, verify, rollback, declines |
| `10788a0` | M18 step 4: notices, the status file, and the /services warning |
| `f32fc6f` | M18 step 5: the quarterly major-release check and the first report |
| `b04a378` | M18 step 6: the major-upgrade runbook |
| `fd4faf6` | M18 step 7: the rehearsal on a scratch compose project, and what it found |
| `7e6a79f` | M18 step 8: reference timer units and docs |
| `5f333ea` | M18: dev replay result after the dev-pair rebuild (m18-build) |
| `e8742fb` | M18: undeployed-path check reads NUL-separated names (a path with spaces is one path) |
| this commit | the build report |

The code is one file, `scripts/upgrades.py`, written ahead of the step
commits; each step's commit carries the part of it that step tests, and
later steps carry fixes found by the rehearsal and the real dry run.

## What was built

| File | What |
| --- | --- |
| `scripts/upgrade_rules.toml` | The rules: one entry per pin with a comment, plus settings, the replay flappers and the report's integration points |
| `scripts/upgrades.py` | Inventory, planning, the monthly run, the quarterly check, notices, status, `ack`, `test-notice` |
| `scripts/upgrade_minor.sh`, `scripts/upgrade_major_check.sh` | Thin wrappers the timers run |
| `scripts/upgrade_rehearsal.py` | The scratch-project rehearsal (criterion 15), re-runnable, with `cleanup` |
| `orchestrator/upgrades_api.py`, `main.py`, `static/services.html` | `GET /api/upgrades` (read-only) and the "Upgrade cadence" card on `/services` |
| `systemd/kronk-upgrade-{minor,major-check,notices}.{service,timer}` | Reference units, not installed |
| `docs/runbooks/major-upgrade.md` | The runbook for a major upgrade, with a Home Assistant section |
| `docs/features/upgrade-cadence.md` | The guide |
| `docs/upgrades/inventory-2026-11-18.md` | The check-first inventory |
| `docs/upgrades/2026-11-18.md` | The first major-upgrade report |
| `tests/test_m18_*.py`, `tests/m18_helpers.py` | 149 tests |

## Check first (criterion 1)

Read-only. Pins from the repo; what runs from `docker inspect`, `docker exec`
version commands, `systemctl --user` and the Whisper venv's `dist-info`;
newest releases from anonymous GETs with the client name `upgrade-check/1.0`.
"Newest minor" applies the rule without the 14-day age limit (the plan
applies it: Grafana's monthly move is 13.2.5 today, not 13.2.6).

| Component | Rule | Pin | Written in | Running | Newest minor (rule) | Newest of any kind | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tempo | auto | 3.1.1 | docker-compose.telemetry.yml:51 | 3.1.1 (64e2fbd56d9c, same id as the tag) | none newer | 3.1.1 | github:grafana/tempo |
| grafana | auto | 13.2.4 | docker-compose.telemetry.yml:63 | 13.2.4 (dbbc38ea2110, same id as the tag) | 13.2.6 | 13.3.1 | github:grafana/grafana |
| wyoming-piper | auto | 2.2.2 | docker-compose.yml:208 | 2.2.2 (c874e4a04657, same id as the tag) | none newer | 2.5.4 | github:rhasspy/wyoming-piper |
| searxng | auto | 2026.11.9-57ff753ad | docker-compose.yml:31 | 2026.11.9-57ff753ad (287af61f1a13, same id as the tag) | 2026.11.17-b99cf7496 | 2026.11.17-b99cf7496 | dockerhub:searxng/searxng |
| nginx | auto | 1.29-alpine | docker-compose.yml:12 | 1.29.8 | none newer | 1.31.8-alpine | dockerhub:library/nginx |
| litellm | auto | v1.88.1 | docker-compose.yml:155 | v1.88.1 (c98c9395c56a, same id as the tag) | v1.88.6 | v1.110.0 | github:BerriAI/litellm |
| python-base-orchestrator | auto | 3.12-slim | orchestrator/Dockerfile:1 | 3.12.13 | 3.12.16-slim | 3.12.16-slim | dockerhub:library/python |
| python-base-tool_service | auto | 3.12-slim | tool_service/Dockerfile:1 | 3.12.13 | 3.12.16-slim | 3.12.16-slim | dockerhub:library/python |
| python-base-voice_tracer | auto | 3.12-slim | voice_tracer/Dockerfile:1 | 3.12.13 | 3.12.16-slim | 3.12.16-slim | dockerhub:library/python |
| uv | report | uv | orchestrator/Dockerfile:13 | 0.13.9 | - | 0.13.9 | pypi:uv |
| git-orchestrator | none | 1:2.47.* | orchestrator/Dockerfile:10 | 2.47.3 | no source | no source | - |
| homeassistant | auto | 2026.5.4 | docker-compose.ha.yml:41 | 2026.5.4 (ceb1202133a5, same id as the tag) | none newer | 2026.11.2 | github:home-assistant/core |
| lock-orchestrator | report | 41 packages | orchestrator/requirements.lock | matches the lock | 12 patch release(s) | 20 newer minor/major | pypi |
| lock-tool_service | report | 55 packages | tool_service/requirements.lock | matches the lock | 13 patch release(s) | 24 newer minor/major | pypi |
| lock-voice_tracer | report | 22 packages | voice_tracer/requirements.lock | matches the lock | 5 patch release(s) | 2 newer minor/major | pypi |
| llama.cpp | none | per unit (ExecStart build directory) | systemd/llama-gemma4-e4b.service | llama-gemma3-4b: llama-b9585-vulkan (active); llama-gemma4-e4b: llama-b9611-vulkan (active); llama-devstral-q4: llama-b9611-vulkan (active); llama-devstral: llama-gfx1151-rocm7 (inactive); llama-mistral-nemo: llama-gfx1151-rocm7 (inactive); llama-bonsai: bonsai-gfx1151-rocm7 (inactive); llama-talkie: talkie-gfx1151-rocm7 (inactive) | n/a (nothing is minor) | b12498 | github:ggml-org/llama.cpp |
| wyoming-faster-whisper | none | none in the repo | ~/services/wyoming-whisper/.venv | 3.1.0 | none newer | 3.9.0 | pypi:wyoming-faster-whisper |
| ma-blueprint | none | 20250404 | ha/blueprints/upstream/mass_assist_blueprint_en.yaml | HA (installed copy, see ha/blueprints) | n/a | 20250404 | raw.githubusercontent.com (upstream file) |
| music-assistant | excluded | 2.11.0b5-kronk.9 | docker-compose.ma.yml:53 | 2.11.0b5-kronk.9 (847b7601461f, same id as the tag) | none newer | upstream stable 2.10.12, pre-release 2.11.0.dev2026111803 | github:music-assistant/server |
| bgutil-pot-provider | excluded | 2.0.0 | docker-compose.ma.yml:82 | 2.0.0 (ed86b6fdd5e4, same id as the tag) | 2.0.2 | 2.0.2 | github:Brainicism/bgutil-ytdlp-pot-provider |

Pins that do not match what runs, or float:

- **nginx** `1.29-alpine` floats (not an exact version). It runs 1.29.8, the
  last of the 1.29 mainline; newer lines exist up to 1.31.8. Skipped by the
  monthly run; proposal in the report: pin an exact `1.30.N-alpine`.
- **Python base images** `python:3.12-slim` float in all three Dockerfiles.
  The locally cached base is 3.12.13 (8 months old); 3.12.16 exists. A
  rebuild does not pull, so the base never moves on its own. Skipped.
- **uv** is `pip install uv`, unpinned: the running images have uv 0.13.9
  (orchestrator), 0.11.26 (tool_service) and 0.11.2 (voice_tracer). Skipped.
- **git** in the orchestrator image is held to the Debian 2.47 series by
  design (2.47.3 runs).
- The **lockfiles** match what the three production containers run
  (checked package by package with `importlib.metadata` in each container).
  The OpenTelemetry instrumentation packages are pinned at a beta, `0.66b1`,
  and `python-dateutil` at `2.9.0.post0`: the rules do not compare those.
  `tests/requirements.txt` is not hash-locked and carries a range
  (`fastembed>=0.4`); the test venv is not built from the service locks.
- **Home Assistant**: the plan's verification saw HA created from `:stable`
  with only that tag local. M12's runbook (H3) tagged the same image id as
  `2026.5.4`; the container now runs `ghcr.io/home-assistant/home-assistant:2026.5.4`,
  image `sha256:ceb1202133a5…`, the same id as the tag. Matches.
- **wyoming-piper** runs from the `sprint-M17` release directory while the
  rest of the Kronk stack runs `sprint-M21-step2`: compose did not recreate
  it because its configuration did not change. Same image as the pin.
- **llama.cpp**: active units run b9585 (router, `llama-gemma3-4b`) and b9611
  (`llama-gemma4-e4b`, `llama-devstral-q4`); the inactive units point at
  hand builds without a build number. The installed units match `systemd/`.
- **Whisper**: production and dev units share one venv,
  wyoming-faster-whisper 3.1.0 (ctranslate2 4.7.2 ROCm build); no pin in the
  repo.
- **The MA blueprint** fork's upstream is still at version 20250404.
- **Music Assistant**: `docs/plans/MA_FORK_STATUS.md` (snapshot 2026-10-16)
  describes `2.11.0b5-kronk.7` and the upstream Pandora PR as open; the
  compose file now pins `2.11.0b5-kronk.9` (the create-station patch), which
  is what runs; the plan (2026-11-14) says the upstream PR is not being
  pursued. Nothing merged upstream; MA stays excluded and was not touched.
- Not a pin, noted: before the dev rebuild `kronk-tool_service-dev-1` ran a
  dangling image id (`18851868f8d8`), built from an older tree; it was not
  recreated by this build (`up --build` produced the same image).

### Hosts contacted

All anonymous GETs with `User-Agent: upgrade-check/1.0`, nothing about this
system in the request (no hostname, token, path or name):

- `api.github.com`: release lists and release-by-tag for home-assistant/core,
  BerriAI/litellm, grafana/tempo, grafana/grafana, rhasspy/wyoming-piper,
  ggml-org/llama.cpp, music-assistant/server, Brainicism/bgutil-ytdlp-pot-provider;
  `rate_limit` (budget checks).
- `ghcr.io`: anonymous pull tokens and `tags/list` for home-assistant/home-assistant
  and berriai/litellm.
- `hub.docker.com`: tag lists and tag lookups for grafana/tempo,
  grafana/grafana, rhasspy/wyoming-piper, searxng/searxng, library/nginx,
  library/python, library/busybox.
- `pypi.org`: the JSON API for each package in the three lockfiles, uv and
  wyoming-faster-whisper.
- `raw.githubusercontent.com`: the upstream MA voice blueprint file.
- `www.home-assistant.io`: the release posts for 2026.5 to 2026.11.
- Docker Hub's registry, by the Docker daemon, for the rehearsal's
  `busybox:1.36.0` and `busybox:1.36.1` pulls (`docker compose pull`,
  `docker pull`). No other image was pulled.
- Local only otherwise: this host's loopback (HA's REST API read-only,
  `/probe/voice_tracer`, `/dev/*`) and Docker.

No lookup needed a login; none was skipped for that reason.

## The rules (criterion 2)

`scripts/upgrade_rules.toml`, one `[[component]]` per pin, each with a
comment. A rule is a regex `pattern` whose groups compare as integers, and
`same`, how many leading groups a minor upgrade keeps equal (2 = patch
within X.Y; 0 = any newer). `minor` is `auto`, `report`, `none` or
`excluded`. The loader refuses Music Assistant or llama.cpp as anything but
excluded/none, and refuses a lockfile, llama build or host venv with a
deploy. Settings: `expected_branch` (`main`; overridden by
`KRONK_UPGRADE_BRANCH`), `min_age_days` 14 (the telemetry pins' two-week
rule), `quiet_minutes` 5, `select_quiet_minutes` 15 (M12 H6), `max_attempts`
7, `inert_paths`, `dev_services`; `[replay] flappers`; `[report]`.

| Component | Pattern | Monthly (auto) | Notes |
| --- | --- | --- | --- |
| Home Assistant | `YYYY.M.P` | patch inside the month | last in the order |
| LiteLLM | `vX.Y.Z` | patch | `-stable`, `-nightly`, `-rc`, `-dev` tags are not releases |
| Tempo, Grafana, Piper | `X.Y.Z` | patch | |
| SearXNG | `YYYY.M.D-<sha>`, the date compared | any newer build ≥ 14 days old | an interpretation; see "Needs Drew" |
| nginx | `X.Y.Z-alpine` | patch, once pinned exactly | today skipped: floating pin |
| Python base images | `X.Y.Z-slim` | patch, once pinned exactly | today skipped |
| uv | `uv==X.Y.Z` | report | today unpinned |
| git (orchestrator) | Debian series | none | by design |
| Python lockfiles | `X.Y[.Z]` | report only | see departures |
| llama.cpp | `b<N>` | none | GitHub flags every build pre-release: `ignore_prerelease_flag` |
| wyoming-faster-whisper | `X.Y.Z` | none | not in Drew's table; GPU build |
| MA blueprint | `YYYYMMDD` | none | hand-merged fork |
| Music Assistant, PO-token helper | | excluded | |

## The monthly run, step by step (criteria 2 to 8)

`scripts/upgrade_minor.sh` (= `python3 scripts/upgrades.py minor`):

1. Takes the lock `data/upgrades/lock` (another run: declines). Reads the
   status file; if this month is done, or 7 attempts were made, exits.
2. Retries pending notices.
3. Plans each component: reads the pin (exactly one matching line), skips a
   pin the rule cannot parse, finds releases (GitHub for dates and notes,
   the registry for which tags exist), takes the newest minor release that
   is not a pre-release, is at least 14 days old with a known date, and
   exists in the registry. Nothing to do: records it, no notice.
4. Declines (logged, status `declined (attempt n of 7)`, retried the next
   night) when any of: the last rollback failed; a `deploy_tag.sh`
   deploy/plan/seed is running; the branch is not the expected one; the tree
   is not clean (tracked changes or untracked files); a satellite is not
   idle or changed state in the last 5 minutes; a media player is playing or
   buffering; voice_tracer holds a run; nginx logged a chat or voice request
   (`/api/chat`, `/message`, `/v1/chat/completions`, `/voice`, and the
   `/dev/` forms) in the last 5 minutes; HA's states, the probe or nginx's
   log cannot be read. On the 7th decline of a month it sends one notice
   ("never found a quiet moment") and raises the `/services` warning.
5. Baseline: `./scripts/run_tests.sh -q` and `voice_replay.py` on `kronk-dev`
   (results in `data/upgrades/runs/`). Either failing: declines.
6. Components in `order`: tempo, grafana, wyoming-piper, searxng, nginx,
   litellm, the base images, homeassistant. For each:
   - Kronk-stack components: production's orchestrator must run from a tag
     (`deploy_tag.sh status`), every non-dev container from some tag, and
     `git diff <tag> HEAD` may touch only `inert_paths`; otherwise "not
     started". HA and telemetry: `docker compose config --hash <svc>` at
     HEAD must equal the running container's `com.docker.compose.config-hash`
     label. HA: M12 H6 (satellites idle, nothing playing, the selects
     unchanged for 15 minutes). The house-busy checks again.
   - Edit the pin, commit `upgrade: <name> <from> -> <to>` as
     `Kronk upgrade_minor <upgrade_minor@kronk.invalid>`.
   - Bring up. Kronk stack: `git tag upgrade-<YYYYMMDD>-<name>`,
     `scripts/deploy_tag.sh deploy <tag>`. Telemetry: `docker compose -p
     kronk-telemetry … pull <svc>`, `up -d --no-deps <svc>`. HA: `docker tag
     <running id> kronk/ha-rollback:<from>`, `docker pull <image>:<to>`,
     `docker stop -t 120 homeassistant`, `docker compose -p kronk-ha -f
     docker-compose.ha.yml up -d homeassistant`.
   - Verify: the container runs `<image>:<to>`; every health check of the
     component (retried for up to 5 minutes); for HA, M12 H8 (StopTimeout
     120, no signal 9 since the stop, satellites not unavailable, selects and
     players as before, `rest_command` services as before, MA not
     restarted, `voice_replay.py --check-only`, voice_tracer reconnected,
     retried up to 10 minutes); then the offline corpus and the replay
     compared with the baseline: a row that passed or xpassed in the
     baseline and fails now, and is not a flapper, fails the upgrade. A
     replay that cannot complete (exit 2 or 3) fails it too: unverified is
     rolled back.
   - Keep, or roll back: the pin restored by a commit (`upgrade: roll back
     <name> <to> -> <from>`), then the previous tag deployed, the previous
     image by id (re-tagged if the old tag moved) with the same restart, or
     `up -d` of the local previous image; then image and health (and H8 for
     HA) verified again. A failed rollback, or any error inside a
     component, records `rollback_failed`, raises the warning and stops the
     run: later runs decline until `ack`. A failure that is not in the
     rollback does not stop the other components.
7. One notice for the run (criterion 8), sent only when something was
   changed or tried: per component `upgraded`, `rolled back to <from>
   (<why>)`, `ROLLBACK FAILED, needs you`, `not started (<why>)`; skipped
   components; the log's path. Status file updated. Exit 0 (nothing,
   declined, kept), 1 (something rolled back), 2 (a rollback failed).

It pushes nothing; tags stay local. It never deletes an image or a release
directory.

## Criteria

1. Inventory: the table above (`docs/upgrades/inventory-2026-11-18.md`),
   mismatches listed, hosts listed, MA fork status recorded.
2. Rules as data with a comment per component; unparseable versions
   skipped and reported; never crosses into major; never touches MA,
   llama.cpp or a model file; never changes a pin that is not exact:
   `tests/test_m18_rules.py` (component-by-component classify, unparseable
   pins), `test_m18_lookups.py` (planning: `test_plan_takes_the_newest_patch_old_enough_and_never_a_major`,
   `test_plan_skips_a_floating_pin_without_a_lookup`, `test_plan_never_looks_up_excluded_components`).
3. One at a time, one commit each, order stated, a failure does not stop
   the others unless it is in the rollback: `test_components_go_one_at_a_time_in_order_each_its_own_commit`,
   `test_a_failed_health_check_rolls_back_and_the_others_continue`,
   `test_a_failed_rollback_stops_the_run_and_blocks_later_runs_until_ack`;
   rehearsal B and C. Lockfiles: report only (departure 1).
4. Verify: health, offline corpus, replay against a baseline with the
   flappers as data, deploy checks, M12's H8 for HA:
   `test_a_replay_regression_rolls_back_…`, `test_a_flapping_row_does_not_fail_an_upgrade`,
   `test_the_offline_corpus_failing_rolls_back`, `test_ha_patch_upgrade_follows_the_restart_runbook`,
   `test_ha_diff_names_what_did_not_come_back`, `test_the_flapper_list_is_data`.
5. Rollback restores pin, image and state and is verified; HA by id, kept as
   `kronk/ha-rollback:<from>`; nothing deleted: `test_a_replay_regression_rolls_back_a_kronk_stack_component_by_deploying_the_previous_tag`,
   `test_ha_failure_rolls_back_to_the_previous_image_by_id`, `test_a_failed_deploy_rolls_back`; rehearsal B (real).
6. Declines: `test_it_declines_when_the_house_is_busy_or_the_tree_is_not_ours`
   (11 cases), `test_it_declines_while_another_run_holds_the_lock`,
   `test_declines_retry_a_bounded_number_of_times_then_say_so`,
   `test_undeployed_work_on_the_branch_declines_the_kronk_stack_components_only`,
   `test_ha_is_not_restarted_when_a_select_changed_recently`; the real
   dry run (declines on the branch).
7. One log per run (`data/upgrades/runs/`, argued below), commits name
   component, from and to, nothing pushed; `--dry-run` changes nothing:
   `test_dry_run_changes_nothing_and_says_what_it_would_do`,
   `test_it_pushes_nothing_…`; the real dry run left no `data/upgrades` and a clean tree.
8. Notice after a run that changed or tried something, none otherwise:
   `test_nothing_to_do_sends_nothing`, `test_notice_text_names_from_to_and_what_was_rolled_back_and_why`,
   `test_a_compose_file_that_does_not_describe_what_runs_declines_that_component` (no notice when nothing was tried).
9. Quarterly check: `tests/test_m18_major.py` (report content from stubbed
   notes, report on disk before the notice, commit only on a clean expected
   branch, upgrades nothing, what could not be fetched is said).
10. First report: `docs/upgrades/2026-11-18.md`, from real release notes,
    with M8 and M15 gains and the named risks (headline below).
11. Runbook: `docs/runbooks/major-upgrade.md`.
12. Notices through `notify.sh`, stubbed in every test; HA failing: status
    file, `/services` warning until `ack`, hourly retry:
    `test_when_ha_is_what_failed_the_result_is_still_on_services`,
    `test_a_notice_ha_does_not_take_is_kept_and_warned_then_retried`,
    `test_the_notices_command_retries_under_the_lock`, `test_ack_clears_…`,
    the API tests. Live: `/dev/api/upgrades` answers on the rebuilt dev pair.
13. Units in `systemd/`, `Persistent=true` for the monthly and quarterly
    timers, 03:30 and 04:30: `tests/test_m18_units.py`; `systemd-analyze
    calendar` gives 2026-12-01 03:30 and 2027-01-08 04:30 as the next
    elapses; not installed (`systemctl --user list-timers` shows none).
14. Tests: 149 new, all with `no_network`, temporary directories and
    temporary git repositories with their own user; suite output below.
15. Rehearsal: below.
16. Nothing changed in production: every container's image id and start
    time compared before the dev rebuild and at the end; only
    `kronk-orchestrator-dev-1` differs. No image was pulled into a tag a
    running stack uses; HA, MA, the pucks, the Whisper and llama units
    untouched; no sound (the replay is dry-run gated; players unchanged).
17. Docs: `docs/features/upgrade-cadence.md`, CLAUDE.md (tenet 3, the HA
    restart rule), ROADMAP.md (Now), the plan's open item.

## `scripts/upgrade_minor.sh --dry-run` against the real system

Run at the end of the build, tree clean, on `refactor`:

```
05:15:14Z upgrade_minor DRY RUN 2026-11-18T05:15:14+00:00 repo /home/drew/git-repos/drawsmcgraw/kronk branch rule 'main'
05:15:14Z lock /home/drew/git-repos/drawsmcgraw/kronk/data/upgrades/lock: free (dry run takes no lock)
05:15:28Z plan:
05:15:28Z   tempo                      3.1.1                  CURRENT   no newer release inside the minor line
05:15:28Z   grafana                    13.2.4                 UPGRADE   13.2.4 -> 13.2.5 (released 2026-10-27, 21 days)
05:15:28Z                                                               . 13.2.6: 7 days old, under 14
05:15:28Z   wyoming-piper              2.2.2                  CURRENT   no newer release inside the minor line
05:15:28Z   searxng                    2026.11.9-57ff753ad    CURRENT   newer minor releases exist, none taken yet
05:15:28Z                                                               . 2026.11.17-b99cf7496: 0 days old, under 14
05:15:28Z                                                               . 2026.11.17-c9c5d1b9b: 0 days old, under 14
05:15:28Z                                                               . 2026.11.17-d3a35cf45: 0 days old, under 14
05:15:28Z                                                               . 2026.11.17-cf3c20177: 1 days old, under 14
05:15:28Z                                                               . 2026.11.15-2d500cb6b: 2 days old, under 14
05:15:28Z                                                               . 2026.11.14-a7128d5d1: 3 days old, under 14
05:15:28Z   nginx                      1.29-alpine            SKIP      pin '1.29-alpine' (docker-compose.yml:12) is not an exact version under the rule; never changed
05:15:28Z   litellm                    v1.88.1                UPGRADE   v1.88.1 -> v1.88.6 (released 2026-08-11, 98 days)
05:15:28Z   python-base-orchestrator   3.12-slim              SKIP      pin '3.12-slim' (orchestrator/Dockerfile:1) is not an exact version under the rule; never changed
05:15:28Z   python-base-tool_service   3.12-slim              SKIP      pin '3.12-slim' (tool_service/Dockerfile:1) is not an exact version under the rule; never changed
05:15:28Z   python-base-voice_tracer   3.12-slim              SKIP      pin '3.12-slim' (voice_tracer/Dockerfile:1) is not an exact version under the rule; never changed
05:15:28Z   uv                         uv                     SKIP      pin 'uv' (orchestrator/Dockerfile:13) is not an exact version under the rule; never changed
05:15:28Z   git-orchestrator           -                      NONE      a Debian package series pin; no registry to read
05:15:28Z   homeassistant              2026.5.4               CURRENT   no newer release inside the minor line
05:15:28Z   lock-orchestrator          41 packages            REPORT    12 patch release(s) available; report only
05:15:28Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               > charset-normalizer 3.4.7 -> 3.4.9
05:15:28Z                                                               > click 8.3.2 -> 8.3.3
05:15:28Z                                                               > fastapi 0.115.0 -> 0.115.14
05:15:28Z                                                               > googleapis-common-protos 1.75.0 -> 1.75.5
05:15:28Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
05:15:28Z                                                               > python-multipart 0.0.9 -> 0.0.33
05:15:28Z                                                               > pyyaml 6.0.2 -> 6.0.3
05:15:28Z                                                               > typing-inspection 0.4.2 -> 0.4.4
05:15:28Z   lock-tool_service          55 packages            REPORT    13 patch release(s) available; report only
05:15:28Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation-asgi==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation-fastapi==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-instrumentation-httpx==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               > charset-normalizer 3.4.7 -> 3.4.9
05:15:28Z                                                               > click 8.3.2 -> 8.3.3
05:15:28Z                                                               > dateparser 1.4.0 -> 1.4.3
05:15:28Z                                                               > fastapi 0.115.0 -> 0.115.14
05:15:28Z                                                               > lxml 6.1.1 -> 6.1.3
05:15:28Z                                                               > lxml-html-clean 0.4.5 -> 0.4.6
05:15:28Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
05:15:28Z                                                               > soupsieve 2.8.3 -> 2.8.4
05:15:28Z                                                               > typing-inspection 0.4.2 -> 0.4.4
05:15:28Z   lock-voice_tracer          22 packages            REPORT    5 patch release(s) available; report only
05:15:28Z                                                               . opentelemetry-exporter-http-transport==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-exporter-otlp-common==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               . opentelemetry-semantic-conventions==0.66b1: not an exact X.Y[.Z] version; skipped
05:15:28Z                                                               > opentelemetry-api 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-exporter-otlp-proto-common 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-exporter-otlp-proto-http 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-proto 1.45.1 -> 1.45.2
05:15:28Z                                                               > opentelemetry-sdk 1.45.1 -> 1.45.2
05:15:28Z   llama.cpp                  -                      NONE      llama.cpp builds have no stable scheme; any new build waits for the quarterly report and the model benches
05:15:28Z   wyoming-faster-whisper     -                      NONE      not in the minor/major table; a GPU (ROCm) build in a host venv, not a pin in the repo
05:15:28Z   ma-blueprint               -                      NONE      a hand-merged fork of the upstream blueprint
05:15:28Z   music-assistant            -                      EXCLUDED  Music Assistant is excluded (Drew 2026-11-14): local fork with the Pandora patch
05:15:28Z   bgutil-pot-provider        -                      EXCLUDED  part of the Music Assistant stack, excluded with it
05:15:28Z lookups: 73 request(s) to api.github.com, ghcr.io, hub.docker.com, pypi.org
05:15:28Z would do, in order (each its own commit; verify = image, health, corpus, replay, stack checks):
05:15:28Z   grafana: docker-compose.telemetry.yml: 13.2.4 -> 13.2.5; commit 'upgrade: grafana 13.2.4 -> 13.2.5'; compose -p kronk-telemetry pull grafana; up -d --no-deps grafana (rollback: pin back, up -d with the local previous image)
05:15:28Z   litellm: docker-compose.yml: v1.88.1 -> v1.88.6; commit 'upgrade: litellm v1.88.1 -> v1.88.6'; git tag upgrade-<date>-litellm; deploy_tag.sh deploy it (rollback: pin back, deploy sprint-M21-step2)
05:15:28Z then: one notice through scripts/lib/notify.sh with what changed; the status file data/upgrades/status.json for /services
05:15:28Z production runs tag: sprint-M21-step2
05:15:28Z HEAD vs sprint-M21-step2: undeployed changes in orchestrator/main.py, orchestrator/static/services.html, orchestrator/upgrades_api.py (Kronk-stack components would be declined)
05:15:28Z docker-compose.telemetry.yml vs running kronk-telemetry-grafana-1: matches (config hash)
05:15:28Z a real run now would DECLINE: on branch 'refactor', expected 'main' (KRONK_UPGRADE_BRANCH / expected_branch)
05:15:28Z dry run: nothing written, nothing changed, nothing sent
```

It declines on the branch rule only. The house checks (HA's states, the
voice_tracer probe, nginx's log, a running deploy) are in the same decline
list, so they read quiet at that moment; no second dry run was made with
another branch setting. LiteLLM would not have been started either: HEAD carries
this item's orchestrator change, which production has not deployed. After
the run `data/upgrades/` did not exist and `git status` was empty.

## The rehearsal (criterion 15)

`scripts/upgrade_rehearsal.py`, run 2026-11-18 04:58 to 05:10 UTC. Target:
compose project `m18-rehearsal`, one service `box`, `busybox:1.36.0` with
`sleep 3600`, `network_mode: none`, no volumes: no network, volume or name
shared with a real stack (checked before: no `rehears*` container, network
or project existed). Images: `busybox:1.36.0` and `busybox:1.36.1`, pulled
from Docker Hub under their own tags, used by nothing else, removed after.

Real: the Docker Hub lookups (the plan read `1.36.0 -> 1.36.1 (released
2026-03-24, 238 days)`; that date is a re-push date), git in a scratch
repository, the lock, the status file and the log in a scratch state
directory, `docker compose config --hash`, `pull`, `up -d --no-deps`,
`docker inspect`, and the health checks (`State.Running`, and an `exec`
check inside the container). Stubbed: HA's states, the voice_tracer probe
and nginx's log (reported quiet), the offline corpus and the replay
(reported passing), the notifier (printed).

- **A, kept.** Dry run first: plan shown, `docker-compose.scratch.yml vs
  running m18-rehearsal-box-1: matches (config hash)`, "a real run now would
  go ahead", state directory not created. Real run: commit `upgrade:
  busybox-scratch 1.36.0 -> 1.36.1`, pull, up, `verified: busybox-scratch
  1.36.1 kept`, one notice "Kronk upgrade: 1 upgraded"; `busybox:1.36.1`
  running; status `upgraded`, month done.
- **B, forced failure, rolled back.** The `exec` check fails for real on
  1.36.1 (`busybox | head -1 | grep -qv 'v1.36.1 '`). After the 5-minute
  health allowance: `FAILED`, `rolling back`, commit `upgrade: roll back
  busybox-scratch 1.36.1 -> 1.36.0`, `rolled back and verified`;
  `busybox:1.36.0` running; exit 1; status `rolled_back` with the warning.
- **C, the rollback fails.** `pull_policy: never`, and after the upgrade the
  harness removes `busybox:1.36.0`. The rollback's `up` really fails (`No
  such image: busybox:1.36.0`): `ROLLBACK FAILED`, "stopping: a rollback
  failed; the remaining components wait", notice "Kronk upgrade: ROLLBACK
  FAILED (busybox-scratch)", exit 2. The next run by hand (`--ignore-month`)
  declines: "the last rollback failed (…); fix it, then `python3
  scripts/upgrades.py ack`". `ack` clears it ("cleared: 1 warning(s), the
  failed-rollback block").
- Cleanup: `upgrade_rehearsal.py … cleanup` removed the container and both
  tags; afterwards no `rehears*` container or network and no busybox image;
  scratch repositories removed with `${VAR:?}` guards; no symlinks in the
  scratch directories.

The rehearsal found four defects, fixed in step 7 and covered by unit tests:
the notice named `data/upgrades/runs/…` when the state directory was
elsewhere (A, B); a failed `exec` check gave no detail and the title read
"0 upgraded, 1 rolled back" (B); the failed-rollback notice carried raw
multi-line daemon output and did not say git already had the old pin while
the new version still ran (C). B and C ran before the respective fixes. The
real dry run found a fifth: the undeployed-paths check split file names with
spaces (fixed, test added).

## The first major-upgrade report (criterion 10)

`docs/upgrades/2026-11-18.md`. Headline: Home Assistant is six monthly
releases behind (2026.5.4 to 2026.11.2). No release note from 2026.6.0 to
2026.11.2 mentions the Ollama integration. The risks are hassil 3.6 to
3.12.1 (sentence matching for the blueprint and `prefer_local_intents`), the
2026.11 entity-name change (`friendly_name`, which tool_service reads for
players), HA's bundled Music Assistant client against the pinned MA beta,
the 2026.9 LLM tool-name prefix, and the restart (a one-way recorder
migration means rollback needs the volume backup). M8 gains only a TTS
stream-leak fix; M15 gains conveniences (bulk exposure, the MCP card, an
OpenAI-compatible conversation agent option), no prerequisite. Proposed: HA
first, after audio capture ends on 2026-12-01. Also major: Grafana 13.3.1
(CVE fixes), LiteLLM v1.110.0 (v1.89 to v1.102 not read), Piper 2.5.4 (a
streamed-synthesis fix; any Piper upgrade must be 2.5.1 or later, which
turns off onnxruntime's telemetry upload; the running onnxruntime is 1.23.2,
older than the 1.29 that turned it on), nginx, llama.cpp b12498,
wyoming-faster-whisper 3.9.0, 30 Python packages.

## Install and uninstall (for the coordinator)

Before installing: production must run a tag that contains this item
(`/api/upgrades`); deploy it the usual way (`scripts/deploy_tag.sh plan`,
then `deploy`). Decide `KRONK_UPGRADE_BRANCH`: production's tags are made on
`refactor` during the sprint; the units say `main`.

```
cp systemd/kronk-upgrade-{minor,major-check,notices}.{service,timer} ~/.config/systemd/user/
# only if production's tags are still made on refactor:
sed -i 's/^Environment=KRONK_UPGRADE_BRANCH=main$/Environment=KRONK_UPGRADE_BRANCH=refactor/' \
    ~/.config/systemd/user/kronk-upgrade-minor.service ~/.config/systemd/user/kronk-upgrade-major-check.service
systemctl --user daemon-reload
systemctl --user enable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
systemctl --user list-timers 'kronk-upgrade-*'      # next: 2026-12-01 03:30, 2027-01-08 04:30, the next hour
scripts/upgrade_minor.sh --dry-run                   # reads only
python3 scripts/upgrades.py test-notice              # the one real test notice: "Kronk: TEST upgrade notice"
```

Uninstall:

```
systemctl --user disable --now kronk-upgrade-minor.timer kronk-upgrade-major-check.timer kronk-upgrade-notices.timer
rm ~/.config/systemd/user/kronk-upgrade-{minor,major-check,notices}.{service,timer}
systemctl --user daemon-reload
```

The first real run is the timer's, 2026-12-01 03:30. Today it would take
Grafana 13.2.5 (or 13.2.6, which is then 21 days old) and LiteLLM v1.88.6;
SearXNG if a build is then 14 days old; HA has no 2026.5 patch left.

## Dev pair and replay

`orchestrator/` changed, so only the dev pair was rebuilt:
`docker compose --profile dev up -d --build --no-deps tool_service-dev
orchestrator-dev` (dry run first: only the pair). `kronk-orchestrator-dev-1`
was recreated (`sha256:b0ad9624…` to `sha256:3c0d434e…`);
`kronk-tool_service-dev-1` was not (same image). Both healthy;
orchestrator-dev has `TELEMETRY_RECORD_TEXT=true`, `MODEL_LOCK=server`,
`VOICE_STREAM=on`, `MUSIC_DRY_RUN=1`; tool_service-dev `MUSIC_DRY_RUN=1`,
`TELEMETRY_RECORD_TEXT=true`; `/dev/api/status` says `music_dry_run: true`;
`/dev/api/settings` says editing is off (`SETTINGS_PASSPHRASE_HASH is not
set`). `/dev/services` serves the new card (200); `/dev/api/upgrades`
answers `{"present": false, "note": "no upgrade run has written a status
file yet"}`; production's `/api/upgrades` is 404 until promotion.

`python3 scripts/voice_replay.py --label m18-build`, 04:49 to 04:55 UTC, all
players idle before: `{'pass': 134, 'xfail': 23, 'skip': 3}; executed 72,
checked read-only 85; players unchanged (17 checked)`, exit 0. Against
`voice_replay_2026-11-16_012824_m10-baseline.json`: no row regressed; the
baseline's one failure, `inc-0930-search-deep-house-ytm` (a known flapper),
passed. Result: `docs/bench/voice_replay_2026-11-18_044931_m18-build.json`.

## Tests

`./scripts/run_tests.sh -q -p no:cacheprovider`, at the commit before this
report:

```
=============================== warnings summary ===============================
orchestrator/main.py:814
tests/test_config_fix2.py::test_a_stray_file_whose_name_is_not_utf8_still_answers
  /home/drew/git-repos/drawsmcgraw/kronk/orchestrator/main.py:814: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class _OpenAIChatRequest(BaseModel):

orchestrator/main.py:1044
tests/test_config_fix2.py::test_a_stray_file_whose_name_is_not_utf8_still_answers
  /home/drew/git-repos/drawsmcgraw/kronk/orchestrator/main.py:1044: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class _OllamaMessage(BaseModel):

orchestrator/main.py:1052
tests/test_config_fix2.py::test_a_stray_file_whose_name_is_not_utf8_still_answers
  /home/drew/git-repos/drawsmcgraw/kronk/orchestrator/main.py:1052: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class _OllamaChatRequest(BaseModel):

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
5138 passed, 3 skipped, 53 xfailed, 6 warnings in 66.65s (0:01:06)
```

Baseline was 4989 passed, 3 skipped, 53 xfailed; 149 new tests.
`tests/test_m12_ha_files.py` changed: it asserted HA's pin was the literal
`2026.5.4`, which would fail the offline corpus, and so roll back, every HA
patch upgrade; it now checks the pin's shape.

## Departures from the criteria, and why

1. **Python lockfiles are report only** (criterion 3 says regenerate with
   hashes, patch releases only). It cannot be made safe unattended here:
   the offline corpus runs in `tests/.venv`, which is not built from the
   service locks, so it would not test the new versions; regenerating
   resolves against PyPI and takes whatever was uploaded, with fresh hashes,
   with no one reading the diff, which removes the point of hash pinning;
   voice_tracer has no dev twin to try a new lock on. The monthly log and
   the quarterly report list the patch releases (16 packages have one
   today); applying them is a runbook step in a window.
2. **A 14-day minimum age** for any release, and a release with no readable
   date is not taken. Not in the table; it is the rule the telemetry pins
   were chosen by, and it keeps a just-pulled release out.
3. **SearXNG**: no patch scheme exists; "any newer build at least 14 days
   old" is the monthly move (what the searxng-bump runbook does by hand).
4. **Logs are under `data/upgrades/runs/`, outside git** (criterion 7
   allows it if argued): a log written into the repo would dirty the tree
   and the next run would decline; committing a log every month is noise.
   The pin changes themselves are commits.
5. **The quarterly report goes to `data/upgrades/reports/`** when the tree is
   not clean or not on the expected branch, and the notice says so; it is
   still written before the notice.
6. **The verify compares the replay with a baseline taken at the start of
   the run**, not with an absolute pass: a row failing before the change is
   not the upgrade's fault. The flapper list is still data and still applies.
7. **"Kept locally until the next month's run"**: the script never deletes
   an image at all. Old images and `kronk/ha-rollback:*` tags accumulate
   until removed by hand.
8. **The `/services` card reads production's `/api/upgrades`** (absolute
   URL): there is one status file per house, in production's `data/`. On
   `/dev/services` it shows production's answer (404 until promotion). The
   dev orchestrator serves the route for its own `/data`, which has no file.
9. **Floating pins** (nginx, the Python base images, uv) are skipped, as
   the criterion says; pinning them exactly is a code change proposed in the
   report, not made.
10. **A failed health check takes up to 5 minutes** to count (the startup
    allowance), HA's H8 up to 10: a failing upgrade costs that much time
    at 03:30.

## Needs Drew's decision

1. **The notice path when HA is what failed.** Built default: the notice
   stays in `data/upgrades/status.json`, `/services` shows a warning until
   `python3 scripts/upgrades.py ack`, and `kronk-upgrade-notices.timer`
   retries hourly until HA takes it. Alternatives:
   - a self-hosted ntfy container and its phone app (local, no cloud; the
     phone receives only on the home network or a VPN; one more service);
   - email through an SMTP relay (reaches the phone anywhere; sends data off
     the box: a tenet-1 decision);
   - a banner on the MagicMirror (a change on a managed host: needs Drew's
     per-action permission, and it is a display, not a push);
   - nothing more than the default.
2. **Unattended commits.** The run commits pin changes, and tags
   `upgrade-<date>-<name>`, on the branch production's tags are made from
   (`KRONK_UPGRADE_BRANCH`, default `main`), as author `Kronk upgrade_minor
   <upgrade_minor@kronk.invalid>`, and never pushes: Drew pushes. It
   declines on any other branch or an unclean tree, and does not start a
   Kronk-stack component while HEAD has undeployed changes outside
   `inert_paths` (it would ship them). Confirm the branch, the author, and
   that the commits may exist unpushed until Drew pushes; or choose that the
   run only reports.
3. **SearXNG's rule** (departure 3): keep, or make SearXNG quarterly
   (`minor = "none"`).
4. **Lockfiles report only** (departure 1): accept, or schedule a window.
5. **Pin nginx (`1.30.N-alpine`), the Python base images and uv exactly**,
   so the monthly run can move their patches.
6. **The first major upgrade**: Home Assistant 2026.5.4 to 2026.11.2, after
   2026-12-01, in a window Drew declares; the report and the runbook's HA
   section are ready.
