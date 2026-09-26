"""Unit tests for backend/main.py's `_warmup_embedding_model` startup hook
(PERF_REVIEW_BACKEND.md P1.2). No real model load or vector store access —
`embed_query`/`get_default_store` are monkeypatched so this stays fast and
free in CI."""
from __future__ import annotations

from unittest.mock import Mock

from backend import main


def test_warmup_embeds_and_queries_when_corpus_nonempty(monkeypatch):
    fake_store = Mock()
    fake_store.count.return_value = 5
    fake_store.query = Mock(return_value=[])
    monkeypatch.setattr(main, "get_default_store", lambda: fake_store)

    embed_calls: list[str] = []

    def fake_embed_query(text: str) -> list[float]:
        embed_calls.append(text)
        return [0.0] * 384

    monkeypatch.setattr(main, "embed_query", fake_embed_query)

    main._warmup_embedding_model()

    assert embed_calls == ["warmup"]
    fake_store.count.assert_called_once()
    fake_store.query.assert_called_once()
    # The precomputed warmup embedding is what gets passed to query(), not
    # a fresh one recomputed inside the store.
    args, _kwargs = fake_store.query.call_args
    assert args[0] == [0.0] * 384


def test_warmup_skips_store_query_when_corpus_empty(monkeypatch):
    fake_store = Mock()
    fake_store.count.return_value = 0
    fake_store.query = Mock()
    monkeypatch.setattr(main, "get_default_store", lambda: fake_store)
    monkeypatch.setattr(main, "embed_query", lambda text: [0.0] * 384)

    main._warmup_embedding_model()

    fake_store.query.assert_not_called()


def test_warmup_never_raises_when_embedding_fails(monkeypatch):
    def boom(text: str) -> list[float]:
        raise RuntimeError("model load failed")

    monkeypatch.setattr(main, "embed_query", boom)

    # Must never raise, or propagate, out of the startup hook.
    main._warmup_embedding_model()


def test_warmup_never_raises_when_store_unreachable(monkeypatch):
    monkeypatch.setattr(main, "embed_query", lambda text: [0.0] * 384)

    def boom_store():
        raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(main, "get_default_store", boom_store)

    main._warmup_embedding_model()
