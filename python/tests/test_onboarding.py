from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from mlm.audiobookshelf import (
    AudiobookshelfClient,
    AudiobookshelfError,
    AudiobookshelfUserExistsError,
)
from mlm.config import load_config
from mlm.onboarding import generate_onboarding_blurb, generate_suggested_password
from mlm.web import create_app


@pytest.mark.asyncio
async def test_audiobookshelf_create_user_success():
    created_payload = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_payload
        if request.url.path == "/api/users" and request.method == "POST":
            created_payload = request.read().decode("utf-8")
            return httpx.Response(
                201,
                json={
                    "id": "user_123",
                    "username": "brian",
                    "type": "user",
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://abs.example.com"
    ) as mock_client:
        client = AudiobookshelfClient(
            "https://abs.example.com", "token123", client=mock_client
        )
        res = await client.create_user("brian", "SecretPass123!")
        assert res["username"] == "brian"
        assert res["id"] == "user_123"
        assert "SecretPass123!" in created_payload


@pytest.mark.asyncio
async def test_audiobookshelf_create_user_already_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Username already taken")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://abs.example.com"
    ) as mock_client:
        client = AudiobookshelfClient(
            "https://abs.example.com", "token123", client=mock_client
        )
        with pytest.raises(AudiobookshelfUserExistsError) as exc_info:
            await client.create_user("brian", "SecretPass123!")
        assert "already exists" in str(exc_info.value)


@pytest.mark.asyncio
async def test_audiobookshelf_create_user_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://abs.example.com"
    ) as mock_client:
        client = AudiobookshelfClient(
            "https://abs.example.com", "bad_token", client=mock_client
        )
        with pytest.raises(AudiobookshelfError) as exc_info:
            await client.create_user("brian", "SecretPass123!")
        assert "lacks admin permissions" in str(exc_info.value)


def test_onboarding_blurb_generation():
    blurb = generate_onboarding_blurb(
        username="alice",
        password="MySecretPassword123",
        display_name="Alice Reader",
        abs_url="https://bookshelf.example.com",
        portal_url="https://requests.example.com",
        auto_approve=True,
        weekly_request_limit=15,
    )
    assert "Hey Alice Reader!" in blurb
    assert "Username: alice" in blurb
    assert "Password: MySecretPassword123" in blurb
    assert "https://bookshelf.example.com" in blurb
    assert "https://requests.example.com" in blurb
    assert "Instant Auto-Approval" in blurb
    assert "15 books per rolling 7 days" in blurb
    assert "Absorb" in blurb
    assert "https://apps.apple.com/app/absorb-for-audiobookshelf/id6477741369" in blurb
    assert "com.audiobookshelf.app" in blurb
    assert "com.barnabas.absorb" in blurb


def test_suggested_password():
    pwd = generate_suggested_password()
    assert len(pwd) >= 8
    assert any(char.isdigit() for char in pwd)
    assert any(char in "!@#$%*" for char in pwd)


def test_onboarding_routes_e2e(tmp_path: Path):
    user_row = (
        '{ username = "existing_user", password_hash = "fake_hash", '
        'display_name = "Existing User", permissions = ["auto_approve"], '
        "weekly_request_limit = 5 }"
    )
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f"""
mam_id = "test_mam_id"
request_portal_enabled = true
request_portal_domains = ["requests.example.com"]
request_portal_users = [{user_row}]

[audiobookshelf]
url = "https://abs.example.com"
token = "test_abs_token"
""",
        encoding="utf-8",
    )
    db_file = tmp_path / "data.sqlite3"

    app = create_app(config_file, db_file)
    client = TestClient(app)

    # 1. GET /onboarding should render page with form and existing users
    response = client.get("/onboarding")
    assert response.status_code == 200
    assert "User Onboarding Center" in response.text
    assert "existing_user" in response.text
    assert "requests.example.com" in response.text

    # 2. POST /onboarding to create a new reader
    post_res = client.post(
        "/onboarding",
        data={
            "username": "new_reader",
            "password": "Password123!",
            "display_name": "New Reader",
            "weekly_request_limit": "20",
            "auto_approve": "true",
            "create_portal": "true",
            # omit create_abs to skip external call in this test
        },
        follow_redirects=True,
    )
    assert post_res.status_code == 200
    assert "Reader Access Pass" in post_res.text
    assert "new_reader" in post_res.text
    assert "Password123!" in post_res.text
    assert "Request Portal account created" in post_res.text
    assert "Absorb" in post_res.text
    assert "id6477741369" in post_res.text
    assert "com.audiobookshelf.app" in post_res.text

    # Verify config file updated with new user
    updated_cfg = load_config(config_file)
    usernames = [u.username for u in updated_cfg.request_portal_users]
    assert "new_reader" in usernames
    new_user = next(
        u for u in updated_cfg.request_portal_users if u.username == "new_reader"
    )
    assert new_user.display_name == "New Reader"
    assert new_user.weekly_request_limit == 20
    assert "auto_approve" in new_user.permissions

    # 3. View existing user's pass
    view_res = client.get("/onboarding?user=existing_user")
    assert view_res.status_code == 200
    assert "Existing User" in view_res.text
    assert "existing_user" in view_res.text


def test_onboarding_with_abs_account_creation(tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
mam_id = "test_mam_id"
request_portal_enabled = true
request_portal_domains = ["requests.example.com"]

[audiobookshelf]
url = "https://abs.example.com"
token = "test_abs_token"
""",
        encoding="utf-8",
    )
    db_file = tmp_path / "data.sqlite3"

    app = create_app(config_file, db_file)
    client = TestClient(app)

    with patch.object(
        AudiobookshelfClient,
        "create_user",
        new=AsyncMock(return_value={"id": "abs_123", "username": "sarah"}),
    ) as mock_abs_create:
        post_res = client.post(
            "/onboarding",
            data={
                "username": "sarah",
                "password": "SarahPassword999!",
                "display_name": "Sarah P",
                "weekly_request_limit": "15",
                "auto_approve": "true",
                "create_portal": "true",
                "create_abs": "true",
            },
            follow_redirects=True,
        )
        assert post_res.status_code == 200
        assert mock_abs_create.called
        assert mock_abs_create.call_args[0] == ("sarah", "SarahPassword999!")
        assert "Audiobookshelf account created" in post_res.text
        assert "Request Portal account created" in post_res.text
        assert "Sarah P" in post_res.text
        assert "SarahPassword999!" in post_res.text
