# Runbook: a major upgrade (one component, one window)

When: the quarterly check (`scripts/upgrade_major_check.sh`) has written a
report to `docs/upgrades/<date>.md` and told Drew; Drew has read it,
greenlit one component and declared a window. Never otherwise: the system
never starts a major upgrade on its own (Drew, 2026-11-14). What is major
per component is `scripts/upgrade_rules.toml`. One component per window.
Music Assistant is excluded until the Pandora work is upstream.

Time: about an hour for a container image, half a day for Home Assistant,
llama.cpp or Whisper (benches). Steps 1 to 3 happen before the window, the
rest inside it.

## 0. Before anything

- Read the component's section of the report and the project's own notes
  for every version in between (the report's keyword hits are leads only).
  Write down what Kronk uses that the notes touch.
- `scripts/upgrade_minor.sh --dry-run`: the house state the monthly run
  would see. Do not run a major upgrade in the same hour as the monthly run
  (they share the lock only when both are the script; this runbook is by
  hand). Check `python3 scripts/upgrades.py status`: no `rollback_failed`.
- Start `docs/incidents/INVESTIGATION_<date>_<component>-upgrade.md` now if
  anything is unusual; evidence evaporates.

## 1. Backup

The backup job (M16) does not exist yet. Until it does, take these by hand
and write the paths into the window's notes. Nothing below is deleted
afterwards; old images, release directories, build directories and venvs
are kept until the next window of the same component.

| Component | What must be backed up | How (by hand, until M16) |
| --- | --- | --- |
| Home Assistant | The whole config volume `kronk_ha-config`, taken while HA is stopped (the recorder may migrate its schema one way across monthly releases; `.storage/` holds integrations, devices, pipelines) | Step H3 below |
| Kronk stack (LiteLLM, nginx, SearXNG, Piper, base images, lockfiles) | Nothing beyond git: the previous tag is the rollback. `data/` is not touched by an image change | `scripts/deploy_tag.sh status` and note the running tag |
| LiteLLM | `litellm/config.yaml` and `litellm/hooks.py` are in git | none |
| Piper | The `piper-models` volume holds re-downloadable voices only | none |
| Grafana | The `grafana-data` volume (admin login, preferences; dashboards are provisioned from git) | `docker stop kronk-telemetry-grafana-1`, then the volume copy as in H3 with the volume `kronk-telemetry_grafana-data` |
| Tempo | `tempo-data` holds 90 days of traces; a major Tempo release may change the block format | Decide whether the traces matter; if they do, copy the volume as for Grafana with Tempo stopped |
| llama.cpp | The current build directory (`~/pai/pai_workspace/llama-cpp/llama-b<N>-vulkan`) and the unit files | Never delete the old directory; the new build goes in a new one |
| wyoming-faster-whisper | `~/services/wyoming-whisper/.venv` (a ROCm build) | `cp -a ~/services/wyoming-whisper/.venv ~/services/wyoming-whisper/.venv-<old version>` |

Volume copies use the component's current image as the helper (it is
already local, so nothing is pulled) and land in `~/backups/<component>/`.

## 2. The new version, off production

There is no second Home Assistant and no second LiteLLM, SearXNG or Piper.
What can be tried off production:

- **Container images (LiteLLM, Piper, SearXNG, nginx, Grafana, Tempo):**
  run the new image as a scratch container with its own name, on a spare
  loopback port, with the same config files mounted read-only, and exercise
  it directly. LiteLLM example:
  `docker run --rm -d --name scratch-litellm -p 127.0.0.1:8012:8012 -v $PWD/litellm/config.yaml:/app/config.yaml:ro -v $PWD/litellm/hooks.py:/app/hooks.py:ro ghcr.io/berriai/litellm:<new> --config /app/config.yaml --port 8012`,
  then one completion per model alias through `localhost:8012`, then
  `docker rm -f scratch-litellm`. Read its log for config warnings.
