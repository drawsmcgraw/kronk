# M9 fix 1: `news_brief` is not a read-only tool

Sprint coordinator, 2026-11-17 (UTC), on top of `e502b10`. The end-to-end
test (`docs/sprint/M9-e2e.md`) failed M9 on one finding, B1, and passed
every other criterion. This is the first fix round. The coordinator made the
change directly instead of launching a fix agent and a second test agent:
it removes one name from one set, and the tester had already shown that
each of the ten acting tools added to that set is caught by the existing
tests.

## Ruling

A news refresh counts as an action. It starts a model generation and
replaces the stored brief after the request has ended, so "I stopped" is
not true. B1 stands as a failure of criterion 6.

## Change

- `orchestrator/tools.py`: `news_brief` removed from `READ_ONLY_TOOLS`,
  with the reason in the comment. A voice request stopped at its deadline
  after calling `news_brief` (with or without `refresh`) now says "That's
  taking too long to confirm. I've already sent the request, so it may
  still go through." A plain read of the brief gets the same sentence; that
  is the safe side and a plain read answers in well under a second.
- `tests/test_m9_deadline.py`: `test_news_brief_is_not_read_only`;
  `news_brief` added to the acting tools in
  `test_deadline_sentence_by_what_was_sent`.

From the report's observations, documentation only:

- O1: `docs/features/model-locks.md` rollback section now says production
  runs from its release directory and how to change it.
- O2, O3: the `max_overtakes` comment in `config/tuning.yaml` says what 0
  does and that display requests reaching the limit together all go ahead.
- O6: the doc notes that a bare `deadline_s:` and an empty `MODEL_LOCK`
  are accepted silently.

Not changed, logged in `PROMOTIONS.md`: O4 (a streamed client that stops
reading holds the server lock, as it held the pipeline lock before), O5
(the "already sent" sentence can be spoken when the call had not left yet;
the safe side), O7 (40 of 114 mutants survive), O8 (two replay rows failed
once, 6 of 6 on rerun), O9, O10, O11.

## Verification

`./scripts/run_tests.sh`: `4517 passed, 3 skipped, 53 xfailed` (was 4516;
one new test). No live retest: the change cannot be reached on dev
(`NEWS_ENABLED=0` there) and alters no path the end-to-end test measured.
