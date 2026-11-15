# Kronk evaluation: findings and implementation plan

Nov 9, 2026 · @Drew

## How to use this document

This is a handoff to a Claude Code session running on the `kronk` host. It records an outside review of the repo at commit `233d7ca` and a plan for each change. Drew (the operator) reviewed every move between 2026-11-09 and 2026-11-15. His decisions are marked "Decided by Drew" or similar in each section, and they are binding. The sprint section near the end gives the order of work and how it is run. What is still undecided is listed in "Still open" at the end.

**What the review could and could not see.** The reviewer read the repo and docs and ran one thing: `ops.classify_readonly()` against sample commands. It had no access to the running box. Every claim about live state (HA settings, env files, what is bound where, current latency) is an inference from the repo and must be checked first.

**Ground rules for the receiving session**

- Read `CLAUDE.md` first and follow its tenets and docs lifecycle. Anything bigger than a day gets a plan doc in `docs/plans/` before build.
- Verify before acting. Each move below starts with a "Check first" list. If a check contradicts the finding, record that, tell Drew, and adjust the move.
- One variable at a time. Run `scripts/run_tests.sh` and the relevant bench before and after each change.
- HA may be restarted when needed; the household knows the system is under construction (Drew, 2026-11-11). Do not change Music Assistant settings, puck firmware, or anything on the MagicMirror Pi without Drew's go.
- The household and guests use this system daily. Build and test on the dev path (M17). Prefer changes that can be rolled back with one env edit or one `docker cp`.

**Stopping rule from Drew, 2026-11-11.** Do not keep adding special-case rules. Rules handle clean, common input. A new rule is added only when captured data (M12) shows the same failure several times. Everything else goes to the model, or to the "ask Kronk to" lead-in (M19). The longer-term direction is for the model to resolve ambiguity itself once it is fast enough.

**Who uses Kronk, per Drew (2026-11-09)**

- Voice users: the whole household plus guests. The satellites are two Home Assistant Voice PE pucks and one FutureProofHomes Satellite1, with a second Satellite1 to be deployed.
- Common voice failures: misheard speech, slow responses, and wrong actions. Not hangs.
- Priority: appliance and learning lab weighted evenly. Experiments have run on the live system; M17 separates them.
- Wanted in the next six months: smart-home control by voice, and spoken general Q&A and search.
- Also wanted: fuzzy music requests that keep working, a reliable way to hand a request straight to the model, a customizable voice, one place to see and edit prompts, and a display of what Kronk is doing.

## Findings summary

The foundation is sound for a music appliance. Three things work against the stated goals: the LLM sits in the path of commands that do not need it, music decisions are split across three systems, and nothing in the design accounts for guests.

**What holds up and should not be reworked**

- Structural guardrails over prompts: terminal tools, verifying playback before claiming success, and reading the user's sentence (`origin.said`) instead of trusting the model's arguments.
- Coordinator-default routing. A shortcut miss costs one hop, never a wrong lane.
- HA as device broker, with Kronk as the brain behind an Ollama-compatible shim.
- Origin stamping, so music plays where it was asked and the model never sees the device.
- Operations discipline: incident docs, hash-pinned dependencies, canaries, benchmarks before model swaps, and traces that join HA's voice run to Kronk's.

**What works against the goals**

| # | Finding | Evidence in repo | Hurts |
| --- | --- | --- | --- |
| F1 | Voice replies are buffered whole before any speech starts | `main.py` `_ollama_single_chunk_stream`, `_ollama_collect` | Speed of spoken Q&A |
| F2 | Regex-pinned commands still take an LLM round to pick a tool the regex already identified | `routing.py` `_PLAYBACK_RE`, `_NOW_PLAYING_RE`, `_RATE_RE` route to the `home` agent | Speed, wrong actions |
| F3 | One global lock serialises every request from every puck and the web UI | `main.py` `_llm_lock` | Speed with several users |
| F4 | Three systems decide music and must agree without sharing code | HA built-in intents, `ha/blueprints/mass_assist_kronk.yaml` (12 changes), `routing.py` plus `tool_service` resolver | Wrong actions |
| F5 | Speech recognition has no vocabulary bias | `systemd/wyoming-whisper.service` has no initial prompt | Misheard |
| F6 | Finished-speaking detection left at `default` | `INVESTIGATION_2026-11-08_anime_pop_radio_hang.md`, fix 3 not applied | Misheard, wrong actions |
| F7 | Real utterances are not captured | `TELEMETRY_RECORD_TEXT` defaults to `false` in compose | Cannot measure accuracy |
| F8 | Health and finance agents are unused, yet any voice at any puck can reach them | No transport condition in `agent_tool_defs()` or `routing.py` | Guests |
| F9 | Web UI on port 80 has no authentication and serves `/finances` and `/health` | `nginx/nginx.conf` | Guests |
| F10 | Voice errors render in debug style | Compose sets `ERROR_STYLE` only; `ERROR_STYLE_VOICE` unset | Guests |
| F11 | The read-only exec classifier can be bypassed | `tool_service/ops.py`: `env` is in `_SAFE` | Safety |
| F12 | The devops agent holds web tools and `remote_exec` together | `agents.py` devops `tool_names` | Safety |
| F13 | `fetch_url` will fetch private addresses | `tool_service/main.py` `/fetch` has no address check | Safety |
| F14 | Kronk cannot control HA devices | Shim ignores tools; `home` agent has no entity tools | Smart-home goal |
| F15 | One copy of everything | ROADMAP "Backups" is still Next | Appliance |
| F16 | Three interacting upgrade debts | HA pinned `2026.5.4`; MA image `2.11.0b5-kronk.9`; blueprint fork | Appliance |
| F17 | Experiments run on the household's live path | No dev pipeline exists | Appliance |

Two smaller notes. Wyoming STT and TTS listen on `0.0.0.0` (ports 10300 and 10200) although HA reaches them on localhost. And "all local" depends on two cloud accounts, Pandora and YouTube Music; only the NAS library is local.

## Work order

The status of every move after review with Drew. The order of work is in the sprint section, not in this table.

| Move | What | Status |
| --- | --- | --- |
| M1 | Remove the health and finance agents, their services, `retire_calc`, and the stored data | Approved; in the sprint |
| M1b | Remove the hot tub feature | Approved; in the sprint |
| M2 | Friendly errors on voice | Approved as a small chore; in the sprint |
| M3 | Harden the read-only exec classifier | In the sprint |
| M4 | Take the web tools away from the devops agent | Decided (option A); in the sprint |
| M5 | Block private addresses in `fetch_url`; add `http_check` | Approved; in the sprint |
| M6 | LAN exposure | Audit only in the sprint. The web UI and Grafana stay open by Drew's choice. Any change needs his go |
| M7 | Direct dispatch for regex-pinned commands | Approved; in the sprint |
| M8 | Sentence-level streaming on `/voice` | Approved; in the sprint |
| M9 | Narrow `_llm_lock` | In the sprint, under the no-slowdown constraint |
| M10 | Voice regression corpus | In the sprint, second item |
| M11 | One decision-maker for music, in `tool_service` | Greenlit as a full consolidation. Built and shadowed in the sprint; cutover after Drew reviews the data |
| M12 | Capture transcripts and two weeks of audio | Approved; in the sprint |
| M13 | Whisper prompt from household vocabulary | Approved. Built in the sprint; off for production until measured |
| M14 | Messy-transcript gate; finished-speaking trial | Gate in the sprint. Puck trial after M12 data |
| M15 | Device control through Kronk; first project is the outdoor lights over Matter | After the sprint |
| M16 | Config-only monthly backup with a tested restore runbook | After the sprint |
| M17 | Dev path on the Hey Jarvis wake word | Approved; first sprint item |
| M18 | Upgrade cadence | Approved. Tooling in the sprint; major upgrades need Drew's greenlight |
| M19 | "Ask Kronk to" lead-in | Approved; in the sprint |
| M20 | TTS trial with a go/no-go | Limits confirmed. Bench in the sprint; listening test waits for Drew's clips |
| M21 | `config/` directory and `/settings` page | Approved, both steps; in the sprint |
| M22 | Kronk on the MagicMirror | Design only; not in the sprint |

## Phase 1: guest exposure and safety

Six small changes. M1 to M5 are code or env only and can each ship alone.

### M1. Remove the health and finance features

Drew's decision, 2026-11-09: he does not use the health or finance agents, so they come out. That also removes the guest exposure in F8 and the `/finances` and `/health` pages in F9.

**Scope settled by Drew, 2026-11-14:** `finance_service`, `health_service` and `retire_calc` all go as well, removed the same way as the hot tub feature (M1b). Remove the two roadmap entries ("Financial expert, phase 2" and "Health RAG completion") and the `/retire/` page. Delete the stored health and financial data; Drew does not want it archived.

**Check first**

- Usage, to confirm nothing depends on them: Tempo counts for `ask_health`, `ask_finance`, `query_health` and `query_finances` over 90 days, and nginx access-log hits on `/finances`, `/health`, `/api/finance/`, `/api/health/` and `/retire/`.
- Scope, with Drew. Settled 2026-11-14: everything goes, including the `finance_service` and `health_service` containers, `retire_calc` and its `/retire/` page, the stored data, and two roadmap entries ("Financial expert, phase 2" and "Health RAG completion"). Drew confirmed that the data is deleted and the roadmap entries are removed.

**Implement**

