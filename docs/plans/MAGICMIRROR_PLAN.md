# MagicMirror agent — Plan

## General ops agent — Phase A SHIPPED + verified live 2026-07-11

`remote_exec` on the devops agent (devstral), read-only, end-to-end proven:
"what is the uptime of the magic mirror?" → deterministic route to **devops**
→ devstral composed `uptime`, ran it via `/ops/exec`, answered *"The magic
mirror has been up for 11 hours and 41 minutes."* `sudo reboot` refused with
422; audit log captured allowed + refused. Files: `tool_service/ops.py`
(registry + classifier + audit), `/ops/exec` route, `orchestrator/tools.py`
`remote_exec`, devops agent wiring, routing split in `routing.py`,
`ops/hosts.json` (gitignored; env-fallback for the mirror). 20 new tests.
Phase B (mutations + confirmation gate) is the next increment; the spec
below stands.

## General ops agent — spec (remote_exec + host registry + routing) — 2026-07-11

Motivating trace: today "what's the uptime of the magic mirror?" dead-ends.
The router sends it to `home` (named entity) whose only mirror tool is the
*mutation* `update_magicmirror` (best case "can't do that", worst case a
misfired update), or to `devops` (right agent) which has only web_search/
fetch_url and no cross-machine reach. No read/diagnostic path to the mirror
exists. This spec adds one.

### Host registry (tool_service)

`ops/hosts.yaml`, **bind-mounted** (not baked — hot-add a host with no
rebuild, same as the update script):

```yaml
magicmirror:
  ssh_target: pi@magicmirror-01.home.hippiehouse.net
  key: /keys/kronk-mm-update
  sudo: true
  description: "MagicMirror Raspberry Pi — Electron kiosk on :8080"
```

The registry IS the opt-in boundary: a host is unreachable unless listed;
`sudo` is per-host. **The kronk box itself is never in the registry** — the
agent cannot target its own host (that's where an injection could reach the
finance DB). The existing `MM_SSH_TARGET`/`MM_SSH_KEY` fold into the
`magicmirror` entry; the update flow migrates to a registry lookup.

### `remote_exec` — two layers

- **tool_service `POST /ops/exec` {host, command}** → registry lookup → SSH
  run → `{exit_code, stdout, duration}`. The classifier, audit, and (later)
  the confirmation gate live here — server-side, deterministic, not the
  model.
- **orchestrator tool `remote_exec(command, host="magicmirror")`** on the
  `devops` agent — NOT a terminal tool; it's a loop (propose → run → read →
  iterate), bounded by the agent's existing round budget + repeat-call
  guardrail. One host today, so `host` defaults; multi-host later surfaces
  the registry to the model.

### Command classifier (the safety core) — staged

- **Phase A — read-only only (ship first, safe to enable immediately).**
  A command runs iff: every program in it is on a **read-only allowlist**
  (`uptime, cat, ls, tail, head, grep, journalctl, systemctl [status|
  is-active|show|list-units], git [status|log|rev-parse], df, free, ps,
  node --version, npm ls`, …) AND it contains no redirects (`>`,`>>`),
  command substitution (`$(`, backticks), background (`&`), or `;`. Pipes
  between allowlisted read programs are allowed (`ps aux | grep node`).
  Anything else → refused: "that's not a read-only command; mutations
  aren't enabled yet." Zero mutation risk, so no confirmation needed — this
  is the whole "prove the devstral loop" phase.
- **Phase B — mutations + destructive gate.** Enable mutating commands; a
  **denylist tier** (`reboot, shutdown, rm, dd, mkfs, userdel, passwd,
  dpkg --purge, apt remove/purge`, redirects into `/etc|/boot`, recursive
  chmod/chown on system paths) requires confirmation — voice pending-action
  with a specific confirm phrase, or staged to the chat UI for the truly
  irreversible (per the pivot section's asymmetric-channel rule).
- **Phase C — richer loops** (widget prototyping: write a module file →
  restart → verify render → iterate; git-reversible, low-stakes).

### Audit (non-negotiable, from phase A)

Every exec → a Langfuse span (as tools already do) + an append-only
`data/ops_audit.log` line: ts, host, command, exit, output-length. Trust
after the fact requires reconstructing what ran on a managed host and when.

### Routing split — "magic mirror" is now multi-agent

home owns the fast named-safe *update* (gemma terminal tool); devops owns
arbitrary *diagnostics/ops* (devstral loop). A 4B router can't split intent
on the shared entity, so use **deterministic route rules** (the
weather-shortcut precedent), checked before the LLM router:

- `update|upgrade … magic mirror` → **home** (fast path, unchanged).
- any other `magic mirror` mention → **devops**.

Narrow home's `routing_hint` to "update the magic mirror" (drop the bare
"magic mirror") so a shortcut miss doesn't pull diagnostics into home.
Add `remote_exec` to the devops agent + a prompt line: mirror queries run on
host "magicmirror", read-only for now.

