# Investigation 2026-10-19: voice thumbs down landed on a different song

Status: closed 2026-10-19. No defect: the operator confirmed Suzume was
audible when "thumbs down" was said; Kronk rated the right song.

## Symptom

During the PR #7068 live test (MA `kronk.4`, fork 7822176fd), the operator
said "thumbs down" on the office Satellite1 while, by their account, a
Creepy Nuts song was playing. The Pandora UI showed no thumbs down for it.

## Evidence

Assist pipeline runs (HA `assist_pipeline/pipeline_debug`, kronk pipeline,
office Satellite1), HA state history for `media_player.satellite1_d8d1bc`,
tool_service and MA logs. Times UTC.

| Time | Event |
|---|---|
| 16:50:21–16:50:31 | A play request is answered by HA's MA blueprint fast path (no Kronk request). It plays the YouTube Music track "Pandora" by Youko (CV: Yui Horie), `ytmusic--kGmHoQDn://track/WR1SiulPL6E`. The transcript fell outside HA's 10-run debug window. |
| 16:50:56 | "Thumbs up." → `/music/rate` 409: not a Pandora track (correct). |
| 16:52:03 | "Skip." → SPECIALZ (King Gnu), from Anime Pop, at 16:52:06. |
| 16:52:17 | "thumbs up" → "Thumbs up for SPECIALZ by King Gnu." Landed on Pandora 16:52:25.783 (`feedbackId` 139165188875386825). |
| 16:52:30 | "Skip." → Suzume (Radwimps), at 16:52:33.156. |
| 16:52:41 | "Thumbs down." tool_service reads the player state at 16:52:46.789, while it still reports Suzume, and rates it. Skip → Itano Ueno Mamono (Creepy Nuts) at 16:52:46.803. Pandora: Suzume (`TR:79642953`) thumbed down at 16:52:47.074 (`feedbackId` 126119483411793855). |
| 16:52:55 | "Stop!" → paused while Itano Ueno Mamono plays. |

Itano Ueno Mamono (`TR:32877658`) still carries the operator's thumbs up
from 2026-10-02. No Pandora or provider errors in MA's log.

## Reading so far

Kronk rated the track MA reported as current: Suzume, about 8 s after the
skip that started it. The Creepy Nuts track began in the same instant as
the thumbs-down skip, and it was what played until "Stop!". Open
question: did the speaker's audio match MA's state (Suzume audible at
16:52:41), or does audio lag or lead the reported track on this player?

Contributing factor: the thumbs-down reply is only "Thumbs down." (short
by operator choice on 2026-10-06, so it doesn't talk over the next song).
It doesn't name the rated song, so a mismatch is invisible to the
listener.

Separate finding: the opening play request went to the HA fast path and
played a YouTube track titled "Pandora" rather than a Pandora station.

## Resolution

The operator confirmed it was Suzume. The system behaved correctly. The
confusion came from the next song starting the instant the skip fired,
with nothing in the reply to say which song was rated.

## Follow-ups (not filed)

- Optional: have the thumbs-down reply name the song ("Thumbs down on
  Suzume."), about 1 s of speech over the next track. That's the
  operator's call; they chose the short reply on 2026-10-06.
- The fast-path play request that picked a YouTube track titled "Pandora"
  is unexplained, because the transcript is gone. Reproduce it before
  filing.
