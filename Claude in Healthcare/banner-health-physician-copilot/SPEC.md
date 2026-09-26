# SPEC — Banner Health Physician Copilot (reference implementation)

Status: approved for implementation. Translates `CLAUDE.md` (design) + `REVIEW.md` (flagged P1s) into a concrete, buildable contract. Where this SPEC adds something not in `CLAUDE.md` (Grok review, local auth, mock FHIR, revised latency target), it is called out explicitly as an addition, not a silent rewrite.

## 1. Scope

Implements `CLAUDE.md` literally: draft-note generation, patient summarization, physician review/edit/sign, audit logging, RBAC. Runs entirely locally via Docker Compose against a mock FHIR R4 server and synthetic data. No real PHI, no real Azure, no real EHR. Adds one runtime safety feature beyond the original design: an independent second-model (Grok) review of every Claude-generated draft before it reaches the physician.

## 2. Entities (exact fields)

```
Encounter                 (mirrored from FHIR on read, not source-of-truth)
  id                       str (FHIR Encounter id)
  patient_id               str (FHIR Patient id)
  attending_practitioner_id str (FHIR Practitioner id, resolved from Encounter.participant
                                  where participant.type includes "ATND")
  status                   str (FHIR encounter-status, e.g. "finished")
  period_start, period_end datetime
  synced_at                 datetime

DraftNote
  id                        uuid
  encounter_id              str
  status                    enum: drafted | drafted_flagged | drafted_unverified |
                                   edited | signed | failed
  draft_text                 json (SOAP: subjective/objective/assessment/plan)
  edited_text                 json | null
  diff                        json | null   (edited vs draft)
  prompt_template_version     str
  claude_model                str
  grok_review                 json | null   (findings payload, see §6)
  failure_reason               str | null   (e.g. "llm_unavailable", "fhir_unavailable")
  created_by                   str (user id)
  created_at, updated_at, signed_at  datetime | null

SummaryRequest
  id                       uuid
  patient_id               str
  summary_text              json
  review_status              enum: clean | flagged | unverified   (mirrors DraftNote's status
                                    split, see §6 — summaries get the same Grok safety net)
  prompt_template_version   str
  claude_model               str
  grok_review                 json | null
  generated_at                datetime
  ttl_seconds                  int (cache TTL, default 900)

AuditEvent
  id            uuid
  actor         str (user id or "system")
  action        str (e.g. "fhir.read", "llm.claude_call", "llm.grok_call",
                       "note.draft", "note.edit", "note.sign", "summary.generate")
  resource_type str
  resource_id   str
  outcome       enum: success | failure
  detail        json | null
  timestamp     datetime

PractitionerAssignment (derived, not a separate write path — see §5)
  practitioner_id  str
  patient_id       str
  via_encounter_id str
```

## 3. API contract (exact, matches `CLAUDE.md` §4 plus the FR-6 addition)

All routes under `/api/v1`, JWT bearer auth required except `/auth/login` and `/health`.

