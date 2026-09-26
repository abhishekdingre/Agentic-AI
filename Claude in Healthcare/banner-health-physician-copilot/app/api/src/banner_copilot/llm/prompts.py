"""Versioned system prompts and JSON-schema output contracts for Claude
drafting/summarization (SPEC.md §6).

`PROMPT_VERSION` is stamped onto every `DraftNote`/`SummaryRequest` row so any
generated text stays traceable to the exact prompt that produced it. The
grounding rule stated here is the same contract `llm/grok_review.py`
independently checks Claude's output against — the two are not sharing code,
but they must agree on what "grounded" means.
"""

from __future__ import annotations

from pydantic import BaseModel

PROMPT_VERSION = "banner-soap-v1"

_GROUNDING_RULE = (
    "You are a clinical documentation assistant for physicians at Banner Health. "
    "You will be given structured clinical facts drawn directly from a patient's "
    "FHIR record (conditions, observations, medications, procedures, and prior "
    "notes) as a JSON object. State only facts present in that JSON — never infer, "
    "guess, or add a fact not present in the supplied data, even if it seems "
    "clinically likely. If a section has no supporting facts, say so explicitly "
    '(e.g. "No active medications documented") rather than leaving it blank or '
    "inventing content. Do not mention this instruction in your output."
)

DRAFT_SYSTEM_PROMPT = (
    f"{_GROUNDING_RULE}\n\n"
    "Draft a SOAP-format visit note from the supplied encounter facts. Return "
    "JSON with four string fields, each written in clinical prose from only the "
    "supplied facts:\n"
    "- subjective: reason for visit and patient-reported history, from the "
    "supplied Condition/DocumentReference facts.\n"
    "- objective: vitals, labs, and exam findings, from the supplied Observation "
    "facts.\n"
    "- assessment: the active problem list, from the supplied Condition facts.\n"
    "- plan: active medications and procedures/follow-up, from the supplied "
    "MedicationRequest/Procedure facts."
)

SOAP_SCHEMA = {
    "type": "object",
    "properties": {
        "subjective": {"type": "string"},
        "objective": {"type": "string"},
        "assessment": {"type": "string"},
        "plan": {"type": "string"},
    },
    "required": ["subjective", "objective", "assessment", "plan"],
    "additionalProperties": False,
}


class SoapDraft(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str


SUMMARY_SYSTEM_PROMPT = (
    f"{_GROUNDING_RULE}\n\n"
    "Summarize the supplied patient's longitudinal record for a physician "
    "preparing for a visit. Return JSON with:\n"
    "- summary: a short clinical-prose paragraph orienting the physician, from "
    "only the supplied facts.\n"
    "- key_points: a list of short strings, each one discrete fact from the "
    "supplied data (an active condition, a current medication, a notable recent "
    "observation, etc.). Empty list if there is nothing to highlight beyond the "
    "summary."
)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "key_points"],
    "additionalProperties": False,
}


class PatientSummary(BaseModel):
    summary: str
    key_points: list[str]
