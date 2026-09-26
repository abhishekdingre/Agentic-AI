# Backend Performance Review

Scope: `backend/` and `data/` ingestion, **efficiency and performance only** — not correctness, not the four hard commitments (allowlist enforcement, grounding, structured output, graceful refusal), which are `p3-triage-agent`'s territory (see `docs/TRIAGE_REPORT.md`). This review does not fix or patch any code.

Method: static read of `backend/agent.py`, `confidence.py`, `grounding.py`, `allowlist.py`, `chunking.py`, `embeddings.py`, `vector_store.py`, `tools.py`, `main.py`, `telemetry.py`, `ingest.py`, `config.py`, `schemas.py`; targeted local timing of the non-model components (embedding, vector store, allowlist reload, tool-schema construction) against the already-ingested corpus (27 current docs / 99 chunks, per `docs/TRIAGE_REPORT.md`), run via `venv/bin/python -c "..."` — no live Anthropic API calls were made (no cost/time spent re-running the model or `k6`). Baseline reused from `docs/LOAD_TEST_REPORT.md`: p95 = 35.29s at up to 6 concurrent VUs, 0% error rate.

---

## P1 — meaningful latency/cost/scalability impact

### P1.1 — No prompt caching anywhere in the agentic loop; every iteration re-sends and re-bills the entire growing, mostly-identical context from scratch

**What**: `backend/agent.py`'s `_run_query_impl` calls `client.messages.create(model=..., max_tokens=8192, system=system_prompt, messages=messages, tools=tools)` once per loop iteration (up to `MAX_ITERATIONS + MAX_GROUNDING_RETRIES` = 8 turns, `agent.py:292-303`). `system_prompt` and `tools` are built once per query (`agent.py:266-270`) and are **byte-for-byte identical on every iteration of that query** (and `tools`/most of `system_prompt` are also identical across different queries with the same domain selection). `messages` grows monotonically every iteration (`agent.py:304` appends the assistant turn, `agent.py:400` appends tool results, and `_handle_search_corpus` embeds the **full retrieved chunk text** — not a summary — into each `search_corpus` tool result payload, `agent.py:124-133`). None of `client.messages.create()`'s calls anywhere in the codebase set a `cache_control` breakpoint (`grep -rn "cache_control" backend/` returns nothing).

