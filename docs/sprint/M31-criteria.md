# M31 pass criteria: retire devstral

Source: Drew, 2026-11-24: "are we actually using devstral? … I think
gemma 4 is actually better suited for that. any reason we should keep
devstral? Freeing up 20+GB of memory sounds useful." and, on the
coordinator's answer: "retire devstral. No need to rerun the bench, just
point both agents to gemma."

Handoff line: the coding and devops agents run on `gemma-4-e4b`, the
devstral server is stopped and gone from the catalog, and about 34 GiB of
GPU memory is free.

Plan doc: this file. No design review (a model swap by environment
variable and the removal of one server). No bench, by Drew's order.

## What the investigation found (coordinator, 2026-11-24)

- `llama-devstral-q4.service` (port 11440) is running and holds
  36,518,264,832 bytes of GPU memory (`data/gpu_mem.json`) and 8.8 GB of
  resident memory.
- It serves two agents, by `CODING_AGENT_MODEL=devstral-2512-q4` and
  `DEVOPS_AGENT_MODEL=devstral-2512-q4` in `docker-compose.yml`.
  `metrics.db`: devops 16 calls on it, the last 2026-08-29; coding 22,
  the last 2026-05-30; none in the last 24 hours.
- The bench of 2026-09-14 (`docs/bench/devops_bench_2026-09-14_142134_
  mm-e4b-vs-devstral.md`): `gemma-4-e4b` equal on eight probes of nine,
  2/3 against 3/3 on `journal_reading`, 122.1 tok/s against 14.8.
- Other places that name it: defaults in `orchestrator/agents.py` and
  `orchestrator/settings_view.py` (`devstral-2512`, a name no server has
  had since the Q8 unit went); `litellm/config.yaml`; the reference unit
  `systemd/llama-devstral-q4.service`; `nginx/nginx.conf` `/devstral/`
  (proxies to port 11436, where nothing has listened since the Q8 unit
  was retired); `kronk-context.md`; README; `.env.dev.example`;
  `scripts/upgrade_rules.toml`; tests (`test_m24_catalog`,
  `test_m25_resources`, `test_servers_gpu_mem`, `test_error_style`,
  `test_m9_lock`); two bench scripts.
- The live config directories do not name it.

## Plumbing (coordinator's decisions)

1. Both variables become `gemma-4-e4b` in `docker-compose.yml`, for
   production and for dev, and in `.env.dev.example`. The defaults in
   `agents.py` and `settings_view.py` become `COORDINATOR_MODEL`'s way of
   defaulting (as `RESEARCH_AGENT_MODEL` does), so no default names a
   server that does not exist.
2. Removed: the `devstral-2512-q4` entry in `litellm/config.yaml`; the
   reference unit file; the `/devstral/` block in `nginx/nginx.conf`; its
   rows in `kronk-context.md` and README; its entry in
   `scripts/upgrade_rules.toml` if it has one. Tests that use the name as
   a fixture string for "some model" may keep it; tests that assert the
   catalog change with the catalog.
3. The bench scripts and `docs/bench/` stay as they are: history.
4. The two agents now share Gemma's server with the coordinator, home and
   research. The build agent states the server's slot count and context
   size from `/props` and whether a devops or coding turn at its round
   budget fits; if it does not, that is reported, not tuned here.
5. Not done by the agent: stopping the unit, removing the installed unit
   file, any restart of LiteLLM or production. At promotion the
   coordinator deploys the tag, then stops and disables
   `llama-devstral-q4.service`, moves the installed unit file to
   `~/backups/m31-units-2026-11-24/`, and reads the freed memory.
6. The model file (`/opt/models/mistralai/…Q4_K_M.gguf`, 14.3 GB) and its
   template stay on disk: the way back is the backed-up unit file and one
   revert. Deleting it is Drew's, later.

## Criteria

1. On dev, rebuilt: a devops request and a coding request each answer,
   and `metrics.db` on dev shows the call under agent `devops` / `coding`
   with model `gemma-4-e4b`. The devops request is a read-only check on
   the magic mirror through dev's audited route ("check the uptime on the
   mirror"), and the answer contains what the command returned.
2. With the dev pair on the new settings, no request reaches port 11440
   during those two turns (the server's journal).
3. `/dev/api/servers`, `/dev/resources` and `/dev/services` show two
   model servers plus Talkie and no devstral row; nothing on them reports
   a fault for its absence once the unit is stopped (shown with a
   `gpu_mem.json` and `llama_props.json` fixture without it, in scratch;
   the unit is not stopped by the agent).
4. `scripts/config_check.py` and the deploy plan (`scripts/deploy_tag.sh
   plan` on a scratch tag is not run; read its checks) name no model the
   catalog lacks.
5. `nginx -t` on the edited file in a throwaway container.
6. The suite passes; last lines quoted.

## Rulings

- 2026-11-24, coordinator: tier 1. One test (`M31-e2e.md`, PASSES, no
  blocker, no should-fix, six notes). Promote; deploy before the unit
  stops, as the test advised.
