# Kronk codebase review — 2026-10-09 (consolidation, refactoring, efficiency)

**Status: recommendations only — nothing here has been implemented.**

Method: six parallel full-read reviews (orchestrator transport; agents/tools/
routing; tool_service; health + finance services; static UI; infra/scripts/
tests), each judged against the `CLAUDE.md` tenets and `ROADMAP.md`. The
reviews were consolidated, deduplicated, and every finding that changed a
rating or claimed a behavior was re-verified by hand against the source (and
in one case measured — see E1). File:line references are as of commit
`1504f7c` with a clean tree. The 2026-07-05 review
(`docs/REVIEW_2026-07-05_codebase.md`) is the prior pass; two of its P2 items
are still open and are called out below.

**Verdict.** The architecture holds; nothing here recommends a service split,
a framework, or a new dependency. The findings cluster into four patterns:

1. **Finished consolidations that stopped halfway.** `static/kronk.css` and
   `static/js/kronk.js` exist (2026-06-12) but the pages never adopted them;
   `_fail()` and `_get_state()` exist in `tools.py` / `tool_service/main.py`
   but half the handlers hand-roll the same thing.
2. **The router removal (2026-08-18) left remnants** across five files:
   `routing_hint`, `roster()`'s "agent bypass" text, `servers.py`'s "Router"
   label, `/probe/ollama*` callers in two pages, `agents.html`'s model names.
