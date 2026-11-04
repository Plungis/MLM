from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 8


def hash_request_password(password: str) -> str:
    """Return a salted password hash suitable for storage in config.toml."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"requester password must contain at least {MIN_PASSWORD_LENGTH} characters"
        )
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_request_password(password: str, encoded: str) -> bool:
    """Verify a requester password without exposing parsing failures."""
    try:
        scheme, iterations_text, salt_text, expected_text = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if iterations < 1 or iterations > 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
    except (binascii.Error, ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
