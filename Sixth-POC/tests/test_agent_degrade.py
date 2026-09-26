"""Integration test for backend/agent.py's retry -> degrade path
(commitment #4: fail gracefully rather than fabricate, specifically for the
case where the model *does* submit a citation that fails grounding and does
not self-correct on retry).

Every other live/unit test in this suite exercises the "model behaves"
path; `_degrade()` (backend/agent.py) had never been directly tested or
observed firing (see docs/TRIAGE_REPORT.md finding 4.3). This test drives
the REAL `_run_query_impl` loop end-to-end — real grounding validation,
real retry/degrade logic — while replacing only the genuinely-external
dependencies with test doubles, so it needs no network access, no
Anthropic credentials, and no live corpus/embedding model:

- `backend.agent._client` is monkeypatched to a fake client whose
  `.messages.create()` returns a fixed sequence of canned responses: one
  `search_corpus` call, then three `submit_structured_answer` attempts that
  never fix the one bad citation.
- `backend.agent.get_default_store` / `backend.agent.embed_texts` are
  monkeypatched so that one `search_corpus` call populates the session's
  retrieval registry with a real, currently-approved chunk (`LIT-004#0`,
  the same fixture `test_grounding.py` uses) without touching chromadb or
  sentence-transformers.

The canned submission has two claims: one citing the real retrieved chunk
(should ground and survive every attempt) and one citing a chunk_id that
was never retrieved this session (fabricated — fails grounding on every
attempt, since the mock never "fixes" it). This should exhaust
MAX_GROUNDING_RETRIES and force `_degrade()`: the good claim is kept, the
bad claim is dropped into `gaps_or_caveats` with a specific reason, and
`escalate_for_human_review` is forced true — never a fabricated citation,
and (since one claim did ground) never a bare full refusal either.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from backend import agent, config
from backend.vector_store import Hit

GOOD_CHUNK_TEXT = (
    "AXN-2401 reduced hydroxyproline content by 58% at 30 mg/kg in the bleomycin model."
)
GOOD_HIT = Hit(
    chunk_id="LIT-004#0",
    source_id="LIT-004",
    domain="literature",
    text=GOOD_CHUNK_TEXT,
    similarity=0.9,
    distance=0.1,
)


def _tool_use_block(name: str, input_: dict, block_id: str) -> SimpleNamespace:
    """Minimal stand-in for anthropic's ToolUseBlock: agent.py only ever
    reads .type/.name/.input/.id via attribute access."""
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=block_id)


def _response(blocks: list[SimpleNamespace]) -> SimpleNamespace:
    """Minimal stand-in for anthropic's Message: agent.py only ever reads
    .content/.stop_reason via attribute access."""
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


def _bad_submission_input() -> dict:
    """One groundable claim (real chunk, real quote) + one claim citing a
    chunk_id that was never returned by search_corpus this session. The
    second claim must fail grounding every single time this exact dict is
    resubmitted, since nothing about it ever changes between retries."""
    return {
        "claims": [
            {
                "claim_id": "c_good",
                "statement": "AXN-2401 reduced hydroxyproline content by 58% at 30 mg/kg.",
                "citations": [
                    {
                        "source_id": "LIT-004",
                        "chunk_id": "LIT-004#0",
                        "quote": "reduced hydroxyproline content by 58%",
                    }
                ],
            },
            {
                "claim_id": "c_bad",
                "statement": "AXN-2401 outperformed standard of care in a head-to-head trial.",
                "citations": [
                    {
                        "source_id": "LIT-999",
                        "chunk_id": "LIT-999#0",
                        "quote": "a quote that was never actually retrieved this session",
                    }
                ],
            },
        ],
        "gaps_or_caveats": [],
        "refused": False,
        "refusal_reason": None,
        "escalate_for_human_review": False,
    }


def test_grounding_failure_retries_then_degrades_without_fabricating(monkeypatch):
    fake_store = Mock()
    fake_store.query = Mock(return_value=[GOOD_HIT])
    monkeypatch.setattr(agent, "get_default_store", lambda: fake_store)
    monkeypatch.setattr(agent, "embed_texts", lambda queries: [[0.0] * 384 for _ in queries])

    search_turn = _response(
        [_tool_use_block("search_corpus", {"query": "hydroxyproline efficacy", "domains": ["literature"]}, "toolu_search")]
    )
    # The SAME bad submission is returned on all three attempts: the mock
    # never self-corrects, so the retry budget must genuinely exhaust.
    submit_turn = _response(
        [_tool_use_block("submit_structured_answer", _bad_submission_input(), "toolu_submit")]
    )

    fake_client = Mock()
    fake_client.messages.create = Mock(
        side_effect=[search_turn, submit_turn, submit_turn, submit_turn]
    )
    monkeypatch.setattr(agent, "_client", lambda: fake_client)

    final_answer, grounding_retries_used, chunk_count = agent._run_query_impl(
        "Does AXN-2401 reduce hydroxyproline, and does it outperform standard of care?",
        ["literature"],
    )

    # The retry mechanism actually engaged (0 -> 1 -> 2) before exhausting
    # MAX_GROUNDING_RETRIES, and the loop made exactly 1 search + 3 submit
    # attempts — not fewer (which would mean no retry happened) and not
    # more (which would mean it kept retrying past the configured budget).
    assert grounding_retries_used == config.MAX_GROUNDING_RETRIES
    assert fake_client.messages.create.call_count == 4
    assert chunk_count == 1

    # Degrade, not a full refusal or a fabricated answer: the one claim
    # that actually grounded survives untouched.
    assert final_answer.refused is False
    assert len(final_answer.claims) == 1
    assert final_answer.claims[0].claim_id == "c_good"
    assert final_answer.claims[0].citations[0].source_id == "LIT-004"

    # The ungrounded claim was dropped into gaps_or_caveats with a specific,
    # traceable reason — never silently discarded, never smuggled into the
    # answer as a real claim.
    gap_text = " ".join(final_answer.gaps_or_caveats)
    assert "c_bad" in gap_text
    assert "LIT-999" in gap_text or "not returned" in gap_text

    # Escalation is mandatory whenever degrade fires.
    assert final_answer.escalate_for_human_review is True
