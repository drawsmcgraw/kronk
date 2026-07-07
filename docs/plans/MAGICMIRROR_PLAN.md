# MagicMirror agent — Plan

Status: **tier 1 built** (2026-07-06, branch `magicmirror-updater`) —
awaiting Pi-side setup (operator steps below), then a live end-to-end test.
Step 0 (model bench) DONE same day — results in `docs/model_results.md`
(2026-07-06 section): qwen3.6-27b ruled out; **operator kept devstral**
(2026-07-06; also confirmed still Mistral's newest open coding model —
Codestral 2508 is the FIM line) and enabled its unit; qwen3-coder-30b-a3b
GGUF kept on disk for a future revisit.

## Decisions (2026-07-06, operator + build)

- **SSH login is user `kronk` on the Pi** ("kronk is not drew"). MagicMirror
  lives in `/home/drew` and pm2 runs as drew, so the forced command is
  `sudo -u drew /home/drew/kronk/mm-update.sh` with a sudoers grant pinned
  to exactly that script — user kronk can run those three verbs as drew and
  nothing else. (Group-membership tricks were rejected: pm2 daemons are
  per-user, so the restart *must* run as drew anyway.)
- **Naming flag:** the operator said the Pi's *hostname* will be `kronk` —
  but the AI box already answers to `kronk`/`kronk.local` on mDNS; two hosts
  with one name will fight. Assumed intent: the *user* is kronk. Compose
  default is `MM_SSH_TARGET=kronk@mirror.local` — override via `.env` once
  the Pi's real hostname is settled.
- **Backup before update is mandatory** (operator requirement): the script
  tars the entire MagicMirror tree (config.js, custom.css, modules/, and
  node_modules — so rollback needs no npm reinstall) to `~/mm-backups/`
  before touching anything; keeps last 3; `rollback` verb restores the
  newest. Update procedure per the official upgrade guide (verified
  2026-07-06): `git pull && node --run install-mm`, npm-install fallback,
  refuse to run over local source edits.
- **Async ack pattern** (voice budget vs a 1–5 min npm install): the tool
  does a fast SSH preflight (`status` verb — proves reachability, auth, and
  the script), speaks "updating, backup first, takes a few minutes", and
  the real update runs as a tool_service background task; the outcome lands
  in `/data/mm_update_last.json` + logs, readable at
  `GET /magicmirror/status`. Claiming "started" is verified truth; the
  final result is verified where it can be (tenet 6).

Kronk gains the ability to act on the MagicMirror — a Raspberry Pi on a
separate machine running MagicMirror². This is Kronk's **first capability
that reaches another machine**, so the access design is the real deliverable:
it sets the pattern for every future cross-machine feature (ROADMAP item 7,
tenet 10).

## Access design: SSH, two keys, least privilege

Direction chosen by the operator (2026-07-05): SSH from Kronk with dedicated
keys whose authorization is enforced Pi-side, not by prompt.

- **Key 1 — `kronk-mm-update` (tier 1):** `authorized_keys` entry with
  `command="/home/pi/kronk/mm-update.sh"` plus
  `no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty`.
  Whatever the client sends, the Pi runs only the update script. The key is
  physically incapable of anything else — structural guardrail, not policy.
- **Key 2 — `kronk-mm-ops` (tier 2, later):** forced command pointing at a
  dispatcher script (`mm-ops.sh <verb>`) that allowlists a small verb set
  (`status`, `logs`, `restart`, `config-get`, `config-set <module> <json>`,
  `screen-on|off`). The model never composes shell — it picks a verb and
  arguments; the dispatcher validates both. Unknown verb → usage text.
- Keys live in a host directory bind-mounted read-only into tool_service
  (the only container with cross-machine reach, same posture as HA_TOKEN).
  Private keys never enter an image or the repo.

## Tier 1 — "update the magic mirror" (ship first, no new model)

`home`-agent **terminal tool** `update_magicmirror`, modeled on `play_music`:

1. `orchestrator/tools.py`: tool def (no args) + handler → `tool_service
   POST /magicmirror/update`.
2. tool_service: SSH via key 1 (`asyncssh` or subprocess `ssh -i … pi@mm`),
   capture exit code + last lines of output. Timeout ~60 s (git pull + pm2
   restart can be slow on a Pi). **Verify the effect** (tenet 6): the update
   script's last act is printing a verifiable marker (new git rev +
   `pm2 status mm` line); the route parses it rather than trusting exit 0.
