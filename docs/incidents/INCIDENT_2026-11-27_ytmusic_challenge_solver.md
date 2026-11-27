# INCIDENT 2026-11-27 — YouTube Music playback fails: yt-dlp challenge solver

**Status:** fixed 2026-11-27 15:56 (MA moved to stock 2.11.0b11, which
installs yt-dlp 2026.11.23). One open item: the living-room Bravia (below).

## Symptom

YouTube Music tracks do not play through Music Assistant. MA's log, on every
stream attempt and on each daily provider reload:

```
WARNING: [youtube] [jsc] Error solving 2 challenge requests using "deno" provider: Error running deno process (returncode: 1): error: Uncaught (in promise) TypeError: a.addOnDisposeCallback is not a function
WARNING: [youtube] dQw4w9WgXcQ: Signature solving failed: Some formats may be missing. ...
WARNING: [youtube] dQw4w9WgXcQ: n challenge solving failed: Some formats may be missing. ...
ERROR: [youtube] dQw4w9WgXcQ: Requested format is not available. Use --list-formats for a list of available formats
```

The player URL in every failing request is
`https://www.youtube.com/s/player/b485e332/player_ias.vflset/en_US/base.js`.
First failure in the journal: 2026-11-24 16:19:52 (video `xxRnd7kkIJ4`).

## Versions (running container `music-assistant-server`)

- Image `kronk/music-assistant:2.11.0b5-kronk.9` (stock 2.11.0b5 + Pandora patches)
- yt-dlp 2026.8.19, yt-dlp-ejs 0.8.0, deno 2.9.6
- bgutil-ytdlp-pot-provider plugin 2.0.2; `bgutil-pot-provider` server image 2.0.0

## Root cause

YouTube's player `b485e332` (and others) broke yt-dlp's JS challenge solver:
yt-dlp issue #18123, "n challenge solving failed: a.addOnDisposeCallback is
not a function (players 05a4ffc0,b485e332,b17b74e6,92eef845)", closed
2026-11-23. Fixed in yt-dlp 2026.11.23 by #18124 ("Use `tv` as default
`player_js_variant`").

Why production never picked the fix up: MA does not ship yt-dlp in its image.
The ytmusic provider installs `yt-dlp[default]` and
`bgutil-ytdlp-pot-provider` at runtime, into the container. In 2.11.0b5,
`install_package()` runs `uv pip install --no-cache <pkg>` with no
`--upgrade`, so the version installed when the container was created stays
until the container is recreated. The container has been up three weeks.
MA 2.11.0b11 changes this to `install_package(..., upgrade=True)` and warns
when the loaded yt-dlp is older than the installed one.

## Verification

Throwaway containers from the production image, host network (real
PO-token server on 127.0.0.1:4416), no cookies, MA's own yt-dlp options
(`player_client: ["web_music"]`, `skip: ["translated_subs", "dash"]`,
music.youtube.com URL, format selector `m4a/bestaudio`):

| yt-dlp | dQw4w9WgXcQ | xxRnd7kkIJ4 |
|---|---|---|
| 2026.8.19 | FAIL: Requested format is not available | FAIL: Requested format is not available |
| 2026.11.23 | OK, format 140 mp4a.40.2 | OK, format 140 mp4a.40.2 |

A first probe with yt-dlp's default clients (not MA's) succeeded on
2026.8.19 too; only MA's `web_music` client reproduces the failure.

## Hypotheses

- MA itself needs upgrading — partly wrong. The fix is in yt-dlp. A newer MA
  only helps because b11 upgrades yt-dlp at provider load; recreating the
  b5 container would also install the latest yt-dlp.
- PO-token plugin/server version mismatch (plugin 2.0.2, server 2.0.0, logged
  as a warning) — not the cause: the passing probe used the same pair.

## Fix

Two options were put to the operator: pin `yt-dlp[default]==2026.11.23`
into the running kronk.9 container and restart, or move MA to stock
2.11.0b11. The operator chose b11.

Cost of b11: the Pandora create-station patch is not upstream and is gone
(no `library_add` / `LIBRARY_RADIOS_EDIT` in b11's Pandora provider); the
thumbs patch (`set_favorite`) is upstream and stays. Kronk's taste ranking
scores Pandora `seed:` items (stations the account doesn't have yet); b11's
search no longer offers them, so genre requests rank existing stations and
YouTube Music only. Existing Pandora stations play as before.

Timeline (local time):

- 15:52:32 all MA players and pucks idle (the two Shields were playing the
  YouTube TV app, not MA); `music-assistant` stopped.
- 15:52:33–15:55:57 quiesced backup
  `~/backups/ma/ma-config-2.11.0b5-20261127-155233-quiesced.tgz`
  (2,366,366,132 bytes, 5,417 entries, listed back clean).
- 15:56:10 up on `ghcr.io/music-assistant/server:2.11.0b11`
  (sha256:2acb3ee333cb25266ff10ecec39611a4203c097930920f5adcd48903e610cb71).
  b11 ran its config migrations: a "player split" (copy kept at
  `/data/settings.json.pre-player-split`) and removal of the retired
  `universal_player` and `sendspin_source` provider configs. HA entity ids
  of the MA players are unchanged.
- 15:56:13 Pandora authenticated; 15:56:17 one `Invalid fragment data ...
  HTTP 400` (the known first-fragment failure after a restart).
- 15:56:18 YouTube Music provider loaded; yt-dlp 2026.11.23, yt-dlp-ejs
  0.8.0, bgutil plugin 2.0.2.
- 15:57:06 `scripts/music_provider_canary.sh`: `available:
  {"filesystem_local": true, "pandora": true, "ytmusic": true}`, PASS.
- MA's yt-dlp options run inside the live container: `RVO3n6v-efI` and
  `xxRnd7kkIJ4` both OK, format 140 mp4a.40.2.
- `./scripts/run_tests.sh`: 8593 passed, 3 skipped, 62 xfailed.

Rollback: `kronk/music-assistant:2.11.0b5-kronk.9` plus a restore of the
backup above (b11 migrated the config).

Open: `media_player.living_room_sony_bravia` (Chromecast `XBR-49X800D`) was
`idle` all day and has been `unavailable` since MA stopped at 15:52:32; MA
re-added it from config but has not rediscovered it. Unknown whether the TV
is in deep standby or b11's cast discovery misses it.

Also still open: the PO-token plugin (2.0.2) and server image (2.0.0) differ;
MA warns on every load. Not the cause here; a separate one-variable bump of
the `bgutil-pot-provider` image.

## What would have caught it sooner

The daily music canary did catch it: ytmusic unavailable from 2026-11-25
07:04, a push on 2026-11-27 07:01. Its message said "paste a fresh cookie",
which sends the reader to the wrong fix; the provider was down because
format resolution failed, not because of the cookie. The canary should
report MA's provider error text instead of guessing a cause.

Structurally: under 2.11.0b5 yt-dlp could only move by recreating the
container. b11 upgrades it on each provider load, unpinned, so the next
YouTube break is fixed by a provider reload once yt-dlp ships, and a bad
yt-dlp release also arrives without a deploy.
