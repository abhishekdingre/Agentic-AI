---
name: agentic-rag-poc
description: How to work in this repo — a grounded agentic-RAG POC for pharma literature review (allowlist enforcement, citation grounding, structured output, graceful refusal); directory layout, how to run ingestion/server/tests, and subagent scope boundaries.
---

# Agentic RAG POC — working in this repo

This repo is a proof of concept for a grounded, agentic retrieval system for drug-discovery literature review. Four commitments are mechanically enforced, not just prompted for — see [[../../docs/PROBLEM_STATEMENT]]:
1. Retrieve only from an approved-source allowlist.
2. Ground every claim to a citation checked against what was actually retrieved.
3. Return a fixed structured schema, never free text.
4. Fail gracefully (refuse/escalate) rather than fabricate.

Full architecture: [[../../docs/AIDLC_PLAN]]. Subagent isolation/delegation rules: [[../../docs/AGENT_OPERATING_MODEL]] — that file is written generically and is meant to be reused as-is on future multi-agent builds, not just this POC.

## Directory layout
- `data/` — `allowlist.json` + the synthetic corpus (`data/corpus/<domain>/*.md`). Owned by the backend agent.
- `backend/` — FastAPI app, agentic loop, RAG engine, grounding/confidence, schemas/tools, MCP server, telemetry. Owned by the backend agent.
- `frontend/` — vanilla HTML/CSS/JS chat UI. Owned by the frontend agent.
- `tests/` — unit tests + `tests/load/` k6 script. Owned by the backend agent.
- `docs/` — AIDLC artifacts (this is also the Obsidian vault). Owned by the orchestrator.

## Scope boundaries (enforced by convention, not by tooling)
- The backend agent never touches `frontend/`.
- The frontend agent never touches `backend/`, `data/`, or `tests/`.
- The P3-Triage-Agent is read-only across both — it files a report, it does not patch code.

## Running things
```bash
cd "Sixth POC"
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python backend/ingest.py        # embed the current-status corpus into Chroma
venv/bin/uvicorn backend.main:app --reload --port 8000
venv/bin/pytest tests -q                 # unit tests (no model calls)
venv/bin/python tests/smoke_test.py      # live smoke queries against the running server
```
Frontend is static — open `frontend/index.html` directly, or serve it with `python3 -m http.server` from `frontend/`.

## Conventions
- Strict Anthropic tool schemas throughout (`strict: true`) — derived from Pydantic v2 models with `ConfigDict(extra="forbid")`. No numeric `minimum`/`maximum` constraints (unsupported in strict mode).
- Never trust model-reported metadata (title/domain/version/similarity) — always re-derive server-side from the allowlist/retrieval log.
- Confidence is always computed server-side; never ask the model to self-report it.
