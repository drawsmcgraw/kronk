# M32 step 1, fix round 1

Fix agent, 2026-11-26. The three jobs of the ruling at the end of
`M32-criteria.md`, nothing else. No unit installed, started or stopped;
production not touched; no deploy, no tag.

## S1: the export service's start limit

- `systemd/kronk-work-export.service`: `StartLimitIntervalSec=0` in
  `[Unit]`, and a comment paragraph in the header: systemd's default (five
  starts in ten seconds) failed the service with `start-limit-hit` on a
  burst of dump writes, and the path unit with it, for good; the service
  is a oneshot, so runs still never overlap.
- `systemd/kronk-work-export.path`: one sentence in its comment that a
  burst cannot fail it, and why (the service has no start limit).
- `docs/features/work-page.md`, "The exporter runs on a write": the same.
- Test: `tests/test_m32_e2e.py::test_a_burst_of_writes_cannot_leave_the_path_unit_failed`
  lost its strict `xfail` and passes; its docstring says the fix is in.
- `systemd-analyze --user verify kronk-work-export.service
  kronk-work-export.path` on scratch copies of both: no output, exit 0.

## S2: one encoding of the outline per change of the file

- `orchestrator/work_api.py`: `_dumps` renders JSON as starlette's
  `JSONResponse` does (`ensure_ascii=False, allow_nan=False`, compact
  separators). `_work_bytes` keeps the outline's encoding in the route's
  `_cache` beside the parse, as `(key, parse, bytes)`, and reuses it while
  the cache key is the same and the parse is the same object;
  `_cache["encodings"]` counts. `work_body` encodes the envelope fresh and
  splices the kept bytes in as `work`, keys in `work_source`'s order. The
  route returns those bytes as `application/json` with `Cache-Control:
  no-store`. `work_source` is unchanged; the unchanged answer (`work:
  null`) encodes no outline.
- Module doc and `docs/features/work-page.md` ("The in-memory copy") say so.
- Tests in `tests/test_m32_live.py`:
  - `test_one_encoding_of_the_outline_per_change_however_many_full_answers`:
    twenty full `GET /api/work` of one file, one encoding (by a spy on
    `_dumps` and by the counter); the file renamed into place, twenty
    more, one more; a `?have=` answer adds none.
  - `test_the_answers_bytes_parse_to_what_they_did` (no `have`, a
    matching `have`, another `have`): `work_body` is byte-for-byte what
    `JSONResponse(jsonable_encoder(work_source(...)))` rendered, first
    call and kept; the route's content type, `no-store` and key order.

## Note 7: "Heard"'s "as of" in the house's zone

- `orchestrator/static/work.html`: `clockOf` now reads
  `const clockOf = sec => (sec == null ? '?' : fmtClockS(sec));` in place
  of a `toLocaleTimeString` in the browser's zone. `fmtClockS` is the
  page's existing helper (through `houseString`), same hour, minute and
  second fields.
- Test: `tests/test_m32_live.py::test_shown_times_are_in_the_house_zone`
  now pins that line and that no `toLocaleTimeString` is left in the code.
- `docs/features/work-page.md`, "Shown times are the house's": updated.
- The page's bytes changed, so its version did: dev `c86f8fcebf43` before,
  `7a2332b2f04f` after.

## Dev check

`docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`,
then:

- `/dev/work`: 200, stamped `kronk-page-version` `7a2332b2f04f`, the new
  `clockOf` line in the served page.
- `/dev/api/work`: 200, `Content-Type: application/json`,
  `cache-control: no-store`, keys `source, schema_known, work, unchanged,
  page_version, server_s`, state fresh, `unchanged` false, 127,892 bytes.
- `/dev/api/work?have=1795695212.2299902` (its ts): 200, `unchanged`
  true, `work` null, 234 bytes.

## The suite

```
===== 8477 passed, 3 skipped, 61 xfailed, 6 warnings in 223.32s (0:03:43) ======
```

Was `8472 passed, 3 skipped, 62 xfailed`: the S1 test moved from xfailed
to passed, and four new tests (one, and one parametrized three ways).
