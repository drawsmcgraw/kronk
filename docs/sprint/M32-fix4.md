# M32 fix 4: the Shipped-links test stops pinning the whole section

Fix agent (Opus 5.5), 2026-11-27, from `57f3df4`. M32 is promoted; this is a
test repair, not a change to what M32 shipped.

## The failure

On HEAD, after `db55bca` (M32's Shipped entry) and `28887d0` (M36's):

```
FAILED tests/test_m32_step2_fix1.py::test_every_other_shipped_entry_links_what_its_lead_names
E         {'A follow-up can refer to the turn before': ['M36'],
E          '`/work` for a wall display': ['M32']}
```

Both entries link their own item, by their leads ("work item M36", "work
item M32"), as they should. The test compared every linked Shipped entry
against a fixed table, so any new Shipped entry failed it.

## What changed

`tests/test_m32_step2_fix1.py` only. The exporter and `ROADMAP.md` are
unchanged.

- `test_every_other_shipped_entry_links_what_its_lead_names` now checks, on
  the live `ROADMAP.md`:
  1. every Shipped entry other than Refactor sprint links exactly the ids
     its lead's first clause names (before the first `;`), entries added
     later included. Entries whose lead names nothing must link nothing,
     so the three in-passing entries (Financial expert, Verbose error
     reporting, `query_health`) are covered here too, beside their own
     test;
  2. Refactor sprint links something (its 15 ids stay pinned in
     `test_the_refactor_entry_cites_its_items`);
  3. the 15 entries of the old table (`OLD_LINKS`) still link what they did.
- "What the lead names" is read by the test itself (`_lead_names`): every
  `M<n>[a-z]` (optionally `step <n>`) before the first `;`, through the
  exporter's `norm_id`. It does not use the exporter's `LINK_RE`, so the
  test is not the exporter checking itself.
- New, `test_the_lead_rule_reads_what_a_new_entry_names`: on five leads of
  the kinds the section uses (single id, "M16 and M16b", an id after `;`,
  an id only after `;`, "item 2"), the plain reading and the exporter
  agree, with a body that names an item in passing ("since sprint M9", a
  path) that must add nothing.

Why this shape: the test's job (`M32-step2-fix1.md`) is that the body
citation rule adds links only to Refactor sprint and that an id named in
passing links nothing. Both are rules over every entry, so the test states
them as rules. The fixed table stays only for the entries it was written
against.

## It still catches what it was for

Run against the exporter before the fix (`b0a88cf`), the new rule check
finds exactly one entry wrong:

```
old exporter differs: Financial expert, phase 1 ['M1'] []
```

That is the change fix 1 made (the first-clause rule). On HEAD, all 55
Shipped entries other than Refactor sprint agree with their leads.

## The suite

`./scripts/run_tests.sh`, with the test changed and this report not yet
written:

```
===== 8593 passed, 3 skipped, 62 xfailed, 6 warnings in 218.19s (0:03:38) ======
```

With the test and this report committed (`3c2ea66`):

```
===== 8593 passed, 3 skipped, 62 xfailed, 6 warnings in 218.05s (0:03:38) ======
```

## State at the end

No deploy, no tag, no production request, no container or unit touched.
`.gitignore` (changed) and `esphome/` (untracked) are the operator's: not
touched, not staged.
