"""The agentic loop: a hand-written loop over `client.messages.create()` (not
a framework/tool_runner), enforcing all four hard commitments end to end.

- Commitment #1 (allowlist): the `search_corpus` tool's domain enum is built
  fresh per-session from the caller's selected domains (`tools.py`); the
  vector store itself only ever contains `current` documents (`ingest.py`);
  and every citation is re-checked against `allowlist.approved_current_ids()`
  at grounding time (`grounding.py`) — three independent layers.
- Commitment #2 (grounding): every citation in a submitted RawAnswer is
  checked deterministically against this session's retrieval registry
  (`grounding.validate_answer`) — never an LLM judge, never the model's own
  say-so.
- Commitment #3 (fixed schema): the model's only way to respond is the
  `submit_structured_answer` tool; the server never accepts or returns free
  text as the answer. The FinalAnswer returned to the caller is rebuilt
  server-side (title/domain/version/similarity/confidence are all looked up
  fresh, never copied from the model).
- Commitment #4 (fail gracefully): grounding failures trigger a bounded
  number of retries with specific feedback; if still unresolved, invalid
  claims are dropped into gaps_or_caveats and escalate_for_human_review is
  set, rather than ever fabricating a citation. Full refusal only happens if
  nothing in the answer can be grounded at all.
"""
from __future__ import annotations

import json
import logging
import time
from functools import lru_cache

import anthropic

from backend import allowlist, confidence, config, grounding, telemetry
from backend.embeddings import embed_texts
from backend.schemas import FinalAnswer, FinalCitation, FinalClaim, RawAnswer, RetrievalLogEntry
from backend.tools import build_search_corpus_tool, build_submit_structured_answer_tool
from backend.vector_store import Hit, VectorStore, get_default_store

logger = logging.getLogger(__name__)

RESPONSE_MAX_TOKENS = 8192


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(**config.anthropic_client_kwargs())


def _normalize_domains(domains: list[str] | None) -> list[str]:
    if not domains:
        return list(config.ALL_DOMAINS)
    normalized = [d for d in dict.fromkeys(domains) if d in config.ALL_DOMAINS]
    return normalized or list(config.ALL_DOMAINS)


