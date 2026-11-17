# M21 step 1 end-to-end test: a config directory of plain files

**M21 STEP 1 FAILS.**

End-to-end test agent (Claude Fable 5.2), 2026-11-17 04:12Z to 04:50Z,
branch `refactor` at `bc39131`. Criteria: `docs/sprint/M21-step1-criteria.md`.
`docs/sprint/M21-step1-build.md` was read as claims to check. The commit
before the build is `ff99263`. I tested; I changed no repo code, test,
corpus or config file.

Why it fails: criteria 8 and 10, on two kinds of file the brief told me to
feed it. A YAML file with a few hundred nested brackets, or a nine-line
alias bomb, is not rejected as a config error. At startup the service does
not start (confirmed live on dev: both dev containers went into a restart
loop and `/dev/` answered 502). On reload the answer is a bare `500
Internal Server Error` with no file and no line, and `/services` goes on
showing the config as healthy. Findings 1 and 2. A third finding is in the
same area: a large file blocks the whole service for the length of the
reload (28 s for 50 MB on dev). Criterion 1 also fails on completeness
(finding 5): the inventory misses the solar thresholds and a few other
tuning values.

What holds, and holds firmly: the move changed no behaviour. Every system
prompt, tool menu, multi-round transcript and tuning value I could render
is byte-identical between `ff99263` and `bc39131`, from the files, with
`config/` absent, and with each of the nine files invalid in turn
(section 2). The pin fixtures are genuine. The ordinary bad files (unknown
key, wrong type, out of range, duplicate key, tabs, missing or empty
prompt, non-UTF-8) are all rejected with file and line, in scratch and
live on dev, and the last good values stay. Criterion 18 passes live.
The replay matches the baseline.

For the coordinator: the fix is narrow. Catch everything a parse can
raise (not only `YAMLError`), cap the file size before parsing, and stop
putting the rejected value's full `repr` into the error message. None of
it touches the prompts, the values or the pins. A reload during a running
request mixes old and new values in that request (finding 4); no
criterion forbids it, the brief asked me to check it, and it is
undocumented.

## 1. Criteria

