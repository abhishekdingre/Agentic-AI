"""Seeded in-memory demo users (SPEC.md §10).

A plain in-module store standing in for Entra ID identity — not a database
table. This matches SPEC.md §10's framing: these are seeded demo users, not
real user-management. The plaintext password for all 4 seeded users is
`demo-pass`; only the bcrypt hash is ever stored/compared.
"""

from __future__ import annotations

from passlib.hash import bcrypt

_DEMO_PASSWORD = "demo-pass"

DemoUser = dict[str, str | None]

DEMO_USERS: dict[str, DemoUser] = {
    "dr.alvarez": {
        "id": "dr.alvarez",
        "password_hash": bcrypt.hash(_DEMO_PASSWORD),
        "role": "physician",
        "practitioner_id": "practitioner-alvarez",
    },
    "dr.chen": {
        "id": "dr.chen",
        "password_hash": bcrypt.hash(_DEMO_PASSWORD),
        "role": "physician",
        "practitioner_id": "practitioner-chen",
    },
    "coordinator": {
        "id": "coordinator",
        "password_hash": bcrypt.hash(_DEMO_PASSWORD),
        "role": "care_coordinator",
        "practitioner_id": None,
    },
    "admin": {
        "id": "admin",
        "password_hash": bcrypt.hash(_DEMO_PASSWORD),
        "role": "admin",
        "practitioner_id": None,
    },
}


def authenticate(username: str, password: str) -> DemoUser | None:
    """Look up a seeded demo user by username and verify the bcrypt hash.

    Returns the user record (`id`, `password_hash`, `role`,
    `practitioner_id`) on success, or `None` for an unknown username or a
    wrong password.
    """
    user = DEMO_USERS.get(username)
    if user is None:
        return None
    if not bcrypt.verify(password, user["password_hash"]):
        return None
    return user
