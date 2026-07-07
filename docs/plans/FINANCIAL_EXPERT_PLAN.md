# Financial expert — Plan

Status: **draft** — written 2026-07-07 from the design conversation.
Operator decisions incorporated; awaiting sign-off on the phases + open
questions at the bottom.

The finance agent grows into an expert on the operator's actual investment
positions, in service of one goal: **early retirement** (operator retires
at 55; spouse is FERS — scenario details live in the retirement-calc
config and `memory/project_retirement_calc.md`). First interactions are
chat-UI questions: "am I on track?" and "help me calculate my retirement
number"; what-ifs come next.

## Prime directive

**Deterministic math in code; the LLM narrates — never the reverse.**
Every number the expert speaks comes out of a tool (positions DB, the
simulation library). The model explains, compares, and routes; it is
structurally prevented from arithmetic the same way terminal tools prevent
it from claiming unverified success. A model that "estimates" a Monte
Carlo success rate is trust-destroying.

Clarified 2026-07-07: this is a **correctness** rule, not a privacy rule.
The operator is explicitly fine with the model seeing all position data
(it's local — tenet 1 is the privacy layer). So: full position detail
flows freely into prompts for narration and analysis; what stays
forbidden is the model *producing* numbers — transcribing them from
documents (ingestion) or computing them (simulation). Minimize data in
prompts only where it measurably helps latency/context, never for privacy
theater.

## Operator decisions (2026-07-07)

1. Positions live everywhere — 401k, TSP (spouse), IRAs, ETFs, mutual
   funds. **Monthly manual export, format NOT guaranteed** (CSV/XLSX/PDF/
   whatever the brokerage emits). Kronk must be clever about parsing.
2. **Liquid vs non-liquid is a first-class distinction**: money accessible
   now (taxable ETFs) vs age-gated (401k/TSP/IRA). This is *the* early-
   retirement question — retiring at 55 means the liquid slice must bridge
   the years until the gated accounts unlock.
3. retirement-calc: operator suspected throwaway; analysis says **throw
   away the app, absorb the math** (verdict below).
4. Chat UI first; voice later. Positions data visible **only to the
   finance expert** (per-service data mounts — review P3.7 gets fixed as
   part of this feature, not after it).

## retirement-calc autopsy (2026-07-07)

Analyzed `~/git-repos/drawsmcgraw/retirement-calc/` (backend 963 lines,
frontend 1,134).

**Absorb (the crown jewels, ~520 lines):**
- `fers.py` — complete OPM FERS scenario matrix: MRA table by birth year,
  age-62 1.1% multiplier rule, MRA+30, MRA+10 with 5%/yr reductions,
  deferred variants, SBP election. Validated domain logic that would be
  painful and risky to rewrite.
- `calc.py` — year-by-year simulation with accumulation/partial/drawdown
  phases, per-account contributions with IRS limits, FERS COLA rule
  (inflation − 1%), tax-efficient withdrawal ordering (taxable →
  traditional → roth).
- `monte_carlo.py` — clean numpy percentile bands (p10–p90) + success
  rate. `taxes.py` — informational tax estimates.

**Discard:** the frontend, the standalone service posture, the
config-blob input model (hand-typed balances).

**Gaps the absorption must fix:**
- **No liquidity gating anywhere** — the simulator will happily drain a
  401k at 55. Withdrawal order is tax-based only. This is the single
  biggest math gap given decision 2.
- **Zero tests.** The FERS matrix gets golden tests during absorption
  (one per OPM scenario, hand-checked), Monte Carlo gets a fixed-seed
  regression test, the simulator gets phase-boundary tests. Non-negotiable
  per definition-of-done.
- Balances are configured, not ingested — the library gets fed from the
  positions store instead.
- IRS contribution limits hardcoded to 2025 — move to a small table with a
  year key so update-day can bump them.

Destination: `finance_service/retirement/` (a library, not a service).
The `retire_calc` container keeps running until the expert reaches parity;
retiring it is an operator call at the end.

## Data model (finance_service SQLite, new tables)

- `accounts`: id, name, institution, kind (401k|tsp|ira_trad|ira_roth|
  taxable|hsa|cash), owner (user|spouse|joint), **liquidity**
  (liquid|age_gated), **unlock_age** (default 59.5 for gated; operator-
  overridable per account — TSP-at-55 and Rule-of-55 nuances land as
  overrides, not code, in v1), tax_treatment (taxable|traditional|roth).
- `position_snapshots`: account_id, as_of_date, ticker/holding name,
  shares, price, value, source_file. History is the point (trajectory,
  contribution tracking, "what changed since March"), and **ingest is
  strictly upsert, never duplicate** (operator requirement 2026-07-07):
  `UNIQUE(account_id, as_of_date, holding)` with upsert semantics, plus a
  file-hash check so re-importing the same export is a clean no-op.
  (Same lesson as the health importer's salted-hash duplication bug —
  P0.2 — but designed in from day one.)
- `roth_basis`: account_id, contribution basis (operator-supplied starting
  value, then maintained from snapshots/contributions). Basis is
  withdrawable before 59.5 — it's real bridge liquidity.
- `import_mappings`: source fingerprint (normalized header signature) →
  column mapping + parse options, confirmed_by_operator flag.
- Raw uploads preserved at `data/finance/raw/<date>-<original-name>`
  (provenance; re-import after mapping fixes).

## Ingestion: arbitrary formats, safely ("Kronk must be clever")

Two-stage design that keeps the LLM away from numbers:

1. **Mapping discovery (LLM-assisted, once per source).** Sniff format
   (CSV/TSV/XLSX/OFX/QFX/JSON; PDF via table extraction). Show the model
   headers + 3 sample rows; it proposes a mapping to the canonical schema
   (which column is ticker, shares, value…) and which account this looks
   like. The mapping — not the data — is the LLM's output.
2. **Deterministic extraction + invariants (every import).** Code applies
   the mapping to every row. Validation gates before anything lands:
   every row parses numerically; shares × price ≈ value where all three
   exist; file total matches a stated total row when present; as-of date
   detected or supplied. Any failure → the import is rejected loudly with
   row-level detail (verbose-errors standard), never partially ingested.
3. First import from a new source shows the proposed mapping + parsed
   preview for operator confirmation in the chat UI/finances page; the
   fingerprint remembers it, so month 2 onward is zero-LLM, zero-friction.
4. Unparseable PDFs fail with "export CSV/XLSX from this brokerage
   instead" — we do not OCR-guess at money.

## The expert (agent tools, phased)

Phase 1 — store + ingest: tables, importer, mapping memory, upload path
  (the /finances page already takes uploads), **per-service data-mount
  migration** (`./data/finance:/data` for finance_service; orchestrator
  and tool_service lose sight of it).
Phase 2 — math absorption: `finance_service/retirement/` library + golden
  tests + **liquidity-gated withdrawal ordering** (liquid taxable funds the
  bridge; gated accounts unlock at each account's unlock_age; simulation
  fails honestly if the bridge runs dry even when the total is sufficient
  — that's the whole point).
Phase 3 — tools on the finance agent:
  - `query_positions(group_by=account|liquidity|owner|ticker)` — includes
    the liquid-vs-gated split in every summary.
  - `retirement_readiness()` — Monte Carlo with live positions: success
    rate, bridge verdict ("liquid covers 55→59.5 with $X margin"), delta
    vs target.
  - `retirement_number(spend_monthly?, retire_age?)` — solve for the
    required portfolio (total AND minimum-liquid-slice) given SWR, FERS
    income, bridge years. Answers "help me calculate my number".
  Chat UI is the venue; answers cite as-of dates ("based on your June 30
  snapshot").
Phase 4 — what-ifs: `run_scenario(retire_age, spend, return_assumptions)`
  → comparison against baseline.
Phase 5 — **bridge strategies** (operator ask 2026-07-07: "I need to know
  all options available to me — IRA ladders or any other maneuvers").
  A `bridge_options()` tool that computes, from actual positions, what
  each early-access mechanism could yield for the 55→unlock gap:
  - Roth conversion ladder (convert $X/yr starting ~5 yrs out; each rung
    accessible after its 5-year clock — the tool lays out the timeline);
  - 72(t)/SEPP substantially-equal payments (IRS formula, computed);
  - Rule of 55 (401k of the employer separated from at 55+ — flag
    applicability per account);
  - Roth contribution basis (already tracked; available immediately);
  - plain taxable bridge (the default).
  Rules and arithmetic in code; the model explains trade-offs. The
  simulator later grows mechanism toggles so what-ifs can compare "ladder
  vs 72(t)" as scenarios.
Later (separate roadmap lines): daily prices via the context cache
  (item 5), readiness on the MagicMirror, monthly proactive digest.

Model: gemma-4-e4b stays until proven insufficient — with all math in
tools, the model's job is routing and narration. Re-bench only on evidence
(single-variable tenet).

## Security posture

- Positions are the most sensitive data on the box. finance_service gets
  `./data/finance:/data` (its own subtree); the shared `./data` mount is
  removed from it; other services keep no path to the positions DB.
  (First mover on review P3.7 — health_service et al. migrate later.)
- Nothing leaves the box (tenet 1): no aggregators, no cloud quotes in v1.
  Price freshness = whatever the monthly snapshot says, clearly dated.
- `data/finance/raw/` holds statements — it's inside the existing backup
  scope (ROADMAP item 4 must ship before this holds much history).

## Resolved questions (operator, 2026-07-07)

1. Spouse's TSP: **in the export ritual** (imported like everything else).
2. Roth basis: **tracked**, and scope expanded to the full bridge-
   strategies menu (phase 5).
3. Valuation: **monthly snapshots fine, maybe quarterly.** Hard
   requirement instead: **upsert-only ingest, never duplicate.** Answers
   always cite the as-of date.
4. retire_calc: **throw away the app/container** once the expert answers
   the same questions (end of phase 3; its `docker-compose.yml` service
   block, the `/retire/` nginx location, and `data/retire/` go with it).
5. Data visibility: model may see everything (local box); determinism
   requirements are for correctness only.
6. Backups: operator decides destination when ready; worst case,
   positions re-import from the raw exports.