| # | Result | Evidence |
|---|---|---|
| 1 | **fail** (incomplete) | The inventory is there and every row has an outcome. By search it misses tuning values defined in `tool_service/`: the solar thresholds, the weather refresh interval, three taste knobs. Finding 5 |
| 2 | pass | `git diff ff99263 bc39131 -- orchestrator/routing.py litellm/ systemd/` is empty. `.env` not touched (not tracked; the build did not change `.env.example`). The `ctx_k` note is in the report and `litellm/config.yaml` is unchanged |
| 3 | pass | `config/prompts/` has exactly seven files, one per name in `AGENTS` plus `coordinator`; `config/tuning.yaml`, `config/music.yaml`. No stub files, no invented values |
| 4 | pass | Read both YAML files: every key has a comment above it. Every prompt file starts with `<!--`, has one `-->`, and names its agent on the first line. No rendered prompt contains `<!--` or an unfilled `{{max_rounds}}` (checked on every captured system message) |
| 5 | pass | My own scan, section 6: no secret name, no secret value, no token shape, no URL, address or e-mail in `config/`. The builder's test also kills two planted secrets (mutants f5, f6) |
| 6 | pass | `./config:/config:ro` on all four services in `docker-compose.yml`. `docker inspect`: both dev containers mount the working tree's `config/` read-only. Production still runs release `sprint-M2`, which has no `/config` mount yet |
| 7 | pass | Unknown key, wrong type (string, bool, float for int, list, null), out of range (low, high, negative, `.inf`, `.nan`), missing prompt, empty prompt, header-only prompt: each rejected with file and, for YAML, line. Scratch (section 3) and live on dev (section 4) |
| 8 | **fail** | Holds for every ordinary bad file, at startup and on reload, in scratch and live. Does not hold for deeply nested YAML or an alias bomb: at startup the service does not start. Findings 1 and 2 |
| 9 | pass, nginx rule not tested live | Reload works from the host and from the compose network on both dev services. Through nginx's `/dev/` prefix every attempt was refused (403 from the handler, or 404). The nginx deny rule is not loaded in the running nginx (it mounts release `sprint-M2`'s config), so it is untested live; in a throwaway container with no network the tree's config answers `/config/…` and `/dev/config/…` with 404 itself. Concurrent reloads are serialised. Section 5 |
| 10 | **fail** | For ordinary bad files the error is on `/dev/api/config` (the `/services` data) with file and line, and in the logs. For the files of findings 1 and 2 the reload answers 500 and `/services` shows `ok=True` with no error |
| 11 | pass | Only `WEATHER_CTX_MAX_AGE_S` has two sources. With the env var set to 600, old tree and new tree inject the forecast for exactly the same ages, from the files and from the defaults (section 2). An invalid env value is an error naming the variable (builder's test; before the build it crashed the import) |
| 12 | pass | No moved constant name is left in `orchestrator/`, `tool_service/`, `scripts/`, compose or the units, outside comments and the env override table. Prompt text exists only in `config/prompts/`, `prompt_defaults.py` and the pin fixture; `prompt_defaults.PROMPTS` is read only by `config_loader` (for the defaults and for the list of names). `scripts/model_bench.py` has its own older, different prompt; it was not moved and not changed |
| 13 | pass | The builder's 3390 pinned variants pass. Independently: 13,440 single-round variants, 144 multi-round transcripts and 224 pipeline runs, byte-identical old against new. The pins were written from `ff99263`. Section 2 |
| 14 | pass | Menus identical. Values identical in effect; one type difference that changes nothing (observation 1) |
| 15 | pass | 17 commits, home prompt first, one piece each. I ran the whole suite and the pin file at every commit, in a scratch extract: all pass (section 7) |
| 16 | pass for the build | `2757 passed, 2 skipped, 18 xfailed`. The new tests make no network attempt. The sentence "unit tests do not reach the network" is not true of the suite as a whole, before or after the build: finding 6 |
| 17 | pass | Complete rerun: 113 of 114 baseline rows have the baseline's status; the one that differs is a known flapper. Section 7 |
| 18 | pass | Live on dev, section 4a. `config/` byte-identical at the end, `git status` clean |
| 19 | pass | `README.md` ("Prompts and tuning", runbook line), `docs/features/config-directory.md` (schema, precedence, reload, failure behaviour), `CLAUDE.md` item 3 and tenet 8 (two edits, nothing else in the diff), `kronk-context.md` one sentence. The feature doc does not say what a reload does to a request in flight (finding 4) |
| 20 | pass | Production containers were all "Up About an hour" at the end, none recreated during my test; HA up 28 h, Music Assistant 12 days; no llama unit, LiteLLM, HA, MA or nginx restart by me. The replay reported "players unchanged (17 checked)" on the complete run |

## 2. No behaviour change (criteria 13, 14)

Method. `git archive ff99263` and `git archive bc39131` into two scratch
directories. One script of my own (not the builder's test, not its
fixtures) ran in each tree with `llm.stream` replaced by a recorder, the
clock frozen, and every INET socket call blocked. It captured, as full
JSON:

- **single round**: `agents.run_stream` for all seven agents × error style
  (debug, friendly, derived) × voice × origin stamp × escalation × five
  `system_extra` blocks × weather context on/off × embedded context ×
  `history_messages` × `delegated`: 13,440 runs, each stored as the sha256
  of the complete message list, tool menu and yielded events;
- **loops**: 144 multi-round transcripts in full: budget exhausted (every
  agent, so the round budget and the repeat-call nudge are exercised, not
  read), duplicate call, rejected tool, failed tool, two calls in one
  round, empty reply, LLM error, escalation, terminal tool success and
  failure, and the coordinator delegating to each specialist in both
  styles, voice and not;
- **pipeline**: `main._run_pipeline` for all seven routes × (ok, LLM error,
  empty reply, escalate) × voice × origin × local context: 224 runs;
- **menus**: `tool_defs()` of every agent, `agent_tool_defs()`,
  `_coordinator_tool_defs()`, `TOOL_DEFINITIONS`, `ESCALATE_TOOL`,
  `ESCALATION_GUIDANCE`, `roster()`, and each agent's model, budget,
  terminal tools, tool names, description and routing hint;
- **values as used**: the `timeout=` that `tools.execute` hands to httpx
  for all 22 tools; whether `weather_context()` injects the forecast at
  twelve ages around the limits; LLM calls made before the budget ends
  (4 for the default agents, 9 for research, 31 for the coordinator… the
  same in both trees); the verify window and the sentence that names it;
  `music_resolve.score()` over 4,320 query/candidate pairs; the
  "closest I found" sentence at eight scores around the hint threshold;
  and the constants themselves.

Result: 64,633 compared leaves per run.

| New tree run with | Config source reported | Leaves that differ from `ff99263` |
|---|---|---|
| `config/` as committed | orchestrator files, tool_service files | 22, all the type of the tool timeout (observation 1); 0 others |
| `config/` absent | both defaults, 8 + 1 "file is missing" errors | 0 |
| each of the seven prompt files invalid in turn | orchestrator defaults, tool_service files | 0 (seven runs) |
| `tuning.yaml` invalid | orchestrator defaults, tool_service files | 0 |
| `music.yaml` invalid | orchestrator files, tool_service defaults | 22, the same type-only leaves |
| `WEATHER_CTX_MAX_AGE_S=600`, files (against old tree with the same env) | files | 22, the same |
| `WEATHER_CTX_MAX_AGE_S=600`, `config/` absent (same) | defaults | 22, the same |

With the env var at 600 both trees inject the forecast at ages 0, 59, 60
and 61 s and at none above; without it, up to and including 7200 s.

**The pin fixtures are genuine.** `git diff ff99263 4f05264` touches only
`tests/` (five files). `git diff 4f05264 bc39131 -- tests/fixtures/m21` is
empty. I extracted `4f05264` (code identical to `ff99263`, no `config/`
directory in it), deleted the three fixture files, ran
`tests/test_m21_pins.py --write` there, and compared with the committed
fixtures: `prompts.json`, `menus.json` and `values.json` are byte-identical
(`cmp`).

## 3. Loading and validation in scratch (criteria 7, 8, 10, 12)

84 cases, each run twice in a fresh interpreter against a scratch copy of
`bc39131`: once as the config directory at startup, once swapped in after a
good start and reloaded through the real `POST /config/reload` route
(FastAPI test client). 6 GB address-space limit, 120 s timeout. Checked on
every case: answer and status code, the values in effect before and after,
`status()` (the `/services` data), and that a second reload still answers.

Rejected with file and line, last good values kept, source `defaults` at
startup, 422 on reload (line `None` where the problem is the whole file):

- `tuning.yaml`: unknown key, unknown top-level section, string for int,
  bool for int, float for int, list for number, null for a required value,
  a section left null, below range, above range, negative, `.inf`, `.nan`,
  duplicate key, duplicate top-level key, tabs, syntax error, a scalar
  document, a list document, two documents, non-UTF-8 bytes, NUL bytes, a
  key that is a list, an integer key, a null key, a
  `!!python/object/apply:os.system` tag (refused by the safe loader,
  nothing ran), missing file, a directory in its place, a dangling
  symlink, a symlink loop.
- prompts: missing file, empty file, header only, header and whitespace
  only, no header, unclosed header, unknown placeholder (line 3 named),
  a prompt file for no agent, non-UTF-8 bytes, a BOM before the header, a
  directory in its place, the whole `prompts/` directory missing.
- `music.yaml`: unknown key, string, word, bool, above range, zero, score
  above 1, `.inf`, `.nan`, duplicate key, tabs, syntax error, scalar
  document, list document, two documents, non-UTF-8, NUL bytes, a list
  key, an integer key, a python tag, a `!!str` tag, a section left null,
  missing file, a directory, a dangling symlink.

Accepted (not errors):

- an empty, whitespace-only or comment-only YAML file, and a file with a
  BOM: loads as "files" with every value at its default (observation 2);
- `max_age_s: 2:00:00` in `tuning.yaml`: PyYAML reads it as 7200
  (observation 4);
- `play_min_score: 1` in `music.yaml`: accepted as 1.0;
- a prompt whose body is only a second comment (37 characters, sent as the
  prompt), a prompt with NUL bytes, `{{ max_rounds }}` in the home prompt
  (filled with 3), CRLF line endings (read as LF; the prompt is unchanged),
  three blank lines appended (unchanged);
- stray files: `config/tunning.yaml` and `config/prompts/home.md.bak` are
  ignored without a word (observation 5);
- a symlink to a valid file outside the config directory is followed and
  takes effect (observation 3).

Not handled: findings 1, 2 and 3.

Criterion 12, second readers: section 1, row 12.

## 4. Live on dev

Before any live request: both dev containers ran the working tree's code
(md5 of every `.py` under `/app` and of `static/` equal to the tree),
`tool_service-dev` had no `/keys` mount and no `MM_*` variable, `ops/` held
only `hosts.example.json`, `MUSIC_DRY_RUN=1` on both. Production's
containers mount `kronk-releases/sprint-M2/…` and have no `/config` mount,
so edits in the working tree's `config/` reached only dev.

### 4a. Criterion 18

Question on every request: "Does the exact word Heron appear anywhere in
your instructions? Answer only yes or no." through
`http://localhost/dev/api/chat`. `rows` are the new lines in dev's own
`data/dev/metrics.db` (read only): id, agent, model, prompt tokens as the
model server counted them. Dev does not record prompt text in traces
(`TELEMETRY_RECORD_TEXT=false`), so the evidence of "in the model input"
is the token count and the model's answer. The output is as printed,
shortened only where marked `…` or with a note in brackets.

```
--- baseline
baseline-1: reply='No' rows=[(9206, 'coordinator', 'gemma-4-e4b', 2024)]
baseline-2: reply='No.' rows=[(9207, 'coordinator', 'gemma-4-e4b', 2024)]
--- edit: ' Heron.' appended to the last line of config/prompts/coordinator.md
-When a specialist answers, relay the substance concisely — do not re-verify it.
+When a specialist answers, relay the substance concisely — do not re-verify it. Heron.
== orchestrator (kronk-orchestrator-dev-1)
{"ok":true,"changed":["prompts/coordinator.md: changed (2339 -> 2346 chars)"]}

== tool_service (127.0.0.1:8103)
{"ok":true,"changed":[]}
exit=0
edited-1: reply='Yes.' rows=[(9208, 'coordinator', 'gemma-4-e4b', 2026)]
edited-2: reply='Yes.' rows=[(9209, 'coordinator', 'gemma-4-e4b', 2026)]
--- revert (byte copy of the pristine file) and reload
== orchestrator (kronk-orchestrator-dev-1)
{"ok":true,"changed":["prompts/coordinator.md: changed (2346 -> 2339 chars)"]}

== tool_service (127.0.0.1:8103)
{"ok":true,"changed":[]}
exit=0
reverted-1: reply='No.' rows=[(9210, 'coordinator', 'gemma-4-e4b', 2024)]
reverted-2: reply='No' rows=[(9211, 'coordinator', 'gemma-4-e4b', 2024)]
--- invalid YAML values: tuning.yaml agent_loop.max_rounds.default: three ; music.yaml verify.timeout_s: 800
== orchestrator (kronk-orchestrator-dev-1)
422 {"ok":false,"errors":[{"file":"config/tuning.yaml","line":17,"message":"agent_loop.max_rounds.default: Input should be a valid integer (got 'three')"}],"kept":"last good values (files, loaded 2026-11-17 04:24:33)"}

== tool_service (127.0.0.1:8103)
{"ok":false,"errors":[{"file":"config/music.yaml","line":19,"message":"verify.timeout_s: Input should be less than or equal to 60 (got 800)"}],"kept":"last good values (files, loaded 2026-11-17 04:24:33)"}
exit=1
--- dev /services data (http://localhost/dev/api/config)
orchestrator ok= False event= reload source= files kept= last good values (files, loaded 2026-11-17 04:24:33) files= {… 'tuning.yaml': '6012df576095'}
    {'file': 'config/tuning.yaml', 'line': 17, 'message': "agent_loop.max_rounds.default: Input should be a valid integer (got 'three')"}
tool_service ok= False event= reload source= files kept= last good values (files, loaded 2026-11-17 04:24:33) files= {'music.yaml': 'c491a4b42753'}
    {'file': 'config/music.yaml', 'line': 19, 'message': 'verify.timeout_s: Input should be less than or equal to 60 (got 800)'}
--- dev /services page
GET /dev/services -> 200
153:    <div class="section-label">Config (config/ prompts and tuning)</div>
276:      const { services } = await (await fetch('api/config')).json();
--- old values still in effect (running processes)
after-bad-reload: reply='No.' rows=[(9212, 'coordinator', 'gemma-4-e4b', 2024)]
{"status":"ok","music_dry_run":true}
GET /dev/api/status -> 200
--- dev logs
2026-11-17 04:24:42,322 ERROR config_loader: config on reload rejected: config/tuning.yaml:17: agent_loop.max_rounds.default: Input should be a valid integer (got 'three') (keeping last good values (files, loaded 2026-11-17 04:24:33))
ERROR:tool_service.config:config on reload rejected: config/music.yaml:19: verify.timeout_s: Input should be less than or equal to 60 (got 800) (keeping last good values (files, loaded 2026-11-17 04:24:33))
--- restore both files and reload
{"ok":true,"changed":[]}   (both services)
restored: reply='No.' rows=[(9213, 'coordinator', 'gemma-4-e4b', 2024)]
config/ sha256 identical to before
git status config/: clean
```

The file digests shown during the failed reload are the last good ones
(`6012df576095` is the committed `tuning.yaml`), which is the old value
still in effect.

### 4b. Bad files on dev

Each case: edit the working tree's `config/`, reload the one dev service
that reads the file, read `/dev/api/config`, probe `/dev/api/status` and
`tool_service-dev`'s `/health`, restore from a byte copy, reload. Both
probes answered 200 after every case.

| Input | Reload answer | On `/dev/api/config` |
|---|---|---|
| `tuning.yaml`: `bogus_key: 1` under `agent_loop` | 422 `config/tuning.yaml:25: agent_loop.bogus_key: unknown key` | same error, `ok=False` |
| `tuning.yaml`: `default: '3'` | 422 `…:17: agent_loop.max_rounds.default: Input should be a valid integer (got '3')` | same |
| `tuning.yaml`: `default_s: 601` | 422 `…:38: tool_timeouts.default_s: Input should be less than or equal to 600 (got 601)` | same |
| `tuning.yaml`: second `weather_context:` block appended | 422 `…:64: duplicate key weather_context` | same |
| `tuning.yaml`: a tab before `repeat_tool_nudge_at` | 422 `…:24: not valid YAML: found character '\t' that cannot start any token` | same |
| `tuning.yaml`: 0 bytes | **200** `{"ok":true,"changed":[]}` | `ok=True` (observation 2) |
| `tuning.yaml`: bytes `ff fe` in a comment | 422 `config/tuning.yaml: cannot be read: 'utf-8' codec can't decode byte 0xff in position 35` (no line) | same |
| `tuning.yaml`: `agent_loop: ` then 20000 `[` and 20000 `]` | **500** `Internal Server Error` (0.49 s) | **`ok=True`, no error** (finding 1) |
| `tuning.yaml`: 50 MB of `# pad` lines, then `default_s: 601` | 422 with line 8738171, after **27.83 s**; a `GET /dev/api/status` sent 1 s into the reload answered after **28.3 s** | same error (finding 3) |
| `tuning.yaml`: symlink to a scratch file outside `config/` | 422 `config/tuning.yaml: file is missing` (the target does not exist inside the container) | same |
| `prompts/talkie.md` removed | 422 `config/prompts/talkie.md: file is missing` | same |
| `prompts/home.md`: 0 bytes | 422 `config/prompts/home.md:1: must start with a <!-- header comment -->` | same |
| `prompts/home.md`: only `<!-- only the header comment -->` | 422 `config/prompts/home.md:1: prompt is empty` | same |
| `prompts/home.md`: byte `e9` in the body | 422 `config/prompts/home.md: cannot be read: 'utf-8' codec can't decode byte 0xe9 in position 14` | same |
| `prompts/home.md`: symlink to a scratch file outside `config/` | 422 `config/prompts/home.md: file is missing` | same |
| `prompts/extra.md` added | 422 `config/prompts/extra.md: unknown prompt file: no agent of that name (known: …)` | same |
| `music.yaml`: `bogus: 1` under `resolver` | 422 `config/music.yaml:58: resolver.bogus: unknown key` | same |
| `music.yaml`: `timeout_s: '8'` | 422 `…:19: verify.timeout_s: Input should be a number (got '8')` | same |
| `music.yaml`: `play_min_score: 1.5` | 422 `…:31: resolver.play_min_score: Input should be less than or equal to 1 (got 1.5)` | same |
| `music.yaml`: `timeout_s` twice | 422 `…:20: not valid YAML: found duplicate key "timeout_s" with value "9" (original value: "8")` | same |
| `music.yaml`: a tab before `timeout_s` | 422 `…:19: not valid YAML: found character '\t' that cannot start any token` | same |
| `music.yaml`: 0 bytes | **200** `{"ok":true,"changed":[]}` | `ok=True` (observation 2) |
| `music.yaml`: bytes `ff fe` | 422 `config/music.yaml: cannot be read: 'utf-8' codec can't decode byte 0xff in position 25` | same |
| `music.yaml`: `verify: ` then 20000 `[` and 20000 `]` | **500** `Internal Server Error` (1.41 s) | **`ok=True`, no error** (finding 1) |
| `music.yaml`: symlink to a scratch file outside `config/` | 422 `config/music.yaml: file is missing` | same |

The alias bomb was not run on dev: in scratch it used all 6 GB it was
allowed, and the dev containers have no memory limit on a host that also
holds the models and a memory watchdog that pushes alerts.

### 4c. Startup with a bad file on dev (criterion 8)

`tuning.yaml` with `default: 0` and `music.yaml` with `timeout_s: eight`,
then `docker restart` of the two dev containers only:

```
   both dev containers healthy after ~8s
   /services: orchestrator ok=False source=defaults event=startup kept=built-in defaults
       {'file': 'config/tuning.yaml', 'line': 17, 'message': 'agent_loop.max_rounds.default: Input should be greater than or equal to 1 (got 0)'}
   /services: tool_service ok=False source=defaults event=startup kept=built-in defaults
       {'file': 'config/music.yaml', 'line': 19, 'message': "verify.timeout_s: Input should be a number (got 'eight')"}
   chat reply: 'Paris'
   last coordinator row: [(9215, 'coordinator', 'gemma-4-e4b', 2016)]
   orchestrator: source defaults | default rounds 3 | research 8 | nudge 3 | weather age 7200 | play_music timeout 20
   tool_service: source defaults | verify 8 | play_min_score 0.75
2026-11-17 04:27:30,242 ERROR main: config at startup rejected: config/tuning.yaml:17: agent_loop.max_rounds.default: Input should be greater than or equal to 1 (got 0) (running on built-in defaults)
ERROR:tool_service.config:config at startup rejected: config/music.yaml:19: verify.timeout_s: Input should be a number (got 'eight') (running on built-in defaults)
```

Files restored and reloaded without a restart: both `ok=True source=files`,
the next request carried 2016 prompt tokens again.

## 5. Reload (criterion 9)

```
== host -> both, via scripts/config_reload.sh dev
{"ok":true,"changed":[]}   {"ok":true,"changed":[]}   exit=0
== compose network: from tool_service-dev
http://orchestrator-dev:8100/config/reload 200 {"ok":true,"changed":[]}
http://tool_service-dev:8103/config/reload 200 {"ok":true,"changed":[]}
== compose network: from orchestrator-dev
http://tool_service-dev:8103/config/reload 200 {"ok":true,"changed":[]}
== POST through nginx, dev prefix (curl --path-as-is)
/dev/config/reload              403 {"detail":"/config/reload is not served through nginx; call it from the host (scripts/config_reload.sh) or the compose network"}
/dev//config/reload             403      /dev/./config/reload        403
/dev/x/../config/reload         403      /dev/%63onfig/reload        403
/dev/config%2Freload            403      /dev/config/reload?x=1      403
/dev/static/../config/reload    403      /dev/config/reload/         307
/dev/CONFIG/reload              404      /dev/voice/config/reload    404
== straight at the handler, inside the dev container
X-Real-IP: 192.0.2.9 -> 403     X-Forwarded-For: 192.0.2.9 -> 403
X-Real-IP: (empty) -> 200       Forwarded: for=192.0.2.9 -> 200     no header -> 200
GET, PUT, DELETE -> 405
== published ports
orchestrator-dev: none        tool_service-dev: 8103/tcp -> 127.0.0.1:8103
```

The app has one route containing "config" for POST, `/config/reload`, and
no mounted sub-app that repeats it (walked `main.app.routes` in the dev
container).

**nginx rule: not tested live.** The build report says nginx was not
restarted; `docker inspect` confirms the running nginx mounts
`kronk-releases/sprint-M2/nginx/nginx.conf`, which differs from the
tree's by exactly the two deny lines. I read the brief as: in that case,
test the handler refusal on dev and send nothing to production's root
path. So no request went to production for this. What I could do without
touching production: the tree's `nginx.conf` in a throwaway
`nginx:1.29-alpine` container with `--network none` (`nginx -t`
successful), POST from inside it:

```
/config/reload  404    /config/  404    /dev/config/reload  404    /dev/config/  404
/dev//config/reload  404    /dev/x/../config/reload  404    /dev/./config/reload  404
/dev/%63onfig/reload  404   /config%2Freload  404           /dev/config%2Freload  404
/config  502    /CONFIG/reload  502    /api/status  502    (502 = handed to an upstream, none there)
```

So the rule answers the reload path itself in every spelling I tried.
`/config` without the slash and upper-case paths go to the app, which has
no such route.

`tool_service` has no handler-level refusal; it relies on the loopback
port binding and on nginx proxying only `/api/solar/` to it. I sent
nothing to production's tool_service. In the throwaway container
`/api/solar/../config/reload` and `/api/solar/..%2Fconfig/reload` were
normalised by nginx before matching (they left the `/api/solar/`
location), but a 502 does not say which upstream was chosen, so that is
reasoning plus a partial test, not a live result.

**Concurrent reloads, live on dev.** 40 reloads on each dev service in
parallel batches while one chat request ran and `coordinator.md` flipped
ten times between two valid texts:

```
   chat finished: 318 chars: 'Tea is made by harvesting the leaves of the tea plant. Hot water is poured over these drie'
   orchestrator reload answers:  40 o 200 {"ok":true,"changed":[…]}
       5 "changed":["prompts/coordinator.md: changed (2339 -> 2346 chars)"]
       5 "changed":["prompts/coordinator.md: changed (2346 -> 2339 chars)"]
      30 "changed":[]
   tool_service reload answers:  40 {"ok":true,"changed":[]} t 200
```

Each of the ten transitions was reported exactly once. `reload()` is a
synchronous call inside an `async` handler on a single-worker uvicorn, so
two reloads cannot interleave, and a load is all or nothing. A reload
during a running request is finding 4.

## 6. Secrets (criterion 5)

My own scan of the nine files in `config/` (no symlinks, no other files):

- names: every key in `.env`, `.env.dev`, `.env.telemetry` and the three
  `.example` files, plus every `${NAME}` in the compose files (28 names),
  searched as whole words: no hit;
- values: every value of six or more characters from `.env`, `.env.dev`
  and `.env.telemetry`, searched as fixed strings without printing them:
  one hit, `TALKIE_MODEL` from `.env.dev`, whose value is the word
  "talkie". Not a secret;
- shapes: JWT, 32+ hex, 40+ base64, `sk-`/`pk-`, PEM headers,
  `password|secret|token|api_key|bearer|login|username` followed by `:` or
  `=`: no hit;
- words of 20+ characters: `remember_music_preference`,
  `repeat_tool_nudge_at`, `WEATHER_CTX_MAX_AGE_S`,
  `test_agent_tool_separation`. Identifiers;
- URLs, IPv4 addresses, e-mail addresses: none.

## 7. Tests, mutation check, replay

```
$ ./scripts/run_tests.sh -q
2757 passed, 2 skipped, 18 xfailed, 6 warnings in 17.74s
```

**Every commit** (suite and `tests/test_m21_pins.py`, scratch extract of
each commit, sockets blocked):

```
4f05264 pin prompts, tool menus and tuning values   | suite: 2699 passed, 2 skipped, 18 xfailed | pins: 5 passed
2a22ab4 config/ loader and reload; home prompt      | suite: 2723 passed, 2 skipped, 18 xfailed | pins: 6 passed
f35899e coordinator prompt                          | suite: 2723 passed, 2 skipped, 18 xfailed | pins: 6 passed
39a2cb9 assistant prompt                            | suite: 2723 passed, 2 skipped, 18 xfailed | pins: 6 passed
38870f2 coding prompt                               | suite: 2723 passed, 2 skipped, 18 xfailed | pins: 6 passed
bee33ed talkie prompt                               | suite: 2723 passed, 2 skipped, 18 xfailed | pins: 6 passed
fc62089 devops prompt                               | suite: 2723 passed, 2 skipped, 18 xfailed | pins: 6 passed
aea08f7 research prompt                             | suite: 2723 passed, 2 skipped, 18 xfailed | pins: 6 passed
a266f2b round budgets and the repeat-call nudge     | suite: 2735 passed, 2 skipped, 18 xfailed | pins: 6 passed
c8813f5 weather-context age                         | suite: 2740 passed, 2 skipped, 18 xfailed | pins: 6 passed
2a12b65 per-tool HTTP timeouts                      | suite: 2743 passed, 2 skipped, 18 xfailed | pins: 6 passed
cfb7e5f tool_service loader; verify window          | suite: 2754 passed, 2 skipped, 18 xfailed | pins: 6 passed
8ce16c7 resolver thresholds                         | suite: 2757 passed, 2 skipped, 18 xfailed | pins: 6 passed
6c6585c load at startup in tool_service             | suite: 2757 passed, 2 skipped, 18 xfailed | pins: 6 passed
5f97b93 log wording                                 | suite: 2757 passed, 2 skipped, 18 xfailed | pins: 6 passed
5f64c5d docs                                        | suite: 2757 passed, 2 skipped, 18 xfailed | pins: 6 passed
bc39131 build report                                | suite: 2757 passed, 2 skipped, 18 xfailed | pins: 6 passed
```

**Mutation check.** 53 single edits in a scratch copy of `bc39131`, the
whole suite after each, with a `sitecustomize` guard that refuses every
INET `connect`, `sendto`, `bind` and name lookup and logs the attempt. No
mutant opened a socket: every attempt was refused (878 over the 53 runs,
all from the pre-existing tests of finding 6, none from the three new
test files). 46 killed, 7 survived:

| Survivor | Edit | What it means |
|---|---|---|
| o6 | orchestrator `reload()` on a rejected file also calls `_set(defaults())` | "the last good values stay" is tested only from the shipped files, which equal the defaults, so falling back to the defaults is not told apart from keeping the last good values |
| s4 | the same edit in `tool_service/config_loader.py` | same gap |
| o10 | `_SECONDS` upper bound 600 → 6000 | no test feeds a timeout between 600 and 6000 |
| a4 | `weather_context`: `>` → `>=` at the age limit | the boundary age is not tested (it was not before the build either) |
| m2 | `config_loader.start()` removed from tool_service's lifespan | nothing tests that tool_service loads at startup rather than at first use |
| m4 | `_not_found_sentence` compares with a literal 0.5 | no test changes `hint_min_score` and looks at the sentence |
| r2 | `score()` compares coverage with a literal 0.80 | no test changes `coverage_min` and looks at a score |

Killed, among others: unknown keys accepted, types coerced, header left
in the prompt, body stripped of spaces, duplicate keys accepted, env
override ignored, any default changed, placeholder not filled, budgets not
applied, the reload refusal removed or weakened, one character changed in
a default prompt or in a prompt file, a trailing space added to a prompt
file, one number changed in either YAML file, a planted token or secret
name in `config/`, either nginx deny line removed, a mount made
read-write or dropped.

**Replay.** First run, stopped by the script's own player check after 110
of 122 rows: a television that was off went `unavailable` on its own.

```
$ python3 scripts/voice_replay.py --label m21-e2e
  F fail  executed   eval-look-for-some-good-deep-house-chill  [tool []]
  …
voice_replay: STOPPED: media_player state changed after eval-what-s-playing-on-the-kitchen: [{'entity_id': 'media_player.xbr_49x800d', 'before': {'state': 'off', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'unavailable', 'media_title': None, 'media_artist': None, 'media_content_id': None}}]
voice_replay: {'pass': 88, 'xfail': 18, 'skip': 2, 'fail': 2}; executed 42, checked read-only 66; players CHANGED: [{'entity_id': 'media_player.xbr_49x800d', …}] (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_042816_m21-e2e.json
```

Rerun, once, as the brief allows. It ran to the end:

```
$ python3 scripts/voice_replay.py --label m21-e2e-rerun
voice_replay: {'pass': 99, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_043258_m21-e2e-rerun.json
exit=0
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`,
row by row:

| Run | Baseline rows with the same status | Different | Not run | Rows not in the baseline |
|---|---|---|---|---|
| first (stopped) | 107 | 3 | 4 | 0 reached |
| rerun (complete) | 113 | 1 | 0 | 8 (`m4b-*`: 7 pass, 1 skip) |

- Rerun, the one difference: `inc-0930-search-deep-house-ytm` fail → pass.
  A known "YouTube Music" search flapper; the same change the build's
  replay showed.
- First run, the three differences: `inc-0930-search-deep-house-ytm` fail →
  pass (known flapper); `eval-look-for-some-good-deep-house-chill` pass →
  fail (a "YouTube Music" search row; the coordinator answered "I cannot
  search YouTube Music…" and called no tool); and
  `eval-shuffle-the-anime-bangers-playlist-on-sonos` pass → fail, **which
  is not on the known-flapper list**: the home model wrote the
  `play_music` call as JSON text instead of calling the tool (trace
  `ccda26736fcd865444f86eaa280817f1`). Both passed in the rerun and in the
  build's replay. The home prompt, menu and values that request received
  are the ones proved identical in section 2, and my mutation and
  bad-file runs were loading the host while the first run was going. I
  count it as a flap and name it so it can be added to the list or looked
  at.
- First run, not run because of the stop:
  `eval-what-s-playing-on-the-kitchen-2`,
  `eval-i-hope-you-play-anime-pop-radio`,
  `eval-we-play-justin-johnson-radio-on-pandora`,
  `eval-start-a-zzqx-florbent-radio-station-on`, and the eight `m4b` rows.
  All ran in the rerun.

The seven `m4b` mirror-update rows reach dev's own
`/magicmirror/update`, which answered 502 each time: `tool_service-dev`
has no `MM_*` variable, no `/keys` mount and no `ops/hosts.json`, so
nothing was sent to the MagicMirror Pi.

## 8. Findings

**1. Deeply nested YAML is not rejected; it crashes startup and turns a
reload into a 500. (Criteria 8 and 10.)**
Input: `config/tuning.yaml` containing `agent_loop: ` followed by 20000
`[` and 20000 `]` (40 kB), or `config/music.yaml` with `verify: ` and the
same brackets. The threshold is far lower: `RecursionError` from 500
nested brackets in the orchestrator's loader (PyYAML) and from 300 in
tool_service's (ruamel); at 200 both parse and reject normally.
Result at startup, scratch: `RecursionError: maximum recursion depth
exceeded` out of `import agents` (orchestrator) and out of the lifespan
(tool_service). Live on dev, both files in place, `docker restart` of the
two dev containers:

```
   + 5s  orchestrator-dev: running health=starting restarts=3 | tool_service-dev: running health=starting restarts=1
   +15s  orchestrator-dev: running health=starting restarts=6 | tool_service-dev: running health=starting restarts=4
   +30s  orchestrator-dev: restarting health=unhealthy restarts=8 exit=1 | tool_service-dev: restarting health=unhealthy restarts=7 exit=3
   GET /dev/api/status -> 502 ; tool_service-dev /health -> 000 ; /dev/api/config -> 502
RecursionError: maximum recursion depth exceeded
ERROR:    Application startup failed. Exiting.
```

Files restored, containers restarted: both healthy in 6 s, `source=files`.
Result on reload, live on dev: `500 Internal Server Error` from both
services, no file, no line; `/dev/api/config` showed both services
`ok=True` with no error; the last good values stayed and the services went
on answering.
Cause, from the code: `parse_yaml` catches only `yaml.YAMLError`
(`MarkedYAMLError`/`YAMLError` in tool_service), and `start()` and
`reload()` catch only `ConfigInvalid`.

**2. A YAML alias bomb is not rejected. (Criteria 8 and 10.) Scratch
only.**
Input, as `tuning.yaml` (326 bytes; for `music.yaml` the last line is
`verify: *i`):

```
a: &a [x,x,x,x,x,x,x,x,x]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]
e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]
f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]
g: &g [*f,*f,*f,*f,*f,*f,*f,*f,*f]
h: &h [*g,*g,*g,*g,*g,*g,*g,*g,*g]
i: &i [*h,*h,*h,*h,*h,*h,*h,*h,*h]
weather_context: *i
```

Result under a 6 GB address-space limit: orchestrator startup,
`MemoryError`, the process exits during import; orchestrator reload, `500
Internal Server Error` after 13.7 s with the status left at "ok";
tool_service startup, `MemoryError` in `_plain()`; tool_service reload,
no answer in 120 s (killed). The values in effect were kept in the cases
that answered. With fewer levels the file is "rejected", but the message
is the problem:

```
bomb, 4 levels (151 bytes):  rejected in 0.00s; message 34,531 chars
bomb, 6 levels (221 bytes):  rejected in 0.02s; message 2,790,151 chars
bomb, 7 levels (256 bytes):  rejected in 0.15s; message 25,110,673 chars; /api/config errors JSON 25,111,235 bytes
a 20 MB string as max_age_s: rejected in 5.57s; message 20,971,587 chars
```

Cause, from the code: both loaders build the message as
`f"{name}: {err['msg']} (got {got!r})"`, so the whole rejected value is
written into the error, the log line, the `config_error` event and the
`/services` data; tool_service's `_plain()` also copies every alias
before validation. Not run on dev, for the reason in section 4b.

**3. A large file blocks the service for the length of the reload.
(Brief, scope 3: "the service keeps answering".)**
Input: `tuning.yaml` with 50 MB of `# pad` lines followed by the normal
content with `default_s: 601`. Live on dev: the reload answered 422, with
the right line, after 27.83 s, and a `GET /dev/api/status` sent one second
into it answered after 28.3 s. Scratch: 22.6 s (orchestrator), 25.0 s
(tool_service, the same shape of file); the same time is spent at startup.
Cause: `reload()` runs synchronously inside the `async` handler and there
is no size limit. Related, scratch: a 30 MB prompt file is accepted
(`prompts/home.md: changed (3637 -> 31457280 chars)`) and would be sent to
the model as the system prompt.

**4. A reload during a running request mixes old and new values in that
request. (Brief, scope 4; no numbered criterion; not documented.)**
Scratch, one `run_stream` of the research agent, fake LLM that asks for a
`web_search` every round; during round 2 the files change (research
budget 8 → 2, nudge 3 → 2, `default_s` 15 → 99, a marker word in
`research.md`) and `config_loader.reload()` runs:

```
reload during round 2: {"ok": true, "changed": ["prompts/research.md: changed (1127 -> 1143 chars)", "tuning.yaml:agent_loop.max_rounds.research: 8 -> 2", "tuning.yaml:agent_loop.repeat_tool_nudge_at: 3 -> 2", "tuning.yaml:tool_timeouts.default_s: 15.0 -> 99.0"]}
  LLM calls made: 9 (old budget 8 -> 9 calls with forced synthesis; new budget 2 -> 3)
  prompt marker seen in any round: False | prompt still says 'budget of 8': True
  tool timeouts in effect per call: [15.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0]
  nudges present by round: [0, 0, 1, 2, 3, 4, 5, 6, 7]
```

The request kept the old prompt and the old budget and took the new
nudge threshold and the new timeout from its second tool call on. And a
coordinator request that delegates after a reload:

```
   ('coordinator', coordinator marker False, home marker False)
   ('home',        coordinator marker False, home marker True)
   ('coordinator', coordinator marker False, home marker False)
```

The coordinator ran on the old prompt; the specialist it called ran on
the new one. Cause: the prompt and the budget are read once at the start
of `run_stream`; the nudge threshold, the tool timeout and the weather
age are read through `config_loader.current()` at each use; a delegated
specialist reads its prompt when it starts. For values an operator edits
by hand between requests this hardly matters; step 2's `/settings` page
will reload while people talk to it.

**5. The inventory misses tuning values. (Criterion 1.)**
By search of `tool_service/` and `docker-compose.yml` against the names in
the build report. Not in the inventory, by name or by a wildcard row:

- `tool_service/solar.py:37-43`: `SOLAR_PRODUCING_FLOOR` 0.03,
  `SOLAR_FAIL_RATIO` 0.40, `SOLAR_MARGINAL_BAND` 0.15, `SOLAR_MIN_SAMPLES`
  8, `SOLAR_BAD_FRACTION` 0.70, `SOLAR_CONFIRM_DAYS` 3, `SOLAR_POLL_MIN`
  15. The inverter-health thresholds; the word "solar" occurs once in the
  build report, in a prompt;
- `tool_service/main.py:60`: `WEATHER_REFRESH_SEC` 3600 (also set in
  compose);
- `tool_service/taste.py`: `MUSIC_TASTE_BUILD_AT` 03:30,
  `MUSIC_TASTE_TAG_TOP_N` 300, `MUSIC_GENRE_ARTIST_RADIO`,
  `HISTORY_LIMIT` 500, `CONCURRENCY` 8;
- `orchestrator/telemetry.py:43`: `TEXT_PREVIEW_CHARS` 500.

The report says which directories it searched; `scripts/`,
`voice_tracer/`, `hottub_monitor/` and `ha/` were not among them. A
documentation fix.

**6. The suite makes network attempts, before and after the build.
(Criterion 16's last sentence; not caused by M21.)**
Under my socket guard the suite still passes, and the guard refused 35
attempts in one run of `bc39131` and 33 in one run of `ff99263`, from the
same tests:

```
 25  socket.getaddrinfo(b'fake-tools', 8003)        tests/test_agentic_loop.py (9 tests), test_error_surfacing.py (4),
                                                     test_retract.py (5), test_render.py (4), test_playback_control.py (1)
  3  socket.getaddrinfo(b'feeds.bbci.co.uk', 443)   tests/test_tool_service_weather.py (3 tests)
  3  socket.getaddrinfo(b'feeds.npr.org', 443)      tests/test_tool_service_weather.py
  2  socket.getaddrinfo(b'www.theguardian.com', 443) tests/test_tool_service_weather.py
  1  socket.getaddrinfo('localhost', 8123)          logged while tests/test_magicmirror.py::test_announce_is_non_fatal ran
  1  socket.bind(('::1', 0))                        at import or collection
```

Without a guard these are real lookups: the name `fake-tools` goes to the
resolver, and the weather tests start tool_service's lifespan, whose news
task tries the real feeds. The three new test files
(`test_config_loader.py`, `test_tool_service_config.py`,
`test_m21_pins.py`) made none, in the suite run or in any of the 53
mutant runs. `./scripts/run_tests.sh` as the brief had me run it has no
guard, so my one plain run made these lookups too.

**7. Test gaps shown by the mutation check.** The seven survivors in
section 7. The first two matter for this item: the property "after a bad
reload the last good values stay" is not tested from a state that differs
from the defaults.

## 9. Observations

1. **Timeout type.** From the files the per-tool timeout reaches httpx as
   a float (`15.0`), where the old constant was an int (`15`); from the
   built-in defaults it is still an int, and after an env override it is a
   float again. The value goes only to `httpx.AsyncClient(timeout=…)`, so
   nothing changes; the only 22 differing leaves in section 2.
2. **An emptied YAML file is valid.** 0 bytes, whitespace or comments
   only: `{"ok":true,"changed":[]}` and every value at its default. The
   file header says a key left out takes its default; a file that lost
   all its content by accident reports as healthy.
3. **Symlinks are followed.** In scratch, `tuning.yaml`, `music.yaml` or a
   prompt file that is a symlink to a valid file outside the config
   directory is loaded and takes effect. In the containers a link to a
   host path dangles ("file is missing"), since only `config/` is
   mounted; a link to a path that exists inside the container would be
   read.
4. **Two YAML dialects.** `tuning.yaml` is read by PyYAML (YAML 1.1) and
   `music.yaml` by ruamel (1.2). `max_age_s: 2:00:00` is accepted in
   `tuning.yaml` as 7200.
5. **Stray files are ignored silently.** `config/tunning.yaml` (a typo of
   the name) and `config/prompts/home.md.bak` load as "no changes". An
   unknown `prompts/*.md` is an error; an unknown YAML file is not.
6. **A prompt that is only a second comment is accepted**
   (`<!-- header -->` then `<!-- second comment -->`: 37 characters, sent
   as the prompt). NUL bytes in a prompt are accepted.
7. **The handler refusal keys on a non-empty header.** `X-Real-IP` with an
   empty value passes; nginx always sets it non-empty, so this is only a
   note. Any container on the compose network can reload either service,
   by design.
8. **`/dev/config/reload/`** (trailing slash) is answered by the app with
   a 307 to `/config/reload`. A client that follows it lands on
   production's root path, which the nginx rule will answer once loaded.
9. **`kronk-context.md` changed by one sentence**, so the text the
   assistant agent's `get_kronk_context` tool returns is one sentence
   longer than before the build. Intended (criterion 19), outside the
   pins; production reads its release copy.
10. **`scripts/config_reload.sh`** prints the HTTP code for the
    orchestrator on failure (`422 {…}`) and not for tool_service. Its
    exit status is right in both cases.
11. **Replay row outside the flapper list** failed once in the stopped
    first run: `eval-shuffle-the-anime-bangers-playlist-on-sonos`
    (section 7).

## 10. What I changed temporarily, and reverted

- `config/` in the working tree, on dev only: the edits of sections 4a,
  4b, 4c, 5 and finding 1, each restored from a byte copy taken before the
  first edit. At the end `sha256sum` of all nine files equals the copy,
  `/config` inside `orchestrator-dev` equals `HEAD:config/` file by file,
  there is no symlink in `config/`, and `git status` shows only the files
  of this report.
- `docker restart` of `kronk-orchestrator-dev-1` and
  `kronk-tool_service-dev-1`, by name, three times (section 4c, finding 1
  and its recovery). No rebuild: the images already matched the tree. No
  other container was restarted, stopped or reconfigured.
- One throwaway `nginx:1.29-alpine` container, `--network none --rm`,
  twice; gone.
- At the end: both dev containers up and healthy, running the working
  tree's code (md5 rechecked) and the committed `config/`, both
  `ok=True source=files`; `tool_service-dev` without `/keys`; `ops/` with
  only `hosts.example.json`; the three llama units, `wyoming-whisper` and
  the hot tub monitor active; production's containers untouched.

Not tested: the nginx deny rule in the running nginx (not loaded);
production's `/config/reload` in any form (not deployed, and out of
bounds); the alias bomb on dev; the reload path through `/api/solar/` to
production's tool_service.

Left behind, and departures from the brief I should own:

- `/tmp/kronk-taste-*`: `tests/conftest.py` makes one per pytest run; my
  runs (the suite, 17 commits, 53 mutants and the scratch renders) added
  94 empty-ish directories there. I left them, since they cannot be told
  from other runs' by name.
- My scripts used Python's default temp directory, so 208 small
  directories appeared directly under `/tmp` instead of the session
  scratch directory. Removed, file by file, with the rest of the scratch
  files; the scratch directory is empty.
- During the run I removed some scratch directories of my own with
  `rm -rf` and `shutil.rmtree` (scratch config copies and per-case work
  directories) before I reread the rule that asks for plain `rm -f` on
  named files. Nothing outside my scratch directory was touched; the final
  clean-up used `rm -f` on files and `rmdir`.
- One preflight command of mine (`docker inspect … | grep`) matched the
  `MA_TOKEN` line of `tool_service-dev`'s environment and printed the
  token into this session's tool output. It was not written to any file,
  to this report, or sent anywhere. If session transcripts are kept where
  others can read them, that token should be rotated.
- No safety control stopped a test.
