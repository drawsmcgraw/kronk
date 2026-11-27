# M32 step 2 fix 1: Shipped lines for old rows, Drew's words whole

Fix agent (Opus 5.5), 2026-11-27, from `b0a88cf`. Contract: `M32-criteria.md`,
"Rulings on step 2's test" (B1 and S1 of `M32-step2-e2e.md`, one fix round).

## What changed

- **B1, `scripts/work_export.py` `roadmap_entry`.** A Shipped entry now
  links two ways:
  1. its lead, as before (`work item Mxx`, `sprint Mxx`, `sprint item Mxx`,
     "and Mxx"), but only the lead's first clause: what follows a `;`
     happened to the entry later;
  2. new, its body: the ids of a parenthesis that opens with them
     (`CITE_RE`: `(` then an id list, then `;`, `,` or `)`). That is the
     citation form of the "Refactor sprint" entry: "(M17)", "(M1, M1b)",
     "(M3, M4, M4b, M5; `docs/…`)", "(M7, `docs/…`)".

  An id in prose ("since sprint M2", "removed 2026-11-15, sprint M1") or in
  a path or quote (`docs/sprint/M34-promote.md`, "M30") links nothing. Open
  entries (Now, Next, Later, Chores) are read exactly as before: their
  links put an entry under the item under way, and nothing asked that to
  change. Both readers (`collect` and `collect_legacy`) use
  `roadmap_entry`, so both get the rule. Module docstring and the "Links"
  bullet of `docs/features/work-page.md` say the rule.
- **S1, `scripts/work.py` `SOURCE_MAX` 600 → 4,000.** `criteria_head` clips
  the joined `Source:` paragraph at this; it is the only clip on the source
  between the file and the page. The bound stays so one runaway paragraph
  can't swell `work.json`; 4,000 is the exporter's `ENTRY_MAX`, and the
  longest `Source:` paragraph of any criteria file is 722 (M33; then M32 685,
  M28 583). The other bound on the source is `HEAD_LINES` 40: the paragraph
  is read from a criteria file's first 40 lines. The latest-ending one ends
  on line 19 (M33). Left as it is.
- The page and the route are unchanged.

## The rule and "in passing"

The ruling: no entry may link an item it names only in passing (a
reference to another item's work, not a statement that it shipped). Every
id named in the Shipped section, by entry:

| Entry (ROADMAP line) | Ids named | Reading |
|---|---|---|
| Refactor sprint (261) | 15 in "(M…)" citations | each states that item shipped that part: linked |
| Financial expert, phase 1 (444) | M1, lead after `;`: "removed 2026-11-15, sprint M1" | M1's work (removing it), not what the entry shipped (2026-07-10): **unlinked by this fix** |
| Verbose error reporting (452) | M2, body: "since sprint M2, `ERROR_STYLE_VOICE` covers …" | M2's work in passing: not linked, before or after |
| `query_health` tool + `/health` dashboard (484) | M1, "(removed 2026-11-15, sprint M1)" (a plain parenthesis that opens with "removed") | M1's work in passing: not linked, before or after |
| the 15 entries with a lead link | their own id, also in paths and quotes in the body | linked by the lead, as before |

Financial expert is the one change to an existing link. M1 showed
"Financial expert, phase 1" as what it shipped; M1 is the removal of the
retired services, which the Refactor sprint entry cites as "(M1, M1b)". The
first-clause rule is what tells the two apart: no other Shipped lead has an
id after a `;` (the other leads with a `;` are Pandora station creation,
Pandora thumbs and MagicMirror updates, none naming an id). If the
coordinator reads "removed …, sprint M1" as M1's Shipped line, it is the
`split(";", 1)` in `roadmap_entry` to revert, and M1 then shows both.

No entry was found where a rule could not tell the two apart.

## Before (exporter at `b0a88cf`)

Every Shipped entry and the items it links, on the live data (export to
stdout, 12:30). The other 36 entries link nothing (in `shipped_unlinked`).

| Line | Entry | Links |
|---|---|---|
| 160 | `/work` drawn from the work-state database | M34 |
| 166 | A kept record of what Whisper heard, and "Heard" on `/work` | M30 |
| 174 | Time and date in the house's zone | M35 |
| 180 | Work-item state and the queue in a database | M33 |
| 185 | Devstral retired | M31 |
| 189 | A tool for launching and checking on agents | M28 |
| 194 | `/work`: the work items in a page | M27 |
| 201 | /resources shows real usage | M25 |
| 209 | Kronk's own ROCm libraries | M26 |
| 215 | Reclaim storage and memory; Talkie kept | M24 |
| 225 | The queue incident | M23 |
| 234 | The basement hand-off | M22 |
| 242 | Backups | M16, M16b |
| 251 | Upgrade cadence | M18 |
| 257 | `/settings` page and live config directories | M21-step1, M21-step2 |
| 444 | Financial expert, phase 1 | M1 |

Unlinked: 36, Refactor sprint among them.

## After

| Line | Entry | Links |
|---|---|---|
| 160 | `/work` drawn from the work-state database | M34 |
| 166 | A kept record of what Whisper heard, and "Heard" on `/work` | M30 |
| 174 | Time and date in the house's zone | M35 |
| 180 | Work-item state and the queue in a database | M33 |
| 185 | Devstral retired | M31 |
| 189 | A tool for launching and checking on agents | M28 |
| 194 | `/work`: the work items in a page | M27 |
| 201 | /resources shows real usage | M25 |
| 209 | Kronk's own ROCm libraries | M26 |
| 215 | Reclaim storage and memory; Talkie kept | M24 |
| 225 | The queue incident | M23 |
| 234 | The basement hand-off | M22 |
| 242 | Backups | M16, M16b |
| 251 | Upgrade cadence | M18 |
| 257 | `/settings` page and live config directories | M21-step1, M21-step2 |
| 261 | **Refactor sprint** | **M1, M1b, M2, M3, M4, M4b, M5, M7, M8, M9, M11, M12, M14, M17, M19** |
| 444 | Financial expert, phase 1 | **none** |

Unlinked: 36 (Refactor sprint out, Financial expert in).

The old Done rows with no handoff line, before → after: the fifteen of B1
(`done:16:M12`, `done:15:M9`, `done:14:M8`, `done:13:M11`, `done:12:M14`,
`done:11:M19`, `done:10:M7`, `done:7:M2`, `done:6:M5`, `done:5:M1b`,
`done:5:M3`, `done:5:M4`, `done:5:M4b`, `done:4:M1b`, `done:1:M17`) none →
"Refactor sprint"; `done:3:M1` "Financial expert, phase 1" → "Refactor
sprint"; M6, M10, M13, M20 none → none (named in no Shipped entry); the
other seven unchanged.

## Tests

New, `tests/test_m32_step2_fix1.py` (12). Against the exporter at
`b0a88cf`, 9 fail; the two open-entry and lead tests pass on both, and
the dev-export test skips (its file predated the fix).

- `test_a_body_citation_links_its_ids`: "(M17)", "(M1, M1b)", "(M3, M4 and
  M4b; `docs/…`)", "(M7, `docs/…`)" link those ids, in order.
