"""MCP server exposing three read-only tools over the same backend the live
agent uses:
  - `search_corpus` — identical retrieval path (embeddings + Chroma query) to
    the tool `backend/agent.py` gives the model, so an MCP client can
    exercise retrieval directly.
  - `allowlist_status` — current vs superseded document counts per domain.
  - `ingestion_status` — whether the vector store is populated and how many
    chunks it holds.

Registered in the project root's `.mcp.json` (stdio transport, project venv
python) so any MCP-aware client, including Claude Code itself, can inspect
retrieval health without going through the FastAPI HTTP API.

Run directly: `venv/bin/python backend/mcp_server.py` (stdio transport).

Uses `mcp` 2.x, where the v1 `FastMCP` class was renamed to `MCPServer`
(confirmed against the installed package: `mcp.server.fastmcp` raises
`ModuleNotFoundError` on this version and points at
`mcp.server.mcpserver.MCPServer`).
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer

from backend import allowlist
from backend.config import ALL_DOMAINS, TOP_K
from backend.embeddings import embed_query
from backend.vector_store import get_default_store

server = MCPServer(name="agentic-rag-poc-backend")


@server.tool()
def search_corpus(query: str, domains: list[str] | None = None) -> list[dict]:
    """Search the approved evidence corpus for chunks relevant to `query`.

    `domains` restricts the search to a subset of {literature, patents,
    clinical_trials, internal_reports}; omit or pass an empty list to search
    all domains. Uses the exact same retrieval path (embeddings + Chroma
    query) as the live agent's search_corpus tool. Only documents currently
    marked `current` in data/allowlist.json are ever returned — superseded
    versions are architecturally unretrievable (never embedded by
    backend/ingest.py in the first place).
    """
    selected = [d for d in (domains or []) if d in ALL_DOMAINS] or list(ALL_DOMAINS)
    store = get_default_store()
    query_embedding = embed_query(query)
    hits = store.query(query_embedding, selected, TOP_K)
    return [
        {
            "chunk_id": h.chunk_id,
            "source_id": h.source_id,
            "domain": h.domain,
            "similarity": round(h.similarity, 3),
            "text": h.text,
        }
        for h in hits
    ]


@server.tool()
def allowlist_status() -> dict:
    """Report current vs superseded document counts per domain, from
    data/allowlist.json — lets an operator confirm what's actually approved
    for retrieval right now."""
    entries = allowlist.all_entries()
    per_domain: dict[str, dict[str, int]] = {}
    for e in entries:
        bucket = per_domain.setdefault(e.domain, {"current": 0, "superseded": 0})
        bucket[e.status] += 1
    superseded_ids = [e.source_id for e in entries if e.status == "superseded"]
    return {
        "total_documents": len(entries),
        "current_documents": len(allowlist.current()),
        "superseded_documents": superseded_ids,
        "per_domain": per_domain,
    }


@server.tool()
def ingestion_status() -> dict:
    """Report whether the vector store has been populated and how many
    chunks it holds, so an operator can tell whether `backend/ingest.py`
    needs to be (re-)run."""
    store = get_default_store()
    count = store.count()
    return {
        "collection_populated": count > 0,
        "chunk_count": count,
        "current_documents_in_allowlist": len(allowlist.current()),
    }


if __name__ == "__main__":
    server.run()
