# Feature: Agents-as-tools routing (self-healing router misses)

**Shipped:** 2026-06-12 · **Origin:** `../../TECH_DEBT.md` [ROUTING-01] · **Incident:** trace `bb8bd8b7` ("I need to search…")

## What it does

When the phase-1 router (gemma-3-4b) sends an ambiguous query down the
`direct` path, the coordinator (gemma-4-e4b) can recover by *delegating*: it
carries `ask_<agent>` tools (one per specialist, built from
`agents.COORDINATOR`), so a router miss becomes an ordinary tool call to the
right specialist instead of a dead end.

## Why it exists

Gate-based routing classifies **before** any LLM reasoning. Queries that are
factual-but-ambiguous ("Why is the US at war with Iran?") can't reliably be
sorted into "needs live data" vs. "answerable from training" at the gate.
The failure that named the problem: a query misrouted to `direct` produced
the literal answer "I need to search…" — the model knew what it needed and
had no way to get it.

Considered and rejected: full coordinator-first design (coordinator sees
every query, decides tool use itself) — more robust but pays coordinator
latency on every request. The shipped hybrid keeps cheap gate routing for
clear-cut domains (health, home, finance, coding, devops) and gives only the
direct path the delegation escape hatch.

## Verified behavior

- Misroutes self-heal via `ask_research`.
- Spurious delegation on pure-knowledge questions: 1/5.
- Delegations observable in Langfuse as `agent.*` spans under direct-routed
  traces.

## Known remaining gap

A multi-domain query routed to a **specialist** still gets a single-domain
answer — specialists have no peer-delegation tools. On the roadmap as "peer
agent handoffs" (Later); attack when it bites in practice.

## Blog hooks

- Routers lie: giving a small-model pipeline an escape hatch instead of a
  better classifier.
- The agent that announced its tool call instead of making it (already in
  `../BLOG_TOPICS.md` — the budget-cliff story is a sibling of this work).
