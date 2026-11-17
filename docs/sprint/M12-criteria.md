# M12 pass criteria: capture what was said

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M12.
Capture what was said", row C6, the M12 line of "Verified on Kronk,
2026-11-15", Drew's decision of 2026-11-11 and his review of 2026-11-15) and
the items promoted since. The tester checks against this file, not against
the build report.

## Drew's decisions this item carries out

Transcripts on. Two weeks of audio capture on all pucks. Restart HA when
needed (the sprint is an outage window, HA included). Transcript text is
kept 90 days, matching Tempo. Grafana stays viewable without a login. A
reminder or a timer-driven check to turn audio capture off after the two
weeks. The tradeoff, accepted by Drew: this records guests as well as the
household, on every puck.

## Who does what

The build agent works in the repo and on the dev path only. It does not
edit `.env`, does not change or restart Home Assistant, and installs no
systemd unit. It prepares everything so that the production steps are a
short, checked runbook. **The coordinator carries out the production steps
at promotion** (the `.env` line, the HA configuration and compose change,
the one HA recreate, the timer), verifies them, and logs them.

## Context

- `TELEMETRY_RECORD_TEXT` is unset in `.env` (off) and `false` in
  `.env.dev`. Tempo's `block_retention` is `2160h` (90 days).
- `ha/configuration.yaml` (installed in M17) carries the capture setting
  commented out, pointing at `/config/voice_recordings`, inside the
  `kronk_ha-config` volume. HA writes
  `<dir>/<device_id>/<pipeline name>/<run id>/` (HA source,
  `assist_pipeline/pipeline.py`).
- `docker-compose.ha.yml` has no `stop_grace_period`; Docker killed HA
  after 10 s at the M17 restart (M17 build report). The fix proposed
  there is `stop_grace_period: 2m`, which needs a recreate.
- Dev requests are tagged in traces (M17) and must stay out of the
  household's data.
- Transcripts already land in two places outside Tempo: the M11 shadow
  log (`data/music_shadow.jsonl`, no rotation) and container logs.

## Criteria

### A. Transcripts

