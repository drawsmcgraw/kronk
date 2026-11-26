# M35 build report: the time and date Kronk is told are the house's

Build agent, 2026-11-25. Specification: `M35-criteria.md` with its
2026-11-25 ruling; `M35-review.md` points 2, 6a, 6b, 6e, 6f.

## What changed

- `orchestrator/agents.py`: `kronk_facts()` takes the instant from
  `_utc_now()` (the one seam: an aware UTC `datetime`) and states it in
  `house_zone()`. `house_zone()` reads `os.getenv("KRONK_TZ") or
  "America/New_York"` (`HOUSE_TZ_DEFAULT`). A name that raises
  `ZoneInfoNotFoundError` or `ValueError` falls back to the default with
  one warning per name per process (`_tz_warned`); if the default does not
  load, it returns `timezone.utc`, the line says UTC, and the error is
  logged once. Never raises. The line's wording is unchanged.
- `tool_service/main.py`: new `_hour_label(start)` labels an hourly
  period with the hour written on it in its own offset
  (`datetime.fromisoformat(start).strftime("%-I %p")`), the raw string
  when it does not parse. The conversion to the container's zone
  (`astimezone()`) and the unused `now = datetime.now(timezone.utc)` are
  gone, with the function-local import.
- `tests/test_m21_pins.py`: the freeze is `agents._utc_now` returning
  2026-11-16 14:05 UTC (was `agents.time` replaced by a `struct_time` at
  09:05 EST). `KRONK_TZ=America/New_York` is pinned beside `LOCATION`.
  `tests/fixtures/m21/prompts.json` is byte for byte unchanged: sha256
  `0c261a5948e24211c52fbbbafa044a2fecba65e174b2d558be779a1e40fa9c53`
  before and after; every pin passes.
- Wiring: `docker-compose.yml` production orchestrator gets
  `KRONK_TZ=America/New_York` after `LOCATION`, with a comment naming the
  other places the zone is written (`NEWS_TZ`, `SOLAR_TZ`,
  `MUSIC_TASTE_TZ`, `TZ` in `docker-compose.ha.yml` and
  `docker-compose.ma.yml`). `.env.dev.example` gets the same line (not
  `.env.dev`; dev runs on the code default, and
  `docker exec kronk-orchestrator-dev-1` shows `KRONK_TZ` empty).
  `orchestrator/settings_view.py` `ENV` gets the `KRONK_TZ` row after
  `LOCATION`. `docs/features/voice-pipeline.md`, ambient-context bullet:
  one sentence. CLAUDE.md, `.env`, `.env.dev` not touched.
- `tests/voice_corpus.jsonl`: two rows (below).

## Criteria

