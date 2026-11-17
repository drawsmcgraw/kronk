# Feature: the "ask Kronk to" lead-in

**Built:** 2026-11-17, sprint item M19, branch `refactor` (build report
`docs/sprint/M19-build.md`; criteria `docs/sprint/M19-criteria.md`; plan:
`Kronk evaluation findings and implementation plan.md`, section M19).
Production once the sprint coordinator promotes it (`docs/sprint/PROMOTIONS.md`).

## What it does

**The rule for users: say it plainly for the fast path; start with "ask
Kronk to" and the model handles it.**

"Pause", "what song is this", "play some jazz" are caught by narrow routing
rules: five of them run their tool with no model at all
(`docs/features/direct-dispatch.md`), others pin the request to one agent.
That is fast and right for clean, common input. When a request is unusual,
or a rule keeps catching it the wrong way, starting the sentence with "ask
Kronk to" skips every rule and hands the rest of the sentence to the
coordinator, which has every tool. Drew picked the phrase (2026-11-11); it
works for any kind of request, not only music.

It guarantees where the request goes, not that the model gets it right. It
is the slow path by design: the coordinator, plus one specialist when it
delegates.

"Plainly" means one short sentence. Since sprint M14 a request with a
second sentence, or more than 20 words, goes to the coordinator too, lead-in
or not (`messy-transcript-gate.md`). The lead-in stays ahead of that gate:
"Ask Kronk to play jazz. That's all." is a lead-in, and the coordinator gets
"play jazz. That's all."

## How it works

### The rule

`lead_in` is the first rule of `routing.RULES` (16 rules since M19), route
`direct` (the coordinator). It matches, at the start of the utterance and
in any casing:

    [filler ...] <verb> <name> [to] <request>

- **verb**: `ask`; **name**: `Kronk`; **fillers**: `hey`, `okay`, `ok`,
  `please`, `can you`, `could you`, any number, each followed by optional
  commas or full stops. Leading spaces and punctuation are skipped (Whisper
  starts transcripts with a space).
- "to" is optional ("ask Kronk what's playing") and needs a word boundary
  ("ask Kronk tonight's forecast" keeps "tonight's").
- The name must end at a space or punctuation: "ask Kronk's opinion" is not
  a lead-in.
- Anchored: "what happens if I ask Kronk to pause" is not a lead-in and is
  routed by the rest of the table (`default`).
