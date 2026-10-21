# Music Assistant fork — status and handoff (2026-10-16)

Purpose: prime a fresh session focused only on Kronk's patched Music
Assistant. Snapshot as of 2026-10-16 (evening); verify live state before
acting (commands at the bottom). Background:

- `docs/plans/MA_UPSTREAM_LESSONS.md` — lessons + test/build runbook for
  upstream MA work, and the starting point for "create station".
- `docs/features/pandora-thumbs.md` — the shipped thumbs feature (current).
- `docs/incidents/INVESTIGATION_2026-10-16_ma_like_latency.md` — why thumbs
  up is fire-and-forget.
- `docs/plans/MA_LOCAL_PANDORA_FEATURES_PLAN.md` — the build approach
  (derived image, hash guard, restart recipe) and Feature A design.
- `docs/plans/PANDORA_STATIONS_AND_THUMBS_PLAN.md` — Pandora API research.
- `docs/incidents/INVESTIGATION_2026-09-19_pandora_skip.md` — why we run
  the 2.11 betas at all.

## What changed on 2026-10-16

- Operator opened discussion https://github.com/orgs/music-assistant/discussions/6770
  proposing thumbs. Maintainer OzGav: "We already have favourite and
  dislike." MA #6482 (in 2.11.0b3+) made favorites a per-user tri-state
  (like / dislike / cleared) forwarded to providers via
  `MusicProvider.set_favorite` when they declare `FAVORITE_*_EDIT`.
- The `pandora/feedback` command is retired. The patch now implements
  `set_favorite` for Pandora tracks.
- MA moved from 2.11.0b2 to 2.11.0b5 (needed for dislike). Named restart
  15:50–15:53; DB migrated 58 → 68.

## What is running

- MA container `music-assistant-server` (compose service `music-assistant`,
  stack `kronk-ma`) runs `kronk/music-assistant:2.11.0b5-kronk.7`: stock
  2.11.0b5 plus the patched Pandora package, built by `ma/build.sh` from
  fork commit f7acc5c62 (label `io.kronk.ma.fork_rev = f7acc5c62`), the
  third review round: the SOCKS session is closed only in `unload()` or
  when the first login fails, stationless feedback ids are all kept, and
  the clear and init handlers catch only MA errors and `TimeoutError`.
  Swapped in 2026-10-21 07:35 (named restart; down 07:32–07:35; backup
  `~/backups/ma/ma-config-2.11.0b5-20261021-073214-quiesced.tgz`, 2.34 GB).
  Live-tested on the kitchen speaker through tool_service: Pandora login
  5 s after start, Anime Pop play 4.27 s, thumbs up, thumbs down on the
  same track (Pandora kept the feedbackId and flipped isPositive; MA
  replaced its one entry), thumbs up on the next track, stop. A like took
  55–70 s while MA's metadata scan retried LRCLIB lyrics (30 s per failed
  attempt); the next one landed in 9 s. `kronk.6` (5e65e1bf8, second review
  round plus the pre-review fixes) ran 2026-10-20 08:09 to 2026-10-21
  07:32; backup before it
  `~/backups/ma/ma-config-2.11.0b5-20261020-080644-quiesced.tgz`. `kronk.5` (3425cd3dc, live-tested:
  late like, Kronk like/clear, dislike→like burst, out-of-band delete then
  clear, voice up/down) ran 07:25–08:06; backup before it
  `~/backups/ma/ma-config-2.11.0b5-20261020-072230-quiesced.tgz`.
  `kronk.4` (7822176fd, first review round) ran 2026-10-19 12:43 to
  2026-10-20 07:22. `kronk.3` (9ea2a5b54, without the throttle) ran 12:05–12:43
  for the before/after timings; backup taken before it:
  `~/backups/ma/ma-config-2.11.0b5-20261019-120208-quiesced.tgz`
  (2.33 GB). `kronk.1`/`kronk.2` (adeb99d34) are superseded.
- `ma/Dockerfile` hash-guards all ten files of the stock 2.11.0b5 Pandora
  package (the COPY replaces the whole directory). Pandora on 2.11.0b5 and
  upstream `dev` (f7aec9c38) is byte-identical, so one change serves the
  image and the PR.
- Rollback to b2: `kronk/music-assistant:2.11.0b2-kronk.1` **plus** restoring
  `~/backups/ma/ma-config-2.11.0b2-20261016-155012-quiesced.tgz` (1.99 GB) —
  the b5 DB migration is one-way. Recorded on the compose `image:` line.
- PO-token helper stays at `brainicism/bgutil-ytdlp-pot-provider:2.0.0`;
  b5 ships plugin 2.0.2 (patch-level mismatch warning only).
- New on b5 (seen in logs, not acted on): Sendspin "non-compliant client"
  warnings for both Voice PEs and the Satellite1 (older firmware,
  "connected unencrypted (transition mode)") — playback works; audio
  analysis downloads `beat_this` torch checkpoints to `/data/.cache` on first
  start.

