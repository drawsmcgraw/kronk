"""
Tests for finance_service: document ingestion and search.

Success criteria:
- PDF/text upload → chunks stored in SQLite
- Query returns matching excerpts when documents exist
- Query returns {"status": "no_documents"} when nothing uploaded
- DELETE removes document and its chunks
- /health returns 200
"""
import io
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_finance_db(tmp_path, monkeypatch):
    """Wire finance_service to use a temporary DB file."""
    db_path = tmp_path / "finances.db"
    monkeypatch.setenv("FINANCE_DB_PATH", str(db_path))
    import finance_service.db as db_mod
    db_mod.init_db()
    return db_path


@pytest.fixture
def finance_client(tmp_finance_db):
    import finance_service.main as main_mod
    with TestClient(main_mod.app) as c:
        yield c


def _make_text_file(content: str, filename: str = "test.txt") -> tuple:
    return (filename, content.encode("utf-8"), "text/plain")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_minimal_pdf() -> bytes:
    """
    Build a minimal valid PDF containing known text.
    Uses pypdf's PdfWriter to avoid external dependencies.
    """
    from pypdf import PdfWriter
    from pypdf.generic import (
        NameObject, NumberObject, ArrayObject, ByteStringObject,
        DictionaryObject, DecodedStreamObject,
    )

    # Simplest possible PDF: one page with a text stream
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ── Tests: /api/ingest ────────────────────────────────────────────────────────

def test_ingest_text_file(finance_client):
    """Uploading a .txt file should create a document and chunks."""
    content = "My salary in 2025 was $95,000. My mortgage payment is $1,800/month."
    resp = finance_client.post(
        "/api/ingest",
        files={"file": _make_text_file(content, "income_2025.txt")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "income_2025.txt"
    assert data["chunks"] >= 1


def test_ingest_creates_document_record(finance_client):
    """After ingest, document should appear in /api/documents."""
    content = "Bank balance: $12,500"
    finance_client.post(
        "/api/ingest",
        files={"file": _make_text_file(content, "bank.txt")},
    )
    resp = finance_client.get("/api/documents")
    assert resp.status_code == 200
    docs = resp.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["name"] == "bank.txt"
    assert docs[0]["chunk_count"] >= 1


def test_ingest_rejects_unsupported_type(finance_client):
    """Uploading a .jpg should return 422."""
    resp = finance_client.post(
        "/api/ingest",
        files={"file": ("photo.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert resp.status_code == 422


# ── Tests: /api/query ────────────────────────────────────────────────────────

def test_query_returns_no_documents_when_empty(finance_client):
    """Empty document store → {"status": "no_documents"}."""
    resp = finance_client.get("/api/query", params={"q": "income"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_documents"


def test_query_returns_matching_excerpt(finance_client):
    """After ingest, query should return chunks containing the search terms."""
    content = "Total annual income: $120,000. Federal tax withheld: $28,000."
    finance_client.post(
        "/api/ingest",
        files={"file": _make_text_file(content, "tax_2025.txt")},
    )
    resp = finance_client.get("/api/query", params={"q": "annual income"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["results"]) >= 1
    assert "income" in data["results"][0]["excerpt"].lower()


def test_query_no_match_returns_empty_results(finance_client):
    """A query that matches nothing in the documents returns empty results list."""
    content = "Checking account balance: $5,000."
    finance_client.post(
        "/api/ingest",
        files={"file": _make_text_file(content, "checking.txt")},
    )
    resp = finance_client.get("/api/query", params={"q": "xyzzy_impossible_match_12345"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["results"] == []


def test_query_requires_q_param(finance_client):
    """Missing q parameter should return 422."""
    resp = finance_client.get("/api/query")
    assert resp.status_code == 422


# ── Tests: DELETE /api/documents/{id} ────────────────────────────────────────

def test_delete_document(finance_client):
    """Deleting a document should remove it from the list."""
    content = "Investment portfolio value: $250,000."
    ingest_resp = finance_client.post(
        "/api/ingest",
        files={"file": _make_text_file(content, "investments.txt")},
    )
    doc_id = ingest_resp.json()["id"]

    del_resp = finance_client.delete(f"/api/documents/{doc_id}")
    assert del_resp.status_code == 200

    docs = finance_client.get("/api/documents").json()["documents"]
    assert all(d["id"] != doc_id for d in docs)


def test_delete_removes_chunks_from_search(finance_client):
    """After deletion, the document's content should not appear in search results."""
    import finance_service.db as db_mod

    content = "Unique phrase xyzzy_test_delete: $99,999."
    ingest_resp = finance_client.post(
        "/api/ingest",
        files={"file": _make_text_file(content, "to_delete.txt")},
    )
    doc_id = ingest_resp.json()["id"]

    # Confirm it's findable
    before = finance_client.get("/api/query", params={"q": "xyzzy_test_delete"}).json()
    assert len(before["results"]) >= 1

    finance_client.delete(f"/api/documents/{doc_id}")

    after = finance_client.get("/api/query", params={"q": "xyzzy_test_delete"}).json()
    assert after["results"] == []


def test_delete_nonexistent_returns_404(finance_client):
    resp = finance_client.delete("/api/documents/99999")
    assert resp.status_code == 404


# ── Tests: /health ────────────────────────────────────────────────────────────

def test_health_returns_ok(finance_client):
    resp = finance_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Tests: chunking logic ─────────────────────────────────────────────────────

def test_chunk_text_splits_long_content():
    """Long text should be split into multiple chunks."""
    import finance_service.main as main_mod
    long_text = "word " * 1000  # ~5000 chars, should produce multiple chunks
    chunks = main_mod._chunk_text(long_text)
    assert len(chunks) > 1


def test_chunk_text_short_content_is_single_chunk():
    """Short text fits in one chunk."""
    import finance_service.main as main_mod
    short_text = "Balance: $1,000."
    chunks = main_mod._chunk_text(short_text)
    assert len(chunks) == 1
    assert chunks[0]["text"] == short_text
