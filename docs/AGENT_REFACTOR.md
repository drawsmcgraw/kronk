# Kronk Agent Architecture Refactor

Design doc + plan for migrating Kronk from a router-based multi-agent system
to an Agent-as-Tool architecture. Driven by `docs/AGENT_ARCHITECTURE_RESEARCH.md`.

Status: **plan written 2026-05-30, awaiting operator green-light**. No code
touched yet.

---

## 1. The old approach (current state)

```
user query
   │
   ▼
[ regex shortcuts ]  ── talkie / direct-override / search-phrase / URL ──┐
   │                                                                     │
   ▼                                                                     │
[ router LLM (gemma-3-4b) ]  ── one of: home, research, health,          │
   │                            assistant, finance, coding, devops,      │
   │                            talkie, direct                           │
   ▼                                                                     │
[ specialist agent runs ]  ── narrow tool allow-list per agent ◀─────────┘
   │
   ▼
response
```

- **Router** is a separate ~200 ms LLM call that picks exactly one specialist
  agent.
- **Each agent** is `gemma-4-e4b` with a narrow tool allow-list, except
  `coding`/`devops` (devstral-q4) and `talkie` (talkie).
- **Agents are mutually exclusive.** A query goes to one agent. Cross-domain
  queries ("weather + my sleep score for tomorrow") cannot be served as one
  coherent call — the router picks one and the other is ignored.
- **History** lives in a single global `history` list shared by every entry
  point (`/message` chat UI, `/api/chat` voice via Ollama shim,
  `/v1/chat/completions` OpenAI shim). Voice and chat-UI conversations bleed
  into each other.

### What's working

- Routing is fast when it's right.
- Each agent's small tool surface (1–6 tools) is easy for gemma-4-e4b to
  navigate.
- Voice end-to-end works for happy-path queries (weather, news, health).
- `kronk_facts()` ambient context reaches every code path.

### What's broken

- **Router misclassifies.** "What is the meaning of life according to Conan
  the Barbarian?" → `direct` (coordinator approximates the quote from
  training instead of looking it up). The user had to add "search online"
  to force the regex shortcut.
- **No cross-domain queries.** Can't ask "what's the weather where I last
  exercised?" — router picks weather OR health, not both.
- **Research agent over-runs and times out.** Burns `MAX_TOOL_ROUNDS` then
  forced-synthesises whatever it has. AVGO closing-price query took 41 s.
- **History pollution between clients.** Voice utterances mixed with chat-UI
  context. A user trying out voice doesn't want it to alter their chat
  history; they may have very different conversational contexts.
- **`set_timer` was reinventing wheels.** HA Assist already has native
  multi-named timer intents — Kronk shouldn't be in that path. (Decided
  separately 2026-05-28; see `docs/VOICE_SETUP.md` Open Items.)

---

## 2. The new approach (Pattern A: Agent-as-Tool)

```
user query
   │
   ▼
[ regex shortcuts (preserved) ]  ── talkie / direct-override / URL ──┐
   │                                                                 │
   ▼                                                                 │
[ optional embedding fast-path ]  ── if confidence > threshold,      │
   │                                  skip to one agent-tool         │
   ▼                                                                 │
[ coordinator (one LLM, the brain) ]  ◀──────────────────────────────┘
   │
   │   has tools:  - web_search          ← cheap, broad, available to coordinator
   │              - get_weather          ← single-call tools
   │              - query_health
   │              - query_finances
   │              - shopping_list_*
   │              - query_hottub
   │              - get_kronk_context
   │              - generate_diagram
   │              - agent_tool: research ← planning agent invoked as a tool
   │              - agent_tool: coding   ← devstral wrapped as a tool
   │
   ▼
[ tool calls execute, possibly in parallel for cross-domain ]
   │
   ▼
[ coordinator synthesises final response from tool results ]
   │
   ▼
response
```

### Key design choices

#### a. Tools, not agents, for single-step work

The current `home`, `health`, `finance`, `assistant` "agents" become bare
tool registrations on the coordinator. They were always thin wrappers around
one tool call + synthesis — the agent abstraction was overhead. The
coordinator handles the synthesis as part of its normal flow.

#### b. `web_search` becomes a coordinator-level tool

Per operator directive: simple lookups (quotes, movie times, AVGO close)
don't deserve to be called "research." They're just `web_search` calls the
coordinator can make directly when its model judgment says a lookup is
needed. Most factual-precision misses (the Conan example) get solved here
without any planning machinery.