3. **The test gate can't be rebuilt from its own file**, and three transports
   have uneven coverage (`/v1/chat/completions` has no endpoint test; no test
   asserts any UI page's fetch targets exist).
4. **Local-first leaks on the main page**: the chat UI loads two unpinned
   libraries from a CDN and will not render without the internet.

Approximate line reduction if every consolidation below lands: ~1,250 lines
of dead UI, ~300 lines of duplicated CSS, ~250 lines of bench-script
duplication, ~100 lines in tool_service music, ~150 in orchestrator, ~150 in
health_service. That is roughly 2,200 of 28,600 tracked lines, with no
behavior change intended in any of it.

---

## 0. Correctness-adjacent findings surfaced by the review

Not the ask, but found while reading, and several are cheaper to fix inside
the consolidation that touches them. Listed so they don't get lost.

| # | Where | What | Tenet |
|---|---|---|---|
| X1 | `orchestrator/main.py:727-742` | OpenAI **non-stream** path collects by re-parsing its own SSE and drops `retract` events, so tool-round text leaks into the reply. `_ollama_collect` (836-848) does it right. No endpoint test exists for `/v1/chat/completions`. | 7 |
| X2 | `main.py:718` + `:290` | The OpenAI shim mints `chatcmpl-<rid>`, then `_run_pipeline` mints a second rid. The id the client sees is not the one in logs or Langfuse. | 7 |
| X3 | `main.py:340, 402, 487, 490-499` | `request_complete` is emitted on two exit paths and skipped on routing failure and the crash handler. Move to the `finally` at 501. | 7 |
| X4 | `tool_service/main.py:1277, 1409, 1503` | Four of five HA state reads `.json()` the body without checking status; a 401/404 reads as "not playing". Only `now_playing` (1603-1605) checks. | 6, 7 |
| X5 | `orchestrator/tools.py:652, 779, 883` | `fetch_url`, `generate_diagram`, `query_hottub` bypass `_fail()`; hottub returns `"[Hot tub status unavailable]"` with no cause. | 7 |
| X6 | `orchestrator/static/agents.html:221`, `services.html:282` | Poll `/probe/ollama-ps` and `/probe/ollama` every 30 s. Neither route exists in `nginx.conf` or `main.py`; the VRAM labels read "checking…" forever and the ollama dot is always red. | 2 |
| X7 | `orchestrator/static/index.html:7-8` | `marked@15` and `dompurify@3` from jsdelivr, unpinned majors; the chat UI's first script line throws without them. `performance.html:7` loads chart.js from CDN while `/static/chart.min.js` is already vendored. | 1, 3 |
| X8 | `tool_service/solar.py:725` | `heatmap` hardcodes `0.7` where the rollup uses `BAD_FRACTION` (`:457`); the env knob doesn't reach the dashboard. | 8 |
| X9 | `health_service/main.py:1003` | `/api/reindex` does a bare `from db import get_conn`, which the module's own header bans; fails only at call time, and nothing calls it. Still open from the July review (P2.7). | 8 |
| X10 | `tests/requirements.txt` | Lists 10 packages, two unpinned. The venv actually needs nine more (langfuse, pyyaml, websockets, trafilatura, bs4, lxml, garminconnect, curl_cffi, uvicorn). A fresh `tests/.venv` from this file would not collect. `run_tests.sh:4` even names langfuse. | 3 |
| X11 | `orchestrator/static/services.html:137,149,175,187,212` | "Open" links hardcode `http://kronk.local:8000/` and `:8080`; only port 80 is published. | 2 |
| X12 | `finance_service/db.py:34` | `ON DELETE CASCADE` with `PRAGMA foreign_keys` never enabled; `delete_document` (94-98) deletes chunks by hand. The DDL lies. | 8 |

---

## 1. Dead code and stale remnants (delete; no behavior change)

| # | Item | Evidence | Effort |
|---|---|---|---|
| D1 | `llm.complete()` | `orchestrator/llm.py:131-189`; zero callers repo-wide; duplicates the tool-arg JSON recovery in `stream()` (111-120 vs 165-173). | S |
| D2 | `_execute_tool`, `from tools import TOOL_DEFINITIONS` in `main.py:29, 83-85` | Test-only compat; `test_agentic_loop.py:84, 682-754` should target `tools.execute` / `tools.TOOL_DEFINITIONS`. | S |
| D3 | `AgentConfig.routing_hint` | `agents.py:167` and every entry; comment at 509-512 says it "remains as documentation"; no non-test reader. | S |
| D4 | `roster()` coordinator entry | `agents.py:936-939` hardcodes a description mentioning "agent bypass" (gone 2026-08-18) that differs from `COORDINATOR` at 454-459. Derive from `COORDINATOR`. | S |
| D5 | `servers.py:120-122` | Still maps `ROUTER_MODEL` → "Router". | S |
| D6 | `COORDINATOR_MODEL` alias | `main.py:47` vs `agents.COORDINATOR.model` (480): two spellings of one fact. | S |
| D7 | health_service dead symbols | `main.py:11` `BackgroundScheduler` never instantiated; `:14` `StaticFiles` never mounted; `:28/40` `sync_withings` never called; `sync_garmin` (49-52) never called. The stub *endpoints* are intentional (TECH_DEBT) and stay. | S |
| D8 | `orchestrator/static/agents.html` + route `main.py:99-100` | Hand-maintained copy of `AGENTS` naming `qwen3:14b` / `mistral-small3.2:24b` (`:122,145,165,184`); `services.html:293-309` already renders from `/api/agents`. | S |
| D9 | `health_service/static/index.html` (973 lines) + `health_service/main.py:67-70` | Served on unpublished port 8004; expects a `/health-ui` prefix (`:444-445`) that no nginx location provides; private copy of the whole theme; Chart.js from cdnjs. `orchestrator/static/health.html` is the live page. **Not pure overlap** — the dead page has bloodwork PDF upload (`:891-913`) and body composition (`:410-440`) the live page lacks. **Operator call: port those two or drop them.** `README.md:50` still credits the dashboard to health_service. | S delete / M port |
| D10 | `scripts/model_bench.py` | Ollama-era: calls `localhost:11434/api/chat` (`:17,28`), Ollama tag names, hardcoded recommendation prose (`:192-239`). Only `docs/HISTORY.md:111` references it. | S |
| D11 | `scripts/benchmark_models.py` | Writes to `/tmp/bench_results.json` (`:192`), never `docs/bench/`; hardcoded ports incl. unassigned models; last touched 2026-06-12. | S |
| D12 | `kronk.js` `pollEvery` / `fetchJSON` | `static/js/kronk.js:7-17`, zero callers. Adopt (see §5) or delete. | S |
| D13 | Stale comments | `main.py:50-53` (SYSTEM_PROMPT env history); `tool_service/Dockerfile:3-4` ("forced-command key" — `main.py:773-774` says it's general-purpose now); `.gitignore:1-3` (`tmp`, `input`, the PDF). | S |
| D14 | `tools.py` `query_bloodwork` / `search_health_data` | Parked, not dead: ROADMAP "Health RAG completion" owns them. Leave. ~95 unreachable lines, no risk. | — |

Runtime remnants (not code, but found while verifying the docs):

- `llama-bonsai` is **running** (1.1 GB GTT) with no consumer anywhere in
  `orchestrator/*.py` or compose. `llama-devstral` (Q8, 11436),
  `llama-mistral-nemo`, `llama-talkie` are disabled but still in
  `litellm/config.yaml:13-35,72-81`; `nginx.conf:51-56` proxies `/devstral/`
  to the dead 11436. **Operator decision per unit.**
- `kronk-hottub-monitor.service` is enabled and running against a feature
  TECH_DEBT calls parked, and has no reference copy in `systemd/`.
  `kronk-perfwatch.service` is the inverse: repo copy, disabled, undocumented.
- A stray `ma-test` container (stock MA 2.11.0b2, cmd `infinity`, since
  2026-10-05) is in no compose file; `docs/plans/MA_LOCAL_PANDORA_FEATURES_PLAN.md:32`
  calls it a dev scaffold. **Operator confirmation** before `docker rm -f` —
  it is not the production MA container, but it is MA-adjacent.

---

## 2. Consolidation — orchestrator (`main.py`, `agents.py`, `tools.py`)

Test baseline: `/message` is endpoint-tested (`test_agentic_loop.py:269-668`,
`test_error_style.py`, `test_retract.py`); `/api/chat` and `/voice/api/chat`
stream + non-stream are pinned by `test_render.py:106-133`;
`/v1/chat/completions` has **no endpoint test**; `run_stream` has 29 test
references and terminal/retract/escalate are all covered.

| # | Finding | Impact / Effort | Risk |
|---|---|---|---|
| O1 | **Ollama envelope built three times.** `main.py:689-708`, `859-871`, `902-912` — same dict, same timestamp literal at 685/858/904. One `_ollama_msg(...)`. ~35 lines. This is the voice wire contract. | M / S | Low; test_render pins both shapes. |
| O2 | **OpenAI non-stream frames SSE then un-frames it** (`727-742`). Collect via the `_ollama_collect` helper (rename `_collect_reply`) for both shims. Fixes X1. Also removes the duplicate `_estimate_tokens` at 755-757 and the never-read `model` param on `_kronk_pipeline_tokens` (629-653). | M / S | Lands with the first `/v1/chat/completions` test (pattern at `test_retract.py:160`). |
| O3 | **Shim preamble duplicated** (`713-719` vs `878-883`): last user message, 400, model, `_shim_context`. `_OpenAIMessage`/`_OllamaMessage` differ only in `stream` default and `extra="allow"`. | L / S | Low. |
| O4 | **Three "clear history" sites**: `219-228`, `639-645`, `947-951`; same string, same two clears. One `_clear_session()`. | L / S | `pipeline_bench.sh:96-118` calls `DELETE /history`; behavior unchanged. |
| O5 | **Two `run_stream` consumer loops in `_run_pipeline`** (`360-380` specialist, `453-477` coordinator): `narration`/`token`/`retract` branches byte-identical. A small `_ReplyAccumulator` or `_forward_common(ev, state)`. Together with X3 and the two timing-payload shapes (`396-401` vs `480-486`) this takes `_run_pipeline` from 233 lines to ~150. | M / M | Voice path; covered indirectly by every `/message` test; run the bench after. |
| O6 | **`run_stream` is 330 lines** (`agents.py:570-901`); the forced-synthesis tail duplicates round-end bookkeeping: stream-error `660-666` vs `852-858` (identical), `gen.end`+`emit`+`metrics.record` `669-693` vs `872-892`, "no response" `698-699` vs `895-896`. Extract `_end_round(...)`, `_stream_error(...)`, and a `_forced_synthesis` sub-generator. ~60 lines. Dead derivation at 870/894 falls out. | M / M | Structural-guardrail path (terminal, retract). Full `test_agentic_loop` + `test_retract` + `test_error_surfacing` + bench pre/post. One variable at a time: do not combine with O10. |
| O7 | **Weather fetched twice per pinned-home request, once wastefully on every pinned request.** `main.py:316` fetches before routing for the coordinator; a pinned specialist returns at 404 without using it; the home agent fetches again at `agents.py:612-615` behind a hardcoded `agent.name == "home"`. Fetch once, pass via `system_extra`, move the flag onto `AgentConfig`. 2 s-timeout local HTTP call on the voice path. | M / S | One test patches `agents.weather_context`. |
| O8 | **Coordinator tool-defs monkey-patch** (`agents.py:492, 503-506`): sets `tool_names=[]` then mutates and reassigns `COORDINATOR.tool_defs` with a `type: ignore`. Make `tool_defs()` resolve against `TOOL_DEFINITIONS + agent_tool_defs()` and declare the names. | L / S | 15 test refs, all via `run_stream`. |
| O9 | **Origin-stamp + failure-tail copy-pasted across four music handlers.** `tools.py:901-906/931-936/958-963/982-987` (identical origin stamp); `try: resp.json().get("detail") except: resp.text[:200]` seven times (`913, 940, 967, 991, 1028, 1051, 1064`) — what `_fail()` already does at 604-608. `_with_origin()` + `_detail()`; `_fail` calls `_detail`. Fixes X5. Keep every `"[Music playing: "` style prefix byte-identical — `_terminal_speech` matches on them. ~45 lines. | M / S | Handlers unit-tested by prefix string. |
| O10 | **Terminal-tool string contract lives in three places** (tenet 8): emitted in `tools.py` (912…1033), parsed in `agents.py:104-129`, declared in `terminal_tools` at `agents.py:282, 491`. Nothing checks a new terminal tool's prefix is mapped; the fallback at 134 only warns at runtime. One `TERMINAL_SPEECH` table beside the handlers. | M / M | **This is the voice guardrail.** 14 `_terminal_speech` test refs, all input→output; diff the 10 mappings one-to-one. Do after O6 lands. |
| O11 | `_tool_narration` 40-line if-chain (`agents.py:55-94`) → dict keyed by tool name, or a `narration` key on each definition. | L / S | 3 tests. Bundle with O10. |
| O12 | `routing._classify_inner` (`routing.py:204-236`) 11 `if RE.search: return` blocks → ordered `_RULES` table; add import-time `assert route in AGENTS or route == "direct"` (routes are bare literals today; a typo falls silently to the coordinator via `main.py:349`). | L / S | 14 refs use `(route, rule)` tuples, preserved. |
| O13 | `telemetry.end_pipeline` updates the root three times (`telemetry.py:186-191`); `update_trace` accepts both args. `telemetry._current_root` is a module global guarded by a comment about `_llm_lock` while `events` and `origin` use ContextVars — make it one. | L / S | No-op unless LANGFUSE_ENABLED. |
| O14 | `sessions.append` called twice per turn (`main.py:512-513`), two connects, and the user/assistant pair isn't atomic. `append_exchange()`. | L / S | `test_sessions.py` exists. |
| O15 | Non-LabCorp PDFs parsed twice on upload (`main.py:981-989` then `1064-1069`). | L / S | Uploads are rare. |

---

## 3. Consolidation — tool_service

Test model that governs everything here: route tests patch module attributes
on `tool_service.main` (`patch.object(ts.httpx, "AsyncClient", FakeHA)`,
`patch.object(ts, "ha_call_service", ...)`, `ts.asyncio.sleep`,
`ts.MUSIC_VERIFY_TIMEOUT_S`, `_ssh_mm`, `_run`, …). Code that moves to
another module stops seeing those patches. **That retargeting is the real
cost of any module split, not the code motion.**

| # | Finding | Impact / Effort | Risk |
|---|---|---|---|
| T1 | **Music route preamble ×4** (`main.py:1189-1209, 1374-1393, 1491-1501, 1591-1600`): token guard, headers, client, `_fetch_players` → `resolve_players` → live filter → 503. The "unavailable" sentence is already drifting (1208 vs 1393/1600). One `_music_targets(req)`. ~45 lines. | M / S | Daily voice path; every branch covered. Keep in `main.py`. |
| T2 | **HA state read written five ways.** `_get_state` exists at 1177-1178; `control_music`/`rate_music` define identical local closures (1409, 1503); `play_music` inlines (1277); `now_playing` inlines with a status check (1603). Collapse onto `_get_state` with the status check. Fixes X4. | M / S | `FakeHA.get` returns status 200 in every fixture. |
| T3 | **Verify-the-effect poll loop ×3** (`1243-1260, 1424-1446, 1542-1547`): same deadline/sleep/fetch shape, three legitimately different predicates. `_poll_until(check, timeout, on_timeout)`; must preserve play_music's accept-at-deadline rule (1256-1260). Also replaces six `asyncio.get_event_loop().time()` with `time.monotonic()`. | M / S | All three loops test-covered; helper must read `MUSIC_VERIFY_TIMEOUT_S` at call time. Bench after. |
| T4 | **Two HA REST clients, two copies of `HA_URL`/`HA_TOKEN`.** `solar.py:47-48` re-reads what `main.py:952-953` reads; `solar._ha_service` (623-638) is the REST path that `main.py:662-670` documents as dropping HA's error message. A ~90-line `tool_service/ha.py` (`HA_URL`, `HA_TOKEN`, `HAServiceError`, `ha_call_service`, `get_state`, template POST). `main.py` must re-export (`ha_call_service = ha.ha_call_service`) so the four `patch.object(ts, "ha_call_service", ...)` fixtures keep working; `Dockerfile:14` COPY list and the dual-compat block at `main.py:23-30` need the new file. | M / M | `test_error_surfacing.py:62-65` stubs `sys.modules["websockets"]`, which is why `ha_call_service` has a local `import websockets` at 689 while `ma_command` uses the module import — converge the tests on the `connect`-patch style first. |
| T5 | **Module seams.** Music is `946-1613` (667 lines, 41% of the file). Split **only** `ha.py` (T4) then `music.py` (an `APIRouter`); leave weather/web/mirror/shopping in place. Each extra module is another patch-target retargeting across six test files and another COPY entry. | M / M | Do after T1-T4 so the patch targets move once. |
| T6 | SSH option list built twice (`775-780` vs `899-902`), differing only in `-i`; both pin `UserKnownHostsFile=/data/mm_known_hosts`, a mirror-named file now shared by every ops host. `_ssh_opts(key)`; rename the file as its own step. | L / S | `test_ssh_mm_stages_script_then_runs_verb` asserts on argv. |
| T7 | `solar.py`: `parse_inverters` (113-117) and `parse_inverter_lifetime` (184-188) rebuild the same `by_idx`; `parse_lifetime_energy` (146-154) is `parse_counters(v)["pv_en"]` (164-178). | L / S | Nil; outputs locked by tests. |
| T8 | Three background loops with the same try/except/sleep skeleton (`main.py:61-70, 76-104`, `news.py:298-313`). | L / S | Only if already in `lifespan`. |
| T9 | "Soft failure" convention is inconsistent: `/fetch` returns `200 {"ok": false}` by design and documented; `/hottub` returns `200 {"online": None, "error"}` undocumented; `/solar/energy` returns `200 {"error"}` while sibling routes raise 502. Pick one and note it once. | L / S | — |
| T10 | `parse_players` + bare-list branch of `parse_player_payload` (`1031-1045`) are compat for a template that no longer exists; protected by tests that would go with it. Leave unless touching T5. | — | — |

---

## 4. Consolidation — health_service and finance_service

| # | Finding | Impact / Effort | Risk |
|---|---|---|---|
| H1 | **Cross-service duplication measured at ~130 lines**: `get_conn` ×3 (13/13/17 lines, identical but for the env var), pypdf extraction ×5 (~4 lines each), decode fallback ×4, dual-compat import blocks ×3, `/health` ×2. Compose builds each service from its own dir, so a `kronk_common/` needs a root build context or a copy step and a third import flavor. **Verdict: leave it.** | L / M | Rejected. |
| H2 | **`query_health` is a 360-line hand-unrolled matrix** (`health_service/db.py:352-711`), 4 metrics × 3 resolutions. The daily_summary branch (607-647) is already table-driven via `col_map` + `_extremes`; hrv (441-486) and withings (650-694) are the same shape by hand; `_date_range` tail pasted 12 times. Table-drive three, keep sleep (derived pct) and activities (SUM) as overrides. ~150 → ~60 lines. | M / M | **Not test-protected**: `test_health_agent.py` only hits defaults (metric=all, raw). Write the parametrized test first. |
| H3 | Activities bypass `db.py`: `INSERT OR REPLACE INTO activities` at `main.py:338-349` and `724-730` with raw `get_conn()`; every other table has `upsert_*_rows`. | L / S | Two tests cover it. |
| H4 | CSV date-guard pasted ×4 (`main.py:361-365, 405-409, 453-457, 532-536`). | L / S | — |
| H5 | CSV importers (`357-564`) never index into chroma while JSON importers do, so `search_health_data` goes silently stale. Still open from July (P2). | M / S | Tenet 7. |
| H6 | `/api/reindex` (`1000-1028`): bare import (X9), unused imports, omits `bloodwork`/`withings_body`, calls `upsert_chunks` instead of `_safe_upsert_chunks`, no caller. Fix or delete. | L / S | — |
| H7 | Finance import landing block ×2 (`finance_service/main.py:314-335` vs `405-416`): save raw → upsert per account → record file → totals. `_land_import()`. | L / S | Well covered by `test_positions_api` + `test_statement_pdf:111-168`. |
| H8 | Filename-date fallback copied, not called: `importer.py:268-275` vs `extractor.py:131-139` (whose comment says "reuse the importer's conventions"). | L / S | Two tests pin it. |
| H9 | `datetime.utcnow()` / `utcfromtimestamp` at `health main.py:713, 824, 844, 911`, `finance db.py:46` — deprecated on 3.12; `positions_db.py:24-25` already has the tz-aware `_now()`. | L / S | — |
| H10 | `dashboard()` reads `/app/static/index.html` by absolute path per request (`main.py:67-70`); moot if D9 lands. | — | — |

**chromadb/fastembed is write-only today.** Writers: JSON importers, bloodwork,
reindex. The only reader is `/api/search` → `tools.search_health_data` →
**wired to no agent** (`agents.py:207`); the dashboard never calls it. Cost:
1.03 GB image vs 359 MB for finance, `mem_limit: 2g`, a 2,688-line lock, a
model-warmup layer, embedding time on every import. ROADMAP "Health RAG
completion" plans the reader. **Not proposing removal — the decision belongs
on that roadmap line**, but it should be made rather than carried.

---

## 5. Front end (`orchestrator/static`)

Inline CSS: 1,571 lines total across pages (index 430, health_service 297,
health 185, finances 144, performance 143, resources 113, services 104,
agents 97, solar 49). `kronk.css` (66 lines) and `kronk.js` (49 lines) are
the consolidation that started 2026-06-12 and stopped. No light mode exists,
so there is no theme duplication to worry about.

| # | Finding | Impact / Effort |
|---|---|---|
| U1 | **Vendor `marked` + `DOMPurify`; point `performance.html` at the local `chart.min.js`** (X7). ~60 KB. | H / S |
| U2 | **Route-audit test.** Nothing asserts a page route returns 200 or that a page's `fetch()`/`href` targets exist (`test_retract.py:35-39` and `test_agentic_loop.py:36-40` stub `open('/app/static/…')` to `<html></html>`). A ~30-line test that walks `static/*.html`, extracts fetched paths, and checks them against `app.routes` + the nginx `location` list would have caught X6, X11, and E2 below. Add before any UI refactor. | M / S |
| U3 | **Delete the dead** (D8, D9, X6 callers), relative-path the services links (X11), and swap the chat page's 30 s liveness ping from `fetch('/health')` — the 28 KB HTML page — to `/api/status` (`index.html:556`, `main.py:200`). | M / S |
| U4 | **`kronk.css` round two.** Same primitives re-declared per page: `main` layout ×7, sticky `header` ×5 (byte-identical), card primitive under ~15 names, button primitive under ~9, badge primitive under ~8 (`.model-badge` defined twice, differently: `agents.html:48-56` vs `services.html:87-96`), `.status-dot` ×4 with three vocabularies (`checkDots` in kronk.js only knows one), `.empty` ×4, `.section-label` duplicated in `performance.html:44-51`. Lift `main`, header, `.card`, `.btn`, `.badge`, `.status-dot`, `.empty`, `.last-updated`. ~250-300 lines out of seven pages. | M / M |
| U5 | **Nav.** No page carries one. `services.html` is the de-facto directory; `/services`, `/solar`, `/shopping_list` are reachable only by typed URL; adding a page means editing `main.py:95-110` + `services.html` + maybe `index.html`. A `static/js/nav.js` with one page array and `document.currentScript` insertion. | M / S |
| U6 | **Adopt or delete `kronk.js`.** Seven hand-rolled `setInterval(fn, 30000)` loops; bare `fetch().then(r=>r.json())` with no `ok` check in five pages; `makeChart` used once while three pages hand-roll destroy-before-recreate. Add a `document.hidden` guard to `pollEvery` and every background tab stops polling. | L / S |
| U7 | Chart.js theming set three ways (`Chart.defaults` in two pages, hardcoded hex in `performance.html:386-405`, nothing in solar/finances → light-theme greys on a dark page). One `chartDefaults()`. | L / S |
| U8 | Duplicated JS: "updated HH:MM:SS" ×4, `escapeHtml` ×2 while three pages interpolate API strings raw into `innerHTML` (`services.html:298-309`, `performance.html:307-347`, `resources.html:291-319`); inside `finances.html` the drag/drop + upload choreography is written twice (`:303-316` vs `:544-555`; `uploadFile` vs `uploadPositions`). | L / S |

---

## 6. Infra, scripts, tests

| # | Finding | Impact / Effort | Sign-off |
|---|---|---|---|
| I1 | **`tests/requirements.txt`** (X10): regenerate as the union of the four service pins, exact versions. The suite is the definition of done and today it can't be rebuilt from the file. | H / S | — |
| I2 | **Two bench scripts are one script twice.** `devops_model_bench.py` and `coordinator_model_bench.py` (403 lines each): identical model→port probe (`363-369` vs `376-383`), the `httpx.post` + timing + `timings.predicted_per_second` extraction (`316-351` vs `219-252`), the `docs/bench/<prefix>_<stamp>_<label>.{json,md}` writer (`377-398` vs `393-398`), the tool-call arg parser, the scoreboard. Coordinator's is the better one. A `scripts/lib/bench.py` (`probe_server`, `chat`, `run_record`, `write_results`) cuts each to its probe table (~150 lines). `test_coordinator_bench.py:18-24` loads by path via importlib and needs the same loader. Delete D10/D11 at the same time. | M / M | — |
| I3 | **Dockerfiles: one template, four drifts.** `health:23` and `finance:12` do `COPY . .` with **no `.dockerignore` anywhere**, so `__pycache__`, `requirements.*`, `setup_withings_auth.py` land in the images; orchestrator/tool_service copy explicit lists. `apt-get install ca-certificates` in health/finance is a no-op (`python:3.12-slim` ships it; verified `dpkg -s`). Fix: root `.dockerignore` + explicit COPY lists + drop the two apt layers. Shared base image rejected at four files. | M / S | normal `up -d --build` |
| I4 | **Compose repetition.** The 15s/5s/5 healthcheck block ×6 (`docker-compose.yml:39-44, 57-62, 124-129, 146-151, 161-166, 188-193, 253-258`), `extra_hosts` ×4, `restart` ×9. `docker-compose.langfuse.yml:36-49` already uses anchors — the house style exists. ~40 lines. Env vars: all 37 are read by a service (grepped); no dead ones. `MAPPING_MODEL`/`EXTRACTION_MODEL` in finance are a second model-name fact outside `AGENTS` (tenet 8) — note, don't move. | L / S | kronk stack only; never touches HA/MA |
| I5 | **systemd `Environment=` duplication.** `KRONK_REPO_DIR` in 5 units; `HA_URL`/`HA_NOTIFY_SERVICE` in 2 units *and* defaulted in `notify.sh:14-15`; `gpu-mem-export.service:16` hardcodes `/home/drew` where every other unit uses `%h`. Values are already defaulted in `notify.sh`, so delete the unit lines rather than add an `EnvironmentFile`. | L / S | `daemon-reload` only |
| I6 | **Reconcile units with reality** (see §1 runtime remnants): README lists six llama units, repo has seven, four run; `llama-bonsai` runs unassigned; `/devstral/` nginx route is dead; hottub-monitor and perfwatch are each half-documented. | M / S | **Operator per unit** |
| I7 | Shell watchers: `REPO_DIR` default repeated in 4 scripts; canary state-file + cooldown jq dance duplicated (`searxng_canary.sh:32-33,92-111` vs `music_provider_canary.sh:20-21,53-63`). Two helpers in `notify.sh`. | L / S | — |
| I8 | **Test fakes duplicated.** `class FakeHA` ×4, `class _Resp` ×4 (three byte-identical), `class _Client` ×5, `_FakeWS` ×2, the hermetic `/message` `client` fixture + `_fake_open` verbatim in `test_agentic_loop.py:36-63` and `test_retract.py:35-49`, `env` fixture ×3, `json_dumps` ×2. Move to `conftest.py` or `tests/fakes.py`. Seven files also re-insert `sys.path` entries conftest already provides; two import `health_service` modules bare, against the policy `conftest.py:3-11` states. | M / M | — |
| I9 | nginx: `proxy_set_header` ×5, `/probe/*` six one-liners. An `include` needs a second bind mount for ten lines; leave it, only drop the dead `/devstral/` route. | L / — | — |

---

## 7. Efficiency

| # | Finding | Measured | Impact / Effort |
|---|---|---|---|
| E1 | `/solar/series` (`tool_service/main.py:577-591`) is `async def` and runs four synchronous sqlite scans on the event loop; `readings` has 196,994 rows (growing ~2.3k/day) and only `idx_readings_day`; the `ts >= ?` filters at `solar.py:664, 673, 714` are unindexed. **Measured against the live DB: 7-day and 90-day loads both complete in 0.06 s.** Not a stall today; the shape is wrong and the table doubles every ~3 months. Fix is `asyncio.to_thread` + `CREATE INDEX idx_readings_ts`. (One reviewer rated this H; the measurement says L-M.) | 0.06 s | L-M / S |
| E2 | **Health import endpoints embed on the event loop.** `import_csv` (`health_service/main.py:807`), `import_export` (831), `import_bloodwork` (898) are `async def` but do sync sqlite + `model.embed(texts)` (`vector_store.py:59`). A full Garmin zip embeds thousands of chunks while `/health` can't answer; the compose healthcheck (5 s × 5) can flip the container unhealthy mid-import. Make them plain `def` (FastAPI threadpools sync routes) or `run_in_threadpool`. finance `ingest` (`finance_service/main.py:85`) same shape; `positions_import` must stay async (awaits the LLM) — wrap its sync parts. | not measured | M / S |
| E3 | Weather double-fetch on the voice path (O7). | ~10-50 ms | M / S |
| E4 | News feeds fetched serially (`news.py:131-141`): eight feeds, 15 s timeout each, two-minute worst case. `asyncio.gather` with per-feed exception capture keeps the "failed sources are named" contract. Background, 3×/day. | — | L / S |
| E5 | UI polling: chat page downloads a 28 KB HTML page every 30 s as a liveness ping (U3); two probes 404 every 30 s forever (X6); seven pollers run in hidden tabs (U6). | — | L-M / S |
| E6 | `rollup_and_confirm` + `_advance` open a connection per inverter per rolled day (`solar.py:469, 476, 483, 489`), every 15 min. | — | L / S |

---

## 8. Dependency drift (facts; the call is the operator's)

`requirements.txt`: orchestrator and tool_service all `==`; finance all `==`
on a newer line; **health_service is 4 `==` / 6 `>=`** (`garminconnect`,
`curl_cffi`, `python-multipart`, `chromadb`, `fastembed`, `pypdf`). All four
locks are hash-pinned, so builds are reproducible — tenet 3 holds at the lock;
the `>=` only bites when someone regenerates it.

| Package | orchestrator | tool_service | health | finance | tests |
|---|---|---|---|---|---|
| fastapi | 0.115.0 | 0.115.0 | 0.115.0 | 0.115.12 | 0.115.12 |
| starlette (lock) | 0.38.6 | 0.38.6 | 0.38.6 | 0.46.2 | — |
| uvicorn | 0.30.6 | 0.30.6 | 0.30.6 | 0.34.0 | (0.49.0 in venv) |
| pypdf | 4.3.1 | — | 6.13.2 (via `>=4.0`) | 5.4.0 | 5.4.0 |
| python-multipart | 0.0.9 | — | 0.0.22 | 0.0.20 | 0.0.20 |

`garminconnect`, `curl_cffi` are imported by no file in the repo;
`apscheduler` only by the dead import in D7. Intentional-pending-secrets per
TECH_DEBT; the image cost is the only thing flagged.

Worth one upgrade day: (a) pin health's six `>=` to what the lock already
holds — zero behavior change; (b) one fastapi/starlette/uvicorn/
python-multipart line across all four. **pypdf spans three majors** and
`test_bloodwork_parser.py` / `test_statement_pdf.py` test on text, not PDFs
(the fixture at `test_finance_service.py:44-62` is a blank page) — gate that
bump on a real-PDF fixture.

---

## 9. Considered and rejected (merged from all six reviews)

- **Shared/pooled `httpx.AsyncClient`** (20 construction sites across the
  services): LiteLLM and HA are on localhost/LAN; eleven test sites patch
  `tools.httpx.AsyncClient` and every tool_service fixture replaces the class.
  Milliseconds against 2-25 s generations; test-rewrite cost exceeds it.
- **Persistent SQLite connections** anywhere: TECH_DEBT already rejected
  pooling; `connect()` on a local file is ~0.1 ms; single writer.
- **Generic "proxy tool" table replacing `tools.py` handlers**: the request
  side is 1-3 lines each; the bulk of every handler is model-facing
  formatting. A table would save ~50 lines and hide the part that matters.
- **Schema/handler co-location, prompts to YAML, deriving routing regexes from
  `AGENTS`**: each splits one source into two or adds ceremony. The pins are
  deliberately independent of agent descriptions (`routing.py:7-11`).
- **`kronk_common/` shared package across services** (H1): ~130 lines, two
  build contexts, a third import flavor.
- **Shared Docker base image**: four files, one more tag to pin, build
  ordering, for ~30 lines.
- **`EnvironmentFile=` for user units**, **nginx `include`**: see I5, I9.
- **Narrowing `_llm_lock`**: it's what makes `telemetry._current_root` and
  `sessions._cache` safe; one operator.
- **Per-domain services, merging compose stacks, a bundler/framework/Tailwind,
  Jinja for the nav, a health SPA, a persistent HA websocket, asyncssh**: all
  tenet 4 or tenet 2.
- **Removing chromadb/fastembed, garminconnect/curl_cffi**: roadmap and
  TECH_DEBT own those decisions; flagged cost only.
- **Refactoring the shopping-list JSON store**: ROADMAP 22 replaces it.
- **Splitting `resolve_players`** (80 lines): it is the blueprint's resolution
  order written out once, with 20 tests pinning every rung.
- **Table-driven SSE framing in `/message`**, **caching `_serve()`**,
  **`sessions.window` O(n²)** (n ≤ 40), **`positions_summary` 3 queries per
  account**: too small to matter.

---

## 10. Plan

Each phase is its own commit (or a few), gated by `./scripts/run_tests.sh`;
phases touching the agent loop or the music path also run
`scripts/pipeline_bench.sh <label>-pre/-post`. No phase restarts HA or MA.
Phases are ordered so the gate is trustworthy before anything that needs it,
and so each later phase moves test patch targets at most once.

**Phase 0 — make the gate trustworthy** (S)
- I1 `tests/requirements.txt` from the union of service pins; rebuild
  `tests/.venv` from it and confirm the suite collects.
- Add the first `/v1/chat/completions` endpoint test (streaming + non-stream,
  with a retract in the fake pipeline). It will fail on X1; that's the point.
- U2 route-audit test for static pages.

**Phase 1 — deletions and remnants, no behavior change** (S)
- D1–D8, D10–D13. D9 after the operator's bloodwork/body-comp call.
- X6 probe callers, X11 links, the `/devstral/` nginx route (after the
  operator confirms Q8 isn't coming back).
- `.gitignore` cruft. README model table and the `/health` attribution.

**Phase 2 — local-first and tenet-7 fixes** (S each)
- U1 vendor marked/DOMPurify/chart.js.
- O2 (fixes X1), X2 single rid, X3 `request_complete` in `finally`.
- O9 (fixes X5), T2 (fixes X4).
- X8 `BAD_FRACTION`, X9/H6 reindex, X12 FK pragma, H5 CSV→chroma indexing.
- I3 `.dockerignore` + explicit COPY + drop the no-op apt layers.
- U3 chat ping → `/api/status`.

**Phase 3 — orchestrator consolidation** (S–M)
- O1, O3, O4, O7, O8, O13, O14, O15 — independent, small, each its own commit.
- O5 (`_run_pipeline` consumer loops + one timing shape). Bench pre/post.
- O6 (`run_stream` round-end helpers). Bench pre/post. **Not combined with
  O5** — one variable at a time on the voice path.
- O10 terminal-speech table, then O11/O12. Only after O6 is green and benched.

**Phase 4 — tool_service consolidation** (S–M)
- T1 + T2 + T3 in-module (every patch target stays). Full music test set +
  bench. T6, T7, T9 ride along.
- T4 `ha.py` with `main.py` re-exports; converge the websocket test style
  first. Update the Dockerfile COPY list.
- T5 `music.py` only after T4 lands and the patch targets have moved once.
- E1 `to_thread` + `ts` index; E4 gather; E6 one connection per rollup.

**Phase 5 — health/finance** (S–M)
- E2 de-async the import endpoints (all go through `TestClient`; parity is
  covered).
- H3, H4, H7, H8, H9 — small, each test-covered.
- H2 `query_health` table: **write the parametrized resolution × metric test
  first**, then refactor.

**Phase 6 — front end round two** (M)
- U5 `nav.js`, U4 `kronk.css` primitives, U6 adopt `pollEvery`/`fetchJSON`
  with the hidden-tab guard, U7 chart defaults, U8 helpers. Visual diff per
  page; the route-audit test from Phase 0 catches broken fetches.

**Phase 7 — infra and scripts** (S–M, some need sign-off)
- I2 `scripts/lib/bench.py` + delete D10/D11.
- I4 compose anchors (kronk stack only), I5 unit env lines, I7 watcher
  helpers, I8 test fakes into `conftest.py`.
- I6 unit reconciliation and §8 pinning day — **operator decisions**, listed
  below.

**Decisions only the operator can make** (nothing above blocks on them except
where noted):
1. `health_service/static/index.html`: port bloodwork PDF upload + body
   composition into `health.html`, or drop them (D9).
2. `llama-bonsai` (running, unassigned, 1.1 GB): disable or assign. `llama-
   devstral` Q8 / `mistral-nemo` / `talkie`: keep in the catalog as idle, or
   prune catalog + unit + `/devstral/` route (I6).
3. `kronk-hottub-monitor`: add to `systemd/` or disable (TECH_DEBT says
   parked, the unit says running).
4. `ma-test` container: remove.
5. Pinning day scope (§8): health `>=`→`==` is free; the fastapi/uvicorn line
   is one upgrade; pypdf waits for a real-PDF fixture.
6. chromadb/fastembed: decide on the ROADMAP "Health RAG completion" line
   rather than carry a 1 GB write-only index.
7. O10 (terminal-speech table): it touches the voice guardrail; confirm you
   want it at all, and when.

**What to do first, if only three things:** Phase 0 (the gate), Phase 1 (the
deletions — ~1,250 lines, three silent 30 s failure loops, zero risk), and U1
+ O2/X2/X3 from Phase 2 (the main page stops needing the internet; the OpenAI
shim stops leaking tool text and stops minting two ids).
