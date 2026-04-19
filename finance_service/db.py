import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def _db_path() -> Path:
    return Path(os.getenv("FINANCE_DB_PATH", "/data/finances.db"))


@contextmanager
def get_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                size_bytes INTEGER,
                ingested_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                page INTEGER,
                text TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
        """)


def insert_document(name: str, size_bytes: int, chunks: list[dict]) -> int:
    """Insert a document and its text chunks. Returns the document id."""
    from datetime import datetime
    ingested_at = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO documents (name, size_bytes, ingested_at) VALUES (?, ?, ?)",
            (name, size_bytes, ingested_at),
        )
        doc_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO chunks (doc_id, page, text) VALUES (?, ?, ?)",
            [(doc_id, c.get("page"), c["text"]) for c in chunks],
        )
    return doc_id


def search_chunks(query: str, limit: int = 8) -> list[dict]:
    """Simple case-insensitive keyword search across all chunks."""
    terms = [t.strip() for t in query.lower().split() if len(t.strip()) > 2]
    if not terms:
        return []

    # Build a LIKE condition for each term (AND logic)
    conditions = " AND ".join(["lower(c.text) LIKE ?" for _ in terms])
    params = [f"%{t}%" for t in terms] + [limit]

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT c.id, c.doc_id, c.page, c.text, d.name AS doc_name
            FROM chunks c
            JOIN documents d ON d.id = c.doc_id
            WHERE {conditions}
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def list_documents() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT d.id, d.name, d.size_bytes, d.ingested_at, COUNT(c.id) AS chunk_count "
            "FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id "
            "GROUP BY d.id ORDER BY d.ingested_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    return cur.rowcount > 0
