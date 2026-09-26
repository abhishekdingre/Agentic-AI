"""Fixed structured schemas (commitment #3: return a fixed structured schema,
never free text).

Two layers, by design:
- `RawAnswer` (+ its nested models) is the ONLY shape the model is allowed
  to submit, via the `submit_structured_answer` tool. It is deliberately
  minimal: citations are just (source_id, chunk_id, quote) — no
  title/domain/version/similarity, because the model's self-report of that
  metadata is never trusted.
- `FinalAnswer` (+ its nested models) is what the server actually returns
  from `POST /api/query`. Every citation's title/domain/version/similarity
  is filled in server-side from the allowlist and this session's retrieval
  log — never copied from anything the model said — and confidence is
  computed server-side (`backend/confidence.py`), never self-reported.

`ConfigDict(extra="forbid")` on every model is what makes
`additionalProperties: false` come through automatically when we derive the
strict Anthropic tool schema from `RawAnswer.model_json_schema()` in
`backend/tools.py`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ConfidenceBucket = Literal["High", "Moderate", "Low", "Insufficient"]


# --- Raw (model-submitted) shapes -------------------------------------------------


class RawCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    chunk_id: str
    quote: str


class RawClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    statement: str
    citations: list[RawCitation]


class RawAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[RawClaim]
    gaps_or_caveats: list[str]
    refused: bool
    refusal_reason: str | None
    escalate_for_human_review: bool


# --- Final (server-enriched) shapes -----------------------------------------------


class FinalCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    chunk_id: str
    quote: str
    title: str
    domain: str
    version: str
    similarity: float


class FinalClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    statement: str
    citations: list[FinalCitation]
    confidence: ConfidenceBucket


class RetrievalLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    tool: str
    args: dict
    result_summary: str


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[FinalClaim]
    gaps_or_caveats: list[str]
    refused: bool
    refusal_reason: str | None
    escalate_for_human_review: bool
    overall_confidence: ConfidenceBucket
    retrieval_log: list[RetrievalLogEntry]
