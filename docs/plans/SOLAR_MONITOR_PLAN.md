# Solar system health monitoring — Plan

Status: **design complete, ready to build (2026-07-14).** Goal: proactively
alert the operator when an inverter is **certainly** failing, and answer
"how's my solar?" on demand. Motivating incident: a failing inverter found
only by chance from glancing at output — live probing during this planning
found **four** underperforming inverters, not one.

## Operator decisions (2026-07-14)

1. **Alert channel: HA persistent notification** only, for now (other
   channels later).
2. **Certainty over speed.** Prefer an inverter failing for *a few days*
   before alerting — no twitchy alarms on passing clouds. Multi-day
   confirmation is the core of the design.
3. **On-demand skill:** "what's the solar system status?" / "check the
   solar system" → a **short summary** (not a detailed dump), in chat UI
   *and* voice. E.g. *"Four inverters are underperforming right now; the
   system is generating 4.9 kW"* or *"The solar system is healthy, currently
   generating 5.4 kW."*

## Confirmed data source (probed live 2026-07-14)

PVS family: **PVS5** (`/sys/info/model`=PVS5, sw_rev 0.0.25.5412,
serial ZT181085000441**D1901**). The production mirror uses the PVS
**varserver** API (read from its `node_helper.js`), which Kronk can reach:

- **Auth:** `GET http://sunpower-bridge.home.hippiehouse.net/auth?login`
  with `Authorization: Basic base64("ssm_owner:" + PVS_SERIAL)` (serial
  `D1901`) → `{ "session": "<token>" }`. Cache the session; re-login on
  401/403 (the mirror does this).
- **Data:** `GET …/vars?match=inverter` with `Cookie: session=<token>` →
  `{ values: [ {name:"/sys/devices/inverter/N/<field>", value:"…"}, … ] }`.
  Permissive TLS (self-signed). HAProxy on the bridge Pi routes `/auth` +
  `/vars` to the PVS; other paths go to Kronk (why earlier `/` probes
  returned Kronk).
- **Per-inverter fields** used: `sn` (roster key), `p3phsumKw` (AC power),
  `vMppt1V` (panel voltage — the fault tell), `tHtsnkDegc` (heatsink temp),
  `ltea3phsumKwh` (lifetime energy). System total for the summary:
  `/vars?match=livedata` → `/sys/livedata/pv_p`.
- **24 inverters.** The mirror only shows the *summed* total, which is
  exactly why a dead inverter is invisible on the display.

### Live finding (the four failing inverters)

Healthy inverters made ~0.22–0.24 kW at `vMppt1V`~51–53 V. Four made
near-zero power *with elevated ~60–62 V* (open-circuit = inverter not
loading its panel → a fault, not shade):

| inv | serial | kW | vMppt1 |
|---|---|---|---|
| 0  | 450051817003219 | 0.000 | 61.5 |
| 21 | 450051818005632 | 0.009 | 60.4 |
| 19 | 450051818002424 | 0.050 | 61.7 |
| 4  | 450051815011992 | 0.061 | 61.5 |

## Architecture (fits existing patterns)

All in **tool_service** (it already reaches the bridge + HA, has the
background-loop pattern from the weather cache, and holds `/data`):

- **`tool_service/solar.py`** — auth + fetch + parse + detection logic
  (pure, testable). Session cached in memory.
- **Background poll loop** (like `_weather_refresh_loop`): every
  `SOLAR_POLL_MIN` (default 15) minutes, fetch per-inverter, append to
  SQLite, and run the daily-rollup + multi-day state machine. Fires the HA
  notification on a confirmed-failure transition.
- **`GET /solar/status`** — live snapshot + confirmed-failure state, for the
  on-demand tool.
- **SQLite `/data/solar.db`:** `readings` (ts, inverter_sn, power, vmppt,
  temp, array_median), `daily` (date, inverter_sn, verdict), `inverter_state`
  (sn, state, bad_days, confirmed_at, notified).

Orchestrator side:
- **`solar_status` tool** (mirrors `query_hottub`) → `GET /solar/status`,
  added to the **home** agent.
- **Routing shortcut** `_SOLAR_RE` (`solar|inverter|solar panel|pv system`)
  → home (weather-shortcut precedent), so "check the solar system" doesn't
  depend on the LLM router.

## Detection & the multi-day certainty machine

Two thresholds, deliberately different for the two jobs:

**Live snapshot (for the on-demand skill):** an inverter is "underperforming
right now" if, during production, `p3phsumKw` < `FAIL_RATIO` (default 0.40)
× the array's median inverter power. Corroborated by `vMppt1V` above the
healthy cohort. This is a *momentary* read — fine for "what's happening now",
never used to alert.