## The fork (upstream PR)

- `~/git-repos/drawsmcgraw/musicassistant-server`, origin
  `git@github.com:drawsmcgraw/musicassistant-server.git`; remotes
  `upstream` (music-assistant/server) and `kronk-clone` (the old clone).
- Branch `pandora-track-feedback` off `dev` at f7aec9c38, one commit
  adeb99d34 (operator-authored):
  - `providers/pandora/__init__.py`: `+ProviderFeature.FAVORITE_TRACKS_EDIT`.
  - `providers/pandora/constants.py`: `ADD_FEEDBACK_ENDPOINT`.
  - `providers/pandora/provider.py`: `set_favorite` — tracks only; None
    ignored (Pandora deletes feedback by an id we don't keep); refuses a
    track not in retained fragments (`MediaNotFoundError`), without a
    `trackToken` or with `allowFeedback: false` (`InvalidDataError`); POSTs
    `station/addFeedback {trackToken, isPositive}`.
  - `tests/providers/pandora/test_favorite.py`: 9 tests.
- Verified in a throwaway container (`ma-pr-test`, since removed; stock b2 image with a
  fresh `/venv` of dev's requirements minus torch and the two git deps):
  Pandora suite 94 passed; `pre-commit run --all-files` all hooks pass
  (incl. mypy, `check_method_order`); full suite 21,434 passed, with the
  same sendspin/sonic_analysis/benchmark failures as clean `dev` in that
  environment.
- Drafts (session scratchpad, copy before it is cleaned): commit message
  and PR body. Upstream's `scripts.check_pr_metadata` passed the body. The
  upstream AI policy requires the PR description and review answers in the
  operator's own words.
- **PR #7068 review follow-up (2026-10-19, committed and pushed by the
  operator, one commit per finding).** Copilot raised three findings (two
  [CRITICAL], which keep the PR a draft via the critical-issue gate) and
  OzGav one docstring comment. All four were right:
  - 728b40a69 *Docstring* — implementation details moved from the
    `set_favorite` docstring to inline comments (upstream rule:
    docstrings are caller-facing).
  - 269497af3 *Rate a track on the station that played it* —
    `PandoraFragment.served` is now `dict[id, resolved_at]`;
    `_find_rated_track` prefers the copy most recently served, falling
    back to the freshest fetch.
  - 9ea2a5b54 *Remove Pandora feedback on clear* — `addFeedback`'s
    `feedbackId` is kept per track and station in MA's persistent cache
    (`CACHE_CATEGORY_FEEDBACK`); clearing calls `station/deleteFeedback
    {feedbackId, isPositive}`. That body was verified live on 2026-10-19
    (add then delete on Anime Pop, confirmed via getStationFeedback); a
    second thumb on the same track and station keeps the same
    `feedbackId` and flips `isPositive`.
  - 7822176fd *Throttle every Pandora API request* — per-instance
    `ThrottlerManager(4/s)` acquired in `_api_request`, around the login,
    and around the CSRF fetch before it. Four, not three: a 401 recovery
    (request, CSRF, login, retry) and a station start with stream
    takeover are four requests each and must not wait. Deliberately not
    `@throttle_with_retries` (it retries on
    `ResourceTemporarilyUnavailable`, which Pandora codes 9/10 also map
    to, and writes must not repeat).
  Gate per commit: `pre-commit run --all-files` and the Pandora suite in
  the `ma-pr-test` rig (97 / 105 / 111 tests). The station-upsert branch
  should rebase onto these rather than add its own throttle, and use
  `acquire()`, not the retry decorator, for `createStation`.
- **Live test 2026-10-19** (office Satellite1, Anime Pop; every result
  checked against Pandora's getStationFeedback; station left at its
  20 up / 19 down baseline). On `kronk.3` (no throttle) and `kronk.4`
  (throttle): Kronk thumbs up, clear by `pandora://` URI, thumbs down with
  verified skip, clear by `library://` URI — all pass on both. Voice
  (`assist_pipeline/run` as the office device) thumbs up 4.39 s, thumbs
  down 5.16 s, on `kronk.4`. Station start: 1.32 / ~1 / 0.25 s without
  the throttle, 1.11 / 0.41 / 0.62 s with it — no measurable cost. Likes
  landed after 61 s (kronk.3) and 139 s / 31 s (kronk.4): MA's metadata
  scan waiting on Wikipedia and LRCLIB retries, not Pandora (the Pandora
  feedback timestamp follows the last LRCLIB failure by ~55 ms).
- **Pandora bot protection.** A hand-built login from a test script was
  refused with HTTP 403, error 1215 `s2s_high_score` (PerimeterX captcha).
  The same login using the provider's own `create_auth_headers` and MA's
  `json_dumps` serializer succeeded. Scripts that talk to Pandora must
  reuse the provider's helpers; repeated blocked attempts risk raising
  the score for the IP MA logs in from.
- **Found, not fixed (Kronk side):** when a station plays a track that is
  in MA's library (anything liked through MA), HA reports
  `library://track/N`, and tool_service `/music/rate` refuses it as "not
  a Pandora station". Seen with `library://track/15356` (Pandora
  `TR:213938455`).
