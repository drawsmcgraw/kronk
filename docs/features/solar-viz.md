# Feature: Solar dashboard (/solar)

**Shipped:** 2026-08-27 · **Plan:** `../plans/SOLAR_VIZ_PLAN.md` ·
**Companion to:** `solar-monitoring.md` (the data this page draws).

## What it does

`/solar` (linked from the chat header) — four panels over the monitoring
data, with 1/7/30/90-day windows:

- **Now-strip**: current kW, today/week/lifetime kWh, confirmed-failing
  count (red when nonzero). Refreshes every minute.
- **Power curve**: system total, bucketed 15 min/hourly/daily by window.
- **Daily energy bars**: per-day kWh from counter-snapshot diffs.
- **Inverter health heatmap**: 24 inverters × days, colored by derived
  daily verdict (good/bad/too-few-samples), sorted failing-first with
  bad-day badges. **Click a row** → per-panel drill-down: that inverter's
  power + vMPPT overlaid — a failed unit reads ~0 kW at ~61 V
  (open-circuit), visibly.

Served by `GET /solar/series?days=N[&inverter=SN]` on tool_service
(nginx: `/api/solar/` → read-only solar endpoints), Chart.js from the
already-vendored bundle, heatmap as a plain HTML table.

## The counter baseline (shipped first, deliberately)

Every 15-minute poll now snapshots **all** PVS lifetime counters —
`pv_en` plus `site_load_en`/`net_en` (consumption and meter-net). The
`energy` table was widened in place (idempotent ALTER; 3,914 legacy rows
keep NULLs). Discovery during build: **this install's `site_load_en`
byte-mirrors `net_en`** — no consumption CTs are wired — so all
consumption-derived views gate on `consumption_data_real()` (true only
once the counters diverge) and the page says so instead of charting
mirror data. If CTs ever get installed, history counts from 2026-08-27.

## Gotchas

- Heatmap verdicts are **derived** from `readings` at query time using
  the rollup's own thresholds — there is no per-day verdict history
  table, so derive, don't duplicate. The derivation and the rollup share
  constants; if the rollup rules change, `heatmap()` follows.
- `energy.day` is keyed by **UTC** day (as stored since July). UTC
  midnight ≈ 20:00 local, after generation ends, so daily bars match
  `solar_energy` figures within rounding — but don't treat the day key
  as a local-midnight boundary.
- The `/api/solar/` nginx prefix exposes tool_service's solar endpoints
  to the LAN — all read-only today; keep mutating endpoints off that
  prefix.

## Blog hooks

- The heatmap that replaces a paragraph: 40 days of inverter failure as
  one red row.
- Recording counters you can't use yet: baseline-first thinking when the
  meter is a gauge, not a historian.
- A dashboard with zero new dependencies: vendored Chart.js, SQL
  aggregation, an HTML-table heatmap.
