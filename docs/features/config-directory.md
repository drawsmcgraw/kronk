# Feature: the config/ directory (prompts and tuning as plain files)

**Shipped:** 2026-11-17 (sprint M21 step 1, branch `refactor`); step 2, the
`/settings` page and the live config directories, built 2026-11-18 on the
dev path (promotion by the runbook in `docs/sprint/M21-step2-build.md`) ·
**Plan:** `Kronk evaluation findings and implementation plan.md`, section
M21 · **Criteria:** `docs/sprint/M21-step1-criteria.md`,
`docs/sprint/M21-step2-criteria.md` · **Build reports:**
`docs/sprint/M21-step1-build.md`, `docs/sprint/M21-step2-build.md`.

## What it does

Every agent prompt and the tuning values that were Python constants now
live in plain files under `config/` in the repo, so one place shows what
the models are told and how the loop is tuned:

| File | Read by | Holds |
|---|---|---|
| `config/prompts/<name>.md` | orchestrator | The system prompt of each agent in `agents.AGENTS` (research, home, assistant, coding, talkie, devops) and of the coordinator |
| `config/tuning.yaml` | orchestrator | Round budgets per agent, the repeat-call nudge threshold, the weather-context age, per-tool HTTP timeouts; since M14 the messy-transcript gate's `max_sentences` and `max_words` (`docs/features/messy-transcript-gate.md`); since M9 `model_lock.max_overtakes` (`docs/features/model-locks.md`) |
| `config/lead_in.yaml` | orchestrator | The words of the "ask Kronk to" lead-in: verbs, names, leading fillers (sprint M19, `docs/features/lead-in.md`) |
| `config/voice.yaml` | orchestrator | The speech mount (sprint M8): `spoken_reply`, the line added last to the coordinator's system text on `/voice/api/chat` requests (null leaves it out), and `stream.hold_sentences`, the units held back when `VOICE_STREAM` is on (`docs/features/voice-pipeline.md`); since M9 `deadline_s`, the speech mount's total deadline (`docs/features/model-locks.md`) |
| `config/music.yaml` | tool_service | The playback verify window and the music resolver's thresholds and bonuses; since M11 the sentence parser's words (`parser`) and the decision order's policy (`decision`: step order and the model step's place, thresholds, provider order, the model question), `docs/features/voice-music-control.md` |

The move changed no behaviour: `tests/test_m21_pins.py` renders 3390
system-prompt variants, 14 tool menus and every moved value and compares
them with fixtures written from the tree before the move.

## How it works

- **Mount.** Since step 2 each instance reads its own **live config
  directory** at `/config`, not the repo's `config/` (see "Live config
  directories" below): production's through the release's `config` link,
  dev's from `../kronk-config/dev` (`KRONK_DEV_CONFIG_DIR`). Long-syntax
  binds with `create_host_path: false`, so a missing directory stops the
  container instead of starting it on an empty root-owned one. Writable in
  the orchestrators (the editor), read-only in the tool_services.
  `KRONK_CONFIG_DIR` overrides the path; tests always point it at the
  repo's `config/` (`tests/conftest.py`).
- **Loaders.** `orchestrator/config_loader.py` (PyYAML) and
  `tool_service/config_loader.py` (ruamel.yaml). Two small modules rather
  than a shared package: each service builds its own image from its own
  directory, and each already pinned a different YAML library. No new
  dependency. Same rules in both. What the two must agree on lives in
  `config_files.py`, which is **byte-identical** in `orchestrator/` and
  `tool_service/` (a test compares them): bounded reading, and the YAML
  dialect.
- **One YAML dialect.** Neither library's type resolution is used. Each
  library only tokenises; `config_files.build()` walks its parse events
  and decides what a value is: `null`/`~`, `true`/`false`, decimal
  integers without leading zeros, and decimal floats. Everything else is
  a string, so YAML 1.1 surprises (`2:00:00`, `0o17`, `017`, `0x1f`,
  `7_200`, `yes`, `on`, `.inf`) reach the schema as strings and a numeric
  field rejects them. Quoted scalars are always strings. Anchors,
  aliases, merge keys (`<<`) and explicit tags (`!!int`) are errors;
  config files need none of them. The two libraries do not tokenise every
  text alike, so what they disagree on is refused before either sees the
  text: control characters other than newline and tab (U+0085 included),
  U+2028 and U+2029, lone surrogates, U+FFFE and U+FFFF, each named with
  its line and byte offset (an ESC after `max_age_s: 7200`:
  `config/tuning.yaml:32: control character U+001B at byte 1627 is not
  allowed in a YAML file (only newline and tab)`; a CRLF line end counts as
  one byte). An empty key (`? ` alone) is an error, and tool_service
  runs ruamel's scanner in YAML 1.1 mode so `{a?b: 1}` is refused by both.
  A shared table and 2,000 generated texts run through both loaders in
  the tests; both accept or both refuse, with the same data and lines
  when accepted. A YAML error the library cannot place says `line
  unknown`.
