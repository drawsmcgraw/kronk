# Feature: the messy-transcript gate

**Built:** 2026-11-17, sprint item M14 step 1, branch `refactor` (build report
`docs/sprint/M14-build.md`; criteria `docs/sprint/M14-criteria.md`; plan:
`Kronk evaluation findings and implementation plan.md`, section M14).
Production once the sprint coordinator promotes it (`docs/sprint/PROMOTIONS.md`).
Steps 2 and 3 of M14 (the kitchen satellite's finished-speaking trial) wait
for two weeks of M12 data; no satellite setting was changed.

## What it does

A request with more than one sentence, or more than 20 words, skips the
rule-based fast paths and goes to the coordinator with the whole transcript.
The coordinator picks out the request and delegates.

Drew's decision (2026-11-11): messy input goes to the model; the rules handle
clean input only. No rule trims trailing speech from a transcript (Whisper's
punctuation is unreliable and names contain full stops), and none was added.
The gate only counts.

The case that started it (`docs/incidents/INVESTIGATION_2026-11-08_anime_pop_radio_hang.md`):
the kitchen satellite kept listening for 9.8 s and Whisper returned " Play
Anime Pop Radio.  That's probably about it. Everything is pretty wet.  I just
needed to make some space." The `music` rule took that as a music request and
handed all of it to the home agent. With the gate it is the coordinator's,
which asked `ask_home` for the station in 10 of 10 dev runs.

**For users:** say it plainly, in one short sentence, for the fast path.
Anything longer, or with a second sentence, is handled by the model: slower,
by design. "ask Kronk to …" still sends any request to the model
(`lead-in.md`).

## How it works

### Counting (`orchestrator/transcript_gate.py`)

`check(text, max_sentences, max_words)` is the only place "messy" is
decided. It returns a `Messy` (which half fired, the counts, the limits) or
`None`.

- **Words:** whitespace-separated tokens holding a letter or a digit.
- **Sentences:** one, plus one for each token that ends a sentence and has
  another word somewhere after it. A token ends a sentence when its last
  character (after closing quotes and brackets) is `.`, `?` or `!`, except:
  an ellipsis (`..`, `…`); initials and dotted abbreviations (`J.`,
  `R.E.M.`, `a.m.`); a word in `ABBREVIATIONS` (`Mr.`, `Mrs.`, `Ms.`, `Dr.`,
  `St.`, `Jr.`, `Sr.`, `Mt.`, `Ft.`, `feat.`, `vs.`, `vol.`, `op.`); `No.`
  before a number; and any mark followed by a word starting lowercase.
  A `.` with no space after it is inside a token (`98.6`, `7.30`, URLs).

The lowercase rule comes from the data, not from grammar. Whisper starts
every sentence with a capital, and the music blueprint appends its room
after the spoken full stop: "Play Hotel Costas Radio. in the Kitchen" is
one request. Ten such hand-offs reached production between 2026-09-14 and
2026-11-17. Without the rule the gate would have sent all ten to the
coordinator.

**What it cannot tell apart.** A name with `!` or `?` before a capital
("Godspeed You! Black Emperor" is two sentences). A sentence that ends on
initials or a listed abbreviation ("Set an alarm for 7 a.m. Thanks." is one).
One letter before a full stop is read as an initial ("Play Plan B. Thanks."
is one). A second sentence typed in lowercase ("pause. play jazz") is one.
Run-on speech with no punctuation is one sentence; only the word count sees
it. A full stop Whisper leaves out is not counted, and one it puts inside a
name is.

### The rule (`orchestrator/routing.py`)

`routing.messy(text)` reads the limits in effect and calls `check`. The
`messy` rule (route `direct`, the coordinator) sits after the five rules
that do not read the text as one clean command, and before every rule that
does:

| # | Rule | Route | Ahead of the gate? | Why |
|---|---|---|---|---|
| 1 | `lead_in` | direct | yes | Goes to the coordinator anyway; reads only its anchored opening words. A messy request after it goes whole, minus the lead-in. |
| 2 | `talkie_explicit` | talkie | yes | Talkie is named explicitly, anywhere; Talkie is not on the coordinator's menu, so behind the gate it would be unreachable in a long request. |
| 3 | `direct_override` | direct | yes | Goes to the coordinator anyway. |
| 4 | `music_url` | home | yes | A typed YouTube Music link is the request's object wherever it sits; speech never carries one. |
| 5 | `url` | research | yes | Same: a typed link. |
| 6 | **`messy`** | **direct** | — | The gate. |
| 7 | `search_phrase` | research | no | A phrase anywhere ("look up", "google") taken as the request's method; trailing speech can contain one. |
| 8 | `weather` | home | no | A topic phrase anywhere. |
| 9–12 | `playback`, `now_playing`, `rate`, `music_another` | home | no | Whole-utterance commands (anchored) with direct calls. |
| 13 | `taste` | home | no | Anchored at the start: reads the opening as the request. |
| 14 | `music` | home | no | Anchored at the start (the motivating case). |
| 15 | `mm_update` | home | no | The pinned sentence anywhere, with a direct call: "Update the magic mirror. And play some jazz." must not update without a model. |
| 16 | `mm_ops` | devops | no | A topic anywhere. |
| 17 | `default` | direct | — | Everything else. |

The test for each row: a rule stays ahead of the gate only if it does not
depend on the transcript being a single clean command.

The same gate also guards the transports' "clear my history" intercept,
which runs before routing and matches anywhere: "Play some jazz. Forget the
conversation we had." goes to the pipeline, not to "Done — fresh start."
(`routing.asks_to_clear_history`).

A messy request has no direct call and no `request` rewrite, so
`main._run_pipeline` treats it like `default`: the coordinator, with the
weather context and its full tool menu, gets the text as said, and
`origin.said` is the whole transcript. The M4b mirror pin still decides the
update: a messy request containing "update the magic mirror" can start it
only through the model calling the tool; one without the sentence is refused
(`mm_update_refused`).

### Every mount, typed and spoken

The gate reads only the text, so `/message`, `/api/chat`, `/v1` and
`/voice/api/chat` behave the same. Reasons: Drew's rule names no mount; the
shortcuts' precision argument ("the whole utterance is the command") fails
on a typed message with context as much as on speech (in `routing_eval.jsonl`
the nine multi-sentence `search_phrase` requests were all context plus a
request, such as "Convert the energy produced by the solar panels last week
into dollars. Look up the typical rates for my area", which research alone
cannot answer); HA's typed Assist reaches the voice mount too, so the mount
is not "spoken" either; and routing stays a pure function of the text, so
the routing pin, the corpus and `routing_eval.jsonl` measure what voice gets.
Cost: a typed multi-sentence research request takes one coordinator hop.

## Thresholds (`config/tuning.yaml`, `transcript_gate`)

```yaml
transcript_gate:
  max_sentences: 1     # more than this is messy; null switches this half off
  max_words: 20        # more than this is messy; null switches this half off
```

Schema `config_loader.TranscriptGate` (1–20 and 1–500, or null). Reload with
`scripts/config_reload.sh dev|prod`; the next request uses the new limits.

**Provisional.** Set on 2026-11-17 from the data that existed before M12:
the voice corpus, `routing_eval.jsonl`, 453 production requests since
2026-09-14 (orchestrator log), 30 captures in `voice_tracer` since
2026-11-08, and HA's pipeline debug (16 runs, all typed). The distributions
and the utterances on each side are in `docs/sprint/M14-build.md`. In short:

- Every multi-sentence request a fast-path rule took was trailing speech
  (the Anime Pop transcripts, and "Put on Portishead on the basement voice
  pe ma. That is all.") or a request spread over several sentences: context
  then the request, or a second question (11 in `routing_eval.jsonl`, all
  typed). No one-sentence request a fast-path rule took was
  over 19 words (typed; spoken, 16). So `max_sentences: 1` and
  `max_words: 20`.
- No captured transcript was a run-on without punctuation: every trailing
  speech case was punctuated, so the sentence half caught all of them. The
  word half is a backstop and catches nothing in today's data. At 20 words
  it would not have caught the incident transcript with its punctuation
  stripped (19 words).

**Replacing them from M12.** Once M12 has two weeks of transcripts
(`TELEMETRY_RECORD_TEXT=true` puts `voice.transcript` on `voice.run`):

1. Every capture, from Tempo:
   `{ resource.service.name = "kronk-voice-tracer" && name = "voice.run" && span.voice.transcript != nil } | select(span.voice.transcript, span.voice.handled_by)`,
   with `voice.stt`'s `voice.vad_start_ms` / `voice.vad_end_ms` for the
   capture length. Run each transcript through
   `transcript_gate.count_sentences` / `count_words`.
2. Every gate decision, from Tempo:
   `{ resource.service.name = "kronk-orchestrator" && span.kronk.rule = "messy" } | select(span.kronk.messy_reason, span.kronk.messy_sentences, span.kronk.messy_words)`,
   or the `route_shortcut` events with `"rule": "messy"` in the orchestrator log.
3. Set `max_words` just above the longest clean command that a fast-path
   rule should take, using M12's hand labels, and check that every request
   it sends to the model is in fact messy. Keep `max_sentences: 1` unless a
   clean command is commonly transcribed as two sentences.

## Telemetry

- `route_shortcut` event: `{rule: "messy", route: "direct", reason,
  sentences, words, max_sentences, max_words}`; `reason` is `sentences`,
  `words` or `sentences+words`.
- `routing.decide` span: `kronk.rule = "messy"` and `kronk.messy_reason`,
  `kronk.messy_sentences`, `kronk.messy_words`, `kronk.messy_max_sentences`,
  `kronk.messy_max_words` (a switched-off limit is left off the span).
- The root span's `kronk.route` is `direct`, as for `default`.

## Latency

A messy request takes the model path, the slow one by design: on dev the
motivating case went from 0.28–5.59 s (the `music` rule, home agent only) to
3.31–8.48 s (coordinator, then home). A clean request pays the gate's
function call: 2–7 µs in the dev container.

## Gotchas

- **The pin moved once, deliberately.** 83 of 7696 rows in
  `tests/fixtures/routing_pin.jsonl` changed to `direct/messy` (69 of them
  were `direct/default`, so only the rule name changed); every other row kept
  its `(route, rule)`. The frozen gate in `tests/test_routing_pin.py` uses
  the shipped limits: change a limit and the pin fails until it is updated
  on purpose.
- **A one-sentence run-on under 21 words is clean.** "Play Anime Pop Radio
  that's probably about it everything is pretty wet I just needed to make
  some space" (19 words) is the `music` rule.
- **The coordinator is not always better.** " Thumbs up!  I know but you
  missed the best part." was `default` before the gate too (the anchored
  `rate` rule never matched it); the coordinator called no tool in 0 of 6 dev
  runs and in production (corpus row `m14-thumbs-up-missed-best-part`,
  `xfail.live`). The gate decides where a request goes, not whether the model
  gets it right.
- **`radio_name()` is unchanged.** tool_service still cuts a station name at
  the full stop when it re-reads the sentence (Drew's decision), so the
  model path keeps the radio type when the model drops it.

## Blog hooks

- One gate instead of a trimming rule: why "cut everything after the first
  full stop" fails on "Mr. Brightside", "R.E.M." and "No. 5", and why
  counting is enough.
- The blueprint's room suffix: the data said "a full stop before a lowercase
  word is not a sentence", and grammar never would have.
- Deciding where a gate sits in a rules table with one question: does this
  rule depend on the input being one clean command?
