# M35 pass criteria: the time and date Kronk is told are the house's

Source: Drew, 2026-11-25. The coordinator, checking the voice shim after
a deploy, asked "what time is it" at 22:20 EST on Wednesday 25 November
and got "The current time is 3:20 AM UTC on Thursday, November 26,
2026." Drew: "Yes, fix the utc issue as its own item."

Handoff line: every prompt carries the date and time of the house's
zone, so Kronk tells the right time and resolves "today" and "tomorrow"
against the right day, on chat and on voice.

Plan doc: this file. Tier 3 (the line is in the system prompt of every
route): a design review, one test, a fresh narrow test after any fix.

## What the investigation found (coordinator, 2026-11-25)

- `kronk_facts()` in `orchestrator/agents.py` builds "Today is …; the
  local time is …" from `time.localtime()`. It is appended to the system
  prompt of the coordinator path and of every specialist agent.
- The production containers have no zone: `TZ` is empty and
  `/etc/localtime` points at `Etc/UTC` (`docker exec kronk-orchestrator-1
  date` prints UTC; `tool_service` the same). So the line states the UTC
  date and time and calls it local.
- The date is wrong from 19:00 EST (20:00 EDT) to midnight every day: a
  relative date ("tomorrow", "this weekend") asked in the evening is
  resolved a day late.
- The line dates from 2026-06-12 (commit `858b142`), added after the
  model guessed the date from its training. It has been UTC since.
- The zone database is in the image: `zoneinfo.ZoneInfo(
  "America/New_York")` loads in the production orchestrator.
- Three features already name the zone, each by its own variable with
  the same default: `NEWS_TZ`, `SOLAR_TZ` (tool_service) and
  `MUSIC_TASTE_TZ`. No test covers `kronk_facts`' clock.

## Plumbing (coordinator's decisions, open to the review)

1. `kronk_facts()` reads the time in a named zone with `zoneinfo`, not
   the container's local clock. The zone comes from one environment
   variable, `KRONK_TZ`, default `America/New_York`, beside `LOCATION`
   in `docker-compose.yml` for production and dev.
2. The containers' own zone is not set. That would move log timestamps
   and every other reader of the local clock in six containers for a
   fix that needs one line to change.
3. A zone name that does not load is not an error at request time: the
   line falls back to the default zone and a warning is logged once.
   The prompt never goes out with no date.
4. The line keeps its wording and names the zone's abbreviation (EST,
   EDT), as now.
5. The three per-feature variables are not merged in this item. The
   review says whether they should follow `KRONK_TZ` when unset, and
   what else in the orchestrator or tool_service reads the local clock
   for something a person sees or a model is told (those are in scope;
   log lines are not).

## Criteria

1. With the clock fixed at 2026-11-26 03:20 UTC, `kronk_facts()` says
   Wednesday, November 25, 2026 and 10:20 PM EST. With it fixed at a
   summer instant, EDT. At the two instants either side of a daylight
   change, the right hour.
2. A bad `KRONK_TZ`: the default zone is used, one warning, no raise.
3. On dev after the build: "what time is it" and "what's the date
   tomorrow" through `/dev/voice/api/chat` and `/dev/api/chat` answer in
   the house's time, the second with the house's tomorrow. Quoted with
   the wall-clock time they were asked.
4. Whatever else the review finds that tells a person or a model the
   time from the container's clock is fixed the same way, each with a
   test, or named as left over with the reason.
5. The voice corpus gains "what time is it" (layer one). The live
   battery (`pipeline_bench.sh`) before and after: no route changes.
6. The suite passes; last lines quoted.
7. By the coordinator at promotion, on production: the two questions of
   criterion 3 through both shims.

## Out of scope

- Setting a zone on any container.
- Timestamps in logs, databases and telemetry (UTC is right there).
- A way to change the zone on `/settings`.

## Rulings
- 2026-11-25, coordinator, on the design review (`M35-review.md`: build
  with these changes). All taken; where a point below differs from the
  text above, the point holds:
  - The weather forecast's hourly labels are in scope (criterion 4):
    `tool_service/main.py:354` converts each period to the container's
    zone, so the home agent's forecast list is five hours off. Fix: label
    each period in the offset it arrives with; no zone variable. Test:
    two fixture periods, `-05:00` and `-07:00`, each with its own hour.
    The dead `now` on line 348 goes with it. The tagged deploy therefore
    includes tool_service, and dev's tool_service is rebuilt for
    criterion 3.
  - `KRONK_TZ` is read by the orchestrator only: production's compose
    environment with a comment naming the other places the house's zone
    is written, `.env.dev.example` (not `.env.dev`), a row in the
    settings page's environment table, one line in
    `docs/features/voice-pipeline.md`. `NEWS_TZ`, `SOLAR_TZ` and
    `MUSIC_TASTE_TZ` stay as they are.
  - `kronk_facts()` gets one seam for the instant. `tests/test_m21_pins.py`
    moves its freeze to it at 2026-11-16 14:05 UTC and
    `tests/fixtures/m21/prompts.json` stays byte for byte the same; that
    is the proof the wording did not change. Tests pass the instant as
    UTC and neither read nor set the process `TZ`.
  - Criterion 1's daylight instants: 2026-03-08 06:59 UTC (1:59 AM EST)
    and 07:00 UTC (3:00 AM EDT); 2026-11-01 05:59 UTC (1:59 AM EDT) and
    06:00 UTC (1:00 AM EST).
  - Criterion 2: the variable is read as `os.getenv("KRONK_TZ") or` the
    default; a name that raises (`ZoneInfoNotFoundError` or `ValueError`:
    unknown, empty, a path) falls back to the default with one warning.
    If the default itself does not load, the line goes out in UTC, says
    UTC, and the error is logged.
  - Criterion 3 passes when the hour and minute are the house's within a
    minute of the quoted wall clock, the zone named is EST, and
    "tomorrow" is the house's tomorrow. The date is only proven between
    19:00 and midnight EST; outside it the test agent says so and shows
    the date by the unit test instead. Baseline before the build, dev,
    23:03 EST: "It is 4:03 AM UTC."
  - Criterion 5: two corpus rows, the time question and the
    date-tomorrow question. Home Assistant very likely answers "what
    time is it" itself (`HassGetCurrentTime`) from its own, correct,
    clock; the test agent's read-only replay decides each row's tier and
    the row is corrected to it. `pipeline_bench.sh` is the coordinator's
    to run.
  - Left over, not this item: the config-reload message's bare time
    (say UTC in the string if the builder is in the file); a solar "day"
    in stored readings is a UTC day (stored data; the house-day answers
    do not use it).
  - A spoken "what time is it" cannot show the fix. Drew's spoken test
    is a relative-date question, or one with the "ask Kronk to" lead-in.
