# Music requests never reach the coordinator — Plan

Status: **shipped 2026-10-05.** `_MUSIC_RE` in `orchestrator/routing.py`,
26 cases in `tests/test_music_pin.py`, suite 590. Live via the shim: "Start a
zzqx florbent radio station on YouTube Music." → `route_shortcut rule=music`
→ home → `play_music` → MA's own "Could not resolve …" spoken, nothing
played, 12.7 s (8 s of it the failed-call verify window). Final shape
differs from the draft below: music-only verbs need no noun, "play"/"start"
need a music noun or source, bare "play <object>" pins unless the object is
a game/video/movie/podcast; "news" excluded everywhere.

## Problem

The coordinator answers music requests itself when the blueprint misses
them. Receipts: 2026-10-04 "Like massive attack radio" → copied the
previous turn's failure text, no tool call; 2026-10-05 "Start an orbital
radio station on YouTube Music" → "I am unable to play specific streams
from YouTube Music", no tool call (`INVESTIGATION_2026-10-04_radio_failures.md`).
The coordinator's prompt already has a PLAYBACK rule and a "never invent"
rule; neither held. Tenet 5: on a 4B model, change the loop, not the prompt.

## Fix: one routing pin

`orchestrator/routing.py` already routes deterministically before the
coordinator sees anything (weather, playback commands, now-playing, magic
mirror, URLs, search phrases). Add `_MUSIC_RE`: a start-anchored music
request → `("home", "music")`, placed after `_NOW_PLAYING_RE`.

Shape (final regex lands with the tests):
- optional prefixes: `please`, `kronk,`, `tell kronk to`
- verb: `play | put on | start | listen to | shuffle | queue up`
- then a music noun somewhere in the rest: `radio | station | music |
  song | track | album | playlist | artist | mix`, or a source: `on
  (pandora | youtube [music] | the nas | atlas)`
- exclusions: `news` anywhere (news_brief is the coordinator's tool),
  `game`, `video`.

The home agent then calls `play_music` (terminal: its result is spoken
verbatim, success or MA's own failure text). A missing station becomes an
honest "Playback failed: Could not resolve Orbital Radio…" instead of an
invented refusal; item 23 later turns that into YouTube radio. Composite
requests still work: a pinned home run may `escalate` back to the
coordinator (existing mechanism) when the request has non-music parts.

Latency improves: pinned home run ≈ 3–5 s to music versus 8–10 s via
coordinator → ask_home → play_music.

## What this does and doesn't add (the tech-debt question)

- It adds one regex to the one file where every pin lives, with the same
  comment-plus-receipt convention as the other six, and a test table.
  That is the house pattern, not a new one.
- It does **not** add a model-output filter. A "reject replies shaped
  like tool results" guard was considered: it is a heuristic over free
  text, it could suppress legitimate answers, and after the pin the
  coordinator should not be seeing music requests at all. Deferred until
  measured: after a week, count coordinator replies matching
  `^(I couldn't play|Now playing|Playback failed)` with no tool call in
  the turn (Langfuse). Zero → nothing to build.
- The standing debt, named: three places encode "what a music request
  looks like" — the blueprint's sentences, this pin, and the home agent's
  prompt. They are tiers, not copies (HA sentence-exact → Kronk regex →
  LLM for loose phrasing), but a new phrasing may need two edits. Accepted
  for now; the fix would be generating the pin from the blueprint's
  sentence list, which is not worth it at six pins.

## Implementation

1. `routing.py`: `_MUSIC_RE` + the branch in `_classify_inner`, comment
   with the two receipts.
2. `tests/test_routing.py` (or the existing routing test file): positives
   — "play orbital radio", "Start an orbital radio station on YouTube
   Music.", "Tell Kronk to start an orbital radio to play on YouTube
   music.", "like massive attack radio" (STT-mangled verb: **not** matched —
   documented limit; it still reaches the coordinator), "put on some jazz",
   "shuffle my youtube playlist X", "play the french lounge playlist on
   atlas"; negatives — "play the news", "what's playing", "play a game
   with me", "what is the weather", "stop", existing pins unchanged.
3. Suite; deploy orchestrator (`up -d --build`), restart nginx, shim check
   through `/api/chat` with a gibberish radio request: honest failure, no
   playback (no live speaker touched without the operator).
4. Operator: "Okay Nabu, start an orbital radio station on YouTube Music"
   — expect the honest MA failure until item 23, then the real thing.
5. ROADMAP chore for the measurement in (deferred guard).

Out of scope: item 23 (fallback), the Pandora station additions, the
upstream PRs.
