# Music Assistant upstream work — lessons and runbook (2026-10-16)

Input for the next session: **Pandora "create station"**, built the same
way as thumbs — run it locally on Kronk's derived MA image for a while,
then fork → apply → PR. Written after the thumbs feature went from a
custom API command (2026-10-06) to an upstream-shaped `set_favorite`
implementation (PR from `drawsmcgraw/musicassistant-server`, commit
adeb99d34). Current fork/image state: `docs/plans/MA_FORK_STATUS.md`.

## Lessons

### 1. Find the existing seam before designing one

Thumbs was first built as a provider-registered command
(`pandora/feedback`) because the 2026-10-04 research concluded "no seam
exists." By the time we filed, upstream had shipped per-user like/dislike
(#6482, 2026-09-27) with a provider hook (`set_favorite` +
`FAVORITE_*_EDIT`). A maintainer's one-line reply ("We already have
favourite and dislike") turned a new API surface into a 30-line override.

- Search current `dev`, not the tag we run, for the concept by every name
  (favorite/like/dislike/rating; station/radio/create/seed). Read
  `models/music_provider.py` (provider hooks) and the `ProviderFeature`
  enum in `music_assistant_models/enums.py` first.
- Check how 2–3 other providers solve the same problem. Thumbs: YouTube
  Music's `set_favorite` was the exact precedent. Station creation:
  iHeartRadio (below).
- Plan docs age fast against a project that merges daily. Re-verify any
  "X doesn't exist" claim against `origin/dev` before building.

### 2. Ask upstream early, in a Discussion, and keep it small

