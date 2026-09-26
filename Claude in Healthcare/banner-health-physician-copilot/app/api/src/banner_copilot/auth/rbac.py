"""RBAC dependencies and ownership predicates (SPEC.md §5).

Scoped deliberately to what's buildable without the FHIR client:
  - `get_current_user` / `require_role` — JWT auth + role gating, usable as
    FastAPI dependencies on any route today.
  - `check_encounter_ownership` / `check_patient_ownership` — the pure/DB
    building-block predicates that a later FHIR-refresh-then-check flow
    (SPEC.md §5 + §7: re-resolve Encounter from FHIR or a fresh mirror hit,
    *then* check ownership — never trust a cached claim alone) will call.
    That full flow depends on `fhir/client.py`, which is a separate,
    not-yet-built piece; it is intentionally not implemented here.
"""

from __future__ import annotations

from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banner_copilot.auth.jwt import decode_token
from banner_copilot.domain.models import Encounter

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency: decode the `Authorization: Bearer <token>` header.

    Raises `401` on a missing, malformed, tampered, or expired token.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_token(credentials.credentials)
    except pyjwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_role(*roles: str):
    """Dependency factory: raises `403` unless `claims["role"]` is in `roles`."""

    async def _require_role(
        claims: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if claims.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{claims.get('role')}' is not permitted to perform this action",
            )
        return claims

    return _require_role


def check_encounter_ownership(encounter: Any, caller_practitioner_id: str) -> bool:
    """True iff `caller_practitioner_id` is the encounter's attending practitioner.

    Pure function. `encounter` may be an `Encounter` model instance or any
    other object exposing an `attending_practitioner_id` attribute.
    """
    return encounter.attending_practitioner_id == caller_practitioner_id


async def check_patient_ownership(
    db: AsyncSession, patient_id: str, caller_practitioner_id: str
) -> bool:
    """True iff the caller attends >=1 mirrored encounter for this patient.

    Implements SPEC.md §5's "own patients" rule, applied transitively via
    the local `encounters` mirror table (the derived `PractitionerAssignment`
    relation described in SPEC.md §2).
    """
    stmt = (
        select(Encounter.id)
        .where(
            Encounter.patient_id == patient_id,
            Encounter.attending_practitioner_id == caller_practitioner_id,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.first() is not None
