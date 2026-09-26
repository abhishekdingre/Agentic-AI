# Running the reference implementation

Implements [`../SPEC.md`](../SPEC.md) (translated from [`../CLAUDE.md`](../CLAUDE.md)). All local, no real Azure, no real PHI.

## Run

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY and XAI_API_KEY in .env
docker compose up --build
```

- API: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:8080
- MinIO console: http://localhost:9001

## Demo logins

Seeded on first API startup (`auth/demo_users.py`):

| username | password | role |
|---|---|---|
| dr.alvarez | demo-pass | physician |
| dr.chen | demo-pass | physician |
| coordinator | demo-pass | care_coordinator |
| admin | demo-pass | admin |

`dr.alvarez` and `dr.chen` are attending on disjoint sets of the mock FHIR server's synthetic encounters, so ownership-rejection (403) is demoable by logging in as one and requesting the other's encounter.

## Smoke test

1. Log in as `dr.alvarez`.
2. `GET /api/v1/encounters` to see attending encounters.
3. `POST /api/v1/encounters/{id}/draft-note` — draft appears, Grok review runs before it's returned.
4. Try the same encounter as `dr.chen` — expect `403`.
5. Edit, then sign the note — archival job runs, signed note lands in the `signed-notes` MinIO bucket.
6. `X-Simulate-Outage: true` header against the mock FHIR server reproduces the `fhir_unavailable` failure path.
