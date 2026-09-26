"""Mock FHIR R4 server — synthetic fixtures for the Banner Health Physician Copilot.

Standalone FastAPI app implementing the subset of FHIR R4 REST semantics the
real app depends on (SPEC.md §4):

- ``GET /Encounter/{id}``               — read interaction, raw resource JSON.
- ``GET /Condition?patient={id}``       — search interaction, Bundle of matches.
- ``GET /Observation?patient={id}``     — search interaction, Bundle of matches.
- ``GET /MedicationRequest?patient={id}`` — search interaction, Bundle of matches.
- ``GET /Procedure?patient={id}``       — search interaction, Bundle of matches.
- ``GET /DocumentReference?patient={id}`` — search interaction, Bundle of matches.
- ``GET /health``                       — plain liveness check for the Compose
  healthcheck; deliberately NOT gated by the outage simulator below.

All fixture data is static synthetic JSON loaded into memory once at import
time from ``fixtures/`` (see ``FIXTURES_DIR``). Nothing here is a source of
real PHI.

**Outage simulation**: every route except ``/health`` honors a
``X-Simulate-Outage: true`` request header by returning ``503`` instead of
its normal response, but only when the ``ENV`` environment variable is
``"test"`` or ``"local"`` (unset/missing ``ENV`` is treated as ``"local"``).
This lets the real API's integration tests deterministically exercise its
FHIR-unavailability circuit-breaker path (SPEC.md §7) without this behavior
ever being reachable in a production-configured deployment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

# main.py lives at <mock_fhir_server>/src/mock_fhir/main.py; fixtures live at
# <mock_fhir_server>/fixtures/. This resolves correctly both in local dev and
# inside the Docker image (COPY'd to /app/src and /app/fixtures respectively).
FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _load_fixture(filename: str) -> list[dict[str, Any]]:
    with open(FIXTURES_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


PRACTITIONERS: list[dict[str, Any]] = _load_fixture("practitioners.json")
PATIENTS: list[dict[str, Any]] = _load_fixture("patients.json")
ENCOUNTERS: list[dict[str, Any]] = _load_fixture("encounters.json")
CONDITIONS: list[dict[str, Any]] = _load_fixture("conditions.json")
OBSERVATIONS: list[dict[str, Any]] = _load_fixture("observations.json")
MEDICATION_REQUESTS: list[dict[str, Any]] = _load_fixture("medication_requests.json")
PROCEDURES: list[dict[str, Any]] = _load_fixture("procedures.json")
DOCUMENT_REFERENCES: list[dict[str, Any]] = _load_fixture("document_references.json")

ENCOUNTERS_BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in ENCOUNTERS}

app = FastAPI(title="Mock FHIR R4 Server", version="0.1.0")


def _outage_simulation_enabled() -> bool:
    """Whether the running environment permits the outage header to fire at all."""
    env = os.environ.get("ENV", "local")
    return env in ("test", "local")


async def simulate_outage_guard(
    x_simulate_outage: str | None = Header(default=None, alias="X-Simulate-Outage"),
) -> None:
    """FastAPI dependency: raise 503 if outage simulation is requested and permitted.

    Applied to every PHI-bearing route except /health.
    """
    if (
        x_simulate_outage is not None
        and x_simulate_outage.lower() == "true"
        and _outage_simulation_enabled()
    ):
        raise HTTPException(status_code=503, detail={"error": "simulated_outage"})


def _operation_outcome(diagnostics: str) -> dict[str, Any]:
    """Minimal FHIR OperationOutcome for a not-found read."""
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "not-found",
                "diagnostics": diagnostics,
            }
        ],
    }


def _searchset_bundle(resources: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a list of resources in a FHIR searchset Bundle (empty entry if none)."""
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": resource} for resource in resources],
    }


def _filter_by_patient(resources: list[dict[str, Any]], patient_id: str) -> list[dict[str, Any]]:
    reference = f"Patient/{patient_id}"
    return [r for r in resources if r.get("subject", {}).get("reference") == reference]


@app.get("/health")
async def health() -> dict[str, str]:
    """Plain liveness check. Not gated by the outage simulator — health checks
    (including the Compose healthcheck wired to this exact path) must stay
    simulator-proof so orchestration doesn't itself get caught by test tooling.
    """
    return {"status": "ok"}


@app.get("/Encounter/{encounter_id}", dependencies=[Depends(simulate_outage_guard)])
async def read_encounter(encounter_id: str) -> Any:
    """FHIR read interaction: returns the raw Encounter resource, or a 404
    OperationOutcome if the id is unknown."""
    encounter = ENCOUNTERS_BY_ID.get(encounter_id)
    if encounter is None:
        return JSONResponse(
            status_code=404,
            content=_operation_outcome(f"Encounter/{encounter_id} not found"),
        )
    return encounter


@app.get("/Condition", dependencies=[Depends(simulate_outage_guard)])
async def search_conditions(patient: str) -> dict[str, Any]:
    return _searchset_bundle(_filter_by_patient(CONDITIONS, patient))


@app.get("/Observation", dependencies=[Depends(simulate_outage_guard)])
async def search_observations(patient: str) -> dict[str, Any]:
    return _searchset_bundle(_filter_by_patient(OBSERVATIONS, patient))


@app.get("/MedicationRequest", dependencies=[Depends(simulate_outage_guard)])
async def search_medication_requests(patient: str) -> dict[str, Any]:
    return _searchset_bundle(_filter_by_patient(MEDICATION_REQUESTS, patient))


@app.get("/Procedure", dependencies=[Depends(simulate_outage_guard)])
async def search_procedures(patient: str) -> dict[str, Any]:
    return _searchset_bundle(_filter_by_patient(PROCEDURES, patient))


@app.get("/DocumentReference", dependencies=[Depends(simulate_outage_guard)])
async def search_document_references(patient: str) -> dict[str, Any]:
    return _searchset_bundle(_filter_by_patient(DOCUMENT_REFERENCES, patient))
