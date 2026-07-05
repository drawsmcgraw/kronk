# Feature: Voice music control

**Shipped:** 2026-07-03 · **Plan:** `../plans/MUSIC_ASSISTANT_PLAN.md` · **Journal entries:** `../VOICE_SETUP.md` timeline (2026-07-03)

## What it does

"Play Pink Floyd on the Sonos Move" — spoken or typed — plays music through
Music Assistant on any mapped player. Two tiers, by design (Option C):

| Tier | Path | Latency | Grammar |
|---|---|---|---|
| 1 | MA local-assist blueprint automation in HA | ~2 s | strict: needs a media-type keyword ("play the **artist** X [on Y]") |
| 2 | Kronk `home` agent → `play_music` tool | ~15–25 s | fuzzy, anything |

Utterances the blueprint's grammar can't parse fall through HA's Assist
pipeline to Kronk automatically — the user just waits longer.

## How it works (tier 2)

`orchestrator/tools.py:play_music` → `tool_service POST /music` → HA REST
`music_assistant.play_media` → MA resolves the free-text query against its
providers → audio on the player. The route:

1. Resolves the spoken player name against the `MUSIC_PLAYERS` env map
   (partial matching), else `MUSIC_DEFAULT_PLAYER` (kitchen Voice PE).
2. Pre-checks the entity exists and is available (503 "may be powered off").
3. Calls `play_media`, then **polls the entity for `playing`** before
   reporting success — MA queues async, so HA's 200 alone proves nothing.
   Expired provider auth (e.g. YouTube Music) surfaces here as a clean
   failure instead of a silent no-play.

## The terminal-tool mechanism (the interesting part)

`play_music` is a **terminal tool** (`AgentConfig.terminal_tools` in
`orchestrator/agents.py`): its result is converted to speech verbatim
(`_terminal_speech`) and the agent turn ends immediately. This is a
*structural* guardrail, added after prompt engineering failed three ways:
gemma-4-e4b narrated fake tool scaffolding aloud, hallucinated "Jazz is now
playing" after a 503, and retried the tool to budget exhaustion. With the
mechanism, the model never gets a chance to editorialize about the result.

## Gotchas

- Player env map entries must be the **MA entities** (`_2`-suffixed, platform
  `music_assistant`) — native Sonos/Cast entities can't be driven by MA.
- The blueprint matches **exact MA player names**: "sonos move" ≠ "Sonos Move
  Derp" → silently plays on the default player. Fix: rename the player in
  MA's UI (entity_id survives renames).
- Blueprint grammar traps: "play the album X **by Y**" stuffs Y into the
  media name; it needs "by the **artist** Y".
- MA 2.8.8's YT Music provider can 500 transiently (`ytmusicapi has no
  attribute YTMusicError` — upstream bug). tool_service logs the full body,
  speaks one clean sentence.

## Blog hooks

- Terminal tools: when prompt engineering loses to a 4B model, change the
  loop, not the prompt.
- "HTTP 200 means nothing": verifying async playback actually started.
- Two-tier voice design: strict-grammar fast path + LLM fuzzy fallback.
