# Investigation 2026-09-30 — kitchen Voice PE stutters mid-song

Status: **resolved 07:41 local** by the operator: AP meshing turned off and
the three 2.4 GHz radios moved to channels 6 / 11 / 1. Cause: 2.4 GHz
channel saturation — all three APs on channel 6, and the kitchen puck's
link rate collapsing to 1 Mbps while it carries a ~1 Mbps FLAC stream.

## Symptom

~06:45 local, YouTube Music (Adam Sandler, "Lunchlady Land") on the
kitchen Voice PE (0ac919, 192.168.1.131) via Kronk / MA 2.11.0b2. Audio
stutters as if failing to buffer.

## Evidence (11:00 UTC / 07:00 local)

- Kronk host idle: load 0.56. MA container up 10 days, no errors from the
  YouTube provider or the PO-token helper.
- MA log, kitchen PE only (MAC 20:F8:3B:0A:C9:19), 13 events in the
  06:45–07:00 window, none earlier in the last 3 h:
  - `Slow send_bytes` stalls: 1.8 s, 2.6 s, 3.9 s, 4.8 s, 7.8 s, **12.8 s**
    for ~10 KB audio chunks.
  - `Late binary … skipping 1 chunk(s)`, late by up to 9.3 s — dropped
    audio, the audible stutter.
  - Sendspin `WebSocket closed, close_code=1006` (abnormal close) five
    times in five minutes, each followed by a reconnect.
- Basement PE (0ab117): zero Sendspin warnings in the same window (not
  streaming).
- ICMP from Kronk (wired), 20–30 pings each:

  | Target | avg | max |
  |---|---|---|
  | router 192.168.1.1 (wired) | 0.4 ms | 1.4 ms |
  | kitchen PE, sample 1 | 1,415 ms | 3,297 ms |
  | kitchen PE, sample 2 | 10 ms | 39 ms |
  | kitchen PE, sample 3 | 2,396 ms | 9,506 ms |
  | basement PE, sample 1 | 96 ms | 151 ms |
  | basement PE, sample 2 | 221 ms | 642 ms |

  No packet loss; latency swings from 2 ms to 9.5 s. Normal for these
  pucks is ~2–5 ms.

## Reading

Wired path clean, server idle, both Wi-Fi pucks slow at once (the idle
basement one too) — the Wi-Fi medium or access point, not MA and not the
kitchen puck alone. Multi-second queueing with zero loss looks like
airtime saturation / bufferbloat on the AP (a large transfer on the same
radio) or heavy interference, not weak signal.

Hypotheses not yet tested:
1. A heavy Wi-Fi client (a console game download — a Switch 2 download
   speed problem was raised the same week) saturating the radio.
2. AP firmware/state (uptime, channel change, DFS event).
3. 2.4 GHz interference in the kitchen (microwave at breakfast time fits
   06:45; would not explain the basement puck).

Differs from INCIDENT_2026-09-10 (basement PE, wedged device state,
cleared by power cycle): there, only the one puck misbehaved.

## UniFi evidence (07:07–07:27 local)

Read-only controller account `kronk` created by the operator (`.env`
`UNIFI_USER`/`UNIFI_PASSWORD`); verified before use: role `custom`, every
app permission `readonly` (access, network, protect,
system.management.user, talk), site role `readonly`, not owner/super admin.

Three U6-Mesh APs (fw 6.8.2.15592, uptime 3,155 h, wired 1 Gbps uplinks)
behind the Dream Machine Pro. **All three 2.4 GHz radios on channel 6,
20 MHz**; 5 GHz on 149/40, 149/40, 48/80.

Snapshot 07:07:

| AP | 2.4 GHz utilization | AP's own TX share | 2.4 clients |
|---|---|---|---|
| living-room | 97% | 81% | 7 |
| upstairs | 89% | 12% | 2 |
| basement | 95% | 11% | 8 |

Kitchen PE (0ac919): on **living-room** AP, 2.4 GHz, signal −56 dBm,
**AP→puck rate 1 Mbps** (lowest legacy rate), 21% lifetime retries
(5.1 M / 24.2 M attempts), 25 GB carried (the music). Basement PE
(0ab117): basement AP, −69 dBm, 58 Mbps, 27% retries. Switch 2
(192.168.1.120): **5 GHz** ch 149, −38 dBm, 0.15 GB — not the cause.
Top-talker hypothesis rejected: no client moving meaningful data at the
time besides the puck.

Snapshot 07:27: living-room 2.4 GHz 35% util, puck rate back to 72 Mbps —
yet MA logged 26 more stalls 07:01–07:27 (every ~30 s at the end). Rate
adaptation is swinging.

