"""Embedding-based fast-path for the v2 coordinator pipeline.

At startup the orchestrator pre-computes a single embedding per coordinator
tool / agent-tool (using `BAAI/bge-small-en-v1.5` via fastembed — ~130 MB
ONNX, CPU-only, no torch dependency).

On each /api/chat or /v1/chat/completions request the user's text is embedded
and cosine-matched against the precomputed set. If the best similarity
exceeds `KRONK_FAST_PATH_THRESHOLD` AND the matched tool is on the
fast-path safelist (i.e. invocable without LLM-derived arguments), we
**skip the coordinator decide step** and call the tool directly with the
raw query (or no args, depending on the tool). A small final-synthesis LLM
call still produces the user-visible answer from the tool result.

Tools that need structured arguments (query_health needs metric/days,
shopping_list_add needs items, set_timer is gone, etc.) are NOT on the
safelist — high-similarity matches against them fall through to the
coordinator so the LLM can construct the args.

Configuration (env vars):
- KRONK_FAST_PATH_ENABLED    — "true" / "false"  (default: "true")
- KRONK_FAST_PATH_THRESHOLD  — cosine similarity threshold  (default: 0.65)
- KRONK_FAST_PATH_MODEL      — fastembed model name  (default: BAAI/bge-small-en-v1.5)

The threshold is conservative-by-default. After Phase 2.5 bench, tune.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Iterable

logger = logging.getLogger(__name__)

FAST_PATH_ENABLED = os.getenv("KRONK_FAST_PATH_ENABLED", "true").lower() == "true"
FAST_PATH_THRESHOLD = float(os.getenv("KRONK_FAST_PATH_THRESHOLD", "0.65"))
FAST_PATH_MODEL = os.getenv("KRONK_FAST_PATH_MODEL", "BAAI/bge-small-en-v1.5")


# Tools that can be invoked with just the raw user query (or no args at all).
# Anything not on this list falls through to the coordinator even on a
# strong embedding match — we can't derive structured args without an LLM.
SAFELIST_TOOL_INVOCATION: dict[str, str] = {
    # tool_name -> argument shape:
    #   "raw_query": call with {"query": <user_text>}
    #   "no_args":   call with {}
    #   "raw_query_as_location": call with {"location": <user_text>} — unsafe, omit
    "web_search":           "raw_query",
    "shopping_list_view":   "no_args",
    "query_hottub":         "no_args",
    "get_kronk_context":    "no_args",
    "get_weather":          "no_args",  # defaults to home location
}


# Lazy-loaded singletons.
_embedder = None
_corpus: list[tuple[str, "any"]] = []   # [(name, embedding_vector)]
_init_lock = threading.Lock()
_init_done = False


def _normalize(vec: list[float]) -> list[float]:
    import math
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0:
        return vec
    return [x / norm for x in vec]


def _cosine_norm(a: list[float], b: list[float]) -> float:
    # assumes both inputs are pre-normalized
    return sum(x * y for x, y in zip(a, b))


def init(items: dict[str, str]) -> None:
    """Initialize the embedder and precompute embeddings.

    `items` is `{name: text_to_embed}`. `name` is what `match()` returns
    on a hit. `text_to_embed` should be the canonical description of the
    tool / agent — for bare tools, their `function.description`; for
    agent-tools, the `routing_hint`.
    """
    global _embedder, _corpus, _init_done
    if not FAST_PATH_ENABLED:
        logger.info("fast_path: disabled by env")
        _init_done = True
        return
    with _init_lock:
        if _init_done:
            return
        try:
            from fastembed import TextEmbedding
        except ImportError:
            logger.warning("fast_path: fastembed not installed — disabling")
            _init_done = True
            return
        try:
            _embedder = TextEmbedding(model_name=FAST_PATH_MODEL)
            names = list(items.keys())
            texts = list(items.values())
            embeds = [_normalize(list(v)) for v in _embedder.embed(texts)]
            _corpus = list(zip(names, embeds))
            logger.info(
                "fast_path: initialized %d tool embeddings (model=%s threshold=%.2f)",
                len(_corpus), FAST_PATH_MODEL, FAST_PATH_THRESHOLD,
            )
        except Exception as e:
            logger.error("fast_path: init failed (%s) — disabling", e)
            _embedder = None
            _corpus = []
        _init_done = True


def match(query: str) -> tuple[str | None, float]:
    """Embed `query`, cosine-match against the corpus.

    Returns (best_name, best_similarity). best_name is None if:
      - fast-path is disabled / unavailable,
      - no item exceeds the threshold,
      - or the matched item is not on the SAFELIST_TOOL_INVOCATION.

    best_similarity is always returned for telemetry/log purposes.
    """
    if not FAST_PATH_ENABLED or _embedder is None or not _corpus:
        return None, 0.0
    try:
        q_vec = _normalize(list(next(_embedder.embed([query]))))
    except Exception as e:
        logger.warning("fast_path: query embed failed: %s", e)
        return None, 0.0
    best_name = None
    best_sim = -1.0
    for name, vec in _corpus:
        sim = _cosine_norm(q_vec, vec)
        if sim > best_sim:
            best_sim = sim
            best_name = name
    if best_sim < FAST_PATH_THRESHOLD:
        return None, best_sim
    if best_name not in SAFELIST_TOOL_INVOCATION:
        # High-confidence match but tool needs structured args — fall through.
        return None, best_sim
    return best_name, best_sim


def args_for(tool_name: str, query: str) -> dict:
    """Build the argument dict to pass to a fast-path-invoked tool."""
    shape = SAFELIST_TOOL_INVOCATION.get(tool_name)
    if shape == "raw_query":
        return {"query": query}
    return {}  # "no_args" or unknown