| # | Test | Result |
|---|---|---|
| 1 | `tests/test_m35_house_time.py`: `test_the_reported_evening_is_wednesday_in_est` (2026-11-26 03:20 UTC → "Wednesday, November 25, 2026; … 10:20 PM EST"), `test_a_summer_instant_is_edt` (2026-07-04 16:30 UTC → 12:30 PM EDT), `test_either_side_of_the_daylight_changes` (the ruling's four instants: 1:59 AM EST / 3:00 AM EDT on 2026-03-08, 1:59 AM EDT / 1:00 AM EST on 2026-11-01), `test_kronk_tz_names_another_zone` (Denver, MST), `test_the_rest_of_the_facts_are_unchanged`; plus the unchanged M21 fixture | pass |
| 2 | `test_a_bad_kronk_tz_falls_back_with_one_warning` (`Nope/Zone`, `../etc/passwd`, `/etc/localtime`, `EST5EDT/../x`; three calls, one warning), `test_an_empty_kronk_tz_is_the_default_without_a_warning`, `test_without_a_zone_database_the_line_says_utc` (`ZoneInfo` patched to raise: "3:20 AM UTC", Thursday, one ERROR), `test_a_bad_kronk_tz_and_no_zone_database` (one WARNING, one ERROR) | pass |
| 3 | Dev, below | pass (inside the date window) |
| 4 | Forecast labels: `test_hourly_labels_keep_each_periods_own_offset` (two fixture periods through `/weather`: `22:00-05:00` → "10 PM", `20:00-07:00` → "8 PM", no "3 AM"), `test_hour_label` (five cases incl. unparseable and empty). With the old `astimezone()` both periods would read "3 AM". The two leftovers the ruling names (config-reload message's bare time; the solar UTC day) are not touched: I was not in those files. | pass |
| 5 | Corpus rows added; layer one passes. `pipeline_bench.sh` is the coordinator's; not run. | layer one pass |
| 6 | Suite, below | pass |
| 7 | The coordinator's, at promotion | not mine |

Wiring: `test_the_zone_is_wired_where_the_ruling_says` checks the
settings row's default, production compose and `.env.dev.example` all
equal `agents.HOUSE_TZ_DEFAULT`.

Every test passes the instant as UTC through `agents._utc_now` and
neither reads nor sets the process `TZ`.

### Corpus rows (tiers are my reading; the test agent's replay corrects them)

- `m35-what-time-is-it`, "what time is it": `ha_local`,
  `{"source": "builtin", "intent": "HassGetCurrentTime"}`; route
  `direct/default`, tool `null`.
- `m35-date-tomorrow`, "what is the date tomorrow": `kronk`; route
  `direct/default`, tool `null`. No HA built-in takes a relative date, as
  far as I can tell offline.

Both tagged `non-music`, `time`, `m35`; sources are the criteria and the
review.

## Criterion 3: dev answers

Dev pair rebuilt with exactly
`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`.
Inside the container after the build: `date` printed
`Thu Nov 26 04:50:32 UTC 2026`; `agents.kronk_facts()` line two printed
`- Today is Wednesday, November 25, 2026; the local time is 11:50 PM EST.`

Body for each: `{"model":"kronk","stream":false,"messages":[{"role":"user","content":"<question>"}]}`.
Wall clock is when the request was sent (host, America/New_York).

| Asked at | Route | Question | Answer |
|---|---|---|---|
| 2026-11-25 23:50:40 EST | `/dev/api/chat` | what time is it | "The current time is 11:50 PM EST." |
| 2026-11-25 23:50:42 EST | `/dev/api/chat` | what is the date tomorrow | "Tomorrow is Thursday, November 26, 2026." |
| 2026-11-25 23:50:44 EST | `/dev/voice/api/chat` | what time is it | "It is 11:50 PM EST on Wednesday, November 25, 2026." |
| 2026-11-25 23:50:45 EST | `/dev/voice/api/chat` | what is the date tomorrow | "Tomorrow is Thursday, November 26, 2026." |

Asked between 19:00 and midnight EST, so the date is proven: the UTC date
was already the 26th, and the old line would have made tomorrow Friday,
November 27. Baseline before the build (review, 23:03 EST): "It is 4:03
AM UTC."

## Not done

- `pipeline_bench.sh` before and after (criterion 5): the coordinator's.
- Production: not touched. No production container rebuilt or restarted,
  no request to a production route, no deploy, no tag. `kronk-orchestrator-1`
  and `kronk-tool_service-1` were "Up 2 hours" after the dev rebuild.
- The replay that settles the two corpus tiers: the test agent's.
- Commit order: `cf67658` carries `tests/test_m35_house_time.py`, whose
  wiring test passes only from `5fe8347` (the compose, example and
  settings rows). Every other test passes at `cf67658`. The tree at the
  report commit passes whole.

## Suite

`./scripts/run_tests.sh` at `5fe8347` (the code as committed; this report
changes no code):

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
8149 passed, 3 skipped, 57 xfailed, 6 warnings in 164.97s (0:02:44)
```

Was `8118 passed, 3 skipped, 57 xfailed`: 31 new (23 in
`test_m35_house_time.py`, 8 from the two corpus rows).
