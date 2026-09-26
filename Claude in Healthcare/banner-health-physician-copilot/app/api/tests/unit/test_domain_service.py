"""Unit tests for `domain.service` — the orchestration layer wiring FHIR,
RBAC, Claude, Grok, persistence, and audit logging together (SPEC.md
§3/§5/§6/§7).

Uses a real in-memory SQLite engine (via `aiosqlite`) with the *full*
`Base.metadata` — unlike `test_rbac.py`'s comment claiming the Postgres-only
`JSONB`/`UUID` column types can't be rendered by SQLite, `Base.metadata.
create_all()` against `sqlite+aiosqlite://` was verified (scratch check,
outside this file) to create all four tables and round-trip a `DraftNote`
row correctly — SQLAlchemy's postgresql dialect types have generic-dialect
fallback compilation. So this file exercises the real DB-touching logic
(upsert, flush-assigned ids, status transitions) rather than only the pure
helpers.

FHIR and LLM calls are faked at the `domain.service` module level (the
names `service.py` imported directly into its own namespace), never a real
network call.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from banner_copilot.db import Base
from banner_copilot.domain import service as svc
from banner_copilot.domain.errors import (
    FhirUnavailableError,
    ImmutableNoteError,
    LlmUnavailableError,
    PermissionDeniedError,
    SignAcknowledgeRequiredError,
)
from banner_copilot.domain.models import AuditEvent, DraftNote, DraftNoteStatus, SummaryReviewStatus
from banner_copilot.fhir.client import FHIRNotFoundError, FHIRUnavailableError
from banner_copilot.fhir.resources import ATND_CODE, ATND_CODING_SYSTEM
from banner_copilot.llm.claude_drafting import ClaudeUnavailableError
from banner_copilot.llm.grok_review import GrokFinding, GrokReviewResult, GrokUnavailableError
from banner_copilot.llm.prompts import PatientSummary, SoapDraft

PHYSICIAN_ALVAREZ = {"sub": "dr.alvarez", "role": "physician", "practitioner_id": "practitioner-alvarez"}
PHYSICIAN_CHEN = {"sub": "dr.chen", "role": "physician", "practitioner_id": "practitioner-chen"}
CARE_COORDINATOR = {"sub": "coordinator", "role": "care_coordinator", "practitioner_id": None}

FHIR_ENCOUNTER = {
    "resourceType": "Encounter",
    "id": "encounter-1",
    "status": "finished",
    "subject": {"reference": "Patient/patient-1"},
    "participant": [
        {
            "type": [{"coding": [{"system": ATND_CODING_SYSTEM, "code": ATND_CODE}]}],
            "individual": {"reference": "Practitioner/practitioner-alvarez"},
        }
    ],
    "period": {"start": "2024-01-15T10:30:00+00:00", "end": "2024-01-15T11:00:00+00:00"},
}

LINKED = {
    "Condition": [{"code": "diabetes"}],
    "Observation": [],
    "MedicationRequest": [],
    "Procedure": [],
    "DocumentReference": [],
}

SOAP = SoapDraft(subjective="s", objective="o", assessment="a", plan="p")
SUMMARY = PatientSummary(summary="doing fine", key_points=["point 1"])
CLEAN_REVIEW = GrokReviewResult(grounded=True, findings=[], overall_assessment="fine")
FLAGGED_REVIEW = GrokReviewResult(
    grounded=False,
    findings=[GrokFinding(severity="critical", claim="x", issue="not in facts")],
    overall_assessment="ungrounded claim",
)


class FakeFHIRClient:
    def __init__(self, *, encounter=None, linked=None, fail: set[str] | None = None, not_found=False):
        self.encounter = encounter
        self.linked = linked or {}
        self.fail = fail or set()
        self.not_found = not_found

    async def get_encounter(self, encounter_id: str) -> dict:
        if "encounter" in self.fail:
            raise FHIRUnavailableError("down")
        if self.not_found:
            raise FHIRNotFoundError(encounter_id)
        return self.encounter

    async def fetch_linked_resources(self, patient_id: str) -> dict[str, list[dict]]:
        if "linked" in self.fail:
            raise FHIRUnavailableError("down")
        return self.linked


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _count(db, model) -> int:
    from sqlalchemy import func, select

    result = await db.execute(select(func.count()).select_from(model))
    return result.scalar_one()


def _patch_claude_draft(monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None) -> None:
    async def fake(facts: dict) -> tuple[SoapDraft, str]:
        if error is not None:
            raise error
        return SOAP, "claude-opus-5-test"

    monkeypatch.setattr(svc, "draft_soap_note", fake)


def _patch_claude_summary(monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None) -> None:
    async def fake(facts: dict) -> tuple[PatientSummary, str]:
        if error is not None:
            raise error
        return SUMMARY, "claude-opus-5-test"

    monkeypatch.setattr(svc, "summarize_patient", fake)


def _patch_grok_draft(monkeypatch: pytest.MonkeyPatch, *, review: GrokReviewResult | None = None, error: Exception | None = None) -> None:
    async def fake(*, facts: dict, draft: dict) -> GrokReviewResult:
        if error is not None:
            raise error
        return review

    monkeypatch.setattr(svc, "review_draft", fake)


def _patch_grok_summary(monkeypatch: pytest.MonkeyPatch, *, review: GrokReviewResult | None = None, error: Exception | None = None) -> None:
    async def fake(*, facts: dict, summary: dict) -> GrokReviewResult:
        if error is not None:
            raise error
        return review

    monkeypatch.setattr(svc, "review_summary", fake)


# ---------------------------------------------------------------------------
# draft_note
# ---------------------------------------------------------------------------


async def test_draft_note_clean_review_success(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, review=CLEAN_REVIEW)
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)

    note = await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert note.status == DraftNoteStatus.DRAFTED
    assert note.draft_text == SOAP.model_dump()
    assert note.claude_model == "claude-opus-5-test"
    assert note.grok_review["grounded"] is True
    assert await _count(db_session, AuditEvent) >= 3  # fhir x2, claude, grok, note.draft


async def test_draft_note_critical_finding_flags(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, review=FLAGGED_REVIEW)
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)

    note = await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert note.status == DraftNoteStatus.DRAFTED_FLAGGED


async def test_draft_note_grok_unavailable_marks_unverified(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, error=GrokUnavailableError("timeout"))
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)

    note = await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert note.status == DraftNoteStatus.DRAFTED_UNVERIFIED
    assert note.grok_review is None


async def test_draft_note_idempotent_returns_existing(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, review=CLEAN_REVIEW)
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)

    first = await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)
    second = await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert first.id == second.id
    assert await _count(db_session, DraftNote) == 1


async def test_draft_note_permission_denied_no_row_created(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, review=CLEAN_REVIEW)
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)

    with pytest.raises(PermissionDeniedError):
        await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_CHEN)

    assert await _count(db_session, DraftNote) == 0
    assert await _count(db_session, AuditEvent) == 2  # fhir.read success + note.draft failure


async def test_draft_note_fhir_unavailable_on_gate_no_row_created(db_session, monkeypatch):
    fhir = FakeFHIRClient(fail={"encounter"})

    with pytest.raises(FhirUnavailableError):
        await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert await _count(db_session, DraftNote) == 0


async def test_draft_note_fhir_unavailable_on_linked_fetch_no_row_created(db_session, monkeypatch):
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, fail={"linked"})

    with pytest.raises(FhirUnavailableError):
        await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert await _count(db_session, DraftNote) == 0


async def test_draft_note_claude_failure_persists_failed_row(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch, error=ClaudeUnavailableError("boom"))
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)

    with pytest.raises(LlmUnavailableError):
        await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert await _count(db_session, DraftNote) == 1
    stored = (await db_session.execute(DraftNote.__table__.select())).mappings().first()
    assert stored["status"] == DraftNoteStatus.FAILED.value
    assert stored["failure_reason"] == "llm_unavailable"


async def test_draft_note_failed_row_is_retried_without_regenerate_flag(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch, error=ClaudeUnavailableError("boom"))
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)
    with pytest.raises(LlmUnavailableError):
        await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, review=CLEAN_REVIEW)
    note = await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    assert note.status == DraftNoteStatus.DRAFTED
    assert await _count(db_session, DraftNote) == 2


# ---------------------------------------------------------------------------
# patch_note / sign_note
# ---------------------------------------------------------------------------


async def _drafted_note(db_session, monkeypatch) -> DraftNote:
    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, review=CLEAN_REVIEW)
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)
    return await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)


async def test_patch_note_computes_diff_and_sets_edited(db_session, monkeypatch):
    note = await _drafted_note(db_session, monkeypatch)

    edited = {"subjective": "s2", "objective": "o", "assessment": "a", "plan": "p"}
    updated = await svc.patch_note(db_session, note.id, PHYSICIAN_ALVAREZ, edited)

    assert updated.status == DraftNoteStatus.EDITED
    assert updated.edited_text == edited
    assert updated.diff == {"subjective": {"before": "s", "after": "s2"}}


async def test_patch_note_wrong_creator_denied(db_session, monkeypatch):
    note = await _drafted_note(db_session, monkeypatch)

    with pytest.raises(PermissionDeniedError):
        await svc.patch_note(db_session, note.id, PHYSICIAN_CHEN, {"subjective": "x"})


async def test_patch_note_rejects_signed_note(db_session, monkeypatch):
    note = await _drafted_note(db_session, monkeypatch)
    await svc.sign_note(db_session, note.id, PHYSICIAN_ALVAREZ)

    with pytest.raises(ImmutableNoteError):
        await svc.patch_note(db_session, note.id, PHYSICIAN_ALVAREZ, {"subjective": "x"})


async def test_sign_note_flagged_requires_acknowledge(db_session, monkeypatch):
    _patch_claude_draft(monkeypatch)
    _patch_grok_draft(monkeypatch, review=FLAGGED_REVIEW)
    fhir = FakeFHIRClient(encounter=FHIR_ENCOUNTER, linked=LINKED)
    note = await svc.draft_note(db_session, fhir, encounter_id="encounter-1", claims=PHYSICIAN_ALVAREZ)

    with pytest.raises(SignAcknowledgeRequiredError):
        await svc.sign_note(db_session, note.id, PHYSICIAN_ALVAREZ)

    signed = await svc.sign_note(db_session, note.id, PHYSICIAN_ALVAREZ, acknowledge_flags=True)
    assert signed.status == DraftNoteStatus.SIGNED
    assert signed.signed_at is not None


async def test_sign_note_enqueues_archival(db_session, monkeypatch):
    note = await _drafted_note(db_session, monkeypatch)
    enqueued: list[str] = []

    async def fake_enqueue(note_id: str) -> None:
        enqueued.append(note_id)

    signed = await svc.sign_note(db_session, note.id, PHYSICIAN_ALVAREZ, enqueue_archival=fake_enqueue)

    assert enqueued == [str(signed.id)]


async def test_sign_note_already_signed_rejected(db_session, monkeypatch):
    note = await _drafted_note(db_session, monkeypatch)
    await svc.sign_note(db_session, note.id, PHYSICIAN_ALVAREZ)

    with pytest.raises(ImmutableNoteError):
        await svc.sign_note(db_session, note.id, PHYSICIAN_ALVAREZ)


# ---------------------------------------------------------------------------
# generate_summary
# ---------------------------------------------------------------------------


async def _seed_encounter_mirror(db_session) -> None:
    from datetime import UTC, datetime

    from banner_copilot.domain.models import Encounter

    db_session.add(
        Encounter(
            id="encounter-1",
            patient_id="patient-1",
            attending_practitioner_id="practitioner-alvarez",
            status="finished",
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
            synced_at=datetime.now(UTC),
        )
    )
    await db_session.commit()


async def test_generate_summary_owning_physician_clean(db_session, monkeypatch):
    await _seed_encounter_mirror(db_session)
    _patch_claude_summary(monkeypatch)
    _patch_grok_summary(monkeypatch, review=CLEAN_REVIEW)
    fhir = FakeFHIRClient(linked=LINKED)

    request = await svc.generate_summary(db_session, fhir, patient_id="patient-1", claims=PHYSICIAN_ALVAREZ)

    assert request.review_status == SummaryReviewStatus.CLEAN
    assert request.summary_text == SUMMARY.model_dump()


async def test_generate_summary_non_owning_physician_denied(db_session, monkeypatch):
    await _seed_encounter_mirror(db_session)
    fhir = FakeFHIRClient(linked=LINKED)

    with pytest.raises(PermissionDeniedError):
        await svc.generate_summary(db_session, fhir, patient_id="patient-1", claims=PHYSICIAN_CHEN)


async def test_generate_summary_care_coordinator_allowed_and_logged(db_session, monkeypatch):
    _patch_claude_summary(monkeypatch)
    _patch_grok_summary(monkeypatch, review=CLEAN_REVIEW)
    fhir = FakeFHIRClient(linked=LINKED)

    await svc.generate_summary(db_session, fhir, patient_id="patient-1", claims=CARE_COORDINATOR)

    events = (await db_session.execute(AuditEvent.__table__.select())).mappings().all()
    assert any(e["action"] == "summary.coordinator_read" for e in events)


async def test_generate_summary_grok_unavailable_marks_unverified(db_session, monkeypatch):
    await _seed_encounter_mirror(db_session)
    _patch_claude_summary(monkeypatch)
    _patch_grok_summary(monkeypatch, error=GrokUnavailableError("timeout"))
    fhir = FakeFHIRClient(linked=LINKED)

    request = await svc.generate_summary(db_session, fhir, patient_id="patient-1", claims=PHYSICIAN_ALVAREZ)

    assert request.review_status == SummaryReviewStatus.UNVERIFIED
    assert request.grok_review is None


async def test_generate_summary_claude_failure_raises_no_row(db_session, monkeypatch):
    await _seed_encounter_mirror(db_session)
    _patch_claude_summary(monkeypatch, error=ClaudeUnavailableError("boom"))
    fhir = FakeFHIRClient(linked=LINKED)

    from banner_copilot.domain.models import SummaryRequest

    with pytest.raises(LlmUnavailableError):
        await svc.generate_summary(db_session, fhir, patient_id="patient-1", claims=PHYSICIAN_ALVAREZ)

    assert await _count(db_session, SummaryRequest) == 0
