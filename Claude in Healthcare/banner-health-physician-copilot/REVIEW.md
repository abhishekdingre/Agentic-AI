# Review — Banner Health Physician Copilot

## Security Review

**Project:** banner-health-physician-copilot/CLAUDE.md

### P1

1. **"Own patients" scoping is claimed in Security & Compliance but not architecturally enforced anywhere.**
   Where: §5 Security & Compliance ("Access control") vs. §4 Design Detail (LLD) "Key interactions" vs. §6 FR-1.
   §5 states the RBAC model gives `physician` the right to "draft/sign/summarize for **their own patients**" — this is the load-bearing security property for the whole app (it's what stops any credentialed physician from pulling any other patient's PHI). But the concrete endpoint specs in §4 only say `POST /api/v1/encounters/{encounterId}/draft-note` "Requires role `physician`"; `POST /api/v1/patients/{patientId}/summary` has no role/ownership check stated at all. Neither mentions checking the authenticated physician's identity against the encounter's attending clinician (or a FHIR CareTeam/Practitioner linkage). FR-1 explicitly requires "for any encounter they are the attending clinician for," but no section describes *how* that attribute-level check happens. As designed, a coarse role check (`physician`) is the only gate on `{encounterId}`/`{patientId}` path parameters, meaning any physician-role holder could request a draft/summary for any patient by guessing/enumerating IDs.
   Suggested direction: name the concrete mechanism — e.g., "the note-drafting service resolves the Encounter's attending Practitioner via FHIR CareTeam and rejects if it doesn't match the caller's Entra ID-mapped practitioner identity" — and state it as a per-request authorization step, not just a role claim.

### P2

1. **Audit-logging story doesn't clearly cover the `edited` state or cached/internal reads.**
   Where: §5 ("Audit logging") vs. §4 (`DraftNote` state machine, `PATCH` endpoint, cached summaries). §5 enumerates exactly three triggers: "every FHIR read, every Claude call, and every note sign-off." The `PATCH` edit step and TTL-cached summary reads aren't in that list, even though the HLD's broader claim ("audit log of every PHI access") would seem to require them. Broaden §5's list to explicitly include edit and read/cache-hit events, or state why they're intentionally excluded.
2. **Redis (holds cached PHI) has no encryption/access-control treatment in §5.**
   Where: §3 Azure topology vs. §5 ("Encryption"). §5's encryption bullet names only Postgres and Blob Storage at rest; the Redis cache explicitly stores FHIR-response PHI and session state but is absent from the encryption story.
3. **`care_coordinator` role is unscoped relative to `physician`'s "own patients" framing.**
   Where: §5 RBAC roles vs. §2 System Context. As written this reads as blanket read access to every patient's summary for every care coordinator in the org, unless coordinator-to-patient assignment scoping is intended but left unstated.
4. **System/Service Account actor named in System Context has no access story anywhere.**
   Where: §2 vs. §5. No role, endpoint, or access-control statement for this actor appears anywhere in §3–§5's RBAC role list. Either remove it or add at least a sentence on its access.

### P3

1. **Audit trail's own access control/integrity is unaddressed.** Worth a one-line addition on how Azure Monitor/Log Analytics access is controlled for the Compliance/Audit Reviewer, and how the AuditEvent stream is protected against tampering.
2. **`GET`/`PATCH` note endpoints don't restate a role requirement** — likely covered by §5's blanket rule, but calling it out per-route removes ambiguity.

**Note:** the PHI-boundary-around-the-LLM-call story and the BAA/production-readiness gap acknowledgment are both handled well — no finding needed there. Multi-tenancy isolation is out of scope for this project and correctly not claimed.

## Performance Review

**Project**: banner-health-physician-copilot/CLAUDE.md

### P1 — Targets not testable or not plausible against the described architecture

1. **Draft-note 8s p95 target is not reconcilable with the described FHIR fetch pattern.** *(Design Detail (LLD) — "Data flow — draft note" × Requirements & Spec — Performance)* The LLD never states whether the ≤20 FHIR resource fetches happen in parallel or sequentially. Using the Performance section's own uncached-FHIR p95 (≤1.5s), a sequential fetch of 20 resources could cost ~30s alone — well past the entire 8s budget — and most reads for a first touch of an encounter will be cache misses. Suggested direction: state the fetches are issued concurrently and add an internal latency budget breakdown (FHIR fetch, prompt construction, Claude call, persistence/audit-write) summing to within 8s.
2. **Summary NFR's scope bound doesn't produce a testable, fixed workload — unlike the draft-note NFR.** *(Requirements & Spec — Performance)* "≤5 years of longitudinal history" is not a resource-count bound, unlike draft-note's "≤20 relevant FHIR resources," so the 6s p95 target's pass/fail depends on which synthetic fixture is chosen. This is compounded by the summary target being *tighter* than draft-note's despite pulling a superset of resource types over a longer horizon. Suggested direction: bound the summary NFR by resource count too, and justify why its budget is tighter than draft-note's.

