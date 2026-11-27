# M37 pass criteria: the clock moves to the end of the prompt

Source: decision #95, from `M36-review.md` point 4: "The clock in every
prompt sits at token 893 of about 4,230, so the model's prompt cache is
emptied every minute. Move it to the end as its own item (latency), or
leave?" Drew, 2026-11-26: "Item: move it".

Handoff line: the time of day leaves the system text and goes just
before the request, so a request a minute after the last one reuses the
cached prompt instead of evaluating about 3,300 tokens again.

Plan doc: this file. Tier 3 (every agent's prompt; the agent loop): a
design review, build, one test, a fresh narrow test after any fix.

## What the investigation found (coordinator, 2026-11-26)

- `orchestrator/agents.py` `kronk_facts()` writes "Today is …; the local
  time is 8:14 PM EST." to the minute (M35: the house's zone).
  `run_stream` builds the system text as the agent's prompt, then
  `kronk_facts()`, then the escalation text, any extra, and for `home`
  the weather block. The chat template renders the tool definitions
  after the system text (`M36-review.md` point 4), so a change at token
  893 discards the cache for the rest of the system text and every tool
  definition.
- Measured on dev after M36 (`M36-promote.md`), home route, the Boston
  follow-up through `/dev/api/chat`: same minute, the first round
  cached 4,183 of 4,184 tokens and took 2.46 s; a minute later it cached
  893 of 4,184 and took 4.95 s. The whole request: 5.0 s against 8.08 s.
  Speech is a request that usually comes minutes after the last one, so
  it pays the cold cost most of the time.
- The date changes once a day; only the time changes each minute.

## Plumbing (coordinator's decisions, open to the review)

1. The date stays in `kronk_facts()`. The time of day leaves the system
   text and goes after the history, just before the request, in the copy
   of the messages sent to the model only. The text the router, the
   session store, the logs and the traces see is unchanged.
2. The form (a line at the head of the last user message, or a message
   of its own before it) is the review's to measure against the chat
   template llama.cpp applies to `gemma-4-e4b`, and Talkie's. Whichever
   it is, it is one function used by every path that calls
   `kronk_facts()`.
3. One variable: the home weather block stays where it is. The review
   reports how often it changes the system text in a day; moving it is
   another item if the number says so.

## Criteria

1. Unit: for the coordinator, a pinned specialist and a delegated
   specialist, the system text holds the date and no time of day; the
   time is in the agreed place, after the history; the request text the
   router and the session store see is unchanged.
2. On dev, home route, ten pairs of requests a minute or more apart,
   with no `/api/status` call between them (one empties the cache:
   `M38-criteria.md`): the first round of the second request caches at
   least 4,000 tokens (it cached 893); first-round times and request
   times quoted, before and after.
3. The time is still right, ten times each through `/dev/api/chat` and
   `/dev/voice/api/chat`: "what time is it, Kronk" (typed; a spoken one
   is answered by Home Assistant) gives the minute of the clock; "what
   is the date tomorrow" after 19:00 EST gives the next local day; the
   M35 corpus rows pass.
4. The voice corpus and the silent replay before and after: no row
   changes route or tool.
5. `pipeline_bench.sh` on dev before and after (the coordinator's):
   every prompt answers; medians within noise or better.
6. The suite passes; last lines quoted.
7. By the coordinator at promotion, on production: a time question on
   both shims; two home requests a minute apart, the second's first
   round cached at least 4,000 tokens.

## Out of scope

- The weather block's place (plumbing 3).
- Any other change to a prompt's text.
- llama.cpp's cache settings.

## Rulings

(none yet)