1. `orchestrator/agents.py`: delete the `health` and `finance` entries from `AGENTS` and the `HEALTH_AGENT_MODEL` and `FINANCE_AGENT_MODEL` settings. The `ask_*` tools are derived from `AGENTS`, so they disappear with the entries. Edit the coordinator prompt, which names health and finances as delegable personal data.
2. `orchestrator/tools.py`: remove `query_health`, `query_finances`, `query_bloodwork` and `search_health_data` (definitions, handlers, dispatch) and their lines in `_tool_narration`.
3. `orchestrator/main.py` and `static/`: remove the `/health` and `/finances` pages and the upload forwarding to `health_service` (`_forward_to_health` and the Garmin, zip and LabCorp detectors).
4. Compose and nginx: remove `health_service`, `finance_service` and `retire_calc`, their `depends_on` entries and env vars, and the `/api/health/`, `/api/finance/`, `/probe/health`, `/probe/finance`, `/retire/` and `/probe/retire` locations. Update `services.html`.
5. Tests and benches: delete the health and finance test files, fix references in `test_agentic_loop.py`, and drop health and finance rows from `docs/bench/routing_eval.jsonl` and the coordinator bench probes.
6. Docs: README inventory, `kronk-context.md`, `ROADMAP.md`, `TECH_DEBT.md` (the Garmin and Withings stub entry). Move the plan and feature docs to history; do not delete them.
7. Data: delete `./data/finance`, `./data/retire`, the health database and the vector store. Drew gave this instruction explicitly on 2026-11-14. List the paths and sizes in the build report before deleting, so the report records exactly what was removed.

**Verify**

- `scripts/run_tests.sh`, then the coordinator bench. A shorter tool menu should not hurt delegation accuracy; confirm it.
- Add a bench probe: "how did I sleep last night" and "what's my net worth" must get a plain "I don't have that" from the coordinator, not an invented figure. With no specialist to delegate to, this is the new failure to watch for.

**What remains of the voice gate**

`devops` and `coding` are still reachable from any puck. After M3 and M4 the risk is low. If Drew wants them off voice, add a request-scoped ContextVar beside `origin.current`, set it in `_ollama_chat_impl` when `profile == "speech"` from an env list, and filter `agent_tool_defs()` with it.

### M1b. Remove the hot tub feature

Drew's decision, 2026-11-14: the hot tub monitor never worked, so the feature is removed from Kronk. `TECH_DEBT.md` (HOTTUB-01) already records it as non-functional.

1. Host: stop, disable and delete `kronk-hottub-monitor.service`. The October review found it enabled and running, with no copy in `systemd/`.
2. Repo: delete `hottub_monitor/`.
3. Orchestrator: remove the `query_hottub` tool definition, handler and narration line, and take it out of the `home` agent's `tool_names`, prompt, description and routing hint.
4. `tool_service`: remove the `/hottub` route and any `geckolib` dependency, then regenerate the lockfile.
5. Docs and UI: README, `kronk-context.md`, `TECH_DEBT.md`, `services.html`, and any hot tub rows in `docs/bench/routing_eval.jsonl` and the bench scripts.
6. Verify with `scripts/run_tests.sh`, and confirm "is the hot tub online" now gets a plain "I can't check that" from the coordinator.

### M2. Friendly errors on voice

`errors.style_for()` already supports `ERROR_STYLE_VOICE`; compose never sets it, so voice runs in debug style. Drew confirmed on 2026-11-09 that the running container has ERROR\_STYLE=debug. Low priority: debug wording only reaches a speaker on a routing failure, a pipeline crash, or an unreachable model server. Music failures, specialist failures reworded by the coordinator, and HA's own errors already sound plain. Treat this as a two-minute chore.

**Check first:** `grep ERROR_STYLE .env docker-compose.yml` on the box, already checked by Drew (debug); re-check only in case `.env` already overrides it.

**Implement:** `docker-compose.yml` already carries the line commented out as `# - ERROR_STYLE_VOICE=friendly` in the orchestrator environment. Uncomment it, then `docker compose up -d orchestrator` and `docker compose restart nginx` (stale-upstream gotcha).

**Notes:** logs and traces keep full detail. Music failure sentences are spoken in both styles, so those stay specific. The setting applies to the whole `shim` transport, which includes `/v1` and `/api/chat`; if Drew wants debug there, key the override on the `/voice` mount instead of the transport. `errors.py` says the voice smoke test runs in debug, so check that test still passes.

### M3. Harden the read-only exec classifier

`tool_service/ops.py` `classify_readonly()` returned `(True, 'read-only')` for all of these when run against the repo code:

| Command | Why it is not read-only |
| --- | --- |
| `env rm -rf /home/pi/MagicMirror` | `env` runs any program |
| `env sh -c "id"` | Same |
| `sort -o /home/pi/.bashrc /tmp/x` | Writes a file |
| `uniq /etc/hostname /home/pi/out` | Second argument is an output file |
| `git log --output=/home/pi/x` | Writes a file |
| `ip link set eth0 down` | `link` is allowed with no check on what follows |
| `cat /home/pi/.ssh/id_rsa`, `printenv` | Read-only, but sends secrets into the model context and traces |
| `tail -f /var/log/syslog`, `pm2 logs` | Never returns; burns the 30 s timeout |

**Check first**

- Read `/data/ops_audit.log` for past use of these forms.
- Check how the registry's `"sudo": true` field is used; `ops_exec` does not appear to read it.

**Implement**

1. Remove `env` and `printenv` from `_SAFE`.
2. Replace the flat allowlist check with per-program rules: forbidden flags (`sort -o/--output`, `git --output`, `date -s/--set`, `tail`/`journalctl` `-f/--follow`, `file -C`), positional limits (`uniq` one, `hostname` zero), and for `ip` allow only a bare object or `show`/`list` after it. Require `pm2 logs --nostream`.
3. Add a path deny list for file-reading programs: `.ssh/`, `id_*`, `.env`, `shadow`, key and token files. Drew's call on how wide.
4. Add every row above to the refusal table in `tests/test_ops_exec.py`, plus the allowed forms that must keep working.

**Tradeoff:** the three known classifier quirks on the roadmap (`git -C`, quoted pipes, `journalctl --user`) are about being too strict. Fix those in the same pass so the rule set is rewritten once.

### M4. Separate web tools from remote exec

The devops agent holds `web_search`, `fetch_url` and `remote_exec` in one loop. A fetched page can carry instructions, and the next round can run a command. With M3 in place the damage is limited to reads, but phase B of the mirror plan adds mutations.

**Decided by Drew, 2026-11-09: option A.** Drop `web_search` and `fetch_url` from the devops agent's `tool_names` and remove the web-lookup sentence from its prompt. When documentation is needed, the coordinator calls `ask_research` and then `ask_devops`, so web text and command execution never share a loop.

Not chosen: keeping the web tools with a per-turn block on `remote_exec` after any web result.

Drew's stated goal for this agent is read-only investigation now and remediation later. Keep this separation when remediation is added.

**Check first:** whether `scripts/devops_model_bench.py` cases depend on web tools. Re-run that bench after the change.

**Residual risk:** command output is also untrusted text (a log line can carry instructions). Keep the confirmation gate planned for phase B regardless.

### M5. Block private addresses in fetch\_url

`/fetch` in `tool_service/main.py` fetches any URL and follows redirects. A page or a user sentence can point the research agent at the solar bridge, the router, or other containers on the `kronk` network.

**Implement**

1. Before fetching, resolve the hostname and reject loopback, RFC 1918, link-local, CGNAT and unspecified addresses, plus single-label hostnames (Docker service names).
2. Turn off `follow_redirects` and follow by hand, up to five hops, applying the same check at each.
3. Return the existing `{"ok": false, "error": ...}` shape so the agent treats it as a normal tool failure.
4. Tests: `127.0.0.1`, `10.0.0.1`, `192.168.1.1`, `169.254.169.254`, `http://finance_service:8005/`, and a public URL that redirects to a private one.

**Limit:** check-then-fetch leaves a DNS rebinding window. Accept it; this is a home LAN, not a hostile multi-tenant host.

**Side note:** the README says `fetch_url` uses `verify=False`. The `/fetch` code at `233d7ca` does not. Correct the README.

**Addition, agreed with Drew 2026-11-09: an `http_check` tool for troubleshooting.** M5 removes Kronk's ability to read LAN pages, and `remote_exec` cannot run on the Kronk host by design. Drew wants a way to ask "is this reachable from Kronk?" without reopening either.

1. `tool_service`: `GET /http_check?url=...`. One GET with a five-second timeout. Return status code, elapsed time, the redirect chain, the final URL, and a few headers (`server`, `content-type`, `content-length`, `location`). On failure return the cause: DNS, connection refused, timeout, TLS error. Never return the body.
2. This endpoint is exempt from the M5 address block; reaching private hosts is its purpose.
3. Give the tool to the devops agent only. After M4 that agent has no web tools, so no fetched page can steer it.
4. Write every call to the ops audit log, as `ops_exec` does.
5. No custom headers, methods, bodies or credentials.

**Limit to raise with Drew:** some LAN devices change state on a plain GET (a relay toggled by URL). A host allow-list beside `ops/hosts.json` closes that; without one, the tool can touch any address on the LAN.

### M6. Close LAN exposure

**Decided by Drew, 2026-11-09: the web UI on port 80 stays open on purpose.** Skip the nginx row in the table below. Do not add a login or an allow-list.

**Do not bind LiteLLM to loopback.** Bridge containers reach it through the Docker host gateway, so a `127.0.0.1` bind breaks the orchestrator. Use a key or a firewall rule. Whisper and Piper are different: their only outside client is HA on host networking, so loopback is safe for both once HA's Wyoming entries are confirmed to use `localhost`.

**Gate, required by Drew: check actual conditions on the box before making any M6 change.** Everything in this section was inferred from the repo. Before editing a bind address, port mapping or firewall rule:

1. Record what is listening and on which address (`ss -tlnp`), and compare it with the table below.
2. For each listener, find its real clients: which containers, which host processes, and which LAN devices connect to it. Check HA's integration entries for the host each one uses.
3. Write the findings down and show Drew. Change only what the findings support, one listener at a time, and confirm voice still works after each.

