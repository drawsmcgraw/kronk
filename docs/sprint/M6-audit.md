# M6 audit: what is listening on the Kronk host

Sprint coordinator, 2026-11-17 03:40 UTC. In the first sprint M6 is an audit
only (plan, section "M6. Close LAN exposure"). Nothing was changed: no bind
address, port mapping, firewall rule or service. Method: `ss -tlnp` and
`ss -ulnp` as the unprivileged user (process names are visible only for the
user's own processes), `docker ps` port mappings, `ss` inside the two
host-network containers, and HA's config entries read from
`.storage/core.config_entries`. No scan and no probe of any other host.
`sudo` and `ufw status` need a password and were not used, so the firewall
state is not known.

Guest Wi-Fi can reach this host (Drew, 2026-11-15), so every listener on
"all" below is reachable by guests.

## TCP listeners

| Port | Bound to | Owner | Real clients | Since the 2026-11-15 audit |
| --- | --- | --- | --- | --- |
| 80 | all | nginx (compose publish) | LAN browsers; HA at `http://localhost/voice` and `/dev/voice` | Same. Stays open by Drew's decision |
| 3000 | all | Grafana (compose publish) | LAN browsers | Same. Stays open by Drew's decision |
| 8002 | all (IPv4) | LiteLLM, host network | Bridge containers through the host gateway | Same. An open model endpoint for the LAN |
| 10300 | all (IPv4) | Whisper, host unit (`python`, pid 2809) | HA only, entry host `localhost` | Same |
| 10200 | all | Piper (compose publishes `10200:10200`) | HA only, entry host `localhost` | Same |
| 8123 | all | Home Assistant | LAN, pucks, phones | Same. Needed |
| 8095, 8097, 8927 | all | Music Assistant, host network | LAN players; HA at `http://192.168.1.179:8095` | Same. Needed |
| 11434 | all | Ollama (`/usr/local/bin/ollama serve`, pid 3331; unit `enabled`) | None known since the llama.cpp migration | Same. Still starts at boot |
| 3389 | all | xrdp | Drew | Same |
| 18555 | all | go2rtc, started by HA | HA | Same |
| 18554 | loopback | go2rtc | HA | Not in the earlier table |
| 39831 | all | Not identified. Seen from inside both host-network containers with no owning process there; Music Assistant runs a `cliairplay` helper, which is a candidate. Needs `sudo ss -tlnp` to settle | Unknown | Same, still unidentified |
| 22 | all | sshd | Drew | Same |
| 3373 | — | `code-tunnel serve-web` | — | **Gone.** Not listening; Drew killed it (C1) |
| 11438, 11439, 11440 | loopback | llama.cpp ×3 | Local | Same |
| 8003 | loopback | tool_service (compose publish) | Local | Same |
| 8103 | loopback | tool_service-dev (compose publish) | Local | New with M17 |
| 3200, 4416 | loopback | Tempo, PO-token helper | Local | Same |
| 631, 53 | loopback | CUPS, systemd-resolved | Local | Not in the earlier table |

`orchestrator` and `orchestrator-dev` publish no host port; they are reached
through nginx only.

## UDP listeners (not in the plan's table)

1900 and 5353 (SSDP, mDNS: HA, Music Assistant, avahi), 319 and 320 (PTP,
used by AirPlay/Sonos sync), 41641 (the usual Tailscale port; not
confirmed), 53613 (the hot tub monitor, pid 1365776), several high
ephemeral ports, and loopback-only 323 (chrony) and 9010.

## What the findings support

Candidates for change, none made. Each is one listener, independent of the
others, and reversible:

| # | Change | Why it is safe | Check after |
| --- | --- | --- | --- |
| 1 | Whisper: bind `127.0.0.1:10300` in `wyoming-whisper.service` | Its only client is HA, on host networking, configured as `localhost` | Speak to a puck; STT works |
| 2 | Piper: publish `127.0.0.1:10200:10200` in `docker-compose.yml` | Same: HA only, `localhost` | A spoken answer is heard |
| 3 | LiteLLM :8002: a master key, or a host firewall rule limiting 8002 to loopback and the Docker bridges. Not a loopback bind (bridge containers reach it through the host gateway) | Clients are the orchestrator pair and tool_service | `/api/chat` and `/dev/api/chat` answer |
| 4 | Ollama :11434: stop and disable the unit | No client since the llama.cpp migration; it is an open model endpoint that starts at boot | Nothing uses it; confirm with a day of normal use |
| 5 | Identify :39831 | Unknown owner on all interfaces | `sudo ss -tlnp \| grep 39831` |

Not candidates: 80 and 3000 (Drew's decisions), 8123 and the Music
Assistant ports (needed by LAN devices), 22 and 3389 (Drew's access).

Each change in rows 1 to 4 is Drew's go, one at a time, with a voice check
after each, as the plan's gate requires.