- **Bounds.** Each file is checked with `lstat` before a byte is read: it
  must be a regular file (not a directory, pipe or device), no path
  component under `config/` may be a symlink, and it must be at most
  64 KiB (`MAX_YAML_BYTES`, `MAX_PROMPT_BYTES`; the largest file today is
  4.2 kB). The read itself is capped at the same size. Nesting deeper
  than 10 collections is an error, raised before the parser reads further.
  A rejected value is quoted in the error up to 80 characters, and a load
  reports at most 50 problems plus a count of the rest. Under these caps
  the worst parse measured is 0.3 s, which bounds how long a reload holds
  the service (reload runs on the event loop).
- **No file takes a load down.** Any exception at all while loading a
  file (a parser `RecursionError`, a `MemoryError`, a library bug) becomes
  a config error for that file. `start()` never raises; `reload()` always
  returns.
- **Every answer can be encoded.** A file name, key, value or parser
  message can hold text JSON cannot carry (a name that is not UTF-8
  decodes to lone surrogates; so does a `"\uD800"` key). Each `Problem`
  passes its file and message through `config_files.safe()` when it is
  built: lone surrogates, control and format characters are escaped
  visibly (`config/caf\udce9.yaml`) and the length is capped; key names
  are cut to 80 characters like values. As a second layer, both services'
  `POST /config/reload` and status endpoints check the answer encodes and,
  if not, answer JSON saying the config status could not be rendered (and,
  for a reload, whether it took effect), never a bare 500. So the reload
  endpoint answers 200 or 422.
- **A file saved while it is read.** An editor that saves in place
  truncates and then writes. Each file's size and mtime are compared
  before and after the read, and with the number of bytes read; if they
  differ the file is read once more, and a second change is the error
  `file changed during read`. A read that falls wholly inside the moment
  between the truncate and the first write sees a steady empty file and
  loads it (with the warning `all defaults (file has no keys)`); only an
  atomic write (write a new file, rename it into place) closes that.
- **Schema.** pydantic models with `extra="forbid"` and `strict=True`:
  an unknown key, a wrong type (`"8"` is not `8`, `true` is not `1`) and a
  number outside its bounds are errors. Round budgets and per-tool
  timeouts are one field per agent or tool, so a misspelt name is an
  unknown key; tests check the field names against `AGENTS` and
  `tools.TOOL_DEFINITIONS`. Duplicate YAML keys are errors. A key left out
  takes its built-in default.
- **Prompt files.** Each starts with a `<!-- … -->` header (what the
  prompt is for, which agent), which is stripped. The rest, minus leading
  and trailing blank lines, is the prompt, byte for byte. Missing file,
  missing header, empty prompt (also: only further `<!-- -->` comments
  after the header), a control character other than newline and tab
  (NUL, ESC, form feed, …) and a `.md` file in `prompts/` that names no
  agent are errors. One placeholder exists: `{{max_rounds}}`, filled with
  the agent's round budget (the research prompt states its budget this
  way, so the prompt and the loop cannot disagree). Any other `{{name}}` is
  an error.
- **Built-in defaults.** The pydantic `Field` defaults and
  `orchestrator/prompt_defaults.py` hold the values as they were before
  the move. They are read only when `config/` fails validation at startup.
  Editing a value means editing its file, never the defaults.