#### c. `research` is a planning agent-tool, invoked rarely

For genuinely intensive work — voting guides, candidate research, deep
multi-source analysis — the coordinator invokes `research` as an agent-tool.
The research agent internally implements Planning (Ch 6): generate a plan,
execute steps, evaluate, replan if needed, synthesize with citations.

Trigger: the coordinator decides based on query shape ("compile a voter
guide on X" → research; "what did so-and-so say about Y" → just web_search).
We bias the tool's description to make this clear.

#### d. `coding` / `devops` is an agent-tool

Devstral stays specialized. The coordinator invokes `coding` as an agent-tool
for code/devops work. This preserves the model specialization without
forcing the coordinator to be good at code (it isn't).

#### e. `talkie` stays a regex-pre-checked shortcut

Talkie is invoked by name only. The existing `_TALKIE_PHRASES` regex fires
before the coordinator and routes directly to talkie. No change.

#### f. Per-client session history (Memory Ch 8 pattern)

Replace the global `history` list with a session store keyed by client. Two
initial clients identified by entry point:

| Entry point                            | Client / session id       |
| ---                                    | ---                       |
| `POST /message`                        | `chat_ui`                 |
| `POST /api/chat` (Ollama shim, HA)     | `voice` (initial)         |
| `POST /v1/chat/completions` (OpenAI)   | `openai_shim` (initial)   |

If HA passes a stable conversation/session ID via the Ollama protocol, we
adopt it (`voice:<ha-conv-id>`). The chat UI gets its own. Each session
maintains its own message history; sessions are isolated.

The `kronk_facts()` ambient context still gets prepended to every session
identically — that's not session-scoped.

#### g. Coordinator model upgrade (research + benchmark)

`gemma-4-e4b` was sized for narrow tool sets (2–6 tools). The new coordinator
will see ~10–12 tool descriptions plus may need to make better-quality tool
selection decisions. Candidates to evaluate:

- Stay with `gemma-4-e4b` — fastest, current, may regress on tool selection
- `mistral-nemo` (already loaded, 12B Q8, currently unassigned)
- Newer Gemma 4 variants (need to check huggingface for sizes > E4B that
  fit our GTT budget)
- Qwen 2.5/3 instruct variants — strong tool-calling reputation
- Llama 3.x 8B instruct — solid baseline

Benchmark protocol (see §6) decides. Default action if benchmark inconclusive:
keep gemma-4-e4b for the first cut, plan upgrade as Pass 1.5.

---

## 3. Why we're changing

| Problem with old approach                                | How new approach fixes it                                              |
| ---                                                      | ---                                                                    |
| Router LLM misclassifies (Conan quote → direct)          | No router; coordinator decides per query with full context             |
| Can't serve cross-domain queries                         | Coordinator can call multiple tools in one turn                        |
| Research agent runs to MAX_ROUNDS and times out          | Research becomes a planner with its own stop signal                    |
| Most "research" is actually just a lookup                | Coordinator calls `web_search` directly; research is only for the rare heavy case |
| Voice + chat-UI histories collide                        | Per-client sessions; voice can't leak into chat-UI and vice versa      |
| Adding a new agent means router prompt edits + tool wiring | New capability = register one tool/agent-tool; coordinator picks it up naturally |
| Latency floor of ~200 ms for the router call             | Eliminated; the coordinator's forward pass *is* the decision           |

### Latency accounting — does Pattern A hurt voice?

This is the load-bearing concern.

**Old**: router (~200 ms) + agent (1–15 s) = **1–15 s** dominated by agent

**New**: coordinator decide (~300–1500 ms, longer prompt with more tool defs)
+ tool calls (≪ 1 s each for fast tools, 1–15 s for agent-tools) + synthesis
(~3–5 s) = **3–16 s**

The honest answer: Pattern A adds **a few hundred milliseconds** to simple
voice queries and **doesn't change the worst case** for tool-heavy queries.
Mitigations baked in:

- **Regex pre-checks survive** (talkie, direct-override) — instant bypass.
- **Embedding fast-path** for high-confidence single-tool queries (see
  §pattern-C below) — skips the coordinator's tool-selection cost.
- **Streaming** continues to be on; first synthesis token arrives at the
  same speed as today.

Net: simple voice queries get +200–500 ms. Tool-heavy queries are a wash.
Cross-domain queries become *faster than impossible* (they were unreachable
before).

---

## 4. Pattern C — what to do with routing hints

The research doc flagged two distinct things, and you wrote "routing hints
(pattern C) are great" which is ambiguous. **Plan picks both** unless you
override:

1. **Reuse `routing_hint` as the agent-tool description.** Each
   agent-tool's description (what the coordinator sees) derives from the
   agent's `routing_hint` field. Single source of truth. The hints we
   already tuned (the Conan-quote fix updated `research`'s hint
   2026-05-29) become the coordinator's decision criterion.

2. **Embedding-based fast-path.** At startup, embed each agent's
   `routing_hint` and the descriptions of bare tools (`web_search`, etc.).
   On each request, embed the query and find the best match. If cosine
   similarity > a threshold (e.g. 0.75), skip the coordinator entirely
   and invoke the matched tool/agent directly. Below threshold, fall
   through to the coordinator. Sub-millisecond overhead, no LLM call.

The embedding model: `sentence-transformers/all-MiniLM-L6-v2` is the
standard cheap option (~80 MB, runs on CPU in <10 ms). Could also use
`bge-small` or similar. Loaded once at orchestrator startup.

**Question for you**: I'm proceeding with both. If you want only #1 (and to
skip embeddings entirely until we measure they're needed), say so.

---

## 5. Implementation phases

### Phase 0 — Branch + bench harness

Before any refactor, set up the test/benchmark infrastructure so we can
measure regressions.

- Create a benchmark script (`benchmarks/agent_bench.py` or similar) that
  POSTs a fixed set of queries to both `/message` and `/api/chat`, records
  per-query timing breakdowns, routing decisions, and final responses to a
  CSV/JSONL.
- Define the query suite: ~30 queries covering each agent's domain
  (weather, health, finance, shopping list, hot tub, code, research, talkie,
  ambiguous, multi-domain).
- Run the suite against current `main` to capture the baseline.

### Phase 1 — Refactor `orchestrator/agents.py` to support agent-as-tool

- Add a `to_tool_definition()` method on `AgentConfig` that emits an
  OpenAI-style tool schema using the `routing_hint` as the description.
- Add an `invoke_as_tool()` async function that, given an agent name +
  query, runs the existing `run_stream` and returns the joined output.
- Existing `run_stream` stays — agent-tool invocation wraps it.

### Phase 2 — New coordinator path in `orchestrator/main.py`

- Add a new `_coordinator_pipeline_tokens()` async generator (parallel to
  the existing `_kronk_pipeline_tokens`) that:
  - Builds the tool set: 12 bare tools + 1 agent-tool (`research`) — see
    table in §8
  - Runs the unified streaming tool-calling loop (same shape as
    `agents.run_stream` does today, just at the coordinator level)
  - Sequential tool execution within a round (parallel deferred — see §8)
- New endpoint `/v2/chat` for testing — leaves `/message`, `/api/chat`,
  `/v1/chat/completions` on the old pipeline.
- Feature flag (env var `KRONK_COORDINATOR=v1|v2`) lets us flip between
  pipelines without removing the old code.

### Phase 2.5 — Embedding fast-path (Pattern C, operator-requested early)

- Load `sentence-transformers/all-MiniLM-L6-v2` at orchestrator startup
  (~80 MB, CPU-only, ~10 ms per embed).
- At startup: embed each bare tool's description and the `research`
  agent-tool's `routing_hint`.
- On each `/v2/chat` request: embed the user message; cosine-similarity
  against all tool/agent embeddings.
- If max similarity > threshold (initial guess: 0.75, tune empirically):
  invoke the matched tool/agent **directly**, skipping the coordinator's
  decide-and-call LLM step.
- Below threshold: fall through to the normal coordinator path.
- Log every fast-path decision (matched name, score, latency) so we can
  tune the threshold.

### Phase 3 — Per-client session store

- Add a `SessionStore` class in `orchestrator/sessions.py` keyed by
  client id (`chat_ui`, `voice`, `openai_shim`).
- Replace global `history` reads/writes in `/message`, `/api/chat`,
  `/v1/chat/completions` with session-scoped reads/writes.
- Keep `kronk_facts()` ambient and global.

### Phase 4 — Coordinator model benchmark + swap

- Research candidate models (Gemma updates, Qwen, Llama 3.x).
- Download into `/opt/models/<vendor>/` if permissions allow; otherwise
  `~/model-staging/` with a note (mirroring the talkie pattern).
- Add llama.cpp systemd units for each candidate.
- Run benchmark suite against each.
- Pick winner, set `COORDINATOR_MODEL` env to it.

### Phase 5 — Promote research to a Planning agent

- Refactor the `research` agent into a planner: explicit plan step,
  execute-evaluate-replan loop, synthesis-with-citations final step.
- Add a `stop_research` self-signal so the agent can decide it has enough.
- Replace the `MAX_TOOL_ROUNDS` hard ceiling with a planner-driven exit.

### Phase 6 — (reserved — was embedding fast-path, moved up to Phase 2.5)

### Phase 7 — Flip the default, remove old code

- After all phases benchmark-clean, flip `KRONK_COORDINATOR=v2` as default
  and remove the v1 router code.
- Update `README.md` architecture section + `agents.AGENTS` docstring.

---

## 6. Testing & benchmarking plan

### Test queries

Fixed list of ~30 queries spanning all domains. Stored at
`benchmarks/queries.yml`:

```
# weather
- "What's the weather today?"
- "Will it rain tomorrow in Baltimore?"
# health
- "What was my average sleep last week?"
- "Show me my resting heart rate trend this month."
# finance
- "What did I spend on groceries last month?"
- "Find my latest 1099."
# home
- "Add milk to my shopping list."
- "Is the hot tub online?"
# research / lookups (should be web_search now, not research agent)
- "What's the famous Conan the Barbarian quote about what is best in life?"
- "What is the last closing price of AVGO?"
- "What time is the new Mission Impossible playing tonight?"
# research-heavy (should invoke research agent-tool)
- "Compile a summary of the candidates running for governor of Maryland and their key positions."
# code
- "Write a Python function to validate an email address."
# talkie
- "Ask talkie what he thinks of cellulitis."
# cross-domain (new capability)
- "Should I run outside tomorrow given the weather and my sleep last night?"
# ambiguous
- "Tell me about cellulitis."
- "What can you tell me about my health?"
```

### Per-query captured metrics

- `query_id`, `query_text`, `pipeline` (`v1` / `v2`), `client` (`message` / `api_chat`)
- `routing_decision` (v1: router output; v2: coordinator's tool call sequence)
- `tools_called` (list with per-tool duration)
- `total_duration_s`
- `time_to_first_token_s`
- `final_response` (full text)
- `tokens_in` / `tokens_out` if available

### Quality checks

I can't human-grade response quality. What I can check programmatically:

- **Non-empty** — response > 20 chars
- **Not garbage** — no `<unused49>` / `<pad>` / Gemma special tokens
- **Right routing** — for each query I know which agent/tool *should* fire;
  fail if it doesn't
- **Citations present** for research queries (look for URL patterns)
- **Cross-domain queries** invoke multiple tools (count tool calls)

I'll flag per-query result quality as `pass` / `suspect` / `fail` based on
these heuristics. You'll need to spot-check the `suspect` and `fail` ones
when you're back.

### Voice path testing without a microphone

I'll exercise `/api/chat` (the Ollama shim HA hits) directly with curl.
This bypasses STT and TTS but covers everything Kronk owns. Specifically:

- Hit `/api/chat` with `{"model":"kronk", "messages":[{"role":"user","content":...}], "stream":false}`
- Measure end-to-end latency
- Compare to the same query via `/message` to confirm parity

### Reports generated

At the end of the autonomous run, I'll produce:

- `benchmarks/results-v1-<timestamp>.jsonl` — baseline (pre-refactor)
- `benchmarks/results-v2-<timestamp>.jsonl` — new architecture
- `benchmarks/comparison-<timestamp>.md` — side-by-side summary with:
  - Per-query latency delta (v1 vs v2)
  - Routing/decision delta (did v2 do the right thing more often?)
  - Quality flag distribution (pass / suspect / fail)
  - Per-pipeline aggregate timings (p50, p95)
  - Notable regressions or wins

---

## 7. Decisions I'll make autonomously (and how I'll log them)

I'll keep a running log at the bottom of this file (§9 below). Every
non-trivial decision gets one bullet with the date, the choice, and the
reason. Examples of decisions I expect to hit:

- Which candidate coordinator model to actually download and benchmark
- Threshold for embedding-fast-path skip (if I implement it)
- How to structure the `research` planner internally
- How to identify the HA voice session ID (does HA pass one we can use?)
- What to do if a candidate model needs an HF token I don't have

Anything that touches **money, deletes data, or modifies HA externally** I
will *not* do autonomously — those will become open questions you'll see
on return.

---

## 8. Refinements from operator review (2026-05-30)

Operator answered the original open questions. Decisions baked in:

1. **Embedding fast-path: implement now.** Phase 6 in the original list
   moves up to **Phase 2.5**, right after the coordinator path is in
   place — so the fast-path can be measured against the coordinator
   from the start. Uses `sentence-transformers/all-MiniLM-L6-v2`.
   Threshold tuned empirically against the benchmark suite.

2. **Parallel tool execution: deferred.** Sequential for now. Single-host
   constraint (one llama.cpp server per model) means parallel calls that
   both need the LLM would serialize at the server anyway. Pros/cons
   captured below for future reconsideration.

3. **Devstral: separate harness, not integrated.** Devstral stays
   accessed via the dedicated `/devstral/` nginx route (already exists
   per README) for external coding harnesses. **This means `coding`
   and `devops` agents disappear entirely from the voice/chat-UI
   coordinator.** Voice or chat-UI code questions get answered by the
   gemma coordinator with its general capabilities — middling but
   acceptable, since "serious" code work uses Mistral Vibe → devstral
   directly. The collapse simplifies the new architecture significantly
   (see "Cascading simplifications" below).

4. **History migration: discard.** No migration. Cutover wipes the
   in-memory global `history`; sessions start fresh per-client.

### Cascading simplifications from decision #3

With devstral out, almost every agent becomes a bare tool:

- `home` was 6 tools — they become bare coordinator tools
- `health` was 1 tool (`query_health`) — bare coordinator tool
- `finance` was 1 tool (`query_finances`) — bare coordinator tool
- `assistant` was 2 tools (`get_kronk_context`, `generate_diagram`) — bare
  coordinator tools
- `research` stays an agent-tool (planning logic justifies the wrapper)
- `coding` / `devops` — gone (devstral is separate harness)
- `talkie` stays a regex pre-check, never reaches the coordinator

**Final coordinator tool surface:**

| Tool                       | Origin agent | Notes                          |
| ---                        | ---          | ---                            |
| `web_search`               | research     | promoted; was research-only    |
| `fetch_url`                | research     | promoted; was research-only    |
| `get_weather`              | home         | bare                           |
| `shopping_list_view`       | home         | bare                           |
| `shopping_list_add`        | home         | bare                           |
| `shopping_list_remove`     | home         | bare                           |
| `shopping_list_clear`      | home         | bare                           |
| `query_hottub`             | home         | bare                           |
| `query_health`             | health       | bare                           |
| `query_finances`           | finance      | bare                           |
| `get_kronk_context`        | assistant    | bare                           |
| `generate_diagram`         | assistant    | bare                           |
| `research` (agent-tool)    | research     | planning agent, invoked rarely |

**12 bare tools + 1 agent-tool = 13 tool descriptions.** Sizeable but
tractable; model selection in Phase 4 matters more than I originally
estimated.

### Stays-gone, stays-out items (don't accidentally restore)

- **`set_timer` tool** — removed 2026-05-28 in favor of HA-native timer
  intents. Survives the refactor as removed. HA Assist handles timers.
- **`coding` / `devops` agents** — devstral is a separate harness;
  coordinator doesn't know about it. Code questions via voice/UI get
  gemma's general capabilities.
- **`search_health_data` and `query_bloodwork`** — defined in
  `orchestrator/tools.py` but never wired to an agent (README roadmap
  has them under "In progress"). Stay dormant in this refactor; wiring
  them up is out of scope and can happen later as straight additions.

### Latency math, revised

With most agents collapsing to bare tools, the coordinator does **one
LLM call to decide-and-invoke + one synthesis call** — exactly the same
shape today's agents do. The +200–500 ms penalty I projected was for
the worst-case 12-tool-selection load.

For simple queries that hit one obvious tool (most voice queries),
expect parity-or-faster vs v1 (no router step). For ambiguous queries,
expect parity (the coordinator does the work the router used to do, in
the same forward pass it would have done anyway).

### Benchmark variance handling

Models are non-deterministic. Each benchmark query runs **3 times,
median taken** for headline timings; all three results stored. Same
for v1 baseline. Adds a few minutes to bench runtime — standard.

### Worst-case for model upgrade

Phase 4 benchmarks at least: `gemma-4-e4b` (current), `mistral-nemo`
(already loaded, unassigned), and 1–2 new candidates I find via
research. If no candidate clearly beats gemma-4-e4b on the benchmark
suite, **stay on gemma-4-e4b**. The refactor doesn't depend on a model
swap; that's an optional follow-on.

### Parallel tool execution — pros/cons (for future reconsideration)

Captured per operator request.

**Pros:**
- Cross-domain queries collapse from sequential sum to max-of-parallel.
  "Weather + sleep score" goes from ~2× single-tool latency to ~1×.
- Modern LLMs are trained to emit multiple tool calls per turn; sequential
  execution wastes that signal.
- Free win for tool calls that don't hit the local LLM (httpx, SearXNG,
  NWS, HA REST) — they can genuinely run concurrently.

**Cons:**
- llama.cpp servers handle one request at a time per the current systemd
  setup. Two concurrent calls that both need the same model serialize at
  the server. Only the LLM-light fetches benefit.
- The synthesis step still serializes (need all results before composing),
  so parallelism only shortens the fetch phase, not the total.
- More moving parts in the orchestrator — current pipeline generator is
  strictly sequential and easy to debug.

**When to reconsider:** when there's a second LLM server or when the host
has the horsepower to run multiple model instances concurrently.

---

## 9. Autonomous decision log (populated during the run)

- **2026-05-30** — Created `feature/agent-refactor` branch from `main` to
  isolate refactor work. Operator mentioned they'd make a branch but I
  started before they got to it; this is the safer default than working
  on `main`. They can rebase/rename later if desired.
- **2026-05-30** — First v1-baseline bench run had to be aborted ~50%
  through: `gemma-4-e4b` server drifted into the `<unused49>` token-spam
  state after several hours of uptime (same symptom as 2026-05-28).
  `lookup_news_brief` and subsequent queries returned garbage. Stopped
  bench, restarted llama-gemma3-4b, llama-gemma4-e4b, llama-mistral-nemo,
  re-launched. Procedural lesson: every bench cycle should
  pre-restart-and-warm the llama servers for reproducibility. Adding that
  to the bench-cycle helper after Phase 0 lands.
- **2026-05-30** — Phase 3 (per-client sessions) implemented as part of
  Phase 1+2 batch since it's a tiny change. The shims (`/api/chat`,
  `/v1/chat/completions`) now use the full `messages` array as
  conversation context — previously dropped. The shim clients (HA voice,
  OpenAI-compatible callers) effectively own their session state since
  they re-send the whole conversation each request, so no server-side
  SessionStore needed. `/message` keeps its global history list as the
  `chat_ui` session. This satisfies the operator's "voice gets its own
  session distinct from chat-UI" requirement.
- **2026-05-30** — Phase 4 model candidate plan: only bench locally-loaded
  models first (`gemma-4-e4b` current, `mistral-nemo` unassigned). Skip
  downloads until v2 baseline shows whether the larger context surface
  needs more horsepower.
- **2026-05-30** — Fast-path (Phase 2.5) safelist limited to tools whose
  arguments are derivable without an LLM: `web_search` (raw query as
  `query` arg), `shopping_list_view`, `query_hottub`, `get_kronk_context`,
  `get_weather` (defaults home location). Other tools (e.g. `query_health`
  needing `metric`/`days`, `shopping_list_add` needing `items`) fall
  through to the coordinator even on a high-confidence match. Avoids
  shipping bad args.
- **2026-05-30** — Coordinator-tool composition: defined the v2 coordinator
  tool surface in `coordinator.COORDINATOR_BARE_TOOLS` (12 entries) +
  `COORDINATOR_AGENT_TOOLS` (1 entry: research). System prompt
  (`COORDINATOR_SYSTEM_PROMPT` in `coordinator.py`) instructs the model
  about when each tool is appropriate, including an explicit "do NOT set
  timers — HA handles timer intents natively" line to defuse any model
  attempt to call a removed tool.
- **2026-05-30** — Coordinator context handling: passes `context` as
  actual `messages` entries (`role: user/assistant`) rather than
  smushing into a system prompt. Better for chat-history-aware models.
  System messages from clients (HA, OpenAI) are dropped — Kronk's own
  system prompt is authoritative.
- **2026-05-30** — Initial v2 smoke test showed `get_weather` query
  end-to-end in ~4.5s vs v1's 13-18s — coordinator-pick-and-call is
  faster than router-then-agent-pick. Encouraging but not conclusive
  until the full bench runs.
- **2026-05-30** — v2-coord-gemma full bench completed. Mixed:
  - `api_chat` p50: 7.88s (v1) → 6.49s (v2) — faster on average
  - `message` p50: 7.98s (v1) → 9.44s (v2) — slower (but /message
    stays on v1 in this pass, so the slowdown is noise from the
    re-run; not a real comparison)
  - api_chat p95: 19.56s → 30.09s — heavier tail, driven by
    `research_voter_guide` going 14s → 43s in v2
  - **Quality regression: 16.7% of api_chat v2 trials failed** with the
    model emitting raw `<|tool_call>...` template text instead of
    structured tool_calls. gemma-4-e4b's tool-calling is fragile when
    given 13 tools to choose from. v1 (with 1–6 tools per agent) hit
    this ~3% of the time.
- **2026-05-30** — Attempted to fix the template-leak by switching
  coordinator to `mistral-nemo` (already-loaded 12B Q8). Hit a Jinja
  template error: mistral-nemo requires tool_call IDs to be exactly
  9 alphanumeric chars; our existing code used `call_<uuid12>`
  (17 chars + underscore) which the template raises on. Patched
  `orchestrator/llm.py` to always normalize tool_call IDs to 9-char
  alphanumeric hex (overriding whatever the model emits). Accepted by
  every other model too. With that fix, mistral-nemo coordinator works
  cleanly — 3/3 weather smoke tests passed with detailed answers, no
  template leaks.
- **2026-05-30** — Discovered the fast-path **synthesis** call was also
  hitting Mistral's strict template (used invalid message shape
  `[system, user, tool]` without an assistant→tool_call pairing, and
  used a non-9char tool_call_id "fastpath"). Patched
  `coordinator.coordinator_stream` to construct a valid 4-message
  conversation `[system, user, assistant(tool_call), tool]` with a
  proper 9-char ID. Caught when reviewing v2-nemo+fp results showed
  3 queries (weather_default, shopping_list_view, hottub_status)
  returning fast-path synth errors as response text.
