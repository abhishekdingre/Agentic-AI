"""Builds the two Anthropic `strict: true` tool definitions used by the
agentic loop (`backend/agent.py`).

Strict tool schemas (confirmed against current Anthropic docs, see the
claude-api skill): `strict: true` is a top-level field on the tool
definition (alongside `name`/`description`/`input_schema`), the schema must
have `additionalProperties: false` on every object, `required` should list
every property that doesn't have a default, and `$ref`/`$defs` ARE
supported (no need to inline nested Pydantic models). Numeric
`minimum`/`maximum` and string length constraints are NOT supported, which
is why `search_corpus` has no `max_results` parameter — top-k is hardcoded
server-side (`backend.config.TOP_K`) instead.

`RawAnswer` already has `ConfigDict(extra="forbid")` throughout
(`backend/schemas.py`), so `model_json_schema()` already emits
`additionalProperties: false` everywhere; the only cleanup needed is
stripping the `title` keys Pydantic adds (Anthropic strict schemas reject
them).
"""
from __future__ import annotations

from functools import lru_cache

from backend.schemas import RawAnswer

SEARCH_CORPUS_DESCRIPTION = (
    "Search the approved evidence corpus (literature, patents, clinical trial "
    "reports, and/or internal reports) for chunks semantically relevant to a "
    "query. Only documents in the current, approved corpus are indexed — "
    "superseded document versions are never returned. Call this iteratively, "
    "decomposing the user's question into sub-questions as needed (e.g. "
    "target validation, safety, efficacy, prior art). Results are restricted "
    "to the domains the user selected for this session; you cannot search "
    "outside them. Returns up to a handful of the most relevant chunks, each "
    "with a chunk_id you must use verbatim if you cite it later."
)

SUBMIT_STRUCTURED_ANSWER_DESCRIPTION = (
    "Submit your final structured answer. This is the ONLY way to answer the "
    "user — you must never reply with plain text. Call this exactly once you "
    "have gathered enough evidence (or determined the evidence is thin, "
    "absent, or contradictory). Every claim's citations must reference a "
    "chunk_id that was actually returned by a search_corpus call earlier in "
    "this same conversation, and every quote must be a verbatim substring of "
    "that chunk's text — fabricated or paraphrased quotes will be rejected "
    "and you will be asked to retry. If you cannot ground a claim this way, "
    "do not include it — move the gap into gaps_or_caveats instead, or set "
    "refused=true with a refusal_reason if there isn't enough evidence to "
    "answer at all."
)


def _strip_titles(schema):
    """Recursively remove Pydantic-generated `title` keys from a JSON schema."""
    if isinstance(schema, dict):
        return {
            key: _strip_titles(value)
            for key, value in schema.items()
            if key != "title"
        }
    if isinstance(schema, list):
        return [_strip_titles(item) for item in schema]
    return schema


def build_search_corpus_tool(selected_domains: list[str]) -> dict:
    """Build the search_corpus tool with the `domains` enum restricted, per
    request, to exactly the domains selected for this session — this is
    query-time allowlist enforcement (layer 2 of commitment #1). The enum is
    NOT hardcoded to all 4 domains.
    """
    if not selected_domains:
        raise ValueError("selected_domains must be non-empty")

    return {
        "name": "search_corpus",
        "description": SEARCH_CORPUS_DESCRIPTION,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A focused natural-language search query (sub-question), not the entire original question.",
                },
                "domains": {
                    "type": "array",
                    "description": "Which domain(s) to search within this call. Must be a subset of the domains selected for this session.",
                    "items": {
                        "type": "string",
                        "enum": list(selected_domains),
                    },
                },
            },
            "required": ["query", "domains"],
            "additionalProperties": False,
        },
    }


@lru_cache(maxsize=1)
def build_submit_structured_answer_tool() -> dict:
    """Build the submit_structured_answer tool, schema derived directly from
    `RawAnswer.model_json_schema()` — the model may only submit exactly this
    shape (commitment #3).

    Cached via `lru_cache`: this takes no arguments and `RawAnswer`'s schema
    never changes at runtime, so rebuilding it via Pydantic introspection on
    every query was pure overhead (PERF_REVIEW_BACKEND.md P3.3). This is
    also the second/last tool in `agent.py`'s `tools = [...]` list, so its
    returned dict carries a `cache_control` breakpoint: combined with the
    first tool and the system prompt's own breakpoint, this makes
    system+tools one stable, cacheable prefix across every iteration of a
    query (and across queries that share a domain selection) — see
    PERF_REVIEW_BACKEND.md P1.1.
    """
    raw_schema = RawAnswer.model_json_schema()
    stripped = _strip_titles(raw_schema)

    return {
        "name": "submit_structured_answer",
        "description": SUBMIT_STRUCTURED_ANSWER_DESCRIPTION,
        "strict": True,
        "input_schema": stripped,
        "cache_control": {"type": "ephemeral"},
    }
