# Agent Architecture Research

Notes from reading *Agentic Design Patterns* (Antonio Gulli, 2025) against
Kronk's current architecture. Captured 2026-05-30 as input for a future
refactor branch.

## The question this is trying to answer

Kronk today: a **router** (`gemma-3-4b`, ~200 ms) classifies each query into
one of eight specialist agents (`home`, `research`, `health`, `assistant`,
`finance`, `coding`, `devops`, `talkie`) or `direct` (coordinator-only). Each
agent is `gemma-4-e4b` with a narrow tool allow-list, except `coding`/`devops`
which use `devstral-q4` and `talkie` which uses the `talkie` model.

This works, but routing failures are a real pain. A recent example: "What is
the meaning of life according to Conan, the Barbarian?" routed to `direct`,
and gemma-4-e4b approximated the famous quote from training instead of
calling `web_search`. The user had to add "search online" — which triggered
a regex shortcut — for the research path to fire.

The operator's intuition: maybe Kronk shouldn't have a separate "research
agent" at all; a sufficiently clever model with tools should call
`web_search` on its own when needed. This is the standard pattern in modern
production assistants (Claude, ChatGPT). Is that the right move? What other
patterns are worth knowing? That's what this research is about.

## What the book gives us

The book is structured around 21 patterns. Mapping Kronk:

| Kronk concept                           | Book pattern                              | How                                       |
| ---                                     | ---                                       | ---                                       |
| Router (gemma-3-4b)                     | **Routing** (Ch 2) — LLM + Rule hybrid    | LLM classifier with regex shortcuts       |
| Specialist agents (home, research, …)   | **Multi-Agent / Hierarchical** (Ch 7)     | Static delegation, one agent per query    |
| `set_timer`, `web_search`, …            | **Tool Use** (Ch 5) — Function Calling    | Standard pattern                          |
| Per-request global `history`            | **Memory** (Ch 8) — Short-term only       | No long-term/vector store                 |
| `kronk_facts()` ambient context         | **Context Engineering** (Ch 1 intro)      | System-prompt-time fact injection         |

Patterns Kronk doesn't use that the book covers and that are relevant:

- **Planning** (Ch 6) — agent generates a plan, executes it, adapts.
- **Reflection** (Ch 4) — agent critiques and revises its own output.
- **Agent as a Tool** (Ch 7 / ADK `AgentTool`) — wrap an agent so another
  agent can invoke it like a function.
- **Parallel agents** (Ch 7 ADK `ParallelAgent`) — run multiple agents
  concurrently for one query.
- **Embedding-based** or **fine-tuned-classifier** Routing (Ch 2 variants).
- **Long-term memory / RAG** (Ch 8, 14) — user preferences, persistent
  recall across conversations.

## The load-bearing insight (Ch 6 Planning)

> *"the decision to use a planning agent versus a simple task-execution
> agent hinges on a single question: does the 'how' need to be discovered,
> or is it already known?"*

Translated to Kronk:

- **Weather, timer, shopping list, hot tub, query_health, query_finances** —
  the "how" is known. One tool call + synthesis. A fixed agent (or even
  just a tool call) is correct. No planning needed.
- **Research / news / "latest on X" / lookups requiring multiple sources** —
  the "how" is genuinely unknown per query. How many searches, which sources,
  when to stop. This is exactly where a **planning agent** earns its keep.

Kronk's `research` agent today is not a planner. It's a tool-using agent
that loops until `MAX_TOOL_ROUNDS` and then falls into a forced synthesis.
The 41-second AVGO query with 3 plan rounds + forced synthesis is the
symptom: hitting the ceiling instead of intelligently stopping.

So the operator's intuition is half right. The fix isn't *removing* the
research agent — it's *promoting it to a planner*. The other domains stay
as fixed-recipe tool callers.

## Four patterns the book offers for Kronk

### Pattern A — Agent as a Tool (Hierarchical-with-tools)

The book's modern multi-agent shape. Instead of a router *replacing* the
coordinator, the coordinator *owns* the entry path and has each specialist
agent registered as a **tool**. The coordinator decides per query: answer
directly, call one agent-tool, or chain multiple.

Implications for Kronk:

- The Conan-quote routing problem dissolves. The coordinator decides "I
  should call the research agent-tool" without a separate router step.
- Cross-domain queries become natural. "Plan my workout based on the
  weather and my sleep score" becomes one query that invokes both
  weather-tool and health-tool, then synthesizes.
- Specialization is preserved. Each agent-tool keeps its narrow tool
  allow-list, prompt, and model. Devstral for code, gemma for general,
  talkie for talkie.
- The router LLM call goes away. The coordinator's normal forward pass
  *is* the routing decision.

Costs:

- The coordinator now sees ~8 tool descriptions (agent-tools) instead of
  ~3 ad-hoc tools. Worth benchmarking with gemma-4-e4b before assuming
  it handles the larger surface gracefully.
- The current per-agent specialization-via-prompt is harder to enforce
  (the coordinator's prompt now bears the "decide which specialist"
  weight).

### Pattern B — Promote research to a Planning agent (Ch 6)

Replace the loop-until-MAX_TOOL_ROUNDS pattern with a true planner.
Two variants the book covers:

