"""Tests for the mock FHIR server (SPEC.md §4).

Covers: read-known-Encounter (with correct attending practitioner
reference), read-unknown-Encounter 404, a non-empty search Bundle, the
X-Simulate-Outage 503 path (and that it is inert outside test/local ENV),
and that /health ignores the outage header entirely.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from mock_fhir.main import app

client = TestClient(app)


def test_read_known_encounter_returns_attending_practitioner() -> None:
    resp = client.get("/Encounter/encounter-1")
    assert resp.status_code == 200

    body = resp.json()
    assert body["resourceType"] == "Encounter"
    assert body["id"] == "encounter-1"

    attending = [
        participant
        for participant in body["participant"]
        for type_entry in participant.get("type", [])
        for coding in type_entry.get("coding", [])
        if coding.get("code") == "ATND"
    ]
    assert len(attending) == 1
    assert attending[0]["individual"]["reference"] == "Practitioner/practitioner-alvarez"


def test_read_unknown_encounter_returns_404_operation_outcome() -> None:
    resp = client.get("/Encounter/does-not-exist")
    assert resp.status_code == 404

    body = resp.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["severity"] == "error"


def test_condition_search_returns_nonempty_bundle_for_known_patient() -> None:
    resp = client.get("/Condition", params={"patient": "patient-1"})
    assert resp.status_code == 200

    body = resp.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    assert len(body["entry"]) > 0
    for entry in body["entry"]:
        assert entry["resource"]["subject"]["reference"] == "Patient/patient-1"


def test_search_returns_empty_bundle_not_404_for_patient_with_no_matches() -> None:
    resp = client.get("/Condition", params={"patient": "patient-does-not-exist"})
    assert resp.status_code == 200

    body = resp.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    assert body["entry"] == []


def test_simulate_outage_header_returns_503(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "test")
    resp = client.get("/Encounter/encounter-1", headers={"X-Simulate-Outage": "true"})
    assert resp.status_code == 503


def test_simulate_outage_header_also_applies_to_search_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "local")
    resp = client.get(
        "/Observation",
        params={"patient": "patient-1"},
        headers={"X-Simulate-Outage": "true"},
    )
    assert resp.status_code == 503


def test_simulate_outage_header_is_inert_outside_test_or_local_env(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "production")
    resp = client.get("/Encounter/encounter-1", headers={"X-Simulate-Outage": "true"})
    assert resp.status_code == 200


def test_health_ignores_outage_header(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "test")
    resp = client.get("/health", headers={"X-Simulate-Outage": "true"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
