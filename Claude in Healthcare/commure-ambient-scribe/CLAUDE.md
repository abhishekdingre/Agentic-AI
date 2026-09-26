# Commure — Ambient Scribe

## 1. Overview

A reference architecture for a system that automates clinical documentation generation directly from patient encounters, at multi-tenant scale — a continuous, passive **ambient scribe** that listens during a live encounter and produces a draft note without the clinician stopping to type or dictate. Inspired by Commure's published "clinical documentation automation at scale" case study. This is a **simulated, representative reference architecture** — it is not an integration with Commure's actual systems, and any patient/audio data referenced anywhere in this design is synthetic.

Core use case: capture audio for the duration of a clinical encounter, transcribe it in near-real-time, and generate a structured draft note from the transcript — across **many health-system tenants simultaneously**, which is the defining architectural difference from this portfolio's other documentation project (Banner Health).

## 2. System Context

**Actors**: Clinician (starts/stops an encounter session, reviews and signs the generated note), Tenant Admin (manages their health system's org config, users, EHR connection — scoped to their own tenant only), Compliance/Audit Reviewer (non-runtime, may operate at tenant or platform level), Platform Operator (cross-tenant operational role, no PHI access by default).

**External systems**: a FHIR R4-compliant EHR **per tenant** (multi-tenant, not a single EHR relationship), a streaming speech-to-text provider (external to this design — treated as a swap-in dependency, not built here), Claude via the Anthropic API, Azure platform services.

## 3. Architecture (HLD)

**Logical components**:
- API/session layer — starts/stops encounter sessions, tenant-scoped throughout (every request carries and is authorized against a tenant context).
- Audio-ingestion service — accepts streamed audio chunks for an active session, forwards to the speech-to-text provider.
- Transcription-assembly service — assembles streaming transcript segments into a coherent per-session transcript as the encounter proceeds.
- Note-generation service — once a session ends (or on demand mid-session), calls Claude with the assembled transcript to produce a structured draft note.
- Tenant-management service — org/user/EHR-connection config, strictly partitioned per tenant.
- Data store — encounter sessions, audio-chunk metadata (not raw audio at rest beyond a short processing window — see §5), transcript segments, generated notes, audit log; every row carries a `tenantOrgId`.

**Azure topology**: Azure Container Apps (session/API layer, audio-ingestion layer — each scaled independently since audio-ingestion concurrency and note-generation load don't move together), Azure Database for PostgreSQL – Flexible Server (**tenant-partitioned** — row-level tenant isolation enforced at the query layer, with the option to move to per-tenant schemas or databases at larger scale), Azure Service Bus (transcript-assembly → note-generation handoff, decoupling real-time ingestion from note drafting), Azure Cache for Redis (active-session state), Azure Blob Storage (short-retention encrypted audio staging, per-tenant containers), Azure Key Vault (per-tenant secrets, e.g. tenant-specific FHIR credentials), Azure Entra ID (supports multi-tenant identity — tenant-scoped app registrations or an equivalent claims-based tenant boundary), Azure Monitor / Log Analytics (per-tenant-scoped log queries, plus platform-level aggregate health).

**Data flow — ambient encounter**: Clinician starts an `EncounterSession` (tenant-scoped) → audio streams in as `AudioChunk`s → speech-to-text provider returns streaming text → transcription-assembly service appends `TranscriptSegment`s in near-real-time → clinician ends the session → note-generation service calls Claude with the full assembled transcript → `GeneratedNote` (draft) returned for clinician review/edit/sign — same never-auto-signed principle as Banner Health.

**Scalability & HA — this is the architecturally distinct part of this project**: audio-ingestion concurrency scales independently from note-generation load (Container Apps autoscaled on separate signals — concurrent active sessions for ingestion, Service Bus queue depth for note-generation); the system is designed for **many tenants concurrently**, not just many encounters for one tenant — noisy-neighbor isolation matters (a burst of activity from one large tenant must not degrade latency for a smaller tenant, addressed via per-tenant rate limiting/quotas at the API layer); multi-AZ primary region, warm-standby DR; because live audio capture is in-progress, a mid-session failure must not silently drop already-captured transcript — partial transcripts are checkpointed, not held only in memory.

## 4. Design Detail (LLD)

**Core entities**: `TenantOrg` (health-system tenant, config, EHR connection details), `EncounterSession` (tenant ref, clinician ref, patient ref, status: `active`→`ended`→`note_generated`, start/end timestamps), `AudioChunk` (session ref, sequence number, short-TTL storage ref — metadata persisted, raw audio purged after processing per §5), `TranscriptSegment` (session ref, sequence, text, timestamp offset), `GeneratedNote` (session ref, status: `drafted`→`edited`→`signed`, text, diff against original draft).

**Key interactions**:
- `POST /api/v1/tenants/{tenantOrgId}/encounter-sessions` — start a session; every subsequent call is scoped to this session and its tenant.
- `POST /api/v1/encounter-sessions/{sessionId}/audio-chunks` — stream audio (or a signed upload-URL pattern for chunked streaming, depending on transport).
- `POST /api/v1/encounter-sessions/{sessionId}/end` — ends capture, triggers note generation.
- `GET /api/v1/encounter-sessions/{sessionId}/note` — fetch the generated draft/edited/signed note.
- `PATCH` / `POST .../note/sign` — same edit/sign contract as Banner Health's `DraftNote`.

**FHIR resource mapping**: `Encounter` (session tied to a specific visit context per tenant's EHR), `DocumentReference` (the eventual signed-note write-back target, per tenant) — read scope is intentionally minimal here since the primary input is the live transcript, not the existing record (unlike Banner Health, which grounds drafting in structured FHIR facts).

**LLM orchestration**: Claude receives the assembled transcript (not raw audio) and produces a structured draft note; because the input is free-form speech rather than structured clinical facts, the drafting prompt explicitly instructs Claude to flag uncertain/inaudible passages rather than guess, and the resulting note is always presented as a draft requiring clinician review — never auto-filed. On note-generation failure, the transcript itself remains available and retrievable so the encounter isn't lost even if drafting must be retried.

**Multi-tenancy isolation — the key design concern in this project**: every entity carries a `tenantOrgId`; every query is scoped by tenant at the data-access layer (not just filtered in application code after a broader fetch); tenant-scoped API credentials/roles mean a compromised credential from one tenant cannot read another tenant's sessions, transcripts, or notes.

## 5. Security & Compliance

- **Access control**: Azure Entra ID-backed OAuth2/OIDC with tenant-scoped claims; RBAC roles `clinician` (own sessions within their tenant), `tenant_admin` (org/user/EHR config within their own tenant only, no cross-tenant access), `platform_operator` (operational visibility, explicitly no PHI access by default). Every data-access path enforces tenant scope as well as role.
- **Audit logging**: every session start/end, every note generation/edit/sign, and every tenant-admin config change emits an `AuditEvent` tagged with `tenantOrgId`; audit queries are themselves tenant-scoped for a tenant's own compliance reviewer, with a separate platform-level aggregate view for cross-tenant operational audit.
- **Encryption**: TLS 1.2+ everywhere, including the audio-streaming transport; audio staged in Blob Storage is encrypted at rest and **retained only for the short window needed for transcription processing**, then purged — raw audio is not a long-term retained artifact, only the resulting transcript and note are.
- **HIPAA technical-safeguards mapping**: Access Control → Entra ID + RBAC + tenant scoping; Audit Controls → tenant-tagged AuditEvent stream; Integrity → immutable signed-note state, checkpointed transcripts (no silent data loss mid-session); Transmission Security → TLS on both the audio stream and API traffic.
- **Multi-tenant isolation as a compliance control, not just a scalability one**: tenant data isolation is itself a HIPAA-relevant boundary (one tenant's PHI must never be visible to another) — enforced at the data-access layer, not assumed from application-level filtering alone.
- **Production-readiness gap**: real PHI use requires a BAA covering the Anthropic API, as with every project in this portfolio; additionally, the speech-to-text provider (external dependency, not built here) requires its own BAA/data-handling review before real audio containing PHI is sent to it — this is a second, separate BAA surface unique to this project.

## 6. Requirements & Spec

**Functional requirements**:
- FR-1: A clinician can start and end an encounter session scoped to their tenant.
- FR-2: Audio is transcribed into a per-session transcript in near-real-time as the session proceeds.
- FR-3: A structured draft note is generated from the assembled transcript on session end, and must never be presented as final without explicit clinician sign-off.
- FR-4: Tenant data (sessions, transcripts, notes, audit events) is never accessible across tenant boundaries, under any role.
- FR-5: A mid-session failure preserves already-captured transcript segments; note generation can be retried without re-capturing audio.

**Non-functional requirements — Performance**:
- Transcription latency: streaming transcript segments available within ≤ 3 seconds of the corresponding audio being spoken (near-real-time, not batch).
- Note generation: p95 end-to-end latency ≤ 10 seconds from session-end to draft-note-available for a typical encounter-length transcript (≤ 30 minutes of speech).
- Multi-tenant throughput: the platform sustains ≥ 1,000 concurrent active encounter sessions across tenants at the above latency targets, with per-tenant rate limiting/quotas preventing one tenant's burst from degrading another's latency (the noisy-neighbor requirement is itself a testable NFR here).
- Audio-ingestion availability: no audio-chunk loss under normal operation; a transient ingestion failure is retried, not silently dropped.

**Other NFRs**:
- Reliability/Availability: 99.95% target for the session/ingestion path (higher than this portfolio's other projects, given live-capture-in-progress cannot simply be retried later); RPO ≤ 5 minutes for transcript data specifically (tighter than note/draft data, since transcript loss cannot be regenerated from anything else).
- Security: see §5, including the multi-tenant isolation requirement.
- Observability: per-tenant latency/error-rate dashboards (not just platform-aggregate) so one tenant's degraded experience is visible even if the aggregate looks healthy.

**Acceptance criteria**: a seeded synthetic encounter session produces a transcript within the streaming-latency target and a structured draft note within the note-generation latency target; a simulated audio-ingestion hiccup does not lose already-captured transcript segments; a query executed under Tenant A's credentials for a Tenant B session id is rejected, not just filtered; a simulated burst of sessions on one tenant does not push another tenant's p95 latency past its target.

## 7. Differentiator

Compared to **Banner Health** (the closest sibling — also clinical-documentation automation): Commure's flow is a **continuous, passive ambient-capture** system running during the live encounter itself, with a **multi-tenant** architecture serving many health systems at once and a genuine live-audio-streaming component. Banner Health's flow is **synchronous and physician-initiated** — a clinician explicitly requests a draft for a specific encounter from existing structured FHIR data, with no audio/streaming component and no multi-tenant isolation concern (single-organization deployment assumed). Commure's hard problems are streaming-transcription latency and tenant isolation at scale; Banner Health has neither.

## 8. Assumptions & Out of Scope

- Assumes a BAA covering the Anthropic API, and a separate BAA/data-handling review covering the speech-to-text provider, are both in place for any real (non-synthetic) audio/PHI use.
- Out of scope: the speech-to-text engine itself — treated as an external swap-in dependency with a defined interface, not built as part of this design.
- Out of scope: scheduling, billing/coding, e-prescribing, or any EHR write-back beyond the signed-note artifact per tenant's FHIR `DocumentReference`.
- Out of scope: cross-tenant analytics or benchmarking features — tenant data isolation is treated as absolute, with no aggregate-across-tenants product surface in this design.
- Assumes each tenant's EHR exposes a FHIR R4 API; does not design around a non-FHIR legacy interface per tenant.
