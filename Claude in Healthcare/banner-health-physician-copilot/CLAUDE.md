# Banner Health — Physician Copilot

## 1. Overview

A reference architecture for an AI clinical assistant that drafts visit documentation and summarizes patient records on demand, so physicians spend less time on paperwork per encounter. Inspired by Banner Health's published "reducing physician burnout at scale" case study. This is a **simulated, representative reference architecture** — it is not an integration with Banner Health's actual systems, and any patient data referenced anywhere in this design is synthetic.

Core use cases:
- **Draft a visit note** from a completed encounter's structured record, for physician review/edit/sign-off (never auto-signed).
- **Summarize a patient's record** on demand, ahead of a visit, so a physician can get oriented quickly.

## 2. System Context

**Actors**: Physician (primary user — initiates drafting/summarization, reviews and signs output), Nurse/Care Coordinator (read-only summary access), Compliance/Audit Reviewer (reviews audit trail, not a runtime actor), System/Service Account (batch or integration calls).

**External systems**: a FHIR R4-compliant EHR (Epic, Oracle Health/Cerner, or any FHIR R4 source — integration is EHR-agnostic by design), Claude via the Anthropic API (LLM), Azure platform services (identity, storage, secrets).

## 3. Architecture (HLD)

**Logical components**:
- API layer (physician-facing REST API + a thin browser UI) — auth'd, RBAC-gated.
- Note-drafting service — pulls the relevant FHIR resources for an encounter, constructs a grounded prompt, calls Claude, returns a structured draft.
- Summarization service — pulls a patient's longitudinal FHIR record, calls Claude for a condensed clinical summary.
- FHIR integration layer — read-only FHIR R4 client against the EHR's FHIR API.
- Data store — persists draft notes, summaries, and review/sign-off state (not the source-of-truth clinical record — that stays in the EHR).
- Audit & observability layer — structured audit log of every PHI access and every LLM call.

**Azure topology**: Azure Container Apps (API + services, autoscaled on concurrent request count), Azure Database for PostgreSQL – Flexible Server (draft/summary/audit state), Azure Key Vault (API keys, signing secrets), Azure Entra ID (physician/staff identity, OAuth2/OIDC), Azure Cache for Redis (session + short-TTL FHIR-response cache), Azure Blob Storage (signed-note archival export), Azure Monitor / Log Analytics (audit and operational logs).

**Data flow — draft note**: Physician selects encounter → API fetches Encounter + linked Condition/Observation/MedicationRequest/Procedure resources from FHIR → note-drafting service builds a grounded prompt (structured clinical facts, not free text guessing) → Claude returns a structured SOAP-style draft → draft stored, returned to physician for edit → physician signs → signed note persisted and audit-logged.

**Data flow — summarize**: Physician requests summary for a patient → API fetches the patient's recent longitudinal FHIR resources → summarization service calls Claude with the structured record → condensed summary returned and cached (short TTL) for the session.

**Scalability & HA**: Container Apps scale out on concurrent request count and queue depth; stateless services behind Azure Front Door/Load Balancer; Postgres Flexible Server configured with a paired read replica for reporting/audit queries so they don't contend with the write path; multi-AZ deployment within the primary Azure region, with a warm-standby region for DR (RPO/RTO targets in §6).

## 4. Design Detail (LLD)

**Core entities**: `Encounter` (mirrors FHIR Encounter, by reference), `DraftNote` (status: `drafted` → `edited` → `signed`; holds the generated text, the physician-edited text, and a diff), `SummaryRequest` (patient ref, generated summary, generated-at timestamp, TTL), `AuditEvent` (actor, action, resource type/id, outcome, timestamp).

**Key interactions**:
- `POST /api/v1/encounters/{encounterId}/draft-note` — triggers drafting; returns `{draftNoteId, status, draftText}`. Requires role `physician`.
- `GET /api/v1/notes/{draftNoteId}` — fetch current draft/edited/signed state.
- `PATCH /api/v1/notes/{draftNoteId}` — physician edit, stores diff against the original draft.
- `POST /api/v1/notes/{draftNoteId}/sign` — finalizes; write is immutable after this point.
- `POST /api/v1/patients/{patientId}/summary` — triggers summarization; returns `{summaryId, summaryText, generatedAt}`.

**FHIR resource mapping**: `Encounter` (visit context), `Condition` (problem list), `Observation` (vitals/labs), `MedicationRequest` (active meds), `Procedure` (recent procedures), `DocumentReference` (prior notes, for summarization context) — all read-only.

