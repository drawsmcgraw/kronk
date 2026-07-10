"""Positions store — accounts, value snapshots, import bookkeeping.

The financial expert's ground truth (docs/plans/FINANCIAL_EXPERT_PLAN.md).
Design invariants:
- "Value, in USD" is the primary lens: `value` is the only required number
  per snapshot row; shares/price are optional provenance.
- Ingest is upsert-only: UNIQUE(account_id, as_of_date, holding) — importing
  the same export twice can never duplicate a dollar.
- liquid vs age_gated is first-class on accounts (the early-retirement
  bridge question), with a per-account unlock_age.
"""
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> Path:
    return Path(os.getenv("POSITIONS_DB_PATH", "/data/positions.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Defaults by account kind: (liquidity, unlock_age, tax_treatment).
# unlock_age is operator-overridable per account (TSP-at-55, Rule of 55 etc.
# land as overrides, not code — plan doc). HSA: penalty-free non-medical at 65.
KIND_DEFAULTS = {
    "401k":      ("age_gated", 59.5, "traditional"),
    "tsp":       ("age_gated", 59.5, "traditional"),
    "ira_trad":  ("age_gated", 59.5, "traditional"),
    "ira_roth":  ("age_gated", 59.5, "roth"),
    "hsa":       ("age_gated", 65.0, "traditional"),
    "taxable":   ("liquid",    None, "taxable"),
    "cash":      ("liquid",    None, "taxable"),
}
VALID_KINDS = set(KIND_DEFAULTS)
VALID_OWNERS = {"user", "spouse", "joint"}


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                institution   TEXT,
                kind          TEXT NOT NULL,
                owner         TEXT NOT NULL,
                liquidity     TEXT NOT NULL CHECK (liquidity IN ('liquid','age_gated')),
                unlock_age    REAL,
                tax_treatment TEXT,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS position_snapshots (
                account_id  TEXT NOT NULL REFERENCES accounts(id),
                as_of_date  TEXT NOT NULL,      -- YYYY-MM-DD
                holding     TEXT NOT NULL,
                shares      REAL,
                price       REAL,
                value       REAL NOT NULL,
                source_file TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE (account_id, as_of_date, holding)
            );

            CREATE TABLE IF NOT EXISTS import_mappings (
                fingerprint  TEXT PRIMARY KEY,
                mapping_json TEXT NOT NULL,
                -- NULL when the mapping carries an account column +
                -- account_map (multi-account files) instead.
                account_id   TEXT,
                confirmed    INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_files (
                sha256      TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                account_id  TEXT,
                as_of_date  TEXT,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS roth_basis (
                account_id TEXT PRIMARY KEY REFERENCES accounts(id),
                basis      REAL NOT NULL,
                as_of_date TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_date
                ON position_snapshots(as_of_date);
        """)


# ── accounts ──────────────────────────────────────────────────────────────────

# Deterministic classification from a document's own account descriptor
# ("DREW MALONE - JOINT WROS - TOD", "ROTH IRA", "Thrift Savings Plan") so
# imports ask the operator ZERO questions (2026-07-07 directive). Everything
# inferred is correctable later via POST /api/accounts; a wrong guess never
# blocks an import and never changes a dollar value — only its
# liquid/age-gated classification, which the operator can fix in one call.
_KIND_HINTS = [
    ("roth", "ira_roth"),
    ("thrift", "tsp"), ("tsp", "tsp"),
    ("401(k)", "401k"), ("401k", "401k"), ("403(b)", "401k"),
    ("hsa", "hsa"), ("health savings", "hsa"),
    ("ira", "ira_trad"), ("sep", "ira_trad"), ("rollover", "ira_trad"),
]


def infer_kind(descriptor: str) -> str:
    d = (descriptor or "").lower()
    for hint, kind in _KIND_HINTS:
        if hint in d:
            return kind
    return "taxable"


def infer_owner(descriptor: str) -> str:
    d = (descriptor or "").lower()
    return "joint" if ("joint" in d or "wros" in d) else "user"

def upsert_account(acct: dict) -> dict:
    kind = acct["kind"]
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
    if acct["owner"] not in VALID_OWNERS:
        raise ValueError(f"owner must be one of {sorted(VALID_OWNERS)}")
    d_liq, d_unlock, d_tax = KIND_DEFAULTS[kind]
    acct_id = acct.get("id") or acct["name"].lower().replace(" ", "_")
    # `or`/explicit-None handling: API models send unlock_age=None when the
    # operator didn't set it — that means "use the kind default", not "no
    # unlock age".
    unlock = acct.get("unlock_age")
    row = {
        "id": acct_id,
        "name": acct["name"],
        "institution": acct.get("institution"),
        "kind": kind,
        "owner": acct["owner"],
        "liquidity": acct.get("liquidity") or d_liq,
        "unlock_age": d_unlock if unlock is None else unlock,
        "tax_treatment": acct.get("tax_treatment") or d_tax,
        "created_at": _now(),
    }
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO accounts (id, name, institution, kind, owner,
                                  liquidity, unlock_age, tax_treatment, created_at)
            VALUES (:id, :name, :institution, :kind, :owner,
                    :liquidity, :unlock_age, :tax_treatment, :created_at)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, institution=excluded.institution,
                kind=excluded.kind, owner=excluded.owner,
                liquidity=excluded.liquidity, unlock_age=excluded.unlock_age,
                tax_treatment=excluded.tax_treatment
        """, row)
    return row


def list_accounts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY owner, name").fetchall()
    return [dict(r) for r in rows]


def get_account(acct_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (acct_id,)).fetchone()
    return dict(row) if row else None


def set_roth_basis(account_id: str, basis: float, as_of_date: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO roth_basis (account_id, basis, as_of_date)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                basis=excluded.basis, as_of_date=excluded.as_of_date
        """, (account_id, basis, as_of_date))


# ── snapshots ─────────────────────────────────────────────────────────────────

def upsert_snapshot_rows(account_id: str, as_of_date: str,
                         rows: list[dict], source_file: str) -> dict:
    """REPLACE the (account, as_of_date) snapshot wholesale, atomically.

    Replace — not per-holding merge — for two reasons: a corrected
    re-export that *removes* a holding must not leave the stale row behind,
    and a statement-value import ("Account value (statement)") must never
    coexist with per-position rows for the same account+date (that would
    double-count). Whichever import ran last IS the snapshot; the raw file
    archive preserves anything replaced."""
    now = _now()
    with get_conn() as conn:
        conn.execute("DELETE FROM position_snapshots "
                     "WHERE account_id = ? AND as_of_date = ?",
                     (account_id, as_of_date))
        conn.executemany("""
            INSERT INTO position_snapshots
                (account_id, as_of_date, holding, shares, price, value,
                 source_file, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [(account_id, as_of_date, r["holding"], r.get("shares"),
               r.get("price"), r["value"], source_file, now) for r in rows])
    return {"rows": len(rows)}


def file_already_imported(sha256: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM import_files WHERE sha256 = ?",
                           (sha256,)).fetchone()
    return dict(row) if row else None


def record_import_file(sha256: str, filename: str, account_id: str,
                       as_of_date: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO import_files
                (sha256, filename, account_id, as_of_date, imported_at)
            VALUES (?, ?, ?, ?, ?)
        """, (sha256, filename, account_id, as_of_date, _now()))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── mappings ──────────────────────────────────────────────────────────────────

def get_mapping(fingerprint: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM import_mappings WHERE fingerprint = ?",
                           (fingerprint,)).fetchone()
    return dict(row) if row else None


def save_mapping(fingerprint: str, mapping_json: str, account_id: str | None,
                 confirmed: bool) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO import_mappings
                (fingerprint, mapping_json, account_id, confirmed, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                mapping_json=excluded.mapping_json,
                account_id=excluded.account_id, confirmed=excluded.confirmed
        """, (fingerprint, mapping_json, account_id, int(confirmed), _now()))


# ── queries (UI + agent) ──────────────────────────────────────────────────────

def latest_snapshot_date(account_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(as_of_date) AS d FROM position_snapshots WHERE account_id = ?",
            (account_id,)).fetchone()
    return row["d"]


def positions_summary() -> dict:
    """Everything the /finances UI and the agent's value questions need:
    per-account latest values (+ previous for deltas), liquidity totals,
    and the full value history for the trajectory chart."""
    accounts = list_accounts()
    out_accounts = []
    totals = {"total": 0.0, "liquid": 0.0, "age_gated": 0.0}
    as_of_dates = []

    with get_conn() as conn:
        for acct in accounts:
            dates = [r["as_of_date"] for r in conn.execute(
                "SELECT DISTINCT as_of_date FROM position_snapshots "
                "WHERE account_id = ? ORDER BY as_of_date DESC LIMIT 2",
                (acct["id"],)).fetchall()]
            if not dates:
                out_accounts.append({**acct, "value": None, "as_of": None,
                                     "prev_value": None, "holdings": []})
                continue

            def _val(d):
                return conn.execute(
                    "SELECT COALESCE(SUM(value), 0) AS v FROM position_snapshots "
                    "WHERE account_id = ? AND as_of_date = ?",
                    (acct["id"], d)).fetchone()["v"]

            value = _val(dates[0])
            prev = _val(dates[1]) if len(dates) > 1 else None
            holdings = [dict(r) for r in conn.execute(
                "SELECT holding, shares, price, value FROM position_snapshots "
                "WHERE account_id = ? AND as_of_date = ? ORDER BY value DESC",
                (acct["id"], dates[0])).fetchall()]
            out_accounts.append({**acct, "value": round(value, 2),
                                 "as_of": dates[0],
                                 "prev_value": round(prev, 2) if prev is not None else None,
                                 "holdings": holdings})
            totals["total"] += value
            totals[acct["liquidity"]] += value
            as_of_dates.append(dates[0])

        # Trajectory: per snapshot date, sum by liquidity. Dates where only
        # some accounts reported use whatever exists on that date (honest —
        # the chart shows reported values, not interpolations).
        history_rows = conn.execute("""
            SELECT s.as_of_date AS date, a.liquidity,
                   SUM(s.value) AS value
            FROM position_snapshots s JOIN accounts a ON a.id = s.account_id
            GROUP BY s.as_of_date, a.liquidity ORDER BY s.as_of_date
        """).fetchall()

    history: dict[str, dict] = {}
    for r in history_rows:
        h = history.setdefault(r["date"], {"date": r["date"], "liquid": 0.0,
                                           "age_gated": 0.0})
        h[r["liquidity"]] = round(r["value"], 2)
    for h in history.values():
        h["total"] = round(h["liquid"] + h["age_gated"], 2)

    with get_conn() as conn:
        basis_rows = conn.execute("SELECT * FROM roth_basis").fetchall()

    return {
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "as_of_latest": max(as_of_dates) if as_of_dates else None,
        "accounts": out_accounts,
        "history": sorted(history.values(), key=lambda h: h["date"]),
        "roth_basis": [dict(r) for r in basis_rows],
    }