- **Pre-plan then execute** — write the research plan first, execute it,
  summarize. Predictable, easy to debug.
- **Iterative plan-execute-replan** (Google DeepResearch example, Ch 6) —
  generate plan, execute one step, evaluate, identify gaps, refine plan,
  repeat. Closer to what humans do.

For Kronk's voice constraint (~15 s ceiling before it feels broken),
variant 1 is more pragmatic. Variant 2 fits chat-UI queries where 30-60 s
is tolerable for a thorough answer.

### Pattern C — Embedding-based Routing for the simple cases

If a router survives, the book's Ch 2 says LLM routing is one of *four*
mechanisms. The cheapest fast one: embed each agent's `routing_hint` once
at startup, embed each query at request time, cosine-similarity to pick.
Sub-millisecond, deterministic, no LLM call. The Conan query would have
matched `research` because its embedding is closer to "quotes, lyrics,
biographies" than to "lookups against the live web."

Not as smart as an LLM classifier on weird queries, but the failure mode
is "boring miss," not "model says nonsense." Worth knowing exists even if
Pattern A is adopted — fallback if coordinator-call latency turns out to
be too high.

### Pattern D — Reflection (Ch 4) on the research synthesis

For the forced-synthesis problem: after synthesis, the model critiques
its own answer ("does this actually contain the verbatim quote? did I
cite a source?"). If not, retry with a tighter prompt. Costs a second
LLM call but catches "I gave up too soon" failures.

## Recommendation: stage in two passes

### Pass 1 — Pattern A (Agent-as-Tool refactor)

Highest leverage, contained scope.

- Define each current agent as a tool the coordinator can call. The
  agent's existing system prompt + tool allow-list become its callable
  signature.
- Drop the router LLM entirely; the coordinator becomes the dispatcher.
- Keep the regex shortcuts (talkie-explicit, direct-override) as
  pre-checks before the coordinator — those are still cheap wins.
- Devstral path: wrap devstral as a callable tool the (gemma) coordinator
  invokes for code queries, *or* keep devstral as its own coordinator-tool
  that the gemma coordinator delegates to. Open question.

### Pass 2 — Pattern B (Promote research to Planning)

After Pass 1 lands.

- The `research` agent-tool internally becomes a planner: generate plan →
  execute steps → synthesize with citations.
- Lets the brittle `MAX_TOOL_ROUNDS` escape hatch be replaced by an
  explicit "I've gathered enough" signal from the planner.

Patterns C and D are good to be aware of, not to reach for yet.

## Open questions / clarifications needed before code

1. **Voice vs chat-UI traffic split.** Voice has a hard ~15 s ceiling.
   Pattern A with a single coordinator pass + 1-2 agent-tool calls should
   fit; Pattern B's planner would not for voice. If voice dominates, gate
   Pattern B to chat-only or to explicit "research this" requests.

2. **Coordinator model size.** Pattern A loads more cognitive work onto
   the coordinator (decide among ~8 agent-tools, not 2-3 direct tools).
   gemma-4-e4b *might* handle it; `mistral-nemo` (currently unassigned in
   the inventory) is larger and likely more reliable on tool selection.
   Worth a benchmark before committing.

3. **History / memory model.** Pattern A is a natural moment to swap the
   chat UI's global `history` list for a proper per-session memory model
   (Ch 8 Session/State/Memory). Separate bigger lift; might be worth
   bundling or worth saving for a Pass 3.

4. **Pre-Pass-1 reads.** Worth re-reading Ch 4 (Reflection) and Ch 17
   (Reasoning Techniques) before firming up Pass 2. They might sharpen
   the planning recommendation. Not blocking Pass 1.

## Patterns from the book that are NOT relevant to this refactor

Listed so we don't get distracted:

- **Prompt Chaining** (Ch 1) — Kronk already uses this implicitly in the
  router → agent → synthesis flow. Nothing to do.
- **Parallelization** (Ch 3) — only matters once cross-domain queries are
  common, which Pattern A enables but doesn't force.
- **Learning and Adaptation** (Ch 9) — Kronk doesn't learn from past
  interactions. Possible future, not relevant to this refactor.
- **Inter-Agent Communication / A2A** (Ch 15) — Pattern A's agent-as-tool
  is the lightweight version of A2A. Full A2A is overkill for single-host.
- **Resource-Aware Optimization** (Ch 16) — relevant for multi-tenant
  cloud, not for one home user.
- **Guardrails** (Ch 18) — Kronk's hallucination guardrails are already
  in place (system prompt + tool-status lines per the README). Not the
  blocker here.

## What we already do well (so we don't accidentally regress)

- **Hallucination guardrails** — three-layer pattern in the README
  Design Decisions section. Pattern A must preserve these.
- **`kronk_facts()` ambient context** — single source of truth for
  "Kronk lives in Laurel, MD" reaching every code path. Pattern A keeps
  this; the coordinator just gets it more directly.
- **Regex shortcuts for talkie / direct override** — deterministic, fast,
  catch the obvious. Worth keeping pre-coordinator.
- **Tool dispatching via `tool_service`** — the HTTP indirection makes
  tool sandboxing and metrics easy. Don't collapse this.
