# M31 build report: retire devstral

Build agent `m31-build`, 2026-11-24. Criteria: `docs/sprint/M31-criteria.md`.

**Guard rails: none broken.** Two points the coordinator should know about:

- The devops check on the mirror went through dev's audited route as
  ordered (one request). Dev cannot reach the Pi by design
  (`docker-compose.yml`, the comment above `tool_service-dev`: no key, no
  `MM_SSH_*`), so the mirror did not answer. By the brief I then ran a
  read-only check on this host: one devops `http_check` of
  `http://tool_service-dev:8103/health` on the Docker network. A first
  attempt at that check routed to research instead (details below), so
  dev got three chat requests for devops in all, and one for coding.
- One `GET http://127.0.0.1:11438/props` (Gemma's server, loopback,
  read-only) for point 4.

No unit was stopped or touched, LiteLLM and production were not restarted,
no sound, no music request. The containers I recreated were
`kronk-orchestrator-dev-1`, by the dev up line. `tool_service-dev` was
already up to date and was not recreated. Production containers all
still showed "Up 3 hours" afterwards.

## What changed

Commit `d7e5582` (plus this report):

| File | Change |
|---|---|
| `docker-compose.yml` | production `orchestrator`: `CODING_AGENT_MODEL` and `DEVOPS_AGENT_MODEL` → `gemma-4-e4b`. `orchestrator-dev`: the same two lines added to its `environment:`, with a comment (see choice 1) |
| `.env.dev.example` | both variables → `gemma-4-e4b` |
| `orchestrator/agents.py` | `CODING_AGENT_MODEL` and `DEVOPS_AGENT_MODEL` default to `COORDINATOR_MODEL`, as `RESEARCH_AGENT_MODEL` does |
| `orchestrator/settings_view.py` | the two rows' "default in code" → `COORDINATOR_MODEL` |
| `litellm/config.yaml` | `devstral-2512-q4` entry removed |
| `systemd/llama-devstral-q4.service` | removed (the reference copy; the installed unit is untouched) |
| `nginx/nginx.conf` | the `/devstral/` block (proxied to 11436, where nothing has listened since M24) removed |
| `scripts/upgrade_rules.toml` | `llama-devstral-q4` dropped from the llama.cpp `units` |
| `kronk-context.md` | the server row gone, gemma's row lists coding and devops, "two GPU units" → "the GPU unit", a retirement line; agent table models |
| `README.md` | `/devstral/` sentence, server table row, "three are the whole catalog" → two, agent table models, install line, "each GPU unit" / "two GPU model servers" → one, a retirement paragraph with the bench reference, the kept model file under "Model storage" |
| `docs/features/model-locks.md` | the "devstral never waits for gemma" example → Talkie; a line that coding and devops now share gemma's lock; the server example |
| `docs/features/upgrade-cadence.md` | the "`/devstral/` traffic" bullet removed (the route is gone) |
| `TECH_DEBT.md` | the template leftover rewritten: no unit uses it; kept on disk as the way back |
| `tests/test_m24_catalog.py`, `tests/test_m25_resources.py`, `tests/test_servers_gpu_mem.py` | the assertions that read the real catalog or `systemd/` now expect two models |
| `tests/test_m31_devstral_retired.py` | new, 9 tests (below) |

### Choices made

1. **Dev override in compose.** `.env.dev` (gitignored, not mine to edit)
   still says `CODING_AGENT_MODEL=devstral-2512-q4` and
   `DEVOPS_AGENT_MODEL=devstral-2512-q4` (lines 26–27). `environment:`
   wins over `env_file:`, so I set both in `orchestrator-dev`'s
   `environment:` block. That is the "for dev in `docker-compose.yml`" of
   plumbing point 1. The cost: on dev, these two are no longer
   per-experiment knobs in `.env.dev`. To try another model for them on
   dev, edit the compose lines. Drew can drop or change the two
   `.env.dev` lines whenever; they have no effect now.
2. **Fixture tests kept.** `test_servers_gpu_mem.py`'s `CATALOG`, `_gpu`
   and `_props`, plus `test_m9_lock.py`, keep `devstral-2512-q4` as a
   fixture: a second server with a large context and no draft. Only the
   assertion that reads the real `litellm/config.yaml` changed.
   `test_m25_resources.py`'s `CAT` changed, because
   `test_load_catalog_gives_name_api_base_and_port_only` asserts it equals
   the real catalog. Its "rows older than the window" test now uses `talkie`.
