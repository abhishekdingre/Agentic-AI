"""FastAPI entry point: POST /api/query runs the full agentic RAG loop
synchronously and returns a FinalAnswer — the fixed structured schema is the
only response shape this API ever returns (commitment #3).

Run locally: `venv/bin/uvicorn backend.main:app --reload --port 8000`
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from backend import allowlist, telemetry
from backend.agent import run_query
from backend.config import ALL_DOMAINS
from backend.embeddings import embed_query
from backend.schemas import FinalAnswer
from backend.vector_store import get_default_store

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Agentic RAG POC — Backend", version="0.1.0")

# CORS open for local dev — the frontend runs on a different local port and
# this is a POC, not a deployed service.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    domains: list[str] = []

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        # A blank/whitespace-only question has no content for the model to
        # respond to and previously reached anthropic.messages.create()
        # uncaught, producing a raw non-JSON 500 (commitment #3 violation:
        # the API must never return anything other than the fixed schema —
        # rejecting this at the request-validation boundary is how we avoid
        # a free-text/non-schema response for this input).
        if not value.strip():
            raise ValueError("question must not be blank or whitespace-only")
        return value

    @field_validator("domains")
    @classmethod
    def _domains_are_recognized(cls, value: list[str]) -> list[str]:
        # Reject unrecognized domain values outright rather than letting
        # `agent._normalize_domains()` silently widen an unrecognized/typo'd
        # selection out to all 4 domains — a caller that asked for a scoped
        # answer should get a clear error, not a silently broader one.
        unrecognized = sorted({d for d in value if d not in ALL_DOMAINS})
        if unrecognized:
            raise ValueError(
                f"unrecognized domain(s) {unrecognized}; must be a subset of {ALL_DOMAINS}"
            )
        return value


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/domains")
def domains() -> dict:
    """Domains the frontend can offer as filter options."""
    return {"domains": ALL_DOMAINS}


@app.get("/api/allowlist")
def allowlist_status() -> dict:
    """Current vs superseded document counts per domain — lets the frontend
    (or an operator) see what's actually retrievable without exposing the
    raw corpus text."""
    entries = allowlist.all_entries()
    per_domain: dict[str, dict[str, int]] = {}
    for e in entries:
        bucket = per_domain.setdefault(e.domain, {"current": 0, "superseded": 0})
        bucket[e.status] += 1
    return {
        "total_documents": len(entries),
        "current_documents": len(allowlist.current()),
        "per_domain": per_domain,
    }


@app.post("/api/query", response_model=FinalAnswer)
def query(request: QueryRequest) -> FinalAnswer:
    """Runs the agentic loop (backend/agent.py) synchronously for one
    question and returns the server-built FinalAnswer. FastAPI runs this
    (sync `def`) handler in its threadpool, so one slow query doesn't block
    the event loop for others."""
    return run_query(request.question, request.domains)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Defense in depth for commitment #3: no code path in this API may
    return free text. `QueryRequest`'s validators close the specific
    blank-question/invalid-domain gaps, but ANY other unexpected failure
    (upstream API error, embedding/vector-store failure, etc.) must still
    come back as valid JSON rather than falling through to Starlette's
    default plain-text 500 response."""
    logging.getLogger(__name__).exception(
        "Unhandled exception while handling %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "An unexpected error occurred while processing the request.",
        },
    )


@app.on_event("startup")
def _setup_observability() -> None:
    """Best-effort OTel setup — never blocks or fails startup if the OTLP
    collector (see docker-compose.observability.yml) isn't running."""
    try:
        telemetry.setup_telemetry()
    except Exception:
        logging.getLogger(__name__).exception(
            "OpenTelemetry setup failed; continuing without export "
            "(spans/metrics will be created but never leave the process)."
        )


@app.on_event("startup")
def _warn_if_corpus_empty() -> None:
    try:
        count = get_default_store().count()
    except Exception:
        logging.getLogger(__name__).exception(
            "Could not reach the vector store on startup — did you run "
            "`venv/bin/python backend/ingest.py`?"
        )
        return
    if count == 0:
        logging.getLogger(__name__).warning(
            "Vector store is empty (0 chunks). Run "
            "`venv/bin/python backend/ingest.py` before querying."
        )


@app.on_event("startup")
def _warmup_embedding_model() -> None:
    """Best-effort embedding-model warmup (PERF_REVIEW_BACKEND.md P1.2):
    `embeddings.get_embedder()` is an `lru_cache`d singleton that is
    otherwise only ever loaded lazily on whichever request happens to be
    the first real `search_corpus` call after process start — measured at
    ~15.9s cold SentenceTransformer load + ~0.57s first-inference warmup.
    Paying that cost once here, at startup, keeps it off of an arbitrary
    user's first query. Never blocks or fails startup if the model/store
    aren't reachable yet."""
    try:
        embedding = embed_query("warmup")
        store = get_default_store()
        if store.count() > 0:
            store.query(embedding, ALL_DOMAINS, 1)
    except Exception:
        logging.getLogger(__name__).exception(
            "Embedding-model warmup failed; continuing without it (the "
            "first real query will pay the cold-load cost instead)."
        )
