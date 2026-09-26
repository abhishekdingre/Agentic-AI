"""Local demo JWT issuance/verification (SPEC.md §3, §10).

HS256, shared-secret JWTs signed with `Settings.jwt_secret` — a local
stand-in for Entra ID/OIDC. Not a production auth scheme.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from banner_copilot.config import get_settings


def issue_token(user_id: str, role: str, practitioner_id: str | None) -> str:
    """Issue a signed JWT for a demo user.

    Claim shape (exact, per SPEC.md §3):
        {"sub": user_id, "role": role, "practitioner_id": practitioner_id,
         "iat": <ts>, "exp": <ts>}
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "practitioner_id": practitioner_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT.

    Raises `jwt.PyJWTError` (or a subclass, e.g. `jwt.ExpiredSignatureError`,
    `jwt.InvalidSignatureError`) on a tampered, malformed, or expired token.
    """
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
