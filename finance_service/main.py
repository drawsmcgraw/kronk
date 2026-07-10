import io
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from pypdf import PdfReader

# Dual-compat: flat layout in the container, package import in tests.
try:
    from .db import delete_document, init_db, insert_document, list_documents, search_chunks
    from . import extractor as ex
    from . import importer as imp
    from . import positions_db as pdb
    from . import statement_pdf as sp
except ImportError:
    from db import delete_document, init_db, insert_document, list_documents, search_chunks
    import extractor as ex
    import importer as imp
    import positions_db as pdb
    import statement_pdf as sp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RAW_DIR = Path(os.getenv("POSITIONS_RAW_DIR", "/data/raw"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    pdb.init_db()
    logger.info("Finance service started")
    yield


app = FastAPI(title="Kronk Finance Service", lifespan=lifespan)

# Max characters per chunk (~500 tokens). Smaller chunks = more precise retrieval.
CHUNK_SIZE = 2000
# Overlap between adjacent chunks to avoid splitting context across boundaries.
CHUNK_OVERLAP = 200


def _chunk_text(text: str, page: int | None = None) -> list[dict]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append({"page": page, "text": chunk_text})
        start = end - CHUNK_OVERLAP
    return chunks


def _extract_chunks(data: bytes, filename: str) -> list[dict]:
    """Extract text chunks from PDF or plain text."""
    if filename.lower().endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            chunks = []
            for page_num, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    chunks.extend(_chunk_text(page_text, page=page_num))
            return chunks
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")
    else:
        # Plain text or markdown
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return _chunk_text(text)


@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...)):
    """Upload a PDF or text file and index its contents."""
    name = file.filename or "upload"
    data = await file.read()

    if not name.lower().endswith((".pdf", ".txt", ".md", ".csv")):
        raise HTTPException(
            status_code=422,
            detail="Supported formats: .pdf, .txt, .md, .csv",
        )

    chunks = _extract_chunks(data, name)
    if not chunks:
        raise HTTPException(status_code=422, detail="No text could be extracted")

    doc_id = insert_document(name, len(data), chunks)
    logger.info("Ingested %s: %d chunks (doc_id=%d)", name, len(chunks), doc_id)
    return {"id": doc_id, "name": name, "chunks": len(chunks)}


@app.get("/api/query")
def query(q: str = Query(..., description="Natural language or keyword query")):
    """Search across all ingested documents. Returns matching text excerpts."""
    if not q.strip():
        raise HTTPException(status_code=422, detail="Query cannot be empty")

    docs = list_documents()
    if not docs:
        return {"status": "no_documents", "results": []}

    results = search_chunks(q, limit=8)
    return {
        "status": "ok",
        "query": q,
        "results": [
            {
                "doc_name": r["doc_name"],
                "page": r["page"],
                "excerpt": r["text"][:600],
            }
            for r in results
        ],
    }


@app.get("/api/documents")
def documents():
    return {"documents": list_documents()}


@app.delete("/api/documents/{doc_id}")
def remove_document(doc_id: int):
    if not delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted"}


# ── Positions (docs/plans/FINANCIAL_EXPERT_PLAN.md, phase 1) ─────────────────
# "Value, in USD" is the primary lens. Ingest is upsert-only; the whole
# import is rejected on any invariant failure (no partial ingests). The LLM
# proposes column mappings for NEW formats only — it never touches numbers.


class AccountIn(BaseModel):
    id: str | None = None
    name: str
    institution: str | None = None
    kind: str                      # 401k|tsp|ira_trad|ira_roth|hsa|taxable|cash
    owner: str                     # user|spouse|joint
    liquidity: str | None = None   # defaulted from kind
    unlock_age: float | None = None
    tax_treatment: str | None = None


class MappingConfirm(BaseModel):
    fingerprint: str
    mapping: dict                  # {"columns": {...}, "account_map": {...}?}
    account_id: str | None = None  # required unless mapping has an account
                                   # column + account_map


