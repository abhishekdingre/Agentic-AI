# Problem Statement

## Who this is for
A pharma R&D team (drug discovery / literature-review function at a company like Eli Lilly) that needs to answer research questions — "what evidence supports target X for indication Y," "what NOAEL was established for compound Z" — by pulling evidence out of literature, patents, clinical trial registries, and internal reports.

## The problem
Doing this by hand today creates three recurring failure modes:
1. **Information overload** — relevant evidence is scattered across four differently-owned, differently-formatted source types.
2. **Version drift** — internal reports get superseded (e.g. a corrected NOAEL value), and nothing stops someone from citing the stale version.
3. **Unverifiable claims** — a written answer whose citations don't actually hold up when someone checks the source ("did the report really say that?").

A generic LLM chatbot on top of these documents makes all three worse: it can quote from anything (including retracted/superseded material), it can state things with no real citation behind them, and it will produce free text that's hard to audit or feed into a downstream system.

## What this system must do
Given a research question and a user-selected set of domains (Literature, Patents, Clinical Trials, Internal Reports), retrieve evidence and produce a structured, auditable answer — **without making the scientific judgment call itself**. The system's job is to surface evidence and flag its own confidence/gaps; a human scientist makes the actual decision.

## Four hard commitments (mechanically enforced, not just prompted for)
1. **Retrieve only from an approved-source allowlist**, enforced up front (ingestion-time + query-time + runtime defense-in-depth) — not just "please only use approved sources" in a system prompt.
2. **Ground every claim to a citation** that is checked, server-side, against what was actually retrieved this session — never trust the model's self-reported citation.
3. **Return a fixed structured schema, never free text** — every response is machine-validated JSON with claims, citations, confidence, and gaps.
4. **Fail gracefully rather than fabricate** — thin, absent, or contradictory evidence produces a refusal or an escalation flag, never an invented answer.

## Explicitly out of scope for this POC
- Making or recommending the scientific decision itself.
- Ingesting real, non-synthetic pharma data (the corpus is a self-authored synthetic program so there's no real-world knowledge for the model to leak in as an uncited "fact").
- Authentication/authorization, multi-tenant access control.
- Production-grade scale (this is a local, single-user proof of concept).

## Success looks like
A user picks domains, asks a question, and gets back: grounded claims with real citations, a confidence rating that's computed (not self-reported), visible gaps/contradictions when evidence is thin or conflicting, and — for at least one deliberately-constructed test question — an honest refusal instead of a fabricated answer.
