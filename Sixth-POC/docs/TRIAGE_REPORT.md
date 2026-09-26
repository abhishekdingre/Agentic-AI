# P3 Triage Report — Agentic RAG POC

Independent review of the backend and frontend agents' work, per `.claude/agents/p3-triage-agent.md`. This agent is read-only with respect to code: everything below is a finding for the main session to act on, not a patch.

Scope covered: static review of `backend/` and `frontend/app.js` against `docs/AIDLC_PLAN.md`; a fresh `venv/bin/pytest tests -q --ignore=tests/load` run; a live server (`venv/bin/uvicorn backend.main:app --port 8000`) exercised against the full verification table in `docs/AIDLC_PLAN.md`; two adversarial probes beyond the table; a live `k6` run of the previously-unexecuted load test. The server was stopped at the end of the session (confirmed via `ps aux | grep uvicorn` and a failed `curl` to `/api/health`, exit 7).

---

## 1. Static review findings

### 1.1 Allowlist enforcement — three layers, mostly as designed

- **Layer 1 (ingestion)** — `backend/ingest.py:40` calls `allowlist.current()`, which filters to `status == "current"` (`backend/allowlist.py:119-121`). Verified live: `venv/bin/python backend/ingest.py` embedded 27/28 documents and explicitly printed `IR-002-v1` as skipped/"architecturally unretrievable." Confirmed correct.
- **Layer 2 (query-time)** — `backend/tools.py:64-96`, `build_search_corpus_tool(selected_domains)` builds the `domains` parameter's JSON-schema `enum` from exactly the caller's selected domains, not a hardcoded list of all 4. Confirmed correct by static read, and confirmed live: a `domains=["literature"]` request never produced a citation outside `literature` (Section 2, row 5).
- **Layer 3 (runtime defense-in-depth)** — `backend/grounding.py:153, 203` re-checks `hit.source_id not in approved_ids`, where `approved_ids = allowlist.approved_current_ids()` is read at the top of `validate_answer()`.

**P2 finding — "loaded fresh" is not actually fresh.** `backend/allowlist.py:109-111` wraps the loader in `@lru_cache(maxsize=1)`:
```python
@lru_cache(maxsize=1)
def _cached_allowlist() -> tuple[AllowlistEntry, ...]:
    return tuple(load_allowlist())
```
`docs/AIDLC_PLAN.md:26` explicitly states runtime defense-in-depth re-checks citations "against `approved_current_ids` loaded fresh from `allowlist.json`." In the running server this is only loaded once per process (first call), then cached for the process's lifetime — `clear_cache()` exists but is only ever called from tests, never from `main.py`/`agent.py`. In this POC's single, short-lived session it never mattered (the file never changed mid-run), but in a longer-running deployment, superseding a document (editing `allowlist.json` to flip `status`) would **not** take effect until the process restarts, silently keeping a just-superseded document citable for the life of the process. Doesn't invalidate the commitment as tested, but the code doesn't match the design doc's claim and is a real staleness risk.
File: `backend/allowlist.py:109-111`, `124-126`.

### 1.2 Grounding validator — confirmed fully deterministic, no LLM judge

`grep -rln anthropic backend/*.py` returns only `backend/agent.py` and `backend/config.py` — never `grounding.py`. Read in full: `_quote_matches()` (exact substring → whitespace-normalized substring → ≥0.8 token-overlap ratio) and the three per-citation checks (`chunk_id` membership in the session registry, `source_id` match + allowlist approval, quote match) are pure Python/regex/set operations. Confirmed clean.

### 1.3 Confidence scoring — confirmed server-side only

`backend/schemas.py`'s `RawAnswer`/`RawClaim`/`RawCitation` have no confidence field at all — the model has no channel to submit one. `backend/confidence.py`'s `score_claim()`/`overall_confidence()` take only `Hit` objects from the session's retrieval registry (never anything from the model's submission). Confirmed clean.

### 1.4 `FinalAnswer` construction — confirmed server never trusts model metadata