- With nothing after the lead-in ("Ask Kronk.", "ask Kronk to", "ask Kronk
  to please") it is not one either: the utterance goes to the coordinator as
  said, which answers it ("Ask Kronk to what? Please complete your request.").

`routing.decide()` returns `Decision(route="direct", rule="lead_in",
request=<the rest>)`. No rule after it is tried, so no regex shortcut and no
direct dispatch can take the request, Talkie's rule included.

### The words live in `config/lead_in.yaml`

`verbs`, `names` and `fillers` are lists in `config/lead_in.yaml`, read by
`orchestrator/config_loader.py` (schema `LeadIn`, strict, the M21 rules:
file and line on every error, all-or-nothing loads, built-in defaults equal
to the shipped file, `POST /config/reload`). Each entry is one or more words
of letters, digits, apostrophes or hyphens; entries are escaped into the
pattern, so no config value is a regex. Duplicates (ignoring case) are
errors. `routing.lead_in_pattern()` builds the pattern from the words in
effect on each call (cached per word set), so a reload is followed on the
next request.

**Mis-heard spellings of "Kronk" go into `names` only with captured data.**
On 2026-11-17 there was none: HA's pipeline debug history held 16 runs, all
typed; the production orchestrator's journald log from 2026-09-12 to
2026-11-17 held 143 distinct spoken transcripts, and the three that named
Kronk ("Ask Kronk to play Groove Armada Radio on Pandora.", "Ask Kronk to ask
the home agent to play French Dinner Radio from Pandora.", "Tell Kronk to
start an orbital radio…") were all transcribed "Kronk". See the build report.

### The pipeline

`main._run_pipeline` reads the decision before the model lock (as for
direct dispatch). With a lead-in:

- the coordinator is given the request without the lead-in, with its full
  tool menu, its usual weather and context blocks, and the conversation
  history;
- `origin.said` (the user's own words for the turn) becomes the request too.
  `play_music` forwards it to tool_service, whose `radio_name()` reads the
  station from the sentence and is anchored on the request verb: "Ask Kronk
  to play Groove Armada Radio on Pandora." would not match, "play Groove
  Armada Radio on Pandora." does. When the turn has no words of its own
  (`said` is None, `main._turn_said`) it stays None;
- the trace input, the `request` event and the web UI history keep the
  utterance as said.

### The mirror pin (M4b)

`update_magicmirror` runs only when `origin.said` contains "update the magic
mirror". With the lead-in, `said` is a suffix of the user's own last message,
and the lead-in cannot overlap the pinned sentence, so `said` holds it
exactly when the message does. "ask Kronk to update the magic mirror" is
therefore neither a way around the pin nor a way to break it: it goes to the
model (not the `mm_update` direct call), and the update runs if the model
calls the tool. "ask Kronk to update the mirror" reaches the tool and is
refused with the fixed sentence. Pinned by `tests/test_lead_in.py`
(`test_lead_in_mirror_update_runs_when_the_model_calls_it`,
`test_lead_in_does_not_widen_the_mirror_pin`,
`test_lead_in_from_an_earlier_turn_cannot_start_the_update`).

### Home Assistant

No HA change. HA's sentence-match debug (read-only, all three satellites)
shows no trigger and no strict built-in intent taking a lead-in sentence:
26 sentences, each "no local match", a partial match, or a fuzzy-only match
that the pipeline's strict local pass skips. They reach Kronk. The table is
in the build report, with the conversation trigger to add if that ever
changes.

## Telemetry

- `route_shortcut` event `{rule: "lead_in", route: "direct"}` (the same
  shape as every other non-direct shortcut), then the coordinator's usual
  `agent_round` / `tool_call` / `request_complete` under the same `rid`.
- `routing.decide` span: `kronk.rule = "lead_in"` and `kronk.lead_in_chars`
  (characters cut off). TraceQL: `{ span.kronk.rule = "lead_in" }`.
- The root span's `kronk.route` is `direct`, as for `default`.

## Latency

Two model hops at most: the coordinator, then the specialist it delegates
to (plus a third when it asks research first). Measured on dev
(`/dev/voice/api/chat`, five runs each): "ask Kronk to pause" median 3.04 s,
"ask Kronk what song is this" 3.04 s, "ask Kronk to find some deep house
music and play it" 7.40 s (17–19 s when the coordinator asks research
first). The same sentences before M19 rode `default` to the same
coordinator and took about as long (build report table). Without the
lead-in, "pause" is a direct call in 0.02 s.

## Gotchas

- **The model still decides.** On dev the coordinator answered "ask Kronk
  what song is this" with "I cannot identify a song" in 11 of 11 runs
  instead of delegating to `now_playing` (it did the same before M19 when
  the sentence rode `default`). "What's playing?" was delegated. The corpus
  row carries `xfail.live`.
- **"clear my history" is not a routing rule.** The transports check it
  before routing, so "ask Kronk to clear my history" still clears it. No
  model has a tool for it.
- **Only "ask".** "tell Kronk to …" is not a lead-in (the `music` rule
  already accepts "tell kronk to" as a prefix for music requests). Add a
  verb in `config/lead_in.yaml` only if Drew wants one.
- **No wake-word filler.** "Okay Nabu, ask Kronk to …" is not a lead-in:
  the wake word is not transcribed, so it is not in the list.
- **M11's parser layer** (`tests/test_voice_corpus.py`) reads a lead-in
  row as the orchestrator hands it on: without "ask Kronk to", which is
  what origin.said carries. A request left with no request verb ("find some
  deep house music and play it") is declined by the parser, by design: the
  lead-in sends it to the model.
- **The Whisper prompt** (plan M19 step 4, adding "Kronk") is M13's and is
  not part of this change.

## Blog hooks

- A circuit breaker for a rules engine: one spoken phrase that turns every
  shortcut off, and why it is the first rule, not the last.
- Configurable words, not configurable regexes: escaping config values into
  one anchored pattern, and the five near-misses that stay out.
- Data before spellings: 143 transcripts searched before adding a single
  "mis-heard Kronk", and the answer was none.
