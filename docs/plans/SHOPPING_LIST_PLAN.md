# Shopping list: HA owns it, Kronk speaks and shows it, Netlify mirrors it — Plan

Status: **approved in conversation 2026-09-17, parked 2026-09-19, not
started.** Build waits for the operator's go. Nothing has been created:
no endpoints, no HA config, no Netlify site. One Home Assistant restart is
part of step 2 and is called out there. To resume: read this doc top to
bottom, then "Open items for the operator" at the end — the Netlify
account/site/token/site id and the passphrase are still needed before
step 5; steps 1–4 need only the go.

## What the household does

Items get added by voice all week, sometimes wrongly (so voice delete is
required). The list is reviewed on a phone, and today hand-copied to paper
before shopping. Requirements, in the operator's words: add and delete by
voice; refuse duplicates with a spoken reason ("bananas are already on the
list"); answer "are bananas on the list?"; a phone UI on Kronk that is
quick and app-like; later, reachable from outside the house by the whole
family with a URL and one password, without any client software; **no
inbound traffic to the house** (showstopper); one source of truth.

## Decisions taken (2026-09-16/17)

- **Home Assistant's `todo.shopping_list` is the list.** Kronk keeps no
  copy: every voice answer and every page load reads HA live
  (`todo.get_items`, ~10 ms over the websocket). Kronk's JSON store goes.
- **Voice through HA's local tier**, fronted by sentence triggers, with the
  logic in Kronk's tool_service (HA's built-in add intent creates
  duplicates unconditionally — `todo/intent.py` calls create straight
  away — and there is no built-in "is X on the list").
- **Outside access is a pushed mirror on Netlify**, static, read-only, a
  plain copy of the list as HA holds it (to-buy and completed), no local
  state, no cleverness. Encrypted before it leaves the box; the family's
  one password is the decryption key. Open on the LAN, password only
  outside.
- Rejected: Tailscale Funnel / Cloudflare Tunnel (inbound), Firebase
  (plaintext in Google, more parts), GitHub Pages (free tier means a
  public repo for the ciphertext).

## Design

### 1. tool_service — the brain (`/shopping/*`)

All list logic in one place, used by both voice tiers and both pages.

