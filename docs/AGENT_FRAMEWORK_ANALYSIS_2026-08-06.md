# Agent-framework analysis — should Kronk adopt one? (2026-08-06)

**Status:** Analysis only — no code changed. Requested by the operator:
"we have a homegrown system [because] the available frameworks at the time
were messy and in flux — see if there are opportunities for simplifying
the codebase and/or adding flexibility by introducing an existing agentic
framework."

**Verdict up front:** Do **not** adopt a framework wholesale now. The
homegrown loop is small (~1,250 lines of genuinely agentic code), tested
(37 loop/routing tests), and most of its bulk is *incident-hardened,
Kronk-specific behavior that no framework ships*. Wholesale adoption would
delete roughly 600–800 generic lines, then force re-implementing the
hard-won guardrails as framework extensions, while adding a large
dependency tree against the pin-everything tenet and putting a
still-moving abstraction between the operator and the loop that tenet 5
says to own. The landscape *has* matured since the build — the premise
"messy and in flux" is mostly no longer true — so this doc also names the
concrete triggers that would flip the verdict, and four à-la-carte
improvements (three framework-free) that capture most of the value now.

---

## 1. What the homegrown system actually is

Inventory of the agentic core (orchestrator only; tool_service etc. are
plain HTTP services and framework-irrelevant):

| File | Lines | Role | Framework-replaceable? |
|---|---|---|---|
| `llm.py` | 189 | OpenAI-compat streaming client; accumulates `tool_calls` deltas by index, recovers missing ids / bad JSON args | **Yes** — this is exactly what every framework's model adapter does |
| `agents.py` | 794 | `AGENTS` registry (~200 lines of prompts/config), agents-as-tools defs, router prompt builder, `run_stream()` loop (~270 lines), narration + terminal-speech mapping | **Partially** — the loop skeleton yes; the guardrails and product behavior no |
| `routing.py` | 193 | Deterministic regex shortcuts + 4B LLM classifier | **No** — this is domain logic; frameworks call it "routing" but ship nothing like the shortcut table |
| `tools.py` | 891 | ~450 lines of JSON-schema dict literals + ~400 lines of HTTP handlers + `_HANDLERS` registry | **Schemas yes** (decorator-derived), handlers no (domain logic) |
| `telemetry.py` | 193 | Langfuse span wrapper (v3/v4 normalization, no-op degradation) | **Yes, eventually** — via OTel auto-instrumentation (see §5.2) |
| `main.py` | 971 | Transports (SSE, OpenAI shim, Ollama shim), pipeline driver | **No** — transport framing is orthogonal to any framework |

The genuinely generic part — streaming accumulation, the
call-LLM/execute-tools/loop skeleton, span plumbing — is maybe **600–800
lines**. Everything else is either configuration that exists under any
framework (prompts, schemas, handlers) or behavior that exists *because a
specific incident happened*:

- **Terminal tools** (`AgentConfig.terminal_tools`, `agents.py:154`) —
  tool result is spoken verbatim and ends the turn; kills hallucinated
  "now playing" after failures. Structural, not prompt-based.
- **Exact-duplicate call dedup** (`agents.py:441,624`) — canned "already
  called" result instead of re-execution.
- **Repeat-call nudge** (`agents.py:669`) — call #3 of the same tool gets
  a stop order appended *to the tool result* (2026-07-05 forecast
  incident: prompts alone never stopped it).
- **Forced synthesis with tool-syntax scrubbing** (`agents.py:684-731`) —
  budget exhaustion triggers one tools-disabled call with an explicit
  "budget exhausted" user message, then regex-scrubs leaked
  `<tool_call>` syntax (2026-06-12 budget-cliff incident).
- **Gemma/llama.cpp accommodations** — router prompt embedded in the user
  turn (Gemma rejects system messages via LiteLLM, `routing.py:158`),
  consecutive same-role turn merging (`routing.py:148`), append-only
  history ordering to preserve llama.cpp's prompt-cache prefix
  (`main.py:443`, `agents.py:504`).