### Phasing / first proof

Phase A is the buildable, immediately-safe increment and the real tracer
for the devstral ops loop (uptime, logs, service state — the model
*composes* the command, unlike update). "Uptime of the mirror" is the
canonical first test; its own ambiguity (host `uptime -p` vs service
`systemctl --user show -p ActiveEnterTimestamp magicmirror`) is exactly the
judgment we're watching, and read-only makes a wrong guess harmless.

### Open decisions for the operator

1. Registry format/location: `ops/hosts.yaml` bind-mounted (proposed) vs
   env vars vs `secrets/`. (It's config, not secret — keys stay in
   `secrets/`.)
2. Phase-A pipe policy: allow pipes between allowlisted read programs
   (proposed, more useful) vs single-program-only (simpler, stricter).
3. Model: keep `remote_exec` on `devops` (devstral) as benched, confirmed.
4. Does phase A ship alone first (prove read-only), or bundle B's gate?
   (Recommend A alone — smaller, safe, immediately useful.)

---


Status: **tier 1 live through `status` (read-only), 2026-07-11.** General
key + scp-staging transport verified end-to-end from the container against
the real Pi (`magicmirror-01.home.hippiehouse.net`): rev b742e839b, MM
2.34.0, service active. The mutating `update` verb is built and staged but
NOT yet run live — awaiting operator go (it restarts the kiosk).

Live-probe discoveries (2026-07-11), folded into the script:
- **MM runs as a systemd USER unit `magicmirror.service`** (Electron kiosk
  on :8080), **not pm2** — the script's restart/verify rewritten to
  `systemctl --user restart/is-active`, with `XDG_RUNTIME_DIR=/run/user/UID`
  exported so `--user` works over non-login SSH.
- node v22 (`node --run install-mm` supported); one untracked module data
  file present (doesn't block — update guard only trips on *tracked* edits).
- Pi OS `pi` has NOPASSWD sudo by default → **this key is root on the Pi**
  the moment it can run commands (accepted; bounded by reflashability).
Step 0 (model bench) DONE same day — results in `docs/model_results.md`
(2026-07-06 section): qwen3.6-27b ruled out; **operator kept devstral**
(2026-07-06; also confirmed still Mistral's newest open coding model —
Codestral 2508 is the FIM line) and enabled its unit; qwen3-coder-30b-a3b
GGUF kept on disk for a future revisit.

## Direction pivot (2026-07-11) — general ops agent, not a locked verb

The real target isn't "update the mirror" — it's **an agent that acts on the
mirror for arbitrary tasks**: investigate logs, update/reboot the machine,
prototype new MM widgets. "Update the magic mirror" is the **tracer round**
that proves the model. This supersedes the tier-2 forced-command dispatcher
below (kept for history).

Consequences, all operator-accepted:

- **The key becomes a general SSH key with sudo** (NOPASSWD) — no forced
  command. The Pi keeps only auth details; all logic lives on Kronk. The
  forced-command lockdown is dropped: its job was bounding *the key's
  capability*, but with an agent in the loop the risk to contain is **the
  loop and its inputs**, not the key. First manual Pi change is the last:
  replace the `command="…"` line in `authorized_keys` with a plain key
  line. Risk is bounded by the box itself — single-purpose, trusted LAN,
  holds nothing irreplaceable, reflashable in ~15 min. That bound is
  load-bearing: this pattern must be re-decided before it points at any
  host where the worst case isn't "re-image and move on" (the kronk box
  itself, a NAS with data).
- **Top threat is prompt injection, not agent clumsiness** (the lethal
  trifecta: reads untrusted content — logs, fetched pages, downloaded
  modules — *and* holds a root shell). Can't be eliminated for an
  autonomous root agent without a real sandbox; it's *bounded* by the low
  value of the box + audit + the destructive-command gate + human-in-loop.

