# Music Assistant — Plan

Adding music streaming on top of the existing voice/HA stack so the Voice PE
(and any other media_player devices HA sees) can play from a streaming
service. Synology NAS music comes later as Phase 6+.

Status: **all phases shipped.** Phases 1–5 2026-06; **Phase 7 (voice
control) 2026-07-03** via a different design than sketched here — see
`../features/voice-music-control.md`; **Phase 6 (Synology NAS music)
2026-09-04** — host-side read-only CIFS mount bound into the container,
no container caps; see the Phase 6 section. Written 2026-05-31.

---

## What Music Assistant actually is

A self-hosted music library + multi-room audio system written in Python and
distributed as a Docker container (or HAOS add-on). It sits between:

- **Music providers** (sources): Spotify, YouTube Music, Apple Music, Tidal,
  Pandora, Subsonic, Plex, Jellyfin, local files / NAS, etc.
- **Players** (sinks): Sonos, Chromecast, AirPlay, Snapcast, Squeezebox,
  ESPHome speakers (this includes the **HA Voice PE**), and HA media_player
  entities in general.

MA handles transcoding, queue management, library indexing, search across
providers, and exposes a web UI on port 8095. The HA integration (built in,
no HACS) surfaces every MA player as a `media_player.*` entity in HA, so
you can control playback from automations, the voice pipeline, or the HA
app.

**Practical mental model:** MA is the "music brain" that talks to streaming
services and pushes audio to speakers. HA is the broker that lets us
control it from automations and (eventually) voice.

---

## Why this is a separate compose stack, not part of `kronk` or `kronk-ha`

Same reasoning as the HA split (2026-05-24, see `../VOICE_SETUP.md`):
- MA's lifecycle is independent of Kronk's orchestrator rebuilds.
- MA requires **host networking** for mDNS/uPnP device discovery — same
  constraint that put HA on host net.
- MA pulls big optional deps (ffmpeg, transcoding binaries); rebuilding
  the kronk stack shouldn't force pulling these.

So: new `docker-compose.ma.yml`, project name `kronk-ma`. Mirrors the
HA stack pattern exactly.

---

## Hardware/network considerations

| Item | Notes |
|---|---|
| **Networking** | `network_mode: host` required (mDNS, uPnP, Chromecast SSDP). Same machine as HA so they're already on the same L2 network. |
| **Ports** | 8095 (web UI), 8097 (audio stream) plus dynamic mDNS/SSDP ports. Free on kronk — current bindings are 80, 8002-8005, 8123, 10200, 10300, 11435-11441. |
| **Caps** | The docs recommend `--cap-add=SYS_ADMIN --cap-add=DAC_READ_SEARCH --security-opt apparmor:unconfined`. These are almost certainly only needed for the **in-container SMB mount** feature (Synology NAS music later). **We'll start without them** and add them in Phase 6 only if needed. Keeps our recent HA-deprivilege hardening philosophy intact. |
| **Volume** | `ma-config:/data` (named volume, like `ha-config`). Holds library DB, cached metadata, login tokens. |
| **CPU/RAM** | Modest. Streaming + transcoding is a few hundred MB RAM and low CPU. |

---

## Streaming service support (the operator-decision lives here)

| Service | MA support | Account needed |
|---|---|---|
| **Spotify** | ✅ Yes | Premium required for full features; Free tier limited |
| **YouTube Music** | ✅ Yes | Works with free account; ads in playback unless YT Premium |
| **Apple Music** | ✅ Yes | Subscription required |
| **Tidal** | ✅ Yes | Subscription required |
| **Pandora** | ✅ Yes | Subscription required (US-only service) |
| **Amazon Music** | ❌ Not supported by MA — verified absent from provider list |

> **Open question for the operator:** which service do you want to start with,
> and do you have an active subscription / credentials ready?

The user mentioned Pandora and Amazon Music. Amazon is out. If Pandora is
the preference, confirm the account; otherwise Spotify or YouTube Music
are the most well-trodden integration paths.

---

## Devices that HA already exposes as media players

From the integrations list we saw during the timer work (HA 2026.5.4):

- `media_player.home_assistant_voice_0ac919_media_player` — your Voice PE
  (the kitchen speaker). Confirmed working as a TTS target.
- Sonos components are loaded in HA (`sonos.media_player`, `sonos.switch`, etc.).
  If you have Sonos speakers on the network, they may already be discoverable.
- Cast components are loaded (`cast.media_player`) — Chromecasts / Cast-capable
  TVs would also appear.

> **Open question for the operator:** Please confirm what shows up in HA
> under **Settings → Devices & Services → Media Players** (or
> `Developer Tools → States → filter "media_player"`). That tells us
> the full target list and what's worth wiring through MA.

For the immediate goal — **play streaming music on the Voice PE** — the
Voice PE alone is enough to validate the chain end-to-end.

---

## Phase plan

### Phase 0 — pre-flight (operator)

Answer the two open questions above:
- Streaming service + active subscription/credentials ready
- Current list of `media_player.*` entities in HA

No code changes needed. Just confirm what's on the table.

### Phase 1 — Music Assistant container up

- New file `docker-compose.ma.yml`, project name `kronk-ma`
- Service: `music-assistant` running `ghcr.io/music-assistant/server:latest`
  on host net, with `ma-config:/data` volume
