"""ChromaDB + fastembed vector store for health data."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import chromadb
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_client: Optional[chromadb.ClientAPI] = None
_collection = None
_model: Optional[TextEmbedding] = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        logger.info("Loading embedding model %s", _EMBED_MODEL)
        _model = TextEmbedding(_EMBED_MODEL)
    return _model


def _chroma_dir() -> Path:
    db_path = Path(os.getenv("HEALTH_DB_PATH", "/data/health.db"))
    return db_path.parent / "chroma"


def _get_collection():
    global _client, _collection
    if _collection is None:
        d = _chroma_dir()
        d.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(d))
        _collection = _client.get_or_create_collection(
            name="health_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Vector store ready — %d chunks indexed", _collection.count())
    return _collection


def _date_int(date_str: str) -> int:
    """Convert ISO date string to sortable int: '2025-06-15' → 20250615."""
    return int(date_str.replace("-", ""))


def upsert_chunks(chunks: list[dict]) -> None:
    """Each chunk: {id: str, text: str, metadata: dict}. metadata must have 'date' and 'type'."""
    if not chunks:
        return
    model = _get_model()
    col = _get_collection()
    texts = [c["text"] for c in chunks]
    embeddings = [e.tolist() for e in model.embed(texts)]
    metadatas = []
    for c in chunks:
        m = dict(c["metadata"])
        if "date" in m:
            m["date_int"] = _date_int(m["date"])
        metadatas.append(m)
    col.upsert(
        ids=[c["id"] for c in chunks],
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    logger.debug("Upserted %d health chunks", len(chunks))


def search(
    query: str,
    n_results: int = 6,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    model = _get_model()
    col = _get_collection()
    if col.count() == 0:
        return []

    query_emb = list(model.embed([query]))[0].tolist()

    where: dict | None = None
    if start_date and end_date:
        where = {"$and": [{"date_int": {"$gte": _date_int(start_date)}}, {"date_int": {"$lte": _date_int(end_date)}}]}
    elif start_date:
        where = {"date_int": {"$gte": _date_int(start_date)}}
    elif end_date:
        where = {"date_int": {"$lte": _date_int(end_date)}}

    kwargs: dict = {
        "query_embeddings": [query_emb],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    res = col.query(**kwargs)
    return [
        {"text": doc, "metadata": meta, "score": round(1 - dist, 3)}
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        )
    ]


def chunk_count() -> int:
    return _get_collection().count()