### Architecture (all Kronk-side)

- **Host registry** — `name → {ssh target, key, sudo?, description}`.
  Cross-machine reach and the sudo grant are per-host opt-ins, so adding a
  new machine is a deliberate act, never a silent escalation. The agent
  points only at *remote managed hosts*, **never the kronk box itself**.
- **One `remote_exec(host, command)` tool** on the `devops` agent
  (devstral) — a real loop (propose → run over SSH → read stdout/stderr/
  exit → iterate), lives in tool_service (has the SSH plumbing + key).
- **Destructive-command confirmation gate** — a deterministic classifier
  flags the irreversible tier (`reboot`, `shutdown`, `rm`, `dd`, `mkfs`,
  `userdel`, `dpkg --purge`, redirects over system files). Read-only
  investigation (the 95%) flows freely; the flagged tier pauses. Catches
  both injection and clumsiness at near-zero friction — same model as
  Claude Code's own permission tiers.
- **Full audit, non-negotiable** — every command + output to Langfuse
  spans and a durable log. Trust after the fact requires reconstructing
  "what ran on the mirror and when."
- **Named-safe operations stay first-class.** The scripted `update`
  (backup → verify → rollback) is *better* than the agent freehanding
  `git pull`, so the agent invokes the structured verb for updates and
  drops to raw shell only for the genuinely arbitrary. Named-safe +
  arbitrary-shell fallback, not arbitrary-shell-for-everything.

### Confirmation over voice (risk is a property of the operation)

- **Named, reversible ops** (update, restart-and-verify, investigate) are
  **self-authorizing when requested by name** — no confirmation prompt.
  Saying "update the magic mirror" *is* the authorization; safety comes
  from the backup+verify+rollback structure, which beats a mis-hearable
  spoken "are you sure?". Fast path: say it → "on it, updating now" → walk
  away.
- **Arbitrary/irreversible commands the agent composes** need input. The
  voice path has conversation continuity (HA conversation_id → Kronk
  session), so a two-turn confirm works: the agent stashes a **pending
  action** (command + host + short expiry) keyed to the conversation, ends
  the turn, and the next utterance either matches a **specific confirm
  phrase** (not bare "yes" — a mis-heard yes must not authorize a reboot)
  or cancels it; the expiry disarms a forgotten pending action.
- **Asymmetric channels for asymmetric risk:** the truly dangerous,
  irreversible tier is **staged and bounced to the chat UI** for a
  deliberate approve (exact command visible, real button) rather than
  confirmed by a low-precision voice channel.

### Announce path — BUILT + verified 2026-07-11

`assist_satellite.announce` confirmed live on the kitchen Voice PE
(`assist_satellite.home_assistant_voice_0ac919_assist_satellite`; operator
heard a test announce). Wired into tool_service:
- `_ha_announce(message, satellite)` — reusable primitive, HA REST via the
  same HA_TOKEN path as timers/music; **non-fatal** (a failed announce is a
  log line; `/magicmirror/status` stays the source of truth).
  `ANNOUNCE_SATELLITE` env; future timer/proactive alerts reuse it.
- `_mm_update_speech(ok, fields, detail)` renders the KRONK result to one
  sentence. Success: "…updated to version X, N modules refreshed" (+ a
  warning clause if some modules failed). Failure: "…failed at the <step>
  step. I kept a backup and left it as it is — ask me to roll it back."
- Called at the end of `_run_mm_update`, after the outcome is persisted.
- **Locked decisions (operator):** no auto-rollback — a failed update keeps
  the bad state; the announce invites an explicit rollback, restore happens
  only on request.

Below is the original design note, kept for context.

### Announce path — original design note

The async ack means the interesting question is not "how do you confirm the
start" but **"how do you learn it finished."** Target UX: say "update the
magic mirror" → "on it, updating now" → walk away → a couple minutes later
the **speaker announces the outcome**: "the mirror updated to version 2.32"
or "the mirror update failed — I rolled it back; ask me why." Mechanism:
HA's `assist_satellite.announce` pushes audio to the Voice PE outside the
conversation flow (verified working in isolation during the timer research
— `docs/VOICE_SETUP.md`). Kronk's background update task, on completion,
calls an HA announce endpoint (via tool_service, same HA_TOKEN path as
timers/music) with the KRONK-OK/KRONK-FAIL result rendered to one spoken
sentence (ERROR_STYLE-aware).