**Check first (these decide whether M6 matters)**

- Is guest Wi-Fi isolated from the VLAN `kronk` is on? If yes, this drops to low priority.
- `ss -tlnp` on the host: list everything bound to `0.0.0.0`.
- In HA, confirm the Wyoming and Ollama integrations point at `localhost`.

**Likely findings and fixes**

| Listener | Problem | Fix |
| --- | --- | --- |
| nginx :80 | `/finances`, `/health`, `/files`, `/retire/`, and chat with `ask_devops` open to the LAN | `auth_basic` on everything except `/voice/`, `/api/chat`, `/api/tags`, `/api/version`, `/api/show` and `/probe/`; or allow-list source addresses |
| LiteLLM :8002 | Host networking, binds `0.0.0.0`: an open model endpoint | Set a LiteLLM master key, or a host firewall rule limiting 8002 to loopback and the Docker bridge |
| whisper :10300 | Unit binds `tcp://0.0.0.0:10300` | Bind `127.0.0.1` (HA is on host networking) |
| piper :10200 | Compose publishes `10200:10200` | Publish `127.0.0.1:10200:10200` |
| Grafana :3000 | Anonymous viewers; traces will hold transcripts once M12 is on | No change. Drew decided on 2026-11-11 that Grafana stays viewable without a login |

**In the first sprint, M6 is an audit only.** Record what is listening and who connects, show Drew, and change nothing without his go. The web UI and Grafana stay open by his decision, so the candidates for change are LiteLLM and the two Wyoming ports.

## Phase 2: latency

Three moves, in this order. M7 removes the model from commands that do not need it. M8 shortens time to first audio for spoken answers. M9 stops one user's request from blocking another's.

**Measure first, for all three.** Pull 30 days from Tempo: request count and p50/p95 duration by `routing.decide` rule, and by transport `voice`. That sizes each win and gives the before numbers.

### M7. Direct dispatch for regex-pinned commands

When an anchored regex matches the whole utterance, the tool and its arguments are already known. Today the request still goes to the `home` agent, where the model spends a round choosing the same tool. The docs record the model dropping or changing arguments on such calls.

**Scope: rules where the match fully determines the call**

| Rule | Tool | Arguments from the match |
| --- | --- | --- |
| `playback` | `control_music` | stop, pause, shut up → `pause`; resume, unpause → `resume`; skip, next → `next`; louder, turn it up, volume up → `volume_up`; quieter, turn it down, volume down → `volume_down` |
| `now_playing` | `now_playing` | none |
| `rate` | `rate_music` | like, love, thumbs up, upvote, more like this → `up`; hate, dislike, don't like, never play this again, thumbs down, downvote, less like this → `down` |
| `music_another` | `play_music` | `kind: another` |
| `mm_update` | `update_magicmirror` | none |

Leave `music`, `taste`, `weather`, `search_phrase` and `url` on the model path for now. Plain "play ..." requests are handled in M11.

**Implement**

1. In `routing.py`, have each rule in scope return a direct call: `(route, rule, direct)` where `direct` is `(tool_name, args)` or `None`. Use named groups in the regexes so the action comes from the match, not a second parse. This fits review item O12 (an ordered rules table).
2. In `_run_pipeline`, before phase 2: if `direct` is set, call `tools.execute(name, args)`, pass the result through `agents._terminal_speech(result, error_style, tool=name)`, yield it as one token, and return. The handlers already read `origin.current`, so the speaker that heard the request is still the target.
3. Keep the telemetry shape: a `tool` span under the pipeline root and a `route_shortcut` event with `direct=true`, so the Grafana dashboards still count these.
4. Run this branch outside `_llm_lock` (see M9). It makes no model call.
5. Fallback: if the direct call raises, speak the failure. Do not fall through to the model; a second attempt could repeat a half-applied action.

**Verify**

- Extend `tests/test_routing_shortcuts.py` with an utterance → `(tool, args)` table. Include the negatives already documented: "stop the timer", "pause my subscription", "what is the next holiday".
- A pipeline test that patches `llm.stream` to fail and asserts "pause" still succeeds.
- Live: `assist_pipeline/run` text runs for "pause", "what song is this", "thumbs up". Expect about one second where the trace shows several today.

**Tradeoff:** requests with a named room ("pause the music in the office") do not match the anchored regex and stay on the model path. Extending the regexes with a room slot is possible later, but the blueprint's experience with greedy room slots ("The Girl with the Sun in Her Head") says to keep them out of this pass.

### M8. Sentence-level streaming on the voice mount

`/voice/api/chat` collects the full reply, runs `render.to_speech`, and sends one chunk. The comment there says TTS needs the whole text first. Home Assistant can now begin speech while an LLM reply is still arriving, so the buffer adds the full generation time to every spoken answer.

**Decided by Drew, 2026-11-14:** yes to streamed speech. The puck should start speaking as sentences arrive instead of waiting for the whole reply.

**Check first**

- Which HA release added streamed TTS for conversation agents, and whether `2026.5.4` and the Voice PE firmware in use support it. Look for `tts_start_streaming` in a pipeline debug run.
- Whether `rhasspy/wyoming-piper:2.2.2` needs a flag to stream (`--help`). The compose command passes none.
- How often `retract` events fire and how many characters they remove (Tempo, and `docs/plans/TOOL_ROUND_RETRACT_PLAN.md`). Streaming cannot unsay text.
- HA's Ollama client timeout, and whether it sends `stream: true`.

**Implement**

1. Add a sentence buffer between `_kronk_pipeline_tokens` and the NDJSON writer for `profile == "speech"`. Accumulate tokens; when a sentence or line boundary arrives, run `to_speech` on the completed part and emit it as one Ollama content chunk.
2. Hold one sentence back. Release sentence N only when sentence N+1 completes or the stream ends. A tool-round preamble is usually one sentence followed by a tool call, so the hold lets a `retract` remove it before anything is sent.
3. Treat markdown blocks as units: do not release inside a table, list or code fence until the block ends. `to_speech` works on whole lines, so line-aligned chunks keep it correct.
4. Keep the existing buffered path behind an env switch (`VOICE_STREAM=off`) for rollback. Terminal-tool results are already one sentence and need no change.
5. Add a speech-profile line to the coordinator's `system_extra`: the reply will be spoken, so answer in one to three plain sentences with no lists or markdown. Shorter replies cut generation time whether or not streaming works. Place it at the end of the system text so the cached prompt prefix is unchanged.

**Verify**

- `tests/test_render.py` pins the single-chunk shape; add cases for multi-chunk output, a retract inside the held sentence, and a table.
- Live, on the dev pipeline (M17) first: a long answer ("explain how a heat pump works") with timestamps for wake, first audio and last audio, before and after.

**Tradeoffs**

- Community reports show HA's streamed TTS can be unreliable depending on versions. If first audio does not move, keep step 5 and the switch, and drop the rest.
- A retract longer than one sentence will leak a spoken preamble. If the trace data shows that happens, widen the hold to two sentences or stream only rounds that follow a tool result.

### M9. Narrow the global lock

`_llm_lock` wraps the whole pipeline, including tool waits such as the eight-second playback verify and web fetches. `TECH_DEBT.md` records it as a deliberate choice for a single operator. That assumption no longer holds with a household and guests on several pucks.

**Hard constraint from Drew, 2026-11-09: no performance penalty.** A single request with nobody else using Kronk must be no slower after M9 than before. The known hazard is llama.cpp's prompt cache: two conversations alternating on one slot evict each other's cache and reprocess their full prompts. Measure single-request latency and prompt-cache reuse before and after every step below, with `scripts/pipeline_bench.sh` and the two-client bench. If a step slows the single-request case or drops cache reuse, revert that step. Steps 1 and 4 cannot cause this and are safe to keep on their own.

**Check first**

- `curl 127.0.0.1:11438/props` and `/slots`: how many slots each llama.cpp server runs and how context is divided.
- Everything that relies on the lock for safety. Known: the comment on `sessions._cache`. `telemetry._current_root` is already a ContextVar.
- Whether the handler stops work when HA disconnects. `_ollama_collect` is awaited inside the handler, and nothing checks for a dropped client.

**Implement, one step per change**

1. Move work that needs no model outside the lock: the M7 branch, `routing.classify`, and the `weather_context` fetch.
2. Replace the pipeline-wide lock with one lock per model server, taken inside `llm.stream` for the duration of one generation. A devops run on devstral then no longer blocks a voice request on gemma, and tool waits hold nothing.
3. Make the per-model lock a priority queue: voice ahead of web UI. No preemption; a running generation finishes.
4. Add a total deadline for voice requests, shorter than HA's client timeout, that ends the run with a spoken sentence. Cancel the pipeline task when the client disconnects.
5. Make `sessions.append` safe without the lock (a small per-session lock, or the `append_exchange()` from review item O14).

**Verify:** a two-client version of `scripts/pipeline_bench.sh`: a research query from the web UI and a voice weather query started one second later. Record the voice latency before and after each step.

**Tradeoff:** two conversations interleaving on one llama.cpp slot evict each other's prompt cache, so each turn pays for its full prompt again. With separate slots, each gets a share of the context window. Measure both; if the cache loss costs more than the queueing saved, stop after step 1 and step 4.

## Phase 3: one decision-maker for music

Most of the music incidents in `docs/incidents/` sit on a seam between three systems. Build the test corpus first (M10), then move the decision into one place (M11).

**How a spoken music request is decided today**

