"""Tests for finance_service/importer.py — deterministic extraction.

The invariants ARE the product (docs/plans/FINANCIAL_EXPERT_PLAN.md): any
bad row rejects the whole import with row-level detail; the LLM proposes
column mappings only and its output is structurally validated."""
import io

import pytest

import finance_service.importer as imp


# ── money / date primitives ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("$1,234.56", 1234.56),
    ("1234.56", 1234.56),
    ("(123.45)", -123.45),
    ("$0.00", 0.0),
    (1234, 1234.0),
    ("  $9,999,999.01 ", 9999999.01),
])
def test_parse_money(raw, expected):
    assert imp.parse_money(raw) == expected


@pytest.mark.parametrize("raw", ["", "-", "N/A", "abc", "$--"])
def test_parse_money_rejects_junk(raw):
    with pytest.raises(ValueError):
        imp.parse_money(raw)


@pytest.mark.parametrize("raw,expected", [
    ("2026-06-30", "2026-06-30"),
    ("6/30/2026", "2026-06-30"),
    ("06/30/2026 16:00:00"[:10], "2026-06-30"),
])
def test_parse_date_cell(raw, expected):
    assert imp.parse_date_cell(raw) == expected


# ── sniffing / parsing ────────────────────────────────────────────────────────

FIDELITY_CSV = b"""\
Account Name,Symbol,Description,Quantity,Last Price,Current Value
Brokerage,VTI,VANGUARD TOTAL STOCK MKT ETF,100,"$275.50","$27,550.00"
Brokerage,VXUS,VANGUARD TOTAL INTL STOCK ETF,200,"$62.25","$12,450.00"
"""

TSP_CSV_NO_SHARES = b"""\
Fund,Balance as of 06/30/2026
C Fund,"$310,000.00"
G Fund,"$45,000.00"
Total,"$355,000.00"
"""

CSV_WITH_PREAMBLE = b"""\
Positions for account X123-456 as of 06/30/2026

Symbol,Description,Value
VTI,Total Market,"$1,000.00"
"""


def test_parse_csv_basic():
    parsed = imp.sniff_and_parse(FIDELITY_CSV, "positions.csv")
    assert parsed.format == "csv"
    assert "Current Value" in parsed.headers
    assert len(parsed.rows) == 2


def test_parse_csv_skips_preamble_lines():
    parsed = imp.sniff_and_parse(CSV_WITH_PREAMBLE, "export.csv")
    assert parsed.headers == ["Symbol", "Description", "Value"]
    assert len(parsed.rows) == 1


def test_parse_xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Fund", "Market Value"])
    ws.append(["C Fund", 310000.00])
    buf = io.BytesIO()
    wb.save(buf)
    parsed = imp.sniff_and_parse(buf.getvalue(), "tsp.xlsx")
    assert parsed.format == "xlsx"
    assert parsed.headers == ["Fund", "Market Value"]
    assert parsed.rows[0]["Market Value"] == 310000.00


def test_pdf_and_ofx_rejected_with_advice():
    for name in ("statement.pdf", "positions.ofx"):
        with pytest.raises(imp.ImportError422) as e:
            imp.sniff_and_parse(b"whatever", name)
        assert "export CSV or XLSX" in str(e.value)


def test_fingerprint_ignores_order_and_case():
    a = imp.fingerprint(["Symbol", "Current Value", "Quantity"])
    b = imp.fingerprint(["quantity", "symbol", "CURRENT VALUE"])
    c = imp.fingerprint(["Symbol", "Current Value"])
    assert a == b
    assert a != c


# ── mapping application + invariants ──────────────────────────────────────────

FID_MAPPING = {"columns": {"holding": "Symbol", "value": "Current Value",
                           "shares": "Quantity", "price": "Last Price"}}


def _parsed(csv_bytes, name="f.csv"):
    return imp.sniff_and_parse(csv_bytes, name)


def test_apply_mapping_happy_path():
    groups, as_of = imp.apply_mapping(_parsed(FIDELITY_CSV), FID_MAPPING,
                                      "2026-06-30", "f.csv")
    rows = groups[None]   # no account column → single anonymous group
    assert len(rows) == 2
    assert rows[0] == {"holding": "VTI", "value": 27550.0,
                       "shares": 100.0, "price": 275.5}
    assert as_of == "2026-06-30"


