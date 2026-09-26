"""Unit tests for backend/confidence.py — pure arithmetic, no model calls."""
from __future__ import annotations

from backend import confidence
from backend.vector_store import Hit


def _hit(source_id, domain, similarity) -> Hit:
    return Hit(
        chunk_id=f"{source_id}#0",
        source_id=source_id,
        domain=domain,
        text="irrelevant",
        similarity=similarity,
        distance=1.0 - similarity,
    )


def test_no_citations_is_insufficient():
    score, bucket = confidence.score_claim([])
    assert score == 0.0
    assert bucket == "Insufficient"


def test_single_source_never_reaches_high_even_at_max_similarity():
    hits = [_hit("A", "literature", 1.0)]
    score, bucket = confidence.score_claim(hits)
    # 0.40*(1/3) + 0.35*1.0 + 0.25*(1/2) = 0.6083... < 0.75, so this is
    # already below High on the raw formula; the explicit single-source
    # floor rule in confidence.py exists as a second line of defense in case
    # the weights ever change.
    assert bucket != "High"
    assert 0.0 < score < 0.75


def test_three_sources_two_domains_high_similarity_reaches_high():
    hits = [
        _hit("A", "literature", 1.0),
        _hit("B", "clinical_trials", 1.0),
        _hit("C", "clinical_trials", 1.0),
    ]
    score, bucket = confidence.score_claim(hits)
    assert score == 1.0
    assert bucket == "High"


def test_two_sources_one_domain_moderate_similarity():
    hits = [_hit("A", "literature", 0.6), _hit("B", "literature", 0.6)]
    score, bucket = confidence.score_claim(hits)
    # independence = 2/3, diversity = 1/2, avg_similarity = 0.6
    expected = 0.40 * (2 / 3) + 0.35 * 0.6 + 0.25 * 0.5
    assert abs(score - expected) < 1e-9
    assert bucket in ("Low", "Moderate")


def test_low_similarity_single_domain_is_low_or_insufficient():
    hits = [_hit("A", "literature", 0.1)]
    score, bucket = confidence.score_claim(hits)
    assert bucket in ("Low", "Insufficient")


def test_overall_confidence_two_of_three_high_is_high():
    # Supermajority threshold: 2 of 3 claims (exactly two-thirds) are High,
    # so High is the highest bucket a genuine supermajority supports.
    overall = confidence.overall_confidence(["High", "Moderate", "High"], gaps_or_caveats=[])
    assert overall == "High"


def test_overall_confidence_thirteen_claims_mostly_strong_is_not_low():
    # Mirrors the real TRIAGE_REPORT.md finding 3.3 scenario: a
    # 13-claim, 4-domain answer where 4 claims are legitimately
    # single-sourced (each individually capped at Low) but the remaining 9
    # are Moderate/High. The old weakest-link rule reported this whole
    # answer as "Low"; the supermajority threshold must not.
    buckets = ["High"] * 5 + ["Moderate"] * 4 + ["Low"] * 4
    overall = confidence.overall_confidence(buckets, gaps_or_caveats=[])
    assert overall in ("Moderate", "High")


def test_overall_confidence_majority_weak_evidence_is_still_low():
    # Confirms the fix doesn't let a strong minority inflate a genuinely
    # thin/contradicted answer: 6 of 8 claims (well over one-third) are
    # Low, so Low is the highest bucket a genuine two-thirds supermajority
    # actually supports.
    buckets = ["Low"] * 6 + ["High"] * 2
    overall = confidence.overall_confidence(buckets, gaps_or_caveats=[])
    assert overall == "Low"


def test_overall_confidence_all_high_no_gaps_is_high():
    overall = confidence.overall_confidence(["High", "High"], gaps_or_caveats=[])
    assert overall == "High"


def test_overall_confidence_capped_at_moderate_when_gaps_present():
    overall = confidence.overall_confidence(["High", "High"], gaps_or_caveats=["some gap"])
    assert overall == "Moderate"


def test_overall_confidence_gaps_do_not_raise_a_low_bucket():
    # The cap only pulls High down to Moderate; it must never raise a
    # genuinely weaker bucket up.
    overall = confidence.overall_confidence(["Low", "High"], gaps_or_caveats=["some gap"])
    assert overall == "Low"


def test_overall_confidence_no_claims_is_insufficient():
    overall = confidence.overall_confidence([], gaps_or_caveats=[])
    assert overall == "Insufficient"