1. HA matches the blueprint's conversation triggers. Specific sentences ("play the album X") resolve the player in Jinja and call Music Assistant directly.
2. Loose and catch-all sentences, and named plays MA rejects, go to Kronk through `conversation.process`. That call cannot carry the device id, so the blueprint appends "in the Kitchen" to the words.
3. Kronk's `_MUSIC_RE` pins the text to the `home` agent. The model builds `play_music` arguments. `tool_service` then re-reads the original sentence because the model's arguments are unreliable, and resolves the player a second time in Python.
4. Anything the blueprint misses that HA's built-in intents can parse never reaches Kronk at all.

The player-resolution order exists twice (Jinja and `resolve_players()`), and `tests/test_music_players.py` exists only to keep them in step.

### M10. Voice regression corpus

Every incident fix so far was verified by hand. A replayable corpus makes M11 safe and gives M13 and M14 something to measure against.

**Check first**

- Whether the "voice smoke test" named in `orchestrator/errors.py` exists, and where.
- What HA keeps: pipeline debug runs hold transcripts, and `voice_tracer` already reads them.

**Implement**

1. Create `tests/voice_corpus.jsonl`. One row per utterance: the text as transcribed, the origin area, and the expected outcome (tier, tool, media type, name, target player).
2. Seed it from the incident docs. Include at least: "Play Anime Pop Radio. That's probably about it. Everything is pretty wet.", "Like massive attack radio", "Start an orbital radio station on YouTube Music", "play The Girl with the Sun in Her Head by Orbital", "shuffle my YouTube playlist anime bangers", "put on some jazz", "Stop!", the "ninety's two thousands" station, and the mishearings "Pasta" for pause and "What's up?" for stop.
3. Add `docs/bench/routing_eval.jsonl` rows that are music or playback.
4. Layer one, offline: a pytest that runs each row through `routing._classify_inner` and the M11 parser and asserts the expected call.
5. Layer two, live: `scripts/voice_replay.py` sends each row to HA's `assist_pipeline/run` with `start_stage: intent` as a chosen satellite. Add a dry-run switch to `tool_service` `/music` that resolves and returns what would play without calling `play_media`, so a replay is silent.
6. Rule going forward: every new incident adds its utterance to the corpus in the same commit as the fix.

### M11. Move the music decision into tool\_service

Target: HA forwards the sentence and the device, and `tool_service` decides in one place. The model stops building `play_music` arguments. It is kept for one narrow judgment it is good at.

**Requirement from Drew, 2026-11-09: fuzzy style requests must keep working.** Examples he gave: "play some deep house chill music" and "play italian music". A past failure played a song whose title matched the request words. A parser reads sentence structure only, and "play deep house" has the same structure as "play Portishead", so structure alone cannot decide. Accuracy on these requests ranks above removing the model.

**Decisions by Drew, 2026-11-11**

- **M11 is greenlit.** The agreed benefit is consolidation: one place decides, the seams between tiers go away, and the logic gets unit tests. Build M10 first.
- **Go full, not hybrid.** HA's music automation only forwards the sentence and device to `tool_service`; the blueprint's direct calls to Music Assistant are retired. Revert to hybrid only if measured latency turns out bad. This replaces the earlier 300 ms ceiling.
- **Provider policy.** Playlists, songs and albums prefer the NAS library. YouTube Music is the fallback when the NAS misses. Radio defaults to Pandora. A provider named in the sentence overrides the default ("start an Avalanches radio on YouTube Music"). Artist requests follow the same order, NAS first then YouTube Music (confirmed 2026-11-14). Drew may change the order later if it adds noticeable lag, so keep it a setting in the music policy file (M21) and measure it.
- **Remove the literal-play fallback.** When search returns no candidates, `/music` today passes the raw words to Music Assistant, which has played unrelated music. Fail plainly instead.

**Implementing the provider policy without a time penalty.** Apply it as a scoring preference over the candidates one Music Assistant search already returns, not as one search per provider in sequence. Check first whether `music_resolve.score()` uses the candidate's providers today. The preference must not let a poor NAS match beat a good YouTube Music match: prefer the NAS only among candidates that pass the match threshold.

**"Play" is not always music.** `_MUSIC_RE` excludes news, games, videos, movies and podcasts. The HA trigger or the `tool_service` parser needs the same exclusions, so "play the news" still reaches the coordinator's `news_brief`.

**Nothing in M11 is still open with Drew.** Shadow-mode results are reviewed with him before each cutover step.

**Target flow**

1. HA: one thin automation. Triggers on the request verbs (play, put on, listen to, shuffle, queue up, throw on) with a single wildcard slot. Action: `rest_command.kronk_music` with the sentence and `trigger.device_id`. Response: the returned `say` sentence.
2. `tool_service`: a new endpoint, for example `POST /music/say`, taking `{sentence, origin_device}`. It runs the decision order below, builds the existing `MusicRequest`, and calls the existing `play_music()`.
3. Orchestrator: the `music` route becomes an M7 direct dispatch to the same endpoint with `origin.said`, so typed and spoken requests share one path.

**Decision order inside tool\_service: style or named item?**

1. Marker words decide outright. "Some", "something", "music", "songs", "vibes" mean a style (`kind: genre`). "Album", "track", "song X by Y", "playlist", "radio", "station" mean a named item of that type.
2. Unmarked request ("play X"): check the genre list first. The list is built from `data/music_taste.yaml`, the learned taste profile, and Music Assistant's genre tags. A hit means style. A type word is the override: "play jazz" plays jazz, "play the album Jazz" plays the album. Confirmed by Drew 2026-11-11; this is also what `/music` does today.
3. Not a known genre: run the resolver as a named item, with the provider policy applied among candidates that pass the threshold. Set the threshold from the M10 corpus.
4. Retry across types (approved by Drew). If a typed request finds no strong match, look for the same name as an album, track, artist or playlist. Accept only a strong match, and say what was found ("Playing the album ..."). Check first whether `_resolve_named` already does this.
5. Try the other reading once (approved by Drew). A style request the genre list does not know ("play some Portishead") is retried as a name. A name with no strong match is retried as a style only if the genre list knows it.
6. Still unclear: ask the model one question, "is this a style or a named item?", with the output constrained to those two values. The model never writes arguments. Its answer selects the branch; the sentence still supplies the words.
7. Standing rule: a weak title match never beats a style reading.
8. If nothing resolves, say so plainly and name the closest match. Never pass the raw words to Music Assistant.

Deferred, not rejected: remembering the closest match for about 30 seconds so that "yes" or "play that" plays it.

**Design principle from Drew, 2026-11-11:** the model should be the one resolving these ambiguities; the rules above exist only because the model path is slow today. Build the order so the model step can be moved earlier by configuration, and revisit when model latency improves (M8, M9, or a faster model).

Record which step decided each request as a trace attribute (for example `music.decided_by`), and chart it on the Music dashboard.

**Check first**

- `ha/rest_command.yaml` defines `kronk_music` pointing at `/music/voice`, but the blueprint at `233d7ca` hands off with `conversation.process` instead. Find out whether the rest command is installed and used.
- How HA ranks a one-slot catch-all against built-in intents with `prefer_local_intents` on. Change 12 suggests sentence triggers win; confirm with the corpus.
- Current latency of the blueprint's direct path per media type, from the Music dashboard. That is the number the new path must match.
- How to make the model call in the decision order. `tool_service` has no model client today. Options: a small direct call to LiteLLM, or an internal orchestrator endpoint. Either way it must respect M9's queueing. Check whether LiteLLM passes llama.cpp's constrained-output options through, and measure the call's latency, since `gemma-4-e4b` spends reasoning tokens before answering.

**Implement**

1. Write the parser in `tool_service/music_resolve.py` beside `radio_name()` and `normalize()`. Match rooms and players from the right-hand end of the sentence against the real names HA returns, not a greedy slot. That removes the "Sun in Her Head" class of error.
2. Keep the fast case fast: try an exact library-name hit first (the reason change 11 plays an owned station in under a second), then search and score.
3. Add these rows to the M10 corpus before writing code: "play some deep house chill music", "play italian music", "play deep house", "play Portishead", and a title that contains a genre word.
4. Shadow mode: run the full decision order on every live request and log what it would have done next to what the blueprint or the `home` agent did. No behaviour change. Review a week of differences with Drew, including how often the model question fired.
5. Cut over one trigger type at a time behind a blueprint input, starting with the loose and catch-all triggers that already go to Kronk. Named albums and stations move last.
6. When all types have moved, retire the Jinja resolution and the fork's 12 changes. Keep the fork file in the repo for rollback: `docker cp` plus `automation.reload`, no HA restart.
7. Keep `play_music` on the `home` agent. The coordinator still needs it for composite requests and for M19.

**Verify:** the M10 corpus, offline and live dry-run, before and after each cutover step. Then the Music dashboard's p50 and p95 for a week.

**Tradeoffs**

- The blueprint's direct call to MA is the fastest path there is. Routing through `tool_service` adds one local HTTP hop and one websocket call. Drew chose full consolidation knowing that. Measure the cost on owned stations and named albums and report it. If the lag proves bad in use, the fallback is a hybrid that keeps HA's specific triggers for those cases; it brings back two grammars, which is the seam the incidents come from, so it is a last resort and Drew's call.
- The model question in step 6 adds a model call for ambiguous requests, so those are slower than the rest. Shadow mode shows how often that is.
- Descriptive requests ("that band with the singer from Soundgarden") are beyond the decision order. M19 is the route for those.
- This does not conflict with the operator's "no new local MA code" directive; nothing changes inside Music Assistant.

### M19. A spoken lead-in that goes straight to the model

Drew wants a reliable way to skip all parsing and hand a request to the model. He has been saying "ask Kronk to find some deep house music and play it" with mixed results.

**Confirmed by Drew, 2026-11-11:** the phrase is "ask Kronk to". It is the circuit breaker for every kind of request, not only music.

**Why it is unreliable today (to confirm on the box)**

