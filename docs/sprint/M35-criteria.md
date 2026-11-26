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
