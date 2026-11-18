# M21 step 2 pass criteria: the `/settings` page

Written by the sprint coordinator on 2026-11-18 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M21. One
place to see and edit prompts and adjustments", step 2; the M21 line of
"Verified on Kronk, 2026-11-15"; Drew's request of 2026-11-11), the step 1
rulings (`docs/sprint/M21-step1-criteria.md`, "Coordinator rulings") and the
step 1 promotion note. The tester checks against this file, not against the
build report.

## What Drew asked for

One place, as a human, to see every prompt and tuning value in the system
and to edit them. Viewing needs nothing. Editing needs a passphrase.

## Design decisions made by the coordinator (Drew may overrule)

1. **Where the live files are.** Production today reads the `config/` of
   its release directory, a copy made at deploy, not a git checkout; an
   edit there would be lost at the next deploy and cannot be committed.
   The repo's `config/` in the working tree is what agents and tests edit,
   so production must not read that either. So each instance gets a live
   config directory outside the repo, its own small git repository with
   local commits only: production's and dev's, side by side next to the
   releases directory. The repo's `config/` stays as the shipped defaults
   and the seed for a new live directory. A deploy never overwrites a live
   file; it adds files and keys the new code needs only through the
   loaders' defaults, and refuses to deploy when the live directory does
   not validate against the new code's schema.
2. **No passphrase is set by the sprint.** `SETTINGS_PASSPHRASE_HASH` is
   absent from `.env` until Drew sets it with a helper that prompts for
   it. Without it the page is read-only and says how to turn editing on.
   Nobody but Drew chooses or sees the passphrase.
3. **Each instance edits its own files.** `/settings` edits production's
   live directory; `/dev/settings` edits dev's. "Apply to dev first" is
   done from production's page by sending the candidate to the dev
   instance, and "bring over from dev" by fetching a file from it.

## Context

- Step 1 left: a reload during a running request can mix old and new
  values within that request; files must be written atomically; the
  `tests/test_m21_pins.py` file pins fail by design at the first real
  edit and must be retargeted; `/config/reload` is refused through nginx.
- The web UI and this page are open on the LAN over plain HTTP by Drew's
  choice (M6). The passphrase crosses the network unencrypted; the plan
  accepts that.
- `config/` now holds `prompts/*.md`, `tuning.yaml`, `music.yaml`,
  `lead_in.yaml`, `voice.yaml`. `tool_service` reads `music.yaml` (and
  what else its loader takes); the orchestrator reads the rest.
- Structural guards do not live in prompts: the mirror pin (M4b), the
  devops agent's tool set (M4), the fetch block (M5), the exec classifier
  (M3) and dry-run are code. An edit on this page must not be able to
  loosen any of them.

## Criteria

### A. Viewing (no passphrase)

1. `/settings` in the web UI (and `/dev/settings` for dev) lists every
   file of the live directory with a one-line description, and for every
   key: the value in effect, where it came from (the live file, an
   environment variable, or the code default), the comment that documents
   it, and when and in which commit it last changed. Prompts are shown
   whole.
2. It also shows, read-only and marked as not editable here: the routing
   rules in order (name, what they match in words or the pattern, where
   they send the request), the environment-derived settings that shape
   behaviour (`VOICE_STREAM`, `MODEL_LOCK`, `MUSIC_SHADOW`,
   `MUSIC_DRY_RUN`, `ERROR_STYLE`, `TELEMETRY_RECORD_TEXT`, model
   assignments, and the rest found by search), tool descriptions and the
   other model-facing text that step 1 left in code, and the sampling,
   context and reasoning flags of the llama units as read from the running
   servers. For each it says where to change it. No secret is shown: no
   token, no hash, no key path contents (a test feeds secrets into the
   environment and finds none in any response).
3. The page states which instance it is (production or dev), which
   release is running, whether editing is enabled, and any config error
   in effect (file and line), as `/services` does.
4. Read endpoints change nothing and need no cookie. Their output is
   escaped: a prompt or value containing HTML or script is shown as text.

### B. Editing

5. Editing is off unless `SETTINGS_PASSPHRASE_HASH` is set. The hash is a
   salted, slow password hash from the standard library (the report names
   it and its cost), compared in constant time. A helper script prompts
   for the passphrase twice without echo and writes the entry to `.env`
   (replacing an old one), never printing or logging the passphrase. The
   sprint does not run it with a real passphrase.
