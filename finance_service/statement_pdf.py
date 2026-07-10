"""Fidelity statement PDF → per-account ENDING VALUES (not positions).

The operator's lens is "value, in USD" — and while per-position tables in
statement PDFs extract as untrustworthy fused number streams, the
account-summary section extracts cleanly: labeled per-account ending
values plus a stated portfolio total. That total is the safety net: the
parse is accepted only when the account values reconcile against it
(0.5% / $1 tolerance), so a mangled extraction rejects loudly instead of
landing wrong dollars (tenet 6; FINANCIAL_EXPERT_PLAN amendments).

Anchors (verified against the operator's real 2026-06 statement):
  "Accounts Included in This Report"      — summary table start
  "Z##-######  $<beginning>  $<ending>"   — one row per account
  "Ending Portfolio Value  $<beg> $<end>" — table total row
  "June 1, 2026 - June 30, 2026"          — period header → as-of date

No OCR anywhere: these are digital PDFs; pypdf text extraction only.
"""
import io
import re

MONEY_RE = re.compile(r"\$?([\d,]+\.\d{2})")
ACCT_ROW_RE = re.compile(r"^([A-Z]?\d{2,3}-\d{5,6})\b(.*)$")
PERIOD_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+(\d{4})\s*[-–]\s*"
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),\s+(\d{4})")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

SECTION_START = "Accounts Included in This Report"
TOTAL_ANCHOR = "Ending Portfolio Value"


class StatementParseError(Exception):
    """Statement rejected — carries the specific, user-facing reason."""


def looks_like_statement(text: str) -> bool:
    return SECTION_START in text


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        raise StatementParseError(f"could not read PDF: {e}")


def _money(s: str) -> float:
    return float(s.replace(",", ""))


ACCT_ANCHOR_RE = re.compile(r"Account\s*#\s*([A-Z]?\d{2,3}-\d{5,6})")
# Section titles that PDF text extraction fuses onto the descriptor line.
_DESC_NOISE_RE = re.compile(
    r"(Account Summary|Holdings|Activity|Income Summary).*$", re.IGNORECASE)


def account_descriptors(text: str) -> dict[str, str]:
    """{account_number: descriptor} — the type/name line that follows each
    'Account # X' anchor (e.g. 'DREW MALONE - JOINT WROS - TOD',
    'JANE DOE - ROTH IRA'). Used to auto-classify accounts so the operator
    is asked zero questions."""
    descs: dict[str, str] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = ACCT_ANCHOR_RE.search(line)
        if not m or m.group(1) in descs:
            continue
        for nxt in lines[i + 1:i + 4]:
            nxt = _DESC_NOISE_RE.sub("", nxt).strip(" -")
            if nxt:
                descs[m.group(1)] = nxt
                break
    return descs


def parse_statement(text: str) -> tuple[str, dict[str, float], float]:
    """Returns (as_of_date, {account_number: ending_value}, stated_total).
    Raises StatementParseError on any structural or reconciliation failure."""
    m = PERIOD_RE.search(text)
    if not m:
        raise StatementParseError(
            "no statement period header found (e.g. 'June 1, 2026 - June 30, 2026')")
    as_of = f"{int(m.group(5)):04d}-{_MONTHS[m.group(3)]:02d}-{int(m.group(4)):02d}"

    idx = text.find(SECTION_START)
    if idx < 0:
        raise StatementParseError(
            f"no '{SECTION_START}' section — not a recognized statement layout")
    section = text[idx:]

    accounts: dict[str, float] = {}
    stated_total: float | None = None
    for line in section.splitlines():
        line = line.strip()
        am = ACCT_ROW_RE.match(line)
        if am:
            amounts = MONEY_RE.findall(am.group(2))
            if len(amounts) >= 2:
                # columns are Beginning | Ending — the LAST amount is ending.
                # Duplicate rows (text-chunk overlap, repeated headers) are
                # harmless: keyed by account number, last one wins.
                accounts[am.group(1)] = _money(amounts[-1])
            continue
        if line.startswith(TOTAL_ANCHOR) and stated_total is None:
            amounts = MONEY_RE.findall(line)
            if amounts:
                stated_total = _money(amounts[-1])
            # Only the FIRST total row after the section start counts — later
            # pages repeat the phrase in other contexts.

    if not accounts:
        raise StatementParseError(
            "found the summary section but no account rows in it")
    if stated_total is None:
        raise StatementParseError(
            f"no '{TOTAL_ANCHOR}' total row to reconcile against — refusing "
            "to import unverifiable values")

    total = sum(accounts.values())
    if abs(total - stated_total) > max(abs(stated_total) * 0.005, 1.0):
        raise StatementParseError(
            f"reconciliation failed: account values sum to {total:,.2f} but "
            f"the statement says {stated_total:,.2f} — the PDF text extraction "
            "is not trustworthy for this file; use a CSV export instead")

    return as_of, accounts, stated_total