- **Narration events** ("searching the web for…") and **error-style
  rendering** (debug/friendly per transport) — voice-product behavior.
- **`_llm_lock`** — one GPU; the lock *is* the scheduler
  (`TECH_DEBT.md`, considered-and-rejected). Orthogonal to frameworks but
  simplifies things they make hard (module-global telemetry root).

The custom event vocabulary (`token`/`narration`/`error`/`done`) is the
seam the three transports consume. Any framework would sit *behind* this
seam, its event stream mapped into it — the transports and `main.py`
would not shrink at all.

## 2. The 2026 landscape — what changed since the build

The "messy and in flux" era is largely over. As of August 2026:

- **Pydantic AI** — v1.0 Sept 2025, **v2.0 June 2026** (current ~2.24),
  with a stated version policy (no intentional breaking changes in
  minors). Provider-agnostic; `OpenAIChatModel` works against any
  OpenAI-compatible endpoint including a LiteLLM proxy. Streams text and
  tool-call events; `agent.iter()` exposes node-by-node loop control;
  *output functions* are Kronk's terminal-tool pattern built in (model
  calls the function, its return ends the run); schema-validated tool
  args with model-retry; `history_processors` could host the
  alternation-merging; native OTel instrumentation that Langfuse ingests
  directly; `pydantic_evals` for eval harnesses; MCP client support.
- **OpenAI Agents SDK** — stable, minimal (agents/handoffs/guardrails/
  sessions), works with local models via its LiteLLM integration.
  Tracing defaults to the OpenAI platform (redirectable, but that default
  is hostile to local-first). Ecosystem reports still show sharp edges
  running agent loops through OpenAI-compatible proxies (e.g.
  `finish_reason` handling ending loops prematurely — seen across
  multiple stacks, not just this SDK).
- **LangGraph** — 1.0 (Oct 2025); durable graphs, checkpointing,
  human-in-the-loop. Built for stateful multi-step workflows at team
  scale; brings the LangChain ecosystem's dependency mass.
- **CrewAI / AutoGen → Microsoft Agent Framework** — role-based
  multi-agent teams; wrong shape for a router → specialist pipeline.
- **smolagents** — code-execution-centric agents; wrong shape.

The one candidate that actually fits Kronk's shape (typed, thin, local-
model-friendly, OTel-native, loop-controllable) is **Pydantic AI**. Every
comparison below uses it as the stand-in for "adopt a framework."

## 3. Feature mapping — Kronk loop vs. framework equivalent

| Kronk behavior | Pydantic AI equivalent | Migration cost |
|---|---|---|
| Streaming token/tool-call accumulation (`llm.py`) | Built in | Delete ~190 lines |
| Tool loop with round budget | Built in (`UsageLimits`) | Delete ~150 lines |
| Terminal tools | Output functions | Rewrite, small |
| Forced synthesis + scrub on budget exhaustion | **Not built in** — catch `UsageLimitExceeded`, re-run with tools stripped + message history | Custom extension |
| Exact-dup dedup + repeat-call nudge | **Not built in** — custom tool wrapper/middleware | Custom extension |
| Narration strings per tool | Map from its event stream | Rewrite, small |
| Agents-as-tools (`ask_*`, depth-capped at 2) | Agent delegation (agents called from tools) — same pattern, hand-rolled either way | Neutral |
| Router (regex shortcuts + 4B classifier) | Could become an agent with an enum output type + validation retry | Neutral — current `VALID_ROUTES` fallback is cheaper than a retry on a 4B |
| Gemma system-msg / alternation quirks | `history_processors` + LiteLLM hooks (unchanged) | Rework, fiddly |
| llama.cpp prompt-cache append-only ordering | Possible but fights the framework's message assembly | **Risk** — silent perf regression if the prefix stops being stable |
| Langfuse spans w/ TTFT, per-round generations | OTel instrumentation → Langfuse OTLP | Better than current (see §5.2) |
| Bad tool-args JSON → `{}` + warning | Validation error → model retry | Framework behavior is *better* (see §5.3) |
| Sequential tool execution in a round | Concurrent tool execution | Framework behavior is *better* (see §5.4) |
| Error-style rendering, event vocabulary, transports | Nothing — stays custom | Zero savings |