### P2 — Real gaps in scalability/HA or cost/latency reasoning

1. **No prompt-caching or model-tier discussion despite an ideal caching candidate.** The reused "versioned system prompt" is a textbook Anthropic prompt-caching candidate, but neither prompt caching nor a target model tier is named anywhere, making the 8s/6s targets impossible to sanity-check against a real cost/latency profile.
2. **Postgres HA story conflates read-scaling with failover.** The read replica is scoped to reporting/audit; the doc never states whether the *primary* instance runs zone-redundant HA/automatic failover.
3. **No per-call timeout values**, which undermines testability of both the latency targets and FR-5's failure-state behavior — add explicit timeout values per external call that sum to within the end-to-end p95 budget.

### P3 — Minor/micro-optimization notes

1. Redis cache failure mode (fail-open vs. fail-request) is unaddressed.
2. Throughput target lacks a max-replica/scale-ceiling statement.
3. No mention of streaming partial SOAP sections to reduce perceived latency.

### Not applicable
Workload-shape-fit (batch/streaming) doesn't apply — §7 explicitly positions this project as synchronous/physician-initiated, and its throughput NFR shape is appropriate.

## Spec-Compliance Review

**Target:** banner-health-physician-copilot/CLAUDE.md (all 8 sections read)
**Sibling cross-checked:** commure-ambient-scribe/CLAUDE.md (named in §7 Differentiator)

### P1

1. **FR-5's "FHIR-source unavailability" clause has no architectural backing anywhere in the doc.** Where: §6 FR-5 vs. §4 LLD and §6 acceptance criteria. The only concrete degrade-gracefully mechanism described covers LLM timeout/error only ("drafting failed, try again"); nothing addresses FHIR-source (EHR) unreachability — no timeout/retry/circuit-breaker, no fallback, no acceptance criterion. Half of a stated FR is completely unbacked. Suggested direction: add an explicit FHIR-unavailability failure path mirroring the LLM one, with a matching acceptance criterion, or narrow FR-5 to LLM-only.

### P2

1. **§3 "read-only FHIR R4 client" contradicts §8's out-of-scope wording implying EHR write-back.** §8 says "no EHR write-back **beyond** the signed-note artifact itself," implying the signed note *is* written back to the EHR, but §3/§4 describe no such write path — the signed note stays in the app's own Postgres/Blob. Likely reused near-verbatim from Commure's doc, where this genuinely is a write-back target. Reword §8 or add the write-back flow.
2. **FR-1's authorization criterion doesn't match §5's stated RBAC rule.** "Attending clinician for an encounter" (FR-1) vs. "their own patients" (§5) are two different concepts, neither tied to a concrete field/mechanism. Pick one and state the concrete data source it's checked against.
3. **Care Coordinator actor/role has no corresponding functional requirement.** Asserted in §2 and §5 but FR-3 only grants summary access to physicians. Add an FR or fold the coordinator's access into FR-3's wording.
4. **System/Service Account actor is named once and never used again** — vestigial as written; add the implied flow or remove the actor.
5. **No acceptance criterion tests RBAC/authorization-boundary enforcement**, unlike Commure's sibling doc which includes exactly this pattern (tenant-boundary rejection test). Add at least one AC exercising role-boundary rejection (e.g., a non-attending physician's request is rejected).

### P3

1. Blob Storage "signed-note archival export" (§3) has no FR/AC referencing it — minor scope drift.
2. The "sign without an existing draft is rejected" AC doesn't specify expected error shape/status code.
3. Commure's §7 states "single-organization deployment assumed" about Banner; Banner's own §8 doesn't say this explicitly (consistent, just not stated in its own words) — minor symmetry gap.

### Differentiator cross-check — passed, no contradiction found
Banner §7 and Commure §7 are mutually consistent: each side's own architecture backs its half of the claim (Banner has no audio/streaming component at all; Commure has an explicit audio pipeline and pervasive tenant partitioning, with NFRs bearing out the "much larger scale" claim).

### Section completeness — passed
All 8 required sections are present with substantive content.
