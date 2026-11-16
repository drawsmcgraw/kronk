# M17 build report: dev path on the Hey Jarvis wake word

Build agent, 2026-11-15, branch `refactor`. Criteria: `docs/sprint/M17-criteria.md`.
Commits: `5e02b3f`, `ec9e4b0`, `88497a8`, `fbc6a3b`, `ea6ce91`, and the commit
that adds this report.

## Summary

- `orchestrator-dev` (:8100) and `tool_service-dev` (:8103) run under compose
  profile `dev`, with their own env file, data directory and OTEL service names.
  They do not depend on any production service.
- nginx `/dev/` strips the prefix and proxies to `orchestrator-dev` through a
  variable. It survives the dev pair being down; `/dev/` then answers 502.
- `MUSIC_DRY_RUN` in `tool_service` suppresses every music state change (play,
  shuffle, transport, rating, taste feedback, pick records) and says so. It is
  on for dev and off for production.
- Home Assistant: an Ollama entry at `http://localhost/dev/voice` with agent
  `conversation.kronk_dev`, and a pipeline `kronk-dev`. All three satellites
  have slot 2 = `kronk-dev` / Hey Jarvis. `configuration.yaml` loads
  `rest_command` and carries the M12 capture setting commented out. HA was
  restarted once.
- `scripts/deploy_tag.sh` (`status` / `plan <tag>` / `deploy <tag>`) is built
  and tested in a scratch clone. It was not run as a deploy.
- Found and fixed in code, not deployed: voice_tracer linked dev voice runs to
  production traces. Found and written up, not fixed: every HA restart through
  compose is a SIGKILL after 10 s
  (`docs/incidents/INCIDENT_2026-11-15_ha_restart_sigkill.md`).

## What was built, by criterion section

### A. Dev services (`docker-compose.yml`)

- `tool_service-dev` and `orchestrator-dev` have `profiles: [dev]` and the
  same `build:` contexts as production (same Dockerfiles). Each sets its port
  with a `command:` override (8103, 8100), because the Dockerfiles hard-code
  the uvicorn port. Healthchecks use the new ports.
- Env file: `.env.dev`, shared by the pair, gitignored. The committed template
  is `.env.dev.example`. It holds only knobs: `MUSIC_DRY_RUN=1`,
  `MUSIC_DEFAULT_PLAYER=media_player.satellite1_d8d1bc` (office), the model
  assignments, `LOCATION`, `HISTORY_MAX_MESSAGES`, `ERROR_STYLE=debug` and
  `TELEMETRY_RECORD_TEXT=false`. Secrets (`HA_TOKEN`, `MA_TOKEN`,
  `SOLAR_SERIAL`) come from `.env` by compose interpolation, as they do for
  production, so they are not duplicated. `.env.dev` holds no secrets today;
  it is gitignored so per-experiment edits do not dirty the tree.
- Wiring: `orchestrator-dev` has `TOOL_SERVICE_URL=http://tool_service-dev:8103`
  and `depends_on: tool_service-dev` only. Health and finance URLs point at the
  production services (read-only queries; both are removed in M1) with no
  `depends_on`, so bringing dev up never starts or recreates a production
  container.
- State: both dev services mount `./data/dev` as `/data`, which gives them
  their own `sessions.db`, `metrics.db`, `ops_audit.log`, weather cache,
  shopping list and generated diagrams. Production's `./data` is mounted
  read-only at `/shared` on `tool_service-dev` only, and env vars point at it:
  `MUSIC_TASTE_FILE`, `MUSIC_TASTE_DB`, `SOLAR_DB`, `NEWS_FILE`.
- Background jobs off on dev: `SOLAR_POLL_ENABLED=0` (new gate),
  `NEWS_ENABLED=0`, `MUSIC_TASTE_ENABLED=0`. Dev reads solar.db, the news brief
  and the taste profile, and writes none of them.
- Shared files dev could write: none. Writes to `/shared` fail loudly:
  - `POST /music/taste` answers 500, "I couldn't save that — the taste file
    isn't writable (Read-only file system)."
  - taste DB writes raise `attempt to write a readonly database`, which the
    existing callers catch and log.
  - `touch /shared/x` fails with "Read-only file system".
