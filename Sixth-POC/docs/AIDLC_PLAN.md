# AIDLC Plan — Architecture & Design

Companion to [[PROBLEM_STATEMENT]]. This is the shared contract both builder agents (backend, frontend) build against — see [[AGENT_OPERATING_MODEL]] for how they're invoked.

## Stack
- **Backend**: Python + FastAPI. A hand-written agentic loop against the Anthropic SDK (`claude-sonnet-5`) using `strict: true` tool schemas — no LangChain/LlamaIndex, no SDK `tool_runner` helper, so the retrieval→grounding path stays fully inspectable and auditable.
- **Frontend**: vanilla HTML/CSS/JS, no build step.
- **Embeddings**: `sentence-transformers` (`all-MiniLM-L6-v2`), computed explicitly (not via a registered Chroma embedding function) so the step stays inspectable.
- **Vector store**: `chromadb.PersistentClient`, local/embedded, no server process.
- **Schemas**: Pydantic v2, `ConfigDict(extra="forbid")` throughout (this is what makes `additionalProperties: false` come for free in the strict tool schemas).

## Auth
This environment's `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` work directly with the `anthropic` Python SDK. `backend/config.py` prefers these, falling back to `ANTHROPIC_API_KEY` from `.env` for portability to other environments.

## Corpus
Local, self-authored, synthetic only — a fictional program (compound `AXN-2401`, target `CRB3`, indication `PF-7`) so the model has no real-world knowledge to leak in as an uncited "fact." ~28 documents across 4 domains (literature, patents, clinical_trials, internal_reports), designed with:
- An abundant cross-domain evidence scenario (so a normal question gets a rich, multi-source, high-confidence answer).
- A deliberate superseded-document trap: `IR-002-v1` (wrong NOAEL) vs `IR-002-v2` (corrected, current) — tests allowlist/version enforcement.
- A deliberate unresolved contradiction: `LIT-007` (hERG signal) vs `IR-006`/`CT-004` (no signal) — tests that the system surfaces disagreement instead of picking a side.
- A deliberate no-evidence scenario (e.g. nonexistent Phase 3 renal-fibrosis data) — tests graceful refusal.

## Allowlist enforcement (layered — commitment #1)
`data/allowlist.json`: array of `{source_id, domain, title, version, status, supersedes, superseded_by, file_path}`, `status` ∈ {`current`, `superseded`}.
1. **Ingestion-time**: `ingest.py` only chunks+embeds `status == "current"` documents. Superseded docs are architecturally unretrievable — not filtered after the fact, never embedded at all.
2. **Query-time**: the `search_corpus` tool's `domains` parameter enum is built per-request from exactly the domains the user selected in the UI.
3. **Runtime defense-in-depth**: every hit and every citation is re-checked, server-side, against `approved_current_ids` loaded fresh from `allowlist.json` — even though (1) and (2) should make this redundant, it's the last line of defense.

## RAG engine — essential/required features (commitment #1's retrieval half)
`backend/chunking.py`, `embeddings.py`, `vector_store.py`, the `search_corpus` tool.
- **Essential**: paragraph-aware chunking with overlap (`CHUNK_MAX_CHARS=700`, `CHUNK_OVERLAP_CHARS=100`); local `all-MiniLM-L6-v2` embeddings; Chroma metadata filtering on `domain`; top-k semantic retrieval exposed to the model only through the strict-schema `search_corpus` tool; similarity score surfaced per hit (L2 distance → similarity via `max(0.0, 1.0 - dist/2.0)`) for confidence scoring downstream.
- **Explicitly deferred**: hybrid keyword+vector search/reranking, query rewriting beyond what the agent does naturally across loop iterations, caching/incremental re-ingestion, parent-document/multi-vector retrieval. If triage flags retrieval quality, tune chunk size/overlap or the tool description before reaching for these.

## Agentic loop (`backend/agent.py`)
Manual loop over `client.messages.create()`, bounded at `MAX_ITERATIONS = 6`.

