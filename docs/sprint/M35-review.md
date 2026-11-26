# M35 design review: the time and date Kronk is told

Reviewer: design review agent, 2026-11-25 (23:00 EST). Read-only; nothing
built, no suite run. One request to dev's `/dev/api/chat` (point 1).

## Verdict

**Build with these changes.** The finding is right and the fix in
`kronk_facts()` is the right one. Five changes to the criteria:

1. A second wrong clock reader is in scope under criterion 4: the hourly
   labels of the weather forecast (`tool_service/main.py:354`) are in UTC
   and reach a model on every home-agent turn (point 2).
2. The fix breaks the clock freeze of `tests/test_m21_pins.py`; the
   builder retargets it, and the fixtures must not change (point 6a).
3. `KRONK_TZ` goes to the orchestrator only. The tool_service does not
   need it, and the three per-feature variables stay as they are
   (point 4).
4. "what time is it" is very likely answered by Home Assistant itself on
   a real voice run, so the corpus row is probably `ha_local`, and a
   spoken test of that sentence cannot show the fix (point 5).
5. The bad-zone criterion names the empty string and the case where the
   default zone itself does not load (point 6b).

## Points

### 1. The finding is right

- `orchestrator/agents.py:554-556`: `time.localtime()` and two
  `time.strftime` calls build the line. One call site, `agents.py:645`,
  inside the agent loop; `orchestrator/main.py:460` and `:570` both run
  that loop (`agents.run_stream`), so the coordinator path and every
  specialist get the line. Nothing else in `orchestrator/` builds a
  system text with a date.
- `docker exec … date` at 23:01:40 EST printed `Thu Nov 26 04:01:40 UTC
  2026` in all four: `kronk-orchestrator-1`, `kronk-orchestrator-dev-1`,
  `kronk-tool_service-1`, `kronk-tool_service-dev-1`. `TZ` empty,
  `/etc/localtime -> /usr/share/zoneinfo/Etc/UTC`, no `/etc/timezone`.
- `zoneinfo.ZoneInfo("America/New_York")` loads in all four from the
  image's `/usr/share/zoneinfo`. The pip `tzdata` package is not
  installed in any of them (`ModuleNotFoundError`), so the zone database
  is the base image's, not a pinned requirement.
- Dev baseline, asked 23:03:32 EST through `/dev/api/chat`: "It is 4:03
  AM UTC." Dev is wrong the same way before the build.

### 2. One more reader is wrong today: the weather hourly labels

- `tool_service/main.py:353-354`: `datetime.fromisoformat(start)` then
  `dt.astimezone().strftime("%-I %p")`. `astimezone()` with no argument
  converts to the container's zone, which is UTC. The National Weather
  Service sends each `startTime` with the forecast place's own offset;
  the code throws that away.
- Seen live on dev's `/weather/cached` (fetched about 22:22 EST): the
  hourly list starts "3 AM: 46°F", "4 AM: 45°F" and runs to "2 PM". The
  first period is the 10 PM hour.
- Who sees it: the model. `orchestrator/agents.py:36-57` puts the
  summary in the home agent's system text on every turn, and
  `get_weather` returns the same summary. "Will it rain at 7" is
  answered from a list that is five hours off (four in summer). After
  M35's main fix the prompt would say 10 PM beside a list that starts at
  3 AM, which is worse than today's consistent UTC.
