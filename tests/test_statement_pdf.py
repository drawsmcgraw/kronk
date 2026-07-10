"""Tests for the Fidelity statement VALUE path (statement_pdf.py + route).

Statement PDFs give per-account ending values — never positions — and only
when they reconcile against the statement's own stated total. Synthetic
fixture text mirrors the real 2026-06 statement's structure (verified
against it live, reconciliation delta $0.00)."""
import json

import pytest

import finance_service.statement_pdf as sp

STATEMENT_TEXT = """\
INVESTMENT REPORT
June 1, 2026 - June 30, 2026

Accounts Included in This Report
Page Account Type/Name
Account
Number Beginning Value Ending Value
Z24-945589 $250,000.00 $260,000.00
Z32-313219 100,000.00 105,000.00
Ending Portfolio Value $350,000.00 $365,000.00

Some later page repeats the phrase:
Ending Portfolio Value $1.00 $2.00

 Account # Z24-945589
DREW MALONE - JOINT WROS - TODAccount Summary

 Account # Z32-313219
JANE DOE - ROTH IRAHoldings
"""


def test_parse_statement_happy_path():
    as_of, accounts, total = sp.parse_statement(STATEMENT_TEXT)
    assert as_of == "2026-06-30"
    assert accounts == {"Z24-945589": 260000.0, "Z32-313219": 105000.0}
    assert total == 365000.0


def test_parse_uses_last_amount_as_ending_and_first_total_row():
    """Columns are Beginning|Ending → last amount wins; and only the first
    'Ending Portfolio Value' row after the section anchors the total."""
    as_of, accounts, total = sp.parse_statement(STATEMENT_TEXT)
    assert accounts["Z24-945589"] != 250000.0   # not the beginning value
    assert total != 2.0                          # not the later repeat


def test_reconciliation_failure_rejects():
    bad = STATEMENT_TEXT.replace("$260,000.00", "$999,000.00")
    with pytest.raises(sp.StatementParseError) as e:
        sp.parse_statement(bad)
    assert "reconciliation failed" in str(e.value)
    assert "CSV export" in str(e.value)


def test_missing_total_row_rejects():
    bad = "\n".join(l for l in STATEMENT_TEXT.splitlines()
                    if not l.startswith("Ending Portfolio Value"))
    with pytest.raises(sp.StatementParseError) as e:
        sp.parse_statement(bad)
    assert "refusing to import unverifiable values" in str(e.value)


def test_missing_period_header_rejects():
    bad = STATEMENT_TEXT.replace("June 1, 2026 - June 30, 2026", "")
    with pytest.raises(sp.StatementParseError):
        sp.parse_statement(bad)


def test_account_descriptors_found_and_denoised():
    descs = sp.account_descriptors(STATEMENT_TEXT)
    assert descs["Z24-945589"] == "DREW MALONE - JOINT WROS - TOD"
    assert descs["Z32-313219"] == "JANE DOE - ROTH IRA"  # fused 'Holdings' stripped


def test_kind_and_owner_inference():
    import finance_service.positions_db as pdb
    assert pdb.infer_kind("DREW MALONE - JOINT WROS - TOD") == "taxable"
    assert pdb.infer_owner("DREW MALONE - JOINT WROS - TOD") == "joint"
    assert pdb.infer_kind("JANE DOE - ROTH IRA") == "ira_roth"
    assert pdb.infer_kind("Rollover IRA") == "ira_trad"
    assert pdb.infer_kind("Thrift Savings Plan") == "tsp"
    assert pdb.infer_kind("Individual 401(k)") == "401k"
    assert pdb.infer_kind("Some Brokerage Account") == "taxable"
    assert pdb.infer_owner("Individual - TOD") == "user"


def test_duplicate_account_rows_collapse():
    """Chunked/overlapping text repeats rows — keyed by account number,
    duplicates are harmless."""
    dup = STATEMENT_TEXT.replace(
        "Z32-313219 100,000.00 105,000.00",
        "Z32-313219 100,000.00 105,000.00\nZ32-313219 100,000.00 105,000.00")
    _, accounts, _ = sp.parse_statement(dup)
    assert len(accounts) == 2


