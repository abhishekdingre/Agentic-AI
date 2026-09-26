"""Domain-level exceptions raised by `domain/service.py`.

Kept separate from both the FHIR client's exceptions and the LLM modules'
exceptions so `routes/*.py` (not yet built) has one place to map failures to
HTTP status codes, without importing `fhir`/`llm` internals directly.
"""

from __future__ import annotations


class NotFoundError(Exception):
    """The requested Encounter/DraftNote/SummaryRequest does not exist. Maps to 404."""


class PermissionDeniedError(Exception):
    """RBAC/ownership check failed (SPEC.md §5). Maps to 403."""


class FhirUnavailableError(Exception):
    """FHIR retries/breaker exhausted (SPEC.md §7). Maps to `503
    {status:"failed", reason:"fhir_unavailable"}`. No DraftNote/SummaryRequest
    row is created for this failure — nothing to persist."""


class LlmUnavailableError(Exception):
    """Claude/Grok call failed, timed out, or failed schema validation (SPEC.md
    §6/§7, FR-5). Maps to `502 {status:"failed", reason:"llm_unavailable"}`.
    For the draft-note flow a `DraftNote` row with `status="failed"` is
    persisted before this is raised (SPEC.md §2's `failure_reason` field
    exists for exactly this); for the summary flow (no such field on
    `SummaryRequest`) nothing is persisted."""


class ImmutableNoteError(Exception):
    """Attempted PATCH on an already-`signed` note. Maps to 409."""


class SignAcknowledgeRequiredError(Exception):
    """Attempted sign on a `drafted_flagged`/`drafted_unverified` note without
    `{"acknowledgeFlags": true}` (SPEC.md §3). Maps to 409."""