**How found**: Static read of `agent.py:260-406`; confirmed via `grep -rn "cache_control\|ephemeral" backend/` (no matches). Measured system-prompt size directly: `_build_system_prompt(ALL_DOMAINS)` → 3,820 chars (~955 tokens estimated at ~4 chars/token). With `TOP_K=5` and `CHUNK_MAX_CHARS=700`, one `search_corpus` result payload is ≈5 chunks × up to ~700 chars + JSON field overhead ≈ 900–1,100 tokens; a realistic multi-domain question (the triage report's Q1 produced 13 claims across 4 domains, implying several `search_corpus` calls) can plausibly accumulate several thousand tokens of retrieved-chunk context by iteration 3–4, all resent verbatim, uncached, on every subsequent iteration.

**Impact**: This is an O(n²)-in-iterations cost pattern (each of the ~4-8 iterations reprocesses the entire prior context from scratch) with zero mitigation. Anthropic prompt caching (`cache_control: {"type": "ephemeral"}` on the system block, the tool definitions, and/or a breakpoint partway through the growing message history) is purpose-built for exactly this shape — repeated system prompt + tool schema across iterations of one query, and a monotonically-growing shared prefix within a query. Without it, both **latency** (uncached input tokens are fully reprocessed, not served from a fast cache path) and **cost** (uncached input tokens are billed at full price vs. a fraction for cache reads) scale with the number of iterations × accumulated context size. Given the load test's p95 is 35.29s and is (per P1.2/cross-reference below) essentially 100% model-call time, this is the single highest-leverage lever available to reduce that number without changing any behavior.

**Suggested direction**: Add `cache_control: {"type": "ephemeral"}` to the system prompt block and to the tool definitions (both are static per-query and often static across queries), and consider a cache breakpoint on the messages list itself (e.g. after each `search_corpus` tool result) so the next iteration's call only pays full price for the newly-appended turn. Anthropic's prompt caching supports up to 4 breakpoints; this loop's shape (stable prefix + append-only suffix) is closer to the canonical use case than most.

### P1.2 — Embedding model is lazily loaded on the first real request, not warmed at FastAPI startup — a ~16s cold-start tax lands on an arbitrary user, and is invisible in the recorded load-test baseline

**What**: `backend/embeddings.py:15-18`'s `get_embedder()` is an `@lru_cache(maxsize=1)` singleton — loaded once per process, which is the right pattern — but nothing calls it eagerly. `backend/main.py`'s two `@app.on_event("startup")` hooks (`_setup_observability`, `_warn_if_corpus_empty`, `main.py:128-156`) only set up OTel and check `get_default_store().count()`; neither calls `embed_query`/`embed_texts` or `store.query(...)`. So the embedding model (and the vector store's first real query, which also has its own smaller first-call cost) is loaded/warmed on whichever request happens to be the first `search_corpus` call after process start.

**How found**: Direct local timing (no API calls): `get_embedder()` cold (first call in a fresh process) took **15.888s** (SentenceTransformer weight loading). The very next call — the first real `.encode()` — took an additional **569.2ms** (first-inference backend warmup, e.g. thread-pool init), vs. a **19.2ms** warm steady-state average over 8 subsequent calls. The vector store's first `collection.query()` call took **192.9ms** vs. a **10.8ms** warm steady-state average over 8 subsequent calls. Confirmed no warmup call exists by reading `main.py:128-156` in full and `grep -rn "get_embedder\|embed_query\|embed_texts" backend/main.py` (no matches).

**Impact**: Whichever request is unlucky enough to be first after a process start/restart (or a `--reload` dev-mode restart, per `main.py:5`'s documented run command) pays roughly **16.5s extra** on top of its normal multi-second-to-tens-of-seconds agentic-loop latency — nearly doubling that one request's latency relative to the recorded p95. Because FastAPI runs the sync `query()` handler in a threadpool (`main.py:99-105`), concurrent requests arriving during that ~16s window would also serialize behind `lru_cache`'s internal lock rather than each independently loading the model, so a burst of traffic right after a cold start could stall multiple early requests, not just one. This cost is **not reflected** in `docs/LOAD_TEST_REPORT.md`'s p95: the triage report's own sequence ran `smoke_test.py` (3 real model calls) before running `k6`, so the server process was already warm by the time the load test measured anything.

**Suggested direction**: Add a startup hook (alongside the existing two in `main.py`) that calls `embed_query("warmup")` (or just `get_embedder()`) and one trivial `store.query(...)` during FastAPI startup, so the cold-load cost is paid once at deploy time rather than on a real user's first query. Cheap to add, fully eliminates the surprise latency spike, and makes the recorded load-test baseline representative of steady-state behavior from request #1.

---

## P2 — worth fixing, moderate impact

### P2.1 — N+1 embedding/query pattern when the model issues multiple `search_corpus` calls in one turn

**What**: `_handle_search_corpus` (`agent.py:86-146`) does exactly one `embed_query(query)` + one `store.query(...)` per call, and the loop that dispatches tool calls (`agent.py:333-339`) processes each `tool_use` block in `tool_use_blocks` **sequentially**, one at a time. Anthropic's API allows a single model turn to request multiple tool calls in parallel (and the system prompt explicitly encourages decomposing into sub-questions, `agent.py:64`), so a turn where the model calls `search_corpus` 2-3 times gets handled as 2-3 separate, sequential `embed_query`+`store.query()` round-trips instead of one batched `embed_texts([q1, q2, q3])` call (which `backend/embeddings.py:21-27` already supports and `ingest.py:71` already uses in bulk) followed by per-domain-group Chroma queries.

**How found**: Static read of `agent.py:86-146, 330-396`; confirmed `embed_texts` batches efficiently (measured **39.7ms for a 3-item batch** vs. the summed cost of 3 sequential single-item calls, ~3×19ms≈57ms warm — a real but modest saving at this scale).

**Impact**: At current steady-state timings (~28ms per combined embed+query call), this is a small absolute cost (tens of ms saved per multi-call turn) — not a meaningful fraction of a 35s query. It's flagged P2 rather than P3 because it's a clean, cheap structural fix (batch the embeddings for all `search_corpus` blocks in a turn before dispatching queries) and because it would matter more if `TOP_K`, chunk count, or corpus size grow, or if a future change swaps in a heavier/remote embedding model where per-call overhead is much larger than the in-process MiniLM model measured here.

**Suggested direction**: When multiple `search_corpus` tool_use blocks appear in the same turn, batch their queries through one `embed_texts([...])` call, then issue per-request Chroma queries (or a single call if domains happen to coincide) using the pre-computed embeddings, rather than one `embed_query` per block.

### P2.2 — No dedup/cache of repeated or near-duplicate `search_corpus` queries within a session

**What**: Nothing in `_handle_search_corpus` or the surrounding loop checks whether a materially similar `(query, domains)` was already searched earlier in the same session before re-embedding and re-querying. `registry[hit.chunk_id] = hit` (`agent.py:121-122`) dedupes at the *chunk* level for citation purposes, but the retrieval log and message history still record and resend the full result payload for every search call, including ones that return largely the same chunks as an earlier call.

**How found**: Static read of `agent.py:86-146`; no caching/memoization keyed on query text or embedding similarity exists anywhere in `backend/`.

**Impact**: Standalone, this is a minor compute cost (each redundant search is only ~28ms per P2.1's measurements). Its real cost is compounding: every redundant search's full JSON payload (chunk text included) becomes part of the growing, uncached message history described in P1.1, so a repeated/near-duplicate search doesn't just waste ~28ms of RAG-engine time — it also inflates the token cost of every subsequent iteration's full-context resend for the rest of that query.

**Suggested direction**: Cheap version: cache `(normalized_query, tuple(sorted(domains)))` → result payload for the duration of one session, short-circuiting an exact repeat. More useful version: track queries already issued this session, and (via the system prompt) actively discourage re-issuing a highly similar sub-question. Lower priority than P1.1's caching fix, which addresses the same underlying cost from the other direction.

---

## P3 — minor/micro-optimizations, informational, or confirmed non-issues

### P3.1 — `allowlist.clear_cache()` before every `approved_current_ids()` read: confirmed negligible (quantifies finding 1.1 from `docs/TRIAGE_REPORT.md` from the performance angle)

**What**: `backend/grounding.py:212-213`'s `validate_answer()` calls `allowlist.clear_cache()` then `allowlist.approved_current_ids()` on every invocation. `validate_answer` is called once per `submit_structured_answer` attempt (`agent.py:356`) plus once more inside the degrade path (`agent.py:382`) — up to `1 + MAX_GROUNDING_RETRIES + 1` = 4 times in the worst case for a single query.

**How found**: Direct local timing: 10 consecutive `clear_cache()` + full reload-and-revalidate cycles (parsing `data/allowlist.json`, 8,586 bytes / 27 entries, plus `load_allowlist`'s supersession-chain validation at `allowlist.py:86-104`) took **2.4ms total → 0.24ms/call**.

**Impact**: Negligible — worst case ~1ms of total added latency per query, against a 35s p95. `docs/TRIAGE_REPORT.md`'s finding 1.1 flagged this as a *correctness* staleness question (cache not cleared elsewhere in the app, `main.py`/`agent.py` never call it outside this one path) — that's out of this report's scope — but from a pure performance angle, calling it on every grounding check (rather than once per request) is not a meaningful cost at this allowlist size and does not need to be batched, throttled, or optimized. Would only become worth revisiting if the allowlist grew by multiple orders of magnitude (thousands of entries) or `validate_answer` were called per-citation instead of per-answer.

**Suggested direction**: No action needed on performance grounds. If the correctness fix for staleness (finding 1.1) changes the caching strategy, re-check this timing at that point, but at current allowlist size (28 entries) it will remain trivial under essentially any reasonable design.

### P3.2 — `confidence.py`: no redundant recomputation found

**What**: `score_claim()` (`confidence.py:44-63`) is called exactly once per surviving claim, exactly once, inside `_build_final_answer`'s loop (`agent.py:195`) — i.e., only after grounding has already validated the claim, never during retries. `overall_confidence()` (`confidence.py:66-80`) is called exactly once per finished query (`agent.py:230-232`). Both are pure, cheap, O(citations-per-claim)/O(claims) computations over already-in-memory `Hit` objects — no I/O, no re-fetching.

**How found**: Static read of `confidence.py` in full and its two call sites in `agent.py`.

**Impact**: None — flagged here only because the role brief asked for a performance-angle check on this file. The known issue with `overall_confidence()`'s weakest-link rollup (`docs/TRIAGE_REPORT.md` finding 3.3) is a correctness/UX concern, not a performance one, and is out of scope here.

**Suggested direction**: No action.

### P3.3 — Tool-schema construction rebuilt once per query rather than cached, but measured negligible

**What**: `agent.py:266-269` calls `build_search_corpus_tool(session_domains)` and `build_submit_structured_answer_tool()` fresh at the start of every `_run_query_impl` call (once per query, not per iteration — the result is reused across all iterations of that query, which is already correct). `build_submit_structured_answer_tool()` (`tools.py:99-111`) additionally calls `RawAnswer.model_json_schema()` (Pydantic schema introspection) every time, even though that schema is fully static and identical for every request.

**How found**: Direct timing: 20 iterations of building both tools combined took 29.2ms total → **1.46ms/call combined**.

**Impact**: Negligible in isolation (~1.5ms against a 35s p95) and correctly scoped outside the iteration loop already. Noted only as a micro-optimization opportunity.

**Suggested direction**: If touched during the refactor anyway, `build_submit_structured_answer_tool()` (which takes no arguments and is fully static) could be wrapped in `@lru_cache` or computed once at module import time. Not worth a dedicated change on its own.

### P3.4 — Chunking cost is entirely offline/one-time; not part of the query path at all

**What**: `backend/chunking.py`'s `chunk_text()` is called only from `ingest.py:53`, during the one-time (or on-demand re-run) ingestion step. No code path in `agent.py`/`main.py`/`tools.py` calls `chunk_text` during a live query.

**How found**: `grep -rn "chunk_text" backend/` → only `ingest.py` and `chunking.py` itself.

**Impact**: Directly answers part of the role brief's cross-reference question (item 6): chunking contributes **zero** to per-query latency or to the load-test p95, by construction. `CHUNK_MAX_CHARS=700`/`CHUNK_OVERLAP_CHARS=100` (~14% overlap) is a reasonable choice at the current corpus scale (28 docs → 99 chunks per `docs/TRIAGE_REPORT.md`); at that scale, chunk count has no measurable effect on the ~10.8ms warm Chroma query time either. Worth revisiting only if the corpus grows by orders of magnitude (larger chunks → fewer, coarser vectors to index/query; smaller chunks → more, finer-grained ones with proportionally more storage/query overhead) — not an issue today.

**Suggested direction**: No action needed now; a scale note for whoever eventually grows the corpus well beyond the current 28-document POC size.

### P3.5 — Grounding-retry feedback is already efficient (confirmed, no finding)

**What**: When a grounding check fails, the tool result sent back to the model is `report.feedback_text()` (`grounding.py:98-116`, dispatched at `agent.py:377`) — a compact, per-claim/per-citation list of specific failure reasons, not a re-send of the full retrieved context or prior conversation.

**How found**: Static read of `grounding.py:98-116` and its call site.

**Impact**: None — this directly answers the role brief's question ("whether grounding-retry feedback re-sends more context than necessary"). It does not; it's already minimal. Noted for completeness since it was one of the explicit checks requested.

**Suggested direction**: No action.

### P3.6 — `allowlist.load_allowlist()`'s supersession-chain validation is O(n²)-shaped, immaterial at current scale

**What**: `allowlist.py:99-104` does a linear `next(...)` scan over all entries for every entry that has a `supersedes` value, inside `load_allowlist()`. This is O(n × k) where k is the number of supersession edges (currently 1: `IR-002-v1`/`IR-002-v2`).

**How found**: Static read of `allowlist.py:70-106`; confirmed negligible via the same 0.24ms/call measurement in P3.1 (which exercises this exact path on every call).

**Impact**: None at 28 entries / 1 supersession edge. Would only matter if the allowlist grew to thousands of entries with many supersession chains, at which point it's still a cheap fix (build an id→entry dict once instead of scanning).

**Suggested direction**: No action needed now.

---

## Cross-reference against the load-test p95 baseline (role-brief item 6)

`docs/LOAD_TEST_REPORT.md`: p95 = **35.29s** at up to 6 concurrent VUs, 0% request failure.

Measured non-model, per-query overhead (all local, no API calls, corpus already warm):

| Component | Warm steady-state cost | Frequency per query |
|---|---|---|
| `embed_query` + `store.query` (one `search_corpus` call) | ~28.1ms | once per `search_corpus` call (several per query) |
| `allowlist.clear_cache()` + reload + revalidate | ~0.24ms | up to ~4 per query (grounding attempts) |
| Tool-schema construction (`build_*_tool`) | ~1.46ms combined | once per query |
| Chunking (`chunk_text`) | N/A | zero — offline/ingest-time only |

Even a generous session (8 `search_corpus` calls + 3 grounding attempts) totals roughly **8×28ms + 3×0.24ms + 1.5ms ≈ 226ms** — well under 1% of the 35.29s p95. **The p95 is overwhelmingly dominated by real Anthropic model round-trips** (multiple `messages.create()` calls per query, each — per P1.1 — reprocessing a growing, entirely uncached context), exactly as `docs/LOAD_TEST_REPORT.md` itself concludes ("reflects that every request is a real multi-iteration agentic loop with real model calls, not a cheap endpoint").

Two caveats to that baseline, both already covered above as P1 findings:
1. **P1.1 (no prompt caching)** means the model-call time itself is inflated beyond what it would be with caching — the 35.29s isn't "irreducible real model time," it's real model time plus avoidable redundant reprocessing of a repeated/growing prefix.
2. **P1.2 (cold-start embedding load)** means the *recorded* p95 is optimistic for a freshly-started process: the triage agent's server was already warm (post-`smoke_test.py`) before `k6` ran, so the ~16.5s one-time SentenceTransformer load cost never showed up in that number, but would hit a real deployment's first post-restart request.

Both are addressed as P1 findings above; net direction for the refactor: **prompt caching is the highest-leverage lever on the recorded number itself; startup warmup is the highest-leverage lever on making that number representative of a cold-started process.**
