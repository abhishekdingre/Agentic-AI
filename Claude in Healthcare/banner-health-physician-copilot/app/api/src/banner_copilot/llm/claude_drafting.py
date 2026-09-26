"""Anthropic-only: Claude drafting (SOAP note) and summarization (SPEC.md §6).

Provider isolation: this file calls the Anthropic SDK exclusively. The
independent Grok grounding review (OpenAI-compatible client) lives in
`llm/grok_review.py` and is never mixed into this module.
"""

from __future__ import annotations

import json

import anthropic
from pydantic import ValidationError

from banner_copilot.config import get_settings
from banner_copilot.llm.prompts import (
    DRAFT_SYSTEM_PROMPT,
    SOAP_SCHEMA,
    SUMMARY_SCHEMA,
    SUMMARY_SYSTEM_PROMPT,
    PatientSummary,
    SoapDraft,
)


class ClaudeUnavailableError(Exception):
    """Claude call timed out, errored, refused, or returned invalid/non-conforming
    JSON. Maps to `status = "failed"`, `failure_reason = "llm_unavailable"` (FR-5)."""


async def _create_structured(*, system_prompt: str, schema: dict, facts: dict) -> dict:
    settings = get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            thinking={"type": "adaptive"},
            output_config={
                "format": {"type": "json_schema", "schema": schema},
                "effort": settings.anthropic_effort,
            },
            messages=[{"role": "user", "content": json.dumps(facts, sort_keys=True)}],
        )
    except anthropic.APIError as exc:
        raise ClaudeUnavailableError(str(exc)) from exc

    if response.stop_reason == "refusal":
        raise ClaudeUnavailableError(f"Claude refused: {response.stop_details}")

    text_block = next((block for block in response.content if block.type == "text"), None)
    if text_block is None:
        raise ClaudeUnavailableError("Claude response contained no text block")

    try:
        return json.loads(text_block.text)
    except json.JSONDecodeError as exc:
        raise ClaudeUnavailableError(f"Claude returned invalid JSON: {exc}") from exc


async def draft_soap_note(facts: dict) -> tuple[SoapDraft, str]:
    """Draft a SOAP note from structured FHIR facts.

    Returns `(draft, claude_model)`. Raises `ClaudeUnavailableError` on any
    failure — never returns a partial/truncated draft (FR-5).
    """
    settings = get_settings()
    data = await _create_structured(system_prompt=DRAFT_SYSTEM_PROMPT, schema=SOAP_SCHEMA, facts=facts)
    try:
        return SoapDraft.model_validate(data), settings.claude_model
    except ValidationError as exc:
        raise ClaudeUnavailableError(f"Claude output failed schema validation: {exc}") from exc


async def summarize_patient(facts: dict) -> tuple[PatientSummary, str]:
    """Summarize a patient's longitudinal record from structured FHIR facts.

    Returns `(summary, claude_model)`. Raises `ClaudeUnavailableError` on any
    failure — never returns a partial/truncated summary (FR-5).
    """
    settings = get_settings()
    data = await _create_structured(system_prompt=SUMMARY_SYSTEM_PROMPT, schema=SUMMARY_SCHEMA, facts=facts)
    try:
        return PatientSummary.model_validate(data), settings.claude_model
    except ValidationError as exc:
        raise ClaudeUnavailableError(f"Claude output failed schema validation: {exc}") from exc