- **No** elevated caps yet (we'll add them only if Phase 6 needs them)
- Bring up: `docker compose -f docker-compose.ma.yml up -d`
- Verify: `http://kronk:8095` loads the MA web UI

### Phase 2 — HA ↔ MA integration

- In HA UI: Settings → Devices & Services → Add Integration → Music Assistant
- Point at MA URL (`http://localhost:8095`, since HA is on host net)
- Verify MA players appear as HA `media_player.*` entities

### Phase 3 — wire the Voice PE as a target

- The Voice PE's media_player should be auto-discoverable to MA via mDNS
  (it's an ESPHome device) or appear via the HA-MA bridge
- Verify by selecting the Voice PE as a target in MA's web UI

### Phase 4 — connect the chosen streaming service

- In MA UI: Settings → Providers → Add Music Provider → pick the operator's
  chosen service
- Auth flow varies — Spotify is OAuth, YouTube Music historically used
  cookie-extraction (look up current method), Pandora is password
- Verify: search for a song, see results

### Phase 5 — play music end-to-end

- From MA UI, queue a track and route to the Voice PE
- Verify audio plays through the kitchen speaker
- Verify play/pause/next from HA UI also works (proves the integration is
  wired both ways)

### Phase 6 — Synology NAS music (decided + built 2026-09-04)

Options weighed (operator conversation 2026-09-04): (1) host mounts the
share, read-only bind into MA, MA's local filesystem provider; (2) same
but Docker's volume driver does the mount — fails MA startup when the
NAS is down; (3) Navidrome/Plex/Jellyfin on the Synology, MA as an HTTP
client — cleanest boundary, one more service to run and pin; (4) MA's
in-container SMB/NFS provider — needs `SYS_ADMIN` + `DAC_READ_SEARCH` +
AppArmor off on a host-networked container; rejected again on tenet 10.
**Chosen: (1) over SMB** — the operator created a read-only NAS user
`kronk`; SMB authenticates per user (NFS would have keyed on kronk's IP,
which is DHCP). NFS vs SMB is otherwise a wash for one Linux reader.

Design:
- Host: `cifs-utils`; credentials root-only at `/etc/kronk/nas-music.cred`
  (never in the repo); fstab `//atlas.local/music → /mnt/nas-music`
  read-only (SMB version negotiated — the Synology refused a pinned 3.1.1, kernel log "Dialect not supported by server"), `ip=192.168.1.14` so the mount never waits on mDNS at boot,
  `nofail,_netdev` so a NAS outage never blocks boot (the dir is just
  empty until `sudo mount /mnt/nas-music`).
- Container: bind `/mnt/nas-music → /media/nas`, `read_only`,
  propagation `rslave` so a later host (re)mount shows up without a
  restart (Docker's default private propagation would hide it).
- Three read-only layers: NAS user, mount, bind. No container caps.
- MA: "Filesystem (local)" provider at `/media/nas` (added in MA's UI).
- Known cost: CIFS metadata is slower than NFS — the first library scan
  of a large collection takes longer; playback unaffected.

Steps: operator runs the sudo block (package, credentials file with the
password filled in, mount point, fstab line, first mount) → compose
`up -d music-assistant` → verify `/media/nas` lists the library from
inside the container → provider in MA UI → play a NAS track on a
satellite by voice → docs.

Built 2026-09-04: first mount failed with kernel "Dialect not supported
by server" — the Synology's maximum SMB protocol was below SMB3; the
operator raised it to SMB3 and the mount negotiated 3.1.1 (the fstab
line pins no version). Verified inside the container: 250 entries at
`/media/nas`, `touch` fails with "Read-only file system". The share
exposes Synology's `#recycle` folder — restrict the recycle bin to
administrators on the NAS (Shared Folder → Edit → Recycle Bin) so MA
never scans deleted files.

### Phase 7 (later, much later) — Kronk voice control of music

"Hey Kronk, play some jazz" → Kronk routes to a new `music` agent →
agent calls MA's REST API to queue/play → audio comes out. This is a
Kronk-side refactor that depends on us having MA working first. Not
in scope for the initial bring-up.

### Bonus — multi-room / push to other devices

Once the Voice PE chain works, the same MA UI lets you select any
target (Sonos, Cast, Snapcast group, etc.). No additional setup beyond
the Phase 2 integration. The "bonus points" the operator mentioned
are essentially free once Phase 1-5 works.

---

## Risks & open items

- **The cap-add story**. The MA docs recommend `SYS_ADMIN` + `DAC_READ_SEARCH`
  + apparmor:unconfined, but I couldn't verify *why* from the docs site
  (404'd on the deep link). Strong inference: these are for the in-container
  SMB mount feature. We'll start without them and add only if Phase 6 (NAS)
  actually needs them. If MA refuses to start without them, we'll
  course-correct then.
- **YouTube Music auth** has historically required cookie extraction (running
  a browser extension to capture session cookies and pasting them into MA).
  This is annoying but not blocking. Newer MA versions may have OAuth; need
  to verify in Phase 4 once we know the operator's pick.
- **Voice PE as a music player vs. assist target.** The Voice PE is
  primarily designed for voice assistant audio (short TTS responses), not
  long-form music playback. Audio quality, volume, and buffering should be
  OK for casual listening but it's not a Sonos. Manage expectations.
- **Streaming service ToS**. Some providers (especially YouTube Music with
  free accounts) technically don't permit third-party clients. Personal /
  household use is generally fine in practice, but be aware.

---

## Operator decisions needed before kickoff

1. **Streaming service** to start with (Spotify / YouTube Music / Tidal /
   Apple Music / Pandora). Confirm an active account/subscription.
2. **Current media_player roster** in HA — paste output of HA's
   Developer Tools → States filtered to `media_player.` so we know what
   else is targetable.
3. **Naming/branding** — should the new compose stack be `kronk-ma` (matches
   `kronk-ha`) or something else?

Once those three are answered, Phase 1 is ~30 minutes to bring up MA. Phases
2-5 are mostly UI work in HA and MA with a few API verifications. Should be
done in one sitting (1-2 hours) assuming auth flows behave.
