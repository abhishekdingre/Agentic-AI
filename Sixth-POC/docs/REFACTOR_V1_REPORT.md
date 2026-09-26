# Refactor V1 Report — Performance + Confidence-Rollup Fix

Companion to [[PERF_REVIEW_BACKEND]], [[PERF_REVIEW_FRONTEND]], [[TRIAGE_REPORT]] (finding 3.3), and [[LOAD_TEST_REPORT]]'s "Run 2" section. This refactor closed the P1/P2 findings from both perf reviews plus the confidence-granularity correctness gap, without touching anything either review confirmed as fine and without re-opening any of the four hard commitments. Ownership followed [[AGENT_OPERATING_MODEL]]: `backend`/`frontend` builder agents implemented their own trees from a precise spec; the orchestrator (this session) owns doc updates, verification, and this report — every claim below was independently re-checked against the actual landed code and a live server, not accepted on the builder agents' self-reports alone.

## 1. Prompt caching (PERF_BACKEND P1.1)

**Before**: no `cache_control` anywhere in `agent.py`; system prompt + tools + the growing tool-result history were resent uncached on every one of up to 8 loop iterations.

**After**: `backend/agent.py`/`tools.py` — `system_blocks` wraps the system prompt in one ephemeral-cached text block; `build_submit_structured_answer_tool()` (the last tool in the list) carries its own `cache_control`, making system+tools one stable cached prefix; a rolling breakpoint on `messages[len(messages)-2]` each iteration caches the growing conversation incrementally, clearing the prior marker first (3 breakpoints total, under Anthropic's 4-limit).

**Verified independently**: ran a real (unmocked) multi-turn query directly through `_run_query_impl` with a thin wrapper around the Anthropic client logging `response.usage` per call:

| iteration | cache_creation_input_tokens | cache_read_input_tokens | input_tokens |
|---|---|---|---|
| 1 | 0 | 2647 | 90 |
| 2 | 314 | 2647 | 2842 |

`cache_read_input_tokens` was non-zero from the very first call in this run (2647) because an identical system+tools prefix had already been cached moments earlier by a prior live query in the same session — exactly the cross-request reuse this fix is meant to produce. `cache_creation_input_tokens` on iteration 2 confirms the rolling message-list breakpoint is also being written. Caching is genuinely honored through this environment's `ANTHROPIC_BASE_URL` proxy, not a silent no-op.

## 2. Startup embedder warmup (PERF_BACKEND P1.2)

**Before**: `embeddings.get_embedder()`'s `lru_cache`d `SentenceTransformer` loaded lazily on whichever request happened to be first — measured at ~15.9s cold load + ~0.57s first-inference, invisible in the original load-test baseline because `smoke_test.py` had already pre-warmed the server before that k6 run.

**After**: `backend/main.py:_warmup_embedding_model`, a third best-effort `@app.on_event("startup")` hook, calls `embed_query("warmup")` and a trivial `store.query(...)` at process start.

**Verified independently**: started a fresh `uvicorn` process, watched the startup log show the full `SentenceTransformer` load and a completed `Batches: 1/1` inference *before* "Application startup complete", then immediately hit `/api/health`: **49ms response**, confirming the cold-load cost is paid once at startup and no longer lands on a user's first query.

## 3. Batched per-turn embedding (PERF_BACKEND P2.1)

**Before**: one `embed_query` (→ one `embed_texts([single_query])` call) per `search_corpus` block, sequentially, when a turn issued several.

**After**: `agent.py`'s dispatch loop batches every `search_corpus` block's query in a turn through one `embed_texts([...])` call before dispatching each block's `store.query(...)` — domain restriction, registry population, and retrieval-log entries stay per-block and unchanged; a defensive fallback synthesizes per-block errors if the one batched embed call itself fails.

**Verified independently**: read the implementation (`agent.py:408-462`) and the dedicated `tests/test_agent_search_batching.py`, which drives a real two-search-block turn through `_run_query_impl` with the Anthropic client and embedder mocked, and asserts `embed_calls == [["target biology", "safety data"]]` — exactly one batched call, not two. Ran this test myself as part of the full suite (below); it passes.

## 4. Confidence rollup fix (TRIAGE_REPORT finding 3.3)

**Before**: `overall_confidence()` used a strict weakest-link `min()` — any single Low claim capped the entire answer, so a 13-claim, 4-domain answer with 9 well-grounded claims and 4 legitimately single-sourced ones reported overall `"Low"`.

**After**: `backend/confidence.py:overall_confidence` — buckets ranked and sorted descending, overall = the bucket at `ceil(2n/3) - 1` (the highest bucket at least two-thirds of claims meet or exceed), then the existing gaps-cap-at-Moderate and no-claims-is-Insufficient rules unchanged.

**Verified independently**:
- Hand-computed the formula against all 5 required scenarios (all-High, all-Low, 9-strong+4-Low/13, 2-High+6-Low/8, gaps-cap) before the backend agent even started — all match.
- Re-read `tests/test_confidence.py`: `test_overall_confidence_two_of_three_high_is_high` (renamed from the old weakest-link test, now correctly asserts `"High"`), plus two new regression tests mirroring the real triage scenario and its inverse. Ran the full suite myself: all pass.
- **Live, twice**, against the real triage Q1 ("What preclinical and clinical evidence supports CRB3..."):
  - Run 1: 11 claims (2 High, 2 Moderate, 7 Low) → **Low**. By hand: sorted ranks `[3,3,2,2,1,1,1,1,1,1,1]`, `threshold_index=7`, `ranks[7]=1`. Correct — 7/11 claims were genuinely single-source (distinct per-trial-phase facts each backed by exactly one report, e.g. separate Phase 1b/2a/2b/OLE claims), a real majority-weak decomposition, not a bug.
  - Run 2: 12 claims (1 High, 7 Moderate, 4 Low) → **Moderate**. `threshold_index=7`, `ranks[7]=2`. This is the scenario the fix targets: 8/12 claims (66.7%) at Moderate-or-above, a genuine supermajority, correctly escaping the old "Low" floor.
  - **Honest takeaway**: the fix is implemented and behaving exactly as designed in both directions, but the real-world outcome depends on the model's non-deterministic claim decomposition for a given run — sometimes it splits evidence into a majority of single-source claims (Low is then the correct, honest signal), sometimes into the minority-weak pattern the original finding described (Moderate/High emerges, as intended). The fix does not *guarantee* escaping Low on every run of this question; it guarantees the overall bucket now reflects a real two-thirds supermajority rather than a single-claim veto.
  - Also ran the NOAEL question live (a genuinely single-source fact by corpus design) — correctly still returns Low, confirming the fix didn't loosen genuinely-thin answers.

`docs/AIDLC_PLAN.md`'s confidence-scoring section was updated to describe the supermajority rule (done by the orchestrator directly, not a builder agent, since it's the shared spec doc).

