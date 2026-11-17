# M14 build: the messy-transcript gate (step 1)

Build agent, 2026-11-17 (UTC), branch `refactor`. Criteria:
`docs/sprint/M14-criteria.md`. Plan: `Kronk evaluation findings and
implementation plan.md`, section M14, step 1 only (decided by Drew
2026-11-11). Steps 2 and 3 (the kitchen satellite's finished-speaking
trial) wait for M12 data; no satellite setting was changed. Tree before the
build: `8bfcde2`. No push, no tag. This report is the last commit.

## Summary

- One gate, `transcript_gate.check()`: more than `max_sentences` (1)
  sentences or more than `max_words` (20) words is messy. Limits in
  `config/tuning.yaml` (`transcript_gate`), null switches a half off.
  Nothing is trimmed.
- New routing rule `messy` (route `direct`, the coordinator), 6th of 17,
  after `lead_in`, `talkie_explicit`, `direct_override`, `music_url`, `url`.
  The transports' clear-history intercept consults the same gate. Every
  mount behaves the same.
- The counter keeps abbreviations, initials, `No.` before a number,
  decimals, times, ellipses and a single trailing mark inside one sentence,
  and does not break before a lowercase word: the music blueprint hands
  Kronk "Play Hotel Costas Radio. in the Kitchen" (10 such requests in
  production since 2026-09-14), which a plain counter would call messy.
- Routing pin: 83 of 7696 rows changed to `direct/messy` (14 changed
  route: 9 `search_phrase`, 3 `music`, 1 `mm_ops`, 1 `weather`; 69 were
  `direct/default`), 184 appended, 7613 unchanged. Every changed row has
  more than one sentence or more than 20 words.
- Motivating case on dev, 10 of 10: `messy` → coordinator → `ask_home` →
  `play_music`, "Dry run: would play the Pandora station Anime Pop Radio".
  Before: 10 of 10 through the `music` rule. Median 2.65 s → 5.77 s.
  Clean commands unchanged (gate 2–7 µs).
- Tests `3725 passed, 2 skipped, 23 xfailed` (baseline `3503 passed, 2
  skipped, 23 xfailed`). Replay `{'pass': 123, 'xfail': 23, 'skip': 3,
  'fail': 2}`, exit 1: both fails are one-sentence rows the gate does not
  touch (a known "YouTube Music" flapper, and an M19 lead-in row where the
  coordinator asked research first); both passed when rerun once. Every row
  the gate moves passed or kept its xfail.

## Commits

| Commit | What |
| --- | --- |
| `bc7e6ca` | Counter, config schema and limits, the `messy` rule, the clear-history guard, the routing pin update, corpus rows, `tests/test_transcript_gate.py`. |
| `3665472` | `xfail.live` on `m14-thumbs-up-missed-best-part` (measured on dev after the first commit); comment fixes (count of blueprint hand-offs: 10, not 13). |
| `f99b692` | Docs. |
| this one | This report and the replay result. |

## Data study (criterion 4)

Sources searched:

| Source | What it held | Used for |
| --- | --- | --- |
| `tests/voice_corpus.jsonl` at `8bfcde2` | 135 rows (4 recorded STT transcripts) | sentences, words |
| `docs/bench/routing_eval.jsonl` | 491 distinct typed requests | sentences, words |
| Production orchestrator log (journald, `CONTAINER_NAME=kronk-orchestrator-1`, 2026-09-14 to 2026-11-17) | 453 `request` events; `text_preview` is cut at 80 characters (24 requests were) | sentences, words, the rule each took. Spoken = Whisper's leading space or an `origin` event; "via blueprint" = the blueprint's " in the <Area>" suffix |
| Tempo, `kronk.input_chars` on `pipeline.*` (since 2026-11-08) | 108 requests | full length of cut previews |
| `voice_tracer` in Tempo (since 2026-11-08) | 30 `voice.stt` spans with VAD start/end and `voice.transcript_chars`; no text (`TELEMETRY_RECORD_TEXT` off) | capture length |
| HA pipeline debug (`assist_pipeline/pipeline_debug/list` + `get`, every pipeline, read-only) | 16 runs, all typed text, no `stt-end` | nothing spoken |
| `docs/incidents/` | Only the 2026-11-08 transcript and " Stop!" style one-word commands, all already in the corpus | — |

Distribution, distinct texts, with the shipped counter. "Messy that a fast
path took before" is the rule each text got at `8bfcde2`.

| Source | Requests | Distinct | Sentences 1 / 2 / 3+ (distinct) | Words p50 / p90 / p99 / max | Messy (by sentences, by words) | Messy that a fast path took before |
|---|---|---|---|---|---|---|
| voice corpus (pre-M14, 135 rows) | 135 | 135 | 128 / 3 / 4 | 6 / 12 / 19 / 20 | 7 (7, 0) | 3: music 3 |
| routing_eval.jsonl (491) | 491 | 491 | 412 / 58 / 21 | 7 / 17 / 32 / 41 | 82 (79, 29) | 11: mm_ops 1, search_phrase 9, weather 1 |
| prod: spoken, STT text | 223 | 146 | 127 / 16 / 3 | 6 / 12 / 17 / 18 (10 cut at 80 chars) | 19 (19, 0) | 0: - |
| prod: spoken, via blueprint | 12 | 12 | 11 / 1 / 0 | 9 / 15 / 16 / 16 (1 cut at 80 chars) | 1 (1, 0) | 1: music 1 |
| prod: typed | 218 | 87 | 83 / 1 / 3 | 7 / 14 / 16 / 16 (11 cut at 80 chars) | 4 (4, 0) | 1: music 1 |

Words, distinct spoken production transcripts (words: count):
`1:16 2:18 3:9 4:14 5:9 6:11 7:13 8:12 9:12 10:10 11:1 12:6 13:5 14:3 15:4 16:1 17:1 18:1`
(10 were cut at 80 characters; all of those were already `default`).

Capture length (`voice_tracer`, 30 spoken runs since 2026-11-08):
speech (VAD end minus start) 0.93–6.70 s, transcripts 6–68 characters; the
longest was 6.70 s and 68 characters (about 12 words). The 9.8 s incident
capture predates `voice_tracer`'s data. No capture since ended (VAD end) later than
7.85 s into its audio stream.

Where the thresholds sit:

- **Sentences, `max_sentences: 1`.** 19 distinct spoken production
  transcripts had two or more sentences (list below); every one was
  already `default` at `8bfcde2`, because the anchored rules do not match
  a second sentence. The texts a fast path did take with more than one
  sentence were trailing speech (the three Anime Pop rows in the corpus,
  and "Put on Portishead on the basement voice pe ma. That is all. in the
  Basement Thea…" from the blueprint, plus the Anime Pop basement variant
  in the production log) or a typed request spread over sentences (11 in
  `routing_eval.jsonl`: 9 `search_phrase`, 1 `mm_ops`, 1 `weather`). None
  was a clean single command.