- Not given to dev: the MagicMirror SSH key, the `magicmirror/` mount and
  `MM_SSH_*`. `remote_exec` on dev answers "unknown host … (none configured)"
  and `update_magicmirror` cannot reach the Pi. To test M3/M4 live on dev,
  add a host deliberately.
- OTEL: `kronk-orchestrator-dev`, `kronk-tool-service-dev`.
- `# Operations:` block: in the comment above `tool_service-dev`. It covers
  first-time setup, up/rebuild, logs, stop, remove, and the one-test dry-run
  switch.

### B. nginx (`nginx/nginx.conf`)

- `location /dev/`: `set $orchestrator_dev orchestrator-dev;
  rewrite ^/dev/(.*)$ /$1 break; proxy_pass http://$orchestrator_dev:8100;`
  plus the same streaming settings as `location /` (buffering and cache off,
  HTTP/1.1, `Connection ''`, 300 s timeouts).
- The variable means nginx resolves the name per request (`resolver
  127.0.0.11 valid=10s` was already set). nginx starts without the dev pair,
  and the pair can come and go without an nginx restart.

### C. Music dry-run (`tool_service/main.py`, `orchestrator/tools.py`, `orchestrator/agents.py`)

- `MUSIC_DRY_RUN` (truthy `1/true/yes/on`) is read at import. Off when absent.
- `/music`: everything up to `play_media` runs as usual. That covers the
  player list, search, scoring, the Pandora station lookup and the genre plan
  (all reads). Then it returns `{"status": "dry_run", "dry_run": true,
  "action": "play", "player", "targets", "would_play": <service_data>,
  "detail": "Dry run: would play … on …. Nothing was sent to the speaker."}`
  plus `resolved` / `station` / `pick` when they apply.
- Not sent: `play_media`, either `shuffle_set`. Not recorded: genre pick, pick
  watcher, `_LAST_PICKS`. So "not that" under a dry run answers that nothing
  was picked.
- `/music/control`: the read-based preconditions still answer for real
  ("Nothing is playing on …", "already paused"). Otherwise it returns
  `dry_run` with "Dry run: would pause on the Office speaker. Nothing was
  sent." No `media_*` or `volume_*` call goes out.
- `/music/rate`: the Pandora and now-playing checks run. Then `dry_run` with
  "Dry run: would rate X by Y thumbs up. Nothing was sent." No MA favorite,
  no skip, no taste feedback, no background like.
- `/music/voice` speaks the dry-run sentence.
- Trace: span `music.dry_run` with `music.dry_run=true`, `music.action`,
  `music.targets`, `music.media_id`. Live example: trace
  `53db301c7a3033c8f866c8d223049d4d`, root `kronk-orchestrator-dev`, mark on
  `kronk-tool-service-dev`.
- Orchestrator: `_tool_play_music` renders a dry-run result as
  `[Music dry run: <detail>]`, and `_terminal_speech` speaks the detail
  verbatim. A dry run is never rendered "Now playing". Control and rate
  already relay `detail`.
- `/health` now returns `{"status": "ok", "music_dry_run": <bool>}`.
  Production answers `false`.
- `SOLAR_POLL_ENABLED` (default `1`) gates the solar poller only.

### D. Home Assistant

See "Changes on the box" below.

### E. Deploy from a tag (`scripts/deploy_tag.sh`)

- `plan <tag>` unpacks `git archive <tag>` into `../kronk-releases/<tag>/` and
  writes `.kronk-release` (tag, commit, time).
- It links runtime state from the live checkout: `data`, `secrets`, `.env`,
  `.env.dev`, `searxng/`, `ops/`. The last two are linked as directories
  because the gitignored file sits inside a bind-mounted directory.
- It refuses a release that defines `homeassistant` or
  `music-assistant-server`, then runs
  `docker compose -p kronk --project-directory <release> up -d --no-build --dry-run`.
- `deploy <tag>` does the same with `up -d --build`, then
  `restart nginx`, then checks `/api/status` and the `/api/chat` shim.
- `status` reads each container's `com.docker.compose.project.working_dir`
  label, so it reports what is actually running: "working tree" or
  "tag <name>".
- No `--profile`, so the dev pair is untouched. The live checkout is never
  switched.
