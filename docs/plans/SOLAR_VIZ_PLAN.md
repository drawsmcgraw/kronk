# Solar visualization — Plan

Status: **shipped 2026-08-27** (same day). Distilled into
`docs/features/solar-viz.md` — read that first. Verified live: migration
kept 3,914 legacy rows; counter snapshots flowing (mirror confirmed →
consumption gated off); series/heatmap/drill-down all serving through
nginx; the failing inverter's 0 kW @ 61 V signature visible in the
per-panel view. Option A (Kronk-native
`/solar` dashboard page) chosen over HA-energy-export (stretches "HA is a
broker") and Grafana (fails right-size).

## Operator decisions (2026-08-27)

1. All four panels: now-strip, intraday power curve, daily energy bars,
   per-inverter health heatmap.
2. Selectable time windows (1/7/30/90 days).
3. Per-inverter drill-down view (power + vMPPT — the fault tell).
4. **Counter baseline ships first**: snapshot `site_load_en`/`net_en`
   alongside `pv_en` so consumption/net history starts accruing now.

## Build-time discovery (2026-08-27 probe)

`site_load_en` and `net_en` are byte-identical on this install
(122,818.59 both; instantaneous `*_p` values mirror too) — **no real
consumption CTs**. The counters are recorded anyway (history starts when
snapshots start; CTs may get wired later), and every consumption-derived
view is gated on `consumption_real` — true only once the two counters
diverge. Nothing renders fake consumption data.

## Design

- **tool_service/solar.py**: `parse_counters()`; `energy` table gains
  `site_load_en`/`net_en` columns (idempotent ALTER migration; old rows
  NULL); `record_energy()` widened. Series functions: `power_series(days,
  sn)` (bucketed — 15 min ≤2 d, hourly ≤14 d, daily beyond; system = sum
  over inverters per poll), `daily_energy(days)` (last-snapshot-per-day
  diffs; day keys are UTC-day as stored — boundary lands ~20:00 EDT,
  after generation ends, so daily figures match `solar_energy` within
  rounding), `heatmap(days)` (per sn×day verdicts derived from `readings`
  with the same producing-gate/FAIL_RATIO/MIN_SAMPLES rules as the
  rollup — no verdict history table exists, so derive, don't duplicate),
  `consumption_data_real()`.
- **`GET /solar/series?days=N[&inverter=SN]`** composes the payload.
- **nginx**: `location /api/solar/` → tool_service `/solar/` (read-only
  endpoints only on that prefix).
- **orchestrator**: serves `static/solar.html` at `/solar` — same
  skeleton as resources/health pages, vendored Chart.js, heatmap as a
  plain HTML table (no chart plugin). Window buttons refetch; heatmap
  row click opens the drill-down.

## Steps + tests

1. Counter baseline: parse/migrate/record + tests (parse, migration on
   legacy schema, storage round-trip). Deploy early — every poll from
   today is baseline.
2. Series functions + endpoint + tests (bucketing math, daily diffs incl.
   NULL counters, heatmap verdict derivation, consumption flag, endpoint
   shape).
3. Page + nginx route + orchestrator route; nav link on index.
4. Deploy, live-verify JSON + a counter-bearing snapshot row, operator
   eyeballs the page.
5. Feature doc + ROADMAP Shipped.