Net: adoption deletes the two files that are least likely to break
(`llm.py` has been stable since June; the loop skeleton is pinned by 37
tests) and preserves every line that actually costs maintenance
(prompts, guardrails, handlers, transports, routing).

## 4. Costs of adoption

1. **Dependency mass vs. the pinning tenet.** The orchestrator has
   **eight** direct deps, hash-pinned. Pydantic AI brings pydantic,
   the OpenAI client, OTel packages, and friends — a much larger
   hash-pinned lockfile to regenerate and audit on every deliberate
   upgrade day, for a component that currently needs none of it.
2. **Churn is reduced, not gone.** V2.0 landed **six weeks ago** with
   breaking changes (dataclass kwargs, parts typing, instrumentation
   default). The version policy is reassuring; a major-version boundary
   six weeks old is not where "pin everything; upgrade deliberately"
   wants to stand.
3. **Tenet 5 — own the loop.** "On 4B-class models, if the loop lets the
   model do the wrong thing, it eventually will — change the loop, not
   the prompt." Every incident fix in §1 was a loop change made in an
   afternoon because the loop is 270 readable lines in one file.
   `agent.iter()` mitigates this (you *can* drive the graph manually),
   but every future guardrail becomes "find the extension point" instead
   of "edit the loop."
4. **Regression risk in exactly the wrong places.** The behaviors most
   likely to regress in a migration are the incident-driven ones — the
   voice path's terminal-tool speech, the prompt-cache ordering, the
   scrubbed forced synthesis. The test suite pins them, but the live
   battery (`pipeline_bench.sh`) and a manual voice pass would all need
   re-running for a change with no user-visible payoff.
5. **Latency.** Voice budget is already at the edge (15–25 s worst
   case). Framework niceties like validation retries cost a full extra
   4B round each. Everything would need tuning off or down.

## 5. What's actually worth doing

The real simplification and flexibility opportunities, ranked. Three of
four need no framework.

### 5.1 Decorator-based tool registry (framework-free, ~half a day)

Adding a tool today touches up to seven places: `TOOL_DEFINITIONS`,
the handler, `_HANDLERS`, `TOOL_TIMEOUTS`, `agent.tool_names`,
`_tool_narration`, and the agent's system prompt. The name is duplicated
across five of them — the codebase's own tenet 8 ("one source of truth
per fact") argues against this. A ~40-line homegrown decorator:

```python
@tool(
    schema={...},            # or derived from the signature
    timeout=20,
    narration=lambda a: f"putting on {a.get('query', 'music')}",
    terminal=True,
)
async def play_music(client, args): ...
```

collapses definition, registration, timeout, and narration into one
block per tool. This is the single highest-value simplification
available and it's exactly the part of framework ergonomics worth
stealing without the framework. (`terminal` stays a per-agent decision
today — keep it on `AgentConfig` if that distinction matters.)

### 5.2 OTel-based instrumentation for Telemetry v2 (roadmap item 6)

Item 6 already says the Langfuse setup is a prototype and re-founding it
is on the table. Whatever v2 becomes, emitting **OpenTelemetry spans**
instead of hand-rolled Langfuse SDK calls future-proofs it: Langfuse
ingests OTLP natively, and so does anything that might replace it. This
could delete most of `telemetry.py`'s SDK-normalization layer
independently of any agent framework — and if a framework is ever
adopted later, its native OTel output plugs into the same collector.
Fold this into the Telemetry v2 plan doc's requirements pass.

### 5.3 Fail loud on unparseable tool args (framework-free, ~1–2 h)