System prompt directs the model to: treat the corpus as the only source of truth; decompose the question; call `search_corpus` iteratively across the selected domains; finish by calling `submit_structured_answer` exactly once (never plain text); back every claim with a verbatim quote from a chunk retrieved *this session*; surface disagreement rather than resolving it; and set `refused=true` when evidence is thin or absent rather than filling the gap from general knowledge.

Both `search_corpus` and `submit_structured_answer` are `strict: true` tool schemas (derived from Pydantic models with `extra="forbid"`, giving `additionalProperties: false` automatically). Confirmed against current Anthropic docs: no beta header required; `required` only needs truly-required fields; numeric `minimum`/`maximum` are **not** supported (so no `max_results` param — the result count is hardcoded server-side instead).

On `submit_structured_answer`: `grounding.py` validates the submission. Invalid → specific per-claim feedback fed back to the model, up to `MAX_GROUNDING_RETRIES = 2`. Still invalid after retries → `degrade()`: keep the claims that *do* ground, move ungrounded ones into `gaps_or_caveats`, set `escalate_for_human_review=true`; a full refusal only happens if nothing grounded remains.

## Grounding validation (`backend/grounding.py`) — commitment #2, deterministic, never an LLM judge
Per citation:
- `chunk_id` must exist in this session's `retrieved_chunks` registry (i.e. it was actually returned by a `search_corpus` call this session).
- `source_id` must match that chunk's actual source and be in `approved_current_ids`.
- The `quote` must actually appear in the retrieved excerpt (exact string match, or ≥0.8 token-overlap fallback for minor whitespace/punctuation drift).

