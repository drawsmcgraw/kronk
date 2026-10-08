# Investigation 2026-10-05 — "What's the latest episode of Last Week Tonight about?" returns nothing

Status: root-caused, read-only investigation. No fix applied; proposals at
the end.

## Symptom (as the user saw it)

Voice (Office satellite), 2026-10-05 14:54 EDT: "What's the latest episode
of Last Week Tonight about?" → 27 s of silence, then "I cannot find a
summary of the latest episode of *Last Week Tonight* at this time."

The operator's theory: the search tool is failing again.

## Trace

Langfuse `4184c21c4fccbd4e483abfd67c65691b`, rid `275401a5`,
2026-10-05T18:54:12Z, transport shim, route `direct` (rule `default`),
latency 27.47 s. Models: gemma-4-e4b for coordinator and research.

## Timeline (UTC, from the trace, orchestrator events, and SearXNG log)

| Time | What |
|---|---|
| 18:54:12.6 | request; `routing.decide` → `direct` |
| 18:54:14.8 | coordinator round 1 → `ask_research("latest episode of Last Week Tonight summary")` |
| 18:54:15.9 | research `web_search` #1 — same query. 0.41 s, 5 results |
| 18:54:15.9–16.2 | SearXNG: duckduckgo `CAPTCHA (us-en)`, brave `Too many request (suspended_time=900)`, qwant `Access denied (suspended_time=3600)` |
| 18:54:17.3 | `web_search` #2 "Last Week Tonight latest episode summary October 2026" — 0.38 s, 5 results |
| 18:54:17.6 | SearXNG: mojeek `HTTP error 403 (suspended_time=3600)` |
| 18:54:20.0 | `web_search` #3 "...official website latest episode summary October 2026" — 2.18 s, 5 results |
| 18:54:25.4 | `web_search` #4 "...latest episode summary YouTube" — 0.27 s, 5 results |
| 18:54:27.7 | research synthesis (round 5 of 8): "I was unable to find the latest episode summary of "Last Week Tonight" in the search results provided." |
| 18:54:28.8 | coordinator round 2 → `ask_research("summary of the most recent episode of Last Week Tonight")` — same question again |
| 18:54:30.7–35.3 | `web_search` #5, #6, #7 (0.33 / 0.28 / 0.34 s, 5 results each) |
| 18:54:37.6 | research synthesis (round 4): same refusal |
| 18:54:40.1 | coordinator synthesis → final answer; `request_complete` 27.47 s |

Seven `web_search` calls, zero `fetch_url` calls, every tool call HTTP 200.
The duckduckgo CAPTCHA fired on all seven.

## Evidence

### What the model actually received

`web_search` #1, verbatim (first two of five):

```
[Web search results for 'latest episode of Last Week Tonight summary']

[Fox News - Breaking News Updates | Latest News Headlines | Photos ...](https://www.foxnews.com/?msockid=3f6ac91d44666fc6085edef445a16e64)
Latest Current News: U.S., World, Entertainment, Health, Business, Technology, Politics, Sports.

[Breaking News, Latest News and Videos | CNN](https://www.cnn.com/)
View the latest news and breaking news today for U.S., world, weather, entertainment, politics and health at CNN.com.
```

`web_search` #2–#4, #6, #7 (queries starting "Last Week Tonight ..."):

```
[Last.fm | Play music, find songs, and discover artists](https://www.last.fm/)
[LAST Definition & Meaning - Merriam-Webster](https://www.merriam-webster.com/dictionary/last)
[LastPass - Sign In](https://app.lastpass.com/?ac=1)
[LAST Synonyms: 204 Similar and Opposite Words - Merriam-Webster](...)
```

`web_search` #5 ("most recent episode of Last Week Tonight summary"):

```
[MOST—Missouri's 529 Education Plan | MOST 529](https://www.missourimost.org/)
[MOST Definition & Meaning - Merriam-Webster](https://www.merriam-webster.com/dictionary/most)
[MOST](https://www.most.org/)
[MOST - INDUSTRIAL ENGINEERING](...)
```

Every result set matches only the **first word** of the query. The
`msockid=` parameter on the Fox URL is a Bing tracking id. The model's
refusal was correct given this input; there was nothing to `fetch_url`.

### SearXNG log, 18:54:15–18:54:35 UTC

```
2026-10-05 18:54:15,950 ERROR:searx.engines.duckduckgo: CAPTCHA (us-en) (suspended_time=0)
2026-10-05 18:54:16,017 ERROR:searx.engines.brave: Too many request (suspended_time=900)
2026-10-05 18:54:16,198 ERROR:searx.engines.qwant: Access denied (suspended_time=3600)
2026-10-05 18:54:17,614 ERROR:searx.engines.mojeek: HTTP error 403 (suspended_time=3600)
2026-10-05 18:54:20,099 ERROR:searx.engines.duckduckgo: CAPTCHA (us-en) (suspended_time=0)
... (duckduckgo CAPTCHA repeats on every one of the seven searches)
```

