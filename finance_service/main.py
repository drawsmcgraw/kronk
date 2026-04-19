import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pypdf import PdfReader

from db import delete_document, init_db, insert_document, list_documents, search_chunks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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


@app.get("/health")
def health():
    docs = list_documents()
    return {"status": "ok", "document_count": len(docs)}
