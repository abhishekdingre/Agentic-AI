# Review — Qualified Health Cohort Identification

## Security Review

**Project:** qualified-health-cohort-identification
**Reviewed:** all 8 sections

### P1

1. **The ingestion/identity-resolution step — the first PHI-touching flow in the pipeline — has no audit-logging story at all.** Where: §3 "Ingestion & reconciliation service", §4 "Identity resolution" vs. §5 "Audit logging". §5's enumerated `AuditEvent` list (criteria version, cohort-run launch, rationale-generation call, review decision) omits the reconciliation step entirely, even though this project's own framing is that cross-source identity resolution is the hard, novel problem here. A false-positive match at this stage merges one real patient's clinical facts into another's candidate record — simultaneously a correctness bug and a PHI-disclosure event, with no audit trail to reconstruct it. Direction: add an `AuditEvent` at the reconciliation step recording which source-system record ids were resolved into a single identity, with a match-confidence/method field.
2. **Cohort-run result exports to Blob Storage are a named PHI egress path with zero access-control or audit coverage.** Where: §3 ("Azure Blob Storage (cohort-run result exports)") vs. §5. §5 never states who may generate/download an export, whether exports are RBAC-gated, whether URLs are time-limited/SAS-scoped, or whether export/download emits an audit event — for a bulk-export path that can contain every matched patient in a run, this is one of the highest-severity exfiltration risks in the system. Direction: gate exports by the same RBAC roles as run-launch/review, emit an `AuditEvent` on creation and each download, and use short-lived identity-bound SAS tokens rather than durable links.

### P2

1. **RBAC granularity doesn't match the differentiated actors in System Context.** §2 distinguishes Care Coordinator (primary reviewer) from Clinician ("secondary reviewer on **flagged** candidates" — a narrower view), but §5 collapses both into one undifferentiated `care_coordinator`/`clinician` role. Split into distinct scopes, or state explicitly why they're merged.
2. **Compliance/Audit Reviewer's access path is unaddressed** — no RBAC role or stated mechanism for reading `AuditEvent`/Log Analytics data. Add a line specifying the (likely out-of-band) access path.
3. **Elevated-role gating on population-wide actions is coarse/binary, with no scoping by population/program/source boundary.** Given sources may span different programs or covered entities, a `program_manager` as described can launch a population-wide screen against any source with no minimum-necessary-use scoping. Note whether `CohortRun` population targeting is itself access-scoped.
4. **The PHI boundary at the LLM call is asserted but not concretely specified.** §4 never states whether rationale generation ever sends raw unstructured `DocumentReference`/`Binary` text (a much larger incidental-PHI surface) versus only structured/coded facts plus a citation pointer. State explicitly which it is.
5. **Encryption/data-minimization for the Redis identity-resolution cache is unaddressed** — this store holds cross-source identity-linkage data, arguably more sensitive than ordinary PHI, with no TTL/eviction/at-rest-encryption statement. Add Redis to the encryption story and state a TTL/eviction policy.

### P3

1. No data-retention/training-use terms named for the Anthropic API — worth naming zero-data-retention as an expected BAA term given P2-4's raw-document-excerpt possibility.
2. Key Vault and Service Bus aren't cross-referenced in the Encryption bullet alongside Postgres/Blob.

## Performance Review

Reviewed: full file, §§1-8.

### P1 — Targets not testable or not plausibly compatible with the described architecture

1. **No stated match rate → the two headline throughput numbers don't actually compose.** §6 gives ≥500 patients/min/replica for matching and ≤3s/candidate for rationale, plus "100,000 patients in ≤4 hours" — but that 4-hour bound is explicitly scoped to deterministic matching only, not to the full run (rationale generation is required for FR-3 before a candidate is presentable). With no assumed match rate stated anywhere, there's no way to compute the rationale phase's total time or check an end-to-end SLA at population scale. Direction: state an assumed match-rate upper bound and give a combined end-to-end SLA (matching + rationale) at the 100k scale.
2. **"500 patients/minute/replica" has no stated concurrency model and looks tight by back-of-envelope math.** 500/min = ~120ms/patient if serial — less time than a single real-world FHIR round trip, let alone the multi-source-fragmentation case this project is built around, plus identity resolution plus rule evaluation. No per-replica concurrency assumption (async fan-out, thread/process count) is stated to make the figure derivable rather than asserted.
3. **The batch NFRs don't say what statistic they are** (sustained average vs. floor vs. hard cap) — unlike the API NFR's clean p95, these can't be turned into an objective acceptance test as written.

