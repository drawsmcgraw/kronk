# Incident 2026-11-15: HA restart is a SIGKILL; select changes made just before it were lost

**Status:** worked around and verified during sprint item M17; root-cause fix proposed, not applied.
**Found by:** the M17 build agent (`docs/sprint/M17-build.md`).

## Symptom

During M17, the slot-2 selects on all three satellites were set through HA's
REST API (`select.<sat>_assistant_2` → `kronk-dev`, `select.<sat>_wake_word_2`
→ `Hey Jarvis`) and read back correctly. About three minutes later Home
Assistant was restarted once, as planned, to load `rest_command` from the new
`configuration.yaml`. After the restart all six selects were back to their old
values (`preferred` / `kronk` and `no_wake_word`). Nothing else visible had
changed: satellites available, Music Assistant loaded, `rest_command` loaded.

## Timeline (UTC)

- 00:26 — selects set on all three satellites; read back as set.
- 00:28:11 — `docker compose -f docker-compose.ha.yml restart homeassistant`.
- 00:28:11 — docker `kill` signal 15 (SIGTERM).
- 00:28:21 — docker `kill` signal 9 (SIGKILL), 10 s later; `stop`, `die`, `start`.
- 00:28:24 — HA up; `.storage/core.restore_state` rewritten with the old select values.
- 00:30 — selects set again through the API; read back as set.

Evidence: `docker events --since 2026-11-16T00:28:00Z --until 2026-11-16T00:28:30Z --filter container=homeassistant`
shows `kill 15` at 1794788891 and `kill 9` at 1794788901. The previous run's log
(`/config/home-assistant.log.1`) ends at 19:17 local with no shutdown lines. The
new run's log opens with the recorder's own verdict:
`The system could not validate that the sqlite3 database at //config/home-assistant_v2.db was shutdown cleanly`
and `Ended unfinished session (id=18 from 2026-11-04 13:45:25.024419)` — the
recorder session opened at the 2026-11-04 start, never closed because this
shutdown was killed.

## Root cause

`docker-compose.ha.yml` sets no `stop_grace_period`, so Docker's default 10 s
applies (`docker inspect homeassistant` → `StopTimeout=<nil>`). HA's shutdown did
not finish in 10 s and was killed before its final write. The assistant and
wake-word selects are restore entities: their state lives in
`.storage/core.restore_state`, which HA writes every 15 minutes and at a clean
shutdown. A change made less than 15 minutes before a killed shutdown is lost.

This predates M17. Every `docker compose ... restart homeassistant` (the
README's documented restart) has been a SIGKILL after 10 s. The same window
applies to any other restore-state entity and to the recorder's last writes.

## Hypotheses tried

1. The select service did not reach the device: ruled out, the states read
   back as set before the restart.
2. The device overrode HA on reconnect: ruled out, the restored values match
   what `core.restore_state` held before the change, and HA pushes its own
   wake-word selection to the device.
3. Killed shutdown, no final restore-state write: confirmed by the docker events.

## Fix

Workaround applied: the selects were set again at 00:30 and left for HA's
periodic restore-state dump. `core.restore_state` was written at 00:43:24 with
all six at their new values (`kronk-dev` / `Hey Jarvis`).

Proposed root-cause fix (not applied; it needs the HA container recreated,
which is its own restart and outside M17): add `stop_grace_period: 2m` to the
`homeassistant` service in `docker-compose.ha.yml`. Until then, restart HA with
`docker stop -t 120 homeassistant && docker start homeassistant`, or wait 15
minutes after changing restore-state entities.

## What would have caught it sooner

Reading back state after every HA restart, not only before it. Here the
read-back after the restart caught it within a minute.
