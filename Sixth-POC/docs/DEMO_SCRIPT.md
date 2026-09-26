# Demo Script

Companion to [[PROBLEM_STATEMENT]], [[AIDLC_PLAN]], [[TRIAGE_REPORT]], [[LOAD_TEST_REPORT]]. AIDLC step 7: "Demonstrate the POC for first user." Reproducible walkthrough — every command below can be copy-pasted from the project root (`/home/labuser/Downloads/Sixth POC`).

## 0. What you're about to see

An agentic RAG system for a fictional pharma R&D program (compound `AXN-2401`, target `CRB3`, indication `PF-7`). Given a research question and a set of selected domains (Literature / Patents / Clinical Trials / Internal Reports), it retrieves only from an approved-source allowlist, grounds every claim to a real citation (checked server-side, never trusted from the model), returns a fixed structured schema (never free text), and refuses or escalates rather than fabricating when evidence is thin or absent. The four commitments map directly onto the four demo scenarios below.

## 1. Start the system

```bash
cd "/home/labuser/Downloads/Sixth POC"
venv/bin/python backend/ingest.py        # only needed if data/chroma/ is empty or corpus changed
venv/bin/uvicorn backend.main:app --port 8000
```

Frontend: open `frontend/index.html` directly in a browser (no build step), or serve it statically:
```bash
python3 -m http.server 8888 --directory frontend
```

## 2. Four scenarios, four commitments

Run each via the UI, or directly:
```bash
curl -s -X POST localhost:8000/api/query -H 'Content-Type: application/json' \
  -d '{"question": "<question>", "domains": ["literature","patents","clinical_trials","internal_reports"]}' | python3 -m json.tool
```

| # | Question | Domains | What to point out | Commitment |
|---|---|---|---|---|
| 1 | "What preclinical and clinical evidence supports CRB3 as a target for AXN-2401 in PF-7?" | all 4 | Multiple claims, citations spanning ≥3 domains, no refusal. Open a citation pill — the title/domain/version/similarity shown is server-enriched, never model-reported. | Grounding + structured output |
| 2 | "What NOAEL was established for AXN-2401?" | all 4 | Cites only `IR-002-v2` (corrected NOAEL). `IR-002-v1`'s superseded figure — which is architecturally unretrievable, excluded at ingestion time, not just filtered at display time — never appears. See the `supersedes` edge `ir_002_v2 → ir_002_v1` in [[knowledge_graph.json\|the knowledge graph]]. | Allowlist/version enforcement |
| 3 | "What Phase 3 renal fibrosis efficacy data exists for AXN-2401?" | all 4 | `refused=true`, `escalate_for_human_review=true`, zero citations — the program is pulmonary-only, there is nothing to cite, and the model doesn't invent something plausible-sounding. | Fail gracefully |
| 4 | "Is there a cardiac safety (hERG) signal for AXN-2401?" | all 4 | Non-empty `gaps_or_caveats`, or split claims citing `LIT-007` (external, IC50 4.8 µM) against `IR-006`/`CT-004` (internal, no signal) — both sides surfaced, neither silently dropped. Same contradiction is flagged in [[knowledge_graph.json\|the knowledge graph]]'s `contradiction_annotations`. | Fail gracefully / surface disagreement |

Bonus — domain-selection enforcement: repeat question 1 with `"domains": ["literature"]` only; every citation's `domain` field comes back `literature`.

Bonus — input validation (fixed after [[TRIAGE_REPORT]] findings 4.1/4.2): a blank/whitespace `question`, or a `domains` value outside the 4 recognized domains, now returns a `422` structured validation error — never a raw plain-text 500, never a silent widening of scope.

## 3. Show the audit trail behind one answer

- Full retrieval log: the collapsible panel in the frontend under any answered turn, or `retrieval_log` in the raw JSON — every `search_corpus` call made, with the query text and chunk IDs/domains/similarity returned.
- Live trace: `docker compose -f docker-compose.observability.yml up -d`, then re-run any query, then `docker compose -f docker-compose.observability.yml logs -f otel-collector` — a span per loop iteration and per tool call, with attributes for domains, chunk count, grounding pass/fail, retry count, confidence bucket. (Known scope reduction, stated up front: this ships the OpenTelemetry Collector with a stdout `debug` exporter, not the full SigNoz UI — see the comment block at the top of `docker-compose.observability.yml` for why and how to point it at real SigNoz.)
- MCP server: `backend/mcp_server.py` (registered in `.mcp.json`) exposes `search_corpus`, `allowlist_status`, `ingestion_status` as MCP tools over the same retrieval code the live app uses — this is what the P3-Triage-Agent used to probe retrieval independent of the FastAPI process.

## 4. Knowledge graph of the evidence base

- `graphify-out/graph.html` — open directly in a browser; interactive, community-clustered view of all 28 corpus documents plus extracted concepts (`AXN-2401`, `CRB3`, `PF-7`, `FBX9`, etc.), built by the installed `graphify` skill.
- `graphify-out/GRAPH_REPORT.md` — the audit report (god nodes, surprising cross-community connections, suggested questions).
- [[knowledge_graph.json]] — the plan-specific artifact: one node per allowlist document (`id`/`domain`/`title`/`version`/`status`), `supersedes` edges built directly from `data/allowlist.json` (ground truth, not inferred), domain clusters, and the `LIT-007`/`IR-006`/`CT-004` hERG contradiction annotation carried over from graphify's semantic-extraction pass (a subagent read all three documents in full before flagging it).

## 5. Test and load evidence

- `venv/bin/pytest tests -q --ignore=tests/load` — unit + integration coverage: allowlist loading, deterministic grounding validation (fabricated chunk_id, fabricated quote, non-current source_id — no LLM judge involved), confidence scoring, the retry-then-degrade path, live smoke tests against a running server.
- [[LOAD_TEST_REPORT]] — k6 run at up to 6 concurrent virtual users against the real end-to-end endpoint: 0% request failure, all thresholds passed, p95 latency reflects genuine multi-turn agentic loop cost (not a regression).

## 6. Known limitations to state up front, not discover live

- Load test is one run at a modest concurrency ceiling — see [[LOAD_TEST_REPORT]]'s "Known limitation."
- Observability ships the OTel Collector + stdout exporter, not a full SigNoz UI — see §3 above.
- RAG engine is scoped to an explicit MVP (paragraph-aware chunking, local embeddings, vector metadata filtering, top-k retrieval) — hybrid search, reranking, caching, and multi-vector retrieval were deliberately deferred; see [[AIDLC_PLAN]]'s "RAG engine" section.
- Corpus is small and synthetic (28 documents, fictional program) by design, so the model has no real-world knowledge to leak in as an uncited "fact."