- **Errors.** Every problem found, not just the first, with file and
  1-based line (`config/tuning.yaml:32: weather_context.max_age_s: Input
  should be a valid integer (got '60')`). Logged at ERROR, emitted as a
  `config_error` event, and shown on `/services` (Config section, data
  from `GET /api/config`, which merges the orchestrator's status with
  tool_service's `GET /config/status`).
- **Warnings.** Two things load but are reported as warnings, on
  `/services` (the card's badge says `warning`) and in the reload answer
  (`"warnings": [...]`): a YAML file with no keys (empty or comments only;
  every value at its default) as `all defaults (file has no keys)`, and
  any entry under `config/` or `config/prompts/` that no service reads (a
  misspelt `tunning.yaml`, a `home.md.bak`). The orchestrator scans the
  whole directory once, so a stray shows once; tool_service does not
  scan.
- **An invalid file never takes effect.** Loads are all or nothing. At
  startup a bad file means the service runs on the built-in defaults and
  says so; after a bad reload the last good values stay in effect.
- **Reload isolation (step 2).** A request keeps the config it started
  with: `config_loader.PinConfig`, an ASGI middleware outermost in both
  services, pins `current()` for the whole request (streamed body and the
  tasks it starts included), and a run reads its prompt and round budget
  through `agents.prompt_and_rounds()` from that pin. A reload takes effect
  for requests that start after it. `latest()` is the unpinned value.
- **Reload.** `POST /config/reload` on each service re-reads the files and
  answers 200 with what changed (`tuning.yaml:agent_loop.max_rounds.research:
  8 -> 6`, or `prompts/home.md: changed (3637 -> 3645 chars)`) or 422 with
  the errors and what stayed in effect. No file watching. From the host:
  `scripts/config_reload.sh dev|prod` (the orchestrator has no host port,
  so it goes through `docker exec`; tool_service is on its loopback port);
  it prints `<HTTP code> <body>` for each service.
- **Not on the LAN.** nginx returns 404 for `/config/` and `/dev/config/`.
  The orchestrator's handler also refuses (403) any request carrying
  `X-Real-IP` or `X-Forwarded-For`, which nginx sets on everything it
  proxies; that covers the time before nginx is restarted with the deny
  rule. tool_service's port is bound to 127.0.0.1 and nginx proxies only
  `/api/solar/` to it.
- **How values reach the code.** The prompts and round budgets are
  copied onto the agent registry by `agents.apply_config`, at import and
  after every successful reload, so `run_stream` reads
  `agent.system_prompt` and `agent.max_rounds` as before. Everything else
  is read at use: `config_loader.current().tuning…` in the orchestrator,
  `config_loader.current().music…` and `music_resolve.policy()` in
  tool_service.

## Live config directories (step 2)

| Instance | Directory | Mounted | Edited on |
|---|---|---|---|
| Production | `kronk-config/prod` (next to `kronk-releases/`) | via `<release>/config -> kronk-config/prod` | `/settings` |
| Dev | `kronk-config/dev` | `../kronk-config/dev` from the working tree's compose file | `/dev/settings` |

Each is its own git repository with local commits only. The repo's
`config/` is the shipped defaults: the seed of a new live directory and
what the tests and the pins read. Nobody edits a live file in the repo.

- **Seeding.** `scripts/deploy_tag.sh seed prod|dev <tag or commit>`
  creates one from `<rev>:config/`, byte for byte, with a first commit
  `Seed from <rev> (<commit>)`. It refuses an existing directory. A deploy
  seeds any that is absent from the deployed tag.
- **What a deploy does.** `scripts/deploy_tag.sh deploy <tag>` never
  overwrites a live file. Keys the new code adds take the loaders' built-in
  defaults until someone writes them. Before anything is built it validates
  production's live directory with the tag's own loaders
  (`scripts/config_check.py`, run by `tests/.venv/bin/python`, which has
  the images' pinned pydantic and YAML libraries) and refuses with file and
  line when it fails. It prints, for information, the shipped defaults that
  differ from the live files, then moves the release's `config/` to
  `config.shipped/` and links `config` to the live directory. `plan` shows
  all of it and changes nothing.
- **Rollback.** Deploying an older tag runs the same validation with the
  older loaders. A live file using a key the older code does not know is
  refused, naming file and line, with what to do: revert that edit in the
  live directory (`git -C kronk-config/prod revert <commit>`) or edit and
  commit by hand, then `plan` again. A tag older than step 1 reads no
  `config/`; the live directory does not affect it.
- **Trying a config change on dev (agents).** Edit it on `/dev/settings`,
  or edit the file in `kronk-config/dev`, commit it there with
  `git -C ../kronk-config/dev commit`, and run
  `scripts/config_reload.sh dev`. Do not edit the repo's `config/` to try a
  value: that changes the shipped defaults (and the pins).

## The `/settings` page (step 2)

`/settings` (production) and `/dev/settings` (dev), linked from
`/services`. No framework, no build step; every string from the server is
put on the page as text, never as HTML.

**Viewing needs nothing.** Every file of the live directory with its
description; per key the value in effect, its source (the file, an
environment variable, or the code default), the comment above it in the
file, its bounds, and the commit that last changed it (from the live
directory's log, the last 100 commits of the file); prompts whole. Read
only, each with where to change it: the routing rules in order (name,
route, the words, the pattern in effect), both services' environment
settings (allow-lists: no secret is ever read), model servers with what
the running llama servers report, tool descriptions, model-facing text in
code, the structural guards, host-side settings. The instance, the
release, whether editing is on and any config error in effect. Read
endpoints: `GET /api/settings`, `/api/settings/file`, `/history`, `/diff`,
`/digests`.

The llama servers listen on the host's loopback, which the containers
cannot reach; `scripts/llama_props_export.py` (host timer
`systemd/llama-props-export.timer`, every 60 s) writes each server's
`GET /props` and its process flags to `data/llama_props.json` and
`data/dev/llama_props.json`. Without the timer the page says so.

**Turning editing on.** `scripts/settings_passphrase.sh` on kronk asks for
a passphrase twice without echo and writes
`SETTINGS_PASSPHRASE_HASH=scrypt:15:8:1:<salt>:<key>` to `.env`, replacing
an old line; it prints neither the passphrase nor the hash. Redeploy
(`scripts/deploy_tag.sh deploy <tag>`; for dev, the dev up line). Both
instances read the same line. Delete it and redeploy to turn editing off.
The page is plain HTTP on the LAN: the passphrase crosses the network
unencrypted when you unlock; use one you use nowhere else. Editing also
needs a usable git repository and a writable live directory; the page says
which is missing.

**Sessions.** Unlocking sets an `HttpOnly`, `SameSite=Strict` cookie
(`kronk_settings_production`, path `/`; `kronk_settings_dev`, path
`/dev/`), kept in memory: 15 minutes idle, 2 hours at most, Lock ends it, a
restart ends all. Every state-changing request needs the cookie, the
session's second token in `X-Settings-Token` and the page's own `Origin`.
Five wrong passphrases from one address, or twenty from all, in 15 minutes
lock unlocking for 15 minutes.

**A save** (`POST /api/settings/save`, `settings_save.py`): the editor's
base version (git blob id of what it loaded) must be the file on disk, else
409 with what changed meanwhile and the difference, nothing merged;
validation with the loader's own schema (tool_service's `/config/validate`
for `music.yaml`), refused with file and line; the corpus check (below);
an atomic write (temp file in the same directory, fsync, rename); reload of
the service that reads the file and a check that the digest in effect is
the one written; reload of the other service; one git commit naming the
file, the keys and who (instance and client address); an audit line in
`data/settings_audit.jsonl` (dev: `data/dev/`). If the reload refuses the
file, the digest differs or the commit fails, the previous bytes go back,
the owner reloads, nothing is committed, and the page names the step. The
other service failing its reload is a warning. Size limits: 64 KiB per
file. Only the known file names can be written (no path from a request is
ever joined); no file can be created, renamed or deleted.

**Guardrails before a save reaches the household.**
- `music.yaml` and `lead_in.yaml`: the voice corpus's offline layer runs
  with the values in effect and with the candidate. Routing and the direct
  call run in the orchestrator (`corpus_routing.py`, the candidate pinned);
  the parser and the decision order run in a child process of tool_service
  (`corpus_check.py` via `POST /config/corpus`: the MA/HA snapshot, dry run
  on, every socket blocked). The page lists rows whose outcome changes; a
  row that newly fails blocks the save until the editor ticks it, and the
  overrides go in the commit message. The test suite runs the same two
  modules; the images carry `tests/voice_corpus.jsonl` and
  `tests/fixtures/music_snapshot.json` from the same commit (compose
  additional context `corpus`, `tests/.dockerignore`), and the answer shows
  the corpus digest.
- Prompts: the editor says the edit reaches the household at once and the
  next request re-reads the whole prompt (one slower request). Production's
  page offers **Apply to dev first** (a request signed with a key derived
  from the shared hash, to dev's `/api/settings/peer/save` over the compose
  network, refused through nginx; dev runs its own save path) and **Bring
  over from dev** (dev's file into production's editor, saved through
  production's path). A file where dev differs is marked.

**History and revert.** Per file: the commits (date, message, who), the
diff of any one, and Revert, which takes that commit's change back out of
the file as it is now (`git merge-file`, so later edits to other lines
stay) and saves the result through the save path as a new commit. Refused
with the reason when later edits touched the same lines, for the seed
commit, or when the result no longer validates against the running code.

**Before a passphrase is set, read `docs/sprint/M21-step2-e2e.md` C3 to
C10.** In short: a hand edit of a live file is committed by the next page
save of that file without being named in the commit message (the page
marks the file "uncommitted change on disk" first); the page cannot put a
file back to exactly its committed text (the commit has nothing to
commit, and the save is undone), so a file broken by hand is repaired with
`git -C <live dir> checkout -- <file>` and `scripts/config_reload.sh`;
request bodies are not size-limited before they are parsed (nginx allows
500 MB); twenty wrong passphrases from any mix of LAN addresses lock
unlocking for everyone for 15 minutes; anything that can read
`SETTINGS_PASSPHRASE_HASH` can write dev's live directory.

**Recovering by hand.** The live directory is an ordinary git repository
owned by the host user:

```bash
cd ~/git-repos/drawsmcgraw/kronk-config/prod      # or dev
git log --oneline -- tuning.yaml                  # what changed, by whom
git show <commit>                                 # one change
git revert <commit>                               # undo it (or edit + git commit)
git status                                        # an uncommitted hand edit?
git checkout -- tuning.yaml                       # drop it
cd -; scripts/config_reload.sh prod               # make it take effect
```

A reload answers 422 with file and line if the result is invalid, and the
last good values stay. A missing or corrupt `.git` turns editing off with
the reason on the page; viewing and loading are unaffected; restore it
from a backup or re-seed into a new directory and copy the files over.

## Precedence

Only one moved value had another source: `WEATHER_CTX_MAX_AGE_S`. For it,
env var > `tuning.yaml` > built-in default (`config_loader.ENV_OVERRIDES`).
An invalid env value is a config error naming the variable (it used to
crash the import). No new env vars were added. Model assignments,
`ERROR_STYLE`, `HISTORY_MAX_MESSAGES`, `LOCATION` and the other env knobs
stay env vars.

## What is not in config/

Routing regexes (`routing.py`, with their unit tests; since M19 the
lead-in's words are in `lead_in.yaml`, escaped into its pattern, so no file
holds a regex), secrets (`.env`;
`tests/test_config_loader.py` scans `config/` for secret names and
token-shaped strings), llama unit flags and `litellm/config.yaml`, tool
descriptions, `_terminal_speech` templates, `errors.py` sentences, the
loop's guardrail texts and the two tool_service LLM prompts. The build
report has the full inventory and the reason for each.

## Gotchas

- **The pins hold the shipped defaults.** `test_m21_pins.py` compares the
  repo's `config/` (and the loaders' built-in defaults, which must equal
  it) with the pre-move rendering. Edits on `/settings` change live
  directories, not the repo, so they fail no test. A deliberate change of a
  shipped default changes `config/`, the code default and the fixtures in
  one commit.
- **Two writers.** The page and a hand edit can meet: the page refuses a
  save based on an older version (409), and a hand edit left uncommitted is
  marked on the page and is committed with the next save of that file.
- **A restart reloads too.** Reload is the only *runtime* trigger, but a
  container restart reads the live files fresh, committed or not.
- **Cross-file coupling.** The orchestrator's `tool_timeouts` for the
  music tools must stay above `music.yaml verify.timeout_s`; no loader
  checks across files.
- **Symlinks are refused on purpose.** Linking a config file to a copy
  elsewhere is an error, not a shortcut: in a container the link target
  usually does not exist, and a target that does exist would be read from
  outside the reviewed directory. The `config/` directory itself may be a
  symlink (a release that links it); only what is under it is checked.
- **Caps live in code.** The size and depth limits are in
  `config_files.py`, not in `config/`: a cap the file could raise is no
  cap.
- **Prompt cache.** A prompt edit invalidates llama.cpp's cached prefix
  once; the next request pays for it.

## Blog hooks

- A settings page that cannot loosen a guard: replace every prompt with
  "ignore your limits" and watch the mirror pin, the devops tool set, the
  fetch block, dry run and the exec classifier hold (step 2, criterion 11).
- The regression corpus as a save button's gatekeeper: 160 utterances run
  offline in two seconds before a music-policy edit reaches the household.
- Two git repositories per house: shipped defaults in the code repo, the
  live values in their own, and a deploy that refuses to roll back past a
  key the old code does not know.

- Moving 7 prompts out of Python without changing a byte: pin 3390
  rendered variants first, then move one piece per commit.
- "Strict" means strict: why `"3"`, `true` and a misspelt key must be
  errors in a file a human edits from a phone.
- An invalid file never takes effect: all-or-nothing loads, built-in
  defaults at startup, last good values on reload.
- Measuring a one-word prompt edit live with nothing but prompt token
  counts (2016 → 2018 → 2016).
