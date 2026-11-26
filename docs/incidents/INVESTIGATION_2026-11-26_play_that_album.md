# Investigation 2026-11-26: "play that album" and the chat history

Status: cause found and reproduced; fix ordered as work item M36.

## Symptom, as Drew saw it

Chat page, production (`item-M34`), 2026-11-26 about 07:09 EST. Drew:
"kronk isn't honoring the chat history. Check the section where I just
asked for 'pulse' by pink floyd. Kronk said it found it. I said 'play
that' and kronk didn't know what I was asking for."

| Time (EST) | Who | Text |
| :-- | :-- | :-- |
| 07:08:59 | Drew | play the album 'pulse' by pink floyd in the kitchen |
| 07:09:05 | Kronk | I couldn't play that. I couldn't find an album called Pink Floyd - Pulse in Music Assistant. The closest I found was Pulse by Pink Floyd. |
| 07:09:20 | Drew | play that album, in the kitchen |
| 07:09:25 | Kronk | I need to know the name of the album you are referring to before I can play it in the kitchen. |

Traces: `846c301ccd62de6179a6900239748e30` (first), 
`be51655aad7b6958f05a169e6544ec52` (second). Both routed by the music
shortcut to the home agent on `gemma-4-e4b`.

## Two faults, one after the other

### 1. The resolver refused the album it had found

The home agent called `play_music` with query "Pink Floyd - Pulse",
`media_type` album. tool_service: `music resolve 'Pink Floyd - Pulse'
[album] stage 1, 2 candidates -> album 'Pulse' by 'Pink Floyd'
library://album/1153 (0.69) BELOW THRESHOLD`, HTTP 502. The same album
asked as "Pulse by Pink Floyd" scores 1.04 and plays (seen a minute
later, when Drew retyped it). The model chose the "Artist - Album" form;
the scorer reads it as an album title. The decision path running in
shadow (`music_decide`) read the request rightly and would have played:
`music shadow {"agree": false, "why": "only the new path plays"}`.

### 2. The follow-up could not see what "that" was

Hypothesis tried first, and wrong: the specialist is not given the
history. It is. `_run_pipeline` hands a pinned specialist the last five
messages (`orchestrator/main.py:470`, `list(history[-5:])`), and
`run_stream` appends them to the system text under "[Recent
conversation context]" as `USER: …` / `ASSISTANT: …` lines
(`orchestrator/agents.py`, the `context` argument). The coordinator path
gets its history another way, as real chat turns (`history_messages`).

Reproduced on dev (music in dry-run, nothing played):

- Chat page route, both turns, three times: the first turn resolved (the
  model wrote "pulse by pink floyd" this time); the follow-up called
  `play_music` twice and once answered "What album would you like me to
  play?", with a successful reply right above it in the history.
- The exact failed exchange replayed through `/dev/api/chat`, five
  times: five refusals ("I need the name of the album…").

Then on the model directly, in the dev orchestrator container, the home
agent's own system prompt and tools, six tries each, no tool executed:

| How the two earlier turns are given | Calls `play_music` with "Pulse by Pink Floyd" |
| :-- | :-- |
| In the system text, as today | 0 of 6 |
| As real chat turns before the request | 6 of 6 |
| Not at all | 0 of 6 (one call with query "that album") |

So the embedded block does nothing measurable for a reference like
"that": the 4B model answers as if it had no history. Given as turns, it
resolves the reference every time.

## Root cause

A pinned specialist's conversation history is written into the end of a
4,300-token system prompt as text, and `gemma-4-e4b` does not use it to
resolve a reference to the turn before. The comment in `run_stream`
says why it was built so (the specialist style; the coordinator path
got real turns for prompt-cache reuse). Tenet 5 applies: the loop lets
the model miss it, so change the loop.

## Fix

Work item M36: a pinned specialist gets the recent turns as chat
messages. Fault 1 is recorded separately (the resolver's reading of
"Artist - Album"; the shadow path already reads it).

## What would have caught it sooner

No test and no corpus row asks a follow-up that depends on the turn
before. The voice corpus and the live battery are single-turn. Rows for
this exchange go in with the fix.
