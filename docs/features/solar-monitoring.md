# Feature: Solar health + energy monitoring (SunPower PVS5)

**Shipped:** health monitor 2026-07-14 (`ba2c195`), per-inverter detail
2026-07-17 (`cfde8a6`), energy tracking 2026-07-17 · **Origin:**
`docs/plans/SOLAR_MONITOR_PLAN.md` (design + live probing record) ·
**Motivating incident:** a failing inverter found only by chance — live
probing during planning found **four**, not one.

## What it does

Three home-agent tools over a 15-minute background poll in `tool_service`:

- **`solar_status`** — 1–2 sentence summary for chat *and* voice: current kW,
  how many inverters are underperforming right now, how many are
  *confirmed* failing and for how long.
- **`solar_detail`** — per-inverter power/voltage/temperature, consecutive
  bad-day counts, and short daily history for the troubled ones. For "which
  inverter", "is it getting worse", "why" questions.
- **`solar_energy`** — kWh produced. `lifetime` reads the PVS's live
  counters (system + all 24 inverters — also the authority for "which panel
  produced the most"); past periods (`today`/`yesterday`/`week`/`month`/
  `since:YYYY-MM-DD`) diff stored counter snapshots.

Alerting is automatic and separate from the tools: a confirmed inverter
failure raises **one** HA persistent notification per episode; recovery
dismisses it. Deterministic code detects; the LLM only narrates.

## How it works

**Data source.** The PVS5's varserver API via the `sunpower-bridge` Pi
(HAProxy fronts the PVS; `/auth` + `/vars` routes reach it). Login: Basic
`ssm_owner:<serial>` → session cookie, re-login on 401/403. `SOLAR_SERIAL`
lives in `.env` — it is effectively a credential. The production MagicMirror
display reads the same API but shows only the *summed* total — which is
exactly why a dead inverter was invisible.

**Failure detection — peer ratio, not sun models.** A cloud dims every
inverter equally, so each inverter is judged against the **array median**:
below `FAIL_RATIO` (0.40×) of the median in >70 % of a day's producing
samples (≥ `MIN_SAMPLES`, gated on the median clearing `PRODUCING_FLOOR`)
= a bad day. `CONFIRM_DAYS` (3) consecutive bad days = confirmed → notify
once. Good day resets; a missing serial counts as a bad day (hard-dead
inverters confirm too, single dropped polls don't). Corroborating fault
tell: `vMppt1V` ~60 V+ (open circuit — the inverter isn't loading its
panel; shade doesn't do that).

**Energy — the PVS is a gauge, not a historian.** `/sys/livedata/pv_en` is
a lifetime counter whose *current* value is all the PVS can report.
Lifetime queries are therefore always live (storing them would duplicate).
Past periods are only answerable if someone recorded the counter at the
period's boundary — so every health poll snapshots `pv_en` into a dedicated
`energy(ts, day, pv_en)` table (not a column on `readings`, which is one
row per inverter per poll — 24× duplication). "Today" = current counter −
snapshot nearest last *local* midnight. Periods predating tracking clamp
honestly ("tracking only began <date>").

**Storage:** SQLite `/data/solar.db` — `readings`, `daily` verdicts,
`inverter_state`, `energy` snapshots.

**Routing:** deterministic `_SOLAR_RE` shortcut sends solar/inverter/PV
queries to the home agent; the LLM router never sees them.

## Verified behavior (live, 2026-08-12/14)

- Energy snapshots: 2,499 rows, unbroken 2026-07-17 → present, including
  through the 2026-08-12 host crash (`INCIDENT_2026-08-12.md`).
- Live figures sane: lifetime 66,361.6 kWh / 24 inverters; "today" measured
  from the 23:53 snapshot (nearest local midnight); week 194.6 kWh.
- State machine: two inverters `confirmed_failing` (…003219 at 31 days,
  …005632 at 8) — both among the four found during planning.
- Tests: fixture-driven parse/detect/rollup/state-machine/energy suites in
  `tests/test_solar.py` (part of the standard battery).

## Known remaining gaps

- Per-inverter energy is **lifetime only** — past-period diffs use the
  system counter; per-inverter snapshots were deliberately not stored.
- Alerting is HA persistent notification only; voice announce deferred
  (operator decision 2026-07-14) — the announce primitive exists when
  wanted.
- `solar.db` has no backup story yet (ROADMAP item 4). Losing it restarts
  the multi-day clock and erases energy history — the snapshots are the
  *only* copy of past counter values, by design of the PVS.
- Two inverters remain confirmed-failing as of 2026-08-14; warranty/service
  follow-up is an operator matter, not a Kronk one.

## Blog hooks

- Dead inverters hide in aggregates: the mirror showed the sum, so four
  failures looked like weather. Per-device data was one API away.
- Peer-ratio beats solar geometry: no sun-elevation math, no irradiance
  model — "below 40 % of the array median, repeatedly" catches faults and
  ignores clouds, because clouds are fair.
- Your inverter is a gauge, not a historian: record the counter or lose the
  past. The 15-minute snapshot that costs nothing and is the only copy of
  "yesterday".
- Certainty over speed as an alerting philosophy: three consecutive bad
  days before a notification, and exactly one notification per episode.