### P2 — Real gaps in scalability/cost/latency reasoning worth tightening

1. **No caching/cost lever for the LLM-heavy rationale phase**, despite it running once per matched candidate (potentially thousands of times per run) with a likely large shared prompt prefix — a textbook prompt-caching candidate, unaddressed.
2. **No mention of Anthropic API rate limits as a scaling constraint** — at population scale with autoscaling workers all firing rationale calls in parallel, RPM/TPM ceilings plausibly compete directly with the "parallelized across workers" claim.
3. **Deterministic-matching and rationale-generation share one undifferentiated Celery worker layer** with no separate queues/concurrency policy called out, despite opposite resource profiles (CPU/DB-bound vs. network/external-API-bound) — one can starve the other under load.
4. **Autoscaling policy is named but not parameterized** — no scale-out trigger threshold, increment, or max replica ceiling for the Service-Bus-queue-depth trigger.
5. **The 100k/4hr example is internally slack in a way that undercuts its own framing.** Taken literally, 500/min × 240min = 120,000 patients — a single replica already clears the target without any scale-out, contradicting the framing that autoscaling is what gets you under 4 hours. Reconcile so the worked example demonstrates the story it's cited for.

### P3 — Minor

1. No RTO/RPO numbers for the named "warm-standby DR" approach.
2. Unclear whether 100,000 patients is the intended ceiling or just illustrative.
3. Redis identity-resolution cache's expected hit rate / cold-cache behavior isn't discussed relative to the 500/min target.

## Spec-Compliance Review

Target: qualified-health-cohort-identification/CLAUDE.md (all 8 sections read in full)
Sibling cross-checked: carta-clinical-data-extraction/CLAUDE.md (full file, particularly its own §7)

### P1

1. **"Identity resolution" is a first-class architectural component with no backing FR or acceptance criterion.** It's presented in §2/§3/§4 as the project's headline differentiator, yet none of FR-1 through FR-5 mention cross-source identity resolution/merging, and no acceptance criterion tests it (a patient split across two source systems could be double-counted/matched twice without violating any stated FR). Add an FR and an acceptance criterion using a seeded population with a deliberately split-record patient.
2. **Internal contradiction: the "deterministic-only eligibility" claim has an undescribed escape hatch for unstructured data.** §4's LLM orchestration subsection walls Claude off from eligibility decisions entirely (deterministic engine only, evaluated against "structured FHIR facts"), but the FHIR resource mapping in the same section names `DocumentReference`/`Binary` as in scope "when a criterion can't be evaluated from structured data alone" (repeated in §8). Nothing names the mechanism that turns unstructured content into a structured fact the deterministic engine can evaluate — and FR-2 states the engine evaluates "every patient... deterministically" with no carve-out. Either scope document-derived criteria out of automated matching explicitly, or name the actual (audited, non-LLM) structuring mechanism with its own FR and acceptance criterion.

### P2

1. **Actor/role model in §2 doesn't match the RBAC split in §5.** §2 names one combined "Care Coordinator / Program Manager" actor; §5 splits this into two roles with an explicit elevated-privilege rationale for keeping them separate. List them as two distinct actors in §2, or note they're grouped narratively but hold different runtime privilege levels.
2. **Differentiator's "also multi-source" tag for Carta isn't supported by Carta's own doc.** Carta's §2/§3 describe a single FHIR R4 source with no cross-source reconciliation concept anywhere. The core differentiator claim (population-ranking vs. per-field accuracy) is mutually consistent and independently restated in Carta's own §7 — no contradiction there — but the "multi-source" framing applied to Carta specifically could mislead a reader. Drop "multi-source" from the Carta parenthetical, or qualify it as QH-specific.

### P3

1. Minor wording mismatch: QH's §7 says Carta documents are "already... identified as in-scope," while Carta's own FR-1 says "registered... for extraction" — compatible but not identically worded. Optional alignment.

### Not flagged (checked, found consistent)
FR-1 through FR-5 each map cleanly to a named component/entity/endpoint; all three acceptance criteria are plausibly checkable; the core differentiator claim is stated symmetrically in both docs; the batch-only framing assumption is honored consistently throughout; all 8 sections are present with substantive content.