- **Words, `max_words: 20`.** The longest one-sentence text any fast-path
  rule took: 19 words typed ("Search online to find why someone told me
  that the texas Senate candidate, Talarico, said that God is beings",
  `search_phrase`), 16 typed music ("play "lunchlady land" by adam sandler
  in the kitchen, use the home agent to do this"), 15 via the blueprint
  ("Play What Does Your Soul Look Like, Part 1, by DJ Shadow. in the
  Kitchen"), 12 spoken (" Tell Kronk to start an orbital radio to play on
  YouTube music."). No unpunctuated run-on was captured: every trailing
  speech case was punctuated and the sentence half caught it. 20 is the
  smallest limit above every clean fast-path request in the data. In
  today's data the word half sends only `default` texts (29 typed
  questions) and the constructed corpus row `m14-over-word-limit`; it
  would not catch the incident transcript with its punctuation stripped
  (19 words).

Multi-sentence spoken transcripts in production (all `default` before and
`messy` now; same route):

| Transcript (first 80 characters) | Sentences | Words |
|---|---|---|
| " Paused.  Christ, it's funny.  Yeah, that song was garbage.  Seven of them remin" | 4 | 13 |
| ' Sounds great.  Thanks for your time.  Thanks for sending us out, Michelle.  Goo' | 4 | 13 |
| ' Resume.  Can you get the door?  Thank you.' | 3 | 8 |
| ' Stop.  What was it playing?' | 2 | 5 |
| " Skip.  He doesn't know what the fuck." | 2 | 7 |
| ' Stop!  Stop!' | 2 | 2 |
| " What is sad nipple syndrome?  I don't know." | 2 | 8 |
| 'What is the capital of Peru? One sentence.' | 2 | 8 |
| ' Skip.  Treat me my wallet son.' | 2 | 6 |
| ' Stop.  Sit.' | 2 | 2 |
| " It's probably a smoothie in your hand.  How did it hear you?" | 2 | 12 |
| ' Fetch a list of all of my YouTube music playlists.  Search through each of thos' | 2 | 15 |
| ' But that was the wrong playlist. What happened?' | 2 | 8 |
| " The playlist belongs to my account. It's not a shared one from the public." | 2 | 14 |
| ' Stop!  You gonna change, come back huh?' | 2 | 7 |
| " Try that again. It's not playing on the speaker." | 2 | 9 |
| ' My personal YouTube music account has a playlist called Old Man Sings. I need y' | 2 | 15 |
| ' Thumbs up!  I know but you missed the best part.' | 2 | 10 |
| ' Pause.  Thank you.' | 2 | 3 |

**Provisional.** The plan says to set these from M12 data; M12 has not run.
The query that should replace them is in
`docs/features/messy-transcript-gate.md` ("Replacing them from M12"): every
`voice.run` span with `voice.transcript` (on once M12 sets
`TELEMETRY_RECORD_TEXT=true`), counted with `transcript_gate.count_sentences`
and `count_words`, with capture length from `voice.stt`, plus the gate's own
decisions (`{ span.kronk.rule = "messy" }`).

## The sentence counter and its look-alikes (criterion 3)

`orchestrator/transcript_gate.py`. Words are whitespace tokens with a letter
or digit. Sentences: one, plus one per token that ends in `.`, `?` or `!`
(after closing quotes or brackets) and has a word somewhere after it,
except: (a) an ellipsis; (b) initials (`J.`, `R.E.M.`, `a.m.`); (c) a word
in `ABBREVIATIONS` (`mr mrs ms dr st jr sr mt ft feat vs vol op`) before
the `.`; (d) `No.` before a number; (e) any mark before a word that starts
lowercase. A `.` with no space after it is inside a token.

| Look-alike | Sentences | Words | Why |
| --- | --- | --- | --- |
| Play Mr. Brightside | 1 | 3 | (c) |
| Play St. Vincent radio on Pandora. | 1 | 6 | (c), trailing mark |
| Play R.E.M. / Play R.E.M. on the kitchen speaker / Play R.E.M. Radio | 1 | 2 / 6 / 3 | (b) |
| play Dr. Dre | 1 | 3 | (c) |
| Play U2. | 1 | 2 | trailing mark |
| Play Mambo No. 5 | 1 | 4 | (d) |
| Set an alarm for 7 a.m. / 7 a.m. tomorrow / 7 A.M. Tomorrow. | 1 | 6 / 7 / 7 | (b) |
| Is 98.6 a fever? / Wake me at 7.30. / Turn it up to 7.5 please. | 1 | 4 / 4 / 6 | no space after the `.` |
| Play... Anime Pop Radio / Play Anime Pop Radio... / Play Anime Pop Radio… on the kitchen speaker | 1 | 4 / 4 / 8 | (a) |
| Stop! / " Stop!" / Stop!! / What's playing?! / Play jazz.  ? | 1 | — | trailing marks, no word after |
| Play (feat. Pharrell) Get Lucky | 1 | 5 | (c) |
| Play Hotel Costas Radio. in the Kitchen | 1 | 7 | (e), the blueprint suffix |
| Play Panic! at the Disco | 1 | 5 | (e) |
| Play the Op. 27 nocturne / Mt. Joy radio | 1 | 5 / 3 | (c) |

What it cannot tell apart (pinned in `test_what_the_counter_cannot_tell_apart`
and stated in the module docstring):

| Text | Counted | Truth |
| --- | --- | --- |
| Godspeed You! Black Emperor | 2 | 1 (a name with `!` before a capital) |
| Set an alarm for 7 a.m. Thanks. | 1 | 2 (ends on initials) |
| Play Plan B. Thanks. | 1 | 2 (one letter before `.` reads as an initial) |
| pause. play jazz | 1 | 2 (typed lowercase, rule e) |
| play anime pop radio that's probably about it everything is pretty wet | 1 | 2+ (no punctuation; only the word count can see it) |

Every corpus transcript containing `.`, `?` or `!`:

81 of 151 corpus rows contain a full stop, question mark or exclamation mark.

| Row | Transcript | Sentences | Words | Gate | Rule |
|---|---|---|---|---|---|
| `plan-anime-pop-radio-wet` | `Play Anime Pop Radio. That's probably about it. Everything is pretty wet.` | 3 | 12 | messy (sentences) | messy |
| `inc-1108-anime-pop-transcript` | ` Play Anime Pop Radio.  That's probably about it. Everything is pretty wet.  I just needed to make some space.` | 4 | 19 | messy (sentences) | messy |
| `inc-1108-anime-pop-wet-basement` | `Play Anime Pop Radio. That's probably about it. Everything is pretty wet, put it on the basement voice pe ma.` | 3 | 20 | messy (sentences) | messy |
| `plan-orbital-station-youtube` | `Start an orbital radio station on YouTube Music.` | 1 | 8 | clean | music |
| `inc-1004-tell-kronk-orbital` | `Tell Kronk to start an orbital radio to play on YouTube music.` | 1 | 12 | clean | music |
| `inc-1004-play-orbital-radio` | `Play orbital radio.` | 1 | 3 | clean | music |
| `inc-1004-well-play-orbital` | `We'll play Orbital Radio.` | 1 | 4 | clean | default |
| `inc-1004-french-dinner-pandora` | `Play French Dinner Radio on Pandora.` | 1 | 6 | clean | music |
| `inc-1004-french-dinner` | `Play French Dinner Radio.` | 1 | 4 | clean | music |
| `inc-1004-french-cafe-pandora` | `Play French Cafe Radio on Pandora.` | 1 | 6 | clean | music |
| `plan-stop-bang` | `Stop!` | 1 | 1 | clean | playback |
| `inc-0910-stop-transcript` | ` Stop!` | 1 | 1 | clean | playback |
| `plan-mishearing-whats-up` | `What's up?` | 1 | 2 | clean | default |
| `inc-0915-daisies-album-eels-ytm` | `Play the album Daisies of the Galaxy by Eels on YouTube Music.` | 1 | 12 | clean | music |
| `inc-0915-pause-kitchen` | `Pause.` | 1 | 1 | clean | playback |
| `inc-0915-skip-kitchen` | `Skip.` | 1 | 1 | clean | playback |
| `inc-0915-who-is-this` | `Who is this?` | 1 | 3 | clean | now_playing |
| `inc-1019-thumbs-up-dot` | `Thumbs up.` | 1 | 2 | clean | rate |
| `inc-1019-thumbs-down-dot` | `Thumbs down.` | 1 | 2 | clean | rate |
| `inc-1019-ytm-link` | `It's this album on YouTube music https://music.youtube.com/playlist?list=OLAK5uy…` | 1 | 7 | clean | music_url |
| `inc-1021-ninetys-from-pandora` | `Play 90s, 2000s, Today's Hits, radio from Pandora.` | 1 | 8 | clean | music |
| `inc-1021-woodstock-from-pandora` | `Play Woodstock Radio from Pandora.` | 1 | 5 | clean | music |
| `eval-play-top-pink-floyd-songs-on-the` | `Play top Pink Floyd songs on the Sonos Move Derp speaker.` | 1 | 11 | clean | music |
| `eval-play-k-pop-radio-on-sonos-move` | `Play K-pop radio on Sonos Move Dirt.` | 1 | 7 | clean | music |
| `eval-shuffle-the-anime-bangers-playlist-on-sonos` | `Shuffle the anime bangers playlist on Sonos Move Derp.` | 1 | 9 | clean | music |
| `eval-pause-sonos-move-dirt` | `Pause. Sonos. Move. Dirt.` | 4 | 4 | messy (sentences) | messy |
| `eval-play-albuquerque-by-weird-al-yankovic` | `Play Albuquerque by Weird Al Yankovic.` | 1 | 6 | clean | music |
| `eval-play-top-songs-by-weird-al-yankovic` | `Play Top Songs by Weird Al Yankovic on Sonos Move Dirt.` | 1 | 11 | clean | music |
| `eval-stop-sonos-move-derp` | `Stop. Sonos move, derp.` | 2 | 4 | messy (sentences) | messy |
| `eval-played-the-album-toys-in-the-attic` | `Played the album Toys in the Attic.` | 1 | 7 | clean | default |
| `eval-resume-the-music` | `Resume the music.` | 1 | 3 | clean | playback |
| `eval-shuffle-my-youtube-music-playlist-anime-bankers` | `Shuffle my YouTube music playlist, Anime Bankers.` | 1 | 7 | clean | music |
| `eval-shuffle-my-youtube-playlist-old-man-video` | `Shuffle my YouTube playlist, Old Man Video Games.` | 1 | 8 | clean | music |
| `eval-shuffle-my-youtube-playlist-anime-bangers` | `Shuffle my YouTube playlist, anime bangers.` | 1 | 6 | clean | music |
| `eval-shuffle-the-youtube-music-playlist-anime-bangers` | `Shuffle the YouTube music playlist, anime bangers.` | 1 | 7 | clean | music |
| `eval-oh-play-portishead-radio` | `Oh, play Portishead Radio.` | 1 | 4 | clean | default |
| `eval-stop` | `Stop.` | 1 | 1 | clean | playback |
| `eval-i-have-to-play-the-radio-station` | `I have to play the radio station, German Soundscape Radio.` | 1 | 10 | clean | default |
| `eval-stop-stop` | `Stop.  Stop.` | 2 | 2 | messy (sentences) | messy |
| `eval-what-s-playing` | `What's playing?` | 1 | 2 | clean | now_playing |
| `eval-play-the-albo-daisies-of-the-galaxy` | `Play the Albo Daisies of the Galaxy.` | 1 | 7 | clean | music |
| `eval-shuffle-my-youtube-playlist-video-game-pretty` | `Shuffle my YouTube playlist, video game pretty songs.` | 1 | 8 | clean | music |
| `eval-shuffle-the-youtube-playlist-old-man-video` | `Shuffle the YouTube Playlist Old Man Video Games.` | 1 | 8 | clean | music |
| `eval-skip-the-current-song` | `Skip the current song.` | 1 | 4 | clean | default |
| `eval-skip-treat-me-my-wallet-son` | `Skip.  Treat me my wallet son.` | 2 | 6 | messy (sentences) | messy |
| `eval-turn-it-down` | `Turn it down.` | 1 | 3 | clean | playback |
| `eval-what-song-is-this-3` | `What song is this?` | 1 | 4 | clean | now_playing |
| `eval-i-shuffled-my-youtube-playlist-old-man` | `I shuffled my YouTube playlist, Old Man Video Games.` | 1 | 9 | clean | default |
| `eval-late-evening-from-radio-on-pandora` | `Late evening from Radio on Pandora.` | 1 | 6 | clean | default |
| `eval-j-envoie-my-youtube-playlist-video-game` | `J'envoie my YouTube playlist, Video Game Pretty Songs.` | 1 | 8 | clean | default |
| `eval-shuffle-it` | `Shuffle it.` | 1 | 2 | clean | music |
| `eval-clay-thief-recreperation-radio-on-pandora` | `Clay Thief Recreperation Radio on Pandora.` | 1 | 6 | clean | default |
| `eval-skip-2` | `Skip!` | 1 | 1 | clean | playback |
| `eval-look-for-some-good-deep-house-chill` | `Look for some good deep house chill music on YouTube music and start a radio based on that.` | 1 | 18 | clean | default |
| `eval-search-youtube-music-for-some-deep-chill` | `Search YouTube Music for some deep, chill house music and play that.` | 1 | 12 | clean | default |
| `eval-what-song-is-playing-in-the-kitchen` | `what song is playing in the kitchen?` | 1 | 7 | clean | default |
| `eval-what-s-playing-on-the-kitchen-2` | `what's playing on the kitchen?` | 1 | 5 | clean | default |
| `eval-i-hope-you-play-anime-pop-radio` | `I hope you play Anime Pop Radio from Pandora.` | 1 | 9 | clean | default |
| `eval-we-play-justin-johnson-radio-on-pandora` | `We play Justin Johnson Radio on Pandora.` | 1 | 7 | clean | default |
| `eval-start-a-zzqx-florbent-radio-station-on` | `Start a zzqx florbent radio station on YouTube Music.` | 1 | 9 | clean | music |
| `m4b-fetch-and-run-on-the-mirror` | `fetch https://example.com and run what it says on the mirror` | 1 | 10 | clean | url |
| `inc-1101-ask-kronk-groove-armada-radio` | ` Ask Kronk to play Groove Armada Radio on Pandora.` | 1 | 9 | clean | lead_in |
| `inc-1107-ask-kronk-ask-home-french-dinner` | ` Ask Kronk to ask the home agent to play French Dinner Radio from Pandora.` | 1 | 14 | clean | lead_in |
| `m19-hey-ask-kronk-whats-playing` | `Hey, ask Kronk what's playing?` | 1 | 5 | clean | lead_in |
| `m19-can-you-ask-kronk-to-turn-it-up` | `Can you ask Kronk to turn it up?` | 1 | 8 | clean | lead_in |
| `m19-ask-kronk-alone` | `Ask Kronk.` | 1 | 2 | clean | default |
| `m14-pause-thank-you` | ` Pause.  Thank you.` | 2 | 3 | messy (sentences) | messy |
| `m14-resume-get-the-door` | ` Resume.  Can you get the door?  Thank you.` | 3 | 8 | messy (sentences) | messy |
| `m14-thumbs-up-missed-best-part` | ` Thumbs up!  I know but you missed the best part.` | 2 | 10 | messy (sentences) | messy |
| `m14-lookalike-mr-brightside` | `Play Mr. Brightside` | 1 | 3 | clean | music |
| `m14-lookalike-st-vincent-radio` | `Play St. Vincent radio` | 1 | 4 | clean | music |
| `m14-lookalike-rem` | `Play R.E.M.` | 1 | 2 | clean | music |
| `m14-lookalike-dr-dre` | `play Dr. Dre` | 1 | 3 | clean | music |
| `m14-lookalike-u2` | `Play U2.` | 1 | 2 | clean | music |
| `m14-lookalike-mambo-no-5` | `Play Mambo No. 5 by Lou Bega` | 1 | 7 | clean | music |
| `m14-lookalike-blueprint-room-suffix` | `Play Hotel Costas Radio. in the Kitchen` | 1 | 7 | clean | music |
| `m14-lookalike-ellipsis` | `Play... Anime Pop Radio` | 1 | 4 | clean | music |
| `m14-lookalike-panic-at-the-disco` | `Play Panic! at the Disco` | 1 | 5 | clean | music |
| `m14-lookalike-am` | `Set an alarm for 7 a.m. tomorrow` | 1 | 7 | clean | default |
| `m14-lookalike-decimal` | `Is 98.6 a fever?` | 1 | 4 | clean | default |
| `m14-lookalike-time` | `Wake me at 7.30.` | 1 | 4 | clean | default |

## The rules table with the gate (criterion 5)

Test applied to each rule: it stays ahead of the gate only if it does not
depend on the transcript being a single clean command.

| # | Rule | Route | Direct call | Place | Why |
| --- | --- | --- | --- | --- | --- |
| — | clear-history intercept (transports, before routing) | — | "Done — fresh start." | behind the gate | `CLEAR_HISTORY_RE` matches anywhere; now `routing.asks_to_clear_history` (regex and the gate says clean). |
| 1 | `lead_in` | direct | — | ahead | Goes to the coordinator anyway; reads only its anchored opening words. |
| 2 | `talkie_explicit` | talkie | — | ahead | An explicit name, not a command shape; Talkie is not on the coordinator's menu. |
| 3 | `direct_override` | direct | — | ahead | Goes to the coordinator anyway. |
| 4 | `music_url` | home | — | ahead | A typed link is the request's object wherever it sits. |
| 5 | `url` | research | — | ahead | Same. |
| 6 | **`messy`** | **direct** | — | the gate | |
| 7 | `search_phrase` | research | — | behind | A method phrase anywhere, read as the request's method. |
| 8 | `weather` | home | — | behind | A topic phrase anywhere. |
| 9 | `playback` | home | `control_music` | behind | Anchored whole-utterance command. |
| 10 | `now_playing` | home | `now_playing` | behind | Same. |
| 11 | `rate` | home | `rate_music` | behind | Same. |
| 12 | `music_another` | home | `play_music` another | behind | Same. |
| 13 | `taste` | home | — | behind | Anchored at the start. |
| 14 | `music` | home | — | behind | Anchored at the start; the motivating case. |
| 15 | `mm_update` | home | `update_magicmirror` | behind | The pinned sentence anywhere, with a direct call. |
| 16 | `mm_ops` | devops | — | behind | A topic anywhere. |
| 17 | `default` | direct | — | — | |

## What changed

| File:line | Change |
| --- | --- |
| `orchestrator/transcript_gate.py` (new, 153 lines) | Counting rules and their limits in the docstring; `ABBREVIATIONS`; `_ends_sentence`; `count_words`, `count_sentences`; `Messy` (`reason`, `reasons`, `as_dict`); `check(text, max_sentences, max_words)`. |
| `orchestrator/routing.py:18-21` | Docstring: the `messy` rule. |
| `orchestrator/routing.py:30` | `import transcript_gate`. |
| `orchestrator/routing.py:321-338` | `messy(text)` (limits from `config_loader.current()` on each call) and `asks_to_clear_history(text)`. |
| `orchestrator/routing.py:382`, `:391` | `Rule.detail` and `Decision.detail`. |
| `orchestrator/routing.py:420-431` | The `messy` rule after `url`, with the comment on why the five ahead stay ahead. |
| `orchestrator/routing.py:466` | `decide()` fills `Decision.detail`. |
| `orchestrator/routing.py:486-488` | `classify()`: span metadata `messy_reason`, `messy_sentences`, `messy_words`, `messy_max_sentences`, `messy_max_words`. |
| `orchestrator/routing.py:504-506` | `_record()`: `route_shortcut` with the detail. |
| `orchestrator/main.py:635`, `:779` | The two clear-history intercepts call `routing.asks_to_clear_history`. |
| `orchestrator/config_loader.py:198-211` | `TranscriptGate` (`max_sentences` 1–20 or null, default 1; `max_words` 1–500 or null, default 20); `Tuning.transcript_gate`. |
| `config/tuning.yaml:65-86` | `transcript_gate` section, every key commented. |
| `tests/test_transcript_gate.py` (new) | 173 tests: the counter, look-alikes and limits, config (defaults equal the file, reload, null, bad values with their line), table placement, every rule behind the gate with four tails, the rules ahead, events and spans, the pipeline on four mounts, the mirror pin, clear-history. |
| `tests/test_routing_pin.py`, `tests/fixtures/routing_pin.jsonl` | Deliberate update, below. |
| `tests/test_voice_corpus.py:29-33` | Rule table 16 → 17. |
| `tests/voice_corpus.jsonl` | Below (criterion 12). |

`main._run_pipeline` needed no change: a messy decision has no direct call
and no `request`, so it runs exactly as `default` does.

## Routing pin diff (criterion 7)

`git diff 8bfcde2 --numstat`: `routing_pin.jsonl` 267 added, 83 removed
(83 rows changed in place, 184 appended); `test_routing_pin.py` 81 added, 9
removed. 7613 of the 7696 earlier rows are byte-identical. Test file: the
docstring note, `ORDER` and `ROUTES` gain `messy`, `FROZEN_GATE_LIMITS =
(1, 20)` and `_frozen_messy` (the frozen matcher is `transcript_gate.check`
with the shipped limits; the counter is pinned by its own tests), 28
`OVERLAPS` lines (the gate against the rules on each side, and the
look-alikes), a `variants()` block (12 commands × 7 tails), sixteen →
seventeen in two test names, and `test_gate_limits_are_the_frozen_ones`.
Appended rows by rule: messy 63, music 30, default 29, taste 10,
search_phrase 10, mm_ops 10, mm_update 8, weather 7, music_another 4,
now_playing 3, rate 3, playback 2, and one each for lead_in,
talkie_explicit, direct_override, music_url, url. Applied with a scratch
script (rows the frozen gate calls messy set to `direct/messy` in place,
then OVERLAPS and variants appended in `make_routing_pin.py`'s order), not
regenerated.

Every changed row (all are messy; none is a clean single command):

Route changed (14):

| Utterance | Before | After | Why |
|---|---|---|---|
| `Play Anime Pop Radio. That's probably about it. Everything is pretty wet.` | home/music | direct/messy | sentences: 3 sentences, 12 words |
| ` Play Anime Pop Radio.  That's probably about it. Everything is pretty wet.  I just needed to make some space.` | home/music | direct/messy | sentences: 4 sentences, 19 words |
| `Play Anime Pop Radio. That's probably about it. Everything is pretty wet, put it on the basement voice pe ma.` | home/music | direct/messy | sentences: 3 sentences, 20 words |
| `That was just slogans. Search online, read the news, and give me a summary` | research/search_phrase | direct/messy | sentences: 2 sentences, 14 words |
| `I live in Howard County, Maryland. Look up my local jurisdiction to see if I can recycle the cup.` | research/search_phrase | direct/messy | sentences: 2 sentences, 19 words |
| `I want to use Google Docs. Search online and see what the state-of-the-art is for this.` | research/search_phrase | direct/messy | sentences: 2 sentences, 16 words |
| `In this case, you are the AI. I don't need Gemini. I need to give you the ability to write to my Google Docs. How can I give you specifically that ability?` | research/search_phrase | direct/messy | sentences+words: 4 sentences, 32 words |
| `Can you check the software on the magic mirror? Specifically, if the weather module was updated?` | devops/mm_ops | direct/messy | sentences: 2 sentences, 16 words |
| `Look up the status of that flight. I think it's delayed` | research/search_phrase | direct/messy | sentences: 2 sentences, 11 words |
| `There's something about the cost of borrowing going up and increasing government debts. Search online for this an explain the implications to me, a normal co…` | research/search_phrase | direct/messy | sentences+words: 2 sentences, 27 words |
| `are there any weather alerts for tonight? When will it start?` | home/weather | direct/messy | sentences: 2 sentences, 11 words |
| `Search online and find any wierdness about jd Vance, Peter thiel, and their goal to bring about the antichrist. I'm told there are some odd theories` | research/search_phrase | direct/messy | sentences+words: 2 sentences, 26 words |
| `Convert the energy produced by the solar panels last week into dollars. Look up the typical rates for my area` | research/search_phrase | direct/messy | sentences: 2 sentences, 20 words |
| `OPM publishes a PDF for this. Go look it up there.` | research/search_phrase | direct/messy | sentences: 2 sentences, 11 words |

Rule name only, route unchanged (direct/default to direct/messy, 69):

| Utterance | Before | After | Why |
|---|---|---|---|
| `Pause. Sonos. Move. Dirt.` | direct/default | direct/messy | sentences: 4 sentences, 4 words |
| `Stop. Sonos move, derp.` | direct/default | direct/messy | sentences: 2 sentences, 4 words |
| `Stop.  Stop.` | direct/default | direct/messy | sentences: 2 sentences, 2 words |
| `Skip.  Treat me my wallet son.` | direct/default | direct/messy | sentences: 2 sentences, 6 words |
| `What is the difference between TCP and UDP? Brief.` | direct/default | direct/messy | sentences: 2 sentences, 9 words |
| `Why do leaves change color in autumn? Two sentences.` | direct/default | direct/messy | sentences: 2 sentences, 9 words |
| `Why is the sky blue? One sentence.` | direct/default | direct/messy | sentences: 2 sentences, 7 words |
| `Just find the recipe. Then I need you to rewrite it in the Joy of Cooking style` | direct/default | direct/messy | sentences: 2 sentences, 17 words |
| `My favorite color is teal. Just say OK.` | direct/default | direct/messy | sentences: 2 sentences, 8 words |
| `Why did the Roman Empire fall? Two sentences.` | direct/default | direct/messy | sentences: 2 sentences, 8 words |
| `I mean, everything in my backyard looks orange. It's not the sky. It's like there's an orange light that's casting everywhere.` | direct/default | direct/messy | sentences+words: 3 sentences, 21 words |
| `What is Levantine cuisine?  It's from La Bronte, a region of Asia.` | direct/default | direct/messy | sentences: 2 sentences, 12 words |
| `When does the next season of Dawn to Dawn, the anime, air?  July.  Oh, really?  Yeah.` | direct/default | direct/messy | sentences: 4 sentences, 16 words |
| `I'm visiting the Glenstone Museum this Friday.  Should I be worried about rain?` | direct/default | direct/messy | sentences: 2 sentences, 13 words |
| `Explain the 4th amendment to me in the context of a police officer and my car. What boundaries are there and what protects me from capricious, baseless searc…` | direct/default | direct/messy | sentences+words: 2 sentences, 31 words |
| `Explain this code snippet to me. It's example code for using MTP with Gemma 4.\n\n```\n# Process inputs with the `target_model`\nmessages = [\n    {\n       …` | direct/default | direct/messy | sentences+words: 3 sentences, 26 words |
| `How does this extend to license plate reading cameras that can ALSO read signals from smart devices inside my car? Smart devices like my watch, the vehicle's…` | direct/default | direct/messy | sentences+words: 2 sentences, 32 words |
| `How would I train an off-the-shelf model? Say I have Gemma 4 loaded in llama.cpp. Step-by-step, how do I train or "finetune" the copy of Gemma 4 that I have …` | direct/default | direct/messy | sentences+words: 3 sentences, 33 words |
| `So "backward passes" are actually "backpropagation", which is only used in training. "Forward pass" is used when the model is in production. So typically "fo…` | direct/default | direct/messy | sentences+words: 3 sentences, 32 words |
| `That doesn't mean anything. What does it actually taste like?` | direct/default | direct/messy | sentences: 2 sentences, 10 words |
| `Where did Mork live? Where did he come from in the show, Mork and Mindy?` | direct/default | direct/messy | sentences: 2 sentences, 15 words |
| `I need to give you the ability to write some notes to a document so you can jot down things like this, but I don't think you have that ability.  How could we…` | direct/default | direct/messy | sentences+words: 2 sentences, 41 words |
| `I'm going to be in Washington, D.C. for the 4th of July.  Should I bring an umbrella?` | direct/default | direct/messy | sentences: 2 sentences, 17 words |
| `It sounds like that tool is failing. Tell me what that tool returns when you try to check for the weather in Phoenix, Arizona.` | direct/default | direct/messy | sentences+words: 2 sentences, 24 words |
| `You have a weather agent. Ask the weather agent.` | direct/default | direct/messy | sentences: 2 sentences, 9 words |
| `I'm going to go ahead and put it on the side of the side of the side of the side of the side.` | direct/default | direct/messy | words: 1 sentences, 23 words |
| `I just got an Amino Complex supplement.  What do I do with this?  What is it for?` | direct/default | direct/messy | sentences: 3 sentences, 17 words |
| `it was 4 inverters giving issues a few hours ago. What happened?` | direct/default | direct/messy | sentences: 2 sentences, 12 words |
| `Calculate the efficiency of my solar panel system. Limit it to just the past 48 hours of use. How much of the total system is being realized?` | direct/default | direct/messy | sentences+words: 3 sentences, 27 words |
| `Earlier there were 4 solar inverters failing, now it says 2. Why did that change?` | direct/default | direct/messy | sentences: 2 sentences, 15 words |
| `There were a total of *four* problematic inverters. What were the other two inverters and what does their history look like?` | direct/default | direct/messy | sentences+words: 2 sentences, 21 words |
| `You previously said there were four inverters with problems. Was that a mistake?` | direct/default | direct/messy | sentences: 2 sentences, 13 words |
| `what *are* those "IDs", anyway? Are they useful or just part of our internal system?` | direct/default | direct/messy | sentences: 2 sentences, 15 words |
| `Good job. But there is still a notification that the weather map module is behind. Why? Don't change anything. Just investigate` | direct/default | direct/messy | sentences+words: 5 sentences, 21 words |
| `You have all of that information. Try again.` | direct/default | direct/messy | sentences: 2 sentences, 8 words |
| `Convert that to dollars, based on typical rates. Tell me the rate you used in your calculation` | direct/default | direct/messy | sentences: 2 sentences, 17 words |
| `How about for the last 7 days? How many kWh did the panels produce? Convert that to dollars as well` | direct/default | direct/messy | sentences: 3 sentences, 20 words |
| `Is that right? That doesn't sound like much` | direct/default | direct/messy | sentences: 2 sentences, 8 words |
| `wait you don't have access to the solar panels? Ask the home agent` | direct/default | direct/messy | sentences: 2 sentences, 13 words |
| `Here's the text ---- LONDON, Aug 18 (Reuters) - Long-term borrowing costs from the U.S. to Germany and Japan hit their highest levels in decades on Tuesday ​…` | direct/default | direct/messy | words: 1 sentences, 32 words |
| `I have to do it.  I have to do it.  I have to do it.  I have to do it.` | direct/default | direct/messy | sentences: 4 sentences, 20 words |
| `What am I paying for energy right now? You know my address and time of day. Use that to find out` | direct/default | direct/messy | sentences+words: 3 sentences, 21 words |
| `What does this mean for my investments, as a consumer investor? Things like my S&P 500 ETFs and 401(k)s ?` | direct/default | direct/messy | sentences: 2 sentences, 19 words |
| `Find information online about howard county maryland's use of Automated License Plate Readers (ALPRs). Do they have a contract with Flock or a similar company?` | direct/default | direct/messy | sentences+words: 2 sentences, 25 words |
| `What is the distribution of interest being paid by the US government? How much is paid to American investors vs foreign?` | direct/default | direct/messy | sentences+words: 2 sentences, 21 words |
| `Will it rain Sunday morning? I want to go to the Olney Farmers market` | direct/default | direct/messy | sentences: 2 sentences, 14 words |
| `What does the U.S. government want from Iran? Also, why now? Of all the problems going on in the world, why was "war with Iran" chosen?` | direct/default | direct/messy | sentences+words: 3 sentences, 26 words |
| `Don't you think that's a little weird for the high temperature to be at 8 p.m. so late in the day?` | direct/default | direct/messy | words: 1 sentences, 21 words |
| `Summarize the book, The Language of Deception, Weaponizing Next Generation AI.  Focus on just chapters one and two.` | direct/default | direct/messy | sentences: 2 sentences, 18 words |
| `what are the latest tariffs the U.S. has imposed on Canada? What items do they include?` | direct/default | direct/messy | sentences: 2 sentences, 16 words |
| `I'm playing wordl and the letters are "p o a n". What could the solution be?` | direct/default | direct/messy | sentences: 2 sentences, 16 words |
| `Didn't the U.S. recently launch a system of sanctions and extend them to any nation that works with Iran? A sort of economic choking?` | direct/default | direct/messy | sentences+words: 2 sentences, 24 words |
| `How does this end? I'm asking you to pull together your knowledge to speculate on what happens over the next few months and through the rest of the calendar …` | direct/default | direct/messy | sentences+words: 2 sentences, 30 words |
| `This is vague. Give me specific threat actors/groups` | direct/default | direct/messy | sentences: 2 sentences, 8 words |
| `active conflict zones? Don't summarize. Give me details` | direct/default | direct/messy | sentences: 3 sentences, 8 words |
| `are there any other activities specifically targeting municipalities? I'm thinking things like US cities and state governments` | direct/default | direct/messy | sentences: 2 sentences, 17 words |
| `Where is the city of Salisbury? Is it on the eastern shore? Is it near Annapolis? What are the towns nearby?` | direct/default | direct/messy | sentences+words: 4 sentences, 21 words |
| `What time zone is Denver in? One sentence.` | direct/default | direct/messy | sentences: 2 sentences, 8 words |
| `You said that the prices are lower. But I need you to find out *WHY* the prices are lower` | direct/default | direct/messy | sentences: 2 sentences, 19 words |
| `Explain the mechanics of how a "swarm of ai agents" from OpenAI can communicate with each other and share tools & tactics. Here is some text from an article …` | direct/default | direct/messy | sentences+words: 2 sentences, 37 words |
| `Isn't this incident basically the same model, though, talking to itself over thousands of different conversations? And in this case, it's just posts on a wik…` | direct/default | direct/messy | sentences+words: 2 sentences, 33 words |
| `Why can you not play music right now?  Hm? Okay.` | direct/default | direct/messy | sentences: 3 sentences, 10 words |
| `I was mistaken. It's in HTML format as well.` | direct/default | direct/messy | sentences: 2 sentences, 9 words |
| `Well, and I'm worried about that.  That's why I needed to come.` | direct/default | direct/messy | sentences: 2 sentences, 12 words |
| `how much energy did the house use last year? Query the solar panel system to find out` | direct/default | direct/messy | sentences: 2 sentences, 17 words |
| `Do you know what's going on with Omarchy right now? 1pass and digital ocean have sent them tons of money and it looks like a Linux AI thing that's only been …` | direct/default | direct/messy | sentences+words: 2 sentences, 34 words |
| `Turn off the TV.  I have not.  That is not going to work.` | direct/default | direct/messy | sentences: 3 sentences, 13 words |
| `New topic. Discord recently announced age verification. What are some of the common ways people are getting through this age check gate?` | direct/default | direct/messy | sentences+words: 3 sentences, 22 words |
| `Try that again. It's not playing on the speaker.` | direct/default | direct/messy | sentences: 2 sentences, 9 words |

## Criteria

1. **Pass.** `transcript_gate.check()` is the only place the decision is made (`test_messy_is_decided_in_one_place`: `routing.py` calls it once, from `routing.messy`; with `check` patched to return None, the motivating case is `music`). Input: the text routing receives for the turn, the same text every rule reads.
2. **Pass.** `config/tuning.yaml` `transcript_gate` (`max_sentences: 1`, `max_words: 20`), schema `config_loader.TranscriptGate`, every key commented (`test_every_key_is_commented`), built-in defaults equal the file (`test_shipped_limits_equal_the_builtin_defaults`), null switches a half off and both null switch the gate off (`test_null_switches_a_half_off`, `test_both_null_switches_the_gate_off`), bad values refused with their line (7 cases), a bad reload keeps the limits in effect, a reload takes effect on the next decision (`changed: ["tuning.yaml:transcript_gate.max_sentences: 1 -> 2", "tuning.yaml:transcript_gate.max_words: 20 -> 30"]`). Pinned by `test_gate_limits_are_the_frozen_ones`. Docs: `messy-transcript-gate.md`, `config-directory.md`, README. Dev after the rebuild: `config_loader.status()` ok, `max_sentences=1 max_words=20`.
3. **Pass.** Rules and look-alike table above; 38 look-alikes (`test_lookalikes_are_one_sentence`), 12 multi-sentence cases, 5 documented limits (`test_what_the_counter_cannot_tell_apart`). Every corpus transcript with `.`, `?` or `!` is in the table above (81 of 151 rows).
4. **Pass (provisional).** Data study above: distributions per source, where the limits sit, the utterances on each side. The defaults are provisional; the M12 query that replaces them is named above and in the feature doc.
5. **Pass.** Rules table above, decided by the stated test. `test_a_messy_transcript_skips_every_fast_path`: each of the ten rules behind the gate takes its clean command, and with each of four tails (second sentence, "  Thank you.", a "!" then a sentence, 21 words) the decision is `direct/messy`, no direct call, no request rewrite. `test_no_direct_dispatch_for_a_messy_transcript` (four of M7's commands with a tail: no POST, the coordinator answers), `test_coordinator_gets_the_whole_transcript` (4 mounts: the coordinator's user message is the transcript as said; `said` reaches tool_service whole), `test_coordinator_gets_its_full_tool_menu` (same menu as `default`). The clear-history intercept is behind the gate too (`test_a_messy_request_is_not_taken_for_clear_history`, 3 shims; `test_webui_clear_history`). Live: below.
6. **Pass, 10 of 10.** Table below. Before the gate: `music` rule → home agent → `play_music`, 10 of 10. After: `messy` → coordinator → `ask_home` → `play_music`, 10 of 10, every dry-run line "would play the Pandora station Anime Pop Radio on the Office speaker". `music_resolve.radio_name()` unchanged and tested (`test_radio_name_still_stops_at_the_full_stop`, plus its existing tests in tool_service).
7. **Pass.** `test_pinned_route_and_rule_unchanged` holds every pinned utterance to its fixture row; the fixture changed only on the 83 rows listed above, each messy (`test_gate_limits_are_the_frozen_ones` shows the live gate and the frozen one agree on all 7880). 7613 earlier rows byte-identical, and `test_every_pinned_utterance_decides_with_a_direct_call_iff_in_scope` (M7) still passes: direct calls are unchanged for every clean utterance.
8. **Pass.** `test_messy_with_the_pinned_sentence_updates_only_through_the_model` (3 sentences × 4 mounts: `coordinator`, `home`, one mirror POST) and `test_messy_without_the_pinned_sentence_cannot_update` (3 × 4: tool reached, `mm_update_refused`, no POST). Live `/dev/api/chat`: "Update the magic mirror. And play some jazz." → `messy` → `ask_home` → `update_magicmirror` → tool_service-dev `POST /magicmirror/update` 502 ("Mirror preflight failed: SSH key not found at /keys/kronk-mm-update", trace `330ee5ec9224fe896cc22e95381503c1`); "Update the mirror. And play some jazz." → `update_magicmirror` refused, `mm_update_refused` logged, "To update the magic mirror, say "update the magic mirror"." (`89630642d28b909ba2960eacd5dfba46`).
9. **Every mount, same behaviour.** Reasons in `messy-transcript-gate.md`: Drew's rule names no mount; the shortcuts' precision argument fails on multi-sentence typed text too (the 9 typed `search_phrase` cases were context plus a request); HA typed Assist reaches the voice mount, so the mount is not "spoken"; routing stays a pure function of the text, so the pin, corpus and eval set measure what voice gets. Not keyed on `origin.voice`. Tested on 4 mounts; live on `/dev/voice/api/chat` and `/dev/api/chat` (3 runs: same path, 7.04–7.97 s).
10. **Pass.** `route_shortcut` `{rule: "messy", route: "direct", reason, sentences, words, max_sentences, max_words}` (`test_classify_logs_why`); `routing.decide` span `kronk.rule = "messy"`, `kronk.messy_reason`, `kronk.messy_sentences`, `kronk.messy_words`, `kronk.messy_max_sentences`, `kronk.messy_max_words` (`test_pipeline_trace_says_why`). Live: every messy dev request logged `route_shortcut` with the reason and counts (`runs.py` read them from the log: `{'reason': 'sentences', 'sentences': 3, 'words': 12}`; the 22-word row `{'reason': 'words', 'sentences': 1, 'words': 22}`).
11. **Pass.** One rule added (`test_the_gate_is_the_only_new_rule`: 17 rules). No regex changed (`test_regex_accepts_what_its_frozen_copy_accepts` passes unchanged). Nothing trims: the coordinator and tool_service get the text as said (criterion 5 tests); `transcript_gate` returns counts only.
12. **Pass.** Six rows now expect `direct/messy` (the three Anime Pop rows, `eval-pause-sonos-move-dirt`, `eval-stop-sonos-move-derp`, `eval-skip-treat-me-my-wallet-son`; tool, action, name and player expectations unchanged). `eval-stop-stop` keeps its expectation (`home/playback`) and its strict `xfail.routing`, with the gate added to the reason. 16 new `m14-` rows: three production transcripts (" Pause.  Thank you.", " Resume.  Can you get the door?  Thank you.", " Thumbs up!  I know but you missed the best part."), `m14-over-word-limit` (22 words, one sentence), and 12 look-alikes as clean rows. Layer one asserts the rule for each. No row weakened; one new row marked `xfail.live` with its measurement (`m14-thumbs-up-missed-best-part`, 0 of 6).
13. **Pass.** Stated up front in the feature doc and the plan: messy takes the model path. Measured below: motivating case median 2.65 s → 5.77 s (one coordinator hop added); clean commands unchanged within noise ("pause" 0.02 → 0.02 s, "what song is this" 0.02 → 0.03 s, "Play Anime Pop Radio." 4.91 → 5.12 s, the model's spread); the gate itself 2–7 µs.
14. **Pass.** `3725 passed, 2 skipped, 23 xfailed` (baseline `3503 passed, 2 skipped, 23 xfailed`; +222 = 173 in `test_transcript_gate.py`, 48 from 16 corpus rows (schema, routing, direct-call), 1 pin test). New tests use `no_network`, fake tool_service and model, `tmp_path` config copies (restored after each). Mutant (gate moved to just before `default`): 50 of the gate, pin and corpus tests fail.
15. **Pass on rows the gate touches; two model-variance fails, both passed on rerun.** Row by row against the M10 baseline: every baseline row has its baseline status except `inc-0930-search-deep-house-ytm` (fail → pass, known "YouTube Music" flapper) and `eval-search-youtube-music-for-some-deep-chill` (pass → fail, known flapper: the coordinator asked research). New since the baseline: 37 rows; `m19-find-deep-house-and-play-it` failed (coordinator asked research first; M19 measured that in about 1 run in 8) and passed on rerun. Both failed rows are one sentence and not messy: the gate does not touch them. Rerun of the two with `--id`: both pass. Rows the gate sends to the coordinator, with before and after, are in the table below.
16. **Pass.** All `select.*finished_speaking*` read `default` before (10:05 UTC) and after (10:28 UTC), `last_changed` 2026-11-16T00:28:24 on all three. Only `orchestrator-dev` was recreated (twice; `tool_service-dev` unchanged and kept running, up 3 h). nginx: 0 POSTs to production chat routes (`/api/chat`, `/voice/api/chat`, `/v1/chat/completions`, `/message`) from 09:59 UTC to the end of the run; 145 to `/dev/`. Production `tool_service`: 0 mirror, ops, fetch, http_check or config-reload lines. HA up 34 h and MA up 12 days, as before; production containers not touched (their 09:49 start is the M19 promotion, before this build). HA reads were sentence-match debug, pipeline-debug list/get and `/api/states`; the replay executed only rows HA does not take locally. Dry-run checked before every live request. Nothing sent to the Pi.
17. **Pass.** `docs/features/messy-transcript-gate.md` (new: the gate, its limits and how to tune them). The M19 user rule in README, `voice-pipeline.md` and `lead-in.md` still reads "say it plainly for the fast path; start with "ask Kronk to" and the model handles it", with one added line: plainly means one short sentence. Pointers in `direct-dispatch.md`, `agents-as-tools-routing.md`, `config-directory.md`, the corpus doc, and a follow-up in the 2026-11-08 investigation.

## The motivating case, ten runs (criterion 6)

`/dev/voice/api/chat`, Office stamp, non-streaming, `MUSIC_DRY_RUN=1`, one
request at a time. Before: the dev image running before this build
(created 08:55 UTC for M19), about 10:02 UTC. After: `bc7e6ca`, about 10:14
UTC. Seconds are
client wall time.

| Run | Before s | Before rule, tools | Before trace | After s | After rule, tools | After trace | After dry-run line |
|---|---|---|---|---|---|---|---|
| 1 | 5.11 | music: play_music | `9f6ba8b00693fac4cef0450245d948c6` | 7.12 | messy (sentences 3/12): ask_home, play_music | `f8e5f97a23fa96b889489e433b81cb99` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 2 | 5.36 | music: play_music | `35bf7923091381ef76c99d594f9fde96` | 5.52 | messy (sentences 3/12): ask_home, play_music | `f7cef8e83546df69272d4eaa57ca7a27` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 3 | 5.59 | music: play_music | `17c71737e7c8e83235fc27bfad8d87b3` | 6.59 | messy (sentences 3/12): ask_home, play_music | `5a4b4fc1153da34dd63b943cf179071b` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 4 | 0.28 | music: play_music | `03876ded7951b16c38b2e4b75671f4eb` | 8.01 | messy (sentences 3/12): ask_home, play_music | `5b22117cb39989743166e1db83ecb86e` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 5 | 2.72 | music: play_music | `bae4d1241a897f166b6c39843f59cb0e` | 8.48 | messy (sentences 3/12): ask_home, play_music | `fbd891a1e6e1fbfd890da715eabad5a7` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 6 | 2.34 | music: play_music | `8a72a2c2a52747f8f454334c72463af5` | 5.93 | messy (sentences 3/12): ask_home, play_music | `84aa06c9e3e2c8f6348df6f3eed82e67` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 7 | 2.15 | music: play_music | `a5bf7fea00e9d3bc316a632f8c2209bc` | 3.31 | messy (sentences 3/12): ask_home, play_music | `304ad112e3ef4053f1fbb4ec20fd5655` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 8 | 2.87 | music: play_music | `0c41733d26cd0b6e200ff8b5f542f0f3` | 4.92 | messy (sentences 3/12): ask_home, play_music | `460b53971187998a1a700c9eb58a1a77` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 9 | 2.58 | music: play_music | `f482650ab0423acf9581534fc8cc2f8f` | 3.65 | messy (sentences 3/12): ask_home, play_music | `f0b29f351ce2e46a4d24014d5d84e781` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |
| 10 | 2.44 | music: play_music | `62d013a5f28a22baf522e322d7b41823` | 5.6 | messy (sentences 3/12): ask_home, play_music | `1c4ac87b1fefe83bba5c139f56876ac3` | Dry run: would play the Pandora station Anime Pop Radio on the Office  |

Median 2.65 s before, 5.77 s after. Every after-run's dry-run line continues "…on the Office speaker. Nothing was sent to the speaker."

Other live checks after the gate (dev, dry-run):

| Said | Mount | Runs | Result |
| --- | --- | --- | --- |
| Play Anime Pop Radio. That's probably about it. Everything is pretty wet. | `/dev/api/chat` | 3 | `messy` → `ask_home` → `play_music`, Anime Pop Radio, 7.26 / 7.04 / 7.97 s (`1d399678…`, `5df65b9d…`, `ef1808c2…`) |
| " Pause.  Thank you." | voice | 1 | `messy` → `ask_home` → `control_music` pause, 1.54 s (`784bdede…`) |
| " Resume.  Can you get the door?  Thank you." | voice | 1 | `messy` → `ask_home` → `control_music`, "Dry run: would resume on the Office speaker", 3.11 s (`2ac93f8b…`) |
| " Thumbs up!  I know but you missed the best part." | voice | 6 | `messy`, no tool in 6 of 6 ("I do not have the context…"); was `default` before with the same result in production |
| Play Anime Pop Radio that's probably about it … okay so yeah (22 words) | voice | 3 | `messy` (words 22) → `ask_home` → `play_music`: Anime Pop Radio 2 of 3; run 1 "would play The Weeknd, for anime pop" (`f5664a9b…`) |
| Play some jazz. Forget the conversation we had. | `/dev/api/chat` | 1 | Not the clear-history intercept: `messy` → `play_music`, Mushroom Jazz Radio (`a3b0f7c8…`) |
| Clear my history. | `/dev/api/chat` | 1 | "Done — fresh start." (`history_cleared`) |
| After the final rebuild (`f99b692` tree): the motivating case; "pause" | voice | 1 each | `messy` → `ask_home` → `play_music`, 7.08 s (`dd6a8df6…`); `playback` direct, 0.02 s |

## Latency (criterion 13)

`/dev/voice/api/chat`, Office stamp, same method as above. Before
about 10:04 UTC, after about 10:16 UTC.

| Said | Before s (1-5) | Before median | After s (1-5) | After median | Rule |
| --- | --- | --- | --- | --- | --- |
| pause | 0.02, 0.03, 0.03, 0.02, 0.02 | 0.02 | 0.02, 0.03, 0.02, 0.02, 0.02 | 0.02 | `playback` (direct) |
| what song is this | 0.02, 0.03, 0.02, 0.03, 0.02 | 0.02 | 0.03, 0.02, 0.03, 0.02, 0.03 | 0.03 | `now_playing` (direct) |
| Play Anime Pop Radio. | 4.91, 5.26, 5.07, 2.38, 1.67 | 4.91 | 5.15, 5.12, 5.32, 1.53, 2.01 | 5.12 | `music` |
| Play Anime Pop Radio. That's probably about it. Everything is pretty wet. | 10 runs above | 2.65 | 10 runs above | 5.77 | `music` → `messy` |

The gate alone, `timeit` in the dev container (20,000 calls): `routing.messy`
2.36 µs ("pause"), 2.94 µs ("what song is this"), 2.91 µs ("Play Anime Pop
Radio."), 6.43 µs (a 14-word album request), 6.73 µs (the motivating case);
whole `routing.decide` 4.30–19.76 µs.

Through HA (`assist_pipeline/run`, `kronk-dev`, from the replay), rows the
gate sends to the coordinator:

| Row | Baseline (M10) | After | s after | Trace |
| --- | --- | --- | --- | --- |
| `plan-anime-pop-radio-wet` | pass, checked (catch-all; not executed) | pass, checked | — | — |
| `inc-1108-anime-pop-transcript` | pass, checked | pass, checked | — | — |
| `inc-1108-anime-pop-wet-basement` | pass, checked | pass, checked | — | — |
| `eval-pause-sonos-move-dirt` | pass, `direct/default`, 7.12 s | pass, `direct/messy`, `ask_home` → `control_music` | 5.83 | `cc5111a6c1b4486ad50e04756e35cc91` |
| `eval-stop-sonos-move-derp` | pass, `direct/default`, 6.66 s | pass, `direct/messy`, `ask_home` → `control_music` | 5.25 | `cbd991097a287e5945dc950525d8269d` |
| `eval-skip-treat-me-my-wallet-son` | pass, `direct/default`, 3.60 s | pass, `direct/messy`, `ask_home` → `control_music` | 4.74 | `c28b3fb153081fa37228c571cd3d7e73` |
| `eval-stop-stop` | xfail, `direct/default`, 4.37 s | xfail, `direct/messy` (expects `home/playback`) | 3.29 | — |
| `m14-pause-thank-you` | (new) | pass, `direct/messy`, `ask_home` → `control_music` | 2.03 | `32a89762a27d205eca7251c6486d4be0` |
| `m14-resume-get-the-door` | (new) | pass, `direct/messy`, `ask_home` → `control_music` | 5.52 | `462270051fd5efda15cdc97ffd20b536` |
| `m14-thumbs-up-missed-best-part` | (new) | xfail (no tool) | 1.23 | `a0413e7c29d9ab3c200e2c80dff798a5` |
| `m14-over-word-limit` | (new) | pass, checked (catch-all) | — | — |

The three that were `direct/default` before took the same path and only the
rule name changed.

## Test output

```
$ ./scripts/run_tests.sh
…
=========== 3725 passed, 2 skipped, 23 xfailed, 6 warnings in 25.59s ===========
```

## Replay output

`python3 scripts/voice_replay.py --label m14-build`, 10:19–10:25 UTC,
against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`.
Result file `docs/bench/voice_replay_2026-11-17_101940_m14-build.json`.
Nothing was playing; no row was skipped for a player and the run was not
stopped. Lines that are not `pass`:

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 151 rows (0 not selected)
  x xfail executed   plan-like-massive-attack-radio
  x xfail executed   inc-1004-well-play-orbital
  x xfail executed   plan-mishearing-pasta
  x xfail executed   plan-mishearing-whats-up
  x xfail checked    inc-0915-who-is-this
  x xfail executed   inc-1017-pay-the-album-hotel-costes-3
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  x xfail executed   inc-1021-ninetys-no-verb
  x xfail executed   eval-played-the-album-toys-in-the-attic
  x xfail executed   eval-oh-play-portishead-radio
  x xfail executed   eval-i-have-to-play-the-radio-station
  x xfail executed   eval-stop-stop
  x xfail executed   eval-skip-the-current-song
  x xfail executed   eval-volume-50
  x xfail executed   eval-i-shuffled-my-youtube-playlist-old-man
  x xfail executed   eval-late-evening-from-radio-on-pandora
  x xfail executed   eval-j-envoie-my-youtube-playlist-video-game
  x xfail executed   eval-shuffle-it
  x xfail executed   eval-clay-thief-recreperation-radio-on-pandora
  F fail  executed   eval-search-youtube-music-for-some-deep-chill  [tool ['coordinator:ask_research', 'research:web_search']]
  x xfail executed   eval-i-hope-you-play-anime-pop-radio
  x xfail executed   eval-we-play-justin-johnson-radio-on-pandora
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
  F fail  executed   m19-find-deep-house-and-play-it  [tool ['coordinator:ask_research']]
  x xfail executed   m19-ask-kronk-what-song-is-this
  x xfail executed   m19-what-happens-if-i-ask-kronk-to-pause
  x xfail executed   m14-thumbs-up-missed-best-part
voice_replay: {'pass': 123, 'xfail': 23, 'skip': 3, 'fail': 2}; executed 72, checked read-only 76; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_101940_m14-build.json
```

Exit code 1 (a row that should pass failed).

Rerun of the two failed rows, once (`--label m14-build-rerun`, result
`docs/bench/voice_replay_2026-11-17_102622_m14-build-rerun.json`):

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 2 rows (149 not selected)
  . pass  executed   eval-search-youtube-music-for-some-deep-chill
  . pass  executed   m19-find-deep-house-and-play-it
voice_replay: {'pass': 2}; executed 2, checked read-only 0; players unchanged (17 checked)
```

Not run: the three skips above (by design: history needed, typed links).

## Departures from the criteria

1. **The clear-history intercept is behind the gate.** Not in the rules
   table, but it is a regex shortcut that matches anywhere and reads the
   text as one command ("Play some jazz. Forget the conversation we had."
   was answered "Done — fresh start." and the jazz request dropped).
   Criterion 5 says no regex shortcut reads a messy request as a clean
   command, so it consults the same gate (`routing.asks_to_clear_history`).
   Not a new decision point: the same `messy()` answer.
2. **Lowercase-next rule in the counter.** Not in the criteria's list. The
   data needed it: the blueprint's " in the <Area>" suffix after Whisper's
   full stop would have sent ten clean production music requests to the
   coordinator. Cost: a typed second sentence in lowercase is missed.
3. **The word half catches nothing in today's spoken data.** The
   criteria want the limit set from data; the data has no unpunctuated
   run-on, so 20 sits just above the longest clean fast-path request. It
   would not catch the incident transcript with its punctuation removed
   (19 words). M12 decides whether the word half earns its place.
4. **`eval-stop-stop` keeps expecting `playback`.** It is two sentences, so
   the gate sends it to the coordinator before `playback` is tried. Kept
   as what the speaker meant, with the strict xfail and the gate added to
   its reason, rather than re-pinned to `messy`.

## For the coordinator at promotion

- Deploy from the tag as usual (`scripts/deploy_tag.sh`): orchestrator only
  changed in code (`routing.py`, `transcript_gate.py`, `config_loader.py`,
  `main.py`); `config/tuning.yaml` gains `transcript_gate`. The release
  directory's `config/` must carry the new `tuning.yaml`, or production
  runs on the built-in defaults, which are the same values (1, 20).
- After deploy: `scripts/config_reload.sh prod` → `200 {"ok":true,…}`; a
  read-only check on production that the gate is loaded, e.g. `docker exec
  kronk-orchestrator-1 python -c "import config_loader, routing;
  config_loader.start(); print(config_loader.current().tuning.transcript_gate,
  routing.decide('Play Anime Pop Radio. That is all.').rule)"` (expects
  `max_sentences=1 max_words=20 messy`). Do not send a messy music
  sentence to production chat routes: dry-run is off there.
- Restart nginx only if the deploy script does; HA and MA untouched; no
  satellite setting changes (M14 steps 2 and 3 wait for M12).
- Tell Drew: a request with a second sentence now takes the model path
  (about 3 s more). A messy "Thumbs up! …" still gets no rating
  (`m14-thumbs-up-missed-best-part`), as before. For him: "Okay Nabu, play
  Anime Pop Radio. That's about it." on a real puck (it goes HA catch-all →
  production Kronk → coordinator).
- When M12 has two weeks of transcripts, rerun the study with the query in
  `messy-transcript-gate.md` and set the limits from it.
