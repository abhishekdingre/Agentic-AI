"""Deterministic grounding validation (commitment #2: every claim's citation
is checked server-side against what was actually retrieved this session —
never trust the model's self-reported citation, and never use an LLM judge
for this check).

Three checks per citation, all pure Python / set membership / string
matching:
  (a) chunk_id must be a chunk this session's search_corpus calls actually
      returned (the session retrieval registry) — rules out fabricated
      chunk_ids.
  (b) source_id must match that chunk's real source_id (from the registry,
      not from the model) AND must currently be in
      allowlist.approved_current_ids() — this is the runtime defense-in-depth
      layer of commitment #1 (layer 3; layers 1/2 are ingest.py only ever
      embedding current documents, and the per-request domain enum built in
      tools.py).
  (c) quote must actually appear in that chunk's text — exact substring
      match (with whitespace normalized), falling back to a token-overlap
      ratio, so trivial whitespace differences don't cause false rejections
      while invented quotes still fail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend import allowlist
from backend.vector_store import Hit

TOKEN_OVERLAP_THRESHOLD = 0.8

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _quote_matches(quote: str, chunk_text: str) -> bool:
    quote = quote.strip()
    if not quote:
        return False
    if quote in chunk_text:
        return True
    normalized_quote = " ".join(quote.split())
    normalized_chunk = " ".join(chunk_text.split())
    if normalized_quote in normalized_chunk:
        return True
    quote_tokens = _tokenize(quote)
    if not quote_tokens:
        return False
    chunk_tokens = set(_tokenize(chunk_text))
    matched = sum(1 for t in quote_tokens if t in chunk_tokens)
    overlap_ratio = matched / len(quote_tokens)
    return overlap_ratio >= TOKEN_OVERLAP_THRESHOLD


@dataclass
class CitationCheck:
    source_id: str
    chunk_id: str
    quote: str
    valid: bool
    reason: str | None = None


@dataclass
class ClaimCheck:
    claim_id: str
    valid: bool
    citation_checks: list[CitationCheck] = field(default_factory=list)

    @property
    def failure_reasons(self) -> list[str]:
        return [
            f"citation (source_id={c.source_id!r}, chunk_id={c.chunk_id!r}): {c.reason}"
            for c in self.citation_checks
            if not c.valid
        ]


@dataclass
class GroundingReport:
    claim_checks: list[ClaimCheck]

    @property
    def all_valid(self) -> bool:
        return all(c.valid for c in self.claim_checks)

    @property
    def invalid_claims(self) -> list[ClaimCheck]:
        return [c for c in self.claim_checks if not c.valid]

    @property
    def valid_claim_ids(self) -> set[str]:
        return {c.claim_id for c in self.claim_checks if c.valid}

    def feedback_text(self) -> str:
        """Human-readable, per-claim/per-citation feedback suitable for an
        `is_error` tool_result sent back to the model for a grounding retry.
        Empty string if everything validated."""
        if self.all_valid:
            return ""
        lines = ["Grounding check FAILED for the following claim(s):"]
        for c in self.invalid_claims:
            lines.append(f"- claim_id={c.claim_id!r}:")
            for reason in c.failure_reasons:
                lines.append(f"    - {reason}")
        lines.append(
            "Fix these by using only chunk_ids that search_corpus actually "
            "returned in this conversation, only verbatim substrings of that "
            "chunk's text as quotes, and only currently-approved source_ids. "
            "Call submit_structured_answer again with corrected or removed "
            "claims."
        )
        return "\n".join(lines)


def validate_claim(claim, registry: dict[str, Hit], approved_ids: set[str]) -> ClaimCheck:
    citation_checks: list[CitationCheck] = []
    for citation in claim.citations:
        hit = registry.get(citation.chunk_id)
        if hit is None:
            citation_checks.append(
                CitationCheck(
                    source_id=citation.source_id,
                    chunk_id=citation.chunk_id,
                    quote=citation.quote,
                    valid=False,
                    reason=(
                        "chunk_id was not returned by any search_corpus call in "
                        "this session (possibly fabricated)"
                    ),
                )
            )
            continue

        if hit.source_id != citation.source_id:
            citation_checks.append(
                CitationCheck(
                    source_id=citation.source_id,
                    chunk_id=citation.chunk_id,
                    quote=citation.quote,
                    valid=False,
                    reason=(
                        f"source_id mismatch: chunk {citation.chunk_id!r} actually "
                        f"belongs to source_id {hit.source_id!r}, not {citation.source_id!r}"
                    ),
                )
            )
            continue

        if hit.source_id not in approved_ids:
            citation_checks.append(
                CitationCheck(
                    source_id=citation.source_id,
                    chunk_id=citation.chunk_id,
                    quote=citation.quote,
                    valid=False,
                    reason=(
                        f"source_id {hit.source_id!r} is not currently approved "
                        "(superseded or removed from the allowlist)"
                    ),
                )
            )
            continue

        if not _quote_matches(citation.quote, hit.text):
            citation_checks.append(
                CitationCheck(
                    source_id=citation.source_id,
                    chunk_id=citation.chunk_id,
                    quote=citation.quote,
                    valid=False,
                    reason=(
                        "quote is not a verbatim (or close-enough) match against "
                        "the retrieved chunk's text — possibly fabricated or "
                        "over-paraphrased"
                    ),
                )
            )
            continue

        citation_checks.append(
            CitationCheck(
                source_id=citation.source_id,
                chunk_id=citation.chunk_id,
                quote=citation.quote,
                valid=True,
            )
        )

    claim_valid = bool(citation_checks) and all(c.valid for c in citation_checks)
    return ClaimCheck(claim_id=claim.claim_id, valid=claim_valid, citation_checks=citation_checks)


def validate_answer(raw_answer, registry: dict[str, Hit]) -> GroundingReport:
    """Validate every claim in a submitted RawAnswer against the session's
    retrieval registry and the live allowlist. Never inspects any
    model-reported metadata beyond source_id/chunk_id/quote — everything else
    (title/domain/version/similarity) is looked up fresh here and in
    `backend/agent.py` when building the FinalAnswer.

    `allowlist.py` caches the parsed allowlist for the process's lifetime
    (a small JSON file, re-parsed on every call would be wasteful) — but
    this specific check is the runtime defense-in-depth layer, and its
    entire point is to catch a document being superseded *after* the
    process started. Correctness matters more than the negligible cost of
    re-reading a small JSON file, so force a fresh load right before
    checking approval, rather than trusting a potentially process-lifetime-
    stale cache."""
    allowlist.clear_cache()
    approved_ids = allowlist.approved_current_ids()
    claim_checks = [validate_claim(claim, registry, approved_ids) for claim in raw_answer.claims]
    return GroundingReport(claim_checks=claim_checks)