# ── route flow ────────────────────────────────────────────────────────────────

from tests.test_positions_api import fin_client, accounts  # fixtures  # noqa


def _upload_pdf(client, name="Statement06302026.pdf"):
    return client.post("/api/positions/import",
                       files={"file": (name, b"%PDF-fake", "application/pdf")})


def test_statement_route_flow_zero_questions(fin_client, monkeypatch):
    """Operator directive 2026-07-07: a statement upload asks NOTHING. First
    upload auto-creates accounts classified from the statement's own
    descriptors and imports in the same request."""
    import finance_service.main as main_mod
    monkeypatch.setattr(main_mod.sp, "extract_pdf_text", lambda data: STATEMENT_TEXT)

    r = _upload_pdf(fin_client)   # no accounts pre-created, no confirm step
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "imported"
    assert body["source"] == "statement_pdf"
    assert body["as_of_date"] == "2026-06-30"

    created = {c["account_id"]: c for c in body["created_accounts"]}
    assert created["z24_945589"]["kind"] == "taxable"    # JOINT WROS
    assert created["z24_945589"]["owner"] == "joint"
    assert created["z32_313219"]["kind"] == "ira_roth"   # ROTH IRA

    s = fin_client.get("/api/positions").json()
    assert s["totals"]["total"] == 365000.0
    assert s["totals"]["liquid"] == 260000.0        # joint taxable
    assert s["totals"]["age_gated"] == 105000.0     # roth

    # second month: same accounts recognized, nothing created
    month2 = STATEMENT_TEXT.replace("June 30, 2026", "July 31, 2026") \
                           .replace("July 1, 2026", "July 1, 2026")
    monkeypatch.setattr(main_mod.sp, "extract_pdf_text",
                        lambda data: month2.replace("June 1, 2026", "July 1, 2026"))
    r2 = fin_client.post("/api/positions/import",
                         files={"file": ("Statement07312026.pdf", b"%PDF-fake2",
                                         "application/pdf")})
    assert r2.json()["created_accounts"] == []


def test_statement_then_csv_same_date_does_not_double_count(fin_client, monkeypatch):
    """Snapshot-replace semantics: a positions CSV imported after a
    statement for the same account+date REPLACES the statement value row."""
    import finance_service.main as main_mod
    from tests.test_positions_api import (FIDELITY_CSV, FID_MAPPING,
                                          _upload, _confirm)
    monkeypatch.setattr(main_mod.sp, "extract_pdf_text", lambda data: STATEMENT_TEXT)

    # statement first — auto-creates z24_945589 and z32_313219
    assert _upload_pdf(fin_client).json()["status"] == "imported"

    # then a positions CSV for the same account + date (values differ —
    # 40k vs the statement's 260k — because it's a partial export)
    r = _upload(fin_client, FIDELITY_CSV, "fid-2026-06-30.csv")
    _confirm(fin_client, r.json()["fingerprint"], FID_MAPPING, "z24_945589")
    _upload(fin_client, FIDELITY_CSV, "fid-2026-06-30.csv")

    s = fin_client.get("/api/positions").json()
    acct = next(a for a in s["accounts"] if a["id"] == "z24_945589")
    holdings = {h["holding"] for h in acct["holdings"]}
    assert "Account value (statement)" not in holdings   # replaced, not added
    assert acct["value"] == 40000.0                       # CSV won (last import)


def test_non_statement_pdf_goes_to_llm_tier_then_rejects_with_advice(accounts, monkeypatch):
    """Since the extractor fallback (2026-07-07), a PDF without Fidelity
    anchors is handed to LLM extraction; when that also can't produce
    verified values, the 422 carries BOTH reasons + CSV advice."""
    import finance_service.main as main_mod
    monkeypatch.setattr(main_mod.sp, "extract_pdf_text",
                        lambda data: "just some random pdf text")

    async def no_json(prompt):
        return "there are no account values in this document"
    monkeypatch.setattr(main_mod.ex, "_call_llm", no_json)

    r = _upload_pdf(accounts, "random.pdf")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "Anchor parser:" in detail
    assert "LLM extraction:" in detail
    assert "CSV/XLSX export will work" in detail