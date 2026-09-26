# Elation Health — Chart Review Accelerator

## 1. Overview

A reference architecture for an AI layer that accelerates chart review and reduces documentation burden for primary-care clinicians, ahead of and during a visit. Inspired by Elation Health's published "61% less time on chart review" case study. This is a **simulated, representative reference architecture** — it is not an integration with Elation Health's actual EHR platform, and any patient data referenced anywhere in this design is synthetic.

**Framing assumption, stated explicitly**: Elation Health's real product is a full primary-care EHR platform. This design does **not** attempt to rebuild an EHR. It scopes strictly to the AI-powered chart-review-acceleration layer that would sit within/alongside a primary-care EHR — chart summarization, problem-list reconciliation, and review-time instrumentation — not scheduling, billing, e-prescribing, or any other EHR-platform function.

Core use cases:
- **Generate/refresh a chart summary** for a patient ahead of a visit — a condensed view of the record's current-relevant state.
- **Synthesize a reconciled problem list** from the patient's raw FHIR `Condition` entries, deduplicating and flagging conflicts.
- **Instrument review time** so the acceleration effect is actually measurable, not just assumed.

## 2. System Context

**Actors**: Primary-care Clinician (reads chart summaries/problem lists, drives review sessions), Care Team Staff (read-only access to summaries), Compliance/Audit Reviewer (non-runtime, reviews audit trail).

**External systems**: a FHIR R4-compliant EHR (the primary-care platform this layer sits alongside — EHR-agnostic by design), Claude via the Anthropic API, Azure platform services.

## 3. Architecture (HLD)