class RothBasisIn(BaseModel):
    basis: float
    as_of_date: str


@app.get("/api/accounts")
def accounts_list():
    return {"accounts": pdb.list_accounts()}


@app.post("/api/accounts")
def accounts_upsert(acct: AccountIn):
    try:
        row = pdb.upsert_account(acct.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return row


@app.post("/api/accounts/{account_id}/roth_basis")
def roth_basis_set(account_id: str, body: RothBasisIn):
    acct = pdb.get_account(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail=f"no account {account_id!r}")
    if acct["tax_treatment"] != "roth":
        raise HTTPException(status_code=422,
                            detail=f"{account_id} is not a Roth account")
    pdb.set_roth_basis(account_id, body.basis, body.as_of_date)
    return {"status": "ok", "account_id": account_id, "basis": body.basis}


@app.get("/api/positions")
def positions():
    return pdb.positions_summary()


@app.post("/api/positions/import")
async def positions_import(file: UploadFile = File(...),
                           as_of: str | None = Query(default=None)):
    name = file.filename or "upload"
    data = await file.read()

    sha = pdb.sha256_bytes(data)
    prior = pdb.file_already_imported(sha)
    if prior:
        return {"status": "already_imported",
                "detail": f"this exact file was imported {prior['imported_at'][:10]} "
                          f"(account {prior['account_id']}, as of {prior['as_of_date']}) "
                          "— nothing changed",
                **{k: prior[k] for k in ("account_id", "as_of_date")}}

    # PDFs get the VALUE path: per-account ending values, accepted only when
    # they reconcile against the document's own stated total. Two tiers:
    # the deterministic anchor parser (Fidelity layout — fast, free), then
    # LLM extraction behind the same verification gate (any reasonably
    # formatted document — TSP statements, layout changes). Both are
    # rejected loudly when the numbers don't reconcile.
    if name.lower().endswith(".pdf"):
        try:
            text = sp.extract_pdf_text(data)
        except sp.StatementParseError as e:
            raise HTTPException(status_code=422, detail=str(e))
        try:
            stmt_as_of, stmt_accounts, stated_total = sp.parse_statement(text)
            descs = sp.account_descriptors(text)
            source = "statement_pdf"
        except sp.StatementParseError as anchor_err:
            logger.info("anchor parse failed (%s) — trying LLM extraction", anchor_err)
            try:
                stmt_as_of, stmt_accounts, stated_total, descs = \
                    await ex.extract_and_verify(text, name, as_of)
                source = "statement_llm"
            except ex.ExtractionError as llm_err:
                raise HTTPException(
                    status_code=422,
                    detail=f"could not extract verified values from this PDF. "
                           f"Anchor parser: {anchor_err}. LLM extraction: "
                           f"{llm_err}. A CSV/XLSX export will work.")
        return await _import_statement(data, name, as_of or stmt_as_of,
                                       stmt_accounts, sha, source, descs)

    try:
        parsed = imp.sniff_and_parse(data, name)
    except imp.ImportError422 as e:
        raise HTTPException(status_code=422, detail=str(e))

    fp = imp.fingerprint(parsed.headers)
    stored = pdb.get_mapping(fp)
    if not stored or not stored["confirmed"]:
        proposal = await imp.propose_mapping(parsed.headers, parsed.sample)
        logger.info("import %s: new format %s, proposal=%s", name, fp, proposal)
        return {"status": "needs_mapping", "fingerprint": fp,
                "format": parsed.format, "headers": parsed.headers,
                "sample_rows": parsed.sample,
                "proposal": proposal,   # null when the LLM couldn't help
                "accounts": pdb.list_accounts(),
                "detail": "unrecognized format — confirm the column mapping, "
                          "then re-upload the same file"}

    mapping = json.loads(stored["mapping_json"])
    try:
        groups, as_of_date = imp.apply_mapping(parsed, mapping, as_of, name)
    except imp.ImportError422 as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Resolve each group to a kronk account. Files with an account column
    # (one brokerage export covering several accounts) use
    # mapping["account_map"]; single-account files use the mapping's
    # account_id. Unmapped values are a structured 422 so the UI can offer
    # a per-value account picker instead of a dead end.
    account_map = mapping.get("account_map") or {}
    resolved: dict[str, list[dict]] = {}
    created: list[dict] = []
    if list(groups) == [None]:
        acct = stored["account_id"]
        if not acct:
            raise HTTPException(status_code=422,
                                detail="mapping has no account_id and the file "
                                       "has no account column")
        resolved[acct] = groups[None]
    else:
        if None in groups:
            raise HTTPException(status_code=422,
                                detail="some rows have an empty account cell — "
                                       "fix the export or the account column choice")
        unknown = sorted(v for v in groups if v not in account_map)
        # Zero questions: classify from the account-column text itself
        # ("Z32 Roth" → ira_roth). Correctable later, never blocking.
        created = _autocreate_accounts(unknown, {}, account_map)
        if created:
            mapping["account_map"] = account_map
            pdb.save_mapping(fp, json.dumps(mapping), stored["account_id"],
                             confirmed=True)
        for val, rows in groups.items():
            target = account_map[val]
            if target == imp.SKIP_ACCOUNT:
                continue
            if not pdb.get_account(target):
                raise HTTPException(status_code=422,
                                    detail=f"account_map points {val!r} at "
                                           f"unknown account {target!r}")
            resolved.setdefault(target, []).extend(rows)
    if not resolved:
        raise HTTPException(status_code=422,
                            detail="every account in the file is mapped to "
                                   "__skip__ — nothing to import")

    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"{as_of_date}-{Path(name).name}").write_bytes(data)
    except OSError as e:
        logger.warning("could not save raw copy of %s: %s", name, e)

    per_account = []
    for acct, rows in resolved.items():
        pdb.upsert_snapshot_rows(acct, as_of_date, rows, name)
        per_account.append({"account_id": acct, "rows": len(rows),
                            "total_value": round(sum(r["value"] for r in rows), 2)})
    pdb.record_import_file(sha, name, ",".join(resolved), as_of_date)
    total = round(sum(a["total_value"] for a in per_account), 2)
    total_rows = sum(a["rows"] for a in per_account)
    logger.info("imported %s: %d rows, %s, accounts=%s, total=%.2f",
                name, total_rows, as_of_date, list(resolved), total)
    out = {"status": "imported", "as_of_date": as_of_date, "rows": total_rows,
           "total_value": total, "accounts": per_account,
           "created_accounts": created}
    if len(per_account) == 1:
        out["account_id"] = per_account[0]["account_id"]
    return out