Nothing logged for google, wikipedia, or wikidata — not then, not in the
last ten days. The same four-engine wipeout appears on the only other two
research sessions in that window (2026-09-28 10:45, 2026-10-01 19:36): the
first search of every session benches brave, qwant and mojeek within two
seconds. On a single-IP instance these engines die on contact.

### Live probes (19:00–19:10 UTC, from inside the container; read-only)

`/search?q=latest episode of Last Week Tonight summary&format=json`:

```
10 results; per-engine {'bing': 10}
unresponsive=[['brave','Suspended: too many requests'],['duckduckgo','CAPTCHA'],
              ['mojeek','Suspended: access denied'],['qwant','Suspended: access denied']]
1. Fox News - Breaking News Updates | Latest News Headlines ...
2. Breaking News, Latest News and Videos | CNN
3. Associated Press News ...
```

Reproduces the trace exactly. Bing is the only engine answering.

Per-engine bang probes:

| Query | Result |
|---|---|
| `!google kronk` | 0 results, no error, nothing logged |
| `!google Last Week Tonight John Oliver latest episode` | 0 results |
| `!wikipedia Harry Houdini` | 0 results, 1 infobox (tool_service reads only `results`) |
| `!wikipedia Last Week Tonight with John Oliver` | 0 results, 0 infoboxes |
| `!wikidata Harry Houdini` | 0 results |
| `!brave kronk` | suspended |
| `!bing kronk` | 10 good results (Kronk, Disney wiki) |
| `!bing Houdini death` | 10 good results |
| `!bing what is the capital of France` | 10 good results (Paris) |
| `!bing Last Week Tonight John Oliver latest episode` | 10 results, all **Bing Visual Search / Google Images / reverse image search** |
| `!bing "Last Week Tonight" John Oliver October 2026` | 10 results, all **r/duckduckgo and Zhihu DuckDuckGo threads** |
| `Last Week Tonight John Oliver` (no bang) | 7 bing results, all correct: Wikipedia, the YouTube channel, HBO Max, the episode list, epguides' topics-and-air-dates page |

So: Google, Wikidata and (for results, not infoboxes) Wikipedia contribute
nothing, silently. Bing is the sole live engine, and Bing on this build is
correct for short queries and returns first-word or random junk for the
long, descriptive queries the research prompt produces.

### This is a known upstream SearXNG bug, fixed after our pin

- searxng/searxng issue #4964 "bing: results are often unrelevant to the
  search query" (opened 2025-07-02): "results match only 1–2 query words or
  show completely unrelated content… problem worsens with multi-word
  queries."
- Fixed by PR #6671 "[fix] engines: bing first word results", merged
  2026-09-11 (commit `ffe96f8`). Diagnosis there: Bing's marketplace/region
  handling — certain markets (`us` among them) "returned complete garbage
  100% of the time"; the fix switches the engine to Bing's `setlang`/`cc`
  parameters and drops the accept-language override.
- We run `searxng/searxng:2026.6.12-cc196f2a5` (pinned 2026-06-12). The
  compose comment on that line says to bump the pin roughly monthly because
  "a stale build is a CAPTCHA magnet". It has been ~4 months. Newest tag at
  time of writing: `2026.10.4-d48c4b555`.

### How long has this been going on

Langfuse `tool.web_search` observations since 2026-08-01 (305 total). The
first-word pattern is visible in nearly every session:

| Date | Query | Top results |
|---|---|---|
| 2026-09-04 | "Framework Desktop BIOS download page" | "Framework - Minecraft Mods" |
| 2026-09-06 | "current residential electricity rate per kWh in Laurel" | "Current \| Future of Banking", "current（英语单词）_百度百科" |
| 2026-09-07 | "GS-9 step 5 salary federal government" | "Home \| Goldman Sachs" |
| 2026-09-07 | "annual salary for GS-9 step 5" | "ANNUAL Definition & Meaning" |
| 2026-09-09 | "Harry Houdini death story cause date and location" | "Johnson & Wales University" |
| 2026-09-09 | "Harry Houdini death and final days narrative" | "登录codex的时候弹出电话号码验证怎么办？ - 知乎" |
| 2026-10-01 | "Conan the barbarian philosophy on life" | "Conan O'Brien - Wikipedia" |