3. Terminal-tool speech: "The magic mirror is updating — it should refresh
   in about a minute." / failure: specific cause (unreachable host, script
   failure tail) per the verbose-errors standard, `ERROR_STYLE`-aware.
4. `mm-update.sh` on the Pi (repo keeps a reference copy under
   `magicmirror/`): `cd ~/MagicMirror && git pull && npm install --omit=dev
   … && pm2 restart mm && echo "KRONK-OK $(git rev-parse --short HEAD)"`.
   Exact contents to be settled with the operator — depends on how the
   mirror is currently deployed/updated by hand.

**Operator steps ([YOU]) — tier 1 is built; this is all that remains:**

1. Settle the Pi's hostname (see naming flag above) and set
   `MM_SSH_TARGET=kronk@<pi-host>` in `.env` if it isn't `mirror.local`.
2. On the Pi, run the setup block from the header of
   `magicmirror/mm-update.sh` (create user kronk, install the
   authorized_keys forced-command line, the sudoers drop-in, and the script
   itself). The public key to paste is
   `secrets/mm/kronk-mm-update.pub` (private key already generated,
   gitignored, mounted read-only into tool_service).
3. Sanity-check from the kronk box:
   `ssh -i secrets/mm/kronk-mm-update kronk@<pi-host> status`
   → expect `KRONK-OK status rev=… version=… pm2=online`.
4. Say "update the magic mirror" — or dry-run first via
   `curl -X POST localhost/api/… /magicmirror/update` through tool_service.

## Tier 2 — devops agent on the mirror (after the model decision)

`devops`-agent tool `magicmirror_ops(verb, args?)` against key 2's
dispatcher. NOT a terminal tool — diagnosis is a loop (status → logs →
restart → verify). The repeat-call guardrail and round budget already bound
it. Scope examples: "why is the mirror black?", "restart the mirror",
"what modules are running?". Config *editing* (`config-set`) ships last,
after read-only verbs prove reliable.

Content flow (calendar/weather panels on the mirror) is explicitly **out of
scope** — that's the context-cache feature (ROADMAP item 5); the mirror
would pull from a Kronk endpoint, no SSH involved.

## Step 0 — model bench for the devops agent (running now)

Question: what should `DEVOPS_AGENT_MODEL` be for tier 2 (and coding/devops
generally)? Incumbent: `devstral-2512-q4` = Devstral Small 2 (Dec 2025,
24B dense). Challengers chosen 2026-07-06 (research in the session log;
GLM-5.2/Devstral-2-123B/Kimi/DeepSeek ruled out — no viable quant or dense
too slow at 256 GB/s):

| Model | Shape | Hypothesis |
|---|---|---|
| Qwen3-Coder-30B-A3B Q4 | MoE, 3B active | ~2-3× devstral throughput on this bandwidth-bound box; purpose-built tool calling. Risk: custom function-call format vs llama.cpp/LiteLLM template. |
| Qwen3.6-27B Q4 | dense, 2026-04 | Community "sweet spot" for local agentic coding; MTP drafter available later (+1.4-2.2×). |
| Devstral Small 2 Q4 | dense 24B | Incumbent / null hypothesis. |

Method (per the Gemma QAT/MTP precedent, `docs/model_results.md`): bench
ports (1149x) while production stays up; single-variable; battery =
tool-call reliability 5× through the OpenAI endpoint + devops/MM-flavored
probes (safe SSH verb selection, MM `config.js` module edit, pm2 log
diagnosis, systemd unit debugging) + timing (TTFT, tok/s). Script:
`scripts/devops_model_bench.py`; results → `docs/bench/` +
`docs/model_results.md`. Decision rule: challenger must beat devstral on
correctness, or tie on correctness and win ≥2× on speed. Loser GGUFs get
deleted (disk is finite); winner gets a systemd unit + LiteLLM entry +
`DEVOPS_AGENT_MODEL`/`CODING_AGENT_MODEL` env change.

## Open questions

- Pi user/hostname; how the mirror is updated by hand today (defines
  `mm-update.sh`).
- `asyncssh` dependency vs shelling out to `ssh` from tool_service
  (leaning: subprocess `ssh` — zero new deps, `BatchMode=yes`,
  `ConnectTimeout=5`).
- Does tier 2 need file *reads* beyond logs (e.g. `config-get` returning
  the whole config.js)? Affects dispatcher verb design.
- Voice latency budget: tier 1 must acknowledge immediately and verify in
  the background, or accept ~10-60 s? (play_music polls 8 s; an update is
  longer — likely speak "updating now" after launch + verify marker, don't
  hold the pipe for the full update.)