def _build_system_prompt(domains: list[str]) -> str:
    domains_list = ", ".join(domains)
    return f"""You are an evidence-grounded literature-review assistant for a pharmaceutical drug-discovery program (compound AXN-2401, target CRB3, indication PF-7).

Ground rules, mechanically enforced by the server around you — follow them exactly:

1. CORPUS-ONLY. Answer using ONLY evidence you retrieve via the search_corpus tool during this conversation. Never use outside/background knowledge about pharmacology, this compound, or anything else, even if you believe it to be true. If the corpus doesn't support a statement, don't make the statement.

2. SEARCH ITERATIVELY. Decompose the user's question into the sub-questions needed to answer it thoroughly (e.g. target biology, chemistry/PK, nonclinical safety, clinical safety, clinical efficacy, regulatory/IP context — only as relevant) and call search_corpus once per sub-question, across as many of these domains as are relevant: {domains_list}. These are the ONLY domains available to you this session; you cannot search outside them. Call search_corpus as many times as you need before answering.

3. VERBATIM-QUOTE-BACKED CLAIMS ONLY. Every claim you submit must cite one or more chunk_ids that search_corpus actually returned earlier in this conversation, with a quote that is a verbatim excerpt of that chunk's text. Do not paraphrase into the quote field and do not invent a chunk_id or source_id. For each citation's source_id field, copy the exact "source_id" value search_corpus returned for that chunk_id (e.g. "CT-002") — it is NOT the domain name (e.g. never put "clinical_trials", "literature", "patents", or "internal_reports" in source_id). Citations are checked mechanically server-side against what was actually retrieved — fabricated or mismatched citations will be rejected and you will be asked to retry with specific feedback.

4. CONSOLIDATE CORROBORATING EVIDENCE. Each claim's confidence is computed from how many independent sources and domains back *that specific claim* — a claim with only one citation cannot score above Low, no matter how strong that one source is. So when multiple retrieved chunks support the same underlying statement, cite all of them together on ONE claim rather than splitting them into several single-citation claims. Do not, however, merge claims just to inflate confidence: only combine citations that genuinely support the same statement, and never merge across a genuine disagreement — that still belongs under rule 5, as separate claims or a gaps_or_caveats entry.

5. SURFACE DISAGREEMENT. If different sources conflict (e.g. an external finding vs. an internal review reaching a different conclusion), do not silently pick one side or average them — state the disagreement explicitly as its own claim (or in gaps_or_caveats) and cite both sides.

6. FAIL GRACEFULLY, NEVER FABRICATE. If, after searching, the evidence for some or all of the question is thin, absent, or contradictory, say so plainly — do not fill the gap with plausible-sounding invented content. Use gaps_or_caveats to name what's missing or uncertain. If there is truly no relevant evidence for the question (e.g. it asks about an indication or claim this program does not address), set refused=true with a clear refusal_reason instead of forcing an answer. Set escalate_for_human_review=true whenever you are refusing, or whenever the evidence only partially resolves the question.

7. STRUCTURED OUTPUT ONLY. You must never reply with plain text. The ONLY way to deliver your answer is calling submit_structured_answer exactly once, when you are done searching. If a submission is rejected for a grounding failure, fix exactly the claims/citations named in the feedback and call submit_structured_answer again.

Domains available this session: {domains_list}."""


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def _content_blocks(message: dict) -> list:
    """Normalize a message's `content` into a list of blocks, in place.

    Anthropic accepts a plain string as shorthand for a single text block;
    this converts that shorthand into the equivalent explicit list form
    (semantically identical, same text) so a `cache_control` marker can be
    attached to "the last content block" uniformly, regardless of whether
    the message content started life as a bare string (the initial user
    question), our own `tool_result` dicts, or the SDK's own response
    content-block objects (`response.content`, appended verbatim as the
    assistant turn).
    """
    content = message["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
        message["content"] = content
    return content


def _set_cache_breakpoint(messages: list[dict], index: int) -> None:
    """Mark the last content block of `messages[index]` as an ephemeral
    prompt-cache breakpoint."""
    blocks = _content_blocks(messages[index])
    if not blocks:
        return
    last = blocks[-1]
    if isinstance(last, dict):
        last["cache_control"] = {"type": "ephemeral"}
    else:
        # SDK response content-block objects (e.g. TextBlock/ToolUseBlock):
        # pydantic models with extra="allow", so a plain attribute set/del
        # round-trips through model_dump() correctly.
        last.cache_control = {"type": "ephemeral"}


def _clear_cache_breakpoint(messages: list[dict], index: int) -> None:
    """Undo `_set_cache_breakpoint` on an earlier message, so the rolling
    breakpoint never accumulates past one active marker on the messages
    list (on top of the system prompt's and the tools list's own
    breakpoints — 3 total, under Anthropic's 4-breakpoint limit)."""
    if index >= len(messages):
        return
    blocks = _content_blocks(messages[index])
    if not blocks:
        return
    last = blocks[-1]
    if isinstance(last, dict):
        last.pop("cache_control", None)
    elif hasattr(last, "cache_control"):
        del last.cache_control


def _handle_search_corpus(
    block,
    session_domains: list[str],
    store: VectorStore,
    registry: dict[str, Hit],
    retrieval_log: list[RetrievalLogEntry],
    iteration: int,
    query_embedding: list[float],
) -> dict:
    query = (block.input or {}).get("query", "")
    requested_domains = (block.input or {}).get("domains") or []
    # Defensive re-restriction: even though the tool schema's enum already
    # limits `domains` to this session's selection, never trust client input
    # for the actual query execution either.
    restricted_domains = [d for d in requested_domains if d in session_domains] or session_domains

    with telemetry.start_tool_span("search_corpus") as span:
        telemetry.annotate_span(span, domains=",".join(restricted_domains))
        try:
            hits = store.query(query_embedding, restricted_domains, config.TOP_K)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("search_corpus execution failed")
            telemetry.annotate_span(span, error=str(exc))
            retrieval_log.append(
                RetrievalLogEntry(
                    iteration=iteration,
                    tool="search_corpus",
                    args={"query": query, "domains": restricted_domains},
                    result_summary=f"error: {exc}",
                )
            )
            return _tool_result(block.id, f"search_corpus failed: {exc}", is_error=True)

        telemetry.annotate_span(span, retrieved_chunk_count=len(hits))

        for hit in hits:
            registry[hit.chunk_id] = hit

        payload = [
            {
                "chunk_id": h.chunk_id,
                "source_id": h.source_id,
                "domain": h.domain,
                "similarity": round(h.similarity, 3),
                "text": h.text,
            }
            for h in hits
        ]
        retrieval_log.append(
            RetrievalLogEntry(
                iteration=iteration,
                tool="search_corpus",
                args={"query": query, "domains": restricted_domains},
                result_summary=(
                    f"{len(hits)} chunk(s) returned: {[h.chunk_id for h in hits]}"
                    if hits
                    else "0 chunks returned"
                ),
            )
        )
        return _tool_result(block.id, json.dumps(payload))


def _degrade(raw_answer: RawAnswer, report: grounding.GroundingReport) -> RawAnswer:
    """Graceful degradation after grounding retries are exhausted: keep only
    claims that validated, move the rest into gaps_or_caveats with specific
    reasons, and force escalation. Full refusal only if nothing survives."""
    valid_ids = report.valid_claim_ids
    kept_claims = [c for c in raw_answer.claims if c.claim_id in valid_ids]

    new_gaps = list(raw_answer.gaps_or_caveats)
    for claim_check in report.invalid_claims:
        reasons = "; ".join(claim_check.failure_reasons)
        new_gaps.append(
            f"Claim {claim_check.claim_id!r} could not be verified against "
            f"retrieved evidence after repeated grounding failures and was "
            f"dropped ({reasons})."
        )

    refused = raw_answer.refused or not kept_claims
    refusal_reason = raw_answer.refusal_reason
    if refused and not refusal_reason:
        refusal_reason = (
            "No claim could be grounded against verifiably retrieved evidence "
            "after repeated attempts."
        )

    return RawAnswer(
        claims=kept_claims,
        gaps_or_caveats=new_gaps,
        refused=refused,
        refusal_reason=refusal_reason,
        escalate_for_human_review=True,
    )


def _build_final_answer(
    raw_answer: RawAnswer,
    report: grounding.GroundingReport,
    registry: dict[str, Hit],
    retrieval_log: list[RetrievalLogEntry],
) -> FinalAnswer:
    valid_ids = report.valid_claim_ids
    final_claims: list[FinalClaim] = []

    for claim in raw_answer.claims:
        if claim.claim_id not in valid_ids:
            continue
        hits_for_claim = [registry[c.chunk_id] for c in claim.citations]
        _, bucket = confidence.score_claim(hits_for_claim)

        final_citations: list[FinalCitation] = []
        for citation in claim.citations:
            hit = registry[citation.chunk_id]
            entry = allowlist.by_source_id(hit.source_id)
            final_citations.append(
                FinalCitation(
                    source_id=hit.source_id,
                    chunk_id=hit.chunk_id,
                    quote=citation.quote,
                    title=entry.title if entry else hit.source_id,
                    domain=entry.domain if entry else hit.domain,
                    version=entry.version if entry else "unknown",
                    similarity=round(hit.similarity, 4),
                )
            )

        final_claims.append(
            FinalClaim(
                claim_id=claim.claim_id,
                statement=claim.statement,
                citations=final_citations,
                confidence=bucket,
            )
        )

    refused = raw_answer.refused or not final_claims
    refusal_reason = raw_answer.refusal_reason
    if refused and not refusal_reason:
        refusal_reason = (
            "No claim in the submitted answer could be grounded against "
            "verifiably retrieved, currently-approved evidence."
        )

    overall = confidence.overall_confidence(
        [c.confidence for c in final_claims], raw_answer.gaps_or_caveats
    )

    return FinalAnswer(
        claims=final_claims,
        gaps_or_caveats=list(raw_answer.gaps_or_caveats),
        refused=refused,
        refusal_reason=refusal_reason,
        escalate_for_human_review=raw_answer.escalate_for_human_review or refused,
        overall_confidence=overall,
        retrieval_log=list(retrieval_log),
    )


def _budget_exhausted_answer(retrieval_log: list[RetrievalLogEntry]) -> FinalAnswer:
    return FinalAnswer(
        claims=[],
        gaps_or_caveats=[
            "The agent did not produce a valid, grounded structured answer "
            "within the iteration budget."
        ],
        refused=True,
        refusal_reason="Iteration budget exhausted before a grounded structured answer could be produced.",
        escalate_for_human_review=True,
        overall_confidence="Insufficient",
        retrieval_log=list(retrieval_log),
    )


def _run_query_impl(
    question: str, session_domains: list[str]
) -> tuple[FinalAnswer, int, int]:
    """Runs the agentic loop; returns (final_answer, grounding_retries_used,
    retrieved_chunk_count) so the `run_query` wrapper can record telemetry."""
    client = _client()
    # `build_submit_structured_answer_tool()` is built second, so its
    # returned dict (carrying its own `cache_control` breakpoint, see
    # tools.py) is the last item in this list — combined with the system
    # prompt's breakpoint below, this makes the entire system+tools prefix
    # (byte-for-byte identical across every iteration of this query, and
    # across queries sharing this domain selection) one stable, cacheable
    # unit (PERF_REVIEW_BACKEND.md P1.1).
    tools = [
        build_search_corpus_tool(session_domains),
        build_submit_structured_answer_tool(),
    ]
    system_prompt = _build_system_prompt(session_domains)
    system_blocks = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    store = get_default_store()

    messages: list[dict] = [{"role": "user", "content": question}]
    registry: dict[str, Hit] = {}
    retrieval_log: list[RetrievalLogEntry] = []
    grounding_retries_used = 0
    # Rolling prompt-cache breakpoint on the growing `messages` list: tracks
    # which message index currently carries the ephemeral marker, so each
    # iteration can clear the previous one before setting a new one further
    # along and never exceed Anthropic's 4-breakpoint limit (system + tools
    # + this one rolling breakpoint = 3, used below).
    cache_breakpoint_index: int | None = None

    raw_answer: RawAnswer | None = None
    final_report: grounding.GroundingReport | None = None

    # The turn budget must guarantee the FULL grounding-retry allowance is
    # still available even in the worst case where the model spends every
    # one of MAX_ITERATIONS turns searching before its first submission
    # attempt (this is realistic: rule #2 explicitly encourages decomposing
    # into several sub-questions and searching per domain). Without this
    # headroom, a first submission on the very last search turn that fails
    # grounding gets its retry feedback sent but the loop exits before the
    # model can act on it — silently downgrading a fixable grounding retry
    # into a full budget-exhausted refusal with zero claims. Extending the
    # cap by MAX_GROUNDING_RETRIES turns removes that starvation case while
    # keeping the loop strictly bounded (no risk of running forever).
    max_turns = config.MAX_ITERATIONS + config.MAX_GROUNDING_RETRIES
    iteration = 0
    while iteration < max_turns:
        iteration += 1
        with telemetry.start_iteration_span(iteration):
            if len(messages) >= 2:
                new_breakpoint_index = len(messages) - 2
                if cache_breakpoint_index is not None:
                    _clear_cache_breakpoint(messages, cache_breakpoint_index)
                _set_cache_breakpoint(messages, new_breakpoint_index)
                cache_breakpoint_index = new_breakpoint_index

            response = client.messages.create(
                model=config.MODEL_ID,
                max_tokens=RESPONSE_MAX_TOKENS,
                system=system_blocks,
                messages=messages,
                tools=tools,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                # Server needs another turn to keep generating; nothing
                # client-side to execute yet.
                continue

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                # Model didn't call a tool at all (e.g. plain text, or
                # truncated by max_tokens). This violates commitment #3;
                # stop and degrade.
                logger.warning(
                    "Model turn produced no tool_use block (stop_reason=%s); "
                    "stopping loop.",
                    response.stop_reason,
                )
                break

            logger.info(
                "iteration %d: stop_reason=%s tools_called=%s",
                iteration,
                response.stop_reason,
                [b.name for b in tool_use_blocks],
            )

            tool_results: list[dict] = []
            final_ready = False

            # Batch this turn's search_corpus queries through one
            # embed_texts([...]) call instead of one embed_query per block
            # (PERF_REVIEW_BACKEND.md P2.1). Each block still gets its own
            # domain restriction, registry/citation dedup, and
            # retrieval-log entry below — only the embedding step itself is
            # batched. On a batch-embedding failure (the same underlying
            # model would fail identically for every block anyway, since
            # it's one singleton), synthesize the same per-block error
            # tool_result + retrieval-log entry each block would have
            # produced individually, rather than letting one exception take
            # down the whole turn silently.
            search_blocks = [b for b in tool_use_blocks if b.name == "search_corpus"]
            query_embeddings: dict[str, list[float]] = {}
            search_errors: dict[str, dict] = {}
            if search_blocks:
                queries = [(b.input or {}).get("query", "") for b in search_blocks]
                try:
                    embeddings = embed_texts(queries)
                    query_embeddings = dict(zip((b.id for b in search_blocks), embeddings))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("Batched embedding of search_corpus queries failed")
                    for b, q in zip(search_blocks, queries):
                        requested_domains = (b.input or {}).get("domains") or []
                        restricted_domains = (
                            [d for d in requested_domains if d in session_domains]
                            or session_domains
                        )
                        retrieval_log.append(
                            RetrievalLogEntry(
                                iteration=iteration,
                                tool="search_corpus",
                                args={"query": q, "domains": restricted_domains},
                                result_summary=f"error: {exc}",
                            )
                        )
                        search_errors[b.id] = _tool_result(
                            b.id, f"search_corpus failed: {exc}", is_error=True
                        )

            for block in tool_use_blocks:
                if block.name == "search_corpus":
                    if block.id in search_errors:
                        tool_results.append(search_errors[block.id])
                    else:
                        tool_results.append(
                            _handle_search_corpus(
                                block,
                                session_domains,
                                store,
                                registry,
                                retrieval_log,
                                iteration,
                                query_embeddings[block.id],
                            )
                        )

                elif block.name == "submit_structured_answer":
                    with telemetry.start_tool_span("submit_structured_answer") as submit_span:
                        try:
                            candidate = RawAnswer.model_validate(block.input)
                        except Exception as exc:
                            telemetry.annotate_span(submit_span, validation_error=str(exc))
                            tool_results.append(
                                _tool_result(
                                    block.id,
                                    f"submit_structured_answer input failed validation: {exc}",
                                    is_error=True,
                                )
                            )
                            continue

                        report = grounding.validate_answer(candidate, registry)
                        telemetry.annotate_span(
                            submit_span,
                            grounding_passed=report.all_valid,
                            retry_count=grounding_retries_used,
                        )
                        logger.info(
                            "submit_structured_answer: %d claim(s) submitted, all_valid=%s%s",
                            len(candidate.claims),
                            report.all_valid,
                            "" if report.all_valid else f" feedback={report.feedback_text()!r}",
                        )

                        if report.all_valid:
                            raw_answer = candidate
                            final_report = report
                            final_ready = True
                            tool_results.append(_tool_result(block.id, "Accepted."))
                        elif grounding_retries_used < config.MAX_GROUNDING_RETRIES:
                            grounding_retries_used += 1
                            tool_results.append(
                                _tool_result(block.id, report.feedback_text(), is_error=True)
                            )
                        else:
                            degraded = _degrade(candidate, report)
                            raw_answer = degraded
                            final_report = grounding.validate_answer(degraded, registry)
                            final_ready = True
                            tool_results.append(
                                _tool_result(
                                    block.id,
                                    "Grounding retries exhausted; answer accepted with "
                                    "invalid claims dropped into gaps_or_caveats.",
                                )
                            )

                else:  # pragma: no cover - defensive, only 2 tools are registered
                    tool_results.append(
                        _tool_result(block.id, f"Unknown tool: {block.name}", is_error=True)
                    )

            if final_ready:
                break

            messages.append({"role": "user", "content": tool_results})

    if raw_answer is None or final_report is None:
        return _budget_exhausted_answer(retrieval_log), grounding_retries_used, len(registry)

    final_answer = _build_final_answer(raw_answer, final_report, registry, retrieval_log)
    return final_answer, grounding_retries_used, len(registry)


def run_query(question: str, domains: list[str] | None = None) -> FinalAnswer:
    """Run the full agentic loop for one question and return a FinalAnswer.

    This is the single entry point `backend/main.py` and `backend/mcp_server.py`
    both call into. Wraps `_run_query_impl` with a top-level trace span and
    records the request-latency/refusal/count metrics exactly once per call.
    """
    session_domains = _normalize_domains(domains)
    start = time.perf_counter()

    with telemetry.start_query_span(question, session_domains):
        final_answer, grounding_retries_used, chunk_count = _run_query_impl(
            question, session_domains
        )

    telemetry.record_query_result(
        duration_seconds=time.perf_counter() - start,
        refused=final_answer.refused,
        overall_confidence=final_answer.overall_confidence,
        retry_count=grounding_retries_used,
        retrieved_chunk_count=chunk_count,
    )
    return final_answer
