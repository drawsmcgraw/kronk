# Blog topic tracker

Working list of writable stories from the Kronk build log. Each entry: the
hook, why it's interesting beyond this project, and where the receipts live
(every claim below has data in this repo — benchmarks, traces, or incident
docs). Status: ☐ unwritten / ✍ drafting / ✅ published.

Shipped features have distilled write-ups in `features/` — each ends with a
"Blog hooks" section; new entries here should link to the feature doc for
receipts instead of restating them.

---

## ☐ The agent that announced its tool call instead of making it

**Hook:** Asked for the heads of state of the top-5 GDP countries, the agent
did everything right for three steps — then emitted `<|tool_call>call:
web_search{...}` as its *answer*. The bug wasn't the model: we took its
tools away mid-plan (tool-round budget exhausted) and never told it.
**Why it travels:** the "budget cliff" is a universal agent-loop design
flaw; the fix (closure message + honest-partial-answer guardrail + bigger
budget) is portable to any tool-calling loop. Bonus honesty: the
parallel-tool-call prompt nudge *didn't* work on a 4B-class model — depth
beat width.
**Receipts:** `docs/bench/research_*_before.json` (2/8 leaked) vs
`_after.json` (0/8, hardest question answered), guardrail test in
`tests/test_agentic_loop.py`.

## ☐ Instrumenting a home assistant with self-hosted Langfuse

**Hook:** From "the chat UI prints a timing line" to span-tree traces,
TTFT/token metrics, and dashboards — on a single home box, ~1.7 GB idle.
Includes the part nobody documents: programmatically seeding Langfuse
dashboards via its Postgres because there's no public dashboards API.
**Why it travels:** every LLM-app builder hits the "where does the time go"
wall; this is a complete, costed, self-hosted answer with the SDK-isolation
pattern (`telemetry.py` — instrumentation that can never break the app).
**Receipts:** `docs/plans/TELEMETRY_PLAN.md`, `docs/TELEMETRY_GUIDE.md`,
the dashboards themselves.

## ☐ The GPU that wouldn't sleep: a silent-hang detective story

**Hook:** A home server hard-hung three times in two weeks. Suspects in
order: Bluetooth, Music Assistant, kernel, firmware… the actual finding: a
known ROCm/HIP bug kept the iGPU at 100% busy / 35 W *24-7 while idle* —
since mid-April. Switching llama.cpp to Vulkan: 0% / 7 W, hangs gone.
Features journal forensics, a hardware watchdog, a phone alert when the box
self-reboots, and two red herrings lovingly documented.
**Why it travels:** Strix Halo / gfx1151 owners are hitting this exact bug
(upstream issues linked in the doc); the debugging method is the real
content.
**Receipts:** `docs/incidents/INVESTIGATION_2026-06-09_hangs.md`,
`docs/CHANGE_2026-06-09_gemma_vulkan_switch.md`.

## ☐ Speculative decoding (MTP) on a consumer iGPU

**Hook:** A 98 MB "assistant" model made generation 37-48% faster with
provably identical outputs — same day the llama.cpp support merged.
Includes the trap: `-md <drafter>` alone silently does nothing
(`--spec-type draft-mtp` required), and draft acceptance is 28% on prose
but 52% on real assistant traffic.
**Receipts:** `docs/model_results.md` (MTP section),
`docs/bench/bench_*_mtp-post.json`.

## ☐ One knob, three failure modes: tuning a reasoning budget

**Hook:** `--reasoning-budget` on a thinking model: 64 made the research
agent plan shallowly (skipped fetch_url), 128 corrupted tool-call grammar
mid-thought, 0 leaked the hidden chain-of-thought into user-visible
answers. 256 was the Goldilocks — and the *eval methodology* (probe
dispatch AND deep research AND visible-answer quality) is the story.
**Receipts:** `docs/PERF_FINDINGS_2026-06-10.md`,
`docs/REPORT_2026-06_response_time_program.md` §2.1.

## ☐ Don't cache answers — inject the data (weather, 10s → 2.6s)