**Logical components**:
- API layer (embeds into/alongside the EHR's chart-review UI via API — no standalone clinician-facing app assumed).
- Chart-summarization service — pulls a patient's current-relevant FHIR resources, calls Claude for a condensed summary.
- Problem-list synthesis service — pulls raw `Condition` resources, deduplicates/reconciles near-duplicates and flags conflicts, optionally uses Claude to draft a human-readable reconciliation rationale.
- Review-telemetry service — records review-session start/end and interaction events, computing time-in-review metrics.
- FHIR integration layer — read-only FHIR R4 client.
- Data store — chart summaries, problem-list synthesis results, review-session telemetry, audit log.

**Azure topology**: Azure Container Apps (autoscaled on concurrent review sessions), Azure Database for PostgreSQL – Flexible Server, Azure Key Vault, Azure Entra ID, Azure Cache for Redis (summary/problem-list cache, since the same patient is often reviewed repeatedly in a short window), Azure Monitor / Log Analytics.

**Data flow — chart summary**: Clinician opens a patient chart in the EHR → EHR (or this layer's UI panel) requests a summary → service fetches current-relevant FHIR resources (`Condition`, `Observation`, `MedicationRequest`, recent `DocumentReference`) → Claude call produces a condensed summary → cached (short TTL) and returned; a manual "regenerate" action bypasses the cache.

**Data flow — problem-list synthesis**: triggered on chart open or on demand → service fetches all `Condition` resources → deterministic dedup/conflict-detection logic groups near-duplicates and flags contradictions (e.g. "active" vs. "resolved" status conflicts across sources) → Claude drafts a short human-readable rationale for any flagged conflict → reconciled list + rationale returned.

**Scalability & HA**: Container Apps autoscale on concurrent review-session count; Redis cache absorbs repeated-view load within a single clinic day; Postgres Flexible Server with a read replica for the review-telemetry reporting path so it never contends with the live-review write path; multi-AZ primary region, warm-standby DR region.

## 4. Design Detail (LLD)

**Core entities**: `ChartSummary` (versioned — each regeneration is a new version, prior versions retained for audit), `ProblemListSynthesis` (reconciled list + flagged conflicts + rationale, linked to the raw `Condition` ids it reconciled), `ReviewSession` (clinician id, patient id, start/end timestamps, interaction-event count), `ChartFlag` (a specific conflict or data-quality issue surfaced during synthesis, with a resolved/unresolved state).

**Key interactions**:
- `GET /api/v1/patients/{patientId}/chart-summary` — returns the current cached summary version, or triggers generation if none exists.
- `POST /api/v1/patients/{patientId}/chart-summary/regenerate` — forces a fresh summary, bypassing cache.
- `GET /api/v1/patients/{patientId}/problem-list-synthesis` — returns the reconciled list + flags.
- `POST /api/v1/review-sessions` / `PATCH /api/v1/review-sessions/{id}` — start/end a review session for telemetry.
- `GET /api/v1/review-sessions/metrics` — aggregate time-in-review metrics (for measuring the burden-reduction effect).

**FHIR resource mapping**: `Condition` (problem list — the primary reconciliation target), `Observation`, `MedicationRequest`, `DocumentReference` (prior notes feeding the chart summary) — all read-only.

**LLM orchestration**: chart-summary prompts are constrained to current-relevant facts only (not the full historical record — that's a deliberate scoping choice for readability); problem-list conflict rationale is drafted by Claude but the conflict *detection* itself is deterministic logic (string/code matching + status comparison across `Condition` entries) — Claude explains conflicts a human already flagged, it does not decide what counts as a conflict. On LLM failure, the reconciled list from deterministic logic is still returned, just without the natural-language rationale — the clinically load-bearing part of this flow does not depend on the LLM succeeding.

**PHI boundary**: only current-relevant structured facts (not full raw record dumps) are sent to Claude, both for summary quality and data minimization.

## 5. Security & Compliance

- **Access control**: Azure Entra ID-backed OAuth2/OIDC; RBAC roles `clinician` (full read + regenerate), `care_team_staff` (read-only summary, no regenerate, no problem-list synthesis access), `admin` (config only, no PHI). Every route validates role against the specific action, not just presence of a token.
- **Audit logging**: every chart-summary generation/regeneration, every problem-list synthesis run, and every review-session start/end emits an `AuditEvent`.
- **Encryption**: TLS 1.2+ everywhere; Postgres/Blob encrypted at rest via Azure-managed (or Key-Vault-supplied) keys.
- **HIPAA technical-safeguards mapping**: Access Control → Entra ID + RBAC; Audit Controls → AuditEvent stream; Integrity → versioned summaries (no silent overwrite of prior versions); Transmission Security → TLS.
- **Production-readiness gap**: real PHI use requires a BAA covering the Anthropic API, as with every project in this portfolio — assumed in place, not solved by this design.

## 6. Requirements & Spec

**Functional requirements**:
- FR-1: A clinician can retrieve a chart summary for any patient on their care team, generating one if none exists.
- FR-2: A clinician can force-regenerate a summary on demand.
- FR-3: The system produces a reconciled problem list that deduplicates near-identical `Condition` entries and flags genuine conflicts.
- FR-4: Review-session start/end and interaction counts are recorded to support time-in-review measurement.
- FR-5: Problem-list conflict detection must not depend on the LLM succeeding — deterministic detection continues to function even if Claude is unavailable.

**Non-functional requirements — Performance**:
- Chart-summary generation (cache miss): p95 end-to-end latency ≤ 5 seconds.
- Chart-summary retrieval (cache hit): p95 ≤ 150ms.
- Problem-list synthesis (deterministic portion only): p95 ≤ 500ms; full synthesis with LLM rationale: p95 ≤ 4 seconds.
- Sustained throughput: ≥ 80 concurrent chart opens per replica set at the above targets (chart review is a higher-frequency action than Banner Health's note drafting).

**Other NFRs**:
- Reliability/Availability: 99.9% monthly availability target; RPO ≤ 15 minutes, RTO ≤ 4 hours.
- Security: see §5.
- Observability: correlation-ID tracing per request; `/metrics` exposing cache hit rate (a first-order performance signal for this specific workload) alongside latency histograms.

**Acceptance criteria**: a chart-summary request against a seeded synthetic patient returns within the cache-miss latency target on first call and the cache-hit target on a repeat call within the cache TTL; a patient with two synthetic near-duplicate `Condition` entries produces a synthesized list with exactly one reconciled entry and a flagged rationale; disabling the mock LLM still yields a valid deterministic problem list per FR-5.

## 7. Differentiator

Compared to **Banner Health** (the closest sibling — also physician-facing AI over clinical records): Elation Health's flow is **read/reconcile/review-speed-focused** — it accelerates review of data that already exists in the chart and produces no new documentation artifact of its own. Banner Health's flow is **write/draft-focused** — it produces a new signed note tied to a specific encounter. A clinician using this layer never signs anything; they only review faster.

## 8. Assumptions & Out of Scope

- Assumes a BAA covering the Anthropic API is in place for any real (non-synthetic) PHI use.
- Out of scope (see framing assumption in §1): scheduling, billing/coding, e-prescribing, encounter documentation, or any other full-EHR function.
- Out of scope: automatic resolution of flagged conflicts — a human always makes the final call; this layer surfaces and explains, it does not silently reconcile.
- Assumes the source EHR's FHIR R4 API is available and read-accessible.