- `GET  /shopping`             → `{to_buy: [...], completed: [...]}` from `todo.get_items`.
- `POST /shopping/add`         `{item}` → adds via `todo.add_item`, or refuses: `{added: false, speech: "Bananas are already on the list."}`.
- `POST /shopping/remove`      `{item}` → `todo.remove_item`, or `speech: "Bananas aren't on the list."`.
- `POST /shopping/contains`    `{item}` → `speech: "Yes, bananas are on the list." / "No, ..."`.
- `POST /shopping/complete`    `{item, completed: bool}` → `todo.update_item` (the page's check-off).
- `POST /shopping/clear_completed` → `todo.remove_completed_items`.
- Every response carries a `speech` sentence for HA to say, and the list.

Normalization (Python, tested): lowercase, trim, collapse spaces, strip a
leading quantity ("2 lbs of", "a dozen", "some"), singularize with a small
rule set (bananas→banana, tomatoes→tomato, cherries→cherry, exceptions
list). Matching compares normalized forms; HA keeps the item as spoken,
capitalized (HA's own convention). Each mutation ends with a mirror push
(section 4), debounced 2 s.

HA calls go over the existing `ha_call_service()` websocket path with
`return_response` for `get_items`. `HA_TOKEN` stays in tool_service.

### 2. Voice, HA tier (fast path, ~1 s, no LLM)

Sentence-trigger automations (a blueprint under `ha/blueprints/`, four
sentence groups) call `rest_command.kronk_shopping` and speak the returned
`speech`:

- add:      `(add|put) {item} (to|on|onto) [the|my] [shopping|grocery] list`
- remove:   `(remove|delete|take) {item} (off|from) [the|my] [shopping|grocery] list`, `take {item} off [the] list`
- contains: `(is|are) [there] {item} on [the|my] [shopping|grocery] list`, `do we have {item} on the list`
- read:     `what's on [the|my] [shopping|grocery] list`, `read [me] the [shopping] list`

Sentence triggers beat HA's built-in intents, so the non-deduping
`HassListAddItem` never fires for these phrasings. `rest_command` supports
`response_variable`, so the automation does
`set_conversation_response: "{{ r.content.speech }}"`.

**HA restart, one, planned:** HA has no `rest_command:` block today; the
first one loads only on restart (later edits reload with
`rest_command.reload`). It is the only restart in this plan, named here so
the operator picks the moment.

### 3. Voice, Kronk tier (loose phrasing) and the JSON store

`shopping_list_view/add/remove/clear` in `orchestrator/tools.py` are
re-pointed at `/shopping/*` (same responses, same dedupe), plus a
`shopping_list_contains` tool. `clear` becomes clear-completed. The home
agent prompt gets one line: exact phrasings are handled before Kronk; when
Kronk gets one ("we're out of milk"), it calls the tool and repeats the
`speech`. `/data/shopping_list.json` is migrated (two items, milk and eggs)
into HA and deleted; the old endpoints go.

### 4. Pages

- **LAN page** (rebuilt `orchestrator/static/shopping_list.html`, served at
  `/shopping_list` as today; orchestrator proxies `/api/shopping/*` to
  tool_service): live from HA on load and every 30 s; add box; tap to
  check off (→ `complete`); swipe/long-press to remove; "clear completed".
  Open on the LAN. Mobile layout, home-screen icon via a manifest; no
  service worker (HTTP on the LAN — item 15's HTTPS gate stays where it
  is).
- **Mirror page** (`mirror/index.html` in the repo, deployed once to
  Netlify): asks for the password once, derives the key (WebCrypto,
  PBKDF2-SHA256, 600k iterations, salt from the payload), keeps the key in
  localStorage, fetches `list.enc` (cache: no-store) every 60 s and on
  focus, decrypts (AES-256-GCM), renders **exactly** the same two sections
  the LAN page shows. Read-only. No check-off, no local state. Wrong
  password → decrypt fails → asks again.

### 5. The push (tool_service → Netlify)

After each mutation (debounced) and hourly (reconcile for edits made in
HA's own UI): read the list, JSON-encode `{to_buy, completed, updated_at}`,
encrypt with the passphrase from `.env` (`SHOPPING_MIRROR_PASSPHRASE`,
fresh random salt and nonce each push), and deploy `list.enc` with
Netlify's file-digest API (`POST /sites/{id}/deploys` with the sha1 map,
then `PUT` the changed file; token `NETLIFY_TOKEN` in `.env`,
`NETLIFY_SITE_ID` env). `index.html` is in the digest too so a viewer
change ships the same way. Failure is logged and retried on the next
mutation/hour; it never blocks the voice reply. Adds one pinned dependency
to tool_service: `cryptography` (AES-GCM; key derivation is stdlib
`hashlib.pbkdf2_hmac`).

Outbound only. Nothing on the internet can reach the house. What leaves:
ciphertext, a few KB, a few dozen times a week. Netlify free tier
(100 GB/month bandwidth) is not a concern.

### 6. Security notes (tenets 1, 10)

- Secrets: `NETLIFY_TOKEN` (Netlify PATs are account-wide, not per site —
  the account should hold only this site) and `SHOPPING_MIRROR_PASSPHRASE`,
  both in `.env`. The mirror page contains no secret.
- The passphrase is the key: choose a long one. Rotation = change `.env`,
  next push re-encrypts, phones re-enter it. Loss costs nothing; HA has
  the list.
- HA gets nothing new: tool_service already holds `HA_TOKEN`. The
  sentence-trigger automations only call the REST command.

## Latency (tenet 12)

Voice, HA tier: STT + ~1 s. Kronk tier unchanged (3–5 s). LAN page:
<200 ms. Mirror: a few seconds after a mutation (Netlify deploy
propagation) plus the page's 60 s poll.

## Tests

- Normalizer table (quantities, plurals, exceptions, case, spacing).
- `/shopping/*` against a fake HA: add/refuse/remove/contains/complete
  wording and service calls; mirror push is scheduled after a mutation
  and not on a read; push failure doesn't change the response.
- Encrypt in Python, decrypt with the page's exact parameters (a small
  Python re-implementation of the browser side), and the reverse.
- Kronk tools call the new endpoints and repeat `speech` verbatim.
- Sentences: `conversation/agent/homeassistant/debug` proves each trigger
  phrasing matches ours and not the built-in (no playback, no mutation).

## Steps

1. tool_service endpoints + normalizer + tests. Deploy, verify live with
   `curl` (add / duplicate / contains / remove) against HA.
2. `rest_command` in HA + the blueprint + automation. **HA restart here.**
   Verify sentences with the debug command, then a real "Okay Nabu, add
   bananas to the shopping list" / "add bananas…" again (refused) / "are
   bananas on the list" / "take bananas off the list".
3. Kronk tools re-pointed; JSON store migrated and removed; suite.
4. LAN page. Verify on a phone.
5. Netlify site (operator creates the account/site, hands over the token
   and site id, picks the passphrase), viewer deployed, push wired, hourly
   job. Verify from a phone off the LAN (mobile data): password → list;
   mutate by voice → page updates.
6. `docs/features/shopping-list.md`, ROADMAP to Shipped.

## Out of scope

Writes from outside; real-time refresh; per-person accounts; quantities as
data (they're stripped for matching, kept in the text); a "clear the whole
list" by voice (destructive, no undo — the page has clear-completed).

## Open items for the operator

- Netlify account and site name (`<name>.netlify.app`), token, site id.
- The passphrase.
- When to take the HA restart (step 2).
