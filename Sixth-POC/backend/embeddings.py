"""Local sentence-transformers embeddings, computed explicitly.

Deliberately not registered as a Chroma embedding function so the embedding
step stays inspectable/testable in isolation (see AIDLC_PLAN.md "Stack").
"""
from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from backend.config import EMBEDDING_MODEL_NAME


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    """Singleton loader for the embedding model (cached across the process)."""
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts, L2-normalized (so dot product == cosine similarity)."""
    if not texts:
        return []
    model = get_embedder()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
