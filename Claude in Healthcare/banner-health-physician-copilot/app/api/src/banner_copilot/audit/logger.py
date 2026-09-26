"""Audit-trail writer (SPEC.md §2, §10, FR-4) — one `AuditEvent` row per FHIR
read / Claude call / Grok call / note action.

`log_event` only `add()`s the row — it deliberately does not `commit()` so it
joins whatever transaction the caller (`domain/service.py`) is already
running: an audit event and the state change it describes must land in the
same commit, never a separate one that could succeed/fail independently.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from banner_copilot.domain.models import AuditEvent, AuditOutcome


async def log_event(
    db: AsyncSession,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: AuditOutcome,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=detail,
    )
    db.add(event)
    return event
