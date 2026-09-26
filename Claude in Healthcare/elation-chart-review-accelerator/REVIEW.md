# Review — Elation Health Chart Review Accelerator

## Security Review

Project: elation-chart-review-accelerator/CLAUDE.md (all 8 sections read in full)

### P1

1. **Audit logging silently excludes the highest-frequency PHI-access path (cache-hit reads), and for one actor class it's the *only* path.** Where: §5 vs. §3/§4. §5's audit events fire on "every chart-summary generation/regeneration" and "every problem-list synthesis run," but a cache-hit view via `GET /api/v1/patients/{patientId}/chart-summary` is neither — so it emits no `AuditEvent`. `care_team_staff` (§5 RBAC) has "read-only summary, no regenerate," meaning every PHI access this role ever makes is a cache-served read with zero audit trail. §5's HIPAA "Audit Controls" mapping doesn't actually hold for this entire named actor. Direction: define a view/access audit event fired on every `GET` regardless of cache hit/miss, or explicitly justify the exemption.
2. **"Compliance/Audit Reviewer" (named in §2) has no role in the RBAC model, and the closest-fit role contradicts what the actor needs to do.** §5's three roles (`clinician`, `care_team_staff`, `admin`) don't include this actor; `admin` is explicitly "config only, no PHI," but audit entries carry patient-identifying data. Direction: add an explicit audit-reviewer role or a scoped/de-identified admin audit view.
3. **PHI-boundary claim ("structured facts only") is contradicted by the chart-summary flow's own inputs.** §4's PHI-boundary line asserts only structured facts are sent to Claude, but the same section's FHIR resource mapping feeds `DocumentReference` (narrative note pointers) into the same call. The doc never resolves whether raw note text or just metadata crosses to Claude — undercutting the data-minimization claim. Direction: state explicitly what subset of `DocumentReference` content is sent, and correct the "structured facts only" claim if full text is included.

### P2

1. **Redis (caches PHI-derived content) is absent from the Encryption story** — §5's encryption bullet covers only Postgres/Blob; name Redis explicitly (at-rest + TLS-only connections).
2. **Encryption section names "Blob" storage that doesn't exist anywhere in the Architecture** — §3 has no Blob Storage component; this looks like unedited boilerplate from a sibling project. Remove or add the missing component.
3. **`ChartFlag` resolution has no defined endpoint and therefore no audit story** — §8 asserts "a human always makes the final call" but §4's Key Interactions has no endpoint to record a flag as resolved, so there's no corresponding audit event either. Add the missing endpoint and an audit event for it.
4. **Problem-list retrieval (viewing an already-synthesized list) likely shares the same audit gap as chart-summary cache hits** — smaller in impact, same fix should cover both.

### P3

1. No mention of network-level isolation (private endpoints/VNet) for Postgres, Redis, or Key Vault.
2. The exact payload sent to Claude for the problem-list rationale step (codes/status/source vs. any narrative text on `Condition`) is under-specified.

### Not applicable
Multi-tenancy isolation: no `TenantOrg`/tenant concept in this project's model, so this check doesn't apply.

## Performance Review

**Project**: elation-chart-review-accelerator/CLAUDE.md

**Baseline observation (not a finding)**: §6's Performance subsection gives four concrete, testable numbers — concreteness is largely satisfied; the gaps below are about plausibility and completeness.

### P1

1. **Unaddressed LLM-call cardinality for the problem-list rationale target.** §4 never states whether rationale generation is one batched Claude call covering all flagged conflicts, or N sequential calls (one per conflict) — a realistic multi-conflict chart could push 3+ sequential calls past the 4s budget before FHIR fetch/dedup time is even counted. Direction: commit explicitly to a single batched rationale call per synthesis run, or restate the SLA as scaling with conflict count.

### P2

1. **`DocumentReference` content resolution not addressed against the 5s cache-miss budget.** FHIR `DocumentReference` typically points to a `Binary` requiring a second round trip per document; "recent" documents is undefined, so the number of extra sequential round trips is unbounded as written. Direction: bound the document window explicitly and state binary fetches happen in parallel.
2. **Cost/latency levers only addressed at the data-cache layer.** No discussion of Anthropic prompt caching or model-tier selection across the two distinct LLM call sites (summary generation vs. conflict rationale), despite different latency budgets. Direction: note model tier per call site and consider prompt caching for the shared prompt portion.
3. **Burst/cold-start behavior at clinic-day start not discussed.** Autoscaling trigger (concurrent review-session count) is concrete, but morning-burst cold-start spin-up time isn't addressed against the p95 targets. Direction: state a warm-replica floor/pre-warming strategy, or scope the throughput figure as steady-state.
4. **Redis failure mode not stated** — the 150ms cache-hit target and the "absorbs repeated-view load" claim both depend on Redis being up; state degrade-to-cache-miss behavior and Redis's own HA posture.

### P3

1. Terminology mismatch: "review-session" (autoscaling trigger) vs. "chart opens" (tested throughput metric) — align the units.
2. "Short TTL" has no concrete value — pin a number.
3. "Per replica set" throughput figure has no stated instance sizing.

## Spec-Compliance Review

**Target**: elation-chart-review-accelerator/CLAUDE.md
**Sibling read**: banner-health-physician-copilot/CLAUDE.md (named in §7 Differentiator)

### Summary of the four non-bucketed checks
- **Section completeness**: all 8 sections present, none placeholder. No finding.
- **FR→architecture mapping**: FR-1 through FR-5 all have concrete backing (chart-summarization service, problem-list synthesis service + entities, review-telemetry service, the explicit LLM-independence paragraph). No orphan FR. One reverse gap found (ChartFlag resolution, see P2).
- **Differentiator cross-check**: Elation's claim ("read/reconcile-focused, no new documentation artifact" vs. Banner's "write/draft-focused, produces a signed note") is fully substantiated by Banner's own doc (DraftNote state machine, sign endpoint) and by the absence of any sign/write endpoint in Elation's own §4. Mutually consistent — no contradiction. (Banner's own §7 names Commure, not Elation, as its closest sibling; that asymmetry is fine per the review scope.)
- **Framing-assumption honesty** (§1 "not a full EHR rebuild"): no scope creep into scheduling/billing/e-prescribing found anywhere in §2–§8; problem-list synthesis is explicitly read-only. Passes.

### P1
None.

### P2

1. **`ChartFlag.resolved/unresolved` state has no interaction to set it, and no FR covers resolution.** §8 states "a human always makes the final call," but no endpoint or FR describes how that decision gets captured. Add a `PATCH` interaction and a corresponding FR, or state resolution is tracked outside this system.
2. **Acceptance criterion depends on an undefined cache TTL.** §6's AC references "the cache TTL," but §3/§4 only ever say "short TTL" with no number — not independently testable as worded. State a specific TTL value.
3. **Problem-list acceptance criterion conflates "near-duplicate" with "conflicting."** Not every near-duplicate pair is a status conflict per §3/§4's own model; the AC fixture should specify a status conflict (e.g., one active/one resolved) to be unambiguous.

### P3

1. **Compliance/Audit Reviewer actor has no matching interaction** in §4 — presumably reviews via Azure Monitor directly, but this isn't stated. (Same gap exists in the sibling Banner Health doc, so this reads as a consistent portfolio convention rather than an isolated oversight.) Add one line noting the audit-review path.
