# CLAUDE.md — session bootstrap for Kronk

Kronk is a fully-local, privacy-first home AI assistant: a chat UI + voice
pipeline in front of a router → specialist-agent → coordinator pipeline over
llama.cpp models, all on one machine (AMD Ryzen AI 375, Radeon 8060S iGPU,
122 GB RAM, hostname `kronk`).

## Read these, in this order

1. **`README.md`** — the architecture doc. Service map, agent-loop walkthrough,
   model/agent/tool inventory, operations runbook, design decisions.
   Read it fully before touching anything.
2. **`ROADMAP.md`** — the single source of truth for planned work. Any new
   feature idea, open item, or deferral belongs there (it also defines the
   docs lifecycle: plan doc → build → `docs/features/` doc → blog topic).
3. **`orchestrator/agents.py`** — the `AGENTS` dict is the single source of
   truth for agents, their tools, and the router prompt.
4. **`orchestrator/tools.py`** — tool definitions + dispatch.
5. **`orchestrator/main.py`** — the pipeline entrypoints: `/message` (chat UI,
   SSE), `/v1/chat/completions` (OpenAI shim), `/api/chat` (Ollama shim — this
   is what Home Assistant's voice pipeline calls).

Then, only when the task touches that area (shipped features each have a
distilled doc in `docs/features/` — start there, then the build journals):

- **Voice** (Voice PE, STT/TTS, HA pipeline): `docs/features/voice-pipeline.md`,
  then `docs/VOICE_SETUP.md` — the build journal with the final architecture
  diagram and open items.
- **Music** (Music Assistant, Sonos, players): `docs/features/voice-music-control.md`,
  then `docs/plans/MUSIC_ASSISTANT_PLAN.md` plus the header comment in
  `docker-compose.ma.yml`.
- **Telemetry / perf**: `docs/features/telemetry.md` (v1 is a prototype —
  Telemetry v2 is on the roadmap), then `docs/TELEMETRY_GUIDE.md`,
  `docs/PERF_FINDINGS_2026-06-10.md`, `docker-compose.langfuse.yml`.
- **Routing / agent loop internals**: `docs/features/agents-as-tools-routing.md`,
  `docs/plans/STREAMING_REFACTOR_PLAN.md`.
- **History / why-is-it-like-this**: `docs/HISTORY.md`, `docs/incidents/`,
  `docs/plans/` (every plan doc carries a status header — trust it over the
  body, which reflects the plan as written, not as built).

## Runtime topology (four compose stacks + host systemd)

| Stack | File | Project |
|---|---|---|
| Kronk app (orchestrator, nginx, tools, litellm, searxng, …) | `docker-compose.yml` | `kronk` |
| Home Assistant | `docker-compose.ha.yml` | `kronk-ha` |
| Music Assistant + YT PO-token helper | `docker-compose.ma.yml` | `kronk-ma` |
| Langfuse telemetry | `docker-compose.langfuse.yml` | (langfuse) |

LLM servers and GPU STT are **not** containers — they're user systemd units on
the host (`~/.config/systemd/user/llama-*.service`, `wyoming-whisper.service`).
Reference copies live in `systemd/`. Each compose file carries an
`# Operations:` comment block with its day-2 commands — read it before
running compose commands against that stack.

## Verify, don't trust the docs

The README and plan docs lag reality. Before asserting what's running:

```bash
docker ps --format '{{.Names}}\t{{.Status}}'
systemctl --user list-units 'llama-*' 'wyoming-*' 'kronk-*' --no-pager
```

(Example: the README lists six llama units; typically only a subset is
running.) Live HA state can be queried read-only with the token in `.env`:
`curl -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/states`.

## Engineering tenets

Principles for building on Kronk. Most were earned here and carry a receipt.
When a change would violate one, say so explicitly and get operator sign-off
before proceeding.

1. **Local-first is the product.** No feature sends data off the box or takes
   a cloud dependency by default; doing so is an explicit operator decision.
2. **Rule of least surprise.** Prefer the boring, conventional design.
   Anything clever gets documented where the next reader will trip over it.
3. **Pin everything; upgrade deliberately.** Hash-pinned lockfiles, pinned
   image tags. Version bumps happen on purpose — never as a side effect of a
   rebuild. *(Receipt: MA 2.8.8 shipped a known-fixed ytmusicapi bug; we run
   it until an intentional update day.)*
4. **Right-size to single-operator scale.** SQLite over Redis, one
   tool_service over microservices, no schema ceremony without payoff.
   *(Receipt: `TECH_DEBT.md` "Considered and rejected".)*
5. **Structural guardrails beat prompt engineering.** On 4B-class models, if
   the loop lets the model do the wrong thing, it eventually will — change
   the loop, not the prompt. *(Receipt: terminal tools — prompt tweaks never
   stopped hallucinated "now playing" after failures; ending the turn did.)*
6. **Verify the effect, not the status code.** A 200 from an async system
   proves nothing; poll for the state change. Never let a model claim a
   success the code didn't verify. *(Receipt: `play_media` returns 200
   before MA even tries; we poll for `playing`.)*