- A merged PR's comment thread is a dead venue. Use a GitHub Discussion
  (that is where #6770 got a maintainer answer within hours).
- Pitch the feature and ask "is there an existing seam you'd want this on?"
  before writing code that needs to land upstream. One sentence from a
  maintainer saved a review cycle.
- Prefer seams that need no core/models change. A provider-only PR is a
  much easier review than a new hook plus a models-repo companion PR.

### 3. Don't overstate complexity

I called the original thumbs PR "complicated" by conflating it with a
hypothetical generic hook. The provider-registered-command pattern was
already used by 8+ upstream providers (`hass`, `party`, `music_quiz`,
`ai_radio`, ...). Check the codebase for precedent before calling
something risky.

### 4. Upstream's gate is pre-commit, not just pytest + ruff

Our b2-era commit passed pytest and ruff but failed mypy twice (an
undeclared `_unregister_feedback` attribute; indexing an Optional in a
test). Run the full `pre-commit run --all-files`; it also enforces
upstream-specific rules:

- `check_method_order`: public methods above private ones in a class.
- `check_blocking_io`, `check_manifests`, `check_translatable_labels`,
  `check_test_layout`, codespell, mypy (strict).
- Declare instance attributes on the class with types (see `hass`'s
  `_unregister_search_command: Callable[[], None] | None = None`).

### 5. Test-environment recipe (works; reuse it)

Upstream `dev` needs Python ≥ 3.14 (host has 3.13). The 2026-10-16
container `ma-pr-test` (removed after the PR) was a stock MA image with the
fork bind-mounted read-only at `/src` and a separate venv at `/venv`.
Recreate it:

```bash
docker run -d --name ma-pr-test --entrypoint sleep \
  -v ~/git-repos/drawsmcgraw/musicassistant-server:/src:ro \
  ghcr.io/music-assistant/server:2.11.0b5 infinity
docker exec -u 0 ma-pr-test sh -c '
  apt-get update -qq && apt-get install -y -qq git >/dev/null
  /app/venv/bin/uv venv -q --python /app/venv/bin/python /venv
  grep -v -i -E "git\+|extra-index-url|^torch" /src/requirements_all.txt > /tmp/req.txt
  /app/venv/bin/uv pip install -q --python /venv/bin/python -r /tmp/req.txt \
    pytest==9.1.1 pytest-aiohttp==1.1.1 pytest-timeout==2.4.0 pytest-xdist==3.8.0 \
    syrupy==6.1.1 ruff==0.16.5 mypy==2.3.0 pre-commit==4.6.2 pre-commit-hooks==6.0.0 codespell==2.4.3
  git config --global --add safe.directory "*"'
```

(Pin versions from the fork's `pyproject.toml` `[project.optional-dependencies] test`
at the time; they move.)

Gotchas, each cost a retry:

- `requirements_all.txt` carries `--extra-index-url` for PyTorch; uv then
  refuses PyPI-only pins (certifi). Drop that line and torch.
- Two git-sourced deps (`aiolibdatachannel`, `yappi`) — skip them; only
  remote_access and profiler tests need them.
- Run pre-commit on a **writable copy** with uv pointed at the venv:
  `cp -a /src /work && cd /work && git add -A && PATH=/app/venv/bin:/venv/bin:$PATH
  UV_PROJECT_ENVIRONMENT=/venv UV_NO_SYNC=1 RUFF_CACHE_DIR=/tmp/ruff pre-commit run --all-files`.
  (`git add -A` in the copy so new files are seen.)
- Use `/venv/bin/python -m pytest` explicitly — `/app/venv/bin` first on
  PATH has no pytest.
- Full suite: `-n 12` (xdist) takes ~95 s instead of >10 min; ignore
  `tests/providers/profiler/test_profiler.py`,
  `tests/providers/sonic_analysis/test_clap_wrapper_warnings.py`,
  `tests/test_remote_access.py`. Expect sendspin / sonic_analysis /
  benchmark failures in this environment.
- **Baseline any failure on clean `dev`** in a second copy. Git refuses
  `checkout` in a copied repo without `safe.directory`; the first baseline
  silently still contained our change. Confirm with `git status` /
  a grep for the new symbol before trusting a baseline.
- `python -m scripts.check_pr_metadata --title "<title>" < body.md` checks
  the PR title (no `feat:` prefixes; release notes use the PR title) and
  that the body keeps the template with exactly one type box ticked and the
  four required checklist items.

### 6. Upstream contribution rules

- Integration branch is `dev` (no `main`).
- PR template must be kept; tick exactly one "Types of changes" box.
- AI policy (https://github.com/music-assistant/.github/blob/main/AI_POLICY.md):
  no autonomous contributions; the PR description and every answer to a
  maintainer must be the operator's own words; the operator must be able
  to explain every change. Claude drafts, the operator rewrites and posts.
- The operator commits and pushes; Claude never does.

### 7. Image build and swap

- One change can serve both the PR (on `dev`) and the image (on a beta
  tag) only if the provider package is identical between them — check
  `git diff --stat <tag> origin/dev -- music_assistant/providers/pandora`
  first. It was identical for b5 vs dev on 2026-10-16.
- `ma/Dockerfile`'s hash guard must cover **every file** in the provider
  package (the `COPY` replaces the directory), and every guard hash must
  match both the stock image and git.
- `ma/build.sh` refuses a dirty checkout. If building before the operator
  commits, build with `docker build` directly and put
  `<rev>+uncommitted-<name>` in the `io.kronk.ma.fork_rev` label; rebuild
  with `build.sh` after the commit.
- New MA features may require a base bump (dislike needed ≥ b3). A bump
  migrates the DB one way (b2→b5: 58 → 68) — rollback is old image **plus**
  the tarball. Record both on the compose `image:` line.
- Before a restart: check every `media_player` state; a "playing" Shield is
  usually its YouTube app (`app_name`), not MA.
- After a restart: Pandora's first fragment may fail (HTTP 400 after a
  stream takeover from the old session); the next play works. Check that
  YouTube Music loaded.

### 8. Verify the effect, and test the slow path

- A 200 from MA proves little. For thumbs we had to confirm that MA's
  write filter (`_write_target_mappings`) actually reaches the Pandora
  source (owner none, sharing everyone; token user `drew`, admin), and
  that the provider awaits Pandora and raises on error.
- MA core does surprising work inline: a like on a non-library track
  awaits `add_item_to_library` → a full metadata scan (24.6–31.1 s).
  The first live test used a track already in the library and took the
  fast path. **Test with fresh items, and time every MA call** that a
  voice reply waits on. Station creation will hit library adds too
  (Radio items, metadata).
- Some MA paths run the provider call in a background task (dislike);
  errors there only reach MA's log.

### 9. Fire-and-forget with an operator back-channel

When MA is slow but reliable, reply to the household at once and run the
call in a detached task (`_spawn`, strong refs) with a long timeout; on
failure push the operator's phone (`notify_operator` →
`HA_NOTIFY_SERVICE`, per-feature `tag`). Test the failure path live once
(bogus URI) and confirm the push arrives. Reusable for "create station" if
creation is slow.

