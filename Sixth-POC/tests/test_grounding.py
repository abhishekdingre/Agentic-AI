"""Unit tests for backend/grounding.py — deterministic validation only, no
model calls, no LLM judge. Covers all three checks (chunk_id membership,
source_id match + approval, quote match) plus report aggregation.

Uses the real project allowlist (data/allowlist.json) for the "not
currently approved" case, since it already contains a genuine superseded
document (IR-002-v1) — the version-drift trap doubles as a natural fixture
for this test.
"""
from __future__ import annotations

from backend import grounding
from backend.schemas import RawAnswer, RawCitation, RawClaim
from backend.vector_store import Hit


def _hit(chunk_id, source_id, domain="literature", text="", similarity=0.9) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        source_id=source_id,
        domain=domain,
        text=text,
        similarity=similarity,
        distance=1.0 - similarity,
    )


def _claim(claim_id, citations) -> RawClaim:
    return RawClaim(claim_id=claim_id, statement="Some statement.", citations=citations)


CHUNK_TEXT = "AXN-2401 reduced hydroxyproline content by 58% at 30 mg/kg in the bleomycin model."


def test_valid_citation_passes():
    registry = {"LIT-004#0": _hit("LIT-004#0", "LIT-004", text=CHUNK_TEXT)}
    citation = RawCitation(source_id="LIT-004", chunk_id="LIT-004#0", quote="reduced hydroxyproline content by 58%")
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("c1", [citation])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        registry,
    )
    assert report.all_valid
    assert report.feedback_text() == ""


def test_fabricated_chunk_id_rejected():
    registry: dict = {}  # nothing was ever retrieved this session
    citation = RawCitation(source_id="LIT-004", chunk_id="LIT-004#0", quote="anything")
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("c1", [citation])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        registry,
    )
    assert not report.all_valid
    reason = report.claim_checks[0].citation_checks[0].reason
    assert "not returned" in reason or "fabricated" in reason
    assert "Grounding check FAILED" in report.feedback_text()


def test_source_id_mismatch_rejected():
    registry = {"LIT-004#0": _hit("LIT-004#0", "LIT-004", text=CHUNK_TEXT)}
    # Citation claims this chunk belongs to a different source_id than it really does.
    citation = RawCitation(source_id="LIT-005", chunk_id="LIT-004#0", quote="hydroxyproline")
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("c1", [citation])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        registry,
    )
    assert not report.all_valid
    assert "mismatch" in report.claim_checks[0].citation_checks[0].reason


def test_superseded_source_rejected_via_real_allowlist():
    # IR-002-v1 is genuinely superseded in data/allowlist.json (the
    # version-drift trap). Manually seed the registry as if it had somehow
    # been retrieved, to test the runtime defense-in-depth layer in
    # isolation from ingestion (which would never embed it in the first
    # place).
    registry = {
        "IR-002-v1#0": _hit(
            "IR-002-v1#0", "IR-002-v1", domain="internal_reports", text="NOAEL = 150 mg/kg/day."
        )
    }
    citation = RawCitation(source_id="IR-002-v1", chunk_id="IR-002-v1#0", quote="NOAEL = 150 mg/kg/day")
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("c1", [citation])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        registry,
    )
    assert not report.all_valid
    assert "not currently approved" in report.claim_checks[0].citation_checks[0].reason


def test_fabricated_quote_rejected():
    registry = {"LIT-004#0": _hit("LIT-004#0", "LIT-004", text=CHUNK_TEXT)}
    citation = RawCitation(
        source_id="LIT-004", chunk_id="LIT-004#0", quote="completely invented sentence not in the chunk"
    )
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("c1", [citation])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        registry,
    )
    assert not report.all_valid
    assert "verbatim" in report.claim_checks[0].citation_checks[0].reason


def test_quote_matches_with_whitespace_normalization():
    text_with_newlines = "AXN-2401 reduced\nhydroxyproline   content by 58%."
    registry = {"LIT-004#0": _hit("LIT-004#0", "LIT-004", text=text_with_newlines)}
    citation = RawCitation(
        source_id="LIT-004", chunk_id="LIT-004#0", quote="reduced hydroxyproline content by 58%"
    )
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("c1", [citation])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        registry,
    )
    assert report.all_valid


def test_claim_with_no_citations_is_invalid():
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("c1", [])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        {},
    )
    assert not report.all_valid
    assert report.claim_checks[0].valid is False


def test_mixed_valid_and_invalid_claims_report_correctly():
    registry = {"LIT-004#0": _hit("LIT-004#0", "LIT-004", text=CHUNK_TEXT)}
    good_citation = RawCitation(source_id="LIT-004", chunk_id="LIT-004#0", quote="hydroxyproline content by 58%")
    bad_citation = RawCitation(source_id="LIT-999", chunk_id="LIT-999#0", quote="anything")
    report = grounding.validate_answer(
        RawAnswer(
            claims=[_claim("good", [good_citation]), _claim("bad", [bad_citation])],
            gaps_or_caveats=[],
            refused=False,
            refusal_reason=None,
            escalate_for_human_review=False,
        ),
        registry,
    )
    assert not report.all_valid
    assert report.valid_claim_ids == {"good"}
    assert {c.claim_id for c in report.invalid_claims} == {"bad"}
