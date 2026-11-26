# Feature: Agents-as-tools routing (self-healing router misses)

**Shipped:** 2026-06-12 · **Origin:** `../../TECH_DEBT.md` [ROUTING-01] · **Incident:** trace `bb8bd8b7` ("I need to search…")

> **Superseded in part (2026-08-18):** the LLM router this doc heals
> against no longer exists — routing is deterministic shortcuts or the
> coordinator, so the `ask_*` delegation described here is now the *main
> path*, not an escape hatch. See
> `coordinator-default-routing.md`. The "known remaining gap" below
> (specialist dead-ends) is that design's deferred Phase 2.

> **Update 2026-11-17 (sprint M7):** `routing.py` is now an ordered rules
> table (`routing.RULES`, 15 rules), and five of them (`playback`,
> `now_playing`, `rate`, `music_another`, `mm_update`) run their tool
> directly with no model call and outside the model lock. See
> `direct-dispatch.md`.

> **Update 2026-11-17 (sprint M19):** a 16th rule, `lead_in`, comes first:
> "ask Kronk to <request>" skips every other rule and gives the coordinator
> the request without the lead-in. See `lead-in.md`.

> **Update 2026-11-17 (sprint M14):** a 17th rule, `messy`, after `url`:
> a message with more than one sentence or more than 20 words goes to the
> coordinator whole, past every later shortcut. See
> `messy-transcript-gate.md`.

> **Update 2026-11-26 (work item M36):** how each agent is given the
> earlier turns. See "Conversation history" below.

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
- Delegations observable in Grafana (Tempo) as `agent.*` spans under direct-routed
  traces.

## Conversation history

Every agent gets the earlier turns as chat messages, after the system text
and before the request (`agents.run_stream(..., history_messages=...)`).
There is no other form: the `[Recent conversation context]` block that
used to be appended to a specialist's system text was removed in M36, and
`run_stream` raises if a caller passes turns as `context`.

| Who | Turns given |
|---|---|
| Coordinator | the whole window `_run_pipeline` was handed (chat page: `sessions.window`, 40 messages at most; shims: the client's array) |
| A pinned specialist (routed by a rule) | `sessions.specialist_turns(history)`: at most two whole exchanges from the end |
| A specialist asked through `ask_*` | none; the coordinator writes a query that stands alone |
| A direct-dispatch rule | none; no model runs |

`specialist_turns` rules: whole exchanges (a user message, then the
assistant's reply) taken from the end, two at most, so the window starts
on a user turn; user messages after the last reply are dropped first (a
shim client that resends the request leaves a copy there); the walk stops
at the first break in alternation (two user messages in a row, an empty
message, another role); each assistant turn is cut at
`ASSISTANT_PROMPT_CHARS`. Failure replies are kept. No tool call or tool
result is rebuilt into the history: for a terminal tool the stored reply is
the tool's own sentence, which is what the next turn needs.

Why (`docs/incidents/INVESTIGATION_2026-11-26_play_that_album.md`):
"play that album, in the kitchen" after a reply naming Pulse by Pink Floyd.
With the turns in the system text the home agent on `gemma-4-e4b` called
`play_music` for Pulse 0 of 6 times ("I need to know the name of the
album"); as chat turns, 6 of 6. The same held for "what's the weather
there tonight" after a Boston exchange (0 of 6, answered for the home
location; 6 of 6 as turns). A window that starts on an assistant turn
(`history[-5:]`, the old cut) gave the wrong album 2 of 18 times in the
design review (`docs/sprint/M36-review.md`).

Gotchas:

- **Speech does not get this for music.** The music blueprint hands a
  spoken "play ..." to Kronk through `conversation.process` with no
  conversation id, so Kronk sees one message. A follow-up Home Assistant
  passes straight to Kronk within its five-minute session does carry the
  conversation (in practice, weather).
- **Prompt cache.** As turns, a follow-up inside the same minute reuses the
  cached system text and tool definitions (cached about 4,160 of 4,230
  tokens, against about 1,976 with the old block, which sat before the tool
  definitions). Across a minute both fall to about 893 tokens, because the
  clock in `kronk_facts()` changes the system text.
- **Single-sentence checks cannot see it.** Layer one of the voice corpus
  and the live battery are single-turn. Rows with a `history` field are
  checked live by `scripts/voice_replay.py --history`
  (`voice-regression-corpus.md`).

## Known remaining gap

A multi-domain query routed to a **specialist** still gets a single-domain
answer — specialists have no peer-delegation tools. On the roadmap as "peer
agent handoffs" (Later); attack when it bites in practice.

## Blog hooks

- Routers lie: giving a small-model pipeline an escape hatch instead of a
  better classifier.
- "Play that album": the history was there all along, written into the
  system prompt where a 4B model never looked. Same words as chat turns,
  0 of 6 became 6 of 6.
- The agent that announced its tool call instead of making it (already in
  `../BLOG_TOPICS.md` — the budget-cliff story is a sibling of this work).
