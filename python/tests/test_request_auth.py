from __future__ import annotations

import pytest

from mlm.request_auth import hash_request_password, verify_request_password


def test_requester_passwords_are_salted_and_verified() -> None:
    first = hash_request_password("correct horse")
    second = hash_request_password("correct horse")

    assert first != second
    assert "correct horse" not in first
    assert verify_request_password("correct horse", first) is True
    assert verify_request_password("wrong password", first) is False
    assert verify_request_password("correct horse", "not-a-password-hash") is False


def test_requester_password_requires_eight_characters() -> None:
    with pytest.raises(ValueError, match="at least 8"):
        hash_request_password("short")