- No HA trigger and no `routing.py` rule treats "ask Kronk to" as special. The sentence falls through HA's intents to the coordinator, which must delegate to the `home` agent, which must then build the `play_music` call. Two model hops.
- Whisper may transcribe "Kronk" as other words. Check HA's pipeline debug history for how it has been heard.

**Implement**

1. HA: one conversation trigger, `ask kronk [to] {request}`, plus the observed mis-hearings. It forwards the request and `trigger.device_id` to Kronk with a flag meaning "use the model". HA prefers the trigger with the most literal words, so this should beat the music catch-all; confirm with the corpus.
2. Orchestrator: a first rule in `routing.py` that sees the lead-in or the flag, strips it, and skips every regex shortcut and every direct dispatch. The request goes to the coordinator with its full tool menu. This follows the existing method-pin principle; `_DIRECT_OVERRIDE` is the precedent.
3. For music reached this way, the model decides what is wanted and calls `play_music`; `tool_service` still reads the sentence and resolves as today.
4. Add "Kronk" to the Whisper initial prompt (M13).
5. Add lead-in rows to the M10 corpus, including mis-heard forms.

**The rule for users:** say it plainly for the fast path; start with "ask Kronk to" and the model handles it.

**Tradeoff:** the lead-in guarantees where the request goes, not that the model gets it right. It is the slow path by design.

## Phase 4: speech recognition accuracy

The roadmap's "Voice accuracy" item already says to capture real utterances before benchmarking models. These three moves make that possible and try the cheap fixes before a model swap.

### M12. Capture what was said

Compose passes `TELEMETRY_RECORD_TEXT=${TELEMETRY_RECORD_TEXT:-false}` to the orchestrator and `voice_tracer`, so traces hold no transcripts unless `.env` overrides it.

**Decided by Drew, 2026-11-11:** transcripts on, and two weeks of audio capture on all pucks. Restart HA when needed; the household knows the system is under construction. Keep transcript text for 90 days, matching Tempo's retention. Grafana stays viewable without a login. Set a reminder or a timer-driven check to turn audio capture off after the two weeks.

**Check first:** `grep TELEMETRY_RECORD_TEXT .env`. It may already be on.

**Implement**

1. Transcripts: set `TELEMETRY_RECORD_TEXT=true` in `.env` and recreate the two containers. Grafana stays viewable without a login, by Drew's decision, so transcripts in traces are readable on the LAN.
2. Audio, time-boxed: HA's `assist_pipeline` has a `debug_recording_dir` option that writes each run's audio to disk. Turn it on for two weeks on all pucks to build a labelled set, then turn it off. This is a `configuration.yaml` change and needs an HA restart, which Drew has approved.
3. Label about 200 clips by hand with the correct text. Store them outside the repo; they are included in the M16 backup. This is the bench set for M13 and for the roadmap's Parakeet and distil-large comparisons.
4. Add an implicit error signal to `voice_tracer`: mark a run when the same satellite issues "not that", a stop, or a near-repeat within 60 seconds. That gives a wrong-action rate without labelling.

**Tradeoff, accepted by Drew:** this records guests as well as the household, on every puck. Transcript text is kept for 90 days. Audio capture ends after two weeks.

### M13. Bias Whisper toward household vocabulary

`systemd/wyoming-whisper.service` runs `large-v3-turbo` with no prompt. Station names, artists and room names are exactly what a general model gets wrong.

**Decided by Drew, 2026-11-11:** M13 is approved. Build it and use it on the dev path, which needs a second Whisper instance on its own port, since production and dev otherwise share one. Keep the prompt off for production until the M12 clips show it helps. The mishearing table below is not approved; review the captured M12 data with Drew after it has accumulated, then decide.

**Check first:** `~/services/wyoming-whisper/.venv/bin/python -m wyoming_faster_whisper --help` on the box, to confirm the `--initial-prompt` flag in the installed 3.1.0 and see the beam-size and VAD options.

**Implement**

1. Write `scripts/build_stt_prompt.py`. Sources: HA area names, MA player names, the Pandora station names in the MA library, and the top artists from `GET /music/taste/profile`. Output one short line, under about 100 tokens.
2. Have the unit read it from an `EnvironmentFile` and pass `--initial-prompt`. Regenerate it on the nightly taste rebuild; restart the unit only when the text changed.
3. Start with names only. Leave command verbs out of the first version.

**Verify:** word error rate and command-form error count on the M12 clip set, with and without the prompt, at the same p95 latency. This is the roadmap's own acceptance rule.

**Tradeoff:** a prompt biases every transcription toward its words, including on silence and noise. `VOICE_SETUP.md` records Whisper inventing text on silence with smaller models. A prompt full of command verbs could turn kitchen noise into "pause", which is why step 3 excludes them.

**Related, optional: a mishearing table.** The known misses are short commands heard as other words ("Pasta", "What's up?"). In `routing.py`, when the origin's player is playing and the whole utterance is a known mishearing, treat it as the command. Seed the table from the M10 corpus only; do not guess entries. The state check keeps "pasta" from pausing music that is not playing.

### M14. Tighter finished-speaking detection

The 2026-11-08 investigation found a 9.8-second capture that included unrelated conversation, and listed the device's finished-speaking setting as fix 3, not applied.

**Check first:** the current value on each satellite (a `select` entity per device) and, from `voice_tracer` data, the distribution of capture length and how often a transcript holds more than one sentence.

**Decided by Drew, 2026-11-11**

- No general rule that trims trailing speech from a transcript. Drew judged it the start of endless special-casing: Whisper's punctuation is unreliable and names contain full stops.
- Messy input goes to the model. Rules handle clean input only.
- Keep the existing `music_resolve.radio_name()` behaviour that stops at a full stop. It is narrow, tested, and guards the model path, where `tool_service` re-reads the sentence because the model drops the radio type.
- Run the puck-setting trial only after M12 has about two weeks of data to compare against.

**Implement**

1. A messy-transcript gate, in one place. If a transcript has more than one sentence or exceeds a set word count, skip the rule-based fast paths (M7 direct dispatch, the M11 decision order) and send it to the coordinator, which extracts the request. One gate, one or two thresholds, kept in `config/` (M21). Set the thresholds from M12 data.
2. After M12 has two weeks of data: set the kitchen satellite's finished-speaking option to the more aggressive value with `select.select_option`. No restart. Leave the other pucks as a control.
3. Compare one week: capture length, multi-sentence transcripts, and truncated commands (a request cut off mid-title). Revert if truncation rises.

**Tradeoffs**

- Messy captures take the slow model path. They should be uncommon, and step 2 aims to make them rarer.
- Aggressive detection cuts off people who pause mid-request, which is common with long album and station names. That is why it is a one-puck trial with a revert condition.

## Phase 5: smart-home control

### M15. Device control through Kronk

Kronk has no way to act on a Home Assistant device today. The shim ignores the tools HA offers and discards HA's system prompt, the setup notes say to leave HA's "control Home Assistant" toggle off, and the `home` agent's tools cover music, weather, solar, the list and the mirror only. Spoken device commands work only when HA's built-in intents match them.

**Check first**

- What HA can control at all. Count entities by domain from `/api/states` (light, switch, climate, cover, lock, fan, scene). The setup journal says this HA instance was built as a voice broker with no Zigbee, Z-Wave or Bluetooth radios. If few devices are integrated, that is step zero and it is Drew's project, not a code change.
- Which entities are exposed to Assist, and whether devices and satellites have areas and aliases.
- From M12 transcripts: which device requests people actually speak, and which ones HA's intents already handle.

**First project, chosen by Drew 2026-11-14: the outdoor lights over Matter.** Not part of the first sprint, but to be implemented soon after it.

Drew has Linkind/AiDot outdoor lights that are Matter-certified. In an earlier conversation three local-control paths were laid out and none was chosen: Matter (whole-strip control only), the AiDot protocol through a community library (fragile, needs their cloud for keys), and replacing the controller with an ESP32 running WLED (full per-pixel control). For Kronk, start with Matter.

1. Check first: whether the HA container setup can run a Matter Server beside it (host networking, IPv6 on the LAN), and whether the lights can be shared to a second Matter controller while staying in the AiDot app.
2. Add a Matter Server container to `docker-compose.ha.yml`, pinned like the others, and add HA's Matter integration.
3. Commission the lights from the HA phone app. They are Wi-Fi Matter devices, so no Zigbee or Thread radio is needed.
4. Give the light entity an area and aliases, expose it to Assist, and add the spoken forms to the M10 corpus. "Turn on the outdoor lights" should then work through HA's built-in intents with no Kronk code.
5. Out of scope for this project: per-zone effects by voice. Those stay in the AiDot app unless Drew later takes one of the other two paths.

Possible second project: an older conversation mentions a few Philips bulbs and smart outlets. If there is a Hue bridge, HA's Hue integration is local. Confirm with Drew what he has.

**Step 1: make the built-in path good (no Kronk code)**

HA's local intents answer in about two seconds with no model, and `prefer_local_intents` is already on. Most of the gain is here.

1. Give every device and every satellite an area, so "turn on the lights" acts on the room that heard it.
2. Add aliases for the names people say.
3. Expose only what a guest should be able to operate. Anything exposed to Assist is controllable by any voice.
4. Add scripts or scenes for multi-device requests ("movie time") with sentence triggers.
5. Add the spoken forms to the M10 corpus with the expected intent.

**Step 2: a fallback for phrasings the intents miss**

Requests HA cannot parse already fall through to Kronk, where the coordinator has nothing to call. Two designs:

|  | A: rephrase and hand back | B: HA's MCP server |
| --- | --- | --- |
| How | New terminal tool `ha_command(sentence)`. The model rewrites the request as a canonical sentence ("turn on the living room lamp"). `tool_service` sends it to HA's built-in agent through `conversation/process` with `agent_id: conversation.home_assistant`, and HA's reply is spoken verbatim. | Enable HA's Model Context Protocol Server integration. `tool_service` connects as a client with `HA_TOKEN` and offers a small subset of its tools (on, off, set light, set temperature, live context) to the `home` agent. |
| Model's job | Paraphrase only | Choose a tool and fill arguments |
| Limits on what it can touch | HA's exposed-entity list, enforced by HA | The same list, enforced by HA |
| New moving parts | One tool, one route | An MCP client, tool schema mapping, a new HA integration |
| Fits a 4B model | Well: output is one short sentence | Less well: the docs record this model dropping arguments |

Recommended: A first. It reuses the terminal-tool pattern that already works for music, and HA does the execution and the refusal.

**Implement A**

1. `tool_service`: `POST /ha/command` taking `{sentence, origin_area}`. If no room is named and an origin area is known, append "in the \<area>", the same workaround the blueprint uses because `conversation.process` cannot carry a device id. Call the built-in agent directly so a miss cannot loop back to Kronk.
2. `tool_service`: `GET /ha/exposed` returning names, areas and states of exposed entities, cached for about a minute.
3. `orchestrator`: add `ha_command` to the `home` agent as a terminal tool, map its result line in `_terminal_speech`, and inject the exposed names and areas into the `home` prompt the way the weather block is injected.
4. Treat HA's "I don't understand" replies as a failure sentence, not success.
5. Add device-control probes to the coordinator bench and rows to the corpus.

**Tradeoffs and limits**

- Never expose locks, garage doors, or alarm panels to this path. A wrong paraphrase turning on the wrong lamp is an annoyance; a wrong paraphrase on a lock is not.
- Design A can only do what HA's sentence grammar can express. Requests that need reasoning over state ("turn off whatever is still on downstairs") need design B or a script.
- Every exposed entity adds prompt tokens on each `home` request. Keep the exposed set small, and measure the prompt size against the 32k context.
- This path takes one model round, so expect several seconds. That is acceptable for a fallback, not for the common commands; those belong in step 1.

## Phase 6: operations

M16 is deliberately a later task (Drew, 2026-11-14): the changes in this plan will add new things to back up, so the backup is designed after they land. M17 makes phases 2 and 3 safer. M18 depends on both.

### M16. Backups: small, monthly, config only

The goal is enough to rebuild Kronk, not a copy of everything. Drew's requirements, 2026-11-14:

- **Configs and values only. No binaries.** He does not want backups measured in megabytes or gigabytes.
- **No llama.cpp binaries.** They are rebuilt in a container and extracted, and a newer server version may suit better anyway. Keep the build recipe in the repo instead.
- **Monthly, not nightly.** Nightly would fill the NAS.
- **Destination: a folder on the NAS.** Drew will create credentials that let the backup process write to it.
- **Plain tar file.** No new backup tool.
- **Scheduled after the first sprint**, once the new config (M21), corpus (M10) and other additions exist.

**Before the first sprint: one manual rollback point.** There is no backup today, and the sprint changes HA automations and settings. Take a one-off tar of HA's `.storage` directory and YAML files, by hand, before starting. This is a single small file and not the backup system.

**What goes in the monthly tar**

| Item | Notes |
| --- | --- |
| The repo itself | Already in git; confirm everything is pushed, including `config/` (M21) |
| Secrets and local config | `.env`, `.env.telemetry`, `searxng/settings.yml`, `secrets/`, `ops/hosts.json`, `/etc/kronk/`, the `/etc/fstab` line, GRUB kernel parameters |
| Host systemd units | `~/.config/systemd/user/`; also reconcile with `systemd/` in the repo |
| HA configuration | `.storage/` (integrations, device and entity registries, voice pipelines) and the YAML files. Exclude the recorder database, TTS cache and logs |
| Music Assistant settings | `settings.json` only, unless the library database turns out to be small |
| `data/music_taste.yaml` | Operator-edited household taste |
| Build recipes | How to build llama.cpp for gfx1151 and the wyoming-whisper wheel, as scripts or docs in the repo, with pinned versions |
| Model list | Each GGUF's source and checksum, so it can be fetched again |

**Check first (sizes decide what is in or out)**

- Size of Music Assistant's `library.db`, and what is lost if only `settings.json` is kept: the library re-syncs from the NAS and providers, but play history, favourites and playlists created inside MA may not come back.
- Size of HA's `.storage/` without the recorder database. The May tarball of the whole config volume was 4.9 MB.
- Sizes of the items in the next table.

**Data items, decided by Drew 2026-11-14**

| Item | Decision |
| --- | --- |
| `solar.db` | Include |
| Labelled voice clips from M12 | Include |
| Music Assistant `library.db` | Include only if it is 10 MB or smaller. Otherwise keep `settings.json` alone; Drew does not need MA's metadata and accepts that it is recreated on restore |
| HA `.storage/` | Include, and take the one-off copy before the sprint |

Not backed up: model files, llama.cpp and Whisper binaries and runtime libraries, Tempo traces, HA's recorder database, M12 audio captures beyond the labelled set, `sessions.db`, metrics.

**Implement**

1. `scripts/backup.sh` plus a monthly user timer. One dated tar per run, written to the NAS folder with Drew's credentials. Keep a fixed number.
2. Alert through `scripts/lib/notify.sh` if a run fails or the newest file is older than about 35 days.
3. **A documented restore plan. Drew rates this as important as the backup itself.** Write `docs/runbooks/restore.md` as a step-by-step rebuild from a bare machine, in order: OS and kernel parameters; clone the repo; unpack secrets and local config; mount the NAS; fetch the models from the recorded list and verify checksums; build llama.cpp and the wyoming-whisper wheel from the recipes; install the systemd units; bring up the compose stacks in dependency order; restore HA's `.storage/`; restore Music Assistant settings and let the library re-sync; restore `solar.db` and the clips; re-pair anything that cannot be restored from files. End with a verification checklist: every `/services` probe green, the M10 corpus passing, one spoken request per puck.
4. Every step in the runbook names the exact command and the file in the tar it uses. Anything that needs a human (a provider re-login, a cookie, a pairing) is listed with what to have ready.
5. Test the runbook once for real: restore onto another machine or scratch containers, and fix every step that did not work as written. Re-test after any change that adds something to the backup.

**Tradeoff:** monthly means up to a month of changes can be lost. For configuration that mostly lives in git, that is acceptable. For HA settings changed through its UI, the gap is real; the script can also be run by hand before any risky change.

### M17. A dev pipeline on the second wake word

Drew weights appliance and lab evenly, and today both run on one pipeline. The Voice PE has two assistant slots; the setup journal shows both bound to `kronk`.

**Decided by Drew, 2026-11-14:** M17 is approved. Its purpose is that new features can be tested on the dev path without interrupting household use of production. **Every puck honours the second wake word**, not just one; this replaces step 4 below.

**Wake word options.** The Voice PE ships with three on-device wake words: Okay Nabu (the default, described by Home Assistant as the most reliable), Hey Jarvis and Hey Mycroft. Each of the two assistant slots can take a different one. A custom word such as "Hey Kronk" is possible but needs a trained microWakeWord model and custom ESPHome firmware on every puck; community reports describe false triggers and build failures. Confirmed by Drew, 2026-11-14: Okay Nabu stays for production and Hey Jarvis is the dev wake word.

**Check on the box**

- The satellites, per Drew: two Voice PEs and one FutureProofHomes Satellite1, with a second Satellite1 to be deployed. Confirm on the box that the Satellite1 firmware offers Hey Jarvis and supports a second wake word bound to a second pipeline. If it does not, say so before changing anything.
- What assistant slot 2 does today on each puck, including the tap-to-talk button, so that binding it to `kronk-dev` does not send button presses to dev by surprise.
- Tag dev-pipeline requests in traces, so they are excluded from the M12 accuracy data and the household dashboards.

**Implement**

1. Add an `orchestrator-dev` service under a compose profile: same image build, its own env file, its own model assignments, a different internal port, and an nginx prefix such as `/dev/`.
2. Give it `MUSIC_DRY_RUN` (from M10) by default, so experiments do not start music in the house unless asked to. Music tests that do make sound use the office speaker.
3. In HA, add a second Ollama integration entry pointing at `/dev/voice` and a second pipeline, `kronk-dev`, with the same STT and TTS.
4. On every satellite, bind the second assistant slot and the Hey Jarvis wake word to `kronk-dev`, after the checks above. Find out what the tap-to-talk button does on each device once slot 2 changes, and tell Drew.
5. Working rule: all sprint work, model swaps and each M11 cutover step run on dev first, with the corpus, before production changes.
6. Deploy production from a tagged commit, not the working tree, so "what is running" has a name and a rollback target.

**Tradeoff:** dev and production share the GPU. A bench on dev slows household requests while it runs. Run heavy benches when the house is empty, or accept the slowdown.

### M18. An upgrade cadence

Three pinned things now depend on each other: Home Assistant at `2026.5.4`, a Music Assistant beta base with a local Pandora patch (`2.11.0b5-kronk.9`), and a blueprint forked from upstream version `20250404`. Pinning is right; having no schedule to move the pins is the gap. LiteLLM (`v1.88.1`) and the llama.cpp build belong on the same list.

**Check first**

- Current stable versions of each, and the release notes between pinned and current for: the Ollama conversation integration, Assist pipelines and satellites, Wyoming, conversation triggers, and security fixes.
- `docs/plans/MA_FORK_STATUS.md`: whether the upstream Pandora change has merged.

**Decided by Drew, 2026-11-14**

