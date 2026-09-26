# Claude in Healthcare

Five simulated, representative reference-architecture designs for AI-in-healthcare case studies. Each lives entirely in its own project folder as a single `CLAUDE.md` (Overview, System Context, Architecture/HLD, Design Detail/LLD, Security & Compliance, Requirements & Spec, Differentiator, Assumptions & Out of Scope). Four are documentation only, with no shared code or infrastructure:

- [banner-health-physician-copilot/CLAUDE.md](banner-health-physician-copilot/CLAUDE.md) — physician-initiated note drafting & patient-record summarization. Also has a runnable reference implementation ([`SPEC.md`](banner-health-physician-copilot/SPEC.md), [`app/`](banner-health-physician-copilot/app/): FastAPI + Postgres/Redis/MinIO via Docker Compose, a mock FHIR server, Claude drafting with an independent Grok grounding review before any draft reaches the physician).
- [elation-chart-review-accelerator/CLAUDE.md](elation-chart-review-accelerator/CLAUDE.md) — chart-review acceleration layer for a primary-care EHR
- [qualified-health-cohort-identification/CLAUDE.md](qualified-health-cohort-identification/CLAUDE.md) — population-scale batch cohort screening for interventions
- [carta-clinical-data-extraction/CLAUDE.md](carta-clinical-data-extraction/CLAUDE.md) — batch clinical-document extraction with measured accuracy
- [commure-ambient-scribe/CLAUDE.md](commure-ambient-scribe/CLAUDE.md) — multi-tenant ambient encounter transcription & note generation

Each project's design doc is reviewed by three shared, project-parameterized agents in [.claude/agents/](.claude/agents/) (`security-review`, `performance-review`, `spec-compliance-review`), which append findings to that project's own `REVIEW.md`.