3. **ROADMAP left alone.** M31 has no roadmap line. Past items got their
   Shipped line at promotion (M24: `73a5e33`), so I left it for the
   coordinator.

### New tests (`tests/test_m31_devstral_retired.py`)

- no devstral entry or port 11440 in the catalog;
- both compose orchestrators set coding and devops to `gemma-4-e4b`;
- every `*_MODEL` value in any compose service and in `.env.dev.example`
  is a model the catalog serves;
- the research, coding and devops defaults in `agents.py` are the name
  `COORDINATOR_MODEL` (AST check);
- `/settings` shows the same defaults;
- no `/devstral/` or `11436` in `nginx.conf`;
- no devstral unit in `systemd/` or `upgrade_rules.toml`;
- criterion 3: `build_servers` on the real catalog with fixtures without
  devstral shows exactly two rows, both running and answering, no reasons
  other than Talkie's "no draft model", and only Whisper under other
  processes;
- the window between the deploy and the unit stop: devstral still running
  shows under `other_processes` as "llama unit not in the catalog", with
  its memory, not as a catalog row.

## Plumbing points

1. **Done.** See the table. Live: `docker exec kronk-orchestrator-dev-1
   printenv CODING_AGENT_MODEL DEVOPS_AGENT_MODEL` → `gemma-4-e4b`,
   `gemma-4-e4b`. `/dev/api/agents` lists Coding Agent and Devops Agent
   on `gemma-4-e4b`.
2. **Done.** Catalog entry, reference unit, nginx block, kronk-context and
   README rows, and the `upgrade_rules.toml` entry. All five places existed.
3. **Kept.** `scripts/devops_model_bench.py`, `scripts/m9_lock_bench.py`
   and `docs/bench/` are unchanged.
4. **Gemma's server:** see "Point 4" below.
5. **Not done, as ordered.** `llama-devstral-q4.service` is still running
   and holds 36,519,350,272 bytes of GPU memory (34.0 GiB, from
   `/dev/api/servers` `other_processes`, 2026-11-25 03:42Z).
6. **Kept.** No model file or template touched. README "Model storage"
   and TECH_DEBT now say they stay as the way back.

## Criteria

### 1. Devops and coding answer on dev, on gemma — PASS (devops via the fallback)

Dev pair rebuilt with `docker compose --profile dev up -d --build
tool_service-dev orchestrator-dev`. The orchestrator came up healthy.

