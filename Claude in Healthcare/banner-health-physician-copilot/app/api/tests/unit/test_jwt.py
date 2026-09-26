"""Unit tests for `banner_copilot.auth.jwt` (SPEC.md §14)."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from banner_copilot.auth.jwt import decode_token, issue_token
from banner_copilot.config import get_settings


def test_issue_then_decode_round_trip_preserves_claims():
    token = issue_token("dr.alvarez", "physician", "practitioner-alvarez")

    claims = decode_token(token)

    assert claims["sub"] == "dr.alvarez"
    assert claims["role"] == "physician"
    assert claims["practitioner_id"] == "practitioner-alvarez"
    assert "iat" in claims
    assert "exp" in claims
    assert claims["exp"] > claims["iat"]


def test_issue_then_decode_round_trip_preserves_null_practitioner_id():
    token = issue_token("coordinator", "care_coordinator", None)

    claims = decode_token(token)

    assert claims["sub"] == "coordinator"
    assert claims["role"] == "care_coordinator"
    assert claims["practitioner_id"] is None


def test_decode_rejects_tampered_token():
    token = issue_token("dr.chen", "physician", "practitioner-chen")
    # Flip the last character of the signature segment.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(pyjwt.PyJWTError):
        decode_token(tampered)


def test_decode_rejects_token_signed_with_a_different_secret():
    bogus_token = pyjwt.encode(
        {"sub": "dr.chen", "role": "physician", "practitioner_id": "practitioner-chen"},
        "not-the-real-secret",
        algorithm=get_settings().jwt_algorithm,
    )

    with pytest.raises(pyjwt.PyJWTError):
        decode_token(bogus_token)


def test_decode_rejects_expired_token():
    settings = get_settings()
    now = int(time.time())
    expired_payload = {
        "sub": "coordinator",
        "role": "care_coordinator",
        "practitioner_id": None,
        "iat": now - 1000,
        "exp": now - 10,
    }
    expired_token = pyjwt.encode(
        expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )

    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_token(expired_token)