## 5. `@lru_cache` on the static submit-tool schema (PERF_BACKEND P3.3, freebie)

`backend/tools.py:build_submit_structured_answer_tool` wrapped in `@lru_cache(maxsize=1)` — takes no arguments, schema is fully static. Verified by reading the diff; trivial and safe.

## 6. Frontend: capped rendered turns (PERF_FRONTEND P1-1)

**Before**: `#results-area` only ever `appendChild`'d; zero removal anywhere; unbounded DOM/text/listener growth over a session.

**After**: `frontend/app.js` — `MAX_RENDERED_TURNS = 20`, a `renderedTurns` array tracking mounted `.turn` elements, `shift()` + `.remove()` on the oldest once the cap is exceeded.

**Verified independently in a real headless Chrome browser** (Puppeteer against the actual `frontend/` served statically, `/api/query` mocked with a schema-conformant response so the test isolates client-side DOM logic from real model latency/cost): submitted 22 questions programmatically through the real form. Result: `document.querySelectorAll('.turn').length === 20` exactly; the first surviving turn's text was "Mock question number 2" (the 3rd submission, 0-indexed) — proving submissions 0 and 1 were **actually removed from the DOM**, not merely hidden.

## 7. Frontend: delegated citation-pill clicks + lazy quote build (PERF_FRONTEND P2-1 + P2-2)

**Before**: every citation pill got its own `click` closure; the `.citation-quote`/`<blockquote>` was built eagerly for every citation regardless of whether it was ever expanded.

**After**: `renderCitation` builds only the pill, stashing `quote`/`sourceId`/`chunkId` on `dataset`; a single delegated listener on `#results-area` (`handleResultsAreaClick`) does `event.target.closest('.citation-pill')`, lazily builds the blockquote on first click (guarded by a `quoteBuilt` dataset flag), then toggles `aria-expanded`/`hidden` on every click.

**Verified independently in the browser test**: before any click, the quote sibling's `innerHTML` was empty and `hidden=true`. After the first click: `aria-expanded="true"`, `hidden=false`, a real `<blockquote>` present containing the exact mocked quote text — confirming the lazy build fired correctly through the delegated handler. A second click collapsed it again (`aria-expanded="false"`, `hidden=true`), confirming the toggle logic (not just the lazy build) works end to end.

## 8. Frontend: lazy retrieval-log build (PERF_FRONTEND P2-2)

**Before**: `renderRetrievalLog`'s full `<ol>` was constructed unconditionally at render time, even for a `<details>` that stays collapsed.

**After**: the `<details>`/`<summary>` shell still builds immediately, but the `<ol>` of log entries is built inside a `toggle` listener with `{once: true}`, firing only on the first open.

**Verified independently in the browser test**: before opening the `<details>`, `querySelector('ol')` was `null`. After clicking `<summary>` once: an `<ol>` existed with exactly 2 `<li>` elements, matching the 2 mocked retrieval-log entries.

## Deferred (explicitly, not an oversight)

- PERF_BACKEND P2.2 (session-level `search_corpus` query dedup) — the review itself flagged this as lower priority than P1.1, which already shrinks the cost of a redundant search from the other direction; a second dedup layer now would be speculative complexity for a POC-scale corpus.
- PERF_BACKEND P3.1/P3.2/P3.4/P3.5/P3.6 and PERF_FRONTEND P3-1/P3-2/P3-3 — both reviews confirmed these as negligible or "no action needed."

## Test suite

`venv/bin/pytest tests -q --ignore=tests/load` run directly by the orchestrator (not accepted from either builder's self-report): **44 passed, 3 skipped** (the pre-existing `smoke_test.py` tests, which self-skip without a live `uvicorn` server — unchanged from before this refactor).

## Load test re-run

See [[LOAD_TEST_REPORT]] "Run 2" for the full numbers. Headline: **p95 = 61s, worse than the 35.29s baseline, and the 60s threshold now fails.** This is reported as-is rather than spun positive. The available evidence points to external model/proxy latency drift between the two measurement sessions rather than a regression caused by this refactor: none of the V1 changes touch real model-call time (the dominant cost per the original review, >99% of p95), and two unrelated manual `curl` calls made earlier in this same session for live confidence-rollup verification took 63.4s and 64.9s wall-clock — matching this k6 run's p95/max almost exactly, on the same code, same machine, same API backend. Request-failure and error rates both held at 0% at up to 6 concurrent VUs, same as the original run. Treat the p95 number as inconclusive rather than as proof the refactor slowed anything down; a clean before/after comparison would need both runs made back-to-back under the same API conditions.