STATEMENT_FINGERPRINT = "fidelity-statement"
STATEMENT_HOLDING = "Account value (statement)"


def _autocreate_accounts(values: list[str], descs: dict,
                         account_map: dict) -> list[dict]:
    """ZERO-questions rule (operator directive 2026-07-07): unknown account
    values are auto-created with kind/owner inferred from the document's
    own descriptor text, never asked about. Everything inferred is
    correctable later via POST /api/accounts (same id) without touching
    the imported values."""
    created = []
    for v in values:
        desc = descs.get(v, v)
        acct_id = re.sub(r"[^a-z0-9]+", "_", v.lower()).strip("_") or "account"
        row = pdb.upsert_account({
            "id": acct_id,
            "name": (desc[:60] if desc else v),
            "kind": pdb.infer_kind(desc),
            "owner": pdb.infer_owner(desc),
        })
        account_map[v] = acct_id
        created.append({"account_id": acct_id, "kind": row["kind"],
                        "owner": row["owner"], "from": v})
        logger.info("auto-created account %s (kind=%s owner=%s) from %r",
                    acct_id, row["kind"], row["owner"], desc[:60])
    return created


async def _import_statement(data: bytes, name: str, as_of_date: str,
                            stmt_accounts: dict, sha: str,
                            source: str = "statement_pdf",
                            descs: dict | None = None):
    """Statement path: one value row per account. Unknown accounts are
    auto-created from the statement's own descriptors — zero questions."""
    stored = pdb.get_mapping(STATEMENT_FINGERPRINT)
    account_map = {}
    if stored and stored["confirmed"]:
        account_map = json.loads(stored["mapping_json"]).get("account_map") or {}
    unknown = sorted(v for v in stmt_accounts if v not in account_map)
    created = _autocreate_accounts(unknown, descs or {}, account_map)
    if created:
        pdb.save_mapping(STATEMENT_FINGERPRINT,
                         json.dumps({"statement": True, "account_map": account_map}),
                         None, confirmed=True)

    resolved: dict[str, list[dict]] = {}
    for acct_num, value in stmt_accounts.items():
        target = account_map[acct_num]
        if target == imp.SKIP_ACCOUNT:
            continue
        if not pdb.get_account(target):
            raise HTTPException(status_code=422,
                                detail=f"account_map points {acct_num!r} at "
                                       f"unknown account {target!r}")
        resolved.setdefault(target, []).append(
            {"holding": STATEMENT_HOLDING, "value": value})
    if not resolved:
        raise HTTPException(
            status_code=422,
            detail="every statement account is marked skip in the stored "
                   "mapping (probably left over from an earlier aborted "
                   "import) — nothing to import. Reset it with: DELETE the "
                   "'fidelity-statement' row via "
                   "POST /api/positions/mappings/confirm with a fresh "
                   "account_map, or ask Kronk to clear the statement mapping.")

    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"{as_of_date}-{Path(name).name}").write_bytes(data)
    except OSError as e:
        logger.warning("could not save raw copy of %s: %s", name, e)

    per_account = []
    for acct, rows in resolved.items():
        pdb.upsert_snapshot_rows(acct, as_of_date, rows, name)
        per_account.append({"account_id": acct, "rows": len(rows),
                            "total_value": round(sum(r["value"] for r in rows), 2)})
    pdb.record_import_file(sha, name, ",".join(resolved), as_of_date)
    total = round(sum(a["total_value"] for a in per_account), 2)
    logger.info("statement import %s (%s): %s, accounts=%s, total=%.2f",
                name, source, as_of_date, list(resolved), total)
    return {"status": "imported", "source": source,
            "as_of_date": as_of_date, "rows": len(per_account),
            "total_value": total, "accounts": per_account,
            "created_accounts": created,
            "note": "account-level values from the statement summary "
                    "(reconciled against the stated portfolio total); "
                    "per-position detail comes from CSV/XLSX exports"}