6. Unlocking gives a session cookie: random, server-side, `HttpOnly`,
   `SameSite=Strict`, with an idle timeout and an absolute lifetime stated
   in the report; a lock button ends it; a restart ends all sessions.
   Wrong attempts are rate-limited per client address and overall, with a
   lockout that a test shows, and are logged without the attempted text.
   Every state-changing request needs the cookie and a second token the
   page holds (so another site's form cannot post an edit), and is refused
   with the wrong `Origin`. The passphrase is never written to a log, a
   trace or the audit record, with `TELEMETRY_RECORD_TEXT` on or off.
7. A save: validates the candidate with the same schema the loader uses
   (and the M21 materiality rule: an invalid file never takes effect, the
   error names file and line and is shown in the editor); writes it
   atomically (a reader never sees a partial file: temp file in the same
   directory, flush, rename); reloads both services; confirms the value in
   effect is the one saved; and makes one local git commit in the live
   directory whose message names the file and the keys changed and who
   (the instance, the client address). No push. If the reload or the
   commit fails, the page says so and the file on disk, the values in
   effect and the git state are left consistent with each other (the
   report defines which wins, and tests show each failure).
8. Two editors at once: a save based on a version that is no longer
   current is refused with the difference shown, not silently merged or
   overwritten.
9. Reload isolation (step 1 ruling): a request in flight keeps the values
   it started with; a reload never changes them part-way. A test runs a
   request across a reload and compares every config-derived value it used.
10. What can be edited is exactly the files of the live directory through
    their schemas. The editor cannot create, rename or delete files outside
    that set, cannot write outside the directory (path traversal, symlinks,
    absolute paths: tests), cannot change an environment setting, a
    routing rule or code, and cannot set a value the schema's bounds
    refuse. Size limits are stated and enforced.
11. Structural guards hold whatever a prompt says. With every prompt
    replaced by text that tells the model to ignore its limits, on dev:
    the mirror update still needs the pinned sentence, the devops agent is
    still offered no web tools, fetch still refuses private addresses,
    dry-run still holds, read-only exec still classifies. The report shows
    the run.

### C. Guardrails before a save reaches the household

12. A save to the music policy or the lead-in file first runs the voice
    corpus's offline layer (routing, parser and decision, as
    `tests/test_voice_corpus.py` does) against the candidate, and shows
    which rows changed outcome against the values in effect. Rows that
    newly fail block the save unless the editor confirms each by an
    explicit override that is recorded in the commit message. The run
    reaches no network and plays nothing. The report says how the corpus
    and the checking code reach the running service and how they are kept
    equal to the repo's.
13. A prompt change offers "apply to dev first": the candidate goes to the
    dev instance only, production is unchanged, and the page shows that
    dev now differs from production for that file, with "bring over from
    dev" to take it. Both directions go through the same validation and
    commit path on the receiving side and need the unlocked session.
14. A change to a prompt shows a plain-language note before saving: the
    edit reaches the household at once, and the next request re-reads the
    whole prompt (one slower request).

### D. History and revert

15. Per file: the list of commits from the live directory's git log
    (date, message, who), the diff of any one, and a revert of any one
    that goes through criterion 7's path (validate, write, reload, verify)
    and is itself a new commit. A revert that no longer validates against
    the running code is refused with the reason.
16. Git from the service: the report states how the commit is made (git
    in the image, or a host-side helper) and why; the live directory is
    owned by the host user after any number of saves (uid and mode
    checked); a corrupt or missing git repository disables editing with a
    clear message and never blocks viewing or loading the config.

### E. Deploys and the live directory

17. `scripts/deploy_tag.sh`: production's and dev's live directories are
    created from the repo's `config/` when absent (a first commit records
    the seed and the tag), linked or mounted into the release, never
    overwritten afterwards. Before a deploy the script validates the live
    directory against the new tag's schema and refuses to deploy when it
    fails, naming file and line. It shows which shipped defaults differ
    from the live files, for information. `plan` shows all of this and
    changes nothing. Rollback to an older tag works with a newer live
    directory, or the script says exactly why not and what to do.
18. A migration runbook for the coordinator, in the build report, each
    step with its check and its undo: seed production's live directory
    from what production is running now (the current release's `config/`,
    byte for byte), switch the mount, confirm the values in effect did not
    change (the `/api/config` digests before and after), and the same for
    dev. No value in effect changes at promotion.
19. Dev: the dev pair reads dev's live directory, not the working tree's
    `config/`. The test suite and the replay keep working; the report says
    what an agent now edits to try a config change on dev, and
    `CLAUDE.md` and `docs/features/config-directory.md` say so.
20. `tests/test_m21_pins.py` is retargeted: the pins hold the shipped
    defaults in the repo (the loaders' defaults equal the shipped files),
    not the live files, so an edit through the page fails no test.

### F. Regression, safety, docs

21. `./scripts/run_tests.sh` passes: at least `4849 passed, 3 skipped,
    53 xfailed` plus the new tests. New tests do not reach the network and
    use temporary directories and temporary git repositories.
22. The voice replay on the dev path matches the M10 baseline row by row
    apart from the known flapping rows.
23. nginx: `/settings` and its read endpoints are served on the LAN as the
    rest of the UI is; the write endpoints are reachable only with the
    session; `/config/reload` stays refused through nginx. Nothing new
    listens on a LAN address.
24. Every save, revert, unlock, failed unlock and lockout is written to an
    audit record (who, when, which file, which keys, the commit), without
    the passphrase and without prompt text beyond the commit's own diff.
25. Dev only during the build; production untouched; no change to Home
    Assistant, Music Assistant, the pucks or `.env`; no sound.
26. Docs: `docs/features/config-directory.md` (the live directories, the
    page, turning editing on, the guardrails, history and revert, what a
    deploy does, how to recover when a save went wrong, including by hand
    with git), `README.md`, `CLAUDE.md` (tenet 8's "config/ for prompts and
    tuning" now points at the live directory and the page), `ROADMAP.md`.

## Reports

Build: `docs/sprint/M21-step2-build.md`. End to end:
`docs/sprint/M21-step2-e2e.md`, one line per criterion with pass, fail or
not testable, and the evidence.
