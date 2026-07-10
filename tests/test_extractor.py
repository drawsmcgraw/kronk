"""Tests for LLM extraction behind the verification gate (extractor.py).

The reworded prime directive: no model output becomes a stored dollar
without passing an independent, deterministic check. These tests are that
check's regression suite — especially the transposed-digit case, which is
the exact silent failure the gate exists to catch."""
import pytest

import finance_service.extractor as ex

TSP_DOC = """\
Thrift Savings Plan — Quarterly Statement
Statement period: April 1, 2026 through June 30, 2026
Your account balance as of 06/30/2026

Fund Balances
C Fund $310,000.00
G Fund $45,000.00
Total Account Balance $355,000.00
"""

GOOD_JSON = ('{"as_of": "2026-06-30", "stated_total": 355000.00, '
             '"accounts": [{"id": "TSP", "ending_value": 355000.00, '
             '"type": "Thrift Savings Plan"}]}')


def _fake_llm(response: str):
    async def call(prompt):
        # the document text must ride along in the prompt
        assert "Fund Balances" in prompt
        return response
    return call


@pytest.mark.asyncio
async def test_verified_extraction_passes(monkeypatch):
    monkeypatch.setattr(ex, "_call_llm", _fake_llm(GOOD_JSON))
    as_of, accounts, total, descs = await ex.extract_and_verify(TSP_DOC, "tsp.pdf", None)
    assert as_of == "2026-06-30"
    assert accounts == {"TSP": 355000.0}
    assert total == 355000.0
    assert descs == {"TSP": "Thrift Savings Plan"}   # feeds kind inference


@pytest.mark.asyncio
async def test_transposed_digit_fails_the_gate(monkeypatch):
    """THE test: the model hallucinates one digit (355000 → 353000); the
    reconciliation gate must reject, never land."""
    bad = GOOD_JSON.replace('"ending_value": 355000.00', '"ending_value": 353000.00')
    monkeypatch.setattr(ex, "_call_llm", _fake_llm(bad))
    with pytest.raises(ex.ExtractionError) as e:
        await ex.extract_and_verify(TSP_DOC, "tsp.pdf", None)
    assert "verification failed" in str(e.value)


@pytest.mark.asyncio
async def test_no_stated_total_refuses(monkeypatch):
    bad = GOOD_JSON.replace('"stated_total": 355000.00', '"stated_total": null')
    monkeypatch.setattr(ex, "_call_llm", _fake_llm(bad))
    with pytest.raises(ex.ExtractionError) as e:
        await ex.extract_and_verify(TSP_DOC, "tsp.pdf", None)
    assert "no total we can verify against" in str(e.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    "I could not find any accounts, sorry!",          # no JSON
    '{"accounts": "oops"}',                            # wrong shape
    '{"stated_total": 1, "accounts": []}',             # empty
    '{"stated_total": 1, "accounts": [{"id": "X", "ending_value": "many"}]}',
])
async def test_malformed_model_output_rejects(monkeypatch, response):
    monkeypatch.setattr(ex, "_call_llm", _fake_llm(response))
    with pytest.raises(ex.ExtractionError):
        await ex.extract_and_verify(TSP_DOC, "tsp.pdf", None)


@pytest.mark.asyncio
async def test_as_of_falls_back_to_filename(monkeypatch):
    no_date = GOOD_JSON.replace('"as_of": "2026-06-30"', '"as_of": null')
    monkeypatch.setattr(ex, "_call_llm", _fake_llm(no_date))
    as_of, _, _, _ = await ex.extract_and_verify(TSP_DOC, "TSP06302026.pdf", None)
    assert as_of == "2026-06-30"


# ── route fallback: anchors fail → LLM extraction, same account_map flow ─────

from tests.test_positions_api import fin_client, accounts  # fixtures  # noqa


def test_pdf_without_fidelity_anchors_uses_llm_fallback(fin_client, monkeypatch):
    """TSP-style PDF: anchors fail → LLM extracts → gate verifies → the
    account is auto-created from the extracted type — zero questions."""
    import finance_service.main as main_mod
    monkeypatch.setattr(main_mod.sp, "extract_pdf_text", lambda data: TSP_DOC)

    async def fake_call(prompt):
        return GOOD_JSON
    monkeypatch.setattr(main_mod.ex, "_call_llm", fake_call)

    r = fin_client.post("/api/positions/import",
                        files={"file": ("tsp-q2.pdf", b"%PDF-fake", "application/pdf")})
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "imported"
    assert body["source"] == "statement_llm"
    assert body["created_accounts"][0]["account_id"] == "tsp"
    assert body["created_accounts"][0]["kind"] == "tsp"   # from extracted type
    assert body["accounts"] == [{"account_id": "tsp", "rows": 1,
                                 "total_value": 355000.0}]


def test_both_tiers_failing_gives_both_reasons(accounts, monkeypatch):
    import finance_service.main as main_mod
    monkeypatch.setattr(main_mod.sp, "extract_pdf_text",
                        lambda data: "an unrelated pdf with no dollar values")

    async def fake_call(prompt):
        return "no accounts here"
    monkeypatch.setattr(main_mod.ex, "_call_llm", fake_call)

    r = accounts.post("/api/positions/import",
                      files={"file": ("random.pdf", b"%PDF-fake", "application/pdf")})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "Anchor parser:" in detail
    assert "LLM extraction:" in detail
    assert "CSV/XLSX export will work" in detail