- **Minor upgrades: monthly, applied without asking.** Notify Drew afterwards through HA's notification service that the upgrade happened and what changed.
- **Major upgrades: quarterly, and only with Drew's explicit greenlight.** The system never starts one on its own. It notifies Drew that a major upgrade is needed, and the impact report must already be written to disk when that notice goes out. The report lists which versions change and which of Kronk's systems are affected, if any. Drew reviews the report, then greenlights and declares a window, or declines.
- **Music Assistant is excluded for now.** It stays on the fork with the Pandora station-create patch. The upstream PR is not being pursued at present. Once that work is merged upstream, MA becomes an ordinary component under these same rules.

**Define minor and major per component before automating anything.** Version schemes differ, and "minor" is not always harmless. Mapping confirmed by Drew, 2026-11-15, including that HA's monthly releases count as major:

| Component | Minor (monthly, automatic) | Major (quarterly, report and window) |
| --- | --- | --- |
| Home Assistant | Patch releases within the pinned month, for example `2026.5.4` to `2026.5.5` | A new monthly release, for example `2026.5` to `2026.6`. These carry breaking changes |
| LiteLLM, SearXNG, nginx, Piper, Tempo, Grafana | Patch releases | Minor and major releases |
| llama.cpp build | None; builds have no stable scheme | Any new build, with the model benches re-run |
| Python dependencies in the lockfiles | Patch releases, regenerated with hashes | Everything else |

**Implement**

1. `scripts/upgrade_minor.sh` on a monthly timer. For each component: find the newest patch release, update the pin, bring it up, run the M10 offline corpus and the live dry-run corpus, and roll back to the previous tag if either fails. Commit the pin change.
2. Notify through `scripts/lib/notify.sh` (HA's notify service to Drew's phone): what was upgraded, from and to which version, and whether anything was rolled back. If HA itself is the component that failed, the notice needs a path that does not depend on HA; decide that with Drew.
3. A quarterly check that looks for major releases and writes a report to `docs/upgrades/<date>.md`: the version changes, the release-note items that touch Kronk's integration points (the Ollama conversation integration, Assist pipelines and satellites, Wyoming, conversation triggers, the Music Assistant integration, Matter), the Kronk systems affected, and a proposed order. Then notify Drew that a major upgrade needs scheduling, with the report's location.
4. Major upgrades run from a runbook in `docs/runbooks/`, modelled on `searxng-bump.md`: take the backup (M16), bring the new version up on the dev path or in a scratch container (M17), run the corpus, upgrade production in the declared window, verify, and roll back by image tag and volume restore if needed. One component per window.
5. The first major upgrade is HA, since M8 and M15 may need newer HA features. It follows this same process: report first, then a window.

## Phase 7: Kronk's voice

### M20. TTS trial with a go/no-go

Drew wants to customize Kronk's voice and asked about Chatterbox. This move is a trial only. Its output is a go/no-go recommendation on replacing Piper, with numbers. Drew values voice customization in its own right, so it is scored as a benefit, not treated as a nice-to-have.

**Candidates**

| Engine | Custom voice | How it reaches HA | Known concern |
| --- | --- | --- | --- |
| Piper `en_US-lessac-medium` (baseline) | No, preset voices | Existing Wyoming service | None; this is the reference |
| Pocket TTS | Yes, cloning | A community Wyoming server | Quality unknown; claimed fast on CPU |
| Chatterbox and Chatterbox-Turbo | Yes, cloning | An OpenAI-compatible Chatterbox server behind the `wyoming-openai` proxy | Needs TheROCK nightly PyTorch for gfx1151; expected near real time at best; brings a ROCm process back |
| RVC layer over Piper (optional) | Yes, timbre only | `wyoming-rvc` in front of the existing Piper | Speed claims are for other GPUs |

**Check first**

- Current versions of each, and whether each Wyoming server supports streamed synthesis. M8 depends on that.
- The June hang investigation (`INVESTIGATION_2026-06-09_hangs.md`): ROCm kept the GPU busy at idle, which is why the llama.cpp units moved to Vulkan. Any PyTorch ROCm process must be checked for the same behaviour.
- The reference voice. Use a recording Drew has the right to use, such as his own voice or a consenting household member's.

**Method**

1. Run each candidate as a Wyoming service on a spare port. Nothing in HA changes during the bench.
2. Write `scripts/tts_bench.py`. It speaks Wyoming directly to each service with a fixed set of about 20 sentences: short confirmations ("Paused in the kitchen."), music confirmations with artist and station names, a two-sentence weather answer, a long spoken answer, and sentences with numbers, dates and times.
3. Measure per engine: time to first audio, total synthesis time, real-time factor, cold-start time, memory, and failures. Repeat each sentence several times.
4. Repeat the run while `gemma-4-e4b` is generating, and record its tokens per second. In real use TTS and the model share the GPU.
5. Leave each service idle for an hour and record GPU busy percentage and power.
6. Listening test: Drew and the household rate the same sentences blind for naturalness, and count mispronunciations and garbled or extra sounds.
7. Finalists only: attach to the `kronk-dev` pipeline (M17) and time wake word to first audio on a puck.
8. Write the results to `docs/bench/tts_bench_<date>.md` with a recommendation.

**Go/no-go (limits confirmed by Drew, 2026-11-14)**

Drew will supply the voice clips for cloning later. Until then, do everything that does not need them: stand up each candidate, run the speed, stability, idle-GPU and model-slowdown measurements with stock or preset voices, and write up those results. The cloning-quality check and the household listening test wait for the clips.

Go only if the candidate passes every hard limit and the household prefers its voice.

| Hard limit | Proposed value |
| --- | --- |
| First audio on a short confirmation | No more than 0.5 s slower than Piper |
| First audio on a three-sentence answer | 2 s or less |
| Effect on the model while both run | Tokens per second drop by no more than 10% |
| Garbled or extra audio | None in 100 sentences |
| Idle GPU after one hour | Back to the Piper-only baseline |
| Service stability | No crash or hang across the full bench |

No-go if any limit fails. Record which limit failed, so the trial can be repeated when the engine or the ROCm stack improves.

**Rollback if adopted:** change the pipeline's TTS back to Piper. Keep the Piper service running for at least a month after any switch.

**Tradeoff to state in the recommendation:** a cloned voice that is slower than Piper trades against the latency work in phase 2. The report should give both numbers side by side so Drew can weigh the voice against the wait.

## Phase 8: operator tooling

### M21. One place to see and edit prompts and adjustments

Requested and approved by Drew, 2026-11-11, both steps. He needs one place, as a human, to track every prompt and tuning value in the system, with an interface to view and edit them. Editing is gated by a passphrase; viewing is not.

**Where the adjustments live today**

| What | Where |
| --- | --- |
| Agent and coordinator prompts | Python strings in `orchestrator/agents.py` |
| Routing rules | Regexes in `orchestrator/routing.py` |
| Thresholds, timeouts, model assignments | Env vars in `docker-compose.yml`, constants in code |
| Household taste | `data/music_taste.yaml` |
| Music sentences and player order | The HA blueprint (retired by M11) |
| Arriving with this plan | Whisper prompt sources (M13), marker words, genre list and provider order (M11), lead-in phrases (M19), TTS choice (M20) |

**Step 1: a config directory of plain files**

1. Create `config/` in the repo: one markdown file per agent prompt under `config/prompts/`, and YAML files for music policy (provider order, marker words, thresholds), tuning (round budgets, timeouts, verify windows), speech (Whisper prompt sources and extra words) and lead-in phrases. Put a comment on every key saying what it does.
2. Bind-mount it into the orchestrator and `tool_service`. Load at startup through a validated schema. Reload on an explicit `POST /config/reload`.
3. An invalid file never takes effect: keep the last good values and show the error on `/services`, with the file and line.
4. No secrets in this directory. Tokens stay in `.env`.
5. Migrate one piece at a time, starting with the `home` agent prompt. For each prompt, add a test that the rendered system prompt is byte-identical before and after the move, so the migration itself changes no behaviour.
6. Routing regexes stay in code with their unit tests.

**Step 2: a `/settings` page in the Kronk web UI**

1. Read-only by default, for anyone on the LAN: each file with its description, the value in effect, where it came from (file, env var or code default), and the date of its last change. Show routing rules and env-derived values here too, read-only.
2. Editing requires a passphrase. Store only its hash, from a `SETTINGS_PASSPHRASE_HASH` entry in `.env`. Use a session cookie and rate-limit attempts.
3. A save validates the file, writes it, reloads, and makes a local git commit of `config/` with a message naming the key changed. No push. The containers run as UID 1000, matching the host user, so file ownership stays correct; confirm git is usable from inside the container or do the commit from a small host-side helper.
4. Guardrails before a save takes effect on the household path: run the M10 offline corpus for music-policy and lead-in changes, and offer "apply to dev first" for prompt changes once M17 exists.
5. A history view per file from the git log, with a one-click revert that is itself a commit.

**Check first**

- Every place a prompt or tuning constant is defined, by search, so nothing is left behind in code. Include `tool_service` (verify timeout, resolver thresholds) and the LiteLLM config.
- Whether `kronk-context.md` and the `assistant` agent should read from the same files, so "what is my current setting for X" can also be asked by voice.

**Tradeoffs**

- The web UI is open on the LAN by Drew's choice and served over plain HTTP, so the passphrase crosses the network unencrypted. Acceptable on this home network; do not reuse a passphrase from elsewhere.
- Live edits reach the household immediately. The guardrails in step 2 are what keep a bad prompt edit from becoming an incident.
- Prompt edits invalidate llama.cpp's cached prompt prefix once. The next request pays for it; no lasting cost.

### M22. Kronk on the MagicMirror (design only; not in the first sprint)

Drew wants a display that reflects what Kronk is doing. He judged the existing MagicMirror (a Raspberry Pi 4) the best place for it, and is short of screen space there. This section records what could be shown and how. Nothing here is built in the first sprint.

