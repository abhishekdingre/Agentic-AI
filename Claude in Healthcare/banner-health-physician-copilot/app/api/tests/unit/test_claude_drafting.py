"""Unit tests for `llm.claude_drafting`: structured SOAP-draft/summary parsing,
refusal handling, invalid-JSON handling, and schema-validation failure — all
mapped to `ClaudeUnavailableError` (SPEC.md §6, FR-5). Mocks the Anthropic SDK
client method directly (never a real network call, never billed) rather than
the HTTP transport, since `anthropic` 1.x runs on `httpx2`, not `httpx`
(respx only patches `httpx`).
"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest
from anthropic.types import Message, TextBlock, Usage

from banner_copilot.config import Settings
from banner_copilot.llm import claude_drafting

FACTS = {"Condition": [{"code": "diabetes"}]}


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # anthropic.AsyncAnthropic requires a non-empty api_key at construction time —
    # the default empty-string setting would fail before our fake_create ever runs.
    monkeypatch.setattr(claude_drafting, "get_settings", lambda: Settings(anthropic_api_key="test-key"))


def _message(*, text: str | None = None, stop_reason: str = "end_turn", stop_details=None) -> Message:
    content = [TextBlock(type="text", text=text)] if text is not None else []
    return Message.model_construct(
        id="msg_1",
        type="message",
        role="assistant",
        model="claude-opus-5",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        stop_details=stop_details,
        usage=Usage(input_tokens=10, output_tokens=10),
    )


@pytest.mark.asyncio
async def test_draft_soap_note_success(monkeypatch: pytest.MonkeyPatch) -> None:
    soap = {"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}
    response = _message(text=json.dumps(soap))

    async def fake_create(self, **kwargs):
        assert kwargs["output_config"]["format"]["type"] == "json_schema"
        assert kwargs["thinking"] == {"type": "adaptive"}
        return response

    monkeypatch.setattr(anthropic.resources.AsyncMessages, "create", fake_create)

    draft, model = await claude_drafting.draft_soap_note(FACTS)
    assert draft.subjective == "s"
    assert draft.plan == "p"
    assert model


@pytest.mark.asyncio
async def test_summarize_patient_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"summary": "doing fine", "key_points": ["point 1"]}
    response = _message(text=json.dumps(payload))

    async def fake_create(self, **kwargs):
        return response

    monkeypatch.setattr(anthropic.resources.AsyncMessages, "create", fake_create)

    summary, model = await claude_drafting.summarize_patient(FACTS)
    assert summary.summary == "doing fine"
    assert summary.key_points == ["point 1"]
    assert model


@pytest.mark.asyncio
async def test_refusal_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _message(text=None, stop_reason="refusal", stop_details={"type": "refusal"})

    async def fake_create(self, **kwargs):
        return response

    monkeypatch.setattr(anthropic.resources.AsyncMessages, "create", fake_create)

    with pytest.raises(claude_drafting.ClaudeUnavailableError):
        await claude_drafting.draft_soap_note(FACTS)


@pytest.mark.asyncio
async def test_invalid_json_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _message(text="not json")

    async def fake_create(self, **kwargs):
        return response

    monkeypatch.setattr(anthropic.resources.AsyncMessages, "create", fake_create)

    with pytest.raises(claude_drafting.ClaudeUnavailableError):
        await claude_drafting.draft_soap_note(FACTS)


@pytest.mark.asyncio
async def test_schema_validation_failure_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Missing required "plan" field.
    incomplete = {"subjective": "s", "objective": "o", "assessment": "a"}
    response = _message(text=json.dumps(incomplete))

    async def fake_create(self, **kwargs):
        return response

    monkeypatch.setattr(anthropic.resources.AsyncMessages, "create", fake_create)

    with pytest.raises(claude_drafting.ClaudeUnavailableError):
        await claude_drafting.draft_soap_note(FACTS)


@pytest.mark.asyncio
async def test_api_error_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(self, **kwargs):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr(anthropic.resources.AsyncMessages, "create", fake_create)

    with pytest.raises(claude_drafting.ClaudeUnavailableError):
        await claude_drafting.draft_soap_note(FACTS)
