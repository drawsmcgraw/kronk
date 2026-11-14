# Investigation 2026-11-09 — "search tool is failing" push

**Status:** closed (doc started 08:28 EST during the investigation; see bottom).

## Symptom

Operator received an HA mobile-app push (`notify/mobile_app_drew_pixel_11_pro`,
tag `searxng-canary`) titled **"Kronk search canary failed"** on the morning
of 2026-11-09, read as "Kronk's search tool is failing".

## Timeline (EST)

- 07:38:19 `kronk-searxng-canary.timer` fires `kronk-searxng-canary.service`
  (weekly, Mon 07:30 + up to 10 min randomized delay).
- 07:38:22 probe 1 `Last Week Tonight John Oliver latest episode topic`:
  status=200 engines=[duckduckgo,yahoo] hits=5/5. PASS.
- 07:38:26 probe 2 `GS-9 step 5 annual salary federal pay scale`:
  status=200 engines=[duckduckgo,yahoo] hits=4/5. PASS.
- 07:38:26 pin check: `2026.10.4-d48c4b555` vs newest Docker Hub tag
  `2026.11.9-57ff753ad` → behind by 36 d > `MAX_PIN_AGE_DAYS=35`. **FAIL.**
- 07:38:26 alert pushed; unit exits 1; `data/searxng_canary.json` records
  `result: fail`.
- 08:28 investigation starts.

## Hypotheses

1. SearXNG engines benched / search returning junk (the 2026-10-05 pattern).
   **Wrong.** Both canary probes answered with two engines and relevant hits;
   tool_service logs show every `/search` call today returned 200.
2. tool_service or the searxng container down. **Wrong.** Both `Up … (healthy)`.
3. The alert is the pin-age half of the canary, not the search half.
   **Confirmed** by the unit journal and the state file.

## Root cause

Not a search failure. The weekly canary bundles two checks under one push
title, "Kronk search canary failed". On 2026-11-09 the search half passed
(two engines, 5/5 and 4/5 relevant hits) and the pin-age half failed: the
compose pin `searxng/searxng:2026.10.4-d48c4b555` was 36 days behind the
newest Docker Hub tag, over the 35-day limit set on 2026-10-05. The alert
was correct and did its job (the pin lapsed again, exactly what the check
exists for). The title was the bug: it did not say which check failed, so
a stale pin read as a broken tool. Tenet 7 (most specific cause available).

Contributing: the canary runs Monday 07:30; the 35-day limit with a
~monthly cadence means a pin bumped on the 5th trips on the fifth Monday
after. Expected behaviour, just the first time it fired.

## Fix (2026-11-09, 08:28–08:35 EST)

1. **Pin bumped** (runbook `docs/runbooks/searxng-bump.md`, first hand-run
   since the runbook was written): `2026.10.4-d48c4b555` →
   `2026.11.9-57ff753ad` in `docker-compose.yml`; `compose pull searxng &&
   up -d searxng`; healthy in 9 s. Kronk-stack only; HA and MA untouched.
   Settings loaded with no incompatibility warnings. Two new log lines on
   this build, both benign: `searx.botdetection.config: missing config
   file: /etc/searxng/limiter.toml` (limiter is off, default `false`) and
   one `ERROR:searx.botdetection: X-Forwarded-For nor X-Real-IP header is
   set!` per container start (tool_service calls searxng directly, no
   proxy; requests still served).
2. **Canary after the bump: PASS**, three engines
   (`brave,duckduckgo,yahoo`) — brave answered for the first time since
   October on the new build.
3. **Bing re-tested** per runbook step 6, bing-only queries: the 7-word LWT
   query returned the show's YouTube/Wikipedia/episode-guide pages; the
   8-word GS-9 query returned FedPayscale/OPM/GS calculators. No first-word
   junk. **Bing re-enabled** in `searxng/settings.yml` and `.example`
   (edited as uid 0 inside the container; `compose restart searxng`,
   healthy in 9 s). Canary again: PASS, four engines
   (`bing,brave,duckduckgo,yahoo`), 5/5 and 4/5 hits.
4. **Alert title now names the failing check** (`scripts/searxng_canary.sh`,
   new `canary_alert_title`): "Kronk SearXNG pin stale — search OK" /
   "Kronk search FAILING (SearXNG canary)" / both. Test:
   `tests/test_searxng_canary.py` runs the real script with stub `docker`
   and `curl` on PATH and asserts the push title and exit code for
   pin-stale-only, junk-search, both, and all-good.
5. Runbook step 5 corrected to the script's actual thresholds (it said
   "≥2 engines, ≥3/5"; the script has used 1 engine as warning-only and
   ≥2/5 since the 2026-10-05 rule adjustment); step 6 rewritten for bing
   being enabled.

## Verification

- `scripts/searxng_canary.sh` → PASS (08:31 and 08:32); through the unit,
  `systemctl --user start kronk-searxng-canary.service` → PASS,
  `Result=success`, unit no longer `failed` (08:33). State file
  `data/searxng_canary.json`: `result: pass, pin: 2026.11.9-57ff753ad`.
- tool_service `/search` (the canary's probe path, `localhost:8003/search`):
  200, four engines, relevant hits on both fixed queries.
- Shim: `POST http://localhost/api/chat` (nginx → orchestrator), "What was
  the topic of the most recent episode of Last Week Tonight with John
  Oliver? Use web search." → research agent, answer cites the web search,
  11.9 s wall clock, `done_reason: stop`.
- Brave re-benched ("too many requests", 900 s) by 08:33 after the probe
  burst of this investigation; expected, documented in the runbook (step 7).

## What would have caught it sooner

- A push title that names the check. Fixed above; this was the whole
  misread.
- Nothing else needed catching: the canary found the lapsed pin on
  schedule, on the first Monday past the limit.

## Follow-up 2026-11-14: bing disabled again

Review before commit. A single bing-only query, "Last Week Tonight John
Oliver latest episode topic", returned Last.fm, Merriam-Webster "LAST",
Wikipedia "Last" — the 2026-10-05 first-word junk, on the same 2026.11.9
build that passed five days earlier. In the merged results the Last.fm row
sat at #5 behind three-engine agreement (brave, duckduckgo, yahoo); with
duckduckgo CAPTCHA-benched it would not be outranked. Bing set back to
`disabled: true` (settings.yml and `.example` edited as uid 0 in the
container, `compose restart searxng`, healthy). One passing check is not
enough to re-enable an engine whose failure comes and goes; the runbook now
says to re-check days later.

## Open operator decisions
- ROADMAP "SearXNG stays healthy": the runbook has now been run once by
  hand after its creation; the headless monthly bump is gated on a second
  hand-run.
- The 35-day limit versus ~monthly bumps will fire roughly every fifth
  Monday after a bump. Acceptable as a nag; raise `MAX_PIN_AGE_DAYS` to
  42 if the alert is unwanted the week before a planned bump.

**Status:** closed 2026-11-09 — alert explained, pin bumped, alert title
fixed with a test. Bing re-enabled 2026-11-09, disabled again 2026-11-14.
