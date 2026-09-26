# Review — Commure Ambient Scribe

## Security Review

Project reviewed: commure-ambient-scribe/CLAUDE.md (all 8 sections read in full).

### P1

1. **Raw-audio purge claim is asserted three times, never backed by a named mechanism.** §5 states audio "is retained only for the short window needed for transcription processing, then purged," §4 echoes this on `AudioChunk`, and §3 adds only the adjective "short-retention" to the Blob Storage bullet. No section names an actual mechanism — no Blob lifecycle-management TTL, no explicit delete step tied to a pipeline event, no failure/retry path if the delete errors, and no statement on whether Blob soft-delete/versioning/backup is disabled for this container (any of which would silently keep recoverable copies past the stated window). Direction: name the mechanism (lifecycle-rule TTL, or an application-triggered delete keyed to transcription-assembly confirmation) plus a failure-handling story, and state the container's soft-delete/versioning/backup posture.
2. **The speech-to-text provider's PHI exposure gets materially less design rigor than Claude's, despite handling the riskier raw-audio surface.** §4's "LLM orchestration" paragraph specifies what crosses to Claude (assembled transcript, not raw audio), why, and a mitigation (flag uncertain/inaudible passages). The STT provider — which receives raw audio itself (voiceprints, background speakers, anything spoken) — gets no equivalent treatment: no statement of audio scope/duration sent, no minimization, no mention of the provider's own retention behavior. §5/§8 name the BAA gap for STT as "a second, separate BAA surface," which is good, but that's a legal control, not the architectural-minimization treatment Claude's boundary received. Direction: add a "speech-to-text boundary" LLD note parallel to "LLM orchestration."
3. **At-rest encryption is concrete only for the transient artifact (audio), silent for the long-term PHI store.** §5 explicitly says audio in Blob Storage is encrypted at rest — but audio is the one artifact the doc says is *not* retained long-term. The actual long-lived PHI (transcripts, notes, audit log in the tenant-partitioned Postgres store) is never stated to be encrypted at rest anywhere, including the HIPAA safeguards mapping, which has no at-rest-encryption row. Redis (may hold in-progress transcript text) has the same gap. Direction: add an explicit at-rest encryption statement for Postgres (and Redis if it holds PHI) to both the encryption bullet and the HIPAA mapping.

### P2

1. **Multi-tenant isolation relies on a single enforcement layer with no stated defense-in-depth.** The mechanism (every query tenant-scoped "at the data-access layer, not just filtered after a broader fetch," paired with a real acceptance criterion testing cross-tenant credential rejection) is more concrete than most docs' hand-wave — but nothing mentions a second, independent layer (e.g., Postgres row-level security bound to per-tenant DB roles) that would still block a cross-tenant read if the shared query-builder library ever had a bug. Direction: state RLS (or equivalent DB-enforced scoping) as a second layer, or explicitly accept the single-layer risk with compensating controls (mandatory test coverage, static analysis on tenant-predicate presence).
2. **Redis and Service Bus are absent from the multi-tenancy isolation discussion.** §3 calls out per-tenant Blob containers and per-tenant Key Vault secrets, but Redis (active-session state) and Service Bus (transcript-assembly → note-generation handoff) get no tenant-partitioning treatment, despite plausibly carrying transcript/PHI content mid-pipeline. Direction: state whether Redis keys/Service Bus messages are tenant-namespaced, or explicitly note they carry no tenant-identifiable PHI.
3. **Audit logging doesn't cover the transcript-retrieval read path implied by the failure-recovery story.** §4 states that on note-generation failure "the transcript itself remains available and retrievable" — a PHI read path not in §5's enumerated audit-event list (session start/end, note generation/edit/sign, tenant-admin config change). Direction: add transcript-read/retrieval to the audited events, or clarify it's covered by generic data-access logging.
4. **Intra-tenant least-privilege and the platform-operator "no PHI access by default" break-glass path are both underspecified.** RBAC addresses cross-tenant isolation thoroughly but not whether one clinician can read another clinician's sessions within the same tenant (HIPAA's minimum-necessary standard applies intra-entity too); "by default" implies a non-default elevated path for `platform_operator` that's never described. Direction: scope clinician access to sessions they participated in, and describe or explicitly rule out any elevated platform-operator PHI access.
5. **Tenant-management/admin flow (a named component and entity) has no audit or FR anchor** — see Spec-Compliance §P2 for the fuller framing; from a security lens, `tenant_admin` config changes are the one audited event category named in §5 that has no described endpoint to generate that event.

### P3

