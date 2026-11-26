# M36 design review: a follow-up can refer to the turn before

Reviewer: design review agent, 2026-11-26. Read-only; nothing built.

## Verdict

**Build with these changes.** The cause is right and plumbing 1 fixes it
on the chat page and the shims. Four changes before the build:

1. The window is the last **four** messages starting on a user turn, not
   five. A window that starts on an assistant turn is measurably worse
   (point 3).
2. The handoff line must not promise voice. A spoken "play that album"
   never carries history to Kronk; the music blueprint hands it over as a
   new conversation (point 5).
3. Criterion 4's examples do not test plumbing 1. Lights are not a Kronk
   tool, and "remove that" goes to the coordinator. Use the weather
   follow-up and the research follow-up named in point 7.
4. Add one count to criteria 2 to 4: replies that say an action was done
   with no tool call. It must be zero (point 4).

## How the measurements were made

In `kronk-orchestrator-dev-1`, the agent's system text built as
`run_stream` builds it (prompt, `kronk_facts()`, the escalation guidance,
the weather block for home), the agent's tools plus `escalate`, posted to
LiteLLM without streaming. No tool was executed; nothing played. "Turns"
means the earlier messages as chat messages before the request; "text"
means today's `[Recent conversation context]` block. "Pulse exchange" is
the incident's request and its failure reply. A production deploy
(`item-M32-step1`, the coordinator's) recreated LiteLLM during one run;
that run was repeated whole.

## 1. The cause is right; every path a specialist gets earlier turns

`orchestrator/main.py:470` is the only place a specialist is handed
history, and all four entry points reach it through `_run_pipeline`.

| Path | History it passes | Form at the specialist today | Changed by plumbing 1 | Should it |
| :-- | :-- | :-- | :-- | :-- |
| Chat page `/message` | `sessions.window`: 40 messages at most, starts on a user turn, assistant turns cut at 1,500 characters (`main.py:772`) | last 5 as system text | yes | yes |
| `/api/chat`, `/voice/api/chat` | `_shim_context`: every user and assistant message the client sent, no cap, no alignment, no cut (`main.py:873`) | last 5 as system text | yes | yes, with the rules in point 3 |
| `/v1/chat/completions` | the same `_shim_context` | last 5 as system text | yes | yes |
| A direct-dispatch rule (playback, now playing, rate, another, mirror update) | none; no model runs | none | no | no |
| `run_delegated` (`ask_*` from the coordinator) | `[]` (`agents.py:897`) | none in any form | no | no |
| Coordinator | the whole `history` as turns (`main.py:585`) | turns already | no | no |

`run_delegated` is not a history path at all. The coordinator holds the
turns and has to write a query that stands alone. Measured, list exchange
("add oat milk…" / "I added oat milk…") then "remove that from the
shopping list" to the coordinator: 4 of 6 `ask_home` with "oat milk" in
the query, 2 of 6 the text "I removed oat milk from the shopping list."
with no tool call. That fault is on the path that already has real turns,
it is not M36, and it is the risk point 4 is about. It needs a ruling of
its own.

After the change nothing passes a non-empty `context`. The build should
remove the `[Recent conversation context]` branch so one form exists.

## 2. The measurement, repeated and extended

Home agent on `gemma-4-e4b`, about 4,230 prompt tokens.

| History | Request | Form | Result |
| :-- | :-- | :-- | :-- |
| Pulse exchange | play that album, in the kitchen | turns | 6 of 6 `play_music` Pulse |
| Abbey Road success, then Pulse exchange | play that album, in the kitchen | turns | 12 of 12 `play_music` Pulse |
| the same two exchanges | play that album, in the kitchen | text | 0 of 12 Pulse (10 asked which album, 1 Abbey Road, 1 `escalate`) |
| Pulse exchange | play Abbey Road in the kitchen | turns | 6 of 6 Abbey Road |
| Pulse exchange | what is the weather | turns | 0 of 6 `play_music` (2 `get_weather`, 4 answered from the weather block) |
| Abbey Road success, then Pulse exchange | play some jazz in the office | turns | 6 of 6 jazz, office |
| Pulse exchange | play pulse by pink floyd in the kitchen | turns / text | 6 of 6 / 6 of 6 `play_music`; no copy of the failure reply |
| Abbey Road success | play that again, in the office | turns | 9 of 12 Abbey Road; 1 `control_music` resume, 1 `play_music` kind another, 1 offer in text |
| list exchange | remove that from the shopping list | turns / text | 6 of 6 / 2 of 6 `shopping_list_remove` oat milk |
| Boston weather exchange | what's the weather there tonight | turns / text | 6 of 6 `get_weather` Boston / 0 of 6 (all six answered for Laurel from the weather block) |

Research agent (775 prompt tokens), "who wrote the novel Dune" answered,
then "look up when he was born": 6 of 6 `web_search` naming Frank Herbert
in both forms. The text form fails on home's long prompt, not on a short
one. Research is a no-regression check, not a second instance.

A request that names its own subject was not pulled to the turn before
in any of 18 tries. The weather row is a second real instance of the bug
today: a wrong answer, stated with confidence.

## 3. The window: start on a user turn, four messages

`history[-5:]` of an alternating list starts on an assistant turn. Tried
as turns: [Pulse failure reply, Abbey Road request, Abbey Road success,
Pulse request, Pulse failure reply], then "play that album, in the
kitchen", 18 tries: 12 Pulse, 2 Abbey Road (the wrong album), 2 a copy of
the failure reply, 1 "I've started playing the album Pulse by Pink Floyd
in the kitchen." with no tool call, 1 tool call written out as text. The
aligned four-message window: 12 of 12. The two differ by the leading
assistant message, which here was also a second failure reply, so the
count does not separate "starts on an assistant turn" from "two failure
replies". Either way the rule is the same.

Rules for the build, in one function used at `main.py:470`:

- Take whole exchanges from the end: user then assistant, two exchanges
  at most. Stop at the first break in alternation. This covers a shim
  array with two user messages in a row, a dropped empty assistant
  message, and the case in `_turn_said` where the request is not the last
  message and so stays in the history as a duplicate.
- Cut each assistant turn at `ASSISTANT_PROMPT_CHARS` there. The shim
  path has no cut today; only `sessions.window` does.
- Keep failure replies. The incident's follow-up resolves from the
  failure reply ("The closest I found was Pulse by Pink Floyd"), and a
  retry after it was not steered (6 of 6). No rule to drop error turns.
- Do not rebuild tool calls or tool results into the history. For
  terminal tools the stored reply is the result's sentence, which was
  enough in every music row. For other tools only the prose survives;
  that was enough for the list and weather rows.

## 4. What real turns change besides the reference

- **Token count and the voice deadline.** The turns form is 4 to 6 tokens
  smaller than the text form (4,230 against 4,236; 4,199 against 4,203;
  775 against 779). No cost to the deadline.
- **Prompt cache.** Plumbing 3 says the system text no longer changes per
  turn. It still does: `kronk_facts()` carries the clock to the minute,
  at about token 893 of 4,230, and home's weather block follows it.
  Measured: the same prompt twice, cached 4,229 of 4,230; the clock one
  minute on, cached 893. What M36 does change: today the context block
  sits before the tool definitions in the rendered prompt, so a new turn
  re-evaluates everything after it (cached 1,976 of 4,203, about 2,200
  tokens evaluated). As turns, a new request after the same system text
  cached 4,160 to 4,218 of about 4,230. So a follow-up inside the same
  minute gets cheaper by about 2,200 tokens; across a minute both forms
  fall to 893. Criterion 6 should report cached tokens for a follow-up in
  the same minute and for one a minute later, or the number will mislead.
  The clock's place in the prompt is not M36.
- **An earlier reply imitated.** With the aligned window on home: 0
  replies claiming an action without a tool call in 60 tries. With the
  assistant-first window: 1 of 18 (above). On the coordinator, which has
  real turns today: 2 of 6 (point 1). A false "now playing" is the
  failure tenet 5 was written for. No new guard in M36; count it in
  criteria 2 to 4 and stop if it is not zero.
- **Terminal tools.** Unchanged. The turn still ends on the tool's
  sentence, and that sentence is what the next turn reads.
- **Escalation.** Offered in every run. 0 escalations in 96 turns-form
  tries on home; 1 of 12 in the text form. On escalation the coordinator
  gets the full history as turns already. No change needed.
- **Truncation in `sessions.window`.** Applies on the chat page only; see
  the rule in point 3.

## 5. Voice

Home Assistant does resend the conversation. Its Ollama integration
keeps up to `max_history` messages (default 20;
`components/ollama/const.py`), the satellite holds the conversation id
(`assist_satellite/entity.py`), and the session expires five minutes
after its last use (`helpers/chat_session.py`, `CONVERSATION_TIMEOUT`).
Read from the source in the `homeassistant` container; the configured
`max_history` was not read.

The voice record agrees (counts only): 116 runs, 77 conversations, 24
with two or more runs. Of the 37 follow-up runs: 13 answered by HA, 6 by
the blueprint, 6 blueprint then Kronk, 7 Kronk default rule, 3 messy, 1
playback, 1 rate. None reached a specialist's model with history:
default and messy are the coordinator, playback and rate run no model.

A spoken "play that album" does not get the fix. Anything starting with
"play" is taken by the music blueprint's catch-all, which calls
`conversation.process` with `agent_id` and `text` only
(`ha/blueprints/mass_assist_kronk.yaml:456` and `:566`). No conversation
id, so Kronk gets a new conversation with one message. Read from the
blueprint, not run live. Passing the conversation through is a blueprint
change and an HA reload: out of scope.

What voice does gain: a follow-up HA passes straight to Kronk that pins
to a specialist, inside five minutes. In practice that is weather
("what's the weather there tonight"). The handoff line should say "on
the chat page, and through the shims when the client resends the
conversation; a spoken 'play that' is not covered". Criterion 2's
`/dev/voice/api/chat` run is still a valid check of the speech mount;
label it that, not "by voice".

## 6. The criteria

- **1** is checkable. Add the window rules of point 3 as unit cases: an
  odd-length history, two user messages in a row, the request duplicated
  in the array, a long assistant reply.
- **2**: the chat route cannot replay the incident's exchange. The
  failure reply came from the resolver, dev may resolve the album (it did
  in the coordinator's reproduction), and the session store is not to be
  seeded by hand. Run the exact exchange through `/dev/api/chat` and
  `/dev/voice/api/chat` with the conversation resent, and on the chat
  route run the natural two turns ("play the album Abbey Road…", then
  "play that again"), accepting whichever reply the first turn gives.
  Expect below ten of ten on "play that again": 9 of 12 here.
- **3**: "stop the music" is the playback rule, a direct call with no
  model. It cannot change and proves nothing; keep it only as a smoke
  check. The Abbey Road and weather rows are the real ones.
- **4**: use home `get_weather` (Boston exchange, "what's the weather
  there tonight": 0 of 6 today, 6 of 6 as turns) and research
  `web_search` ("look up when he was born": 6 of 6 both forms, so a
  no-regression check; it does run a web search on dev). "Remove that"
  and anything about lights do not reach a pinned specialist.
- **5**: see point 7.
- **6**: read the cached-token count with point 4 in hand.
- Add: zero replies claiming an action without a tool call, counted over
  the runs of 2 to 4.

## 7. The cheapest multi-turn check

The suite with a fake model can pin only the message shape (criterion
1). The check that would have caught this is live: a message array with
the earlier turns, posted to the dev shim, music in dry-run, N times,
reading which tool was called and with what.

Put the rows in `tests/voice_corpus.jsonl` with an optional `history`
field (a list of role and content), one source of truth. Layer one
ignores `history`; routing does not read it. `scripts/voice_replay.py`
keeps skipping these rows on the HA path, which cannot be handed a
history, and gains a mode that posts them to `/dev/api/chat` and
`/dev/voice/api/chat` after its dry-run check. Three rows to start: the
incident's follow-up, the weather follow-up, and the existing
`inc-1017-try-playing-it-ytm`, which is tagged `needs-context` and
skipped today.
