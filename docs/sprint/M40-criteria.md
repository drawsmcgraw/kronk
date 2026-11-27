# M40 pass criteria: the PO-token server matches its plugin (2.0.0 → 2.0.2)

Source: `docs/incidents/INCIDENT_2026-11-27_ytmusic_challenge_solver.md`,
"Also still open". Drew, 2026-11-27: "add the p0-token server bump as a
work item to the queue as well."

Handoff line: `bgutil-pot-provider` runs the same version as the
`bgutil-ytdlp-pot-provider` plugin Music Assistant installs, and MA stops
warning about the mismatch on every YouTube Music load.

Plan doc: this file. Tier 1 (an image swap; the comparable case in
WORKFLOW's table is a config or model swap). No design review.

## What the investigation found (coordinator, 2026-11-27)

- `docker-compose.ma.yml` pins `brainicism/bgutil-ytdlp-pot-provider:2.0.0`
  (container `bgutil-pot-provider`, bridge network, 127.0.0.1:4416).
- MA 2.11.0b11 installs the plugin at runtime, unpinned and upgraded on
  each provider load (`PACKAGES_TO_INSTALL` in `providers/ytmusic`); it is
  2.0.2 today. MA logs on each load:
  `[pot:bgutil:http] The provider plugin and the HTTP server are on
  different versions ... (plugin: 2.0.2, HTTP server: 2.0.0)`.
- Upstream releases: 2.0.2 (2026-10-07) is the latest. Docker Hub has
  `2.0.2`, the same image as `2.0.2-node` (digest begins
  `sha256:d7d34108b76e`), and a separate `2.0.2-deno` variant. The current
  pin is the node image; 2.0.2 keeps that variant.
- The mismatch did not cause the 2026-11-27 outage: a probe with plugin
  2.0.2 against server 2.0.0 resolved streams once yt-dlp was 2026.11.23.
- `scripts/upgrade_rules.toml` lists this component as excluded, with MA;
  its `match` reads the compose line and needs no change for a tag bump.

## Plumbing (coordinator's decisions)

1. Pin `brainicism/bgutil-ytdlp-pot-provider:2.0.2`, with the full digest
   in the compose comment (read from the pulled image).
2. Recreate only the `bgutil-pot-provider` container
   (`docker compose -f docker-compose.ma.yml up -d bgutil-pot-provider`).
   MA is not restarted: the HTTP server is stateless and MA calls it per
   request. Check every MA player first and do it only when nothing plays
   on YouTube Music (CLAUDE.md: name the step before doing it).
3. Rollback: the 2.0.0 tag.

## Criteria

1. `docker ps` shows `bgutil-pot-provider` on 2.0.2, up; the loopback port
   answers (`curl -s http://127.0.0.1:4416/ping` or the route the image
   documents), quoted.
2. A YouTube Music provider reload (`scripts/music_provider_canary.sh`)
   passes, and MA's log for that load has no `different versions`
   warning, quoted.
3. One YouTube Music stream resolves through MA's yt-dlp options inside
   the MA container (the probe in the incident doc's Verification), format
   quoted.
4. `./scripts/run_tests.sh` passes (the compose file and upgrade rules are
   read by the suite's upgrade tests); last line quoted.

## Out of scope

- Pinning the plugin version inside MA (MA installs it; that is upstream's
  choice).
- Taking the component out of the upgrade rules' exclusion.

## Rulings