**LLM orchestration**: a versioned system prompt constrains Claude to only state facts present in the supplied structured FHIR data (no inference of facts not in the record); output is requested as structured sections (Subjective/Objective/Assessment/Plan) rather than free text, so the API layer can validate shape before showing it to the physician. Every draft/summary is traceable to the prompt-template version that produced it. On an LLM timeout or error, the service returns a clear "drafting failed, try again" state rather than a partial/garbled note — never silently returns an empty or truncated draft as if it succeeded.

**PHI boundary**: the structured clinical facts needed for the note/summary are sent to Claude via the Anthropic API — that's the point of the call. What's explicitly *not* sent: other patients' data, and any FHIR fields not relevant to the requested flow (data minimization at the prompt-construction step).

## 5. Security & Compliance

- **Access control**: Azure Entra ID-backed OAuth2/OIDC; RBAC roles `physician` (draft/sign/summarize for their own patients), `care_coordinator` (read-only summary), `admin` (no direct PHI access, manages config only). Every route touching patient data requires both a valid token and an explicit role check — not just "logged in."
- **Audit logging**: every FHIR read, every Claude call, and every note sign-off emits an `AuditEvent` (actor, action, resource, outcome, timestamp) to Azure Monitor/Log Analytics, retained per the organization's compliance retention policy.
- **Encryption**: TLS 1.2+ in transit everywhere (API, FHIR client, Anthropic API calls); Postgres and Blob Storage encrypted at rest via Azure-managed keys, with the option to bring your own key via Key Vault.
- **HIPAA technical-safeguards mapping**: Access Control → Entra ID + RBAC; Audit Controls → AuditEvent stream; Integrity → immutable signed-note state (no post-sign mutation); Transmission Security → TLS everywhere.
- **Production-readiness gap (explicit, not glossed over)**: using Claude via the Anthropic API with real PHI in production requires a signed Business Associate Agreement (BAA) covering the API and appropriate data-handling terms — this reference architecture assumes that is in place; it is not something this design "solves," and is called out here as a precondition rather than assumed away.

## 6. Requirements & Spec

**Functional requirements**:
- FR-1: A physician can request a draft note for any encounter they are the attending clinician for.
- FR-2: A draft note must never be presented as final/signed without explicit physician sign-off.
- FR-3: A physician can request a condensed summary of a patient's longitudinal record.
- FR-4: Every draft/summary/sign action is attributable to a specific user and timestamp in the audit trail.
- FR-5: The system must degrade gracefully (explicit failure state, not a fabricated partial note) on LLM or FHIR-source unavailability.

**Non-functional requirements — Performance** (concrete targets, not aspirational):
- Draft-note generation: p95 end-to-end latency ≤ 8 seconds for a typical encounter (≤ 20 relevant FHIR resources).
- Summary generation: p95 end-to-end latency ≤ 6 seconds for a patient with ≤ 5 years of longitudinal history.
- Sustained throughput: ≥ 50 concurrent draft/summary requests per Container Apps replica set at the above latency targets, autoscaling beyond that.
- FHIR read round-trip (cached): p95 ≤ 300ms; (uncached): p95 ≤ 1.5s.

**Other NFRs**:
- Reliability/Availability: 99.9% monthly API availability target; RPO ≤ 15 minutes, RTO ≤ 4 hours for the DR region.
- Security: see §5 in full.
- Observability: structured request tracing with correlation IDs from API entry through FHIR/LLM calls; `/metrics` exposing request-count, latency histograms, and LLM-call error rate.

**Acceptance criteria**: a draft-note request against a seeded synthetic encounter returns a structured SOAP draft within the latency target; an attempt to sign without an existing draft is rejected; every one of the above flows produces a matching AuditEvent; a simulated LLM timeout produces the explicit failure state from FR-5, not a partial note.

## 7. Differentiator

Compared to **Commure** (the closest sibling — also clinical-documentation automation): Banner Health's flow is **synchronous and physician-initiated** — a clinician explicitly requests a draft for a specific encounter or a summary for a specific patient, reviews it, and edits/signs it before anything is finalized. Commure, by contrast, is a **continuous, passive ambient-capture** system running during the live encounter itself, at a much larger multi-tenant scale. Banner Health has no audio/streaming component at all.

## 8. Assumptions & Out of Scope

- Assumes a BAA covering the Anthropic API is in place for any real (non-synthetic) PHI use — see §5.
- Out of scope: scheduling, billing/coding, e-prescribing, and any EHR write-back beyond the signed-note artifact itself.
- Out of scope: real-time collaborative editing of a draft by multiple physicians.
- Assumes the source EHR's FHIR R4 API is available and read-accessible; does not design around a non-FHIR legacy interface.