**Confirmed failure (for alerting):**
1. **Producing gate:** a poll counts only if the array median power >
   `PRODUCING_FLOOR` (default 0.03 kW) — self-calibrating for daylight; no
   sun-elevation math, and it discards night + fully-overcast samples where
   nothing can be judged.
2. **Daily verdict:** an inverter has a **bad day** if, across that day's
   producing samples (need ≥ `MIN_SAMPLES`, default 8), it was below
   `FAIL_RATIO` of the array median in > 70% of them. A cloud dims *every*
   inverter equally, so the *peer ratio* stays healthy — clouds don't
   produce bad days; a faulted inverter is low relative to peers every day.
3. **Confirmation:** `CONFIRM_DAYS` (default **3**) consecutive bad days →
   state `confirmed_failing` → fire **one** HA notification. This is the
   "failing for a few days, then alert" the operator asked for.
4. **Recovery:** a good day resets `bad_days`; if a confirmed inverter
   recovers, clear/dismiss its notification.
5. **Missing inverter** (sn absent from the roster): treated as a bad day
   too, so a hard-dead inverter also confirms over `CONFIRM_DAYS` (avoids
   alarming on a single dropped poll).

All thresholds are env-configurable; detection is deterministic code — the
LLM only narrates.

## The on-demand skill

`solar_status` returns structured data; the home agent renders a 1–2
sentence summary (never a table dump):

- healthy: *"The solar system is healthy, currently generating 5.4 kW."*
- issues: *"Four inverters are underperforming right now and the system is
  generating 4.9 kW. Two of them have been failing for several days."*

`/solar/status` payload: `{ total_kw, inverter_count, live_underperforming,
confirmed_failing: [{sn, days}], as_of }`. Works identically in chat and
voice (same pipeline entry point).

## Alerting — HA persistent notification

On a `confirmed_failing` transition, `POST /api/services/
persistent_notification/create` (existing `HA_URL`/`HA_TOKEN` path) with a
stable `notification_id` per inverter (so re-runs update, not duplicate;
recovery dismisses via `persistent_notification/dismiss`). Message names the
inverter and evidence: *"Solar inverter …003219 (panel #0) has produced
near-zero power for 3 days while its neighbors are normal — likely failed.
Current array output 4.9 kW."* One notification per failure episode, never
per-poll (transition-gated). Voice announce is deferred (HA-notification-only
per the decision), but the announce primitive is already available if wanted.

## Config (env on tool_service)

`SOLAR_HOST` (http://sunpower-bridge.home.hippiehouse.net), `SOLAR_SERIAL`
(D1901), `SOLAR_POLL_MIN` (15), `PRODUCING_FLOOR` (0.03), `FAIL_RATIO`
(0.40), `MIN_SAMPLES` (8), `CONFIRM_DAYS` (3). The serial is a credential
of sorts — put `SOLAR_SERIAL` in `.env`, not committed.

## Build steps + tests (each lands green before the next)

1. **`solar.py` fetch/parse + `/solar/status` live snapshot.**
   *Tests:* fixture varserver JSON → 24 parsed inverters; live-outlier count
   correct (the 4 known bad ones flagged, healthy ones not); auth re-login
   on a 403 fixture. `/solar/status` returns the summary payload.
2. **`solar_status` tool + home-agent wiring + routing shortcut.**
   *Tests:* "check the solar system" → home deterministically; tool formats
   healthy vs N-failing summaries; a mocked `/solar/status` drives an
   end-to-end `/message` answer containing the kW and count.
3. **Poll loop + SQLite readings + daily rollup.**
   *Tests:* simulated poll sequence writes readings; a day of samples rolls
   up to the right per-inverter verdict; cloudy day (all-low) is skipped
   (no verdict), all-healthy day → no bad day, one dead inverter → bad day.
4. **Multi-day state machine + HA notification.**
   *Tests:* 3 consecutive bad days → `confirmed_failing` + notification
   fired **once** (mocked HA); a good day resets; recovery dismisses; a
   missing-sn sequence also confirms over 3 days; no notification spam on
   day 4+.
5. **Live bring-up.** Deploy, watch a few real poll cycles land in SQLite,
   confirm `/solar/status` reports the four known bad inverters, and — since
   they're already failing — verify the state machine confirms + notifies
   after ~3 days (or seed history to test the transition without waiting).

## Notes

- One instantaneous reading isn't proof; the multi-day machine is precisely
  the certainty the operator wants. The four inverters found during planning
  should confirm quickly once history accumulates (or via seeded test).
- `solar.db` is small but is the kind of state ROADMAP item 4 (backups)
  should cover; losing it just restarts the multi-day clock, not the world.
- Deterministic detection; the LLM only phrases summaries and alerts
  (tenet: math in code, model narrates).