1. With `TELEMETRY_RECORD_TEXT=true`, a voice request's trace carries
   what Whisper heard (on the voice-tracer's pipeline-run span and on
   the orchestrator's pipeline span) and Kronk's reply. The report lists
   every span attribute and log field that carries text, with the
   switch on and off, for the orchestrator, `voice_tracer` and
   `tool_service`. With it off, none of those carries text (a test for
   each service's side).
2. The report states where transcript text is stored once the switch is
   on, and how long each store keeps it: Tempo (90 days, confirmed from
   the running config), container logs (Docker's rotation settings as
   found), the M11 shadow log, the chat session store, anything else
   found. Where a store keeps text longer than 90 days or without limit,
   the report says so and proposes the smallest change that bounds it;
   changes inside the repo are built, changes outside it are listed for
   the coordinator.
3. A way to read the household's transcripts for the accuracy review
   that needs no hand-written TraceQL: a script (or a saved Grafana
   panel in the provisioned dashboards) that lists voice runs over a
   period with time, satellite, pipeline, what was heard, which tier and
   rule handled it, the tool called, the reply, and the wrong-action
   mark of criterion 8; dev runs excluded by default. It reads Tempo on
   loopback and writes nothing outside a path given on the command line.

### B. Audio capture, two weeks, all pucks

4. The repo's `ha/configuration.yaml` has the capture setting switched
   on, and `docker-compose.ha.yml` gains `stop_grace_period: 2m` and
   whatever mount the recordings need. The recordings directory is
   outside the repo and outside git, on the host disk where the
   coordinator and the labelling script can read it without entering
   the HA container, and writable by the HA container's user. The
   report says where, why, and what the path is inside and outside the
   container. Nothing is installed in HA by the build.
5. A runbook for the coordinator, in the build report, each step with
   its check and its undo: back up the live HA configuration; install
   the file; `check_config`; check no one is mid-request and what is
   playing; recreate HA with the new compose file (one restart); confirm
   HA is up, the three satellites reconnect, the `kronk` and `kronk-dev`
   pipelines and `rest_command` are still there, Music Assistant and the
   speakers are as before; confirm a recording appears for the next
   spoken request. And the reverse runbook for the end date.
6. Disk: the report estimates bytes per recorded run from HA's format
   (sample rate, width, whether wake-word audio is kept as well as the
   command) and the household's run rate from Tempo, gives the two-week
   total, and names the free space. If dev-pipeline runs or wake-word
   false triggers are recorded too, it says so and what share they are.
7. The end date. A check that runs on a timer (a systemd user unit,
   reference copies in `systemd/` as for the other timers; a compose
   service is acceptable if the report argues it): from the end date
   on, while capture is still switched on in the live HA configuration,
   it sends Drew a push notification once a day through the existing
   HA notify service saying capture should be turned off and how; it
   sends nothing before the end date and nothing once capture is off.
   It does not restart HA, change HA, or delete recordings. The end
   date is set in one place at install time (start date plus 14 days).
   Tested with an injected clock and a stubbed notifier. The report
   gives the install commands for the coordinator.

### C. The wrong-action signal

8. `voice_tracer` marks a voice run when the same satellite, within 60
   seconds after it, issues a correction: "not that" (and the corpus's
   other correction forms that route to `music_another` or the
   rating-down rule), a stop or pause, or a near-repeat of the same
   request. The mark is a span attribute on the earlier run's span
   (which kind of correction, seconds until it, the later run's id), so
   a wrong-action rate can be queried without labelling. "Near-repeat"
   has one stated, tested definition. Pure logic is separated from I/O
   and unit-tested: same satellite only, the 60 s window's edges,
   different satellites interleaved, a stop after a long song is not a
   correction of a request made minutes earlier, dev runs never mark or
   are marked by production runs.
9. The mark works without transcript text where it can (stop and "not
   that" are identifiable from the routing rule or HA's intent) and
   says so; near-repeat needs text and is skipped, not guessed, when
   text recording is off.
10. Late marking is handled: the earlier run's span may already be
    exported when the correction arrives. The report says how the mark
    reaches Tempo (a delayed export, a linked span, or a separate marker
    span that the query joins) and the query in criterion 3 uses it.
    Restarting `voice_tracer` neither loses pending marks silently nor
    emits duplicates.

### D. Labelling

11. A script that builds the labelling sheet from the recordings
    directory and Tempo: one row per recorded run with its audio path,
    time, satellite, what Whisper heard, and an empty column for the
    correct text; it can sample about 200 runs spread over days,
    satellites and tiers; it never modifies or deletes a recording; its
    output goes to a path outside the repo given on the command line.
    Tested against a fixture directory in HA's layout and stubbed
    Tempo. It tolerates runs with audio and no trace, and traces with
    no audio. The file format a bench (M13) will read is documented.
12. The labelled set's location is named for M16's backup list in the
    docs; nothing is added to a backup job in this item.

### E. Regression, safety, docs

13. `./scripts/run_tests.sh` passes: at least `4517 passed, 3 skipped,
    53 xfailed` plus the new tests. New tests do not reach the network.
14. On dev, with text recording turned on for the dev pair by a compose
    setting for the dev services (not `.env.dev`), a dev voice request's
    trace shows the text attributes of criterion 1, and the voice replay
    matches the M10 baseline row by row apart from the known flapping
    rows. Dev runs do not appear in the criterion 3 listing by default.
15. The build changes nothing in production, Home Assistant, Music
    Assistant or the pucks, records no audio, makes no sound, and sends
    no notification to Drew's phone (the notifier is stubbed in tests;
    one real test push is the coordinator's to send at install).
16. Docs: `docs/features/telemetry.md` and `docs/features/voice-pipeline.md`
    describe what is captured, where it is stored and for how long, who
    can read it (Grafana without a login, by Drew's decision), the end
    date mechanism, how to turn each part off, and the wrong-action
    query. `README.md` where it says traces hold no text. A line for the
    guest-facing fact: every puck records audio for two weeks.

## Reports

Build: `docs/sprint/M12-build.md`. End to end: `docs/sprint/M12-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