- **Shared dependency with the timer/proactive work** (ROADMAP item 3 +
  Proactive Kronk): the announce path is the same `assist_satellite.announce`
  plumbing native HA timers need. Sequence them together — build the announce
  primitive once, use it for timer chimes, update completions, and future
  proactive alerts.
- **Interim before announce exists:** say it → "updating now" → outcome on
  `GET /magicmirror/status` / ask "did the mirror update work?" later. The
  announce path is what makes the walk-away UX complete.

### Staging the script from Kronk (no manual file drops, ever)

Operator constraint: never hand-drop files on the Pi. Since Kronk has SSH it
has scp — so tool_service **stages the canonical `magicmirror/mm-update.sh`
from the repo on every mm operation, then runs it**:

1. `scp -i <key> mm-update.sh pi@host:~/kronk/mm-update.sh` (idempotent;
   always refreshes to the current repo version — kills version drift, the
   main friction of the old "manual scp when the script changes" model).
2. `ssh -i <key> pi@host '~/kronk/mm-update.sh <verb>'`.

The script persists at a known path (debuggable; `rollback` can reference
it later). Zero-footprint alternative: `ssh pi@host 'bash -s -- <verb>'
< mm-update.sh` streams the script over stdin and leaves nothing on disk —
mention as the option if we'd rather keep the Pi pristine. Either way the
operator never touches the Pi after the one-time authorized_keys line.

- **Requires the authorized_keys pivot above** (a forced-command key
  can't scp — it can only run the one pinned script). Staging is a direct
  consequence of, and consistent with, the general-key direction.
- **Scripts are bind-mounted, not baked** (operator directive 2026-07-11):
  `./magicmirror:/magicmirror:ro` on tool_service (done), `MM_SCRIPT=
  /magicmirror/mm-update.sh`. Editing the script takes effect with no
  rebuild or restart — same rationale as `litellm/config.yaml`. The
  container reads MM_SCRIPT and scp's it up per operation.
- The script becomes a **known-good procedure Kronk owns and pushes**, not
  the Pi's authorization policy — that role moved to the destructive gate +
  audit on the Kronk side.

---

## Decisions (2026-07-06, operator + build)

- **SSH login is user `pi`** (corrected 2026-07-11 — the earlier
  kronk-user + sudoers-as-drew design assumed the SSH login wasn't the MM
  owner; it is, so both the separate user and the sudoers grant are gone).
  The forced command `command="/home/pi/kronk/mm-update.sh"` on pi's
  authorized_keys is the entire authorization boundary: this key can run
  the three allowlisted verbs and nothing else, pty-less, no forwarding.
  Assumption to verify at first live test: MagicMirror + pm2 run as pi
  (stock install; `MM_DIR` defaults to `$HOME/MagicMirror`).
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

**Operator setup — DONE 2026-07-11:** general key installed on the Pi
(`authorized_keys` line without the forced command), `MM_SSH_TARGET=
pi@magicmirror-01.home.hippiehouse.net` in `.env`, key at
`secrets/mm/kronk-mm-update`, script bind-mounted + staged by tool_service.
`status` verified live. Nothing left to install.

**Remaining: the mutating update.** Say "update the magic mirror" (or
`POST /magicmirror/update`). It preflights (status), acks, then in the
background: backup → `git pull --ff-only` → `node --run install-mm` →
**update third-party modules** → `systemctl --user restart magicmirror` →
verify active. Rollback verb restores the newest backup. First live update
is the operator's call — it restarts the running kiosk.

**Module updates (operator requirement 2026-07-11).** Each third-party
module is its own git repo under `modules/`; `update` now pulls each and
runs `npm install` where a `package.json` exists, best-effort — one
module's failure never aborts the core update. **Skips** (never clobbered):
core `default`, `*.bak` backups, non-git dirs, and any module with
*tracked* local edits (the full-tree backup makes rollback total anyway).
Results ride the KRONK-OK line (`mods_ok/skipped/failed` + `mod_failures`).
This Pi: `status` reports **8 updatable modules** (10 dirs − 2 `.bak`);
MMM-RAIN-RADAR is dirty so it'll be skip-protected at update time. The
bash module loop is validated live (like the rest of the script); the
Python transport that parses its result is unit-tested.

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
