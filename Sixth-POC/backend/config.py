"""Central configuration for the agentic RAG backend.

Loads environment variables (via python-dotenv, falling back to whatever is
already in the process environment) and exposes a small, typed surface the
rest of the backend imports from, instead of every module reaching for
`os.environ` directly.

Auth preference order for the Anthropic client (see AIDLC_PLAN.md "Auth"):
1. ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL (this environment's confirmed
   working path) -> passed as `auth_token=`/`base_url=`.
2. ANTHROPIC_API_KEY from a `.env` file (portable fallback for other
   environments) -> passed as `api_key=`.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env if present; does not override already-set process env vars.
load_dotenv(PROJECT_ROOT / ".env")

# --- Anthropic auth -------------------------------------------------------
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or None
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL") or None
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or None

MODEL_ID = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


def anthropic_client_kwargs() -> dict:
    """Build the kwargs for `anthropic.Anthropic(...)` per the preference order above."""
    if ANTHROPIC_AUTH_TOKEN:
        kwargs: dict = {"auth_token": ANTHROPIC_AUTH_TOKEN}
        if ANTHROPIC_BASE_URL:
            kwargs["base_url"] = ANTHROPIC_BASE_URL
        return kwargs
    if ANTHROPIC_API_KEY:
        return {"api_key": ANTHROPIC_API_KEY}
    raise RuntimeError(
        "No Anthropic credentials found. Set ANTHROPIC_AUTH_TOKEN (+ optionally "
        "ANTHROPIC_BASE_URL) or ANTHROPIC_API_KEY in the environment or .env file."
    )


# --- Data paths ------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
ALLOWLIST_PATH = DATA_DIR / "allowlist.json"
CORPUS_DIR = DATA_DIR / "corpus"
CHROMA_PERSIST_DIR = DATA_DIR / "chroma"
COLLECTION_NAME = "axn2401_corpus"

# --- Chunking ---------------------------------------------------------------
CHUNK_MAX_CHARS = 700
CHUNK_OVERLAP_CHARS = 100

# --- Embeddings ---------------------------------------------------------------
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# --- Retrieval ---------------------------------------------------------------
TOP_K = 5
ALL_DOMAINS = ["literature", "patents", "clinical_trials", "internal_reports"]

# --- Agent loop ---------------------------------------------------------------
MAX_ITERATIONS = 6
MAX_GROUNDING_RETRIES = 2

# --- Telemetry ---------------------------------------------------------------
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "agentic-rag-backend")
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
)