- **Home Assistant:** never start a second HA on the real config (it would
  reach the pucks, Music Assistant and the phone over host networking). Off
  production, only: pull the new image, and read from it with
  `docker run --rm --network none --entrypoint python3 ghcr.io/home-assistant/home-assistant:<new> -c "import importlib.metadata as m; print(m.version('music-assistant-client'), m.version('hassil'))"`.
  Compare the Music Assistant client with what `music-assistant-server`
  (`2.11.0b5-kronk.9`) speaks, before the window. The real test is the
  corpus after the upgrade, with the rollback ready.
- **llama.cpp:** build into a new directory, start `llama-server` from it on
  a spare port with the same model and flags as the unit, run the model
  bench against that port, stop it. Check GPU memory first
  (`/resources`); dev and production share the GPU.
- **wyoming-faster-whisper:** a new venv in a new directory, a second
  instance on a spare port (as the dev unit does on 10301), the STT bench.

## 3. The corpus, before

- `./scripts/run_tests.sh` passes.
- `python3 scripts/voice_replay.py --label <component>-pre` on `kronk-dev`
  (dry-run gated, silent). Keep the result file: it is the baseline.

## 4. The window

- Drew declared it. The house is quiet: every `assist_satellite` idle, no
  `media_player` playing, voice_tracer `held: 0`, no spoken request in the
  last five minutes (M12 runbook steps H6 and H7a, with M12-fix2's
  corrections). If not, wait or move the window.
- No `docker compose` command is typed in the working tree against the
  production Kronk stack (M12): Kronk-stack changes go commit, tag,
  `scripts/deploy_tag.sh plan <tag>`, `scripts/deploy_tag.sh deploy <tag>`.

## 5. The upgrade

1. Edit the pin; commit `upgrade: <component> <from> -> <to> (major, window <date>)`.
2. Bring it up:
   - Kronk stack: `git tag upgrade-<date>-<component>`, then `deploy_tag.sh plan`, read it, `deploy_tag.sh deploy`.
   - Telemetry: `docker compose -f docker-compose.telemetry.yml --env-file .env.telemetry pull <svc>` then `up -d --no-deps <svc>`.
   - Home Assistant: the HA section below.
   - llama.cpp / Whisper: edit the unit's `ExecStart` to the new directory (installed unit and the `systemd/` copy), `systemctl --user daemon-reload`, `systemctl --user restart <unit>`.

## 6. Verify

- The component's own health check (the `health` entries in
  `scripts/upgrade_rules.toml`), and `docker inspect` shows the new image.
- The stack's own checks: `deploy_tag.sh deploy` checks `/api/status` and
  the `/api/chat` shim; for HA, step H8 below.
- `./scripts/run_tests.sh`; `python3 scripts/voice_replay.py --label <component>-post`,
  compared with the `-pre` file: a row that passed before and fails now is
  a regression, except the flappers listed in `scripts/upgrade_rules.toml`.
- One spoken "Okay Nabu" request (Drew) and one "Hey Jarvis" request.
- For llama.cpp and Whisper: the benches, against the numbers in the report.

## 7. Roll back

Decide within the window. Roll back when a check fails and the cause is not
obvious and small.

- Kronk stack: put the pin back by a commit, `deploy_tag.sh deploy <the previous tag>`.
- Telemetry: pin back, `up -d --no-deps <svc>` (the old image is still local).
- Home Assistant: H-rollback below. After a recorder migration, the image
  alone is not enough: the volume is restored from step H3's copy.
- llama.cpp / Whisper: the unit back to the old directory or venv, restart.
- Then verify as in step 6, and write the incident doc.

## 8. Record

- The report's component line: done on `<date>`, `<from>` to `<to>`, or
  rolled back and why.
- Anything odd in `docs/incidents/`. Voice or music breakage also adds its
  utterance to `tests/voice_corpus.jsonl` (CLAUDE.md).
- Rules: if the component's version scheme changed, update
  `scripts/upgrade_rules.toml` and its tests in the same commit.

## Home Assistant

The first major upgrade is Home Assistant (plan M18 step 5). The report
of 2026-11-18 names the risks: hassil 3.6 to 3.12 (sentence matching for the
blueprint and `prefer_local_intents`), the 2026.11 entity-name change
(`friendly_name`, read by tool_service for players), the bundled Music
Assistant client against the pinned MA beta, the 2026.9 LLM tool names,
and the restart itself.