- **2026-05-30** — `benchmarks/run_cycle.sh` was not forwarding
  `KRONK_FAST_PATH_ENABLED` to compose. First v2-nemo run had fast-path
  on (unintended); re-ran with fast-path explicitly off for clean
  isolation. Cycle script now forwards all relevant env vars.
- **2026-05-30** — **Refactor verdict: do not flip default to v2 yet.**
  Full report at `benchmarks/REPORT.md`. Summary:
  - v1 baseline: 7.88s p50 api_chat, 88% pass
  - v2-gemma+fp: 6.49s p50 but **only 67% pass** (gemma template-leak
    failures get worse when given 13 tools, even fast-path can't
    prevent it because gemma also leaks in the synth step)
  - v2-nemo+fp: 10.51s p50 (33% slower than v1), 92% pass
  - No v2 variant is unambiguously better than v1
  - Architectural code stays in place behind `KRONK_COORDINATOR` flag
  - Restored orchestrator to `KRONK_COORDINATOR=v1` after bench
- **2026-05-30** — Recommendations (in priority order, all deferred for
  operator return):
  1. Try **Qwen 2.5 7B Instruct** as a v2 coordinator candidate — small,
     well-regarded tool-caller. May give us gemma-speed + nemo-reliability.
  2. If Qwen doesn't pan out, write a server-side parser for gemma's
     template-leak output (~50% recovery rate estimated).
  3. Tighter coordinator system prompt to reduce nemo's verbosity for
     status queries.
  4. Defer Phase 5 (research planning agent) until a coordinator is
     chosen — planning compounds the model-speed cost we saw.

---

## 10. Definition of done

- `v2` pipeline serves the benchmark suite with quality flags ≥ v1 on
  average (no regressions on weather/health/finance happy paths).
- Cross-domain queries that v1 couldn't serve correctly now invoke
  multiple tools and synthesize coherently.
- Voice path (`/api/chat`) total-duration p50 within +500 ms of v1 baseline.
- Per-client sessions isolated (a `/message` query doesn't pollute a
  `/api/chat` query's history, demonstrated by test).
- Benchmark report committed to `benchmarks/comparison-*.md`.
- Decision log §9 complete.
- This document updated to reflect actual implementation (anywhere I
  diverged from the plan).