@app.post("/api/positions/mappings/confirm")
def mapping_confirm(body: MappingConfirm):
    is_statement = bool(body.mapping.get("statement"))
    cols = body.mapping.get("columns") or {}
    if not is_statement:
        missing = [f for f in imp.REQUIRED_FIELDS if not cols.get(f)]
        if missing:
            raise HTTPException(status_code=422,
                                detail=f"mapping is missing required field(s): {missing}")
    has_account_col = bool(cols.get("account")) or is_statement
    if not has_account_col:
        if not body.account_id:
            raise HTTPException(status_code=422,
                                detail="account_id is required when the file has "
                                       "no account column")
        if not pdb.get_account(body.account_id):
            raise HTTPException(status_code=422,
                                detail=f"no account {body.account_id!r} — create it first")
    for val, target in (body.mapping.get("account_map") or {}).items():
        if target != imp.SKIP_ACCOUNT and not pdb.get_account(target):
            raise HTTPException(status_code=422,
                                detail=f"account_map points {val!r} at unknown "
                                       f"account {target!r}")
    pdb.save_mapping(body.fingerprint, json.dumps(body.mapping),
                     body.account_id, confirmed=True)
    return {"status": "confirmed", "fingerprint": body.fingerprint,
            "detail": "re-upload the file to import it"}


@app.get("/health")
def health():
    docs = list_documents()
    return {"status": "ok", "document_count": len(docs)}