Pick the window after the M12 audio capture ends (2026-12-01): a restart
cuts voice_tracer's correction windows short. `$HA_TOKEN` from `.env`;
`D=~/backups/ha/major-$(date +%F)`; `NEW` is the target version, `OLD` the
running one.

| Step | Command | Check |
| --- | --- | --- |
| H1 | `mkdir -p $D && docker pull ghcr.io/home-assistant/home-assistant:$NEW` (a new tag; nothing running uses it) | `docker image inspect ghcr.io/home-assistant/home-assistant:$NEW` succeeds |
| H2 | Section 2's read of the new image: Music Assistant client and hassil versions | the MA client still speaks to `2.11.0b5-kronk.9` (MA's release notes or the client's changelog); if not, stop and tell Drew |
| H2b | Each Ollama entry's options in HA's UI (Settings > Devices & services > Ollama > Configure): is an LLM API selected? | if one is, the 2026.9 tool names apply; note it |
| H3 | Snapshot and backup: M12 H6 into `$D/states-before.txt`; `docker inspect homeassistant -f '{{.Image}}' > $D/old-image-id`; `docker tag $(cat $D/old-image-id) kronk/ha-rollback:$OLD`; then `docker stop -t 120 homeassistant` and `docker run --rm --network none --entrypoint tar -v kronk_ha-config:/config:ro -v $D:/b ghcr.io/home-assistant/home-assistant:$OLD czf /b/ha-config.tgz -C /config .` | `tar tzf $D/ha-config.tgz \| grep -c '^./.storage/core.config_entries$'` is `1`; the size is near the config volume's (about 51 MB, mostly the recorder) |
| H4 | Edit `docker-compose.ha.yml` to `$NEW`; commit | `git show --stat HEAD` names only that file |
| H5 | `docker compose -f docker-compose.ha.yml up -d homeassistant` (HA is already stopped from H3; this is the one start) | `docker inspect homeassistant -f '{{.Config.Image}} {{.Config.StopTimeout}}'` shows `$NEW` and `120` |
| H6 | (wait for HA; the first start after six releases can take minutes: migrations) | M12 H8 in full: `/api/` 200; states compared with `$D/states-before.txt` (satellites not `unavailable`, slot-2 selects unchanged, players as before); `voice_replay.py --check-only` passes; `rest_command` services as before; `music-assistant-server` not restarted; voice_tracer connected; HA's log has no integration that failed to set up (`docker logs homeassistant 2>&1 \| grep -iE 'error setting up\|failed to set up'`) |
| H7 | The corpus (step 6), the spoken requests, `/music/players` names compared with before | as step 6 |
| H8 | `scripts/upgrade_minor.sh --dry-run` | HA shows `CURRENT` on the new month's line; from here the monthly run takes its patches |

H-rollback (when H6 or H7 fails and the cause is not small):

| Step | Command | Check |
| --- | --- | --- |
| R1 | `docker stop -t 120 homeassistant` | stopped |
| R2 | Restore the volume from H3 with the old image as the helper: `docker run --rm --network none --entrypoint sh -v kronk_ha-config:/config -v $D:/b:ro ghcr.io/home-assistant/home-assistant:$OLD -c 'find /config -mindepth 1 -delete && tar xzf /b/ha-config.tgz -C /config'` (destructive inside the volume: only with H3's check passed) | `docker run --rm --network none --entrypoint ls -v kronk_ha-config:/config ghcr.io/home-assistant/home-assistant:$OLD -la /config/.storage` lists `core.config_entries` |
| R3 | Pin back by a commit; if `ghcr.io/home-assistant/home-assistant:$OLD` no longer points at `$D/old-image-id`, `docker tag $(cat $D/old-image-id) ghcr.io/home-assistant/home-assistant:$OLD`; `docker compose -f docker-compose.ha.yml up -d homeassistant` | M12 H8 as in H6 |
| R4 | Write the incident doc; tell Drew what failed | |

Never `docker compose down -v` on this stack. No firmware change to any
puck, and no click on the bundled "update all" card for the ESPHome
devices (2026.7.0) during or after the window.