7. **Fail loud, specific, and traceable.** Every user-facing failure carries
   the most specific cause available and is findable in telemetry in one
   step. "An unexpected error occurred" is itself a bug.
8. **One source of truth per fact.** `AGENTS` dict for agents, `ROADMAP.md`
   for plans, env vars for wiring. Duplicated facts drift, and drifted docs
   lie — the "verify, don't trust the docs" section above exists because of
   this.
9. **Single-variable changes.** One change, one test, then the next.
   *(Receipt: the Whisper model ladder — small → medium → large-v3-turbo,
   each step isolating one failure mode.)*
10. **Least privilege by default.** De-privileged HA container, `HA_TOKEN`
    scoped to tool_service only, and cross-machine reach (MagicMirror) via
    forced-command SSH keys that can do exactly one thing.
11. **Reversibility before destruction.** Take the backup before the
    destructive op; prefer changes git can undo; never `down -v` on stateful
    stacks. *(Receipt: the HA config tarball taken before the compose-stack
    split.)*
12. **Latency is a feature spec.** Voice has a budget (~2 s local tier,
    ~15–25 s worst-case Kronk tier — already at the edge of tolerable). New
    voice features state their expected latency up front, not after.

## Definition of done — run the tests, unprompted

The operator will not remind you. A change is not "done" until the tier
below that matches its blast radius has passed, and failures are reported
verbatim — never summarized away.

1. **Any code change** → `./scripts/run_tests.sh` (wraps pytest with
   `tests/.venv` — bare `pytest` on system python fakes import errors).
   Must pass before suggesting a commit. New behavior lands with a test;
   a bug fix lands with the test that would have caught it.
2. **Anything deployed** → verify live after `up -d --build`: hit the
   touched endpoint(s) for real. If the orchestrator was rebuilt, restart
   nginx, then verify through the **shim** path too (`/api/chat`) — that's
   the route HA/voice actually uses and it fails independently.
3. **Large features, or anything touching the pipeline, routing, shims, or
   agent loop** → also run the live battery:
   `./scripts/pipeline_bench.sh <label>` (8 prompts, every route + transport,
   results land in `docs/bench/`). For perf-sensitive work run it before
   (`<x>-pre`) and after (`<x>-post`) — the files are diffable. **Caveat: it
   clears chat history**; confirm with the operator if a session is live.
4. **Voice-touching changes** → no automated smoke test exists yet
   (ROADMAP item 8). Until it ships: run the utterance checks manually via
   HA's websocket (`assist_pipeline/run` — recipe in
   `docs/features/voice-pipeline.md` → "Testing utterances"), and
   ask the operator for a real "Okay Nabu" test on anything user-facing.
   **When item 8 ships, replace this bullet with the script and make it
   mandatory.**

## Ground rules

- **Deploys**: code change → `docker compose up -d --build <service>` (never
  plain `restart` — it reuses the old image). nginx config → `docker compose
  restart nginx`. `litellm/config.yaml` is bind-mounted, hot-editable.
- **Never `docker compose down -v`** on the `kronk-ha` or `kronk-ma` stacks —
  their volumes hold all HA integrations / the MA library and auth tokens.
- **Git**: the operator runs `git commit` / `git push` themselves. Suggest a
  commit at milestones; don't run those commands.
- **Secrets**: `.env` (gitignored) holds `HA_TOKEN`. `searxng/settings.yml` is
  gitignored; its `.example` is the template. No other secrets exist.
- **New compose files** get an `# Operations:` comment block (up / restart /
  logs / down recipes) like the existing ones.
- **Docs lifecycle** (defined in `ROADMAP.md`, follow it): sizeable feature →
  plan doc in `docs/plans/` first → build → distill into
  `docs/features/<name>.md` (what/how/gotchas + a "Blog hooks" section — the
  operator blogs from these) → update the plan doc's status header and move
  the `ROADMAP.md` line to Shipped. Never add a roadmap section to any other
  file — `ROADMAP.md` is the single source of truth for planned work.
- **Incidents**: whenever something breaks or a real investigation starts
  (production misbehavior, data loss/scare, anything needing more than a
  quick obvious fix), write it up in `docs/incidents/` — follow the existing
  naming (`INCIDENT_<date>.md` for breakage, `INVESTIGATION_<date>_<slug>.md`
  for open-ended debugging). Start the doc **during** the investigation, not
  after — evidence (logs, trace IDs, timelines) evaporates. Capture: symptom
  as the user saw it, timeline, hypotheses tried (including the wrong ones),
  root cause, fix, and what would have caught it sooner. Unprompted, like
  tests — the operator won't remind you. These double as blog material.