- Exercised:
  - `status` against the box: every container "working tree".
  - `plan nosuchtag`: refused.
  - In a scratch clone with a scratch tag, `plan` exported, linked and listed
    the ten production containers to recreate. It listed no dev, HA or MA
    container and touched nothing.
  - A release with a `homeassistant` service was refused.
- It was not run as a deploy. Promotion is the coordinator's.
- Caveat, also in the script header: after a tag deploy, `nginx.conf`,
  `litellm/config.yaml` and `kronk-context.md` come from the release. Edits in
  the working tree stop reaching production until the next tag.

### F. Tests

Added: `tests/test_music_dry_run.py` (14), dry-run cases in
`tests/test_playback_control.py` (7) and `tests/test_music_rate.py` (2), and
dev-pipeline cases in `tests/test_voice_tracer.py` (4). Baseline was 841 + 2
skipped; now 868 + 2 skipped. Skips unchanged.

### Also: voice_tracer (committed, NOT deployed)

- The bug: voice_tracer joins a voice run to a Kronk trace by device id and a
  ±5 s window, searching only `kronk-orchestrator`.
- Live 00:26 UTC: kronk-dev run `01M7GVZ61DKCSRG8GZT4PE7X90` (office) was
  linked to production trace `aa7ae31ad2ada7eac1611c8e45a52d16`, a kronk run
  from the same device 2 s later. Dev voice runs also shared
  `service.name=kronk-voice-tracer` with production.
- The fix (`fbc6a3b`): runs on a pipeline in `VOICE_TRACER_DEV_PIPELINES`
  (default `kronk-dev`) search `kronk-orchestrator-dev`, and are emitted by a
  second tracer provider as `kronk-voice-tracer-dev`. Production runs are
  unchanged.
- Not deployed: voice_tracer is a production container, and production
  promotion is the coordinator's step. Until it is promoted, dev voice runs
  appear under `kronk-voice-tracer` with `ha.pipeline_name=kronk-dev` and may
  mislink. The filter below handles both states.

## Files changed

| File | Change |
| --- | --- |
| `tool_service/main.py` | `MUSIC_DRY_RUN`, `_dry_run_result`, `_dry_run_play`; dry-run branches in `/music`, `_play_genre`, `/music/control`, `/music/rate`, `voice_sentence`; `/health` flag; `SOLAR_POLL_ENABLED` gate; startup warning |
| `orchestrator/tools.py` | `_tool_play_music` renders `[Music dry run: …]` |
| `orchestrator/agents.py` | `_terminal_speech` maps `Music dry run:` |
| `voice_tracer/main.py` | dev pipeline join target and service name |
| `docker-compose.yml` | `tool_service-dev`, `orchestrator-dev`, Operations block |
| `nginx/nginx.conf` | `location /dev/` |
| `.env.dev.example` | new; template for `.env.dev` |
| `.gitignore` | `.env.dev` |
| `scripts/deploy_tag.sh` | new |
| `README.md` | Deploy/restart: tag deploys and the dev pair |
| `ha/configuration.yaml` | new; source for HA's live file |
| `docs/incidents/INCIDENT_2026-11-15_ha_restart_sigkill.md` | new |
| `tests/test_music_dry_run.py` | new |
| `tests/test_playback_control.py`, `tests/test_music_rate.py`, `tests/test_voice_tracer.py` | added cases |
| `docs/sprint/M17-build.md` | this report |

## Changes on the box outside the repo, and how to roll each back

Backups taken before the HA changes, in `~/backups/ha/m17-2026-11-15/`:
`core.config_entries`, `assist_pipeline.pipelines`, `core.entity_registry`,
`core.device_registry`, `configuration.yaml` (all copied from
`/config/.storage/` and `/config/` before any change). The sprint-wide copy
`~/backups/ha/ha-config-pre-refactor-2026-11-15.tar.gz` is untouched.

1. **`.env.dev`** in the repo root, copied from the example. `data/dev/`
   created. Rollback: `docker compose --profile dev rm -sf orchestrator-dev
   tool_service-dev; rm .env.dev; rm -r data/dev`.
2. **Dev containers** `kronk-orchestrator-dev-1`, `kronk-tool_service-dev-1`
   and images `kronk-orchestrator-dev`, `kronk-tool_service-dev`. Rollback:
   same `rm -sf`, then `docker image rm` the two images.
