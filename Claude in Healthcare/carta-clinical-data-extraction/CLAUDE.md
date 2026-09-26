# Carta Healthcare — Clinical Data Extraction

## 1. Overview

A reference architecture for a pipeline that automates extraction and structuring of clinical data from health records — turning unstructured/semi-structured clinical documents into discrete, registry-ready fields — while maintaining measured, monitored accuracy. Inspired by Carta Healthcare's published "66% faster clinical data processing, 99% accuracy" case study. This is a **simulated, representative reference architecture** — it is not an integration with Carta Healthcare's actual systems, and any patient/document data referenced anywhere in this design is synthetic.

Core use case: given a source clinical document (structured FHIR resource or unstructured narrative/PDF-derived text), extract a defined set of registry fields, and **continuously measure extraction accuracy against a gold-standard sample** — the accuracy claim is a first-class, monitored property of the system, not an assumption.

## 2. System Context

**Actors**: Data Abstractor / Registry Analyst (submits documents, reviews extraction results, curates gold-standard samples), QA Reviewer (monitors the accuracy dashboard, investigates drift), Compliance/Audit Reviewer (non-runtime).

**External systems**: FHIR R4 source (for `DocumentReference`/`Binary` retrieval), Claude via the Anthropic API, Azure platform services.

## 3. Architecture (HLD)