## Reading (revised)

The Voice PE's media pipeline is FLAC 48 kHz stereo (~1 Mbps). When the
living-room AP's rate control drops the puck to 1 Mbps, that one stream
needs roughly all of the channel's airtime (AP self-TX 81%), and every
other 2.4 GHz device on channel 6 — including the other two APs'
clients, since they share the channel — waits. That matches both pucks'
multi-second ping times with zero loss. Contributing: three APs on one
2.4 GHz channel contend with each other; legacy 1–5.5 Mbps rates are
allowed. The Switch hypothesis was wrong.

## Fix options (operator, UniFi console — not applied)

1. Spread 2.4 GHz channels: 1 / 6 / 11 across the three APs.
2. Raise the 2.4 GHz minimum data rate (drop 1/2/5.5 Mbps) so no client
   can be served at 1 Mbps and eat the channel.
One at a time, re-measure between (tenet 9). Each briefly reconnects
2.4 GHz clients.

## Known upstream issues (research 2026-09-30)

- music-assistant/support #6190 (2026-08-26, closed not planned):
  aiosendspin awaits `send_bytes()` on the event loop with no timeout; one
  slow Sendspin client backs up and stalls the whole MA loop ("Slow
  send_bytes" up to 232 s). Same log signature as ours — the Wi-Fi is the
  trigger, this is why a slow puck hurts more than itself. aiosendspin
  PR #431 (merged 2026-09-17) moves encoding off the loop; not confirmed
  in any MA 2.11 beta.
- Voice PE firmware sets no `power_save_mode`, so ESP32 light modem sleep
  applies (variable latency; esp-idf #9766, esphome #6364 on
  listen_interval vs DTIM). Community mitigation, not vendor guidance:
  DTIM 1 on the 2.4 GHz SSID, or a custom build with `power_save_mode: none`
  (ROADMAP 20). Controller showed the kitchen PE `powersave=False`, the
  basement PE `True`.
- home-assistant.io #40632: a Voice PE that worked on channel 11 and not
  channel 6 — anecdotal support for moving off the shared channel 6.
- Wedge bugs open on 26.6.0 with no maintainer replies: #638 (stopping a
  ringing timer with the wake word leaves the announcement pipeline stuck
  until reboot — plausible link to the 2026-09-14 ducking event), #643,
  #613, #556.
- Firmware 26.9.0 (2026-09-17) is out; nothing in it touches Wi-Fi,
  Sendspin, stutter, ducking, or the wedges.

## Fix and verification (07:41–07:53 local)

Operator turned off meshing on the APs (at least one had it on; all
uplinks were already wired) and set 2.4 GHz channels living-room 6,
upstairs 11, basement 1. 5 GHz unchanged (149 / 149 / 48).

| | Before | After |
|---|---|---|
| 2.4 GHz utilization (LR / up / base) | 97 / 89 / 95% | 20 / 19 / 14% |
| Kitchen PE AP→puck rate | 1–72 Mbps, swinging | 65 Mbps |
| Kitchen PE ping avg / max | 2.4 s / 9.5 s | 4–9 ms / 17–24 ms |
| Basement PE ping avg | 96–221 ms | 69–76 ms |
| Kitchen Sendspin stalls/closes | 6–14 per 5 min | 6 in the first 2 min after reconnect (07:42–07:44), then **0** through 07:53 |

Basement PE still ~70 ms: it runs Wi-Fi power save (controller
`powersave=True`; kitchen `False`) — modem-sleep latency, not congestion.

## Why it held for a year

2.4 GHz carried only low-rate IoT traffic (solar monitor, litter robots,
vacuum); phones, laptops and consoles are on 5 GHz. Three APs sharing one
channel is fine at that load. Continuous per-puck music streaming (the
first steady, latency-sensitive 2.4 GHz load) is recent; one rate-control
collapse to 1 Mbps turned a ~1 Mbps stream into ~100% airtime on the
shared channel. INCIDENT_2026-09-10 (basement dropouts "fixed" by a power
cycle, which also resets the link rate) may have been an early instance.

## What would have caught it sooner

A standing latency probe on the pucks and AP radio stats next to the MA
logs — see the ROADMAP chore added the same day.

## Leftovers

- 5 GHz: living-room and upstairs APs share channel 149 (10–17% busy); move
  one if 5 GHz ever gets busy.
- Minimum 2.4 GHz data rate and DTIM 1 — not needed so far; next levers if
  stalls return.
