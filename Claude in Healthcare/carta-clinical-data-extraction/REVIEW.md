# Review — Carta Healthcare Clinical Data Extraction

## Security Review

Project reviewed: carta-clinical-data-extraction/CLAUDE.md (all 8 sections read in full).

### P1

1. **No audit story for the actual PHI-viewing flow.** `GET /api/v1/extraction-jobs/{jobId}/results` (§4) is where humans view extracted field values and source-span provenance — snippets of the original document text, which per §5's own admission "may contain more PHI than the extracted fields alone." §5's four enumerated audit events (ingestion, job launch, gold-sample curation, accuracy-snapshot generation) don't include "results viewed." Direction: add a `results_viewed` `AuditEvent` capturing who viewed which job/document's fields and provenance.
2. **Raw source-document storage (the actual excess-PHI surface) has no access-control or audit story distinct from the app-level RBAC/AuditEvent stream.** Blob Storage holds the raw `Binary` content with only "encrypted" named as a control — no storage-level RBAC/SAS scoping, private endpoints, or blob-read auditing distinct from the one-time ingestion event. Direction: name a storage-level access boundary (scoped managed identity / short-TTL SAS / private endpoint) and confirm blob-read diagnostic logging feeds the same audit pipeline.
3. **The BAA/production-readiness framing understates what actually crosses to Anthropic.** §5 states generically that real PHI use "requires a BAA covering the Anthropic API," but §4 is explicit that the *entire normalized document text* is sent to Claude, not a redacted/field-scoped excerpt — and §5 never connects this to its own admission that documents carry excess PHI. Direction: state explicitly that the BAA must cover full document content, not just registry fields, and consider whether minimization/redaction is feasible before the LLM call.

### P2

1. **RBAC roles (§5) don't match the actor responsibilities named in System Context (§2).** §2 says the Data Abstractor "curates gold-standard samples," but §5 grants that to `qa_reviewer` instead; §2 says QA Reviewer "investigates drift," but `qa_reviewer`'s granted permissions (curate gold samples, view accuracy reports) don't include viewing individual extraction results/provenance needed to diagnose drift. Reconcile §2 and §5.
2. **`GET /api/v1/qa/accuracy-report` has no explicit audit-event entry** — §5 logs snapshot generation (write side) but not report retrieval (read side). Lower severity than the results-viewing gap, same class of omission.
3. **Encryption inventory is silent on in-flight/in-queue payloads.** Azure Service Bus and Celery task payloads (which plausibly carry full normalized document text between ingestion and structuring) aren't named alongside Blob/Postgres and "TLS everywhere."

### P3

1. Compliance/Audit Reviewer's access path to audit output isn't described (presumably fine given "non-runtime," but a one-line note would help).
2. No mention of customer-managed keys (CMK) via Key Vault for at-rest encryption, despite Key Vault being named in the topology.

**Not flagged:** multi-tenancy isolation is not applicable (no tenant/org concept). No contradiction found between §8 and §5 — the OCR/Document-Intelligence swap is already correctly treated as its own BAA/data-handling surface distinct from the Anthropic BAA.

## Performance Review

Reviewed in full: carta-clinical-data-extraction/CLAUDE.md (§3, §4, §6).

### P1

1. **Throughput target is not shown to be achievable by the described worker architecture.** §6's "≥200 documents/minute per worker-pool replica" combined with the p95 ≤10s per-document latency requires, via Little's Law, roughly 13–33 concurrent in-flight Claude calls per replica. Neither §3 nor §4 states a concurrency model beyond "Batch worker layer (Celery)" — default Celery prefork workers process one task per process, so reaching that concurrency needs an async/threaded model that isn't mentioned. Direction: state per-replica concurrency (async task execution, pool size) and confirm consistency with the latency target and Anthropic API rate limits.
2. **Per-document latency NFR doesn't say whether queue-wait counts.** "p95 ≤10 seconds from normalized-text-ready to results-persisted" is ambiguous about whether it starts at enqueue or dequeue — in a batch workload that explicitly relies on autoscaling under queue depth, a burst submission sitting queued during scale-out would blow the target if "normalized-text-ready" means enqueue time. Direction: scope the metric explicitly to processing time, and add a distinct queue-wait/submit-to-result percentile for batch submissions.