**Logical components**:
- API layer (submit documents, launch extraction jobs, review results and accuracy reports).
- Document-ingestion service — retrieves source documents via FHIR `DocumentReference`/`Binary`, normalizes to extractable text (a `TextExtractor` interface — this reference design assumes text is already available or OCR'd upstream; OCR/Document-Intelligence integration is a named production swap point, not built here).
- Structuring service — extracts a defined registry field schema from document text via Claude structured output, with per-field confidence and source-span provenance.
- Accuracy-QA service — samples extraction results against a curated gold-standard set, computes precision/recall/field-level accuracy, and tracks drift over time.
- Batch worker layer (Celery) — extraction jobs run as background batch work, since document volume at registry scale is not a synchronous-request workload.
- Data store — source documents (metadata, not raw PHI-bearing content beyond what's needed), extraction jobs/results, gold-standard samples, accuracy snapshots, audit log.

**Azure topology**: Azure Container Apps (API + worker pool, worker pool scaled on queue depth), Azure Database for PostgreSQL – Flexible Server, Azure Service Bus (batch-job broker), Azure Blob Storage (source document staging, encrypted), Azure Key Vault, Azure Entra ID, Azure Monitor / Log Analytics (accuracy-metric dashboards live here as monitored time series, not just a static report).

**Data flow — extraction job**: Analyst submits a document reference (or a batch of them) → ingestion service retrieves and normalizes text → structuring service calls Claude with the registry field schema as a structured-output target, extracting each field with confidence + provenance (which text span it came from) → results persisted per document → periodically (or on demand), the accuracy-QA service draws a sample, compares against gold-standard-labeled documents, and publishes an `AccuracyMetricSnapshot`.

**Scalability & HA**: worker pool autoscales on Service Bus queue depth independent of the API; Postgres read replica for the results/accuracy-dashboard read path; multi-AZ primary region, warm-standby DR; Blob Storage lifecycle policy for source-document retention per compliance requirements.

## 4. Design Detail (LLD)

**Core entities**: `SourceDocument` (FHIR DocumentReference ref, ingestion status), `ExtractionJob` (batch of documents, status, started/completed), `ExtractedField` (field name, extracted value, confidence score, source-span provenance, linked to the extraction that produced it), `QAGoldSample` (a document with human-verified correct field values, curated by a QA reviewer), `AccuracyMetricSnapshot` (timestamp, sample size, per-field and overall precision/recall/accuracy).

**Key interactions**:
- `POST /api/v1/documents` — register a source document for extraction (by FHIR DocumentReference id).
- `POST /api/v1/extraction-jobs` — launch a batch extraction job over one or more registered documents; async, returns a job id.
- `GET /api/v1/extraction-jobs/{jobId}/results` — extracted fields with confidence/provenance once the job completes.
- `POST /api/v1/qa/gold-samples` — register a human-verified gold-standard document for ongoing accuracy measurement.
- `GET /api/v1/qa/accuracy-report` — current and historical `AccuracyMetricSnapshot`s.

**FHIR resource mapping**: `DocumentReference`/`Binary` (the source material being extracted from — this is this project's primary FHIR touchpoint, unlike the structured-resource-heavy mapping in Banner Health/Elation).

**LLM orchestration**: Claude is given the registry field schema as a structured-output target and the normalized document text; every extracted field carries a confidence score and a source-span citation back into the original text, so a low-confidence or unprovenanced field is visibly flagged rather than silently trusted. The **99%-accuracy claim is substantiated by the accuracy-QA service**, not asserted: a running sample of extractions is checked against `QAGoldSample` entries, and the resulting precision/recall is the actual evidence behind any accuracy number reported. A drop below a defined accuracy threshold on a monitored field triggers a flag for QA-reviewer attention — this is a monitored property of the system, not a one-time claim.

**Error handling**: an extraction failure for one document does not fail the whole batch job — each document's result is independent; a low-confidence field is surfaced as low-confidence, never silently rounded up to look confident.

## 5. Security & Compliance

- **Access control**: Azure Entra ID-backed OAuth2/OIDC; RBAC roles `data_abstractor` (submit documents, launch jobs, view results), `qa_reviewer` (curate gold samples, view accuracy reports), `admin` (config only).
- **Audit logging**: every document ingestion, every extraction-job launch, and every gold-sample curation emits an `AuditEvent`; accuracy-snapshot generation is logged as a system event.
- **Encryption**: TLS 1.2+ everywhere; source documents in Blob Storage and extracted field values in Postgres encrypted at rest.
- **HIPAA technical-safeguards mapping**: Access Control → Entra ID + RBAC; Audit Controls → AuditEvent stream covering document access specifically (documents may contain more PHI than the extracted fields alone); Integrity → source-span provenance makes every extracted value traceable back to its origin text; Transmission Security → TLS.
- **Production-readiness gap**: real PHI use requires a BAA covering the Anthropic API, as with every project in this portfolio. Additionally, if OCR/Document-Intelligence is added as the production text-extraction swap (see §3), that service's own data-handling terms must be reviewed — not assumed covered by the LLM's BAA.

## 6. Requirements & Spec

**Functional requirements**:
- FR-1: A registered source document can be submitted for extraction against a defined registry field schema.
- FR-2: Every extracted field carries a confidence score and source-span provenance.
- FR-3: A QA reviewer can curate gold-standard samples independent of the live extraction pipeline.
- FR-4: The system computes and exposes precision/recall/accuracy metrics against the gold-standard sample on an ongoing basis, not as a one-time calculation.
- FR-5: A single document's extraction failure does not fail the containing batch job for other documents.

**Non-functional requirements — Performance**:
- Extraction throughput: ≥ 200 documents/minute per worker-pool replica for typical-length clinical documents (assumed ≤ 5 pages of normalized text), autoscaling on queue depth for larger batches.
- Per-document extraction latency: p95 ≤ 10 seconds from normalized-text-ready to results-persisted.
- Accuracy-snapshot computation: completes within 30 minutes of being triggered for a sample of up to 1,000 documents.
- API responsiveness (status/results/report endpoints): p95 ≤ 500ms.

**Other NFRs**:
- Reliability/Availability: batch jobs must be resumable — a worker crash mid-batch resumes remaining documents, not the whole batch; 99.9% API availability target.
- Security: see §5.
- Observability: per-field accuracy trend tracked over time (not just an aggregate number) so drift on a specific field is visible before it affects the overall claim.

**Acceptance criteria**: extraction against a seeded synthetic document with known expected field values returns those values with correct provenance; a seeded low-quality/ambiguous document produces a visibly low confidence score rather than a falsely confident wrong value; the accuracy report reflects a change in measured accuracy after adding a new gold-standard sample with a deliberately mismatched extraction.

## 7. Differentiator

Compared to **Qualified Health** (the closest sibling — also multi-source, document-heavy): Carta's contract is **per-field extraction accuracy** against a fixed registry schema, measured continuously against a gold-standard sample. Qualified Health's contract is **population-level coverage/precision of a ranked candidate list**, not field-level accuracy at all — it asks "who qualifies," not "what does this document say, exactly." A document Carta extracts perfectly could still be irrelevant to any of Qualified Health's intervention criteria, and vice versa.

## 8. Assumptions & Out of Scope

- Assumes a BAA covering the Anthropic API is in place for any real (non-synthetic) PHI use.
- Out of scope: OCR / scanned-document image processing itself — this design assumes normalized text is available (via an upstream OCR/Document-Intelligence step, explicitly flagged as a production swap point, not built here).
- Out of scope: registry submission/transmission to an external quality-registry system — this design stops at "structured, accuracy-measured extraction," not downstream registry integration.
- Assumes the registry field schema is defined and versioned outside this system (e.g. by a clinical-registry standards body); this design does not include schema-authoring tooling.
