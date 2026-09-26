"""Unit tests for `llm.grok_review`: grounded/flagged/unavailable outcomes
(SPEC.md §6, FR-6). Mocks the OpenAI-compatible SDK client method directly
(never a real network call, never billed) rather than the HTTP transport,
matching the approach in `test_claude_drafting.py`.
"""

from __future__ import annotations

import json

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from banner_copilot.config import Settings
from banner_copilot.llm import grok_review

FACTS = {"Condition": [{"code": "diabetes"}]}
DRAFT = {"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # openai.AsyncOpenAI raises at construction time on an empty api_key — the
    # default empty-string setting would fail before our fake_create ever runs.
    monkeypatch.setattr(grok_review, "get_settings", lambda: Settings(xai_api_key="test-key"))


def _completion(content: str | None) -> ChatCompletion:
    return ChatCompletion.model_construct(
        id="cc_1",
        object="chat.completion",
        created=0,
        model="grok-4.7",
        choices=[
            Choice.model_construct(
                index=0,
                finish_reason="stop",
                message=ChatCompletionMessage(role="assistant", content=content),
            )
        ],
    )


@pytest.mark.asyncio
async def test_review_draft_grounded(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"grounded": True, "findings": [], "overall_assessment": "looks fine"}

    async def fake_create(self, **kwargs):
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["reasoning_effort"]
        return _completion(json.dumps(payload))

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    review = await grok_review.review_draft(facts=FACTS, draft=DRAFT)
    assert review.grounded is True
    assert grok_review.has_critical_finding(review) is False


@pytest.mark.asyncio
async def test_review_draft_with_critical_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "grounded": False,
        "findings": [{"severity": "critical", "claim": "patient has pneumonia", "issue": "not in supplied facts"}],
        "overall_assessment": "ungrounded claim found",
    }

    async def fake_create(self, **kwargs):
        return _completion(json.dumps(payload))

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    review = await grok_review.review_draft(facts=FACTS, draft=DRAFT)
    assert grok_review.has_critical_finding(review) is True


@pytest.mark.asyncio
async def test_review_draft_warning_only_not_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "grounded": True,
        "findings": [{"severity": "warning", "claim": "n/a", "issue": "omitted a minor fact"}],
        "overall_assessment": "mostly fine",
    }

    async def fake_create(self, **kwargs):
        return _completion(json.dumps(payload))

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    review = await grok_review.review_draft(facts=FACTS, draft=DRAFT)
    assert grok_review.has_critical_finding(review) is False


@pytest.mark.asyncio
async def test_api_error_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(self, **kwargs):
        raise openai.APIConnectionError(request=httpx2.Request("POST", "https://api.x.ai/v1/chat/completions"))

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    with pytest.raises(grok_review.GrokUnavailableError):
        await grok_review.review_draft(facts=FACTS, draft=DRAFT)


@pytest.mark.asyncio
async def test_empty_content_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(self, **kwargs):
        return _completion(None)

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    with pytest.raises(grok_review.GrokUnavailableError):
        await grok_review.review_draft(facts=FACTS, draft=DRAFT)


@pytest.mark.asyncio
async def test_invalid_json_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(self, **kwargs):
        return _completion("not json")

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    with pytest.raises(grok_review.GrokUnavailableError):
        await grok_review.review_draft(facts=FACTS, draft=DRAFT)


@pytest.mark.asyncio
async def test_schema_validation_failure_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # "severity" outside the enum -> Pydantic validation failure.
    payload = {
        "grounded": True,
        "findings": [{"severity": "info", "claim": "x", "issue": "y"}],
        "overall_assessment": "ok",
    }

    async def fake_create(self, **kwargs):
        return _completion(json.dumps(payload))

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    with pytest.raises(grok_review.GrokUnavailableError):
        await grok_review.review_draft(facts=FACTS, draft=DRAFT)


@pytest.mark.asyncio
async def test_review_summary_uses_summary_label(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"grounded": True, "findings": [], "overall_assessment": "fine"}
    captured: dict = {}

    async def fake_create(self, **kwargs):
        captured["messages"] = kwargs["messages"]
        return _completion(json.dumps(payload))

    monkeypatch.setattr(openai.resources.chat.completions.completions.AsyncCompletions, "create", fake_create)

    await grok_review.review_summary(facts=FACTS, summary={"summary": "s", "key_points": []})
    user_message = json.loads(captured["messages"][1]["content"])
    assert "generated_summary" in user_message