- The old clone `~/git-repos/music-assistant/server` (branch
  `kronk/pandora-2.11.0b2`, commit c814b2490 `pandora/feedback`) is history
  only; the running image no longer uses it.

## Kronk side

- `tool_service/main.py` `POST /music/rate`: reads `media_content_id`
  (`pandora://track/<id>`) from HA, then:
  - up → `like_in_background()` detached (`_spawn`, strong refs in
    `_BACKGROUND_TASKS`), `music/favorites/set_item(item, True)` with
    `_timeout=MA_LIKE_TIMEOUT_S` (180 s); on failure `notify_operator()` →
    `HA_NOTIFY_SERVICE` (compose env; `notify/mobile_app_drew_pixel_11_pro`).
    Reply "Thumbs up for X by Y." immediately (`confirmed: false` in JSON).
  - down → awaited `set_item(item, False)`, then `media_next_track` and
    verify the title changed. Reply "Thumbs down."
- `ma_command(command, _timeout=20, **args)`: `_timeout` bounds only the
  wait for the command's reply.
- Tests: `tests/test_music_rate.py`. Full suite 635 passed, 2 skipped.
- Live-verified 2026-10-16: direct thumbs up/down; "thumbs up" through
  `assist_pipeline/run` in 4.1 s with the background like landing; a
  deliberate failure produced the phone push. Operator "Okay Nabu" test
  passed 2026-10-16 (thumbs up and down).

## Open items, roughly in priority order

1. Operator: answer the three Copilot threads and OzGav's comment on
   PR #7068 (own words, per upstream AI policy), then mark it ready for
   review. Remove the `ma-pr-test` rig container once the PR merges.
2. Station creation (Feature A), new seam: copy the iHeartRadio provider's
   lazy pattern — `search` uses `fullSearch` and returns synthetic dynamic
   `Radio` items (`seed:<pandoraId>`, "X Radio"); `get_radio` resolves them;
   the first `get_dynamic_radio_tracks` calls `station/createStation` and
   maps to the real station id; optionally `LIBRARY_RADIOS_EDIT` +
   `library_add`/`library_remove` = create/delete. No core or models change
   (refs: `iheartradio/stations.py:112, 274-290`, `parsers.py:91-122`).
   Pandora error 1005 "Station limit reached" already exists in
   `constants.py`. Open its own discussion first.
3. Upstream ask: run the post-like metadata scan in the background
   (`controller.py:1627` awaits `update_metadata` inside
   `add_item_to_library`), so likes return in ~1 s.
4. HA fast tier for thumbs (blueprint + `rest_command`; needs an HA
   restart — batch with ROADMAP items 22/23).
5. Least-privilege MA token (non-admin user; confirm `set_item` still
   reaches the home-shared Pandora source).

## Rules that apply to this work

- MA restarts are their own named step, announced before execution, never a
  side effect; check player states first and never replace or pause music
  the operator started.
- Pin everything: image tags are explicit; `docker-compose.ma.yml` has no
  `build:` block. Bumps are deliberate.
- Never `docker compose down -v` on `kronk-ma`.
- Restart recipe:
  ```bash
  docker compose -f docker-compose.ma.yml stop music-assistant
  docker run --rm -v kronk-ma_ma-config:/data:ro -v ~/backups/ma:/backup \
    --entrypoint tar ghcr.io/music-assistant/server:2.11.0b5 \
    czf /backup/ma-config-2.11.0b5-$(date +%Y%m%d-%H%M%S)-quiesced.tgz -C /data .
  # edit the image tag, then:
  docker compose -f docker-compose.ma.yml up -d music-assistant
  ```
  After a restart: Pandora's first fragment may fail (stream takeover from
  the old session; seen as HTTP 400 on 2026-10-16, next play worked), and
  YouTube Music should be checked (the daily canary at 07:00 also does).
- Operator runs `git commit` / `git push`. Run `./scripts/run_tests.sh`
  before suggesting a commit.

## Verify live state first

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep music
docker image inspect kronk/music-assistant:2.11.0b5-kronk.2 \
  --format '{{index .Config.Labels "io.kronk.ma.fork_rev"}}'
git -C ~/git-repos/drawsmcgraw/musicassistant-server status -sb
docker logs --since 1h kronk-tool_service-1 2>&1 | grep -E 'thumbs up (landed|failed)'
```
