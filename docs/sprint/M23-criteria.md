# M23 pass criteria: the queue incident (fixes 1, 2 and 3)

Source: `docs/incidents/INVESTIGATION_2026-11-21_cracker_radio.md`,
"Proposed fixes" 1 to 3; Drew's order of 2026-11-21. Fixes 4 and 5 are out
of scope. No new routing rule (the stopping rule).

Drew's two conditions:

- **Fix 3 is required.** Kronk's own picks must not teach the taste profile
  that the household likes what Kronk picked: "we're creating incorrect
  bias".
- **"Play some music" must keep working as it does today.** Saying just
  that, and getting a pick from the household's taste, is a feature. Fix 2
  must not touch it. If fix 2 cannot be built without risk to it, fix 2 is
  left out and the report says why.

## Scope

1. **Every play states what happens to the queue.** tool_service's named
   play and its picks send `enqueue: replace`; the blueprint's `play_media`
   does too (fork change 15). "Play X" means X, not X and then whatever was
   queued. A request that is meant to add to the queue (if any path has
   one today) keeps its meaning.
2. **A stripped request is not a generic request.** When the query that
   reaches `/music` holds no terms (a bare "music") but the sentence the
   user spoke holds more than a request verb and generic words, Kronk does
   not pick from the profile. It answers that it did not catch what to play
   and names the closest match if the resolver has one. When the spoken
   sentence itself is generic ("play some music", "play music", "put on
   some tunes", "play something", with or without a room), the pick from
   the profile happens exactly as today. When the spoken sentence is not
   available to `/music`, today's behaviour stays.
3. **Kronk's own picks do not feed the profile as household taste.** Plays
   that Kronk chose (genre picks, the no-phrase pick, picks by the taste
   resolver) are left out of, or discounted in, what the nightly taste
   learning reads from Music Assistant's play history; plays the household
   asked for by name still count, and so does feedback (thumbs, skips,
   "play it again"). The builder shows how a pick is told from a named
   play in the history and what happens to the picks already counted (the
   Cracker picks of 2026-11-20 and 2026-11-21: ids 18 and 19).

## Criteria

1. A test pins the service data of every play path (named, pick, blueprint
   YAML) to `enqueue: replace`, and fails on the old code. Shown once on
   the dev path in dry run that the data would carry it.
2. Fix 2: tests with both captures from the incident as refusals, and a
   table of at least twenty generic sentences (the household's own forms
   from the corpus first) that still pick from the profile; the existing
   corpus, pin and shadow tests pass unchanged except rows the fix is meant
   to change, each named in the report. The refusal is short, true, and
   names the closest match only when the resolver found one.
3. Fix 3: tests that a Kronk pick does not raise its artist's weight and a
   named play does; the profile recomputed in scratch from a copy of the
   real taste data, before and after, with the top ten artists and weights
   in the report (artist names are household data already in the incident
   document; nothing else from the history is quoted). The real taste DB
   and taste file are NOT rewritten by the build: the report gives the
   coordinator the exact step that applies it, and its undo.
4. No request changes target or item except those the fixes are meant to
   change; each such change is listed.
5. Nothing is installed: no blueprint copy, no reload, no deploy; install
   steps for the coordinator in the report.
6. Docs: `docs/features/voice-music-control.md` (the enqueue rule; the
   "Don't stop the music" line the incident found stale),
   `docs/features/music-taste.md` (what feeds the profile), the incident
   document (dated lines), the corpus row from the incident with `xfail`
   as needed.
7. `./scripts/run_tests.sh` passes (before: `5738 passed, 3 skipped, 53
   xfailed`).
8. Production containers, units and timers unchanged; HA and MA not
   restarted, reloaded or written to; no sound; no notification; nothing
   playing is interrupted.

Reports: `docs/sprint/M23-build.md`, `docs/sprint/M23-e2e.md`.
