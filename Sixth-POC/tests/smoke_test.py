"""End-to-end smoke test against a *running* local server
(`venv/bin/uvicorn backend.main:app --port 8000`).

This is the one test in this suite that makes a real Anthropic API call (via
the live agent loop) and therefore costs money and takes real wall-clock
time. It is deliberately self-skipping under plain `pytest tests -q` if no
server is reachable on SMOKE_TEST_BASE_URL, so the rest of the suite (which
is pure unit tests, no network, no model calls) stays fast and free.

Run directly for a real check:
    venv/bin/uvicorn backend.main:app --port 8000 &
    venv/bin/python tests/smoke_test.py
    # or: venv/bin/pytest tests/smoke_test.py -q -s
"""
from __future__ import annotations

import os
import sys

import httpx
import pytest

BASE_URL = os.environ.get("SMOKE_TEST_BASE_URL", "http://localhost:8000")
TIMEOUT = float(os.environ.get("SMOKE_TEST_TIMEOUT", "120"))

REQUIRED_FINAL_ANSWER_KEYS = {
    "claims",
    "gaps_or_caveats",
    "refused",
    "refusal_reason",
    "escalate_for_human_review",
    "overall_confidence",
    "retrieval_log",
}
VALID_CONFIDENCE_BUCKETS = {"High", "Moderate", "Low", "Insufficient"}


def _server_reachable() -> bool:
    try:
        response = httpx.get(f"{BASE_URL}/api/health", timeout=3.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _assert_final_answer_shape(data: dict) -> None:
    assert REQUIRED_FINAL_ANSWER_KEYS.issubset(data.keys()), (
        f"response missing keys: {REQUIRED_FINAL_ANSWER_KEYS - data.keys()}"
    )
    assert isinstance(data["claims"], list)
    assert isinstance(data["gaps_or_caveats"], list)
    assert isinstance(data["refused"], bool)
    assert isinstance(data["escalate_for_human_review"], bool)
    assert data["overall_confidence"] in VALID_CONFIDENCE_BUCKETS
    assert isinstance(data["retrieval_log"], list)

    for claim in data["claims"]:
        assert {"claim_id", "statement", "citations", "confidence"}.issubset(claim.keys())
        assert claim["confidence"] in VALID_CONFIDENCE_BUCKETS
        for citation in claim["citations"]:
            assert {
                "source_id",
                "chunk_id",
                "quote",
                "title",
                "domain",
                "version",
                "similarity",
            }.issubset(citation.keys())
            assert isinstance(citation["similarity"], (int, float))

    for entry in data["retrieval_log"]:
        assert {"iteration", "tool", "args", "result_summary"}.issubset(entry.keys())


def test_health_endpoint():
    if not _server_reachable():
        pytest.skip(f"no server reachable at {BASE_URL}; boot it first to run this test")
    response = httpx.get(f"{BASE_URL}/api/health", timeout=5.0)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_rich_evidence_question_returns_valid_final_answer():
    """A question the corpus richly supports (target validation for
    CRB3/AXN-2401) across multiple domains — should come back with claims,
    not a refusal."""
    if not _server_reachable():
        pytest.skip(f"no server reachable at {BASE_URL}; boot it first to run this test")

    response = httpx.post(
        f"{BASE_URL}/api/query",
        json={
            "question": (
                "What is the evidence that CRB3 is a validated target for "
                "AXN-2401 in PF-7 pulmonary fibrosis?"
            ),
            "domains": ["literature", "clinical_trials", "internal_reports"],
        },
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    _assert_final_answer_shape(data)
    assert len(data["claims"]) > 0, "expected at least one grounded claim for a richly-evidenced question"


def test_query_no_evidence_question_refuses_or_flags_gap():
    """The corpus deliberately has no renal fibrosis efficacy data (the
    program is pulmonary-only) — the agent must refuse or surface a gap,
    never fabricate an answer."""
    if not _server_reachable():
        pytest.skip(f"no server reachable at {BASE_URL}; boot it first to run this test")

    response = httpx.post(
        f"{BASE_URL}/api/query",
        json={
            "question": "What clinical efficacy data supports using AXN-2401 to treat renal fibrosis?",
            "domains": ["literature", "clinical_trials", "internal_reports", "patents"],
        },
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    _assert_final_answer_shape(data)
    assert data["refused"] is True or len(data["gaps_or_caveats"]) > 0, (
        "expected a refusal or an explicit gap for a question with no supporting evidence"
    )


if __name__ == "__main__":
    if not _server_reachable():
        print(f"No server reachable at {BASE_URL}. Start it with:")
        print("  venv/bin/uvicorn backend.main:app --port 8000")
        sys.exit(1)

    print(f"Server reachable at {BASE_URL}. Running smoke checks...")
    test_health_endpoint()
    print("  [ok] /api/health")
    test_query_rich_evidence_question_returns_valid_final_answer()
    print("  [ok] rich-evidence query returns a valid, non-empty FinalAnswer")
    test_query_no_evidence_question_refuses_or_flags_gap()
    print("  [ok] no-evidence query refuses or flags a gap (no fabrication)")
    print("All smoke checks passed.")
