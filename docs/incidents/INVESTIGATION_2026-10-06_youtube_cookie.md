# Investigation 2026-10-06 — YouTube Music provider dead after the MA restart

Status: root-caused. Cookie is dead server-side; a provider reload does not
recover it. Fix is operator-side (fresh cookie). Follow-ups proposed below
(provider-availability canary; MA discards yt-dlp's rotated cookies).

## Symptom

After the named MA restart at 08:51–08:53 local (onto the derived image
`kronk/music-assistant:2.11.0b2-kronk.1`, see
`docs/plans/MA_LOCAL_PANDORA_FEATURES_PLAN.md`), MA's YouTube Music
provider failed to load. `docker logs music-assistant-server`:

```
WARNING: [youtube] The provided YouTube account cookies are no longer valid. They have likely been rotated in the browser as a security measure. For tips on how to effectively export YouTube cookies, refer to  https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies .
(×4)
WARNING: [youtube] [pot:bgutil:http] The provider plugin and the HTTP server are on different versions, this may cause compatibility issues. Please ensure they are on the same version. Otherwise, help will NOT be provided for any issues that arise. (plugin: 2.0.1, HTTP server: 2.0.0)
2026-10-06 08:53:05.269 WARNING (MainThread) [music_assistant] Error loading provider(instance) ytmusic--kGmHoQDn: User does not have Youtube Music Premium
```

MA UI / API (`config.get_provider_config`) shows `last_error = {'error_code':
6, 'message': 'Login failed. Please check your credentials and try again.'}`
and the provider is absent from the loaded-providers list.

The operator believed YouTube Music had worked that morning ("Daisies of the
Galaxy" played). The cookie is the dedicated Kronk Google account's, pasted
2026-10-02 (feature doc, "YouTube Music runs as a dedicated Google account").

## Timeline (local = EDT; HA history / Langfuse / bgutil log are UTC, −4 h)

| Local | Source | Event |
|---|---|---|
| 2026-10-02 14:48:50 | MA log (rotated, `/data/musicassistant.log.1`) | `Loaded music provider YouTube Music` — the Kronk-account cookie pasted; the load-time premium check passed, so the cookie was live. |
| 2026-10-05 14:44–16:25 | bgutil log + HA history | ytmusic streams on the kitchen (`8VE7UDF2mMU` "Best of Deep House 2026…", then a run of tracks); last POT `GTr2wIyBXGo` at 16:25:14. |
| 2026-10-06 06:13:43 | HA logbook | Blueprint `kronk_music_assistant_voice_device_first` triggered on the kitchen Voice PE. |
| 06:13:46 | MA log | `Fetching tracks to play for album Insane` (Black Gryph0n, ytmusic). |
| 06:13:49 / 06:13:52 | bgutil log | `Generating POT for sabR15q_X6c` / `tWLoADg7iw8` (track + next-track resolve). |
| 06:13:50 | MA log + HA history | `Start Queue Flow stream` for `kitchen voice pe ma`; `media_player.home_assistant_voice_0ac919_pe_01` → `playing`, content `ytmusic--kGmHoQDn://track/sabR15q_X6c`. Audio flowed; no MA stream error. |
| 06:14:30 | Langfuse `d71956057b3dcbab6c674b23dca72228` | Operator: `" Stop!"` → `"Paused on the Kitchen speaker."` Player idle at 06:14:34 (44 s of playback, stopped on purpose). |
| 06:14:52–06:14:58 | HA logbook | Resume via HA built-in, 5 s, idle again. |
| 06:15:06 | HA logbook + MA log | Blueprint triggered; `Fetching tracks to play for album Daisies of the Galaxy`. |
| 06:15:16–06:49:35 | HA history | `library://track/5420` … `library://track/5413` (Eels, Daisies of the Galaxy) — **the NAS copy, filesystem provider, not YouTube.** |
| 08:51:08 | MA log | Container stopped for the named restart (tarball `~/backups/ma/ma-config-2.11.0b2-20261006-085110-quiesced.tgz`). |
| 08:53:02–08:53:05 | docker logs | ytmusic load: 4× yt-dlp "cookies are no longer valid", then `User does not have Youtube Music Premium`. **First hard evidence the cookie is dead.** bgutil: `Generating POT for dQw4w9WgXcQ` at 08:53:04 (the premium-check track). |
| 09:08:29 | HA log | Satellite 1 blueprint run: `Error executing script. Error for call_service at pos 1: Invalid or unsupported command.` — the operator's first post-restart YouTube request, refused because the provider is unloaded. Pandora thumbs at 09:08:42 onward worked. |
| 09:18:21 | this investigation | `config/providers/reload` → `LoginFailed`, same four yt-dlp warnings, `config/providers/reload: User does not have Youtube Music Premium`. Not transient. |

Answer to "what served Daisies": the local library. The YouTube play that
morning was the 44-second "Insane" stream at 06:13, which the operator
stopped himself.

## Evidence

**The failing check** — `music_assistant/providers/ytmusic/__init__.py`
(MA 2.11.0b2; container path `/app/venv/lib/python3.14/site-packages/…`):

```python
YTM_PREMIUM_CHECK_TRACK_ID = "dQw4w9WgXcQ"
...
    async def handle_async_init(self) -> None:
        ...
        if not await self._user_has_ytm_premium():
            raise LoginFailed("User does not have Youtube Music Premium")
...
    async def _user_has_ytm_premium(self) -> bool:
        """Check if the user has Youtube Music Premium."""
        stream_format = await self._get_stream_format(YTM_PREMIUM_CHECK_TRACK_ID)
        # Only premium users can stream the HQ stream of this song
        format_id: str = stream_format["format_id"]
        return format_id == "141"
```

`_get_stream_format` runs yt-dlp with `"cookiefile": StringIO(self._netscape_cookie)`
(a fresh copy of the pasted cookie on every call — rotated cookies yt-dlp
receives are thrown away), `player_client: ["web_music"]`, bgutil PO tokens.
The check runs **only at provider load** (start, reload, reconfigure). The
provider's other LoginFailed path (`helpers._raise_if_signed_out`, triggered
from `sync_library`) only fires when a ytmusicapi call returns the signed-out
page; it did not fire here (see live test).

**The yt-dlp warning** — `yt_dlp/extractor/youtube/_base.py` (yt-dlp 2026.8.19):

```python
    @property
    def _has_auth_cookies(self):
        yt_sapisid, yt_1psapisid, yt_3psapisid = self._get_sid_cookies()
        # YouTube doesn't appear to clear 3PSAPISID when rotating cookies (as of 2025-04-26)
        # But LOGIN_INFO is cleared and should exist if logged in
        has_login_info = 'LOGIN_INFO' in self._youtube_cookies
        return bool(has_login_info and (yt_sapisid or yt_1psapisid or yt_3psapisid))

    def _request_webpage(self, *args, **kwargs):
        response = super()._request_webpage(*args, **kwargs)
        # Check that we are still logged-in and cookies have not rotated after every request
        if getattr(self, '_passed_auth_cookies', None) and not self._has_auth_cookies:
            self.report_warning('The provided YouTube account cookies are no longer valid. ...')
```

`_passed_auth_cookies` is set at extractor init only if the jar already has
`LOGIN_INFO` + a SAPISID. So the warning means: the paste was structurally
complete, and YouTube's **response** to the first request expired
`LOGIN_INFO` — the server no longer accepts the session. yt-dlp then
continues as signed-out.

**Live test (09:2x local, inside the MA container, cookie decrypted in-process
with MA's Fernet key, never printed):**

- Cookie names present: `APISID, CONSISTENCY, HSID, LOGIN_INFO, PREF, SAPISID,
  SID, SIDCC, SSID, VISITOR_INFO1_LIVE, VISITOR_PRIVACY_METADATA, YSC,
  __Secure-1PAPISID, __Secure-1PSID, __Secure-1PSIDCC, __Secure-1PSIDTS,
  __Secure-3PAPISID, __Secure-3PSID, __Secure-3PSIDCC, __Secure-3PSIDTS,
  __Secure-BUCKET, __Secure-ROLLOUT_TOKEN, __Secure-YNID, _gcl_au` — a
  complete, correctly captured header. Not a paste error.
- yt-dlp with the provider's exact options on `dQw4w9WgXcQ`:
  `formats: ['sb3','sb2','sb1','sb0','249','250','140','251','160','278','133','242','134','18','243','135','244','136','247','137','248','271','313']`,
  selected `140`; `is_authenticated after request: False`, `LOGIN_INFO still
  in jar: False`. No `141` → premium check fails. **But a stream still
  resolves** (128 kbps AAC, signed-out).
- ytmusicapi call with the provider's own SAPISIDHASH header recipe
  (`get_library_playlists`): returned 0 playlists without raising — YTM
  answered anonymously rather than with the signed-out page, so
  `_raise_if_signed_out` would not have caught this either.

**bgutil mismatch** — `yt_dlp_plugins/extractor/getpot_bgutil.py`
`_check_version`: unequal versions → `warning(once=True)`; only a **major**
mismatch raises. 2.0.1 vs 2.0.0 is patch-level, and the helper generated the
POT for the check track at 08:53:04 and for every stream since 10-04. Not a
factor.

## Hypotheses

1. ~~The restart / derived image broke the provider.~~ No: the provider code
   is upstream's, the check is the same one that passed on 10-02, and the
   reload at 09:18 (no restart) fails identically.
2. ~~Cookie pasted wrong / incomplete.~~ No: all auth cookies present; yt-dlp
   accepted it as an account cookie before the first request.
3. ~~PO-token helper mismatch blocks login.~~ No: patch-level, warn-only,
   POTs issued.
4. ~~Closing the private window killed it.~~ Backwards: closing the window is
   what *stops* the browser from rotating the session out from under the
   paste (yt-dlp wiki). The feature-doc recipe already does this.
5. **Google expired the session server-side.** Fits everything: live on
   10-02 14:48, dead by 10-06 08:53 (≤ 3.75 days), the yt-dlp warning
   pattern (LOGIN_INFO cleared by the server), and yt-dlp issue #13964
   ("youtube cookies expires 3-5 days even in incognito", Aug 2025 —
   reporters say the private-window export that used to last ~a month now
   dies in 3–5 days). Mechanism, as far as it is documented: YouTube rotates
   `__Secure-1PSIDTS` / `__Secure-3PSIDTS` on requests and eventually
   rejects a session still presenting the original stamps; MA re-feeds the
   original paste into every yt-dlp run and discards the rotated values
   (`StringIO(self._netscape_cookie)`), so each run presents a session
   YouTube has already moved past. Whether the trigger is age, request
   count, or a sign-in elsewhere is not determinable from here.
6. **Operator-side sign-in/sign-out of the Kronk account** between 10-02
   and 10-06 (e.g. to accept shared playlists or Pandora stations — see
   `INVESTIGATION_2026-10-04_radio_failures.md`). A plain sign-in in another
   browser does not revoke other sessions; "sign out of all devices" or a
   password/security change does. Open — operator to confirm.

## Root cause

The YouTube session behind the pasted cookie is dead server-side (YouTube
clears `LOGIN_INFO` on first contact and serves signed-out formats). MA's
provider only notices at load time, through `_user_has_ytm_premium()`, which
reports the signed-out state as "User does not have Youtube Music Premium" /
"Login failed" (tenet 7 — the real cause lives only in yt-dlp's stderr
warning). The restart did not kill the cookie; it was the first moment
anything checked.

Exact death time is not recoverable: a dead cookie still streams
(signed-out, format 140), MA logs stream details at DEBUG, and HA history
carries no bitrate. Window: between 2026-10-02 14:48 and 2026-10-06 08:53.
The 06:13 "Insane" stream proves YouTube delivered audio, not that the
session was signed in.

## Fix

**Applied:** none in the repo. Reload attempted (09:18:21) — no recovery.

**Required (operator):** a fresh cookie, same recipe as
`docs/features/voice-music-control.md`:

1. New private window. Sign in to YouTube Music as the Kronk account only.
2. DevTools → Network → pick a `browse` request → copy the whole `cookie`
   request header. (yt-dlp's wiki variant: open `youtube.com/robots.txt` as
   the only tab before copying, so no YouTube page is live-rotating the
   session while you export.)
3. Close the private window **without signing out**. Never reopen that
   session.
4. MA UI → Settings → Providers → YouTube Music → reconfigure → paste.
   Saving reloads the provider; success is `Loaded music provider YouTube
   Music` in `docker logs music-assistant-server` with no `[youtube]`
   cookie warning. No container restart needed.
5. Verify from a satellite: "Play <something only on YouTube Music>".

**Making it last longer — what is and isn't on the table:**

- The private-window-then-close recipe is already the yt-dlp-recommended
  one; there is no setting on our side that extends it. ytmusicapi's docs
  claim "about 2 years unless you log out"; yt-dlp users report 3–5 days
  since mid-2025. Expect the latter.
- A normal browser profile dedicated to the Kronk account is **worse**: an
  open YouTube tab rotates the session continuously and the paste goes
  stale within minutes.
- Don't sign the Kronk account in anywhere else while the cookie is live if
  it can be avoided; if a sign-in is needed (sharing playlists/stations),
  expect to re-paste afterwards.
- Structural option (follow-up, ROADMAP): MA throws away the rotated
  cookies yt-dlp receives. Persisting the jar across runs (write
  `cookiefile` to `/data`, let yt-dlp update it) is what keeps a yt-dlp
  session alive elsewhere; we already run a derived MA image, so this is a
  candidate patch — but it is upstream behavior to change, not a quick fix,
  and unproven for this failure.

## What would have caught it sooner

- **A provider-availability canary** (pattern: `scripts/searxng_canary.sh`
  + a systemd user timer). Poll MA (`providers` list / `get_provider_config`
  `last_error` for `ytmusic--kGmHoQDn`, and a real `_get_stream_format`-style
  probe — the premium check is the only thing that distinguishes signed-in
  from signed-out) and push one HA notification when it flips. The daily
  check would have reported the dead session before the restart, and it is
  the only way to see this between restarts, since nothing in normal
  playback fails.
- **Surface yt-dlp's warning in MA's own log** (derived-image patch or
  upstream PR): route yt-dlp's logger into the provider logger so "cookies
  are no longer valid" appears next to the misleading "no Premium" line.
- The tool_service / blueprint error the operator heard at 09:08 ("Invalid
  or unsupported command") says nothing about *which* provider is down —
  tenet 7 again; a provider-down check in `play_music` would name it.

## Why the operator's cookie lasted months and Kronk's days

**Same machinery, both times.** The operator's cookie was pasted 2026-05-31
14:52 (`INCIDENT_2026-05-31.md`). The load-time Premium check exists in 2.8.8
too (`__init__.py:235` in that image) and passed on 08-12, 09-04 and 09-19
(logs in the 09-19 tarball in `~/backups/ma/`); no re-paste is recorded, so
that session lived ≥124 days. `convert_to_netscape`
forwards every cookie, PSIDTS included; neither yt-dlp nor ytmusicapi calls
`RotateCookies`; each call re-presents the original paste. The Kronk paste is verified complete (24 names, both PSIDTS);
the operator's old one sits in the 09-19 tarball under the legacy server_id
key, not decrypted here. Volume is not it: bgutil POTs/day were 120 and 202 on
09-24/25 under the operator's cookie, 13/15/4 on 10-04/05/06 under Kronk's.

**Correction.** yt-dlp [#13964](https://github.com/yt-dlp/yt-dlp/issues/13964)
is one user's claim, closed as "question" with the standard recipe as its only
reply. In [#8227](https://github.com/yt-dlp/yt-dlp/issues/8227) coletdjnz's
"invalidated if not refreshed" was a stated guess, and the never-rotated
incognito export was then reported alive for months. A static snapshot does
not die of age.

**Ranked causes.**
1. *Account trust.* Kronk's account is weeks old, had one session ever, no
   device history, then scripted traffic. Analog in #8227
   (assassinliujie, 2024-06-14): two accounts exported identically; the one
   also used daily in Chrome lasted two months under heavy use, the spare died
   "within a few hours". The [yt-dlp
   wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)
   warns spare accounts risk bans.
2. *A security event on the Kronk account* 10-02..10-06: password, recovery or
   2SV change, or "sign out all". A plain phone sign-in does not revoke
   sessions; [a password change
   does](https://support.google.com/accounts/answer/41078). Open — operator to
   confirm.
3. *The private window outlived the capture*, rotating PSIDTS under the paste.

**Distinguishing test (operator, read-only).** Kronk account →
myaccount.google.com → Security → *Your devices* and *Recent security
activity*: a signed-out 10-02 Linux-browser session dates the death; a listed
event is cause 2. On the next paste, run the in-container format-141 probe
daily to get the death day.

**Getting the old lifetime back.** Age the Kronk account before capturing:
recovery phone, 2SV on, a week signed in on a phone's YT Music app, then the
private-window capture. Or sidestep it: MA supports a
brand account — a 21-digit username becomes ytmusicapi's `user`, so
searches land in the brand channel's history while
the cookie stays the operator's trusted session ([MA
docs](https://www.music-assistant.io/music-providers/youtube-music/)); verify
history separation first. OAuth is out: `setup_flow.py` takes
username/cookie/PO-token only, and ytmusicapi's OAuth lost YTM access in [Sept
2025](https://docs.multi-scrobbler.app/configuration/sources/youtube-music/).

## 2026-10-08 — fresh Kronk cookie, 2SV on, lifetime test started

Operator turned on 2-Step Verification on the Kronk account (security
activity showed no sign-outs or events 10-02..10-06), captured a fresh
cookie into `secrets/yt-token.txt` (gitignored). Submitted without a UI and
without printing it: one MA websocket connection, `auth` →
`config/providers/reconfigure(instance_id=ytmusic--kGmHoQDn)` →
`config/flows/submit(flow_id, {cookie})`; MA's finish handler merged it
into the encrypted setup_data (username and PO-token URL kept) and reloaded
the provider. 16:03:39 `Loaded music provider YouTube Music`, zero "cookies
are no longer valid" warnings, provider available, `last_error` None.

Lifetime is now measured: `scripts/music_provider_canary.sh` via
`kronk-music-canary.timer` (daily 07:00) reloads the provider — re-running
MA's own Premium check — skips if a speaker is playing YouTube, alerts once
per day on failure, and records the first day a provider is seen dead in
`data/music_canary.json`. If this cookie dies within days with nothing in
the account's security log, account trust is confirmed and the brand-account
route (or aging the account further) is next.
