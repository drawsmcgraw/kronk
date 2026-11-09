# Investigation 2026-11-08 — Tempo metrics queries returning 500 after back-dated spans

**Status:** resolved 2026-11-08 during the Telemetry v2 build. Cause understood,
blocks removed, guard rails in place. Reported here because it will bite
anyone who emits spans with timestamps in the past (the voice_tracer does,
by design).

## Symptom

TraceQL metrics queries (`/api/metrics/query_range`) over the afternoon's
data returned `HTTP 500  end must be greater than start`. Range search and
trace-by-id kept working. Grafana dashboards would have shown "no data" or an
error for every panel whose time range touched the bad blocks.

## Timeline (UTC)

- 21:49 Tempo 3.1.1 up, defaults (`ingestion_time_range_slack: 2m` on all
  three WALs — live-store, block-builder, storage).
- 22:07 voice_tracer first start with a 1-hour backfill: emits the 21:11
  Anime Pop pipeline run and five 21:23–21:25 blueprint runs, all ~45–55
  minutes older than their ingestion time.
- 22:15, 22:20, 22:23 three deliberate back-dated test spans (−10, −5,
  −5 min) sent while investigating why a backfilled run was not findable
  at its real time.
- 22:27 `{} | count_over_time()` over 3 h → 500. Tempo's frontend log shows
  the per-block sub-request for block `1b6012d5` with
  `start=1794175500… end=1794173400…` (start after end).
- 22:31 block metadata listed from the `tempo-data` volume: six blocks with
  `startTime > endTime`, each holding exactly the back-dated spans (one was
  a compaction of two others).
- 22:33 Tempo stopped, the six block directories removed, Tempo started with
  `ingestion_time_range_slack: 15m` on all three WALs. Metrics over 3 h:
  281 spans, all 42 dashboard queries 200.

## Root cause

Tempo clamps a block's start/end to the ingestion time ± slack and does not
let spans outside the slack widen the range. A block whose only (or
earliest) spans are far older than the slack ends up with its `startTime`
taken from one rule and `endTime` from another, inverted. The query frontend
intersects the query range with the block range per job; an inverted block
range yields a sub-request with start > end, which the querier rejects, and
the whole metrics query fails.

## Fix and guard rails

- `telemetry/tempo.yaml`: `ingestion_time_range_slack: 15m` on
  `storage.trace.wal`, `live_store.wal` and `block_builder.wal`.
- `voice_tracer/main.py`: startup backfill 10 minutes (was 60); event-driven
  and polled fetches look back 10 minutes.
- Documented in `docs/TELEMETRY_GUIDE.md` and `docs/features/telemetry.md`:
  never send Tempo a span older than the slack; how to find and remove an
  inverted block if it happens again.

## What would have caught it sooner

- Validating dashboard queries over a window that included the backfilled
  spans before writing more test spans. The first validation pass ran with
  a 6 h window and reported 0 failures because the bad block did not exist
  yet.
- Reading `/status/config` for every `ingestion_time_range_slack` before
  assuming the one under `storage.trace.wal` governed the live-store.

## Lost data

The six removed blocks held the 21:11 Anime Pop voice run (already in
`INVESTIGATION_2026-11-08_anime_pop_radio_hang.md`), five afternoon blueprint
runs, and three synthetic test spans. Nothing else.