1. Potential soft tension between "platform-level aggregate health/audit views" (§3/§5) and §8's "no aggregate-across-tenants product surface" — likely different things (ops metrics vs. PHI-content analytics) but never explicitly distinguished. Add a clarifying clause.
2. Entra ID identity model is hedged ("tenant-scoped app registrations or an equivalent claims-based tenant boundary") rather than fixed — reasonable for a reference architecture, but the choice materially affects blast radius of a compromised token.
3. Key Vault access governance (who can read tenant FHIR credentials, rotation cadence) isn't described.

## Performance Review

Reviewed in full: commure-ambient-scribe/CLAUDE.md (§3, §4, §6).

### P1

1. **≤3s streaming-transcription target has no budget breakdown, and most of it is owned by an unbounded external dependency.** The path is audio-ingestion → speech-to-text provider (explicitly external, "swap-in dependency, not built here") → transcription-assembly, with no assumed STT-provider latency ceiling stated. Without a sub-budget, the ≤3s number is contingent on an unpinned third party and can't be shown achievable, only asserted. Direction: state an assumed max STT-provider latency (as a documented integration requirement for any swapped-in provider) and allocate the remaining budget across ingestion/assembly hops.
2. **The two "decoupled" autoscaling signals are proxies, not direct measurements, of the SLOs they protect, and neither has a warm-replica floor.** Ingestion scales on concurrent sessions (matches the platform-throughput NFR's own unit — self-consistent), but note-generation scales on Service Bus queue depth with no stated threshold or worker-throughput formula relating queue depth to expected wait time against the ≤10s p95 target. Neither service states a minimum warm-replica count, so the very burst that triggers scale-out is the one most likely to blow through ≤3s/≤10s while new capacity spins up. Direction: tie the note-gen trigger to an estimated-wait-time threshold (queue depth ÷ worker throughput) and state a minimum warm-replica floor for both services.
3. **Transcription-assembly — on the ≤3s critical path — has no stated compute placement or autoscaling policy.** §3 names it as a distinct logical component, but the Azure topology/scaling paragraph only scales the "session/API layer" and "audio-ingestion layer" — it's ambiguous whether assembly is bundled with ingestion or a separate, unaddressed hop. Direction: explicitly state where transcription-assembly runs and what scales it.
4. **≥1,000 concurrent sessions and the per-tenant noisy-neighbor claim are numbers without a backing capacity model or per-tenant cap.** As written, one tenant could account for 990 of 1,000 sessions and the platform-wide target would still be "met" while the multi-tenant intent (no tenant crowding out others) goes untested; "per-tenant rate limiting/quotas" has no stated figures (sessions/tenant cap, requests/sec). No capacity model (replicas × per-replica session capacity) justifies 1,000 as achievable rather than an arbitrary round number. Direction: state an explicit per-tenant session/rate cap and a rough capacity calculation.

### P2

1. **Rate limiting is scoped to "the API layer" (control-plane calls), but the resource cost of a noisy tenant is sustained streaming connections, not start/stop calls.** Unclear whether the quota caps concurrent active sessions/streaming throughput per tenant (which protects ingestion compute) versus only new-session-start calls (which wouldn't). Direction: specify what's capped and confirm enforcement reaches the ingestion data plane.
2. **No caching/cost lever named for the LLM-heavy note-generation step.** No mention of Anthropic prompt caching on the static instruction prefix, no model-tier rationale for the 10s p95, no rough per-note cost estimate at platform scale.
3. **No latency sub-budget for the ≤10s note-generation p95**, and no statement of whether the Claude response is streamed back (which could satisfy "draft-note-available" sooner) or awaited in full. The ≤30-minutes-of-speech input bound is a good, genuinely concrete scoping choice worth crediting, but the stage-by-stage breakdown is still missing.
4. **No write-throughput/connection-pooling story for PostgreSQL under continuous TranscriptSegment inserts at up to 1,000 concurrent sessions** — a plausible real bottleneck, unaddressed alongside the (well-handled) row-level tenant isolation.
5. **HA claim is half-concrete: RPO stated (≤5min), RTO is not** — the failover-time side of the warm-standby DR claim can't be tested end-to-end without it.
6. **Noisy-neighbor acceptance criterion doesn't define the burst it must survive** — "a simulated burst of sessions on one tenant" needs a stated magnitude/rate to be testable as written.

### P3

1. Redis and Service Bus have no stated sharding/partitioning approach at 1,000-session scale.
2. "Concurrent active sessions" as the sole ingestion autoscaling signal ignores per-session load variance (chunk rate/size); a composite signal would be a more faithful proxy.

## Spec-Compliance Review

**Target:** commure-ambient-scribe/CLAUDE.md (all 8 sections read in full)
**Sibling cross-checked:** banner-health-physician-copilot/CLAUDE.md (named in §7 Differentiator; Banner's own §7 independently names Commure back)

**Specific checks — answered directly:**
- **FR-4 (tenant isolation) has strong architectural backing** in §3 (every row carries `tenantOrgId`; Postgres "tenant-partitioned — row-level isolation enforced at the query layer"), §4's dedicated multi-tenancy paragraph, §5 (tenant-scoped RBAC/claims), and a sharp acceptance criterion (a Tenant-A-credentialed query for a Tenant-B session id is rejected, not just filtered). One of the better-backed FRs in the whole portfolio.
- **Multi-tenant framing in §1/§2 stays consistent everywhere** — no single-tenant scope leak found across all 8 sections; §8 explicitly reinforces isolation-as-absolute ("no aggregate-across-tenants product surface").
- **Banner's "no audio/streaming component at all" and Commure's attribution of "single-organization deployment assumed" to Banner: no mutual contradiction.** Banner's doc has zero mentions of audio/streaming or "tenant," and its architecture/RBAC (`physician`/`care_coordinator`/`admin`) is single-org shaped throughout. However, Banner's own §8 never explicitly states a single-org assumption — Commure is inferring it from absence rather than quoting an explicit claim on Banner's side. See P2 below.

### P1
None. All 5 FRs have concrete architectural backing, all 8 sections are present and non-placeholder, and no differentiator claim is directly contradicted by the sibling doc.

### P2

1. **Tenant-management service and the `TenantOrg` entity have no FR coverage.** §3 lists a "Tenant-management service" as a logical component and §4 names `TenantOrg` as a core entity, but none of FR-1 through FR-5 describe tenant onboarding, org/user/EHR-connection config, or the `tenant_admin` role's actions (named in §2/§5) — and §4's key interactions list zero endpoints for this despite the component/entity/role all being named prominently. Direction: add an FR for tenant-admin configuration scoped to their own tenant, with a matching endpoint, or explicitly mark it out-of-scope/deferred in §8.
2. **`AuditEvent` is referenced in §5 but never defined in §4's core-entity list.** §5 describes an `AuditEvent` tagged with `tenantOrgId` at length, but §4's entity list (`TenantOrg`, `EncounterSession`, `AudioChunk`, `TranscriptSegment`, `GeneratedNote`) omits it — Banner's own doc, by contrast, does list `AuditEvent` as a core entity. Compounding this, no FR requires audit logging at all (Commure's FR-4 covers tenant isolation instead), leaving a HIPAA control called out at length in §5 with no FR/AC anchor. Direction: add `AuditEvent` to §4's entity list and consider a short FR/AC for audit-trail completeness.
3. **Commure's "single-organization deployment assumed" claim about Banner rests on absence, not an explicit statement in Banner's own doc.** Not a contradiction — Banner's doc is fully consistent with single-org framing — but the differentiator is only as strong as the weaker side's self-declaration, and right now Commure is speaking for Banner on this point. Direction: have Banner's §8 add one explicit line stating the single-organization assumption in its own words.
4. **"Platform-level aggregate health"/"aggregate view for cross-tenant operational audit" sits close to the §8 out-of-scope line** disclaiming "no aggregate-across-tenants product surface." Plausibly justified by the Platform Operator's ops-telemetry role, but the doc never draws that distinction explicitly. Direction: add a clarifying clause that aggregate views are operational-telemetry-only, non-PHI, distinct from any cross-tenant product/analytics surface.

### P3

1. **API path inconsistency for tenant scoping.** The session-creation endpoint embeds `{tenantOrgId}` in the path; `audio-chunks`, `end`, and `note` endpoints carry only `{sessionId}`, relying on stored-session tenant context + auth token. Functionally fine, reads as inconsistent. Direction: add a one-line note on how tenant context is derived for the latter endpoints, or make the path shape uniform.
2. **"A simulated audio-ingestion hiccup" (acceptance criteria) is under-specified** relative to the doc's otherwise sharp criteria (e.g., the Tenant A/B rejection test) — doesn't name the fault type (dropped chunk, service restart, network partition) or duration. Direction: name the specific fault condition being simulated.
3. **`Compliance/Audit Reviewer` and `Platform Operator` actors have no dedicated FR** — same class of gap as P2-1/P2-2, lower stakes since their read-only/oversight nature is implicitly covered by §5's audit language.

### Differentiator cross-check — passed, no contradiction found
Commure §7 and Banner §7 are mutually consistent on both axes claimed: continuous/passive ambient capture vs. synchronous/physician-initiated, and pervasive multi-tenant isolation vs. no tenant construct at all. The only softness is noted in P2-3 (Banner's side is inferred, not self-stated).

### Section completeness — passed
All 8 required sections are present with substantive content.
