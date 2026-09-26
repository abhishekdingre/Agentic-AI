"""Server-side confidence scoring (never self-reported by the model — the
model's RawAnswer/RawClaim shapes have no confidence field at all).

Per-claim score:
    score = 0.40 * independence + 0.35 * avg_similarity + 0.25 * diversity
where:
    independence   = min(distinct_source_count / 3, 1)
    diversity       = min(distinct_domain_count / 2, 1)
    avg_similarity  = mean of each citation's retrieval similarity, looked up
                      from the session's retrieval registry (never from the
                      model)

Scores are bucketed into High/Moderate/Low/Insufficient, with two hard
rules layered on top of the raw score:
  - Floor rule: a claim backed by only one distinct source can never be
    bucketed High, no matter how high its score — single-source claims cap
    out at Moderate.
  - Cap rule: the OVERALL answer confidence uses a supermajority-threshold
    rollup across claims — the highest bucket that at least two-thirds of
    all claims meet or exceed (not a single-worst-claim veto and not an
    average) — additionally capped at Moderate whenever gaps_or_caveats is
    non-empty.
"""
from __future__ import annotations

import math

from backend.schemas import ConfidenceBucket
from backend.vector_store import Hit

_BUCKET_ORDER: list[ConfidenceBucket] = ["Insufficient", "Low", "Moderate", "High"]


def _rank(bucket: ConfidenceBucket) -> int:
    return _BUCKET_ORDER.index(bucket)


def _bucket_from_score(score: float) -> ConfidenceBucket:
    if score >= 0.75:
        return "High"
    if score >= 0.50:
        return "Moderate"
    if score >= 0.25:
        return "Low"
    return "Insufficient"


def score_claim(citation_hits: list[Hit]) -> tuple[float, ConfidenceBucket]:
    """`citation_hits` are the registry Hit objects backing one claim's
    (already grounding-validated) citations. Returns (raw_score, bucket)."""
    if not citation_hits:
        return 0.0, "Insufficient"

    distinct_sources = {h.source_id for h in citation_hits}
    distinct_domains = {h.domain for h in citation_hits}

    independence = min(len(distinct_sources) / 3, 1.0)
    diversity = min(len(distinct_domains) / 2, 1.0)
    avg_similarity = sum(h.similarity for h in citation_hits) / len(citation_hits)

    score = 0.40 * independence + 0.35 * avg_similarity + 0.25 * diversity
    bucket = _bucket_from_score(score)

    if len(distinct_sources) == 1 and bucket == "High":
        bucket = "Moderate"

    return score, bucket


def overall_confidence(
    claim_buckets: list[ConfidenceBucket], gaps_or_caveats: list[str]
) -> ConfidenceBucket:
    """Supermajority-threshold rollup across claims: the overall bucket is
    the highest bucket that at least two-thirds of all claims meet or
    exceed — not a single-worst-claim veto (weakest-link) and not an
    average. This is additionally capped at Moderate if there are any gaps
    or caveats — an answer that is honest about a gap can never be reported
    as fully High-confidence overall, even if every individual claim is."""
    if not claim_buckets:
        overall: ConfidenceBucket = "Insufficient"
    else:
        ranks = sorted((_rank(b) for b in claim_buckets), reverse=True)
        n = len(ranks)
        threshold_index = math.ceil(2 * n / 3) - 1
        overall = _BUCKET_ORDER[ranks[threshold_index]]

    if gaps_or_caveats and _rank(overall) > _rank("Moderate"):
        overall = "Moderate"

    return overall
