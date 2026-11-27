# M32 step 2 retest: fix 1 (B1 and S1)

Verdict: PASSES

Narrow retest agent (Opus 5.5), 2026-11-27, 12:45 to 13:05 local (EST), on
`ab0195b` (fix commit `40cac4b`). Contract: `docs/sprint/M32-criteria.md`,
"Rulings on step 2's test"; `M32-step2-e2e.md` B1 and S1; the fix report
`M32-step2-fix1.md`. Scope: the fix's diff, the criteria it touches, the
suite. I changed no code and added no test; this report is the only file
committed.

## Verdicts

| Check | Verdict |
|---|---|
| 1. B1 on `/dev/work` | **Passes**: the fifteen rows each show "Refactor sprint (2026-11-15 to 2026-11-21)" under "Shipped as"; M6, M10, M13, M20 none; M1 "Refactor sprint" only; every other Done row as the fix report's after table |
| 2. The rule against the real Shipped section | **Passes**: no item linked by a mention in passing; no entry that states an item shipped left unlinked |
| 3. S1, Why whole | **Passes**: M32's and M33's Why equal their `Source:` paragraphs whole |
| 4. 2a, 2b, 2j on the touched rows | **Passes** |
| 5. The suite | **Passes**: `8563 passed, 3 skipped, 62 xfailed` |

## 1. B1

Headless Firefox at 1280×900 through geckodriver, a bare WebDriver client,
and a loopback proxy that passed only `/dev/` paths to port 80 (Firefox's
own traffic to a second listener answering 403), with one injected
`<script>` recording `error`, `unhandledrejection`, `console.error` and
`console.warn` (the method of `M32-step2-e2e.md`, "How it was tested").
Every Done row (38) and M32's card scrolled into view, clicked open, read,
clicked closed.

| Rows | Shipped as on the page |
|---|---|
| `done:16:M12`, `done:15:M9`, `done:14:M8`, `done:13:M11`, `done:12:M14`, `done:11:M19`, `done:10:M7`, `done:7:M2`, `done:6:M5`, `done:5:M1b`, `done:5:M3`, `done:5:M4`, `done:5:M4b`, `done:4:M1b`, `done:1:M17` (15) | "Refactor sprint (2026-11-15 to 2026-11-21)", one line each |
| `done:3:M1` | "Refactor sprint (2026-11-15 to 2026-11-21)" only; no "Financial expert" |
| `done:8:M6`, `done:2:M10`, `done:17:M13`, `done:20:M20` | no "Shipped as" part: title, open leftovers and steps |
| M34, M30, M35, M33, M31, M28, M27, M25, M26, M24, M23, M22, M16b, M16, M18, M21-step2, M21-step1 | their own entry, as the after table: M16 and M16b both "Backups", both M21 steps "`/settings` page and live config directories" |
| `done:39:M36` | none, before and after (named in no Shipped entry) |

Against the fix report's before/after tables, the only rows whose Shipped
line changed are the fifteen (none → Refactor sprint) and M1 (Financial
expert → Refactor sprint). No other row lost or gained one.

## 2. The rule

Every id in `ROADMAP.md`'s Shipped section (line 154 on), by entry:

- The section's intro (line 157, "items before M34"): not an entry.
- The fifteen lead-linked entries (lines 160 to 257): each lead names its
  own item ("work item M34", "sprint M16 and M16b", "sprint M21"); the body
  names the same id only in a path or a quote (`M34-promote.md`, "M30"),
  which links nothing new. Each is the statement that item shipped.
- Refactor sprint (261): the fifteen ids are in parentheses after the part
  each shipped ("removal of the retired services (M1, M1b)", "one lock per
  model server (M9, …)"). Each is a statement that the item shipped that
  part. Linked.
- Financial expert, phase 1 (444): "2026-07-10; removed 2026-11-15, sprint
  M1". M1 removed it; the entry is what shipped on 2026-07-10. Unlinked by
  the `;` rule. Correct.
- Verbose error reporting (452): "since sprint M2, `ERROR_STYLE_VOICE`
  covers …" is M2's later change named in passing. Unlinked. Correct.
- `query_health` tool (484): "(removed 2026-11-15, sprint M1)". The
  parenthesis opens with "removed", so `CITE_RE` does not match. Unlinked.
  Correct.

No other id appears in the section. The 36 unlinked entries name no id,
so none can be linked by rule. `CITE_RE` requires the parenthesis to open
with an id list followed by `;`, `,` or `)`; no prose parenthesis in the
section opens that way. No item is linked by an entry that names it only in
passing, and no entry that states an item shipped is left unlinked.

The Financial expert unlink is the one reading call the fix flags for the
coordinator. I read it the same way the fix does.

## 3. S1

On the page, the `.why` text of `uw:M32` and `done:33:M33` against my own
parse of each criteria file's `Source:` paragraph (lines joined, the
`Source:` word dropped):

| Item | File | Page | Equal |
|---|---|---|---|
| M32 | 685 characters | 683 | yes, with backticks and stars dropped (the page renders them as `code`/em) |
| M33 | 722 | 722 | yes, character for character |

M32's ends "…to help visualize the state of a current work item." with no
clip. M33's text holds two "…" of Drew's own (file lines 13 and 18), not a
clip; it ends "…pick up where we left off".

## 4. 2a, 2b, 2j on the touched rows

- **2a**: each of the 38 Done rows opened on one click and closed on the
  next (the open state read after each click). M32 open by default.
- **2b**: two sessions. In each, rows marked (an expando on every row node
  and its body node), touched rows opened by click (`done:16:M12`,
  `done:7:M2`, `done:3:M1`, `done:1:M17`; then `done:5:M1b`, `done:4:M1b`,
  `done:5:M4`, `done:3:M1`), then real exports of the live data written to
  dev's file (`work_export.py --out data/dev/work.json`; one, then two).
  The state line's export time moved (12:54 → 12:55 in the second session),
  so the page took the new file. After: 42 of 42 rows the same node, every
  body the same node, the same keys in the same order, the opened rows
  still open, every body's text the same length.
- **2j**: no entry in the error log in either session; no visible text
  node holds "undefined" (the 15 matches of `body.textContent` are in the
  page's own `<script>` source, `line: undefined`).

## 5. The suite

On `ab0195b`, before this report existed in the tree:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
===== 8563 passed, 3 skipped, 62 xfailed, 6 warnings in 216.00s (0:03:35) ======
```

The same count as the fix report's run with its report committed. The
report was committed before any further run, so `test_m34_work_db`'s
uncommitted-step check (fix report, "The suite") does not apply.

## Notes

1. `done:39:M36` (a follow-up can refer to the turn before) is promoted and
   shows no Shipped line: ROADMAP's Shipped section has no entry for it.
   A ROADMAP gap, not this fix's; the row is right to show none.
2. Not run, outside the narrow scope: 2c to 2i, plumbing 4 and 9, the
   1920×1080 wall, zones, the gjs harness.

## Guard rails

None broken. Production: no request; only `/dev/` routes through the proxy.
No rebuild or restart; no timer touched; no unit installed. Dev's
`data/dev/work.json` written three times by the exporter from the live data
(no fixture); it holds the live data at the end. No sound, no notification.
No household transcript text read or printed (Heard not opened).

## State at the end

No browser, geckodriver or proxy running. Scratch holds the harness and its
JSON output. `.gitignore` (changed) and `esphome/` (untracked) are the
operator's: not touched, not staged.
