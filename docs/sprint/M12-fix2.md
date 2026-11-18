# M12 fix 2: the whole shopping-list item is masked

Sprint coordinator, 2026-11-18 (UTC), on top of `ce85ad4`. The second
end-to-end test (`docs/sprint/M12-e2e2.md`) failed M12 on one finding and
confirmed every other fix of round 1. This is the second fix round. The
coordinator made the change directly: one pattern in two files.

## Block 1: the item is recorded with the switch off

The mask stopped at a space, a quote or `#`, and exempted any item whose
first word is "clear". It now runs to the end of the value, and only the
exact path `/shopping_list/clear` (optionally followed by `?` or `#`) is
left alone. The same for the orchestrator's `/files/{name}`.

- `orchestrator/telemetry.py`, `tool_service/telemetry.py`: `_PATH_TEXT`.
  The helpers are applied to one span attribute or one log argument at a
  time (the URL or the path alone), so "to the end of the value" cannot eat
  the rest of a log line.
- `tests/test_m12_fix1_text.py`: 12 items (the tester's live and offline
  inputs: several words, encoded and decoded, a first word "clear", an
  apostrophe, a double quote, `#`, `?`, a slash, a newline) through
  `strip_url_text` and `mask_path` in both services; the fixed paths left
  alone; an upload name with spaces and quotes; and a tool_service server
  span, through the instrumentation, for a multi-word item with an
  apostrophe and for one starting with "clear".

Live on dev, text switch off by a scratch compose override, three requests
on `/dev/api/chat` (items not on the dev list: 404, nothing written):
"remove wumblecrest vashtikorn cream from the shopping list", "remove clear
dorvalexun wrap …", "remove krelbotun's snarfweedle …". Orchestrator log:
`HTTP Request: DELETE http://tool_service-dev:8103/shopping_list/<item>
"HTTP/1.1 404 Not Found"` three times; tool_service access log: `"DELETE
/shopping_list/<item> HTTP/1.1" 404 Not Found` three times; marker words in
either container's log: 0; Tempo, `http.target`, `http.url`, `url.path`,
`url.full` matching any of the five marker words: 0 traces; the three
server spans read `/shopping_list/<item>`. The dev pair was then put back
(`TELEMETRY_RECORD_TEXT=true`, healthy).

`./scripts/run_tests.sh`: `4743 passed, 3 skipped, 53 xfailed`.

## Rulings on the second test's other findings

- Must-know 1 (a stop while Tempo is unreachable loses the held runs, with
  no counter) and 2 (a run that ends during a websocket outage of nine
  minutes or more is never fetched; the same before M12): accepted, and
  `docs/features/voice-capture.md` now says so instead of "only on a hard
  kill". Logged in `PROMOTIONS.md`.
- Must-know 3 (`ha.call_service`'s status description carries HA's
  play-failure message): exception text, accepted (ruling F6).
- Must-know 7 (a flapping HA websocket makes the mark useless while it
  lasts; `cut_short` counts it): accepted, logged.

## Runbook corrections (supersede the same steps in `M12-fix1.md`)

- **H6, third check.** States can be two words ("Okay Nabu"), so the
  timestamp is the last field: `awk -v t=$(date -u -d '15 min ago' +%FT%T)
  '/^select\./ && $NF > t' $D/states-before.txt` → empty.
- **H8, before and after.** Compare everything but the timestamp:
  `diff <(sed 's/ [^ ]*$//' $D/states-before.txt) <(sed 's/ [^ ]*$//'
  $D/states-after.txt)`.
- **H7a.** The two checks read 0 for up to a minute after a spoken
  request; the rule is the step's own words, no spoken request in the last
  five minutes (read the production orchestrator's and HA's logs).
- **T2.** The deploy's own check sends "What time is it?" through
  production `/api/chat`; that is the first sentence recorded.
- Before a host shutdown or stopping the telemetry stack: wait five
  minutes after the last spoken request.
