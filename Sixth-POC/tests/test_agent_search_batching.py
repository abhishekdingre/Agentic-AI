"""Unit test for backend/agent.py's per-turn search_corpus batching
(PERF_REVIEW_BACKEND.md P2.1): when a single model turn issues multiple
search_corpus tool calls, their queries must be batched through ONE
embed_texts([...]) call rather than one embed_query call per block, while
each block still gets its own domain restriction, registry entry, and
retrieval-log entry (no behavior change beyond the embedding call itself).

Follows the same test-double pattern as test_agent_degrade.py: only the
genuinely-external dependencies (the Anthropic client, the vector store,
the embedding step) are monkeypatched, so this drives the real
`_run_query_impl` loop with no network access and no real model load.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from backend import agent
from backend.vector_store import Hit

HIT_LIT = Hit(
    chunk_id="LIT-004#0",
    source_id="LIT-004",
    domain="literature",
    text="AXN-2401 reduced hydroxyproline content by 58% at 30 mg/kg.",
    similarity=0.9,
    distance=0.1,
)
HIT_CT = Hit(
    chunk_id="CT-002#0",
    source_id="CT-002",
    domain="clinical_trials",
    text="Phase 1b study of AXN-2401 tracked CRB3 pathway biomarkers.",
    similarity=0.8,
    distance=0.2,
)


def _tool_use_block(name: str, input_: dict, block_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=block_id)


def _response(blocks: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


def test_multiple_search_blocks_in_one_turn_batch_through_one_embed_call(monkeypatch):
    fake_store = Mock()

    def fake_query(embedding, domains, top_k):
        if domains == ["literature"]:
            return [HIT_LIT]
        if domains == ["clinical_trials"]:
            return [HIT_CT]
        return []

    fake_store.query = Mock(side_effect=fake_query)
    monkeypatch.setattr(agent, "get_default_store", lambda: fake_store)

    embed_calls: list[list[str]] = []

    def fake_embed_texts(queries: list[str]) -> list[list[float]]:
        embed_calls.append(list(queries))
        return [[float(i)] * 384 for i in range(len(queries))]

    monkeypatch.setattr(agent, "embed_texts", fake_embed_texts)

    search_turn = _response(
        [
            _tool_use_block(
                "search_corpus",
                {"query": "target biology", "domains": ["literature"]},
                "toolu_1",
            ),
            _tool_use_block(
                "search_corpus",
                {"query": "safety data", "domains": ["clinical_trials"]},
                "toolu_2",
            ),
        ]
    )

    submit_input = {
        "claims": [
            {
                "claim_id": "c1",
                "statement": "AXN-2401 has supporting nonclinical and clinical evidence.",
                "citations": [
                    {
                        "source_id": "LIT-004",
                        "chunk_id": "LIT-004#0",
                        "quote": "reduced hydroxyproline content by 58%",
                    },
                    {
                        "source_id": "CT-002",
                        "chunk_id": "CT-002#0",
                        "quote": "tracked CRB3 pathway biomarkers",
                    },
                ],
            }
        ],
        "gaps_or_caveats": [],
        "refused": False,
        "refusal_reason": None,
        "escalate_for_human_review": False,
    }
    submit_turn = _response(
        [_tool_use_block("submit_structured_answer", submit_input, "toolu_submit")]
    )

    fake_client = Mock()
    fake_client.messages.create = Mock(side_effect=[search_turn, submit_turn])
    monkeypatch.setattr(agent, "_client", lambda: fake_client)

    final_answer, grounding_retries_used, chunk_count = agent._run_query_impl(
        "What evidence supports AXN-2401?",
        ["literature", "clinical_trials"],
    )

    # Exactly one batched embed_texts call covering both queries together —
    # not one embed_texts([...]) call per search_corpus block.
    assert embed_calls == [["target biology", "safety data"]]

    # Each block still queried its own (correctly restricted) domain and
    # ended up in the registry / retrieval log independently.
    assert fake_store.query.call_count == 2
    assert chunk_count == 2

    assert grounding_retries_used == 0
    assert final_answer.refused is False
    assert len(final_answer.claims) == 1
    assert {c.source_id for c in final_answer.claims[0].citations} == {"LIT-004", "CT-002"}
    assert len(final_answer.retrieval_log) == 2