- **Devops, the mirror** (`/dev/api/chat`, 03:40:12Z, "check the uptime on
  the mirror"). Route `default` → coordinator → `ask_devops` → devops
  round `plan_1` on `gemma-4-e4b` → `remote_exec` → `POST
  http://tool_service-dev:8103/ops/exec "HTTP/1.1 404 Not Found"`. The
  tool result the model saw (`orchestrator/tools.py` `_fail`):
  `[remote_exec on magicmirror failed (HTTP 404): unknown host
  'magicmirror' — known hosts: (none configured)]`. Answer, 6.72 s:
  "The host 'magicmirror' is currently unreachable, so I cannot retrieve
  the uptime." The mirror did not answer, because dev has no route to it
  by design. See finding 1 on the wording.
- **Devops, read-only check on this host, attempt 1** (03:41:08Z, the
  prompt held `http://tool_service-dev:8103/health`). Routing rule `url`
  sent it to research, which has no `http_check`. Answer: "I cannot run
  that HTTP check because the `devops` specialist does not have web
  access." The rule did its job; the prompt was wrong.
- **Devops, read-only check on this host, attempt 2** (03:41:28Z, "is the
  dev tool service up? have devops do an http check on tool_service-dev
  port 8103, path /health"). Route `default` → `ask_devops` → devops on
  `gemma-4-e4b` → `http_check` → `GET
  http://tool_service-dev:8103/http_check?url=http%3A%2F%2Ftool_service-dev%3A8103%2Fhealth
  "HTTP/1.1 200 OK"`. Dev's audit log (`data/dev/ops_audit.log`):
  `{"ts": 1795578092.844, "host": "tool_service-dev", "command":
  "http_check GET http://tool_service-dev:8103/health", "allowed": true,
  "exit_code": 200, "out_len": 0, "note": "HTTP 200"}`. Answer, 7.17 s:
  "The dev tool service is up. The HTTP check returned a 200 status
  code." The answer contains what the check returned.
- **Coding** (03:41:48Z, "ask the coding agent: write a python function
  that checks whether a string is a palindrome, ignoring case and
  spaces"). `ask_coding` → coding `synthesis` on `gemma-4-e4b`, 850
  completion tokens in 6.72 s. Answer, 11.13 s total: a correct
  `is_palindrome` (`"".join(c for c in text if c.isalnum()).lower()`,
  compared with its reverse) and three examples.
- **`data/dev/metrics.db`**, rows from 03:40:00Z on (read-only):

  ```
  ('2026-11-25 03:40:15', 'coordinator', 'gemma-4-e4b', 2203, 277)
  ('2026-11-25 03:40:16', 'devops', 'gemma-4-e4b', 674, 63)
  ('2026-11-25 03:40:17', 'devops', 'gemma-4-e4b', 740, 137)
  ('2026-11-25 03:40:18', 'coordinator', 'gemma-4-e4b', 2259, 81)
  ('2026-11-25 03:41:11', 'research', 'gemma-4-e4b', 768, 298)
  ('2026-11-25 03:41:15', 'coordinator', 'gemma-4-e4b', 2300, 279)
  ('2026-11-25 03:41:30', 'coordinator', 'gemma-4-e4b', 2227, 304)
  ('2026-11-25 03:41:32', 'devops', 'gemma-4-e4b', 695, 152)
  ('2026-11-25 03:41:34', 'devops', 'gemma-4-e4b', 808, 136)
  ('2026-11-25 03:41:35', 'coordinator', 'gemma-4-e4b', 2317, 82)
  ('2026-11-25 03:41:50', 'coordinator', 'gemma-4-e4b', 2219, 65)
  ('2026-11-25 03:41:57', 'coding', 'gemma-4-e4b', 401, 850)
  ('2026-11-25 03:41:59', 'coordinator', 'gemma-4-e4b', 2855, 228)
  devstral rows on dev since 03:40: 0
  ```

### 2. No request reaches port 11440 — PASS

`journalctl --user -u llama-devstral-q4 -o cat | wc -l`: 357063 before the
rebuild (22:39:28 local) and 357063 after the last turn.
`journalctl --user -u llama-devstral-q4 --since "2026-11-24 22:39:00"` →
`-- No entries --`. The server's last line was at 19:34:54 local, three
hours before the work started.

### 3. Pages show two model servers plus Talkie's row, no devstral row, no fault — PASS

"Two model servers plus Talkie" reads as two rows, gemma and Talkie: the
catalog has two entries.

- Live `/dev/api/servers`: `gemma-4-e4b running answering` (agents:
  Coordinator, Research, Home, Assistant, Coding, Devops; n_ctx 32768,
  4 slots; reasons `{}`) and `talkie running answering` (reasons only
  "the server runs no draft model"). `other_processes`:
  `llama-devstral-q4` "llama unit not in the catalog" (36,519,350,272
  bytes, still running until the coordinator stops it), both Whisper
  units. `/dev/resources` and `/dev/services` answer 200.
  `/dev/api/upgrades`, `/dev/api/backups`, `/dev/api/config` and
  `/dev/api/agents` do not name devstral.
- **Fixture with the unit stopped.** I wrote
  `gpu_mem.json` and `llama_props.json` to scratch as copies of the live
  files without port 11440, freshly dated. I ran the orchestrator app
  on them (TestClient, with socket connect refused, the real
  `litellm/config.yaml`). Output:

  ```
  servers: [('gemma-4-e4b', 'running', 'answering', ['Coordinator', 'Research Agent', 'Home Agent', 'Assistant Agent', 'Coding Agent', 'Devops Agent'], {}), ('talkie', 'running', 'answering', ['Talkie Agent'], {'draft_file': 'the server runs no draft model', 'draft_bytes': 'the server runs no draft model'})]
  other: [('wyoming-whisper-dev', 'speech to text'), ('wyoming-whisper', 'speech to text')]
  sources: {'gpu_mem': ('fresh', None), 'llama_props': ('fresh', None)}
  system: 200 {'gpu_reasons': {}, 'disk_reason': None}
  activity: ['gemma-4-e4b', 'talkie'] not_in_catalog: []
  ```

  The same case is pinned in
  `test_with_the_unit_stopped_two_servers_and_no_fault`. The script and
  fixtures are in the session scratchpad
  (`m31_fixture_check.py`, `m31-fixture/`) and are not part of the repo.
- `/api/activity`'s `not_in_catalog` lists only models with calls in the
  last 24 hours. Dev had no devstral call in that window. Production's
  last was 2026-08-29 (criteria file), so no devstral row shows there.
  A devstral call inside the window would show as "not in catalog": a
  true fact, not a fault.

### 4. No model the catalog lacks in config_check or the deploy plan — PASS

- `tests/.venv/bin/python scripts/config_check.py --code . --dir
  ../kronk-config/prod` → `valid`, exit 0; the same on `dev` → `valid`,
  exit 0. (The system `python3` cannot run it, because it lacks pydantic;
  `deploy_tag.sh` uses `tests/.venv/bin/python` through `CHECK_PY`.)
- The only model the config directories name is `music.yaml`
  `model: gemma-4-e4b`, the same in prod, dev and `config/`. `grep -i -E
  'devstral|11440'` over both live directories finds nothing.
- `deploy_tag.sh` (read, not run): `plan`/`deploy` run `config_check.py`.
  They refuse a service list that holds HA or MA, and after a deploy they
  check `/api/status` and the `/api/chat` shim with `"model":
  "kronk:latest"`, which goes to the coordinator. None of these checks
  reads the catalog or names a model, so nothing there names a model the
  catalog lacks. The new
  `test_every_model_compose_and_the_dev_example_name_is_in_the_catalog`
  covers what the deploy does not.

### 5. `nginx -t` — PASS

```
docker run --rm --network kronk_kronk --add-host host.docker.internal:host-gateway \
  -v "$PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:1.29-alpine nginx -t
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
exit 0
```

### 6. The suite — PASS

`./scripts/run_tests.sh -q`, last lines:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
7796 passed, 3 skipped, 54 xfailed, 6 warnings in 122.26s (0:02:02)
```

## Point 4: Gemma's server against a devops and a coding turn

From `/props` (b9611-02182fc5b) and `data/llama_props.json`:
`total_slots` 4, `n_ctx` 32768. Each slot reports the full 32768
(`default_generation_settings.n_ctx`), as the M9 finding recorded. With 4
slots that means one unified KV cache: one request can use up to 32,768
tokens, and all slots held at once share those 32,768. That reading
follows from the numbers; `/props` has no `kv_unified` field to show it
directly.

Round budget for both agents: `max_rounds.default: 3`, the same in prod
and dev (`research: 8` is the only override). That is up to 3 tool rounds
plus one forced answer with tools off.

| Turn | Measured on dev today | Worst case at the round budget | Fits 32,768? |
|---|---|---|---|
| devops | 674–808 prompt tokens per call; coordinator 2,203–2,317 around it | system ~670 tokens + 3 `remote_exec` results capped at 8,000 chars each (`tool_service/main.py`, `text[:8000]`; about 2,300 tokens each) ≈ 7,600–8,000 in the last call. `http_check` returns no body. With the coordinator's own context resident (prod max 8,882 tokens over 1,349 calls in `data/metrics.db`) ≈ 17,000 | yes |
| coding | 401 prompt + 850 completion; coordinator 2,855 after it | system ~400 + 3 `fetch_url` results capped at `FETCH_TOKEN_LIMIT = 4000` ≈ 12,400, plus a ~1,000-token reply ≈ 13,400. With the coordinator resident ≈ 22,300 | yes |

History on devstral (production `metrics.db`, read-only): devops 20
calls, largest 1,176 tokens; coding 30 calls, largest 4,900.

So one turn of either agent fits, with room to spare. What does not fit
by this arithmetic is two worst-case nested turns resident at once, for
example a worst-case coding turn on production alongside one on dev: about
44,600 against a 32,768 pool. Research at its 8-round budget (8 × 4,000)
could already fill the pool alone before M31, and M31 does not change
that. Reported, not tuned, per point 4.

Latency, which follows from the shared lock: coding and devops now take
gemma's model lock (`docs/features/model-locks.md`, updated). Voice
waiters still go first in the queue, but a running generation is never
interrupted. A voice request that arrives during a coding generation
waits for it to finish: 6.72 s for 850 tokens today. Before M31 these two
agents were on their own server and lock.

## Devstral hits left, and why

`grep -rn -i devstral` outside `docs/bench`, `docs/sprint`, `docs/plans`,
`docs/incidents`, `docs/HISTORY.md` (and `.git`, `data/`, `tests/.venv`):

| Hit | Why it stays |
|---|---|
| `docker-compose.yml:450` | the comment on the dev override names what a pre-M31 `.env.dev` holds |
| `README.md:246,248,251,591` | the retirement history paragraph, the bench file's name, and the kept model file |
| `kronk-context.md:53` | the retirement line |
| `TECH_DEBT.md:71–73` | the template file's real path, and that it stays |
| `ROADMAP.md:186,327` | Shipped entries (M24, the K2 bench): history |
| `Kronk evaluation findings and implementation plan.md:102,126,433` | a dated evaluation: history |
| `scripts/devops_model_bench.py`, `scripts/m9_lock_bench.py` | bench scripts, kept by point 3 |
| `scripts/gpu_mem_export.py:12` | the measurement that is the reason for reading fdinfo; still true as history |
| `tests/test_error_style.py:45` | a friendly error must not name a model; the name in the regex costs nothing |
| `tests/test_m9_lock.py:224–234`, `tests/test_servers_gpu_mem.py:28–143` | fixture strings for "a second server" (choice 2) |
| `tests/test_m24_catalog.py:7,39` | docstrings: history |
| `tests/test_m31_devstral_retired.py` | its subject |
| `docs/model_results.md`, `docs/VOICE_SETUP.md`, `docs/REVIEW_2026-10-09_consolidation.md`, `docs/history/FINANCIAL_EXPERT_PLAN.md`, `docs/upgrades/2026-11-18.md`, `docs/upgrades/inventory-2026-11-18.md` | dated results, a build journal, a dated review, a retired plan, dated upgrade reports: history. (`EXTRACTION_MODEL` in the financial plan exists nowhere in code.) |

Outside the repo and not mine: `.env.dev` lines 26–27 (choice 1).

## Findings, not acted on

1. **The devops answer lost the cause.** The tool said `unknown host
   'magicmirror' — known hosts: (none configured)`, and gemma said
   "currently unreachable". That answer is true and short, but it is less
   specific than the tool's cause (tenet 7). This is one sample, and dev
   cannot show what devstral would have said, since dev never had the Pi.
   Worth a look on production after the deploy: one read-only "check the
   uptime on the mirror", which is the coordinator's.
2. **`COORDINATOR_MODEL`'s default in code is still `mistral-nemo:12b`**
   (`orchestrator/agents.py:209`, `settings_view.py:30`), a server retired
   in M24. Every compose service sets `COORDINATOR_MODEL`, so nothing runs
   on that default. Still, the new coding and devops defaults inherit a
   name no server has, so "no default names a server that does not exist"
   holds only while the env var is set. The plumbing named
   `COORDINATOR_MODEL`'s way of defaulting, so I left that one value
   alone. Making it `gemma-4-e4b` is a one-word change, if the
   coordinator wants it.
3. **When LiteLLM drops the entry.** The release directory supplies
   `litellm/config.yaml`, so the devstral entry leaves LiteLLM when the
   deploy recreates the `litellm` container on the new release's mount.
   I expect compose to recreate it because the bind-mount source path
   changes per tag. That is inferred from the compose file, not
   verified. If `deploy_tag.sh status` shows `litellm` on the old tag
   after the deploy, LiteLLM still lists devstral.

## Not done

- Plumbing point 5 (stop and disable the unit, move the installed unit
  file, read the freed memory): the coordinator's.
- The ROADMAP Shipped line (choice 3).
- No bench, by Drew's order.
