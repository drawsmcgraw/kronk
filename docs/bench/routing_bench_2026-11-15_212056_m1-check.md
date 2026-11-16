# Routing bench — m1-check (2026-11-15_212056)

test items 118 (first seen ≥ 2026-09-01, bench prompts excluded); exemplars 357; coverage pool = home-labelled test items the pins send to the coordinator (26)

| Arm | wrong-lane | named wrong | coverage | abstain | median ms | p95 ms |
|---|---|---|---|---|---|---|
| regex | 1 | 0 | 0/26 | 76 | 0.1 | 0.1 |

Pass rule: wrong-lane ≤ regex baseline AND named wrong = 0 AND coverage ≥ 50% AND p95 < 50 ms. `@N` = thresholds fit allowing N% wrong-lane on the exemplars (leave-one-out).

## regex: wrong-lane rows
- 'Convert the energy produced by the solar panels last week into dollars' label=direct pred=research (search_phrase)

## Coverage pool (home-labelled test items the pins leave to the coordinator), per arm
- 'Played the album Toys in the Attic.' [music]: 
- 'Oh, play Portishead Radio.' [music]: 
- 'How much energy have the solar panels produced this week?' [solar]: 
- 'how much energy did the house use last year? Query the solar' [solar]: 
- 'what is the hostname for the solar panel pvs?' [solar]: 
- 'Any more rain today?' [weather]: 
- 'temperature' [weather]: 
- 'I have to play the radio station, German Soundscape Radio.' [music]: 
- 'Stop.  Stop.' [playback]: 
- 'how is it outside right now?' [weather]: 
- 'Skip the current song.' [playback]: 
- 'Skip.  Treat me my wallet son.' [playback]: 
- 'volume 50%' [playback]: 
- 'I shuffled my YouTube playlist, Old Man Video Games.' [music]: 
- 'Late evening from Radio on Pandora.' [music]: 
- "J'envoie my YouTube playlist, Video Game Pretty Songs." [music]: 
- 'Clay Thief Recreperation Radio on Pandora.' [music]: 
- 'Look for some good deep house chill music on YouTube music a' [music]: 
- 'Search YouTube Music for some deep, chill house music and pl' [music]: 
- 'what song is playing in the kitchen?' [now_playing]: 
- "what's playing on the kitchen" [now_playing]: 
- "what's playing on the kitchen?" [now_playing]: 
- 'I hope you play Anime Pop Radio from Pandora.' [music]: 
- 'We play Justin Johnson Radio on Pandora.' [music]: 
- 'Like massive attack radio' [music]: 
- "We'll play Orbital Radio." [music]: 