**Hook:** The obvious fix for slow weather queries is caching answers; the
right fix was pre-fetching the *forecast data* hourly into the agent's
prompt — one LLM round, zero tool calls, fresher than any answer cache, no
cache-key machinery. Plus the same-day humbling: the model confidently
invented "next Tuesday, October 22" because nobody told it the date.
**Receipts:** REPORT §2.2 + §4.1, before/after in `docs/bench/`.

## ☐ A home assistant that remembers: per-client sessions without Redis

**Hook:** Voice had zero conversation memory; the web UI forgot on restart.
SQLite + in-process cache beat Redis on every axis at this scale — and the
voice path needed *no storage at all* once we read HA's source and found it
resends the full conversation every request. "Clear my history" by voice
included.
**Receipts:** REPORT §2.3, `orchestrator/sessions.py`, `tests/test_sessions.py`.

## ☐ Agents-as-tools: letting the coordinator phone a specialist

**Hook:** A router misclassified "who is the county executive?" onto a
tool-less path, and the model answered "I need to search for that" — a
dead end by architecture. Fix: every specialist became a callable tool
(`ask_research`, …) for the coordinator, depth-capped at 2 by construction.
Router misses now self-heal; multi-domain questions compose; the miss rate
became measurable in traces.
**Receipts:** REPORT §4.3, TECH_DEBT ROUTING-01 (the entry predicted the
fix), Langfuse delegation spans.

## ☐ My self-hosted search went AOL-only (SearXNG ops for LLM backends)

**Hook:** Recipe queries returning Microsoft 365 admin docs. Diagnosis:
`use_default_settings: true` silently merged 84 default engines; agent
traffic CAPTCHA-benched the good ones (default bench: up to a *day*);
sole survivor: AOL. Fix: `keep_only`, shortened `suspended_times`, pinned
weekly-moving image, burst-resilience testing.
**Why it travels:** everyone wiring SearXNG into a RAG/agent stack hits
this; almost nobody measures engine survival under burst.
**Receipts:** searxng/settings.yml.example comments, the hot-chicken
session logs.

## ☐ 79% of my token budget was navigation links (trafilatura swap)

**Hook:** The agent fetched the right recipe page and honestly reported it
couldn't find the recipe — because keep-everything HTML extraction spent
16,000 chars on menu links and truncated before the ingredients. Main-
content extraction: 79% → 9% link share, recipe complete in 7k chars.
Bonus bug: advertising brotli you can't decode turns pages into mojibake.
**Receipts:** the before/after extraction measurements,
`tests/test_extract_page_text.py`.

## ☐ The box that reboots itself and texts you about it

**Hook:** sp5100_tco hardware watchdog + systemd RuntimeWatchdog + a boot
notifier that reads the previous boot's journal to decide "clean shutdown
or hang?" and pushes to your phone via HA. The home-lab self-healing
starter kit, born from real hangs.
**Receipts:** `scripts/boot_notify.sh`, `scripts/lib/notify.sh`,
incident docs.

## ☐ Refactor with a net: getting the test suite green first

**Hook:** A whole-codebase review found ~400 duplicated lines — but the
test suite had been broken so long it couldn't catch regressions. Order of
operations: fix the suite (3 failed/4 errors → 122 green), add 45 tests
for the newest code, THEN refactor. The net paid for itself within the
hour: it caught a real bug in a "safety fix" from earlier the same day.
**Receipts:** the refactor-program commits, `scripts/run_tests.sh`,
benchmark-neutrality data (`bench_*_refactor-post.json`).

## ☐ Benchmark-bookended development (the meta-post)

**Hook:** Every change in this program shipped between two identical
benchmark runs (`pipeline_bench.sh`, `research_bench.sh`): baseline →
change → re-measure → keep or revert. Three reverts/retunes happened
*because the after-data said so* (reasoning budget twice, a router prompt
example that made a 4B router drag timezone questions to web search).
**Receipts:** the whole `docs/bench/` directory tells the story in JSON.

---

## Parking lot (smaller seeds)

- Langfuse password reset without SMTP (bcrypt straight into Postgres)
- Pinning rolling Docker tags after a stealth-upgrade scare (`main-latest`)
- The voice latency journey end-to-end: 11s → ~2.6s across five techniques
- Gemma 4 QAT swap: +15% generation for free, benchmark-gated
- HA owns voice history: reading Home Assistant's source instead of
  building a session store you don't need
