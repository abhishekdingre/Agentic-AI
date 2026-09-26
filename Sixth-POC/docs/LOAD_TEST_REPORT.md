# Load Test Report

Companion to [[AIDLC_PLAN]] ("Load testing") and [[TRIAGE_REPORT]] (finding 4.4). Script: `tests/load/query_load_test.js` (k6), `ramping-vus` executor against a real, locally running `POST /api/query` — no mocking, actual model calls end to end.

## Run 1 (executed by the P3-Triage-Agent during its review)

**Profile**: 0→3 VUs over 30s, 3→6 VUs over 1m, 6→0 over 30s. Mixed question set drawn from the verification table (rich cross-domain, superseded-doc, no-evidence, contradiction questions) with varying domain selections.

**Result**:
```
THRESHOLDS
  http_req_duration: ✓ 'p(95)<60000' p(95)=35.29s
  http_req_failed:   ✓ 'rate<0.05'   rate=0.00%
  rag_error_rate:    ✓ 'rate<0.05'   rate=0.00%
checks_succeeded: 100.00% (48/48)
http_reqs: 16, http_req_failed 0/16, rag_refusal_rate 18.75% (3/16)
```
All thresholds passed on the first real run. 0% request-failure rate at up to 6 concurrent virtual users; p95 latency (35.3s) is well under the 60s threshold but reflects that every request is a real multi-iteration agentic loop with real model calls, not a cheap endpoint — expected for this architecture, not a regression. Refusal rate (18.75%) is plausible given the mixed question set deliberately includes no-evidence and contradiction questions. The 3 "interrupted" iterations are k6 cutting off in-flight requests during ramp-down (`ramping-vus` executor behavior), not request failures.

## Known limitation
This is one run at a modest concurrency ceiling (6 VUs). It demonstrates the system doesn't fall over under light concurrent load and gives a real p95 baseline, but it is not a substitute for a repeated run or a higher-concurrency test before treating the system as load-validated for anything beyond POC/demo purposes. Re-run with `k6 run tests/load/query_load_test.js` (requires the corpus ingested and the server running) if load characteristics need to be re-confirmed after further changes — in particular after the fix cycle in [[TRIAGE_REPORT]] lands, since input-validation changes (rejecting blank questions / invalid domains) could shift the error-rate metric if the question mix is ever changed to include invalid inputs.

## Run 2 — V1 re-run (after [[REFACTOR_V1_REPORT]]: prompt caching, embedder warmup, batched per-turn embedding)

**Profile**: identical script/profile to Run 1 (0→3 VUs/30s, 3→6 VUs/1m, 6→0/30s, same mixed question set).

**Result**:
```
THRESHOLDS
  http_req_duration: ✗ 'p(95)<60000' p(95)=1m1s
  http_req_failed:   ✓ 'rate<0.05'   rate=0.00%
  rag_error_rate:    ✓ 'rate<0.05'   rate=0.00%
checks_succeeded: 100.00% (39/39)
http_reqs: 13, http_req_failed 0/13, rag_refusal_rate 7.69% (1/13)
http_req_duration: avg=31.11s min=12.13s med=25.49s max=1m2s p(90)=59.43s p(95)=1m1s
```

**p95 got worse, not better: 61s vs. the 35.29s baseline, and the 60s threshold now fails.** Reporting this plainly rather than spinning it — the refactor did not deliver a measurable end-to-end latency win on this run, and by this one metric it looks like a regression.

**Why this is very unlikely to be caused by the V1 changes themselves:**
- Every V1 backend change (prompt caching, startup embedder warmup, batched per-turn `embed_texts`) targets *non-model* overhead — the original [[PERF_REVIEW_BACKEND]] measured that overhead at ≈226ms worst case against a 35s p95 (<1%). None of it can plausibly add tens of seconds per request; caching reduces prompt-processing cost, it doesn't add to it.
- Independent evidence the real cost is model/proxy latency, not the refactor: during this same session, two manual `curl` calls to `/api/query` for the triage report's Q1 (unrelated to the k6 run, run beforehand) took **63.4s and 64.9s** wall-clock each — matching this k6 run's p95/max almost exactly. That's a same-session, same-code-path signal that the current floor for a real multi-iteration agentic loop through this environment's `ANTHROPIC_BASE_URL` is simply higher right now than it was during Run 1's session, for reasons external to this code (the proxy/fallback chain noted in [[TRIAGE_REPORT]] finding 4.1).
- Sample size is small either way (13 completed iterations here vs. 16 in Run 1) — a couple of slow real model calls at the tail move p95 substantially at this volume.

**What Run 2 does confirm**: 0% request-failure rate and 0% `rag_error_rate` hold at up to 6 concurrent VUs, same as Run 1 — the system doesn't fall over under load after the refactor either. The confirmed, measurable wins from this refactor are the ones verified directly against `response.usage` (real `cache_read_input_tokens`/`cache_creation_input_tokens`) and the startup-warmup timing check, not this end-to-end p95 number — see [[REFACTOR_V1_REPORT]] for that evidence. Treat this run as inconclusive on latency, not as a disproof of the caching win, and re-run again in a future session (ideally back-to-back with a Run-1-style baseline under the same API conditions) if a clean before/after p95 comparison is needed.