- `test_an_id_in_passing_links_nothing`: "since sprint M2", "(removed …,
  sprint M1)", a lead "2026-07-10; removed …, sprint M1", a path and a quote
  link nothing.
- `test_a_leads_first_clause_still_links`; `test_an_open_entry_is_read_as_before`.
- On the real `ROADMAP.md`: `test_the_refactor_entry_cites_its_items` (the
  15 ids); `test_entries_that_name_an_item_in_passing_link_nothing`
  (Financial expert, Verbose error reporting, `query_health`);
  `test_every_other_shipped_entry_links_what_its_lead_names` (the after
  table, less Refactor sprint).
- `test_a_promoted_item_cited_in_a_body_gets_the_entry`: through `collect`
  on a fixture database and repo, a promoted M2 cited "(M2, …)" gets that
  entry and not the two that name it in passing.
- `test_the_fifteen_old_rows_have_their_shipped_line`: on dev's export (read
  only; skips when the file has no such rows or predates the fix), each of
  the fifteen rows shows "Refactor sprint", and it is not unlinked.
- `test_the_live_sources_arrive_whole[M32-685]`, `[M33-722]`: the source
  `criteria_head` gives equals the file's paragraph, 685 and 722 characters.
- `test_a_long_source_is_whole_up_to_the_bound`: 3,499 characters arrive
  whole through `criteria_head` and `collect`; 5,000 are clipped to 4,000
  with "…".

## Dev check

- The dev pair was not rebuilt: it serves `work.html` and `work_api.py`,
  neither changed; the exporter runs on the host.
- `python3 -B scripts/work_export.py --out data/dev/work.json`, 12:37:41.
- `GET http://localhost/dev/api/work`: 200, schema 4, `schema_known` 4,
  source fresh (10.3 s). 15 of the 15 B1 rows show "Refactor sprint" as
  their only Shipped entry; M6, M10, M13, M20 none; M1 "Refactor sprint".
  M32's source 685 characters and M33's 722, each equal to its file's
  `Source:` paragraph, no "…".
- Production: not touched by me. `kronk-work-export.timer` runs the
  working tree's exporter with no `--out`, so from 12:32 (the rename) it
  writes production's `data/work.json` with this rule too, as it has
  carried schema 4 since step 2's build. Production's page reads the same
  keys; only `shipped` and `source` values change.

## For the coordinator

- The Financial expert unlink (above) is the one reading call in this
  fix. One line reverts it.

## The suite

On the code, before this report existed (code commit `40cac4b`):

```
8563 passed, 3 skipped, 62 xfailed, 6 warnings in 217.12s (0:03:37)
```

With this report written and not yet committed, one failure:
`test_m34_work_db.py::test_criterion_2_every_difference_from_the_old_reader_has_a_class`,
`AssertionError: {'GONE': ['items[M32-step2].steps[M32-step2-fix1.md]']}`.
The old reader reads the working tree; `import-legacy --at HEAD` reads
only committed files, so an uncommitted step report is "GONE". Not a fault
of the fix. With the report committed (`8e12903`):

```
8563 passed, 3 skipped, 62 xfailed, 6 warnings in 217.40s (0:03:37)
```

## State at the end

Edited in scratch, moved in by rename (mode kept). No unit, timer, container
or production file touched by hand; no rebuild; no sound, no notification.
`.gitignore` (changed) and `esphome/` (untracked) are the operator's: not
touched, not staged.