**What to show**

| Widget | Content | When visible | Source |
| --- | --- | --- | --- |
| Now playing | Track, artist, station or album, room, provider | Only while something is playing | `tool_service` `/music/now_playing`, or HA `media_player` state |
| What Kronk heard | The last transcript and which puck heard it | About ten seconds after a request | `voice_tracer` (HA pipeline runs) or the orchestrator's request event |
| What Kronk did | The spoken reply, the route or tool used, and how long it took | With the transcript | Orchestrator events and the trace |
| Puck state | Listening, thinking, speaking | While a request is in flight | HA `assist_satellite` entity state |
| Health | A service that is down, a failed canary, a pending upgrade notice | Only when something is wrong | `/probe/*` and the canary timers |

**How**

1. A live status feed on the orchestrator, for example a server-sent-events endpoint, that merges the sources above into one stream of small JSON events. The `/settings` and `/services` pages can use it too.
2. A MagicMirror module, `MMM-Kronk`, that subscribes to the feed and renders the widgets. Each widget shows and hides itself on events, so Kronk borrows screen space only when it has something to say.
3. If space is still short: a page-rotation module, a higher-resolution panel of the same size, or a small second display on the Pi 4's second HDMI output. Drew has not yet said what the current panel is or whether it sits behind mirror glass; ask before recommending hardware.

**Constraints**

- The roadmap says any change on the Pi needs Drew's per-action go.
- Transcripts on a wall display are visible to everyone in the room. Drew has approved recording transcripts (M12); confirm he also wants them displayed.
- Tag dev-pipeline requests so the mirror shows production activity only.

## The first sprint: how the work will be run

Drew's process, stated 2026-11-14. The receiving session is the long-running coordinator.

**Setup**

1. Drew starts a fresh Claude Code session on Kronk inside herdr, a terminal multiplexer. That session runs for the whole sprint and coordinates it.
2. Confirm every assumption in this document against the real system. Each move's "Check first" list is the starting point.
3. Change the plan where discovery on Kronk contradicts it, and tell Drew what changed.
4. Create one git branch for all sprint work, named `refactor` or similar. Every commit goes on it.

**Per item**

1. Pass criteria. Before the build, the coordinator writes the item's pass criteria from this document, so the tester checks against the plan and not against the builder's own claims.
2. Build. Launch a separate agent in its own herdr workspace: Opus 5.5, high thinking, `--dangerously-skip-permissions`. It works on the `refactor` branch, writes the item, runs the tests, and commits. It writes a report of its work to disk and reports back to the coordinator.
3. End-to-end test. When the build agent finishes, launch a separate agent: Fable 5.2, medium thinking, skip-permissions. It tests the item end to end on the dev path against the pass criteria, then runs the whole regression set: the unit tests plus the M10 corpus.
4. Fix loop. On a failure, the test agent reports to the coordinator, which launches another Opus agent with enough context to fix it and report back. Then the end-to-end test starts again. After three failed fix rounds on one item, stop and report to Drew.
5. Promote. When the item passes all tests, promote it to production and tag the branch. Held items follow their own notes (see "Promotion to production" below).
6. Repeat for the next item until the sprint's items are done.

**Fixed rules**

- Music tests that make sound use the office speaker only.
- HA may be restarted when needed; the household knows the system is under construction.
- Follow the stopping rule and every "Decided by Drew" note in this document.

**Not in the first sprint (already decided)**

- M15, the outdoor lights over Matter: soon after the sprint.
- M16, backups: after the sprint, once the new pieces exist. Take the one-off copy of HA's `.storage/` before starting.
- M22, the MagicMirror display.
- The Pandora upstream PR.

**Sprint items, in order (agreed with Drew 2026-11-15)**

1. M17, dev path: a second orchestrator and a `kronk-dev` HA pipeline on Hey Jarvis.
2. M10, regression corpus: utterances with expected outcomes, a silent replay script, a music dry-run switch.
3. M1, remove health and finance: both agents, both services, `retire_calc`, and their data.
4. M1b, remove the hot tub feature.
5. M3, harden the exec filter.
6. M4, take the web tools away from the devops agent.
7. M5, guard `fetch_url` and add `http_check`.
8. M2, friendly voice errors.
9. M6, exposure audit only: record what is listening and who connects; change nothing without Drew's go.
10. M21 step 1, the `config/` directory.
11. M7, direct dispatch.
12. M19, the "ask Kronk to" lead-in.
13. M14, the messy-transcript gate. The puck trial waits for M12 data.
14. M11, music in `tool_service`, built and run in shadow mode only. The cutover waits for about a week of shadow data and Drew's review.
15. M8, streamed speech and the short-answer instruction.
16. M9, narrow the lock, under the no-slowdown constraint.
17. M12, transcripts on, two weeks of audio, the wrong-action signal.
18. M13, the Whisper vocabulary prompt. Built and usable on the dev path; kept off for production until M12 clips show it helps.
19. M21 step 2, the `/settings` page.
20. M18, upgrade tooling: the monthly minor-upgrade script and the major-upgrade report and notice.
21. M20, the TTS bench: speed and stability now; cloning and the listening test wait for Drew's clips.

**Promotion to production, decided by Drew 2026-11-15.** All sprint work is built and tested on the dev path behind Hey Jarvis. An item is promoted to production once it passes all its tests and the full regression set; no separate approval is needed per item. Items that are deliberately held follow their own notes: M11 goes to production in shadow mode only, M13 stays off for production until measured, and M6 changes nothing. At the end of the sprint Drew needs a report listing what reached production and what still needs work.

**Review step before any work starts, added by Drew 2026-11-15.** After assumptions are confirmed on Kronk, update this document with what was found and review it with Drew. Only then create the branch and launch the first agent.

**Checkpoint with Drew after items 1 and 2 (M17 and M10).** Every later test depends on the dev path and the corpus. Show him both working before launching item 3.

**Rules for the coordinator and every agent, accepted by Drew**

- Guard rails for agents that skip permission prompts on a machine the household depends on. Put these in every agent brief: no deletion of data outside the repo except where this document says Drew ordered it (M1, M1b); no changes to Music Assistant's settings; no firmware changes to pucks; no change on the MagicMirror Pi; stop and ask when a step is not reversible.
- One agent at a time. If agents ever run in parallel on one branch, give each its own git worktree.
- Reports go to `docs/sprint/<item>-build.md` and `docs/sprint/<item>-e2e.md`.
- Some items cannot finish inside one continuous sprint because they need elapsed time or a person: M11's cutover (about a week of shadow data, reviewed with Drew), M12's two weeks of audio and the hand-labelling, M13's measurement and M14's puck trial (both need M12 data), M20's listening test and cloning (need Drew's clips), and any M6 change (needs his go). Build these and leave them running or waiting.
- End-of-sprint report for Drew: what reached production, what is built but held and why, and what still needs work.
- herdr: this document's author does not know herdr's commands. Learn them from its help output on Kronk before relying on them.

**Workspace cleanup, required by Drew 2026-11-15.** Every build, test and fix agent's herdr workspace is shut down and closed when that agent finishes. Order matters: the agent writes its report to disk and reports back first; the coordinator confirms it has the report, then closes the workspace. Do not have an agent close its own workspace before its report is confirmed, or the report can be lost. No workspaces are left open between items, and the end-of-sprint report confirms none remain.

## Still open

Everything else in this document is decided. Raise each of these with Drew when the move it belongs to comes up.

- [ ] **M3:** how wide the path deny list for file-reading commands should be.
- [ ] **M5:** whether `http_check` gets a host allow-list.
- [ ] **M6:** after the audit, which listeners to change (LiteLLM, the Wyoming ports), and whether guest Wi-Fi is isolated from Kronk's network.
- [ ] **M11:** when to cut over from shadow mode, after Drew reviews about a week of data; and whether the NAS-first lag is acceptable.
- [ ] **M13:** whether to add the mishearing table, after Drew reviews the M12 data.
- [ ] **M15:** what other devices exist (Philips bulbs, smart outlets, a Hue bridge?), and which are never exposed to voice.
- [ ] **M16:** the NAS folder and credentials for the backup.
- [ ] **M17:** whether the Satellite1 supports Hey Jarvis and a second wake word; verify on the box.
- [ ] **M18:** how an upgrade notice reaches Drew when HA itself is the thing that failed.
- [ ] **M20:** the voice clips to clone, which Drew will supply.
- [ ] **M21:** the passphrase for editing on `/settings`.
- [ ] **M22:** what the current MagicMirror panel is, and whether transcripts should be shown on it.

## External references

These came from web search results during the review. They were not read in depth and are leads to verify, not settled facts.

- Streamed TTS for LLM replies in Home Assistant, including a report of streaming starting near 65 characters: [TTS Streaming of LLM Chat Messages activated but not working](https://community.home-assistant.io/t/tts-streaming-of-llm-chat-messages-activated-but-not-working/1005088), [Piper TTS: no streaming w/LLM](https://community.home-assistant.io/t/piper-tts-no-streaming-w-llm/917374)
- An initial-prompt option for wyoming-faster-whisper: [NixOS module options](https://mynixos.com/options/services.wyoming.faster-whisper.servers.%3Cname%3E)
- Home Assistant's MCP server and its exposed-entity limit: [Model Context Protocol Server](https://home-assistant.io/integrations/mcp_server/)
- Speaker recognition is a community request, not a built-in feature: [Speaker recognition in voice assistant](https://community.home-assistant.io/t/speaker-recognition-in-voice-assistant/654276)
- Home Assistant Core 2026.10.3 reported as released on 2026-10-23: [Home Assistant Assist review](https://www.promptquorum.com/power-local-llm/home-assistant-assist-review)