## Confidence scoring (`backend/confidence.py`) — server-side only, never self-reported
Per claim: `0.40 × independence + 0.35 × avg_similarity + 0.25 × diversity`, where `independence = min(n_sources/3, 1)` and `diversity = min(n_domains/2, 1)`. Bucketed into High / Moderate / Low / Insufficient with a floor rule (e.g. a single-source claim can't reach High regardless of similarity).

Overall confidence (revised in the V1 refactor, see `docs/TRIAGE_REPORT.md` finding 3.3 and `docs/REFACTOR_V1_REPORT.md`): a **supermajority threshold**, not a strict weakest-link and not an average. Claim buckets are ranked and sorted descending; overall is the highest bucket that at least two-thirds of all claims meet or exceed. A small minority (≤ ~1/3) of legitimately single-sourced Low claims no longer single-handedly caps an otherwise strong, multi-claim answer, but a genuinely thin or contradicted majority still resolves to Low/Insufficient exactly as before. Still additionally capped at Moderate whenever `gaps_or_caveats` is non-empty.

## Fixed output schema (`backend/schemas.py`) — commitment #3
Two layers, so the server never trusts model-reported metadata:
- `RawAnswer` (what the model submits — minimal): `claims[{claim_id, statement, citations[{source_id, chunk_id, quote}]}]`, `gaps_or_caveats`, `refused`, `refusal_reason`, `escalate_for_human_review`.
- `FinalAnswer` (what the API returns — server-enriched): same shape, but every citation's `title`/`domain`/`version`/`similarity` is filled in server-side from the allowlist/retrieval log (never from the model), plus a computed `confidence` per claim and overall, plus the full `retrieval_log`.

The UI only ever renders `FinalAnswer`. There is no code path that returns free text to the user.

## Fail gracefully (commitment #4)
Enforced by construction, not by asking nicely: the grounding validator rejects ungrounded claims regardless of what the model intended; `degrade()` is the only way partial evidence reaches the user, and it's paired with `escalate_for_human_review`; the "no evidence" corpus scenario and the contradiction scenario in the verification table exist specifically to prove this path works end to end.

## Frontend contract
`POST /api/query {"question": str, "domains": [str]}` → `FinalAnswer` JSON (synchronous call, "Retrieving evidence…" spinner while it's in flight).

UI: 4 domain checkboxes (Literature / Patents / Clinical Trials / Internal Reports, default all checked) → a chat-style list of turns → per-turn source-trail panel: overall confidence badge (color-coded; a red banner + `refusal_reason` if `refused`), one card per claim with citation pills (title/domain/version/similarity, expandable to the full quote), a `gaps_or_caveats` warning list, and a collapsible full retrieval log for audit.

## File structure
```
Sixth POC/
├── docs/PROBLEM_STATEMENT.md, AIDLC_PLAN.md, AGENT_OPERATING_MODEL.md, TRIAGE_REPORT.md,
│   DEMO_SCRIPT.md, LOAD_TEST_REPORT.md, knowledge_graph.json
├── .claude/skills/agentic-rag-poc/SKILL.md
├── .claude/agents/backend.md, frontend.md, p3-triage-agent.md
├── .claude/settings.json, .claude/hooks/run-backend-tests.sh
├── .mcp.json
├── docker-compose.observability.yml
├── README.md, requirements.txt, .env.example
├── data/allowlist.json, data/corpus/{literature,patents,clinical_trials,internal_reports}/*.md
├── backend/main.py, config.py, allowlist.py, embeddings.py, chunking.py, vector_store.py,
│   ingest.py, schemas.py, tools.py, grounding.py, confidence.py, agent.py, mcp_server.py,
│   telemetry.py
├── frontend/index.html, styles.css, app.js
└── tests/test_allowlist.py, test_grounding.py, test_confidence.py, smoke_test.py, load/query_load_test.js
```

## Observability
`backend/telemetry.py` instruments `agent.py` with OpenTelemetry: a span per loop iteration and per tool call, with attributes for selected domains, retrieved chunk count, grounding pass/fail, retry count, and final confidence bucket; counters/histograms for request latency, refusal rate, grounding-retry rate. Exported via OTLP to a single-container **SigNoz** stack (`docker-compose.observability.yml`) — bundles traces/logs/metrics/UI, the lightest-weight option for a POC.

## Load testing
`tests/load/query_load_test.js` (k6): hits `POST /api/query` with a mix of the [[PROBLEM_STATEMENT|verification]] questions across varying domain selections, ramping virtual users, thresholds on p95 latency and error rate. Results recorded in `docs/LOAD_TEST_REPORT.md`.

## Knowledge graph
Once the corpus + `allowlist.json` exist, the installed **graphify** skill is run against them to produce `docs/knowledge_graph.json`: nodes for documents/claims/citations, edges for `supersedes`/`superseded_by` chains and claim→citation→source links, clustered by domain. Doubles as a demo artifact and a sanity check that the allowlist's version chains are correct.

## Verification plan
| Question | Domains | Expected | Commitment |
|---|---|---|---|
| "What preclinical and clinical evidence supports CRB3 as a target for AXN-2401 in PF-7?" | all 4 | Multi-claim, ≥3-domain citations, High/Moderate confidence, no refusal | Grounding + structured output |
| "What NOAEL was established for AXN-2401?" | all 4 | Cites only `IR-002-v2`; `IR-002-v1`'s wrong figure never appears | Allowlist/version enforcement |
| "What Phase 3 renal fibrosis efficacy data exists for AXN-2401?" | all 4 | `refused=true`, `escalate_for_human_review=true`, zero fabricated citations | Fail gracefully |
| "Is there a cardiac safety (hERG) signal for AXN-2401?" | all 4 | Non-empty `gaps_or_caveats` or split claims citing `LIT-007` vs `IR-006`/`CT-004`; confidence ∈ {Low, Insufficient} | Fail gracefully |
| Repeat question 1 with `domains=["literature"]` only | literature | Every citation's domain is `literature` | Domain-selection enforcement |

Plus unit tests in `test_grounding.py` (fabricated `chunk_id`, fabricated quote, non-current `source_id`) that never call the model. See [[TRIAGE_REPORT]] for how the P3-Triage-Agent exercises this table.