### P2

1. **Anthropic API rate limits (RPM/TPM) as a shared scaling ceiling are never addressed** — all replicas (and the Accuracy-QA service) draw against one org-level quota; this is plausibly the real bottleneck before Container Apps replica limits are, and isn't mentioned.
2. **No caching or batch-API cost/latency lever named for the LLM-heavy structuring step.** The registry field schema is a large, static structured-output target sent on every call — a prompt-caching candidate — and the workload is explicitly batch, which is what Anthropic's Message Batches API targets (though that API is in tension with the per-item p95 ≤10s guarantee — a tradeoff that should be stated explicitly either way).
3. **Accuracy-QA snapshot computation's cost basis and resource isolation are unspecified.** It's unclear whether QA sampling means fresh Claude re-extraction of sample documents or a pure diff of persisted results — if re-extraction, it competes with the live pipeline's Claude-API quota and worker pool with no stated isolation/priority lane, and no cadence is given for "periodic" triggering.

### P3

1. Autoscaling is named at the mechanism level but lacks parameters (target queue depth, min/max replicas, scale-out latency).
2. The flat "API responsiveness p95 ≤500ms" is applied uniformly including `/qa/accuracy-report`, which returns historical time-series with no stated pagination/date-range scoping — likely the first endpoint to violate a flat target as history accumulates.
3. The ≤5-page document assumption has no stated fallback/chunking strategy for longer documents beyond generic volume autoscaling.

## Spec-Compliance Review

**Target:** carta-clinical-data-extraction/CLAUDE.md (all 8 sections read in full)
**Sibling read:** qualified-health-cohort-identification/CLAUDE.md (in full, including its own §7)

**Specific check — "99% accuracy" framing (§1) vs. "measured/monitored" claim (§4): confirmed consistent, no contradiction.** The 99% figure appears once in §1, explicitly attributed to the external case study and immediately hedged; §4 reinforces "substantiated... not asserted." The figure never reappears as a static NFR/SLA/acceptance-criterion target. Handled cleanly.

### P1
None. All 8 sections present with substantive content; every FR maps to a concrete component/entity/endpoint; the core differentiator claim is mutually and consistently stated in both docs' §7.

### P2

1. **"Multi-source" mischaracterizes Carta's own architecture, and the sibling repeats the same imprecision.** Carta's own §7 calls Qualified Health "also multi-source, document-heavy," but Carta's own §2/§3 describe a single FHIR R4 source with no identity-resolution/reconciliation component — QH's own doc defines "multi-source" as fragmented endpoints requiring exactly that kind of reconciliation service, which Carta doesn't have. Both docs use the identical phrase, so it's a shared, load-bearing imprecision, not a one-off typo. Direction: drop "multi-source" from the Carta-side parenthetical, or clarify it means "multiple document formats," not fragmented FHIR endpoints.
2. **FR-5 has no corresponding acceptance criterion.** FR-5 (a single document's failure doesn't fail the batch) is listed in §6 but none of the three ACs test partial-batch-failure isolation. Add a fourth AC exercising this.
3. **Confidence/accuracy thresholds are referenced twice but never given a value or a data-model home.** §4 references "a low-confidence field" and "a defined accuracy threshold" without quantifying either or storing them anywhere (not even on `AccuracyMetricSnapshot`); the §6 AC inherits the same vagueness ("visibly low confidence score"). Give the threshold(s) a concrete value or explicit config field.
4. **The QA-alerting behavior on accuracy drift has no owning FR.** §4 describes an active "triggers a flag for QA-reviewer attention" behavior that FR-4 (metrics computation only) doesn't cover, and no acceptance criterion tests it — the part of the headline "monitored property" story most likely to silently not get built. Add an FR and matching AC.

### P3

1. Acceptance criterion 1 doesn't define "correct provenance" (exact vs. overlapping span match).
2. Actor/RBAC role-list asymmetry (Compliance/Audit Reviewer has no §5 role; `admin` has no §2 actor) — identical pattern in the QH sibling doc, reads as a deliberate portfolio-wide convention rather than a Carta-specific slip.
