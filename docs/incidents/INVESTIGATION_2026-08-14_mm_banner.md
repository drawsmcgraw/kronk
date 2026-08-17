# Investigation 2026-08-14 — mirror "update available" banner survives Kronk updates

**Symptom (operator):** Kronk updated the MagicMirror (2026-08-14 06:55,
reported success + spoke the completion announce), but the mirror UI still
shows the banner that "the weather module is out of date."

**Verdict: the update really did succeed — and really did skip the banner's
module, silently, as designed.** The updater's own first run in July dirtied
the module's lockfile, and its never-clobber-local-edits guard has locked
that module out of every update since. No layer names skipped modules, so
every run since July has looked like a full success.

Investigated read-only via the Phase-A `/ops/exec` path (audited; no state
touched on the Pi).

## Evidence trail

1. **The update outcome** (`/data/mm_update_last.json`): `KRONK-OK update
   old=4b4a595 new=4b4a595 version=2.37.0 … mods_ok=5 mods_skipped=5
   mods_failed=0`. Core already current; five modules refreshed; five
   skipped **without names**.
2. **The banner's module is `MMM-RAIN-MAP`** (the animated rain radar —
   "the weather module" colloquially; the *forecast* module
   MMM-OpenWeatherMapForecast is clean and current). Pi journal shows
   `[updatenotification] Update found for module: MMM-RAIN-MAP` **every
   10 minutes**, unbroken across the 06:58 restart. MM 2.37's banner string
   is "Update available for {MODULE_NAME} module."
3. **Why the updater skips it:** `git status` in MMM-RAIN-MAP shows tracked
   local modifications — `package-lock.json | 39 deletions`. mm-update.sh's
   dirty-tree guard ("never clobber operator work") skips any module with
   tracked edits.
4. **Where the dirt came from — the updater itself.** The module's
   `.git/FETCH_HEAD` is dated **2026-07-11 13:19** — the day the mirror
   update feature was built and live-verified. That first run pulled
   RAIN-MAP (local tip `68479bc`, release 3.0.5, 2026-06-15), then ran
   `npm install --omit=dev` inside it, and npm **regenerated the tracked
   lockfile** (39 lines pruned). Every run since classifies that drift as
   operator work and skips — so RAIN-MAP has had no real fetch since
   July 11.
5. **Why the banner still knows about upstream:** `updatenotification`
   checks with `git fetch -n --dry-run` + `rev-list --count` on its own
   schedule — it sees upstream `main` has moved past the frozen local refs
   and re-banners every 10 min. Kronk's updater and the mirror's own
   update detector disagree, and both are right.

## Root cause

A feedback loop between two individually-correct behaviors:

- `npm install` inside a module rewrites its tracked `package-lock.json`
  (different npm version than the module author's).
- The dirty-tree guard treats any tracked modification as operator work
  and skips the module — permanently, since nothing ever un-dirties it.

Plus a reporting gap that hid it: skips are a bare count at every layer
(`mods_skipped=5` in the script output, absent from the spoken announce,
which only voices `mods_ok`/`mods_failed`). A watched, active module
silently pinned since July looked identical to ".bak leftovers were
skipped."

Same failure *shape* as the dead sp5100 watchdog (INCIDENT_2026-08-12) and
the Shield's stale sideload: silent state drift with no canary.

## Fix options (none applied — investigation only, per operator request)

1. ~~**One-time unpin (Pi, manual)**~~ — **applied 2026-08-16** (operator
   authorized): `git checkout -- package-lock.json` in `MMM-RAIN-MAP`,
   run over SSH from tool_service's key; verified clean before/after.
   The banner clears only after the *next* update actually pulls the
   module. Note: until fix 2 lands, that update's `npm install` will
   likely re-dirty the lockfile and re-arm the trap for the following
   upstream release.
2. ~~**Stop creating the dirt**~~ — **applied 2026-08-16**: module dep
   installs use `npm ci --omit=dev` when the module commits a lockfile
   (never rewrites it), `npm install` only when none exists; and the dep
   step now runs only when the pull moved HEAD (or `node_modules` is
   missing), so unchanged modules aren't churned at all.
3. ~~**Name the skips**~~ — **applied 2026-08-16** as `mods_dirty=` in the
   KRONK-OK line (dirty skips named; `.bak`/non-git stay count-only —
   they're permanent expected noise), flowing through to
   `/magicmirror/status` and spoken by name in the announce. Landed with
   tests (speech + parse).
4. Not needed: auto-restoring lockfile drift — fix 2 removes the source.

## Resolution (2026-08-16)

Update run post-fix: `mods_ok=6 mods_skipped=4 mods_failed=0
mods_dirty=MMM-DarkSkyRadar,MMM-RAIN-RADAR`. MMM-RAIN-MAP pulled 3.0.5 →
**3.1.0**, working tree **clean after `npm ci`** (the trap cannot recur),
and `updatenotification` logged **zero** "Update found" lines after the
restart (previously one every 10 minutes). Banner cleared.

The named-skips field earned its keep on its first run: it exposed two
*more* silently-pinned modules (MMM-DarkSkyRadar, MMM-RAIN-RADAR — both
inactive in config, so cosmetic; likely the same npm-drift cause). Clean
them the same way if they're ever re-enabled, or delete them.

## Ops-exec sharp edges found while investigating (Phase-A classifier)

- `git -C <path> status` is refused: the classifier takes the first
  non-dash token as the subcommand and sees the `-C` *value*.
  Workaround used: `git --git-dir=… --work-tree=… status`.
- `journalctl --since today` / `-u <unit>` refused for the same
  value-token reason; all-dashed forms (`-n4000 --no-pager`) pass.
- Quoted pipes inside grep patterns break the pipeline splitter (it splits
  on `|` before shlex).
- `journalctl --user` on the Pi returns "No journal files were found" —
  user-unit logs land in the *system* journal there.

These are candidate small fixes for Phase B; the workarounds were
sufficient today.

Also noted (tenet 10 drift): `kronk-mm-update` is a **general-purpose** key
— the read-only guarantee lives entirely in Kronk's classifier, not in the
key. CLAUDE.md/plan language ("forced-command keys that can do exactly one
thing") predates the Phase-A ops path, which runs arbitrary commands over
this same key. Either re-scope the docs or split keys (forced-command for
the update flow, general for ops) — operator decision.