3. **nginx** restarted twice (`docker compose restart nginx`) to load
   `/dev/`, once of them with the dev pair stopped. Rollback: revert
   `nginx/nginx.conf`, restart nginx.
4. **HA Ollama config entry** `01M7GVWT0Z1C556N5QZRTA1VP1`.
   - Title `http://localhost/dev/voice`, `url=http://localhost/dev/voice`,
     `api_key=kronk`.
   - Created through the config flow API.
   - Conversation subentry `01M7GVX8K43XPNVRW39PAW7NZD`, title "Kronk dev",
     created through the subentry flow API. Its fields are copied from the
     production subentry: `model kronk:latest`, the same prompt including the
     `[kronk-origin]` line, `llm_hass_api []`, `num_ctx 8192`,
     `max_history 20`, `keep_alive -1`, `think false`.
   - Entity: `conversation.kronk_dev`.
   - The production entry `01KSC27VCMSADHG9NH149BP0CR` was not touched.
   - Rollback: delete the entry (Settings → Devices & services → Ollama →
     `http://localhost/dev/voice` → Delete, or
     `DELETE /api/config/config_entries/entry/01M7GVWT0Z1C556N5QZRTA1VP1`).
     Do that after step 6's rollback.
5. **HA pipeline `kronk-dev`**, id `01m7gvyhhfk2jp7havxm88ecme`.
   - Created with websocket `assist_pipeline/pipeline/create`.
   - Fields are `kronk`'s except name and engine: `conversation_engine
     conversation.kronk_dev`, `conversation_language *`, `language en`,
     `stt.faster_whisper/en`, `tts.piper/en_US/en_US-hfc_male-medium`,
     `prefer_local_intents true`.
   - `kronk` (`01ksc2pw5bkhtk37dh2d6zwcxp`) compared equal to its pre-change
     copy, and is still `preferred_pipeline`.
   - Rollback: websocket `assist_pipeline/pipeline/delete` with
     `pipeline_id 01m7gvyhhfk2jp7havxm88ecme`, after step 6's rollback.
6. **Satellite selects**, set with `select.select_option` over REST (HA
   entities only, no firmware or ESPHome change):

   | Entity | Before | After |
   | --- | --- | --- |
   | `select.home_assistant_voice_0ac919_assistant_2` (kitchen) | `kronk` | `kronk-dev` |
   | `select.home_assistant_voice_0ac919_wake_word_2` | `no_wake_word` | `Hey Jarvis` |
   | `select.satellite1_d8d1bc_assistant_2` (office) | `preferred` | `kronk-dev` |
   | `select.satellite1_d8d1bc_wake_word_2` | `no_wake_word` | `Hey Jarvis` |
   | `select.home_assistant_voice_0ab117_assistant_2` (basement) | `preferred` | `kronk-dev` |
   | `select.home_assistant_voice_0ab117_wake_word_2` | `no_wake_word` | `Hey Jarvis` |

   - Slot 1 unchanged on all three: `_assistant kronk`, `_wake_word Okay Nabu`.
   - Firmware unchanged: Voice PEs `26.6.0`, Satellite1 `v0.2.1-beta.0`
     (`update.*` `installed_version`, read before and after the restart).
   - The restart reverted these six; they were set again at 00:30 UTC (see
     Deviations, item 1). Restore-state check: see "Restore-state check" below.
   - Rollback: set each back to its "Before" value with the same service.
7. **HA files.**
   - `/config/rest_command.yaml`: new, copied from `ha/rest_command.yaml`,
     chowned root.
   - `/config/configuration.yaml`: replaced with `ha/configuration.yaml`;
     original at `~/backups/ha/m17-2026-11-15/configuration.yaml`.
     `check_config` returned `valid` before the restart. Diff (backup → live):

   ```diff
   +# Home Assistant configuration.yaml for Kronk.
   +#
   +# Source of truth for the live file /config/configuration.yaml in the
   +# kronk_ha-config volume (same pattern as ha/rest_command.yaml).
   +# Install (takes an HA restart; check first):
   +#   docker cp homeassistant:/config/configuration.yaml ~/backups/ha/configuration.yaml.$(date +%F)
   +#   docker cp ha/configuration.yaml homeassistant:/config/configuration.yaml
   +#   curl -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/config/core/check_config
   +#   docker compose -f docker-compose.ha.yml restart homeassistant

    # Loads default set of integrations. Do not remove.
    default_config:
   @@
    automation: !include automations.yaml
    script: !include scripts.yaml
    scene: !include scenes.yaml
   +
   +# Kronk's REST handoffs (sprint M17, plan change C6): ha/rest_command.yaml in
   +# the kronk repo, installed as /config/rest_command.yaml. Later edits to that
   +# file reload without a restart (rest_command.reload).
   +rest_command: !include rest_command.yaml
   +
   +# Voice audio capture, switched OFF. Sprint item M12 turns it on by
   +# uncommenting the two lines below and restarting HA; it may pick another
   +# directory. While these stay commented, HA writes no pipeline audio.
   +# assist_pipeline:
   +#   debug_recording_dir: /config/voice_recordings
   ```

   - Rollback: `docker cp ~/backups/ha/m17-2026-11-15/configuration.yaml
     homeassistant:/config/configuration.yaml`, `docker exec homeassistant rm
     /config/rest_command.yaml`, restart HA (use `docker stop -t 120
     homeassistant && docker start homeassistant`, see the incident).
   - Loading `rest_command` changes no production behaviour today. Neither the
     installed blueprint nor any automation calls `rest_command.kronk_music`
     yet.
8. **One HA restart**, 00:28:11 UTC,
   `docker compose -f docker-compose.ha.yml restart homeassistant`.
   - Before it: no `media_player` playing, all satellites idle.
   - After it: `rest_command` services `kronk_music`, `reload`.
   - All three `assist_satellite.*` idle (available).
   - `automation.kronk_music_assistant_voice_device_first` on.
   - Config entry `music_assistant` loaded; both Ollama entries loaded.
   - `/config/voice_recordings` and `/share` absent; zero `.wav`/`.raw` files
     newer than `configuration.yaml` anywhere in the container.
9. **Office speaker test** (criterion 15).
   - Dry run off on dev for one request. "play some jazz in the office" played
     Mushroom Jazz Radio (Pandora) on `media_player.satellite1_d8d1bc`; the
     other 16 `media_player` states were unchanged.
   - Then "stop the music in the office" through dev paused it ("Paused on the
     Office speaker.").
   - The office MA queue now holds that station with shuffle on, instead of
     "Eat The Rich". Dry run is back on: `.env.dev` `MUSIC_DRY_RUN=1`,
     `/health` `music_dry_run: true`.
   - The genre pick could not be recorded: dev's taste DB is read-only, which
     the existing code logs and continues.

## Verification run by the build agent (the tester re-checks independently)

| # | Evidence |
| --- | --- |
| 1 | `docker compose up -d --dry-run`: 0 lines mention a `-dev` container. Start times and image ids of all production containers, from the snapshot before the build to the end: unchanged except `homeassistant` (the planned restart, 00:28:21) and `kronk-nginx-1` (restarted by the build for `/dev/`, last 00:34:27). Both were restarted, not recreated: same image ids. Bringing the dev pair up, down and up again changed nothing else. **Pre-existing, not M17:** plain `up -d` would recreate `wyoming-piper`, `kronk-litellm-1`, `kronk-retire_calc-1`, `kronk-nginx-1`. The same dry-run with the M17 compose change stashed shows the same four. Their image ids and litellm's config hash match. All four were created by compose 5.1.4 (now 5.4.0). The tester should not run a real plain `up -d` expecting no recreates. |
| 2 | Same `build:` contexts; ports 8100/8103; `env_file: [.env.dev]`; `.env.dev.example` committed, `.env.dev` gitignored. |
| 3 | "What is the weather forecast for tomorrow?" via `/dev/api/chat`: `tool_service-dev` logged `GET /weather?location=Laurel%2C%20MD 200`; production `tool_service` logged nothing in the window. |
| 4 | `/dev/message` (session `m17-dev-check`) wrote two rows to `data/dev/sessions.db`. `md5sum data/sessions.db data/ops_audit.log` was identical before and after. Dev audit log path is `/data/ops_audit.log` inside dev = `data/dev/ops_audit.log` (not yet created: dev has no ops hosts). |
| 5 | `/shared` read-only; the taste write fails with the 500 quoted above. Dev writes no shared file. |
| 6 | Tempo root services `kronk-orchestrator-dev` / `kronk-tool-service-dev` for dev traffic. Filter below. |
| 8 | `/dev/api/chat` "What is the capital of France?" → "The capital of France is Paris." (1.7 s). Streamed `/dev/api/chat` chunks arrive token by token about 20 ms apart, same as production `/api/chat`. |
| 9, 10 | `/` 200, `/api/status` 200, `/message` 422 (invalid body, so no history write), `/api/chat` 200, `/voice/api/chat` 200, `/v1/chat/completions` 200. Same with the dev pair stopped and nginx restarted; `/dev/*` then 502. After the pair came back, `/dev/*` answered 200 without an nginx restart. |
| 14 | "play some jazz in the office" via `/dev/api/chat` → "Dry run: would play Parov Stelar, for jazz, on the Office speaker. Nothing was sent to the speaker."; all 17 `media_player` states identical before and after. |
| 15 | See item 9 above. |
| 17 | Pipeline fields as in item 5; `kronk` equal to its pre-change copy and still preferred. |
| 18, 19 | `assist_pipeline/run` intent-only as device `ac6dd1f1c8cf1d229cda0f42224a2013` (Satellite1). `kronk-dev` → `orchestrator-dev` logged `origin device=ac6dd1f1… area=Office`, trace `f2a65fc770d7a5e0f40f3ac45be82847`. `kronk` → production, same origin, trace `aa7ae31ad2ada7eac1611c8e45a52d16`. Answer on both: "The capital of Peru is Lima." |
| 25 | After the restart, on `kronk`: "what time is it" → "7:30 PM" (local intent). "what is playing in the office" → "Nothing is playing on the Office speaker." via `now_playing`, production trace `88c91823ff74e4eaa7a35977482a66d0`. Same on `kronk-dev`, dev trace `070e9cabdead7efbf0b17f24a8cc0e3e`. A read-only music request, so nothing playing was disturbed. |

One production request was not a check: a streaming comparison of
`/api/chat` ("Write two sentences about the moon."). The Ollama shims do not
write chat history (dev's `/api/chat` calls left `data/dev/sessions.db`
empty), so production history was not touched.

## Test output

`./scripts/run_tests.sh`, last lines, verbatim:

```
    synced_at = datetime.utcnow().isoformat()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
================= 868 passed, 2 skipped, 14 warnings in 13.00s =================
```

The 14 warnings are pre-existing deprecations (Pydantic class config, `datetime.utcnow`).

## TraceQL: excluding dev traffic

Once the voice_tracer change is promoted, one filter:

```
{ trace:rootService !~ ".*-dev" }
```

Until then, and also correct after it:

```
{ trace:rootService !~ ".*-dev" } && { nestedSetParent = -1 && (span.ha.pipeline_name = nil || span.ha.pipeline_name != "kronk-dev") }
```

Checked against the last hour in Tempo 3.1.1:

| Filter | Traces | By root service |
| --- | --- | --- |
| `{ }` | 30 | orchestrator-dev 15, voice-tracer 8, orchestrator 4, tool-service-dev 3 |
| `{ trace:rootService !~ ".*-dev" }` | 12 | voice-tracer 8, orchestrator 4 |
| combined filter | 8 | orchestrator 4, voice-tracer 4 (the 4 kronk-dev voice runs excluded) |

For M12 accuracy queries over voice runs alone:
`{ name = "voice.run" && span.ha.pipeline_name != "kronk-dev" }`.

The Grafana dashboards (`telemetry/grafana/dashboards/*.json`) filter by
span name, not service, so today they count dev traffic. Not changed here.
Adding the filter to `telemetry/grafana/build_dashboards.py` is a follow-up.

## Deviations from the plan, and findings

1. **HA restart is a SIGKILL** (incident doc).
   - `docker-compose.ha.yml` has no `stop_grace_period`. Docker killed HA 10 s
     after SIGTERM (`docker events`: `kill 15` 00:28:11, `kill 9` 00:28:21).
     HA never wrote `core.restore_state`, so the six slot-2 selects reverted
     to their old values. The recorder logged an unclean shutdown.
   - The selects were set again at 00:30 UTC and left for HA's 15-minute
     restore-state dump.
   - Proposed fix, not applied (needs an HA recreate): `stop_grace_period: 2m`.
     Until then, restart HA with `docker stop -t 120 homeassistant && docker
     start homeassistant`. This affects M12's restart too.
2. **voice_tracer** fixed in code, not deployed (above).
3. **`prefer_local_intents` is `true` on `kronk-dev`**, matching `kronk` as
   briefed. Consequence: on Hey Jarvis, anything HA's local tier handles is
   handled before Kronk dev is asked. That includes the music blueprint
   automation `kronk_mass_voice_device_first` (it plays through MA directly)
   and timers. MUSIC_DRY_RUN does not cover the blueprint, so "Hey Jarvis,
   play X" that the blueprint matches plays for real on that puck. The same
   holds for an M10 replay through `kronk-dev`. To send everything to Kronk
   dev, set `prefer_local_intents false` on `kronk-dev` with websocket
   `assist_pipeline/pipeline/update`. Decision for Drew or the coordinator.
4. One env file for the pair (`.env.dev`), not one per service. Both read it;
   each ignores the other's keys.
5. `configuration.yaml`'s M12 line uses `/config/voice_recordings`.
   `/share` (HA's documented example) does not exist in this container. M12
   picks the real path.
6. Dev gaps, by design or not worth closing before M1/M1b:
   - no hot tub status (the path is hard-coded under `/data`);
   - its own empty shopping list;
   - no measured GPU memory on dev's `/resources` page;
   - the chat UI under `/dev/` loads production's absolute asset and API
     paths. The shims (`/dev/api/chat`, `/dev/voice/...`, `/dev/v1/...`) and
     `/dev/message` are the dev entry points.
   - Dev and production share the GPU models (no new model server), so a
     heavy bench on dev slows household requests, as the plan says.
7. Not mine, seen in HA's log: websocket auth failures from host Python
   (`Python/3.13 websockets/16.0`) at 17:03, 19:16, 19:17, 19:32 and 19:33
   local. The build agent only connected from inside the HA container
   (aiohttp). Probably another session's probe; worth a look.

## Left for Drew (criteria 26, 27)

- **26:** say "Hey Jarvis, what time is it" and "Hey Jarvis, what's the capital
  of Peru" on each puck (kitchen, office, basement). The second should appear
  in `docker logs kronk-orchestrator-dev-1` (an `origin` event with that
  puck's device id). Then "Okay Nabu, what's the capital of Peru" should land
  in `kronk-orchestrator-1`. Avoid music on Hey Jarvis until item 3 above is
  decided.
- **27, the button:** the code says production. HA 2026.5.4's ESPHome
  satellite (`components/esphome/assist_satellite.py`,
  `handle_pipeline_start`) picks the pipeline by matching the wake-word
  phrase the device reports against `_wake_word`, then `_wake_word_2`, first
  match wins, and falls back to slot 1. So a button press that reports no
  wake word runs slot 1 (`kronk`). What the firmware reports on a press is
  not visible from HA without a press. `docs/features/voice-pipeline.md`
  records that in May "slot 2 (tap-to-talk) silently defaulted to HA's local
  Assist", which suggests older firmware sent presses to slot 2.
  - To check, press each puck's button, ask "what's the capital of Peru", and
    see which orchestrator logs the `origin` event. Or read HA's debug runs
    (Settings → Voice assistants → kronk / kronk-dev → Debug): a button run
    starts at the STT stage.
  - If a puck's button lands on `kronk-dev`, the household's presses go to
    dev. HA cannot split the button from the second wake word: slot 2 is one
    pipeline. The choices are to accept that, or to set that puck's
    `_assistant_2` back to `kronk`, which also sends its Hey Jarvis to
    production. Drew's call.

## Restore-state check

HA's periodic dump wrote `/config/.storage/core.restore_state` at 00:43:24
UTC. Read at 00:43:32, all six slot-2 selects hold `kronk-dev` / `Hey Jarvis`,
so they survive the next restart. Live states at the same time:
- slot 1 `kronk` / `Okay Nabu` on all three;
- slot 2 `kronk-dev` / `Hey Jarvis` on all three;
- satellites idle;
- firmware `26.6.0` / `26.6.0` / `v0.2.1-beta.0`;
- `tool_service-dev` `/health` `music_dry_run: true`.
