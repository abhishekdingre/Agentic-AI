"""Idempotent ingestion: clears and rebuilds the Chroma collection from
scratch, embedding only documents with status == "current".

This is layer 1 of commitment #1 (allowlist enforcement): superseded
documents (e.g. IR-002-v1) are never chunked or embedded at all, so they are
architecturally unretrievable — not filtered out after the fact.

Run directly: `venv/bin/python backend/ingest.py` (or `venv/bin/python -m
backend.ingest` from the project root — both work; the sys.path shim below
makes the plain script invocation work too, since running a file inside a
package directly would otherwise leave the `backend` package itself
unimportable).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import allowlist
from backend.chunking import chunk_text
from backend.config import CORPUS_DIR, PROJECT_ROOT, TOP_K
from backend.embeddings import embed_texts
from backend.vector_store import VectorStore


def _read_document(file_path: str) -> str:
    full_path = PROJECT_ROOT / file_path
    with open(full_path, encoding="utf-8") as f:
        return f.read()


def run_ingest(store: VectorStore | None = None) -> dict:
    store = store or VectorStore()
    store.reset_collection()

    entries = allowlist.current()
    if not entries:
        print("WARNING: no current documents in allowlist; collection left empty.")
        return {"documents": 0, "chunks": 0}

    all_ids: list[str] = []
    all_texts: list[str] = []
    all_metadatas: list[dict] = []

    per_domain_counts: dict[str, int] = {}

    for entry in entries:
        text = _read_document(entry.file_path)
        chunks = chunk_text(text)
        for chunk in chunks:
            chunk_id = f"{entry.source_id}#{chunk.index}"
            all_ids.append(chunk_id)
            all_texts.append(chunk.text)
            all_metadatas.append(
                {
                    "source_id": entry.source_id,
                    "domain": entry.domain,
                    "title": entry.title,
                    "version": entry.version,
                    "chunk_index": chunk.index,
                }
            )
        per_domain_counts[entry.domain] = per_domain_counts.get(entry.domain, 0) + 1

    print(f"Embedding {len(all_texts)} chunks from {len(entries)} current documents...")
    t0 = time.time()
    embeddings = embed_texts(all_texts)
    print(f"Embedded in {time.time() - t0:.1f}s")

    store.add(ids=all_ids, embeddings=embeddings, documents=all_texts, metadatas=all_metadatas)

    summary = {
        "documents": len(entries),
        "chunks": len(all_ids),
        "per_domain_documents": per_domain_counts,
    }
    return summary


def main() -> None:
    summary = run_ingest()
    print("Ingestion complete:")
    print(f"  documents embedded: {summary['documents']}")
    print(f"  chunks embedded:    {summary['chunks']}")
    print(f"  per-domain document counts: {summary.get('per_domain_documents')}")

    superseded = [e for e in allowlist.all_entries() if e.status == "superseded"]
    if superseded:
        print(
            f"  (skipped {len(superseded)} superseded document(s), "
            f"architecturally unretrievable: {[e.source_id for e in superseded]})"
        )

    store = VectorStore()
    print(f"  collection count in Chroma: {store.count()}")
    print(f"  (query-time top_k default: {TOP_K}, corpus dir: {CORPUS_DIR})")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - CLI convenience
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        raise
