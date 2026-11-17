# M14 pass criteria: the messy-transcript gate (gate only)

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M14.
Tighter finished-speaking detection", step 1 only; decided by Drew
2026-11-11) and the items promoted since (M7's rules table and direct
dispatch, M19's lead-in rule, M2's voice signal, M21's `config/`). Steps 2
and 3 (the puck setting trial) wait for two weeks of M12 data and are out
of scope; no satellite setting is changed. The tester checks against this
file, not against the build report.

## What the gate is

Drew's decision: messy input goes to the model; rules handle clean input
only; no general rule trims trailing speech from a transcript. So one
gate, in one place: a transcript with more than one sentence, or longer
than a set word count, skips the rule-based fast paths and goes to the
coordinator, which extracts the request.

## Criteria

### A. The gate

1. One function decides "messy" from the user's sentence for the turn:
   more than a configured number of sentences, or more than a configured
   number of words. It is the only place the decision is made.
2. The thresholds live in `config/` (an existing file or a new one, added
   through the M21 loader with schema, comments on every key, built-in
   defaults equal to the file, pins and docs). A threshold can switch its
   half of the gate off.
3. How sentences are counted is stated and tested, including what the
   plan warns about: Whisper's punctuation is unreliable and names
   contain full stops. Abbreviations and initials inside a request
   ("Mr. Brightside", "St. Vincent", "R.E.M.", "Dr. Dre", "U2.", "No. 5",
   "a.m."), decimals and times ("98.6", "7.30"), an ellipsis, and a
   single trailing full stop, question mark or exclamation mark do not
   make a clean one-sentence request messy. The report shows what the
   counter does with every transcript in the corpus that contains a full
   stop, question mark or exclamation mark.
4. The default thresholds are set from the data that exists today (the
   voice corpus, `routing_eval.jsonl`, incident docs, `voice_tracer`
   data and HA's pipeline debug history where present), not guessed: the
   report shows the distribution of sentence count and word count over
   that data, where the thresholds sit in it, and which real utterances
   fall on each side. The plan says to set them from M12 data; M12 has
   not run yet, so the report says the defaults are provisional and
   names the M12 query that should replace them.
5. A messy transcript skips the fast paths: no regex shortcut that
   reads the request as a clean command, and no M7 direct dispatch. It
   goes to the coordinator with its full tool menu and the whole
   transcript. Which rules count as fast paths, and which still apply to
   a messy transcript and why (for example `talkie_explicit`, typed URL
   rules, the M19 lead-in), is a table in the report, decided by this
   test: a rule stays ahead of the gate only if it does not depend on
   the transcript being a single clean command.
6. The plan's motivating case behaves as intended on dev: "Play Anime Pop
   Radio. That's probably about it. Everything is pretty wet." goes to
   the coordinator, which extracts the request; the report gives what
   happened in ten runs (dry-run on) and how that compares with the path
   it took before. The existing `music_resolve.radio_name()` behaviour
   that stops at a full stop is kept and still tested.
7. A clean transcript is unaffected: for every utterance in the M7
   routing pin that the gate does not call messy, `(route, rule)` and the
   direct call are identical before and after. The report lists every
   pinned utterance whose outcome changes because it is messy, each with
   old and new outcome; none of them is a clean single command.
8. The mirror pin (M4b) holds with the gate in place: a messy transcript
   containing the pinned sentence can reach the update only through the
   model calling the tool, and a messy transcript without it cannot.
9. The gate applies on every mount the same way (typed and spoken), or
   only on voice; the report says which and why, and it is keyed on M2's
   `origin.voice` if it is voice-only.
10. The route is visible: an event and span attribute say the gate fired
    and why (sentence count or word count, with the numbers), so its use
    can be counted and the thresholds tuned from M12 data.
11. No new special-case rule and no trimming of transcripts anywhere.

### B. Corpus, regression, safety

12. Corpus rows are added or updated for messy transcripts (the plan's
    case, one over the word limit, the look-alikes of criterion 3 as
    clean rows); layer one asserts the gate; no row is weakened.
13. Latency stated up front (tenet 12): a messy transcript takes the
    model path, by design the slow one; a clean transcript pays nothing
    measurable (the gate is a function call). Measured on dev.
14. `./scripts/run_tests.sh` passes: at least `3503 passed, 2 skipped,
    23 xfailed` plus the new tests. New tests do not reach the network.
15. The voice replay on the dev path matches the M10 baseline row by row
    apart from the known flapping rows and rows the gate now sends to the
    coordinator, each listed with its before and after.
16. No sound; dev only; production untouched; no HA or Music Assistant
    restart or configuration change; **no satellite setting changed**
    (`select.*finished_speaking*` on all three pucks reads `default`
    before and after); no command to the MagicMirror Pi.
17. Docs: the gate, its thresholds and how to tune them are in the
    routing or voice feature doc; the "rule for users" from M19 still
    reads correctly.

## Reports

Build: `docs/sprint/M14-build.md`. End to end: `docs/sprint/M14-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
