# Investigation 2026-09-14 — music stayed quiet after a timer request

Status: unresolved (cleared on its own). Evidence path opened as ROADMAP item 20.

## Symptom

Music playing on the kitchen Voice PE (0ac919, firmware 26.6.0).
Operator asked for a 19-minute timer at 17:32 local. The PE lowered the
music for the wake word, confirmed the timer, and the music stayed at
the lowered level. Roughly 15 minutes later it was back to normal;
nothing was done in between except that the timer ran (it had not yet
rung when the volume returned, by the operator's account).

## Evidence

- HA: ESPHome media player volume 1.0 throughout, music `playing`,
  assist satellite `idle` from 17:32:06. Nothing on the HA side changed
  the volume. The reduction is the firmware's 20 dB mixer duck, which
  HA cannot see.
- Firmware 26.6.0 YAML (read from the release tag; identical on `dev`):
  duck 20 dB on `voice_assistant.on_start` and on
  `media_player.on_announcement`; restore to 0 dB (1 s ramp) in
  `voice_assistant.on_end` after `wait_until: not is_running`
  (unconditional) and in `media_player.on_state` guarded by
  `timer_ringing` off, assistant not running, not announcing. A running
  timer touches nothing; only a ringing one ducks.
- No upstream issue matches; no fix on `dev`.
- No device logs: no ESPHome dashboard/CLI on this box.

## Hypothesis

The TTS announcement's duck landed after `on_end`'s restore, and the
`on_state` restore at announcement end was skipped because the
assistant still counted as running. Unproven. The later self-recovery
fits a subsequent `on_state` (a track change) firing with the guards
true.

## Next

ROADMAP item 20 step 1 (ESPHome CLI logs) before chasing it further.
If it recurs: stream the device log, reproduce with a timer request
while music plays, and file upstream with the log.
