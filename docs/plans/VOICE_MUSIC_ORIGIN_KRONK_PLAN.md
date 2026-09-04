# Voice music: play on the device that asked (Kronk tier) — Plan

Status: **shipped 2026-09-04 (same day).** ROADMAP item 12. Follows
`VOICE_MUSIC_DEVICE_FIRST_PLAN.md` (HA tier, shipped) and
`MUSIC_PLAYERS_FROM_HA_PLAN.md` (players discovered from HA, the
`origin_area` slot). Verified live through the Assist pipeline as each
satellite: "put on some aerosmith" from the office → Kronk saw
`origin device=… area=Office` → "Playing Angel by Aerosmith on the
Office speaker", office playing, kitchen idle; "put on some jazz" from
the kitchen → kitchen targeted; no device → default. 44 tests across
`test_origin.py` / `test_music_players.py`; suite 464. Stamp installed
by storage edit (backup in the session scratchpad and
`.storage/core.config_entries` restored on rollback = delete the line).

**Two findings during verification** (distilled into the feature doc):
HA's own built-in `HassMediaSearchAndPlay` intent catches "play X [in
Y]" phrasings before either blueprint or Kronk and is already
area-aware — so there are three local tiers, not two. And MA's
`play_media` can return HTTP 500 *and still play* (the jazz run): the
tool reported failure while the kitchen played. Follow-up (ROADMAP
chores): on a 5xx, still poll for `playing` before declaring failure.

## Why

Fuzzy requests that miss the blueprint's grammar ("shuffle my YouTube
playlist anime bangers", "put on some jazz") fall through to Kronk, and
Kronk has no idea which satellite spoke — so they play on the default
(rid `c61f305a`, `6d5b3b50`: satellite 1 asked, the kitchen played).

## Mechanism (verified against HA source, 2026-09-04)

HA renders the Ollama conversation prompt as a Jinja template with
`llm_context` in scope (`conversation/chat_log.py`
`_async_expand_prompt_template`); `LLMContext.device_id` is the
satellite that heard the utterance. One appended line stamps the origin
onto the system message HA already sends every request:

```
[kronk-origin] device={{ llm_context.device_id or '' }} area={{ area_name(llm_context.device_id) if llm_context.device_id else '' }}
```

The Ollama conversation subentry's prompt is editable in the UI
(Reconfigure) or by the storage-edit procedure (HA stopped, backup,
edit `.storage/core.config_entries`, start). A template error there
would make HA answer every utterance "Sorry, I had a problem with my
template" — the line is dry-run through HA's template API first;
rollback is deleting the line.

## Design

- **Shim** (`orchestrator/main.py`): `_shim_context` keeps dropping
  system messages; `origin.from_messages()` reads the one tagged line
  into `Origin(device_id, area)` ("None"/"" → None).
- **Propagation** (`orchestrator/origin.py`): a request-scoped
  `ContextVar`, set by `_run_pipeline` for the duration of the run and
  reset on exit, so the coordinator → `ask_home` → home agent →
  `tools.execute` chain sees it without a new parameter at every hop.
  One producer, one consumer, three async layers apart — the explicit
  threading precedent (`error_style`) is consumed at every level; this
  value at exactly one leaf. Tests prove two concurrent runs keep their
  own origin and that the web UI / OpenAI paths carry none.
- **Tool** (`orchestrator/tools.py`): `play_music` adds `origin_device`
  and `origin_area` to every music request. The model never sees either.
- **tool_service**: the one HA template call also returns each player's
  `mass_player_type` and its MA device key (`up<mac>`), plus the origin
  device's key (device id validated as 32 hex before it enters the
  template). Resolution = the fork's order: named player (exact) →
  named room (exact, group-preferred) → substring name → substring room
  → **own device** → own room (group-preferred) → default.
- Latency: none added (60 chars on a prompt Kronk discards; the tool's
  one HA call already exists).

## Steps

1. tool_service rungs + tests; deploy; verify with the origin passed by
   hand (curl) — the device rung against satellite 1's device id.
2. `origin.py` + shim parse + tool payload + tests; deploy orchestrator,
   nginx restart, shim check.
3. HA stamp line: dry-run, then storage edit + restart (operator
   authorized execution 2026-09-04 "execute Tier 2. go").
4. Verify by text through `assist_pipeline/run` as each satellite with
   fuzzy utterances (must miss the blueprint): office → satellite 1;
   kitchen → Voice PE; "…in the office" from the kitchen → office; web
   UI → default. Operator: real "Okay Nabu" runs.
5. `pipeline_bench.sh origin-pre` (before) / `origin-post` (after) — the
   change touches the request path. Clears chat history.
6. Docs: feature doc, ROADMAP 12 → Shipped, memory.

## Out of scope

Whisper's tense drift ("Played the album…") — in front of both tiers;
next.
