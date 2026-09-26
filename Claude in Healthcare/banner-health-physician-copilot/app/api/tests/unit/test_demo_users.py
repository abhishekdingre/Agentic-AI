"""Unit tests for `banner_copilot.auth.demo_users` (SPEC.md §14)."""

from __future__ import annotations

import pytest

from banner_copilot.auth.demo_users import DEMO_USERS, authenticate

EXPECTED_USERS = {
    "dr.alvarez": ("physician", "practitioner-alvarez"),
    "dr.chen": ("physician", "practitioner-chen"),
    "coordinator": ("care_coordinator", None),
    "admin": ("admin", None),
}


def test_seeded_users_match_spec():
    assert set(DEMO_USERS.keys()) == set(EXPECTED_USERS.keys())
    for username, (role, practitioner_id) in EXPECTED_USERS.items():
        user = DEMO_USERS[username]
        assert user["role"] == role
        assert user["practitioner_id"] == practitioner_id


@pytest.mark.parametrize("username", list(EXPECTED_USERS.keys()))
def test_authenticate_succeeds_with_correct_password(username):
    user = authenticate(username, "demo-pass")

    assert user is not None
    assert user["id"] == username


@pytest.mark.parametrize("username", list(EXPECTED_USERS.keys()))
def test_authenticate_fails_with_wrong_password(username):
    assert authenticate(username, "wrong-password") is None


def test_authenticate_fails_for_unknown_user():
    assert authenticate("nobody", "demo-pass") is None