`llm.py:113-120` recovers bad tool-call argument JSON as `{}` with a
log warning — the tool then runs with defaults (weather silently uses
the home location; others return confusing "X is required" strings).
That's a tenet-7 gap the frameworks get right: surface it *to the
model* as an explicit tool-error result ("your arguments were not valid
JSON — re-issue the call") instead of executing with `{}`. One round of
self-correction, structural not prompt-based, and cheap to test.

### 5.4 Parallel tool execution within a round (framework-free, ~half a day)

The research prompt explicitly coaches "issue all independent lookups in
a single response — that costs one round instead of many"
(`agents.py:192`), but `run_stream` then executes those calls
**sequentially** (`agents.py:620`). Frameworks run them concurrently.
An `asyncio.gather` over the round's non-terminal tool calls (results
appended in call order to keep the message array deterministic) directly
cuts multi-lookup research latency — the loop most exposed to the voice
budget. Terminal tools and the dedup/nudge bookkeeping need care;
`_llm_lock` is unaffected (tools are HTTP calls to local services, not
LLM calls).

### 5.5 MCP client support — the actual flexibility play (evaluate later)

If "adding flexibility" means "adding integrations without writing a
handler + schema + service route each time," the 2026 answer is **MCP**,
not an agent framework. Home Assistant ships an MCP server integration;
Hue/calendar/etc. increasingly arrive as MCP servers. A homegrown MCP
client (the `mcp` package, one dep) could expose a server's tools into
the existing `TOOL_DEFINITIONS`/`execute()` seam — config instead of
code per integration, still fully local over the LAN. Caveats: MCP tools
are generic — anything needing Kronk's verify-the-effect pattern (the
`play_music` poll-for-`playing`) or terminal-tool treatment still wants
a hand-written wrapper. Worth a plan doc the next time a new integration
(roadmap: Hue, calendar) comes up; not worth building speculatively.

## 6. Triggers that would flip the verdict

Adopt (or re-run this analysis toward adopting) Pydantic AI if any of
these become true:

1. **Productize Kronk moves up the roadmap.** For an open-source product,
   a known framework outsources loop maintenance and gives contributors
   conventions and docs Kronk's bespoke loop can't. This is the
   strongest future argument for adoption.
2. **Peer agent handoffs get built.** Multi-agent topology
   (specialist↔specialist delegation, shared state) is where hand-rolled
   loops genuinely start to hurt and graph/handoff primitives start to
   pay. Today's depth-2 agents-as-tools doesn't qualify.
3. **The model tier jumps.** If the box moves to 8–30B-class agents,
   structured outputs and validation retries become reliable and cheap,
   and the 4B-specific guardrails (the main custom surface) matter less.
   The cost/benefit inverts.
4. **A second maintainer arrives.** The loop's readability currently
   serves one operator who wrote it. Shared maintenance changes the math
   on bespoke code.

If adopting: pilot on **one** agent (research — most rounds, no terminal
tools) behind the existing `run_stream` event vocabulary, with an
adapter mapping Pydantic AI's event stream to
`token`/`narration`/`error`/`done`. Routing, transports, and the other
agents stay untouched until the pilot survives `pipeline_bench.sh`
pre/post and a manual voice pass.

## 7. Sources

- [Pydantic AI](https://pydantic.dev/pydantic-ai) · [version policy](https://pydantic.dev/docs/ai/project/version-policy/) · [upgrade guide / V2 changes](https://pydantic.dev/docs/ai/project/changelog/) · [OpenAI-compatible models](https://pydantic.dev/docs/ai/models/openai/)
- [Langfuse: Comparing open-source AI agent frameworks](https://langfuse.com/blog/2025-03-19-ai-agent-comparison)
- [LangGraph vs OpenAI Agents SDK vs PydanticAI (2026)](https://open-techstack.com/blog/langgraph-vs-openai-agents-sdk-vs-pydanticai-2026/)
- [Running OpenAI Agents SDK locally with LiteLLM](https://getstream.io/blog/local-openai-agents/)
- [Example of OpenAI-compatible-proxy loop sharp edges (opencode #20719)](https://github.com/anomalyco/opencode/issues/20719)
- [8 Best Python AI Agent Frameworks Compared (2026)](https://fast.io/resources/best-ai-agent-frameworks-for-python/)