The June runs of this exact question (traces `58222a9d`, `782fdd40`,
`7de3ea41`, 2026-06-16, pre-coordinator routing) show the same Bing junk
("TOP Definition & Meaning - Merriam-Webster" for "top news headlines…")
*interleaved with* real results from the other engines — HBO Max, the
Wikipedia episode list, a YouTube episode dated Jun 7. `782fdd40` still
failed to name the topic, and `58222a9d` answered wrongly (NBC Nightly
News headlines, lifted from the junk). The question has never been
answered correctly; in June the secondary engines at least gave the model
something real to work with. Now they don't.

## Hypotheses considered

- **SearXNG down / tool timeout** — no. Every call HTTP 200 in 0.27–2.18 s;
  tool_service log shows seven clean `GET /search … 200 OK`.
- **Empty results (404 "No results found")** — no. Five results every time.
  Plausible-looking ones, which is worse than empty: the model got nothing
  it could recognise as a failure.
- **Wrong query from the model** — not the cause. The queries were
  reasonable English. They were also long (6–10 words), which is exactly
  the input the Bing bug mangles; the one short probe query worked.
- **fetch_url bot wall** — not reached. No URL worth fetching.
- **Budget cliff / repeat-call loop** — no. Research stopped itself at
  rounds 5 and 4 of 8. The coordinator did re-delegate the identical
  question a second time (7 searches for one question), which doubled
  latency but changed nothing.
- **Model answering from stale knowledge** — no. It refused, which is the
  correct behaviour given its input.
- **Engine rate-limiting / CAPTCHA** — yes, contributing: four of eight
  engines benched on the first search. But this is the same as every
  research session for weeks and it is the stale build's bot-detection
  signature, not something new today.

## Root cause

Two layers, both in SearXNG, both consequences of a four-month-old pin:

1. **Engine attrition.** On `2026.6.12`, duckduckgo CAPTCHAs on every
   request, brave 429s on the first, qwant and mojeek 403 on the first and
   bench for an hour; google, wikidata and wikipedia return zero results
   with no error. Bing is the only engine delivering `results`.
