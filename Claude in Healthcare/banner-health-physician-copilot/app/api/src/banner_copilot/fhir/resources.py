"""Typed accessors over raw FHIR R4 JSON (SPEC.md §2, §4).

Deliberately thin: these functions extract exactly the fields this app's
domain layer needs (RBAC ownership, the Encounter mirror, prompt facts for
the LLM layer) rather than modeling FHIR resources in full.
"""

from __future__ import annotations

ATND_CODING_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"
ATND_CODE = "ATND"


def bundle_entries(bundle: dict) -> list[dict]:
    """Extract `resource` dicts from a FHIR search-set Bundle. Empty list if none."""
    return [entry["resource"] for entry in bundle.get("entry", []) if "resource" in entry]


def get_attending_practitioner_id(encounter: dict) -> str | None:
    """Resolve the attending practitioner id from `Encounter.participant`.

    Looks for a `participant[].type[].coding[]` entry with
    system=`ATND_CODING_SYSTEM`, code=`ATND_CODE`, and reads that
    participant's `individual.reference` (format `"Practitioner/{id}"`).
    Returns None if no such participant is present.
    """
    for participant in encounter.get("participant", []):
        for type_ in participant.get("type", []):
            for coding in type_.get("coding", []):
                if coding.get("system") == ATND_CODING_SYSTEM and coding.get("code") == ATND_CODE:
                    reference = participant.get("individual", {}).get("reference", "")
                    if reference.startswith("Practitioner/"):
                        return reference.removeprefix("Practitioner/")
    return None


def get_period(encounter: dict) -> tuple[str | None, str | None]:
    """Return `(period_start, period_end)` ISO strings from `Encounter.period`, or (None, None)."""
    period = encounter.get("period", {})
    return period.get("start"), period.get("end")


def get_patient_id(encounter: dict) -> str | None:
    """Resolve the patient id from `Encounter.subject.reference` (format `"Patient/{id}"`)."""
    reference = encounter.get("subject", {}).get("reference", "")
    if reference.startswith("Patient/"):
        return reference.removeprefix("Patient/")
    return None
