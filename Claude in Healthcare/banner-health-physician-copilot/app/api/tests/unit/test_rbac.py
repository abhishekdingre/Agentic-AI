"""Unit tests for `banner_copilot.auth.rbac` ownership predicates (SPEC.md §14).

Uses a temporary in-memory SQLite engine (via `aiosqlite`) instead of a live
Postgres instance, per SPEC.md §14 ("without hitting any live Postgres").
Only the `encounters` table is created here — not the full `Base.metadata`
— because `DraftNote`/`SummaryRequest`/`AuditEvent` use Postgres-only
`JSONB`/`UUID` column types that SQLite's DDL compiler can't render, and the
ownership checks under test only ever touch `Encounter` rows.

Fixture IDs below match the shared scheme used by the mock FHIR server
fixtures exactly (do not drift from these):
  encounter-1 -> patient-1, practitioner-alvarez
  encounter-2 -> patient-1, practitioner-alvarez
  encounter-3 -> patient-2, practitioner-chen
  encounter-4 -> patient-3, practitioner-chen
  encounter-5 -> patient-2, practitioner-alvarez
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from banner_copilot.auth.rbac import check_encounter_ownership, check_patient_ownership
from banner_copilot.domain.models import Encounter

ENCOUNTER_FIXTURES = [
    ("encounter-1", "patient-1", "practitioner-alvarez"),
    ("encounter-2", "patient-1", "practitioner-alvarez"),
    ("encounter-3", "patient-2", "practitioner-chen"),
    ("encounter-4", "patient-3", "practitioner-chen"),
    ("encounter-5", "patient-2", "practitioner-alvarez"),
]


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Encounter.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for encounter_id, patient_id, practitioner_id in ENCOUNTER_FIXTURES:
            session.add(
                Encounter(
                    id=encounter_id,
                    patient_id=patient_id,
                    attending_practitioner_id=practitioner_id,
                    status="finished",
                    period_start=now,
                    period_end=now,
                    synced_at=now,
                )
            )
        await session.commit()
        yield session

    await engine.dispose()


def test_check_encounter_ownership_true_when_caller_is_attending():
    encounter = Encounter(id="encounter-1", attending_practitioner_id="practitioner-alvarez")

    assert check_encounter_ownership(encounter, "practitioner-alvarez") is True


def test_check_encounter_ownership_false_when_caller_is_not_attending():
    encounter = Encounter(id="encounter-3", attending_practitioner_id="practitioner-chen")

    assert check_encounter_ownership(encounter, "practitioner-alvarez") is False


async def test_check_patient_ownership_patient_2_true_for_alvarez(db_session):
    # encounter-5: patient-2, practitioner-alvarez
    assert await check_patient_ownership(db_session, "patient-2", "practitioner-alvarez") is True


async def test_check_patient_ownership_patient_2_true_for_chen(db_session):
    # encounter-3: patient-2, practitioner-chen
    assert await check_patient_ownership(db_session, "patient-2", "practitioner-chen") is True


async def test_check_patient_ownership_patient_3_true_only_for_chen(db_session):
    assert await check_patient_ownership(db_session, "patient-3", "practitioner-chen") is True
    assert await check_patient_ownership(db_session, "patient-3", "practitioner-alvarez") is False


async def test_check_patient_ownership_false_for_unknown_patient(db_session):
    assert await check_patient_ownership(db_session, "patient-999", "practitioner-alvarez") is False


async def test_check_patient_ownership_false_for_unknown_practitioner(db_session):
    assert await check_patient_ownership(db_session, "patient-1", "practitioner-nobody") is False