def test_value_only_rows_are_first_class():
    """The operator's lens: 'value, in USD'. A TSP-style export with no
    shares/price columns must import cleanly, and its Total row must be
    used for reconciliation, not ingested as a holding."""
    mapping = {"columns": {"holding": "Fund", "value": "Balance as of 06/30/2026"}}
    groups, as_of = imp.apply_mapping(_parsed(TSP_CSV_NO_SHARES), mapping,
                                      "2026-06-30", "tsp.csv")
    rows = groups[None]
    assert [r["holding"] for r in rows] == ["C Fund", "G Fund"]
    assert all("shares" not in r for r in rows)


def test_shares_times_price_mismatch_rejects_whole_import():
    bad = FIDELITY_CSV.replace(b'"$27,550.00"', b'"$99,999.00"')
    with pytest.raises(imp.ImportError422) as e:
        imp.apply_mapping(_parsed(bad), FID_MAPPING, "2026-06-30", "f.csv")
    assert "shares x price" in str(e.value)
    assert "VTI" in str(e.value)  # row-level detail


def test_stated_total_mismatch_rejects():
    bad = TSP_CSV_NO_SHARES.replace(b'"$355,000.00"', b'"$999,999.00"')
    mapping = {"columns": {"holding": "Fund", "value": "Balance as of 06/30/2026"}}
    with pytest.raises(imp.ImportError422) as e:
        imp.apply_mapping(_parsed(bad), mapping, "2026-06-30", "t.csv")
    assert "stated total" in str(e.value)


def test_unparseable_cell_rejects_with_row_number():
    bad = FIDELITY_CSV.replace(b'"$62.25"', b"n/a")
    with pytest.raises(imp.ImportError422) as e:
        imp.apply_mapping(_parsed(bad), FID_MAPPING, "2026-06-30", "f.csv")
    assert "row 2" in str(e.value)


def test_as_of_resolution_order():
    # filename date used when nothing else is available
    _, as_of = imp.apply_mapping(_parsed(FIDELITY_CSV), FID_MAPPING,
                                 None, "positions-2026-06-30.csv")
    assert as_of == "2026-06-30"
    # brokerage statement convention — MMDDYYYY, no separators (the
    # operator's real file was Statement06302026.pdf)
    _, as_of = imp.apply_mapping(_parsed(FIDELITY_CSV), FID_MAPPING,
                                 None, "Statement06302026.csv")
    assert as_of == "2026-06-30"
    # no date anywhere → rejected with instructions
    with pytest.raises(imp.ImportError422) as e:
        imp.apply_mapping(_parsed(FIDELITY_CSV), FID_MAPPING, None, "positions.csv")
    assert "as_of" in str(e.value)


def test_validate_mapping_catches_bad_columns():
    errs = imp.validate_mapping({"columns": {"holding": "Nope", "value": "Current Value"}},
                                ["Symbol", "Current Value"])
    assert any("Nope" in e for e in errs)
    errs = imp.validate_mapping({"columns": {"value": "Current Value"}},
                                ["Symbol", "Current Value"])
    assert any("holding" in e for e in errs)


# ── LLM proposal (mocked transport) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_propose_mapping_validates_llm_output(monkeypatch):
    """A structurally-invalid proposal (hallucinated column) returns None —
    the operator maps manually; garbage never becomes a stored mapping."""
    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content":
                '{"columns": {"holding": "Imaginary Column", "value": "Current Value"}}'}}]}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw): return FakeResp()

    monkeypatch.setattr(imp.httpx, "AsyncClient", FakeClient)
    out = await imp.propose_mapping(["Symbol", "Current Value"], [])
    assert out is None


@pytest.mark.asyncio
async def test_propose_mapping_accepts_valid_output(monkeypatch):
    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content":
                'Sure! {"columns": {"holding": "Symbol", "value": "Current Value", '
                '"shares": null, "price": null}}'}}]}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw): return FakeResp()

    monkeypatch.setattr(imp.httpx, "AsyncClient", FakeClient)
    out = await imp.propose_mapping(["Symbol", "Current Value"], [])
    assert out == {"columns": {"holding": "Symbol", "value": "Current Value"}}


@pytest.mark.asyncio
async def test_propose_mapping_llm_down_returns_none(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw): raise OSError("connection refused")

    monkeypatch.setattr(imp.httpx, "AsyncClient", FakeClient)
    assert await imp.propose_mapping(["A", "B"], []) is None
