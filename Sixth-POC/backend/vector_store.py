"""Thin wrapper around a local `chromadb.PersistentClient` collection.

Embeddings are computed explicitly by `backend.embeddings` (not via a
registered Chroma embedding function) and passed in, so the embedding step
stays inspectable. Query-time domain filtering uses Chroma's `where`
metadata filter (`{"domain": {"$in": [...]}}`) — this is query-time
allowlist enforcement (layer 2 of commitment #1); layer 1 is that
`ingest.py` never embeds non-current documents in the first place, and
layer 3 is `grounding.py` re-checking every hit against
`allowlist.approved_current_ids()` at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass

import chromadb

from backend.config import CHROMA_PERSIST_DIR, COLLECTION_NAME


@dataclass
class Hit:
    chunk_id: str
    source_id: str
    domain: str
    text: str
    similarity: float
    distance: float


def _distance_to_similarity(distance: float) -> float:
    """L2 distance -> similarity, per AIDLC_PLAN.md: max(0.0, 1.0 - dist/2.0).

    (Embeddings are normalized, so L2 distance is bounded in [0, 2]; this
    maps that range onto a [0, 1] similarity score.)
    """
    return max(0.0, 1.0 - distance / 2.0)


class VectorStore:
    def __init__(self, persist_dir=CHROMA_PERSIST_DIR, collection_name: str = COLLECTION_NAME):
        self.persist_dir = str(persist_dir)
        self.collection_name = collection_name
        self._client = chromadb.PersistentClient(path=self.persist_dir)

    @property
    def client(self):
        return self._client

    def get_or_create_collection(self):
        # metadata={"hnsw:space": "l2"} is Chroma's default distance metric;
        # set explicitly since the similarity conversion above assumes L2.
        return self._client.get_or_create_collection(
            self.collection_name, metadata={"hnsw:space": "l2"}
        )

    def reset_collection(self):
        """Idempotent rebuild support: drop the collection if it exists, then recreate empty."""
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        return self.get_or_create_collection()

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        if not ids:
            return
        collection = self.get_or_create_collection()
        collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    def count(self) -> int:
        return self.get_or_create_collection().count()

    def query(
        self,
        query_embedding: list[float],
        domains: list[str],
        top_k: int,
    ) -> list[Hit]:
        """Query with a metadata filter restricting results to `domains`.

        `domains` must already be the caller's approved/selected domain set —
        this function does not itself widen or narrow it.
        """
        collection = self.get_or_create_collection()
        if collection.count() == 0:
            return []
        where = {"domain": {"$in": domains}} if domains else None
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )
        hits: list[Hit] = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        for chunk_id, doc, meta, dist in zip(ids, docs, metas, dists):
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    source_id=meta.get("source_id", ""),
                    domain=meta.get("domain", ""),
                    text=doc,
                    similarity=_distance_to_similarity(dist),
                    distance=dist,
                )
            )
        return hits


_default_store: VectorStore | None = None


def get_default_store() -> VectorStore:
    global _default_store
    if _default_store is None:
        _default_store = VectorStore()
    return _default_store