- `POST /auth/login` — demo-only; body `{username, password}` → `{token, role, practitioner_id?}`. Not in the original design doc; local stand-in for Entra ID/OIDC (see §5).
- `GET /encounters` — **additive, not in `CLAUDE.md`'s named interactions, but necessary plumbing**: nothing otherwise lets a physician discover which encounters they're attending, even though the HLD's own data-flow line ("physician selects encounter") presumes exactly this. Role `physician` → list of encounters where caller is `attending_practitioner_id`. Also the point where the local `Encounter` mirror gets populated/refreshed from FHIR.
- `GET /patients/{patientId}/encounters` — same idea, scoped to one patient; used to compute "own patients" for summary access (§5).
- `POST /encounters/{encounterId}/draft-note` — role `physician`, ownership check (§5). Returns `201 {draftNoteId, status, draftText, grokReview}` — `draftText` here is always the **Grok-reviewed** draft (the endpoint does not return until Grok's review completes, including when the outcome is `drafted_flagged`/`drafted_unverified` — never Claude's raw un-reviewed output). Or a failure body (§7/§8). Idempotency: a second call while one is already `drafted*`/`edited` for the same encounter returns the existing note (no duplicate draft) unless `?regenerate=true`.
- `GET /notes/{draftNoteId}` — any authenticated role that owns/can view it (physician who created it, or `care_coordinator`/`admin` per §5) → `{draftNoteId, status, draftText, editedText, grokReview, ...}`.
- `PATCH /notes/{draftNoteId}` — role `physician`, must be the creator, note must be in `drafted`/`drafted_flagged`/`drafted_unverified`/`edited` (not `signed`). Body `{editedText}` → stores diff, status → `edited`.
- `POST /notes/{draftNoteId}/sign` — role `physician`, must be the creator, note must not already be `signed`. → `{status: "signed", signedAt}`; enqueues the archival job (§9). Immutable after this point — any further `PATCH` is rejected `409`. **If the note's current status is `drafted_flagged` or `drafted_unverified` (Grok raised a concern, or Grok's review itself couldn't complete), the request body must include `{"acknowledgeFlags": true}` or the call is rejected `409` — a physician can still sign over a flagged/unverified draft (FR-2: human makes the final call), but cannot do so by accident, and the acknowledgment itself is recorded on the `AuditEvent`.**
- `POST /patients/{patientId}/summary` — role `physician` (own patients, §5) or `care_coordinator` (read-only, any patient — per `CLAUDE.md` §5's stated blanket read grant, reconciled in §5 below) → `{summaryId, summaryText, generatedAt, grokReview, reviewStatus}`.
- `GET /health`, `GET /metrics` — liveness/readiness + Prometheus-style counters (request count, latency histogram, LLM error rate) per `CLAUDE.md` §6 Observability, via `prometheus-fastapi-instrumentator` — no separate Prometheus/Grafana containers, just the `/metrics` endpoint the NFR literally asks for.

Failure bodies are structured and distinct by cause (never a fabricated partial note, per FR-2/FR-5):
```
503 {status: "failed", reason: "fhir_unavailable", detail}
502 {status: "failed", reason: "llm_unavailable", detail}
```

## 4. FHIR resource mapping & mock server

Resources: `Encounter`, `Condition`, `Observation`, `MedicationRequest`, `Procedure`, `DocumentReference` — all read-only, matching `CLAUDE.md` §4.

**Mock FHIR server** (`app/mock_fhir_server/`): standalone FastAPI app serving static synthetic fixtures with real FHIR REST search semantics:
- `GET /Encounter/{id}`, `GET /Condition?patient={id}`, `GET /Observation?patient={id}`, `GET /MedicationRequest?patient={id}`, `GET /Procedure?patient={id}`, `GET /DocumentReference?patient={id}`.
- Fixtures: 3 synthetic patients, 4-5 encounters split across 2 demo physicians as attending practitioner (so ownership-rejection is testable), each with 5-15 linked resources across the 6 types (≤20 total per encounter, matching the NFR's scoping bound).
- The real app's `FHIRClient` only knows `FHIR_BASE_URL` (env var) — swapping to a real FHIR R4 endpoint requires no code change, only config, matching `CLAUDE.md`'s "EHR-agnostic by design."
- A `X-Simulate-Outage: true` header (test-only, honored only when `ENV=test`/`local`) makes the mock server return 503, used to exercise the FHIR-unavailability path deterministically in integration tests.

## 5. RBAC & ownership (closes Security P1 — reconciles FR-1 vs §5)

`CLAUDE.md` states two different things: FR-1 says "attending clinician for an encounter," §5 says "own patients." This SPEC adopts **one concrete rule**, derived from FR-1's wording since it names a checkable field:

- A `physician` may `draft-note`/`sign`/`PATCH` on an `Encounter` **iff** `encounter.attending_practitioner_id == caller.practitioner_id`. Checked by re-resolving the Encounter from FHIR (or the local mirror if fresh) on every request — never trusted from a cached claim alone.
- A `physician` may request a patient `summary` **iff** they are `attending_practitioner_id` on **at least one** `Encounter` for that patient in our mirrored data (the derived `PractitionerAssignment` relation in §2). This is FR-1's rule applied transitively to patient-level access, since the design doc has no separate patient-assignment concept.
- `care_coordinator`: read-only on `GET /notes/{id}` and `POST /patients/{patientId}/summary` for **any** patient — per `CLAUDE.md` §5's literal wording ("read-only summary access," no ownership qualifier stated for this role, unlike physician's). `REVIEW.md` P2 flags this as unscoped; this SPEC keeps it as designed (in scope) but logs every coordinator summary read as its own `AuditEvent` action `summary.coordinator_read` so the breadth is at least fully auditable — tightening this further is out of scope for this pass.
- `admin`: no PHI routes at all (config-only, none of which exist yet in this build — `admin` role is seeded but has no admin-only endpoints in v1).
- Enforcement point: a FastAPI dependency (`auth/rbac.py`) run on every PHI route, never a global middleware alone — ownership checks need the specific resource id from the path.

## 6. LLM orchestration

**Claude (drafting/summarization)** — `llm/claude_drafting.py`:
- Model: `claude-opus-5` by default, overridable via `CLAUDE_MODEL` env var.
- Versioned system prompt (`llm/prompts.py`, `PROMPT_VERSION = "banner-soap-v1"`) constrains Claude to state only facts present in the supplied structured FHIR data — no inference beyond the record. Cached via `cache_control: {"type": "ephemeral"}` on the system block (stable across requests; only the per-request FHIR facts + patient context vary).
- Output: `output_config={"format": {"type": "json_schema", "schema": SOAP_SCHEMA}}` — `{subjective, objective, assessment, plan}`, each a string built only from supplied facts.
- `thinking={"type": "adaptive"}`, `output_config` also carries `"effort": "medium"` (latency-sensitive path).
- On timeout/error/refusal: `DraftNote.status = "failed"`, `failure_reason = "llm_unavailable"` — never a partial/truncated draft returned as if successful (FR-5, existing behavior, unchanged).

**Grok (independent grounding review)** — `llm/grok_review.py`, **new in this build, not in `CLAUDE.md`** — implements the user's "multi-agent review system" requirement:
- Runs only after Claude successfully produces a draft. Receives the **same structured FHIR facts** Claude received, plus Claude's draft — never anything Claude wasn't also given (no PHI-boundary widening).
- Model: `grok-4.7` by default (`GROK_MODEL` env var) via the OpenAI-compatible client (`openai` SDK, `base_url="https://api.x.ai/v1"`). **Exact model slug and structured-output parameter shape to be confirmed against live xAI docs at implementation time** — this default is provisional pending that check.
- Task, strictly bounded to grounding/consistency (not a second clinical opinion): for every clinical claim in the draft, verify it traces to a supplied fact; flag claims that don't, internal contradictions, and material omissions of supplied facts.
- Output schema (validated against our own Pydantic model regardless of what xAI's API enforces — a schema-validation failure is treated identically to a Grok-call failure):
  ```
  {
    "grounded": bool,
    "findings": [{"severity": "critical"|"warning", "claim": str, "issue": str}],
    "overall_assessment": str
  }
  ```
- Gating (this is the FR-6 behavior, §8):
  - No `critical` findings → `DraftNote.status = "drafted"`.
  - ≥1 `critical` finding → `DraftNote.status = "drafted_flagged"`, findings included in the API response and rendered prominently in the UI. Physician can still edit/sign (FR-2: human always makes the final call) — flags are surfaced, never a hard block.
  - Grok call fails/times out/fails schema validation → `DraftNote.status = "drafted_unverified"`, `grok_review = null`; UI shows "AI safety check unavailable — review with extra care" rather than silently presenting the draft as reviewed.
- Same pattern applies to summaries (`SummaryRequest.grok_review`).
- Kept in a separate file/module from `claude_drafting.py` per the provider-isolation convention (no mixed Anthropic/OpenAI-compatible calls in one file).

## 7. FHIR-unavailability handling (closes Spec-Compliance P1)

`fhir/client.py`: per-call timeout (2s), 1 retry on connection error/5xx/timeout, then a failure-counter circuit breaker (opens after 3 consecutive failures within 30s, half-open retry after 15s). **The breaker's failure-count/open-since state lives in Redis (`redis_client.py`), keyed per resource-type, not in-process memory** — the API runs as multiple replicas (§9), and an in-process counter would let each replica independently hammer a down FHIR server 3 times before tripping, and would never trip at all under low-traffic-per-replica conditions; a shared Redis counter makes the breaker actually protect the FHIR server across the whole fleet. On exhaustion (retry failed or breaker open): the calling service returns `503 {status:"failed", reason:"fhir_unavailable"}` immediately — distinct from and symmetric to the existing `llm_unavailable` path — and does **not** call Claude/Grok with partial data. `DraftNote`/`SummaryRequest` row is not created for a FHIR failure (nothing to persist); an `AuditEvent` (`action: "fhir.read"`, `outcome: "failure"`) is still recorded.

**Fetch shape** (also see §9): `Encounter` is not just another parallel fetch target — it is the authorization gate (§5 needs `attending_practitioner_id` before any other data is touched). The client resolves `Encounter` first (mirror hit, or one authorizing fetch), performs the RBAC check, and only then fetches the remaining ≤19 linked resources (`Condition`/`Observation`/`MedicationRequest`/`Procedure`/`DocumentReference`) concurrently via `asyncio.gather`. This two-phase shape (one gate fetch, then a parallel batch) is what the §9 latency budget is built on — it is not a single flat parallel fetch of all resource types.

Acceptance criterion (new, closes the gap `REVIEW.md` flagged): sending `X-Simulate-Outage: true` to the mock FHIR server on a draft-note request produces the `fhir_unavailable` failure body, not a partial or fabricated draft.

## 8. Functional requirements (FR-1..5 from `CLAUDE.md`, unchanged, plus new FR-6)

- FR-1: A physician can request a draft note for any encounter they are the attending clinician for. *(enforced per §5)*
- FR-2: A draft note must never be presented as final/signed without explicit physician sign-off. *(unchanged; `drafted_flagged`/`drafted_unverified` are still pre-sign states)*
- FR-3: A physician can request a condensed summary of a patient's longitudinal record. *(care_coordinator also, per §5)*
- FR-4: Every draft/summary/sign action is attributable to a specific user+timestamp in the audit trail. *(§2 AuditEvent, written on every FHIR read / Claude call / Grok call / note action)*
- FR-5: The system must degrade gracefully (explicit failure state, not a fabricated partial note) on LLM or FHIR-source unavailability. *(§7, both paths now symmetric)*
- **FR-6 (new, this build)**: Every Claude-generated draft note or summary must be independently reviewed by a second, architecturally distinct model (Grok) for grounding before being returned to the physician; the review's outcome (clean / flagged / unavailable) must always be visible to the physician, never silently dropped. *(§6)*

## 9. Performance (revised — closes Performance P1)

Original `CLAUDE.md` target (≤8s p95, single LLM call) is revised because FR-6 adds a mandatory second sequential model call on the critical path (Grok must review before the physician sees the draft — that's the explicit ask, not an optional background check):

- **Draft-note p95 ≤ 12s** end-to-end for a typical encounter (≤20 FHIR resources), budgeted as:
  - FHIR fetch, two-phase (§7 — `Encounter` gate fetch, then the remaining ≤19 resources via `asyncio.gather`, not one flat parallel batch): ≤ 1.5s total (≤0.3s cache-hit gate + ≤1.2s parallel remainder, uncached)
  - Claude structured draft (prompt-cache hit on system prompt): ≤ 6s
  - Grok grounding review: ≤ 3.5s
  - Persistence + audit-write: ≤ 0.5s
  - Buffer: ≤ 0.5s
- **Summary p95 ≤ 10s** (same reasoning, ≤5 years longitudinal history), same proportional budget split.
- Sustained throughput: ≥ 50 concurrent draft/summary requests per API replica, matching `CLAUDE.md` (stateless FastAPI, horizontally replicable via `docker compose up --scale api=N`).
- FHIR read round-trip: cached p95 ≤ 300ms (Redis), uncached p95 ≤ 1.5s (unchanged from design doc; mock server + local network makes this trivially achievable, budget kept for fidelity to the design intent).

## 10. Security & audit (mapped to local equivalents)

- **Auth**: local demo JWT issuer (`auth/jwt.py`, HS256, `JWT_SECRET` env var) stands in for Entra ID/OIDC. Seeded users (`auth/demo_users.py`): 2 physicians (`dr.alvarez`, `dr.chen` — attending on different encounter subsets, so ownership-rejection is demoable), 1 `care_coordinator`, 1 `admin`.
- **Audit logging**: every FHIR read, every Claude call, every Grok call, every note draft/edit/sign, every summary generation → `AuditEvent` row (`audit/logger.py`), matching and extending `CLAUDE.md` §5 (which only listed FHIR read / Claude call / sign-off — this build also logs Grok calls and edits, closing `REVIEW.md`'s P2 on that point).
- **Encryption**: TLS termination is out of scope for local Docker Compose (no public exposure); Postgres/Redis/MinIO run on the Compose-internal network only, not published beyond the ports needed for local access.
- **PHI boundary**: unchanged from `CLAUDE.md` §4 — only the structured facts relevant to the specific encounter/patient are sent to Claude, and the identical fact set (never more) is sent to Grok.

## 11. Async job: signed-note archival (enterprise pattern: async jobs)

On `POST /notes/{id}/sign`, the API enqueues an `arq` job (`archival/jobs.py`) rather than writing to object storage inline — decouples the sign request's latency from archival I/O. The worker (`archival/worker.py`, its own Compose service) writes the signed note (text + metadata) to a MinIO bucket (`signed-notes`), local stand-in for `CLAUDE.md` §3's Blob Storage archival export. Job failure retries with backoff (arq default) and is itself audit-logged (`action: "note.archive"`, outcome success/failure).

## 12. Docker Compose services

`postgres`, `redis`, `minio`, `mock-fhir` (mock FHIR server), `api` (FastAPI, connection-pooled to postgres/redis), `worker` (arq worker, same image as `api`, different entrypoint), `frontend` (static file server for the thin UI). All on one Compose network; only `api` and `frontend` ports published to the host.

## 13. Environment variables (`.env.example`)

`ANTHROPIC_API_KEY`, `XAI_API_KEY`, `CLAUDE_MODEL` (default `claude-opus-5`), `ANTHROPIC_EFFORT` (default `medium` — the §6 drafting-call `output_config.effort`, env-configurable so the latency/quality tradeoff can be tuned without a code change), `GROK_MODEL` (default `grok-4.7`), `XAI_REASONING_EFFORT` (default `high`, per xAI's `reasoning_effort` param — `low`/`medium`/`high`/`xhigh`), `DATABASE_URL`, `REDIS_URL`, `FHIR_BASE_URL` (default `http://mock-fhir:8000`), `JWT_SECRET`, `MINIO_ENDPOINT`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`, `ENV` (`local`/`test`).

## 14. Testing strategy

- **Unit** (`tests/unit/`): RBAC/ownership logic, FHIR client retry/circuit-breaker state transitions, prompt construction (fact-only, no live calls), Grok response parsing + gating logic (clean/flagged/unavailable), diff computation for `PATCH`.
- **Integration** (`tests/integration/`): full draft-note→sign→archival flow against the real mock-fhir-server + test Postgres/Redis, with Claude/Grok HTTP calls stubbed via `respx` (never hits a real paid API in automated tests). Covers: happy path, non-attending-physician 403, `X-Simulate-Outage` → `fhir_unavailable`, stubbed-Grok-critical-finding → `drafted_flagged`, stubbed-Grok-failure → `drafted_unverified`, sign-without-draft rejection, PATCH-after-sign rejection.
- CI-safe by construction: no test suite run makes a real Anthropic or xAI API call.
