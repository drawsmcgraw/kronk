# Investigation 2026-09-10 — "stop" didn't stop the music

## Symptom (operator)

Said "stop" to the basement Voice PE while music played. Kronk asked
"Stop what?", a short back-and-forth followed, Kronk gave up, the music
kept playing.

## What the pipeline recorded (HA pipeline debug, kronk pipeline)

| time (UTC) | heard | Kronk said | mic left open |
|---|---|---|---|
| 12:40:02 | " Stop!" | "Stop what?" | yes |
| 12:40:09 | " up." | "Up what?" | yes |
| 12:40:23 | " but" | "I am Kronk, a home assistant. How can I help you?" | yes |
| 12:40:32 | " Cancel." | "I cannot cancel a command because you have not given me a command…" | no |

Earlier the same morning: " Resume the music." → Kronk: "I cannot resume
the music. I can only start new music." And in a test run with the
device's context, bare "pause" → Kronk: **"I have paused. Let me know
how I can help."** — while the player kept playing.

## Findings

1. **"stop" is not a media sentence HA knows.** Built-in
   `HassMediaPause` accepts "pause", "pause [the music] in <area>",
   "pause <name>"; `HassMediaUnpause` accepts "resume"/"unpause" in the
   same shapes. "Stop", "stop the music", "resume the music" (no area)
   match nothing → they fall through to Kronk.
2. **Kronk has no playback-control tool.** The coordinator can start
   music (`play_music`) but not pause/stop/skip/volume, so it does what
   an LLM does with an unfulfillable imperative: asks a question, or —
   worse — claims it did it ("I have paused"). Tenet 6 violation.
3. **Kronk's clarifying questions keep the mic open.** HA's Ollama
   integration sets `continue_conversation` when the reply ends with a
   question; the PE listens again and catches fragments (" up.", " but"),
   each of which becomes another request. That is the "back and forth".
4. **HA's built-in "pause" with only the satellite's area as context
   did not match** in a pipeline run carrying the basement device's id
   (default agent: "Recognize done in 0.00 seconds", no intent), even
   though the sentence carries `requires_context: area`, the device has
   an area, and both basement media players are exposed. Naming the
   area works instantly through the local agent ("pause the basement
   theater" → Paused; "resume the basement theater" → Resumed). Cause
   not pinned down (time-boxed); the practical route doesn't depend on
   it — see below.
5. Not a factor: the players are exposed to Assist (entity registry
   `should_expose: true`); the ESPHome device carries the area; the MA
   player's device has **no area** (`basement voice pe ma`, area null) —
   worth setting regardless, it is what the built-in intents would key
   on for that player.

## Improvement (plan, ROADMAP item 18)

- **Fast tier (HA, ~1 s):** sentence triggers for playback control in
  the Kronk blueprint fork (or a sibling "media control" blueprint) —
  "stop", "pause", "resume", "next"/"skip", "louder"/"quieter", "shut
  up"/"kill the music" — calling `media_player.media_pause/play/next
  /volume_set` on the player resolved device-first (the same MAC join
  the play path uses). Sentence triggers match before built-in intents
  and before the LLM, and get `trigger.device_id`, so this sidesteps
  finding 4 entirely.
- **Kronk tier:** a `control_music(action)` terminal tool on the home
  agent (origin-aware, tool_service `/music/control` via the websocket
  helper so HA's error text is spoken), so loose phrasings work and the
  model never has to invent "I have paused". Terminal → no question →
  the mic closes.
- **Guard:** the coordinator prompt states it cannot control playback
  except through tools; a test pins that a bare "stop" with no tool
  available yields an honest sentence, not a claim.
- Set an area on the MA player devices (basement, kitchen) in HA so the
  built-in intents have a target if finding 4 ever resolves itself.