- Fix: drop the conversion and label in the offset the period carries
  (`dt.strftime(...)`). That is right for the home and for any other
  place asked about (a Denver forecast labelled in Denver's hours), and
  it needs no zone variable. A `TZ` on the container would label
  Denver's hours in Eastern time.
- Test: two fixture periods, one `-05:00` and one `-07:00`, each
  labelled with its own written hour. No clock involved.
- The named periods ("Tonight", "Thursday") come from the Weather
  Service as text and are right.
- Deploy note: this is a tool_service change, so `tool_service-dev` is
  rebuilt for criterion 3 and production's tool_service is in the
  tagged deploy.

Everything else that reads a clock is either explicit about its zone or
is seen by nobody as a house time. The table at the end lists each one.

### 3. `KRONK_TZ` in `kronk_facts()` against `TZ` on the containers

The named zone in `kronk_facts()` is the right fix. The criteria's
reason for it is weaker than stated, so here is what `TZ` would change,
from the code:

- **Nothing stored moves.** SQLite's `datetime('now')` is UTC whatever
  `TZ` says; only the `'localtime'` modifier follows it, and neither
  service uses that modifier (`orchestrator/metrics.py:30,130` is the one
  `datetime('now')`, read back as UTC). Solar, news and taste name
  their zone or UTC in every call. No store holds container-local time.
- **Log lines move** in two services: `orchestrator/main.py:41` and
  `voice_tracer/main.py:98` put `%(asctime)s` in the format.
  `tool_service/main.py:59` has no timestamp in its format. Docker's own
  log timestamps stay UTC.
- **The settings audit log changes offset mid-file.**
  `orchestrator/settings_audit.py:33` writes `%z`, so entries would go
  from `+0000` to `-0500` in one file. Each stays correct; text order
  does not.
- **The config-reload message** (`orchestrator/config_loader.py:725`,
  `tool_service/config_loader.py:469`) carries a local time with no zone
  mark; it would become house time.
- It would fix `kronk_facts()` and the home forecast's labels with no
  code change, and mislabel forecasts for other places (point 2).

Why the variable is still better: the result does not depend on how a
container was started (a `docker run`, the test venv, a bench script on
the host: `scripts/coordinator_model_bench.py:58` imports
`kronk_facts`), it is testable with a fixed instant, and it changes one
line's behaviour, not six containers'.

One thing to record: the house zone is already written as
`TZ=America/New_York` in `docker-compose.ha.yml:57` and
`docker-compose.ma.yml:72,90`, and as the default of three tool_service
variables. `KRONK_TZ` is one more place. Acceptable; a comment beside it
in the compose file should say so.

### 4. The three per-feature variables, and the tool_service

- Do not make `NEWS_TZ`, `SOLAR_TZ` and `MUSIC_TASTE_TZ` fall back to
  `KRONK_TZ` in this item. All three are right today
  (`tool_service/news.py:256,272`, `solar.py:394`, `taste.py:747`,
  `main.py:2001` each pass a named zone). A fallback changes no
  behaviour, adds the variable to two more compose services and changes
  three rows of `tool_service/settings_info.py`.
- The tool_service does not need `KRONK_TZ` for this item. Its one wrong
  reader (point 2) is fixed without a zone.
- If the house ever moves there are four variables and two compose `TZ`
  lines to change. That is not worth a roadmap line.

### 5. Home Assistant's fast path and "what time is it"

Not called, as ordered. From the repo:

- `docs/features/voice-pipeline.md:18`: `prefer_local_intents` is on;
  the local matcher runs before the Kronk fallback.
  `docs/features/voice-regression-corpus.md:178-186`: the pipeline's
  local pass is strict; a fuzzy-only match falls through to Kronk.
- HA 2026.5.4 (`docker-compose.ha.yml:41`) has the built-in intent
  `HassGetCurrentTime`. Evidence in the repo: sixteen replay files in
  `docs/bench/` (for example `voice_replay_2026-11-17_192154_m12-build.json`)
  record a corpus row about the current song as "fuzzy
  HassGetCurrentTime only (strict pass misses)".
- The corpus has no row whose text asks the time or the date (162 rows
  searched), so no recorded strict match for the plain sentence exists.
  "what time is it" is that intent's plain sentence; I expect a strict
  match, which means HA answers on a real voice run, from its own clock
  (`TZ=America/New_York`, already right). **This is an inference, not a
  reading.** The replay's read-only debug match settles it in one row.
- So: the bug as reported shows only on the shim asked directly, on
  chat, and on every voice request that reaches Kronk with a relative
  date in it. A spoken "what time is it" proves nothing either way.
- For a spoken test use a sentence HA does not take: the date-tomorrow
  question (to be confirmed by the same debug match), or the "ask Kronk
  to" lead-in, which HA takes on no satellite
  (`voice-pipeline.md`, "The 'ask Kronk to' lead-in").

### 6. The criteria

a. **Criterion 1 needs a way to fix the clock, and it breaks an existing
   one.** `tests/test_m21_pins.py:66-78` freezes the prompt pins by
   replacing `agents.time` with an object whose `localtime()` returns a
   `struct_time` (2026-11-16 09:05 EST). Once `kronk_facts()` reads
   `datetime.now(zone)` that freeze does nothing and the pinned hashes
   in `tests/fixtures/m21/prompts.json` follow the live clock. The
   builder gives `kronk_facts()` one seam (an optional `now` argument or
   a module-level function returning the aware instant), moves the pins'
   freeze to it at 2026-11-16 14:05 UTC, and the fixtures stay
   byte-identical. That unchanged fixture file is also the proof of
   plumbing decision 4 (same wording, same "EST"). Add it to the
   criteria. No freezing library is in `tests/.venv`; none is needed.

   The test must pass the instant as UTC and must not read or set the
   process `TZ` (`tests/test_m30_voice_runs.py:438` does set it and
   calls `tzset`; a test that depends on the host zone would pass on
   this host by accident).

   Instants for the daylight change, so the test is not left to choose:
   2026-03-08 06:59 UTC is 1:59 AM EST and 07:00 UTC is 3:00 AM EDT;
   2026-11-01 05:59 UTC is 1:59 AM EDT and 06:00 UTC is 1:00 AM EST.

b. **Criterion 2 is short two cases.** On this Python
   `ZoneInfo("Nope/Zone")` raises `ZoneInfoNotFoundError` (a `KeyError`);
   `ZoneInfo("")` and `ZoneInfo("../etc/passwd")` raise `ValueError`.
   Catching only the first lets an empty `KRONK_TZ=` raise on every
   request. Read it as `os.getenv("KRONK_TZ") or DEFAULT` and catch
   both. Second case: the default zone itself does not load (a base
   image without the zone database; it is not a pinned requirement,
   point 1). Then the line goes out in UTC, says UTC, and the error is
   logged. "One warning" needs module state; the test resets it.

c. **Criterion 3 should say what passes.** The model words the answer
   freely. Pass: the hour and minute are the house's within a minute of
   the quoted wall clock, the zone named is EST, and "tomorrow" is the
   house's tomorrow. It is only a proof of the date when asked between
   19:00 and midnight EST; outside that window UTC and house dates
   agree. State the window in the criterion. Before the build the answer
   on dev is the one quoted in point 1.

d. **Criterion 5, the corpus row.** Its tier is `ha_local` with
   `{"source": "builtin", "intent": "HassGetCurrentTime"}` if the debug
   match is strict, `kronk` if not (point 5); the builder cannot decide
   that offline, so the criterion should say the test agent's replay
   decides and the row is corrected to it. Layer one checks the Kronk
   side either way (direct, default rule, no tool). Add the
   date-tomorrow question as a second row; it is the one that exercises
   the fix. `pipeline_bench.sh` is the coordinator's to run (agents may
   not), and it clears chat history.

