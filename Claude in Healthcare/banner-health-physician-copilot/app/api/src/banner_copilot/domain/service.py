"""Orchestration layer (SPEC.md §3/§5/§6/§7): wires the FHIR client, RBAC
ownership checks, the two LLM modules, persistence, and audit logging
together into the five flows the routes layer (not yet built) will call.

Every function here either returns a persisted row or raises one of
`domain.errors`' exceptions — never a partial/fabricated draft or summary
(FR-2/FR-5). Each function commits its own transaction: a failure path logs
an `AuditEvent` and commits *before* raising, so the audit trail survives
even though the request itself fails.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banner_copilot.audit.logger import log_event
from banner_copilot.auth.rbac import check_encounter_ownership, check_patient_ownership
from banner_copilot.domain.errors import (
    FhirUnavailableError,
    ImmutableNoteError,
    LlmUnavailableError,
    NotFoundError,
    PermissionDeniedError,
    SignAcknowledgeRequiredError,
)
from banner_copilot.domain.models import (
    AuditOutcome,
    DraftNote,
    DraftNoteStatus,
    Encounter,
    SummaryRequest,
    SummaryReviewStatus,
)
from banner_copilot.fhir.client import FHIRClient, FHIRNotFoundError, FHIRUnavailableError
from banner_copilot.fhir.resources import get_attending_practitioner_id, get_patient_id, get_period
from banner_copilot.llm.claude_drafting import ClaudeUnavailableError, draft_soap_note, summarize_patient
from banner_copilot.llm.grok_review import GrokUnavailableError, has_critical_finding, review_draft, review_summary
from banner_copilot.llm.prompts import PROMPT_VERSION


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _compute_diff(original: dict[str, Any], edited: dict[str, Any]) -> dict[str, Any]:
    """Field-level diff of `edited` against the original Claude draft
    (SPEC.md §2: `diff (edited vs draft)`) — not against any intermediate edit."""
    return {
        field: {"before": original.get(field), "after": value}
        for field, value in edited.items()
        if original.get(field) != value
    }


def _can_view_note(note: DraftNote, claims: dict[str, Any]) -> bool:
    """SPEC.md §3 lists `admin` among the roles that can view a note, but §5
    states `admin` has no PHI routes at all. §5 is the section written to close
    the Security P1 gap, so it wins here: `admin` is denied."""
    role = claims.get("role")
    if role == "physician":
        return note.created_by == claims.get("sub")
    return role == "care_coordinator"


async def _latest_draft_note(db: AsyncSession, encounter_id: str) -> DraftNote | None:
    stmt = (
        select(DraftNote)
        .where(DraftNote.encounter_id == encounter_id)
        .order_by(DraftNote.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _sync_encounter_mirror(db: AsyncSession, encounter_id: str, fhir_encounter: dict) -> Encounter:
    """Upsert the local `Encounter` mirror from a freshly-fetched FHIR resource.

    Called on every draft-note request (SPEC.md §5: ownership is checked
    against a freshly re-resolved Encounter, never a stale cached claim
    alone) — `fhir_client.get_encounter`'s own short-TTL cache is the "or the
    local mirror if fresh" half of that rule, so this function itself does
    not need its own freshness logic.
    """
    patient_id = get_patient_id(fhir_encounter)
    attending_practitioner_id = get_attending_practitioner_id(fhir_encounter)
    if patient_id is None or attending_practitioner_id is None:
        raise NotFoundError(f"Encounter {encounter_id} is missing subject or attending practitioner")

    period_start, period_end = get_period(fhir_encounter)
    encounter = await db.get(Encounter, encounter_id)
    if encounter is None:
        encounter = Encounter(id=encounter_id)
        db.add(encounter)
    encounter.patient_id = patient_id
    encounter.attending_practitioner_id = attending_practitioner_id
    encounter.status = fhir_encounter.get("status", "unknown")
    encounter.period_start = _parse_datetime(period_start)
    encounter.period_end = _parse_datetime(period_end)
    encounter.synced_at = datetime.now(UTC)
    await db.flush()
    return encounter


async def draft_note(
    db: AsyncSession,
    fhir_client: FHIRClient,
    *,
    encounter_id: str,
    claims: dict[str, Any],
    regenerate: bool = False,
) -> DraftNote:
    """Draft (or return the existing) SOAP note for an encounter (SPEC.md §3/§6/§7).

    Idempotent: a `drafted*`/`edited` note for this encounter is returned
    as-is unless `regenerate=True`; a `signed` note is always returned as-is
    (immutable); a `failed` note is always retried.
    """
    actor = claims["sub"]
    caller_practitioner_id = claims.get("practitioner_id")

    existing = await _latest_draft_note(db, encounter_id)
    if existing is not None:
        if existing.status == DraftNoteStatus.SIGNED:
            return existing
        if existing.status != DraftNoteStatus.FAILED and not regenerate:
            return existing

    try:
        fhir_encounter = await fhir_client.get_encounter(encounter_id)
    except FHIRNotFoundError as exc:
        raise NotFoundError(f"Encounter {encounter_id} not found") from exc
    except FHIRUnavailableError as exc:
        await log_event(
            db,
            actor=actor,
            action="fhir.read",
            resource_type="Encounter",
            resource_id=encounter_id,
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "fhir_unavailable"},
        )
        await db.commit()
        raise FhirUnavailableError(str(exc)) from exc

    await log_event(
        db,
        actor=actor,
        action="fhir.read",
        resource_type="Encounter",
        resource_id=encounter_id,
        outcome=AuditOutcome.SUCCESS,
    )

    encounter = await _sync_encounter_mirror(db, encounter_id, fhir_encounter)

    if not check_encounter_ownership(encounter, caller_practitioner_id):
        await log_event(
            db,
            actor=actor,
            action="note.draft",
            resource_type="Encounter",
            resource_id=encounter_id,
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "permission_denied"},
        )
        await db.commit()
        raise PermissionDeniedError(f"{actor} is not the attending practitioner for encounter {encounter_id}")

    try:
        linked = await fhir_client.fetch_linked_resources(encounter.patient_id)
    except FHIRUnavailableError as exc:
        await log_event(
            db,
            actor=actor,
            action="fhir.read",
            resource_type="Patient",
            resource_id=encounter.patient_id,
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "fhir_unavailable"},
        )
        await db.commit()
        raise FhirUnavailableError(str(exc)) from exc

    await log_event(
        db,
        actor=actor,
        action="fhir.read",
        resource_type="Patient",
        resource_id=encounter.patient_id,
        outcome=AuditOutcome.SUCCESS,
        detail={"resource_types": list(linked)},
    )

    facts = {"Encounter": fhir_encounter, **linked}

    try:
        draft, claude_model = await draft_soap_note(facts)
    except ClaudeUnavailableError as exc:
        note = DraftNote(
            encounter_id=encounter_id,
            status=DraftNoteStatus.FAILED,
            failure_reason="llm_unavailable",
            prompt_template_version=PROMPT_VERSION,
            created_by=actor,
        )
        db.add(note)
        await db.flush()
        await log_event(
            db,
            actor=actor,
            action="llm.claude_call",
            resource_type="DraftNote",
            resource_id=str(note.id),
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "llm_unavailable"},
        )
        await db.commit()
        raise LlmUnavailableError(str(exc)) from exc

    await log_event(
        db,
        actor=actor,
        action="llm.claude_call",
        resource_type="Encounter",
        resource_id=encounter_id,
        outcome=AuditOutcome.SUCCESS,
    )

    draft_dict = draft.model_dump()
    try:
        review = await review_draft(facts=facts, draft=draft_dict)
    except GrokUnavailableError:
        status_ = DraftNoteStatus.DRAFTED_UNVERIFIED
        grok_review_payload = None
        await log_event(
            db,
            actor=actor,
            action="llm.grok_call",
            resource_type="Encounter",
            resource_id=encounter_id,
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "grok_unavailable"},
        )
    else:
        grok_review_payload = review.model_dump()
        status_ = DraftNoteStatus.DRAFTED_FLAGGED if has_critical_finding(review) else DraftNoteStatus.DRAFTED
        await log_event(
            db,
            actor=actor,
            action="llm.grok_call",
            resource_type="Encounter",
            resource_id=encounter_id,
            outcome=AuditOutcome.SUCCESS,
            detail={"grounded": review.grounded},
        )

    note = DraftNote(
        encounter_id=encounter_id,
        status=status_,
        draft_text=draft_dict,
        prompt_template_version=PROMPT_VERSION,
        claude_model=claude_model,
        grok_review=grok_review_payload,
        created_by=actor,
    )
    db.add(note)
    await db.flush()
    await log_event(
        db,
        actor=actor,
        action="note.draft",
        resource_type="DraftNote",
        resource_id=str(note.id),
        outcome=AuditOutcome.SUCCESS,
        detail={"status": status_.value},
    )
    await db.commit()
    return note


async def get_note(db: AsyncSession, note_id: Any, claims: dict[str, Any]) -> DraftNote:
    """Fetch a `DraftNote` (SPEC.md §3). Raises `NotFoundError`/`PermissionDeniedError`."""
    note = await db.get(DraftNote, note_id)
    if note is None:
        raise NotFoundError(f"DraftNote {note_id} not found")
    if not _can_view_note(note, claims):
        raise PermissionDeniedError(f"{claims.get('sub')} may not view DraftNote {note_id}")
    return note


async def patch_note(db: AsyncSession, note_id: Any, claims: dict[str, Any], edited_text: dict[str, Any]) -> DraftNote:
    """Physician edit (SPEC.md §3): stores a diff against the original draft, status -> `edited`."""
    actor = claims["sub"]
    note = await db.get(DraftNote, note_id)
    if note is None:
        raise NotFoundError(f"DraftNote {note_id} not found")
    if claims.get("role") != "physician" or note.created_by != actor:
        raise PermissionDeniedError(f"{actor} may not edit DraftNote {note_id}")
    if note.status == DraftNoteStatus.SIGNED:
        raise ImmutableNoteError(f"DraftNote {note_id} is already signed")

    note.diff = _compute_diff(note.draft_text or {}, edited_text)
    note.edited_text = edited_text
    note.status = DraftNoteStatus.EDITED
    await log_event(
        db,
        actor=actor,
        action="note.edit",
        resource_type="DraftNote",
        resource_id=str(note.id),
        outcome=AuditOutcome.SUCCESS,
    )
    await db.commit()
    return note


async def sign_note(
    db: AsyncSession,
    note_id: Any,
    claims: dict[str, Any],
    *,
    acknowledge_flags: bool = False,
    enqueue_archival: Callable[[str], Awaitable[None]] | None = None,
) -> DraftNote:
    """Finalize a note (SPEC.md §3). Immutable after this point.

    A `drafted_flagged`/`drafted_unverified` note requires
    `acknowledge_flags=True` or is rejected (`SignAcknowledgeRequiredError`,
    maps to 409) — a physician can still sign over a flag (FR-2: human makes
    the final call), but never by accident.
    """
    actor = claims["sub"]
    note = await db.get(DraftNote, note_id)
    if note is None:
        raise NotFoundError(f"DraftNote {note_id} not found")
    if claims.get("role") != "physician" or note.created_by != actor:
        raise PermissionDeniedError(f"{actor} may not sign DraftNote {note_id}")
    if note.status == DraftNoteStatus.SIGNED:
        raise ImmutableNoteError(f"DraftNote {note_id} is already signed")
    if note.status in (DraftNoteStatus.DRAFTED_FLAGGED, DraftNoteStatus.DRAFTED_UNVERIFIED) and not acknowledge_flags:
        raise SignAcknowledgeRequiredError(f"DraftNote {note_id} has unacknowledged flags")

    note.status = DraftNoteStatus.SIGNED
    note.signed_at = datetime.now(UTC)
    await log_event(
        db,
        actor=actor,
        action="note.sign",
        resource_type="DraftNote",
        resource_id=str(note.id),
        outcome=AuditOutcome.SUCCESS,
        detail={"acknowledged_flags": acknowledge_flags},
    )
    await db.commit()

    if enqueue_archival is not None:
        await enqueue_archival(str(note.id))
    return note


async def generate_summary(
    db: AsyncSession,
    fhir_client: FHIRClient,
    *,
    patient_id: str,
    claims: dict[str, Any],
) -> SummaryRequest:
    """Summarize a patient's longitudinal record (SPEC.md §3/§5/§6).

    Ownership for the physician role is checked against the local
    `Encounter` mirror only (SPEC.md §5's "own patients" rule stated without
    a live-re-fetch requirement, unlike the encounter-scoped draft-note
    flow) — no gate fetch, since there is no single Encounter to gate on.
    `care_coordinator` gets unscoped read access, but every such read is its
    own audited action (`summary.coordinator_read`, SPEC.md §5).
    """
    actor = claims["sub"]
    role = claims.get("role")
    caller_practitioner_id = claims.get("practitioner_id")

    if role == "physician":
        owns = await check_patient_ownership(db, patient_id, caller_practitioner_id)
        if not owns:
            await log_event(
                db,
                actor=actor,
                action="summary.generate",
                resource_type="Patient",
                resource_id=patient_id,
                outcome=AuditOutcome.FAILURE,
                detail={"reason": "permission_denied"},
            )
            await db.commit()
            raise PermissionDeniedError(f"{actor} has no attended encounters for patient {patient_id}")
    elif role == "care_coordinator":
        await log_event(
            db,
            actor=actor,
            action="summary.coordinator_read",
            resource_type="Patient",
            resource_id=patient_id,
            outcome=AuditOutcome.SUCCESS,
        )
    else:
        raise PermissionDeniedError(f"role '{role}' cannot request patient summaries")

    try:
        linked = await fhir_client.fetch_linked_resources(patient_id)
    except FHIRUnavailableError as exc:
        await log_event(
            db,
            actor=actor,
            action="fhir.read",
            resource_type="Patient",
            resource_id=patient_id,
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "fhir_unavailable"},
        )
        await db.commit()
        raise FhirUnavailableError(str(exc)) from exc

    await log_event(
        db,
        actor=actor,
        action="fhir.read",
        resource_type="Patient",
        resource_id=patient_id,
        outcome=AuditOutcome.SUCCESS,
        detail={"resource_types": list(linked)},
    )

    facts = linked

    try:
        summary, claude_model = await summarize_patient(facts)
    except ClaudeUnavailableError as exc:
        await log_event(
            db,
            actor=actor,
            action="llm.claude_call",
            resource_type="Patient",
            resource_id=patient_id,
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "llm_unavailable"},
        )
        await db.commit()
        raise LlmUnavailableError(str(exc)) from exc

    await log_event(
        db,
        actor=actor,
        action="llm.claude_call",
        resource_type="Patient",
        resource_id=patient_id,
        outcome=AuditOutcome.SUCCESS,
    )

    summary_dict = summary.model_dump()
    try:
        review = await review_summary(facts=facts, summary=summary_dict)
    except GrokUnavailableError:
        review_status = SummaryReviewStatus.UNVERIFIED
        grok_review_payload = None
        await log_event(
            db,
            actor=actor,
            action="llm.grok_call",
            resource_type="Patient",
            resource_id=patient_id,
            outcome=AuditOutcome.FAILURE,
            detail={"reason": "grok_unavailable"},
        )
    else:
        grok_review_payload = review.model_dump()
        review_status = SummaryReviewStatus.FLAGGED if has_critical_finding(review) else SummaryReviewStatus.CLEAN
        await log_event(
            db,
            actor=actor,
            action="llm.grok_call",
            resource_type="Patient",
            resource_id=patient_id,
            outcome=AuditOutcome.SUCCESS,
            detail={"grounded": review.grounded},
        )

    request = SummaryRequest(
        patient_id=patient_id,
        summary_text=summary_dict,
        review_status=review_status,
        prompt_template_version=PROMPT_VERSION,
        claude_model=claude_model,
        grok_review=grok_review_payload,
    )
    db.add(request)
    await db.flush()
    await log_event(
        db,
        actor=actor,
        action="summary.generate",
        resource_type="SummaryRequest",
        resource_id=str(request.id),
        outcome=AuditOutcome.SUCCESS,
        detail={"review_status": review_status.value},
    )
    await db.commit()
    return request
