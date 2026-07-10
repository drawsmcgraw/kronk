"""Tests for the positions store + /api/positions routes end to end.

Pins the two operator requirements from the plan: upsert-only ingest (the
same data can never land twice) and the value-in-USD lens (summary leads
with totals + liquid/gated split; holdings are detail)."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITIONS_DB_PATH", str(tmp_path / "positions.db"))
    monkeypatch.setenv("FINANCE_DB_PATH", str(tmp_path / "finances.db"))
    import finance_service.main as main_mod
    import finance_service.positions_db as pdb
    monkeypatch.setattr(main_mod, "RAW_DIR", tmp_path / "raw")
    pdb.init_db()
    with TestClient(main_mod.app) as c:
        yield c


@pytest.fixture
def accounts(fin_client):
    fin_client.post("/api/accounts", json={
        "id": "brokerage", "name": "Taxable Brokerage",
        "institution": "Fidelity", "kind": "taxable", "owner": "user"})
    fin_client.post("/api/accounts", json={
        "id": "spouse_tsp", "name": "Spouse TSP",
        "institution": "TSP", "kind": "tsp", "owner": "spouse"})
    return fin_client


FIDELITY_CSV = (
    "Symbol,Quantity,Last Price,Current Value\n"
    'VTI,100,"$275.50","$27,550.00"\n'
    'VXUS,200,"$62.25","$12,450.00"\n'
)
FID_MAPPING = {"columns": {"holding": "Symbol", "value": "Current Value",
                           "shares": "Quantity", "price": "Last Price"}}


def _upload(client, content: str, name: str, as_of: str | None = None):
    params = {"as_of": as_of} if as_of else {}
    return client.post("/api/positions/import", params=params,
                       files={"file": (name, content.encode(), "text/csv")})


def _confirm(client, fingerprint, mapping, account_id):
    return client.post("/api/positions/mappings/confirm", json={
        "fingerprint": fingerprint, "mapping": mapping, "account_id": account_id})


# ── account defaults ──────────────────────────────────────────────────────────

def test_account_kind_defaults(accounts):
    accts = {a["id"]: a for a in accounts.get("/api/accounts").json()["accounts"]}
    assert accts["brokerage"]["liquidity"] == "liquid"
    assert accts["brokerage"]["unlock_age"] is None
    assert accts["spouse_tsp"]["liquidity"] == "age_gated"
    assert accts["spouse_tsp"]["unlock_age"] == 59.5


def test_account_invalid_kind_is_422(fin_client):
    resp = fin_client.post("/api/accounts", json={
        "name": "X", "kind": "crypto_wallet", "owner": "user"})
    assert resp.status_code == 422


# ── the mapping flow ──────────────────────────────────────────────────────────

def test_new_format_needs_mapping_then_imports(accounts, monkeypatch):
    """First upload of an unknown format → needs_mapping (with the LLM's
    proposal); confirm; re-upload → imported. Month 2 (same headers) never
    consults the LLM again."""
    import finance_service.main as main_mod
    llm_calls = []

    async def fake_propose(headers, sample):
        llm_calls.append(headers)
        return FID_MAPPING

    monkeypatch.setattr(main_mod.imp, "propose_mapping", fake_propose)

    r1 = _upload(accounts, FIDELITY_CSV, "fid-2026-06-30.csv")
    assert r1.status_code == 200
    body = r1.json()
    assert body["status"] == "needs_mapping"
    assert body["proposal"] == FID_MAPPING
    assert len(llm_calls) == 1

    assert _confirm(accounts, body["fingerprint"], FID_MAPPING,
                    "brokerage").status_code == 200

    r2 = _upload(accounts, FIDELITY_CSV, "fid-2026-06-30.csv")
    assert r2.json()["status"] == "imported"
    assert r2.json()["total_value"] == 40000.0

    # month 2: same format, new data/date — zero LLM involvement
    # (price moves with value: the shares x price invariant is always on)
    month2 = FIDELITY_CSV.replace("275.50", "280.00").replace("27,550.00", "28,000.00")
    r3 = _upload(accounts, month2, "fid-2026-07-31.csv")
    assert r3.json()["status"] == "imported"
    assert len(llm_calls) == 1


def test_confirm_requires_existing_account_and_required_fields(accounts):
    assert _confirm(accounts, "abc", FID_MAPPING, "nope").status_code == 422
    assert _confirm(accounts, "abc", {"columns": {"value": "Current Value"}},
                    "brokerage").status_code == 422


# ── upsert-only, never duplicate (operator requirement) ───────────────────────

def _import_fidelity(accounts, content=FIDELITY_CSV, name="fid-2026-06-30.csv"):
    fp = None
    r = _upload(accounts, content, name)
    if r.json().get("status") == "needs_mapping":
        fp = r.json()["fingerprint"]
        _confirm(accounts, fp, FID_MAPPING, "brokerage")
        r = _upload(accounts, content, name)
    return r


def test_same_file_reimport_is_a_noop(accounts):
    r1 = _import_fidelity(accounts)
    assert r1.json()["status"] == "imported"
    r2 = _upload(accounts, FIDELITY_CSV, "fid-2026-06-30.csv")
    assert r2.json()["status"] == "already_imported"
    assert "nothing changed" in r2.json()["detail"]


def test_corrected_reexport_upserts_not_duplicates(accounts):
    """Same account + date + holdings, different values (a corrected
    export): rows must be REPLACED. Total reflects only the new file."""
    _import_fidelity(accounts)
    corrected = FIDELITY_CSV.replace('"$27,550.00"', '"$27,550.00"') \
                            .replace("VXUS,200", "VXUS,210") \
                            .replace('"$12,450.00"', '"$13,072.50"')
    r = _import_fidelity(accounts, corrected, "fid-corrected-2026-06-30.csv")
    assert r.json()["status"] == "imported"
    summary = accounts.get("/api/positions").json()
    acct = next(a for a in summary["accounts"] if a["id"] == "brokerage")
    assert len(acct["holdings"]) == 2          # not 4 — upserted
    assert summary["totals"]["total"] == 27550.0 + 13072.50


def test_rejected_import_lands_nothing(accounts):
    """Invariant failure mid-file → zero rows ingested (no partial state)."""
    _import_fidelity(accounts)  # establish the mapping
    bad = FIDELITY_CSV.replace('"$62.25"', "garbage")
    r = _upload(accounts, bad, "fid-2026-07-31.csv")
    assert r.status_code == 422
    summary = accounts.get("/api/positions").json()
    dates = {h["date"] for h in summary["history"]}
    assert dates == {"2026-06-30"}  # July never landed


# ── the value lens ────────────────────────────────────────────────────────────

TSP_CSV = (
    "Fund,Balance\n"
    'C Fund,"$310,000.00"\n'
    'G Fund,"$45,000.00"\n'
)
TSP_MAPPING = {"columns": {"holding": "Fund", "value": "Balance"}}


def test_summary_leads_with_liquidity_split(accounts, monkeypatch):
    import finance_service.main as main_mod

    async def no_llm(headers, sample):
        return None
    monkeypatch.setattr(main_mod.imp, "propose_mapping", no_llm)

    _import_fidelity(accounts)
    r = _upload(accounts, TSP_CSV, "tsp-2026-06-30.csv")
    _confirm(accounts, r.json()["fingerprint"], TSP_MAPPING, "spouse_tsp")
    _upload(accounts, TSP_CSV, "tsp-2026-06-30.csv")

    s = accounts.get("/api/positions").json()
    assert s["totals"] == {"total": 395000.0, "liquid": 40000.0,
                           "age_gated": 355000.0}
    assert s["as_of_latest"] == "2026-06-30"
    tsp = next(a for a in s["accounts"] if a["id"] == "spouse_tsp")
    # value-only holdings: no shares/price, and that's fine
    assert tsp["holdings"][0]["shares"] is None
    assert tsp["value"] == 355000.0


def test_history_tracks_deltas_across_snapshots(accounts):
    _import_fidelity(accounts)
    month2 = FIDELITY_CSV.replace("275.50", "290.00").replace("27,550.00", "29,000.00")
    _import_fidelity(accounts, month2, "fid-2026-07-31.csv")

    s = accounts.get("/api/positions").json()
    assert [h["date"] for h in s["history"]] == ["2026-06-30", "2026-07-31"]
    assert s["history"][1]["liquid"] == 29000.0 + 12450.0
    acct = next(a for a in s["accounts"] if a["id"] == "brokerage")
    assert acct["as_of"] == "2026-07-31"
    assert acct["prev_value"] == 40000.0


# ── multi-account files (one brokerage export, several accounts) ─────────────

MULTI_CSV = (
    "Account Name,Symbol,Quantity,Last Price,Current Value\n"
    'Z24 Joint,VTI,100,"$275.50","$27,550.00"\n'
    'Z32 Roth,VXUS,200,"$62.25","$12,450.00"\n'
    'Z99 Old,BND,10,"$70.00","$700.00"\n'
)
MULTI_MAPPING = {
    "columns": {"holding": "Symbol", "value": "Current Value",
                "shares": "Quantity", "price": "Last Price",
                "account": "Account Name"},
    "account_map": {"Z24 Joint": "brokerage", "Z32 Roth": "roth_ira",
                    "Z99 Old": "__skip__"},
}


def test_multi_account_file_splits_by_account_map(accounts):
    accounts.post("/api/accounts", json={
        "id": "roth_ira", "name": "Roth IRA", "kind": "ira_roth", "owner": "user"})
    r = _upload(accounts, MULTI_CSV, "multi-2026-06-30.csv")
    fp = r.json()["fingerprint"]
    assert _confirm(accounts, fp, MULTI_MAPPING, None).status_code == 200

    r = _upload(accounts, MULTI_CSV, "multi-2026-06-30.csv")
    body = r.json()
    assert body["status"] == "imported"
    per = {a["account_id"]: a for a in body["accounts"]}
    assert per["brokerage"]["total_value"] == 27550.0
    assert per["roth_ira"]["total_value"] == 12450.0
    assert "Z99" not in str(per)          # skipped account never lands
    assert body["total_value"] == 40000.0  # skip excluded from total

    s = accounts.get("/api/positions").json()
    assert s["totals"]["liquid"] == 27550.0      # brokerage
    assert s["totals"]["age_gated"] == 12450.0   # roth


def test_unknown_account_values_autocreate_zero_questions(accounts):
    """2026-07-07 directive: unknown account-column values never block or
    ask — they auto-create accounts classified from the value text itself
    ('Z32 Roth' → ira_roth), remembered in the mapping for next month."""
    partial = {"columns": MULTI_MAPPING["columns"],
               "account_map": {"Z24 Joint": "brokerage"}}
    r = _upload(accounts, MULTI_CSV, "multi-2026-06-30.csv")
    _confirm(accounts, r.json()["fingerprint"], partial, None)
    r = _upload(accounts, MULTI_CSV, "multi-2026-06-30.csv")
    body = r.json()
    assert body["status"] == "imported"
    created = {c["account_id"]: c for c in body["created_accounts"]}
    assert created["z32_roth"]["kind"] == "ira_roth"   # classified from text
    assert created["z99_old"]["kind"] == "taxable"     # default
    per = {a["account_id"]: a["total_value"] for a in body["accounts"]}
    assert per["brokerage"] == 27550.0                 # explicit map honored
    assert per["z32_roth"] == 12450.0


def test_confirm_without_account_id_requires_account_column(accounts):
    r = _confirm(accounts, "xyz", FID_MAPPING, None)
    assert r.status_code == 422
    assert "account_id is required" in r.json()["detail"]


# ── roth basis ────────────────────────────────────────────────────────────────

def test_roth_basis_only_on_roth_accounts(accounts):
    accounts.post("/api/accounts", json={
        "id": "roth_ira", "name": "Roth IRA", "kind": "ira_roth", "owner": "user"})
    ok = accounts.post("/api/accounts/roth_ira/roth_basis",
                       json={"basis": 85000, "as_of_date": "2026-06-30"})
    assert ok.status_code == 200
    bad = accounts.post("/api/accounts/spouse_tsp/roth_basis",
                        json={"basis": 1, "as_of_date": "2026-06-30"})
    assert bad.status_code == 422
    assert "not a Roth" in bad.json()["detail"]