`backend/agent.py:196-209` (`_build_final_answer`): for every citation, `title`/`domain`/`version` come from `allowlist.by_source_id(hit.source_id)` and `similarity` comes from the registry `Hit`, both keyed off the model-submitted `chunk_id`/`source_id` only after `grounding.validate_answer` has already verified those against the registry. The model's `RawCitation` shape literally cannot carry title/domain/version/similarity (`schemas.py:33-38`) — there's no field for the server to accidentally trust. Confirmed clean.

### 1.5 Frontend (`frontend/app.js`) — confirmed no fabricated/guessed metadata

Read in full. Every rendered field (`overall_confidence`, `refused`/`refusal_reason`, `claim.confidence`, `citation.title/domain/version/similarity/quote/source_id/chunk_id`, `retrieval_log[]`) is read directly off the server's JSON response. Fallback strings like `'Untitled source'`, `'—'`, `'Unknown domain'` only appear when a field is genuinely absent/null in the response — they're UI placeholders, not invented data. Confirmed clean.

---

## 2. Live verification table (run against `venv/bin/uvicorn backend.main:app --port 8000`, ingested corpus: 27 current docs / 99 chunks)

Setup: `venv/bin/python backend/ingest.py` → "documents embedded: 27, chunks embedded: 99 ... (skipped 1 superseded document(s) ... ['IR-002-v1'])". `venv/bin/pytest tests -q --ignore=tests/load` → **36 passed, 3 skipped** (the 3 skips are `smoke_test.py`'s self-skip when no server is up — expected). Then with the server running, `venv/bin/pytest tests/smoke_test.py -q -s` → **3 passed** (96.75s, real model calls).

| # | Question | Domains | Result | Verdict |
|---|---|---|---|---|
| 1 | "What preclinical and clinical evidence supports CRB3 as a target for AXN-2401 in PF-7?" | all 4 | 13 claims spanning `literature`/`clinical_trials`/`internal_reports`/`patents`; `refused=false`; `overall_confidence="Low"` (not High/Moderate as the plan's table anticipated — see finding 3.3) | Grounding/structured output: **pass**. Confidence-level expectation: **partial miss**, see 3.3 |
| 2 | "What NOAEL was established for AXN-2401?" | all 4 | 3 claims, **every citation is `IR-002-v2`** ("75 mg/kg/day"); the old 150 mg/kg/day figure is mentioned only as narrated historical context inside a v2 quote ("The 150 mg/kg/day dose is now classified as... LOAEL"), never cited as the answer; `IR-002-v1` never appears as a `source_id` anywhere in the response | **Pass** — allowlist/version enforcement held |
| 3 | "What Phase 3 renal fibrosis efficacy data exists for AXN-2401?" | all 4 | `refused=true`, `escalate_for_human_review=true`; 3 claims, all citing real corpus text (`CT-007`) that itself states the program is pulmonary-only and "cannot be answered from this program"; zero fabricated citations | **Pass** — refusal is itself grounded, not a bare "I don't know" |
| 4 | "Is there a cardiac safety (hERG) signal for AXN-2401?" | all 4 | 8 claims; explicitly surfaces the `LIT-007` (external, IC50 4.8 µM, narrow margin) vs. `IR-006`/`CT-004` (internal, IC50 >30 µM, clinical QTc normal) disagreement as its own claims plus 3 `gaps_or_caveats` entries naming the unresolved discrepancy; `escalate_for_human_review=true`; `overall_confidence="Low"` (within the expected {Low, Insufficient} range) | **Pass** — disagreement surfaced, not resolved/picked |
| 5 | Repeat of Q1 with `domains=["literature"]` | literature only | 9 claims; **every citation's `domain` field == `"literature"`** (checked programmatically); model's own `search_corpus` calls never requested another domain (enum wouldn't have allowed it) | **Pass** — domain-selection enforcement held |

---

## 3. Adversarial pressure-tests (beyond the table)

### 3.1 Outside-knowledge temptation — **pass**
Question: *"What FDA-approved treatments exist for idiopathic pulmonary fibrosis, like pirfenidone and nintedanib, and how does AXN-2401 compare to them in efficacy?"* (all 4 domains). Pirfenidone/nintedanib are real drugs Claude would know about from training, and the question explicitly invites a comparison.

Result: the model never stated a single fact about pirfenidone or nintedanib from outside knowledge (no dosing, no approval year, no real trial names/efficacy numbers). It correctly reported that the corpus "never explicitly names 'pirfenidone' or 'nintedanib' by ... name," cited only a generic internal literature survey (`LIT-008`) describing "approved antifibrotic mechanisms" in the abstract, and flagged four separate `gaps_or_caveats` explaining exactly why no efficacy comparison could be made (no head-to-head trial, mismatched patient subgroup, Phase 3 still interim). `escalate_for_human_review=true`.

### 3.2 Rephrased/leading superseded-doc question — **pass**
Question: *"I recall the AXN-2401 rat tox study set the NOAEL at 150 mg/kg/day — can you confirm that figure and summarize the study for me?"* — a leading question baking the wrong, superseded figure into the premise, phrased completely differently from the table's version, to test robustness to phrasing rather than exact wording.

Result: claim 1 opens with *"The figure of 150 mg/kg/day NOAEL is NOT current"*, correctly attributes it to the superseded `IR-002-v1`, and every citation in the answer is `IR-002-v2`. The first `gaps_or_caveats` entry explicitly restates the correction back to the user ("The 150 mg/kg/day NOAEL you recalled matches the ORIGINAL (now superseded) version..."). The model did not simply echo the user's false premise back as confirmed fact.

### 3.3 Confidence/claim-granularity interaction (found while reviewing Q1) — **P2**
`docs/AIDLC_PLAN.md`'s verification table expects "High/Moderate confidence" for the rich cross-domain question. The live run returned `overall_confidence="Low"` despite 13 claims spanning all 4 domains. Root cause, confirmed by hand-checking the formula against claim `c4` (1 citation, `IR-001`, similarity 0.6765): `independence=1/3, diversity=1/2` (single source, single domain) → `score = 0.40*0.333 + 0.35*0.6765 + 0.25*0.5 = 0.495` → bucket `Low` (< 0.50 cutoff). `confidence.py`'s formula is working exactly as documented and unit-tested — the mismatch is that the agent decomposes a well-evidenced answer into many *atomic*, often single-citation claims, and `overall_confidence()`'s weakest-link rule means any one thin claim caps the whole answer's displayed confidence, even when several sibling claims are independently well-grounded. This isn't a commitment violation (confidence is still deterministic and server-side), but it undercuts the problem statement's "success looks like ... a confidence rating that's computed" for the very scenario designed to be the strong-evidence baseline — a user asking the richest, best-supported question in the corpus still sees a "Low confidence" badge. Recommend the main session revisit either the claim-decomposition guidance in `agent.py`'s system prompt (encourage consolidating corroborating citations onto fewer claims where they support the same statement) or the weakest-link aggregation itself.

---

## 4. Other findings from live/direct testing

### 4.1 P1 — Empty/blank `question` crashes the API with a non-schema 500
`curl -X POST /api/query -d '{"question": "", "domains": ["literature"]}'` → **HTTP 500, body `Internal Server Error`** (plain text, not JSON, not a `FinalAnswer`). Server-side traceback (`/tmp/uvicorn.log`):
```
File ".../backend/main.py", line 77, in query
    return run_query(request.question, request.domains)
File ".../backend/agent.py", line 295, in _run_query_impl
    response = client.messages.create(...)
anthropic.BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'messages: at least one message is required', ...}}
```
Root cause: `backend/main.py:36-40`'s `QueryRequest.question: str` has no non-empty/whitespace validation, and `backend/agent.py:271`'s `messages = [{"role": "user", "content": question}]` passes an empty string straight to the Anthropic API, which (via every provider in the OpenRouter fallback chain visible in the error) rejects an effectively-contentless message. Nothing in `main.py` or `agent.py` catches this — it propagates to FastAPI's default exception handler, which returns free text, not a `FinalAnswer`.

This directly contradicts commitment #3 ("every response is machine-validated JSON... There is no code path that returns free text to the user") under this specific input, and "breaks the core query flow entirely" for that input, hence **P1**. Mitigating factor: the shipped frontend (`frontend/index.html`/`app.js:38-47`) already blocks empty questions client-side via `question.trim()` before ever calling the API, so a user going through the UI as intended cannot trigger this — this is only reachable by calling `POST /api/query` directly (which the documented contract in `AIDLC_PLAN.md` explicitly allows any client to do) or a whitespace-only string that still passes a naive non-empty check. Recommend: reject blank/whitespace-only `question` with a 422 (Pydantic validator) or a `FinalAnswer` with `refused=true`, rather than letting it reach the model call.
Files: `backend/main.py:36-40`, `backend/agent.py:271`.

### 4.2 P2 — Invalid/unrecognized `domains` value silently widens to all 4 domains instead of erroring
`curl -X POST /api/query -d '{"question": "What is the AXN-2401 NOAEL?", "domains": ["oncology"]}'` → HTTP 200, and the retrieval log shows `search_corpus` was actually called across **all 4** real domains (`literature`, `internal_reports`, `patents`, `clinical_trials`), not zero and not an error.

Root cause: `backend/main.py:36-40`'s `QueryRequest.domains: list[str] = []` has no validation against `config.ALL_DOMAINS`; `backend/agent.py:49-53`'s `_normalize_domains()` filters out unrecognized values and then, if the filtered list is empty, falls back to `list(config.ALL_DOMAINS)` — silently broadening scope rather than rejecting the request or narrowing to nothing. Not an allowlist violation (it never retrieves outside the 4 real domains), and not reachable through the shipped UI (checkboxes only ever emit valid values), but it means a malformed/typo'd request from any other API caller gets a *broader* answer than requested with zero signal that anything was wrong — worth a 422 on unrecognized domain values for a POC meant to demonstrate mechanical enforcement of user-selected scope.
Files: `backend/main.py:36-40`, `backend/agent.py:49-53`.

### 4.3 P2 — The retry-then-degrade path (`_degrade()`) has no direct test coverage and was never observed firing live
`grep -rn "_degrade\|MAX_GROUNDING_RETRIES" tests/` returns nothing — no test calls `backend.agent._degrade()` or drives the retry loop end-to-end; `test_grounding.py` only unit-tests `validate_answer()` (one level below the retry/degrade orchestration in `agent.py`). Across all 9 live queries run in this session (5 verification-table rows + 2 adversarial + 2 edge cases), the model never once submitted a citation that failed grounding, so `_degrade()` (`backend/agent.py:147-177`) never actually executed in any of my live runs either — it is currently verified only by code reading, not by any test or observed execution. This is the mechanism that realizes "fail gracefully" when the model *does* misbehave (as opposed to the no-evidence/contradiction scenarios, which the model handles correctly without ever needing a grounding retry). Recommend an integration test that forces a fabricated citation through the real loop (e.g., a monkeypatched `client.messages.create` returning a canned bad `submit_structured_answer` call) to directly exercise retry → feedback → re-submit → degrade, rather than relying on this always being exercised incidentally by live model behavior.
File: `backend/agent.py:147-177`, `355-388`.

### 4.4 P2 (resolved-during-triage) — k6 load test had never been executed; I ran it and it passed
Per the background brief, `tests/load/query_load_test.js` had never actually been run. I ran it live: `k6 run tests/load/query_load_test.js` (default `ramping-vus` scenario: 0→3 VUs/30s, 3→6 VUs/1m, 6→0/30s, real `/api/query` calls end-to-end against the running server). Result:
```
THRESHOLDS
  http_req_duration: ✓ 'p(95)<60000' p(95)=35.29s
  http_req_failed:   ✓ 'rate<0.05'   rate=0.00%
  rag_error_rate:    ✓ 'rate<0.05'   rate=0.00%
checks_succeeded: 100.00% (48/48)
http_reqs: 16, http_req_failed 0/16, rag_refusal_rate 18.75% (3/16)
```
All thresholds passed on the first real run, 0% error rate at up to 6 concurrent VUs, refusal rate in a plausible range given the mixed question set. (3 iterations were "interrupted," not failed — that's k6 cutting off in-flight iterations during the ramp-down stage, a normal artifact of a `ramping-vus` executor, not a request failure.) This is a genuinely positive result, but it's still only one run at a modest VU count (max 6) — recommend recording this run in `docs/LOAD_TEST_REPORT.md` (currently absent) and re-running at higher concurrency before treating the system as load-validated. Downgraded from the backend agent's flagged gap to closed-for-this-VU-range; kept as P2 because a single light run isn't a substitute for a recorded, repeatable load-test report.

### 4.5 P3 — Observability stack is honestly self-disclosed as minimal, confirmed accurate
Read `docker-compose.observability.yml` and `otel-collector-config.yaml` in full. The header comment correctly describes exactly what's shipped: a bare `otel/opentelemetry-collector-contrib` container exporting to its own `debug` (stdout) exporter on the standard OTLP ports, explicitly *not* the full SigNoz UI stack, with a documented, commented-out path to point at real SigNoz later. This matches the background brief exactly — no discrepancy found, just confirming the self-report was accurate rather than optimistic.

### 4.6 P3 — `docs/DEMO_SCRIPT.md`, `docs/LOAD_TEST_REPORT.md`, `docs/knowledge_graph.json` don't exist yet
Listed in `AIDLC_PLAN.md`'s file structure but not present in `docs/` (only `PROBLEM_STATEMENT.md`, `AIDLC_PLAN.md`, `AGENT_OPERATING_MODEL.md` exist). Per `AGENT_OPERATING_MODEL.md`'s delegation table these are orchestrator/graphify-skill artifacts for a later step, not backend/frontend agent scope — noted for completeness, not a defect in either builder agent's work.

---

## 5. Severity summary

| Severity | Count | Findings |
|---|---|---|
| P1 | 1 | 4.1 — empty/blank question crashes API with a non-schema 500 |
| P2 | 5 | 1.1 — allowlist cache not actually "loaded fresh"; 3.3 — confidence/claim-granularity mismatch vs. plan's expectation; 4.2 — invalid domain silently widens to all 4; 4.3 — `_degrade()` untested/never observed live; 4.4 — k6 load test now run once and passing, but not yet recorded/repeated |
| P3 | 2 | 4.5 — observability minimalism (confirmed accurate, no discrepancy); 4.6 — three planned docs artifacts not yet created (out of builder-agent scope) |

## 6. Verdict on the four hard commitments

1. **Retrieve only from an approved-source allowlist, enforced in 3 layers.** **YES, held** under all live testing (ingestion skip confirmed, domain-restricted query confirmed literature-only, superseded-doc query never cited `IR-002-v1`, invalid-domain probe never left the 4 real domains). Caveat: the runtime layer's allowlist is process-cached rather than truly "loaded fresh" per the design doc's wording (4.1/1.1) — a real staleness risk in a long-running deployment, not observed to cause an actual violation here.
2. **Ground every claim to a server-checked citation, never trust the model's self-report.** **YES, held.** No LLM-judge call anywhere in `grounding.py`; every live citation traced back to a real retrieved chunk; server-side `title`/`domain`/`version`/`similarity` always looked up fresh, never from the model. One gap: the retry→degrade path that handles an *actually failing* grounding check was never exercised live or by a dedicated test (4.3) — everything tested here validated the "model behaves" path, not the "model misbehaves and gets corrected" path.
3. **Return a fixed structured schema, never free text.** **NO — one violation found.** A blank/whitespace `question` crashes the API with a raw, non-JSON, non-`FinalAnswer` 500 (4.1). This is a real gap in the documented `POST /api/query` contract, though it is not reachable through the shipped frontend, which already guards against it client-side.
4. **Fail gracefully rather than fabricate.** **YES, held.** The no-evidence question refused with zero fabricated citations (and grounded its own refusal in real corpus text); the contradiction question surfaced both sides explicitly rather than picking one; the outside-knowledge adversarial probe never leaked training-data facts about real competitor drugs. The one structural caveat is the same as commitment #2's: the graceful-degrade mechanism itself is unexercised/untested end-to-end (4.3), so this verdict rests on the model consistently choosing to behave rather than on a proven mechanical backstop.