e. **Dev wiring.** `LOCATION` is in `docker-compose.yml:259` for
   production only; dev takes it from `.env.dev`
   (`.env.dev.example:28`), which agents may not edit. With the default
   in code, dev needs no wiring. Put `KRONK_TZ` in production's compose
   environment and in `.env.dev.example`, not in `.env.dev`.

f. **Missing from the criteria:** a row for `KRONK_TZ` in the settings
   page's environment table (`orchestrator/settings_view.py:39`, beside
   `LOCATION`), and one line in the ambient-context paragraph of
   `docs/features/voice-pipeline.md`.

g. **The prompt cache is not this item's.** The line already carries the
   minute and already sits after the agent's prompt and before the
   weather block, so everything after it is re-read by the model server
   once a minute today. The fix changes the characters, not the
   position or the rate. No latency criterion is needed.

## Every clock reader (question 2)

Searched `orchestrator/`, `tool_service/`, `voice_tracer/` for
`time.localtime`, `time.strftime`, `datetime.now`, `date.today`,
`fromtimestamp`, `astimezone()`, `utcnow`, SQLite `datetime('now')` and
`'localtime'`. `date.today`, `utcnow` and `'localtime'`: no hits.
`voice_tracer` tells nobody a time (`time.time()` arithmetic only, plus
its log format).

| Where | Reads | Who sees it | Wrong today |
|---|---|---|---|
| `orchestrator/agents.py:554-556` | container local clock | the model, every route | **Yes.** The item. |
| `tool_service/main.py:354` | forecast hours converted to container zone | the model (home agent's system text, `get_weather`) | **Yes.** Point 2; in scope. |
| `tool_service/main.py:348` | UTC `now` | nobody (assigned, not used) | No. Dead line; the builder may remove it with the fix. |
| `orchestrator/config_loader.py:725`, `tool_service/config_loader.py:469` | container local, no zone mark | the operator: a failed reload's "kept … loaded <time>" in the log, the reload answer and the settings status | Misleading (UTC shown bare). Left over: operator text, not a house time; say UTC in the string if the builder is in the file, otherwise leave. |
| `orchestrator/settings_audit.py:33` | container local with `%z` | audit log file | No (`+0000` is written). |
| `orchestrator/main.py:41`, `voice_tracer/main.py:98` | container local in log lines | logs | Out of scope (logs, UTC). |
| `orchestrator/main.py:186,959,1141,1199,1338` | `time.gmtime()` with a `Z` | API fields (`read_at`, Ollama `created_at`) | No. |
| `orchestrator/metrics.py:30,130-169` | SQLite `datetime('now')`, UTC, marked `Z` on the way out | the performance page (the browser converts) | No. |
| `orchestrator/backups_api.py:40` | UTC | backup age arithmetic | No. |
| `tool_service/news.py:256,272` | `NEWS_TZ` | edition boundary; the brief's date in the summarizer's prompt; `generated_at_local` in the tool's answer | No. |
| `tool_service/news.py:151` | UTC | item window arithmetic | No. |
| `tool_service/solar.py:394-425` | `SOLAR_TZ` | "today", "yesterday" and the from/to of an energy answer (the model) | No. |
| `tool_service/solar.py:313,332,467` | UTC day string | the `day` key of stored readings and of the daily history rows (solar page, `solar_detail`); the day an inverter verdict covers | A solar "day" is a UTC day: it turns over at 19:00 EST (20:00 EDT). In June the last half hour before sunset lands in the next day's bucket. Left over: it is stored data, changing it changes what old rows mean, and the house-day answers (`solar_energy`) do not use it. |
| `tool_service/solar.py:254,362,507,592` | UTC ISO with offset | `as_of` fields | No. |
| `tool_service/taste.py:747`, `tool_service/main.py:2001` | `MUSIC_TASTE_TZ` | nightly build time; which taste rule applies now | No. |
| `tool_service/music_shadow.py:129` | `time.gmtime()` with a `Z` | shadow record file | No. |
| `orchestrator/static/*.html` | the browser's clock and zone | pages | No (the viewer's zone). |

Not a clock reader, checked because the brief names them: shopping list
tools carry no date logic in either service; there is no calendar tool
(`orchestrator/tools.py` tool list).
