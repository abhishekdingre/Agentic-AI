# Qualified Health — Cohort Identification

## 1. Overview

A reference architecture for a system that screens large patient populations against fragmented medical records to surface candidates for evidence-based interventions (e.g. a clinical trial or a targeted care program). Inspired by Qualified Health's published "identifying patients for life-saving treatments" case study. This is a **simulated, representative reference architecture** — it is not an integration with Qualified Health's actual systems, and any patient data referenced anywhere in this design is synthetic.

Core use case: given a versioned set of intervention eligibility criteria, run a **batch cohort screen** across a patient population drawn from multiple, fragmented FHIR sources, producing a ranked, explained candidate list for a care team to review and act on. This is fundamentally a **population-scale batch workload**, not a single-patient interactive one.

## 2. System Context

**Actors**: Care Coordinator / Program Manager (defines intervention criteria, reviews candidate lists, records decisions), Clinician (secondary reviewer on flagged candidates), Compliance/Audit Reviewer (non-runtime).

**External systems**: multiple FHIR R4 sources (this project explicitly assumes fragmentation — a patient's record may span more than one FHIR endpoint, unlike the single-EHR assumption in Banner Health/Elation), Claude via the Anthropic API, Azure platform services.

## 3. Architecture (HLD)

**Logical components**:
- API layer (program manager-facing — define criteria, launch runs, review candidates).
- Ingestion & reconciliation service — pulls patient records from multiple fragmented FHIR sources and resolves identity across them (same patient, different source-system ids).
- Eligibility-matching engine — a **deterministic rule engine** evaluates each patient against versioned intervention criteria; this is the actual eligibility decision-maker.
- Rationale-generation service — Claude drafts a human-readable summary of *why* a patient matched (citing the specific evidence), for care-team review — it does not make the eligibility call itself.
- Batch worker layer (Celery) — runs a cohort screen as a background job across the full population, since this cannot be a synchronous request/response operation at population scale.
- Data store — criteria versions, cohort runs, candidate results, review decisions, audit log.

**Azure topology**: Azure Container Apps (API + worker pool, worker pool scaled independently from the API on queue depth), Azure Database for PostgreSQL – Flexible Server, Azure Service Bus (the Celery broker for batch jobs at this scale — chosen over Redis-as-broker for the durability/ordering guarantees a population-scale run needs), Azure Cache for Redis (identity-resolution lookup cache), Azure Key Vault, Azure Entra ID, Azure Blob Storage (cohort-run result exports), Azure Monitor / Log Analytics.

**Data flow — cohort run**: Program manager defines/selects versioned `InterventionCriteria` → launches a `CohortRun` against a population → worker pool pulls each patient's fragmented records via the ingestion/reconciliation service → deterministic eligibility-matching engine evaluates each patient against the criteria → for each match, Claude drafts an evidence-citing rationale → results persisted as ranked `CandidatePatient` records → care team reviews and records a `ReviewDecision` per candidate.

**Scalability & HA**: worker pool autoscales on Service Bus queue depth, independent of the API's own autoscaling; a cohort run against a large population is explicitly designed to complete in bounded wall-clock time via worker parallelism, not by making any single call faster; Postgres Flexible Server with a read replica for candidate-list browsing so it doesn't contend with the batch write path; multi-AZ primary region, warm-standby DR.

## 4. Design Detail (LLD)

**Core entities**: `InterventionCriteria` (versioned — a cohort run always references a specific version, so results are reproducible), `CohortRun` (status, population definition, started/completed timestamps), `CandidatePatient` (score, matched-criteria list, Claude-drafted rationale, evidence citations back to specific FHIR resource ids), `ReviewDecision` (reviewer, outcome, timestamp).

**Key interactions**:
- `POST /api/v1/criteria` — define/version a new set of intervention criteria.
- `POST /api/v1/cohort-runs` — launch a batch run against a population for a given criteria version; returns immediately with a run id (async — this is never a synchronous call).
- `GET /api/v1/cohort-runs/{runId}` — run status/progress.
- `GET /api/v1/cohort-runs/{runId}/candidates` — ranked candidate list once available.
- `POST /api/v1/candidates/{candidateId}/decision` — record a care-team review decision.

**FHIR resource mapping**: `Patient` (identity resolution across fragmented sources), `Condition`, `Observation`, `MedicationRequest` (the clinical facts eligibility criteria are evaluated against), `DocumentReference`/`Binary` (unstructured source material, when a criterion can't be evaluated from structured data alone).

**LLM orchestration — the key architectural decision in this project**: eligibility is decided by a **deterministic rule engine** (structured criteria evaluated against structured FHIR facts, auditable and reproducible). Claude is used **only** to draft the human-readable rationale/evidence summary presented to the care team — it never decides who is eligible. This is a deliberate choice to avoid clinical-decision hallucination risk in a workflow whose whole point is identifying candidates for high-stakes interventions. If the rationale-generation call fails, the candidate is still surfaced (with its deterministic match reasons) — a missing narrative rationale does not hide a valid match.

**Identity resolution**: since sources are fragmented by design in this project, patient identity resolution (matching records across source systems to the same real patient) is a first-class, explicitly-deterministic step before eligibility matching runs — not an LLM task.

## 5. Security & Compliance

- **Access control**: Azure Entra ID-backed OAuth2/OIDC; RBAC roles `program_manager` (define criteria, launch runs), `care_coordinator`/`clinician` (review candidates, record decisions), `admin` (config only). Because this project touches a whole population rather than one physician's own patients, criteria-definition and run-launch actions require an elevated role, separate from candidate-review access.
- **Audit logging**: every criteria version, every cohort-run launch, every rationale-generation call, and every review decision emits an `AuditEvent` — population-scale runs make audit granularity (per-patient-per-run, not just per-run) especially important here.
- **Encryption**: TLS 1.2+ everywhere; Postgres/Blob encrypted at rest.
- **HIPAA technical-safeguards mapping**: Access Control → Entra ID + RBAC with elevated-role gating on population-wide actions; Audit Controls → per-patient-per-run AuditEvent granularity; Integrity → versioned, immutable criteria (a run's results are always traceable to a specific criteria version); Transmission Security → TLS.
- **Production-readiness gap**: real PHI use requires a BAA covering the Anthropic API, as with every project in this portfolio.

## 6. Requirements & Spec

**Functional requirements**:
- FR-1: A program manager can define and version a set of intervention criteria.
- FR-2: A cohort run evaluates every patient in a defined population against a specific criteria version, deterministically.
- FR-3: Each matched candidate receives a Claude-drafted rationale citing specific evidence; rationale failure does not remove the candidate from results.
- FR-4: A care team member can record a review decision per candidate.
- FR-5: Every cohort run's results are reproducible given the same population and criteria version (deterministic matching, no non-determinism from the LLM in the eligibility decision itself).

**Non-functional requirements — Performance**:
- Cohort-run throughput: ≥ 500 patients/minute per worker-pool replica during the deterministic-matching phase; rationale generation for matched candidates only (a small fraction of the population) at ≤ 3 seconds per candidate, parallelized across workers.
- A 100,000-patient population run completes deterministic matching in ≤ 4 hours at default worker-pool size, autoscaling to reduce this under load-driven scale-out.
- API responsiveness (status/candidate-list endpoints, not the batch job itself): p95 ≤ 500ms.

**Other NFRs**:
- Reliability/Availability: worker jobs must be resumable/idempotent — a worker crash mid-run must not require restarting the whole population from scratch; 99.9% API availability target.
- Security: see §5.
- Observability: run-level progress metrics (patients processed/remaining), per-run error rate for both the matching engine and the rationale-generation step tracked separately (since a matching-engine error is a correctness issue, a rationale-generation error is not).

**Acceptance criteria**: a cohort run against a seeded synthetic population of known composition returns exactly the expected deterministic matches for a fixed criteria version (reproducibility check); a simulated rationale-generation failure for one candidate does not remove that candidate from the results; re-running the same population against the same criteria version twice yields identical match sets.

## 7. Differentiator

Compared to **Carta Healthcare** (the closest sibling — also multi-source, document-heavy): Qualified Health's contract is **population coverage and precision of a ranked eligibility list** — did we correctly identify the right candidates out of a large population. Carta's contract is **per-field extraction accuracy** against a fixed registry schema for documents that have already been identified as in-scope. Qualified Health asks "who qualifies," Carta asks "what does this document say, exactly."

## 8. Assumptions & Out of Scope

- Assumes a BAA covering the Anthropic API is in place for any real (non-synthetic) PHI use.
- Out of scope: enrollment/consent workflows once a candidate is identified — this system stops at "surfaced for care-team review," not enrollment execution.
- Out of scope: real-time/streaming cohort screening — this is a batch workload by design; a newly-added patient is picked up on the next run, not instantly.
- Assumes fragmented source systems each expose a FHIR R4 API; does not design around non-FHIR legacy sources beyond the DocumentReference/Binary escape hatch for unstructured material.