2. **The one surviving engine has a known bug.** Bing on this build returns
   first-word or random results for multi-word queries (upstream #4964,
   fixed in PR #6671 on 2026-09-11, after our pin). The research agent's
   queries are always multi-word.

Secondary (ours, tenet 7): `tool_service /search` discards SearXNG's
`unresponsive_engines` and `infoboxes`, and the orchestrator renders
whatever comes back as a confident five-result list. Neither the model nor
Langfuse carries any signal that four engines were down and the fifth was
answering a different question. The failure was invisible for a month.

This is the 2026-06-12 "AOL-only" incident again in a different coat
(`docs/BLOG_TOPICS.md` → "My self-hosted search went AOL-only"; the
settings.yml header). Same mechanism: a stale build gets the good engines
benched, one engine survives, and that engine's output is junk. The
June fix (`keep_only`, short `suspended_times`, pinned image) was right
and is still in place; the part that lapsed was the "bump the pin
monthly" operational step, which lives only in a compose comment.

## Proposed fix (not applied)

1. **Bump the SearXNG pin** to `2026.10.4-d48c4b555` (contains #6671).
   Deliberate, single-variable. Verify with the probe queries above
   before and after: `latest episode of Last Week Tonight summary` must
   return ≥2 engines and a top result containing at least two query
   words. This is a Kronk-stack `up -d searxng`; it does not touch HA/MA.
2. **Surface engine health in the tool result** (`tool_service/main.py`
   `/search`): pass `unresponsive_engines` and the set of engines that
   answered through to the orchestrator, and have `_tool_web_search`
   prepend a line like `[engines: bing only; brave, duckduckgo, mojeek,
   qwant unavailable]`. Fold `infoboxes` (Wikipedia/Wikidata) into results
   — today the wikipedia engine's entire output is dropped on the floor.
   Tenet 7: the failure becomes findable in one step.
3. **A SearXNG canary** in `scripts/` (and run from `pipeline_bench.sh`):
   one multi-word query, assert engines-answered ≥ 2 and top-result term
   overlap ≥ 2. Run it on update day and whenever research answers look
   off. Cheap; would have flagged this on 2026-09-04.
4. **Google**: zero results with no exception for `kronk` means the
   engine is broken, not benched. Re-check after the bump; if still
   silent, drop it from `keep_only` per the settings-file rule ("an engine
   that consistently returns junk or empty results … REMOVE it").
5. **Put the monthly pin bump somewhere it gets seen** — a ROADMAP
   operations line or the update-day checklist. The compose comment did
   not survive contact with four months.

Not proposed: prompt changes to make the model write shorter queries
(tenet 5 — the loop would still be feeding it an engine that lies), or
raising the research budget (more rounds of the same junk).

## What would have caught it sooner

- The canary in (3). The pattern has been in Langfuse since at least
  2026-09-04 — a month of research answers quietly degraded to one broken
  engine, with no error anywhere because nothing was *failing*.
- Surfacing `unresponsive_engines` (2): the 2026-09-28 and 2026-10-01
  sessions would have shown "4 engines down" in the trace output instead
  of five tidy results.
- A term-overlap check on `web_search` output, in the tool or as a
  Langfuse-side eval: "no returned title or snippet contains more than
  one query word" is a one-line test for exactly this bug class.

## Open questions

- Why google returns zero silently on this build (consent wall? parser
  drift?). Answer after the bump before deciding whether to keep it.
- Whether the post-bump Bing engine still trips on the `us` market from
  this IP (PR #6671 skips `us`, `cn`, `ru` markets explicitly — check which
  region the engine resolves for `language=en`).
- The coordinator re-delegating the identical question after a research
  refusal (two `ask_research` calls, 7 searches, +9 s) is a loop
  behaviour worth a guard of its own; out of scope here.

## Fix applied (2026-10-05, later the same day)

1. **Pin bumped** `searxng/searxng:2026.6.12-cc196f2a5` → `2026.10.4-d48c4b555`
   (`docker compose up -d searxng`, healthy in 6 s). Effect: duckduckgo
   stopped CAPTCHA-ing on normal traffic. **Bing still returned first-word
   junk** on the new build — "Last Week Tonight John Oliver latest episode
   topic" → Last.fm / Merriam-Webster "LAST"; `!bing` with a 5-word query is
   fine, 7+ words is junk; with any explicit region (en-GB/CA/AU) Bing
   returns nothing. PR #6671's region fix does not cover this address.
2. **Bing disabled** in `searxng/settings.yml` (+ `.example`), with the
   receipt in a comment. The files are owned by the container's uid 977;
   edit via `docker exec -u 0 kronk-searxng-1 …`, then `compose restart
   searxng`. After: the LWT query answered by duckduckgo (correct), the
   GS-9 query by duckduckgo (FederalPay first), mojeek answers the same
   queries correctly when asked alone. brave 429 / qwant CAPTCHA / google
   403 remain benched from this address.
3. **tool_service `/search` now reports `engines` (answered) and
   `unresponsive_engines`**, and the web_search tool text carries them
   ("answered by duckduckgo; unavailable: brave: Suspended…"); a no-result
   404 names the benched engines. Two tests; suite 592.
4. Verified through the shim: "What was the main topic of the most recent
   episode of Last Week Tonight with John Oliver?" → research agent, two
   searches, an answer naming the October 4 episode's topic, 12.4 s
   (rid dadbf834). First correct answer to this question on record.

New fragility, named: with Bing gone, duckduckgo is the load-bearing
engine; rapid probing during this investigation CAPTCHA-suspended it for
30 min and a query returned zero engines. Mojeek is the second leg. The
engine list in every result is how that shows up instead of hiding.

## What now watches for this (2026-10-05)

`scripts/searxng_canary.sh`, fired weekly by `kronk-searxng-canary.timer`
(Mon 07:30, persistent): two fixed multi-word searches through tool_service
`/search` — FAIL on zero engines or fewer than 2 of 5 relevant results
(first-word junk scores 0); a single answering engine is a warning only,
because duckduckgo-alone is the normal state here — plus the compose pin's
age against the newest Docker Hub tag (FAIL over 35 days). One HA
mobile-app push per failure with a 24 h cooldown; state in
`data/searxng_canary.json`. The bump is `docs/runbooks/searxng-bump.md`.
First live run pushed a real alert (rule was then too strict: 1-engine
counted as failure, and "GS-9" was required verbatim); rule adjusted the
same hour.

## Engine roster after the bump (2026-10-05, evening)

- **mojeek and startpage are `inactive: true` upstream** on the October
  build (both moved to proof-of-work CAPTCHAs; startpage: searxng PR
  #6669). SearXNG never loads an inactive engine, so our `keep_only:
  mojeek` was naming a ghost — and a probe with `engines=mojeek` silently
  fell back to the defaults, which made it look like mojeek answered.
  Removed from settings; **yahoo** added as the second English leg (loads,
  answers the canary queries correctly on its own).
- SearXNG dedups identical URLs across engines and tags the merged row
  with one `engine` plus an `engines` list; tool_service now unions the
  list, so "answered by duckduckgo, yahoo" is reported when both agree.
- Working from this address today: duckduckgo, yahoo. Benched most of the
  time: brave (429), qwant (CAPTCHA), google (403). Disabled: bing (junk).
