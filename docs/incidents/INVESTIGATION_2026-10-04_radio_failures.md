# Investigation 2026-10-04 — "Play X radio" failing after the Pandora account switch

Status: root-caused; three separate causes. Cause 2 (HA tier "Done") fixed
2026-10-04 as blueprint KRONK change 6; causes 1 and 3 open.

## Symptom

Kitchen, ~08:01–08:05 local: "French Dinner radio", "Orbital radio" and
"Massive Attack radio" all failed or did nothing. MA's Pandora provider had
been moved to the Kronk account on 2026-10-02.

## Evidence

Pipeline runs (HA debug, UTC):

| Time | STT | Handled by | Spoken |
|---|---|---|---|
| 12:01:51 | "Play French Dinner Radio on Pandora." | HA (blueprint) | "Done" |
| 12:02:10 | "Play French Dinner Radio." | HA (blueprint) | "Done" |
| 12:02:28 | "Play French Cafe Radio on Pandora." | HA (blueprint) | "Done" |
| 12:02:39 | "We'll play Orbital Radio." | Kronk | "I couldn't play that. Playback failed: Could not resolve Orbital Radio…" |
| 12:05:27 | "Like massive attack radio" | Kronk | "I couldn't play that. Playback failed: Could not resolve massive attack radio…" |

- HA log: `automation.kronk_music_assistant_voice_device_first … Error
  executing script … Could not resolve ['French …']` for each "Done".
- tool_service: one `music request query='Orbital Radio'` + MA's
  could-not-resolve. **No request at all for Massive Attack.**
- Orchestrator events, rid e8162cc3 ("Like massive attack radio"): request
  → route direct → `request_complete` 1.58 s, **no tool_call**.
- Kronk's Pandora account, read live: Anime Pop, Santana Radio, Justin
  Johnson Radio, Parov Stelar Radio, German Soundscape, Massive Attack
  Radio. MA resolves "Massive Attack" / "Massive Attack Radio" to Kronk's
  station 209818706564363049 (in library). French Dinner and Orbital
  resolve to nothing.

## Root causes

1. **Stations not on the Kronk account** (French Dinner, French Cafe,
   Orbital). Pandora sharing sends a link; the station exists on the
   recipient only after someone signed in as that account adds it. MA
   correctly cannot resolve them.
2. **HA tier reports "Done" on failure.** The blueprint fork sets its
   conversation response in a step after `music_assistant.play_media`;
   when the call raises, the script aborts and HA speaks its default
   "Done". A false success on the ~1 s path (tenet 6), present since the
   fork shipped.
3. **Fabricated failure from the coordinator.** "Like massive attack
   radio" (STT turned "play" into "like", so the blueprint did not match)
   reached Kronk; the coordinator answered directly without calling any
   tool, copying the shape of the previous turn's real tool failure from
   the voice session history ("I couldn't play that. Playback failed:
   Could not resolve … to playable media item"). The station would have
   played. Tenet 5: a model can emit text that looks like a tool result;
   the loop must not let it.

## Fix options (not applied)

1. Operator: add French Dinner / French Cafe / Orbital on Kronk's
   Pandora account; then an on-demand Pandora sync.
2. Blueprint: `continue_on_error: true` on play_media, then a response that
   checks the outcome (e.g. a `wait_template` on the player starting, else
   "I couldn't find {media_name}"). Needs a test via the sentence debugger.
3. Kronk, structural: (a) a routing pin for "(play|put on|listen to) … radio"
   → home agent, so music requests never ride the coordinator; (b) a
   guard: a coordinator reply that matches tool-result shapes ("Playback
   failed", "Now playing", "Could not resolve") with no tool call in the
   turn is not delivered as-is — retry with the tool, or answer
   "I didn't get that". (b) covers more than music.

## Fix applied — cause 2 (2026-10-04)

Blueprint KRONK change 6 (`ha/blueprints/mass_assist_kronk.yaml`, version
`20250404-kronk6`): snapshot the first target's state / content id / title
/ position, `music_assistant.play_media` with `continue_on_error: true`,
then `wait_template` (10 s, continue on timeout) for the player to be
`playing` something new — different content or title, or the same item
restarted (position went backwards), or it was not playing before. Started
→ shuffle_set + the usual reply; timed out → "I couldn't play <name>
[radio]." Deployed by copy + `automation.reload` (no HA restart).

Verified via `conversation/process` (agent `conversation.home_assistant`,
kitchen device id), nothing played: "Play zzqx florbent radio" →
`action_done`, "I couldn't play zzqx florbent radio." in 10.1 s; kitchen
player unchanged. Success path: operator voice test pending.

Cost: a failure now takes the full 10 s before it is spoken (HA scripts
expose no error from a `continue_on_error` call to branch on). Successful
starts observed at 1–4 s, so the wait ends at the first matching state.

## 2026-10-05 recurrence — "Orbital radio", three tries from the kitchen

| Time (UTC) | STT | Handled by | Result |
|---|---|---|---|
| 11:11:24 | "Play orbital radio." | HA blueprint | MA: `Could not resolve ['orbital']`; spoken "I couldn't play orbital radio." (change 6 working as designed) |
| 11:11:46 | "Start an orbital radio station on YouTube Music." | Kronk coordinator, **no tool call**, 4.0 s | "I am unable to play specific streams from YouTube Music. I can only control the playback…" — invented |
| 11:12:14 | "Tell Kronk to start an orbital radio to play on YouTube music." | Kronk coordinator, **no tool call**, 1.5 s | "I cannot directly start a YouTube Music stream…" — invented |

Kronk's Pandora account still has six stations, none named Orbital (the
cause-1 loose end: Orbital / French Dinner / French Cafe were never added
on the bot account). Under the operator's old account the station existed,
which is why this reads as a regression: the account move removed
stations the household asks for by name.

Causes 1 and 3 again. Cause 3 now shows as a confident *refusal* instead of
a copied failure: the coordinator handles "start … radio station" itself
and never calls `ask_home`/`play_music`. Same fix as proposed: a
deterministic routing pin for music requests (play/start/put on/listen to
… radio/station/music/album/song/playlist) → home agent, and a guard that
rejects a coordinator reply shaped like a tool outcome when no tool ran.

## 2026-10-07 — "Play French Dinner Radio on Pandora" → "Done" again (cause 4)

06:49 local, kitchen. The station is on Kronk's account now and **did start**
(MA: `Start Queue Flow stream for Queue kitchen voice pe ma` 06:49:38.448)
— then the blueprint's `media_player.shuffle_set` step failed: MA 2.11
refuses shuffle on a dynamic radio queue (`player_queues/shuffle: Cannot
change shuffle while the queue is in dynamic mode`; HA: `Invalid or
unsupported command`). The script aborted before `set_conversation_response`
and Assist spoke its default "Done" — music playing, reply wrong. A
regression from the MA 2.11 move (2.8.8 accepted shuffle_set on a station),
and change 6's verify did its job (the play was confirmed) only for the
next step to abort.

Fix: KRONK change 7 — `continue_on_error: true` on the shuffle step
(shuffle is best-effort; a station cannot shuffle anyway). Deployed by copy
+ `automation.reload`; verified: the automation trace now finishes with the
real response ("Anime Pop playing in the Kitchen") while HA still logs the
shuffle error. tool_service's `/music` already wraps its shuffle_set in a
try/except, so the Kronk tier was not affected.

Trace evidence: run 10:49:37Z `script_execution=error`, steps end at
`action/4/then/0`, no response; run 10:51:21Z (after the fix)
`script_execution=finished`, `action/4/then/1` reached, response set.