### 10. Live tests touch the real account

Every thumbs test wrote real feedback to the Kronk Pandora account. Station
creation is more persistent: created stations stay, and Pandora has a
station limit (error 1005 "Station limit reached"). Plan the test seeds
and the cleanup (delete test stations in the Pandora app, or implement
delete) before the first live call, and get the operator's OK for any
probe against the account.

### 11. Communication with the operator

- `git cherry-pick -n` stages the commit; later edits sit unstaged. Say so
  up front — the operator asked why there were unstaged changes.
- List every file in the change, including untracked new tests; the
  operator assumed two files.
- Explain the hook chain with file:line references (feature flag →
  controller gate → base-class override); that is what the operator must
  be able to explain to maintainers.

## Starting point for "create station"

What's known (2026-10-16, upstream `dev` f7aec9c38 / MA 2.11.0b5):

- **No create-station seam exists upstream**, generic or Pandora. The only
  create hook is `create_playlist(name, media_types) -> Playlist`
  (`PLAYLIST_CREATE*`), which takes no seed and returns a Playlist; Pandora
  stations are `Radio` items. Poor fit (chrisuthe's stalled branch used it).
- **Precedent: iHeartRadio** creates real provider-side artist stations
  with existing hooks only:
  - `search` returns synthetic dynamic `Radio` items (`artist:<id>`,
    "X Radio") — `iheartradio/provider.py:316-325`, `parsers.py:91-122`.
  - `get_radio` resolves them (`provider.py:143-146`).
  - First `get_dynamic_radio_tracks` creates the station if missing
    (`stations.py:112`, `_create_station` at `274-290`).
  - "Add to library" = `library_add` under `LIBRARY_RADIOS_EDIT`
    (`library.py:60-62, 90-110`).
- **Pandora provider today**: features `BROWSE`, `SEARCH`, `LIBRARY_RADIOS`;
  `search` only substring-matches the account's own station names
  (comment: the catalogue search needs endpoints the provider doesn't
  speak); endpoints login / getStations / getFragment / playbackResumed /
  addFeedback (ours). Error 1005 "Station limit reached" is already mapped.
- **Pandora API shapes** (from chrisuthe's `feat/pandora-station-management`,
  commit 1cac2b18, verified on Premium): `POST /api/v1/search/fullSearch
  {query, count: 5}` → first result whose `pandoraId` prefix is
  AR/CO/GE/TR → `POST /api/v1/station/createStation {pandoraId,
  stationName}`; Pandora names the station after the seed ("Radiohead
  Radio"). Needs one read-only `fullSearch` probe on the Kronk account
  first (operator OK).
- **Proposed shape** (copy iHeart; provider-only, no core/models change):
  `fullSearch` in `search` → `seed:<pandoraId>` dynamic Radio items;
  `get_radio` resolves `seed:`; first play calls `createStation` (or reuses
  an existing station for that seed) and maps to the real station id;
  optionally `LIBRARY_RADIOS_EDIT` + `library_add`/`library_remove` =
  create/delete.
- **Kronk side**: `/music` radio requests → existing Pandora station, else
  the seed radio from MA search (name match or refuse), play, verify.
  Relates to ROADMAP item 23 (YouTube Music artist-radio fallback) — decide
  the order: Pandora create first, YT fallback second?
- **Open questions for the operator**: name-match strictness (refuse vs.
  closest seed); whether search results should show seed radios to every
  MA client (UI side effect); station-limit handling; test-station cleanup;
  whether to open the upstream Discussion *before* building (lesson 2
  says yes — ask "would you take this via the iHeart pattern?").
- **Docs lifecycle**: write the plan doc in `docs/plans/` first (update
  `MA_LOCAL_PANDORA_FEATURES_PLAN.md` Feature A or a new
  `PANDORA_CREATE_STATION_PLAN.md`), then build, then
  `docs/features/` and ROADMAP.
