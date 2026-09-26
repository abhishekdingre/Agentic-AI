"""Paragraph-aware sliding-window chunking.

Splits document text on paragraph boundaries (blank lines) and packs
paragraphs into chunks up to `CHUNK_MAX_CHARS`, carrying roughly
`CHUNK_OVERLAP_CHARS` of trailing text from one chunk into the start of the
next so meaning isn't lost at a chunk boundary. A single paragraph longer
than `CHUNK_MAX_CHARS` is itself split with a sliding window + overlap.

This is intentionally simple (no NLP sentence segmentation, no reranking) —
see AIDLC_PLAN.md's "explicitly deferred" list for the RAG engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.config import CHUNK_MAX_CHARS, CHUNK_OVERLAP_CHARS

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    index: int
    text: str


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text)]
    return [p for p in paragraphs if p]


def _sliding_window_split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split a single long string into overlapping windows of <= max_chars."""
    if len(text) <= max_chars:
        return [text]
    windows: list[str] = []
    start = 0
    step = max(1, max_chars - overlap_chars)
    while start < len(text):
        end = min(start + max_chars, len(text))
        windows.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return windows


def chunk_text(
    text: str,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[Chunk]:
    """Paragraph-aware sliding-window chunking with overlap.

    Algorithm:
    1. Split text into paragraphs on blank lines.
    2. Greedily pack paragraphs into a chunk until the next paragraph would
       push it past `max_chars`.
    3. Start the next chunk with the trailing `overlap_chars` of the
       previous chunk (so context isn't lost at the boundary), then keep
       packing paragraphs.
    4. Any single paragraph longer than `max_chars` is itself split via a
       sliding window with the same overlap.
    """
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    # Expand any oversized paragraph into pre-split pieces up front.
    pieces: list[str] = []
    for p in paragraphs:
        if len(p) > max_chars:
            pieces.extend(_sliding_window_split(p, max_chars, overlap_chars))
        else:
            pieces.append(p)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            chunks.append(current)
            # Carry overlap from the tail of the just-closed chunk.
            overlap = current[-overlap_chars:] if overlap_chars > 0 else ""
            current = f"{overlap}\n\n{piece}" if overlap else piece
    if current:
        chunks.append(current)

    return [Chunk(index=i, text=c) for i, c in enumerate(chunks)]
