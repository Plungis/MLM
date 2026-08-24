from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mlm.config import load_config
from mlm.database import ensure_database
from mlm.repository import Repository
from mlm.request_auth import hash_request_password
from mlm.request_portal import (
    GoodreadsLookupError,
    lookup_goodreads_book,
    parse_goodreads_book,
    validate_goodreads_url,
)
from mlm.web import create_app


class PortalMam:
    def __init__(self) -> None:
        self.queries: list[dict] = []
        self.row = {
            "id": 321,
            "title": "Dungeon Crawler Carl",
            "author_info": '{"1":"Matt Dinniman"}',
            "narrator_info": '{"2":"Jeff Hays"}',
            "series_info": '{"3":["Dungeon Crawler Carl","1"]}',
            "filetype": "m4b",
            "size": "734.5 MiB",
            "catname": "Audiobook",
            "category": 1,
            "mediatype": 1,
            "main_cat": 13,
            "language": 1,
            "numfiles": 1,
            "seeders": 12,
            "leechers": 2,
            "times_completed": 99,
            "owner_name": "BookSeeder",
            "added": "2026-08-05 12:00:00",
            "personal_freeleech": 1,
            "dl": "private-download-token",
        }

    async def search(self, query: dict) -> dict:
        self.queries.append(query)
        if query.get("tor", {}).get("startNumber", 0):
            return {"found": 1, "data": []}
        return {"found": 1, "data": [self.row]}

    async def get_torrent_info_by_id(self, mam_id: int) -> dict | None:
        return self.row if mam_id == self.row["id"] else None


class PortalServices:
    def __init__(self, config) -> None:
        self.config = config
        self.jobs = {}
        self.mam_stats = {}
        self.mam = PortalMam()
        self.triggered: list[str] = []

    async def trigger(self, name: str) -> None:
        self.triggered.append(name)

    async def reconfigure(self, config) -> None:
        self.config = config


def portal_config(
    path: Path,
    *,
    access_code: str = "",
    username: str = "",
    password: str = "",
    users: tuple[dict[str, object], ...] = (),
    require_account_login: bool = False,
    domain: str = "requests.example.test",
) -> None:
    password_hash = hash_request_password(password) if password else ""
    user_rows = []
    for user in users:
        permissions = user.get("permissions", ())
        permission_values = ", ".join(json.dumps(item) for item in permissions)
        user_password_hash = hash_request_password(str(user["password"]))
        user_rows.append(
            "{ "
            f"username = {json.dumps(user['username'])}, "
            f"password_hash = {json.dumps(user_password_hash)}, "
            f"display_name = {json.dumps(user.get('display_name', ''))}, "
            f"permissions = [{permission_values}], "
            f"weekly_request_limit = {int(user.get('weekly_request_limit', 0))}"
            " }"
        )
    users_toml = "[" + ", ".join(user_rows) + "]"
    path.write_text(
        f"""
mam_id = "session"
request_portal_enabled = true
request_portal_require_account_login = {str(require_account_login).lower()}
request_portal_domains = [{json.dumps(domain)}]
request_portal_title = "Randy's Library Requests"
request_portal_access_code = {json.dumps(access_code)}
request_portal_username = {json.dumps(username)}
request_portal_password_hash = {json.dumps(password_hash)}
request_portal_rate_limit = 20
request_portal_users = {users_toml}
audio_types = ["m4b", "mp3"]
""",
        encoding="utf-8",
    )


def test_goodreads_next_data_parser_reads_book_author_and_series() -> None:
    state = {
        "Book:page": {
            "legacyId": 54659324,
            "title": "Dungeon Crawler Carl",
            "titleComplete": "Dungeon Crawler Carl (Dungeon Crawler Carl, #1)",
            "primaryContributorEdge": {"node": {"__ref": "Contributor:author"}},
            "bookSeries": [{"userPosition": "1", "series": {"__ref": "Series:dcc"}}],
            "details": {
                "isbn13": "9780593820247",
                "format": "Hardcover",
                "language": {"name": "English"},
            },
            "imageUrl": "https://images.example.test/dcc.jpg",
        },
        "Contributor:author": {"name": "Matt Dinniman"},
        "Series:dcc": {"title": "Dungeon Crawler Carl"},
    }
    payload = {"props": {"pageProps": {"apolloState": state}}}
    document = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script>"
    )

    book = parse_goodreads_book(
        document,
        "https://www.goodreads.com/book/show/54659324-dungeon-crawler-carl",
    )

    assert book["goodreads_id"] == 54659324
    assert book["title"] == "Dungeon Crawler Carl"
    assert book["authors"] == ["Matt Dinniman"]
    assert book["series"] == "Dungeon Crawler Carl"
    assert book["series_position"] == "1"
    assert book["language"] == "English"
    assert book["isbn"] == "9780593820247"


def test_goodreads_reader_rejects_non_goodreads_urls_and_redirects() -> None:
    with pytest.raises(GoodreadsLookupError, match="full https"):
        validate_goodreads_url("https://example.com/book/show/123")

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    302,
                    headers={"location": "https://example.com/private"},
                )
            )
        ) as client:
            with pytest.raises(GoodreadsLookupError, match="full https"):
                await lookup_goodreads_book(
                    "https://www.goodreads.com/book/show/123-example",
                    client=client,
                )

    asyncio.run(exercise())


def test_custom_domain_is_request_only_and_approval_queues_release(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    portal_config(config_path)
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    app = create_app(config_path, database)
    services = PortalServices(load_config(config_path))
    app.state.services = services
    client = TestClient(app)
    portal_headers = {"host": "requests.example.test"}

    portal = client.get(
        "/",
        params={
            "author": "Matt Dinniman",
            "series": "Dungeon Crawler Carl",
            "filetype": "m4b",
        },
        headers=portal_headers,
    )

    assert portal.status_code == 200
    assert "Randy&#39;s Library Requests" in portal.text
    assert "Goodreads link reader" in portal.text
    assert "Dungeon Crawler Carl" in portal.text
    assert "Send request for approval" in portal.text
    stylesheet = client.get("/static/app.css", headers=portal_headers)
    assert stylesheet.status_code == 200
    assert ".request-portal-body" in stylesheet.text
    assert client.get("/config", headers=portal_headers).status_code == 404
    assert client.get("/requests", headers=portal_headers).status_code == 404
    assert client.get("/").text.find("Your library, at a glance") != -1

    submitted = client.post(
        "/request/submit",
        data={
            "mam_id": "321",
            "requester_name": "Reader",
            "requester_contact": "reader@example.test",
            "note": "M4B please",
            "goodreads_url": "",
            "website": "",
        },
        headers=portal_headers,
        follow_redirects=False,
    )

    assert submitted.status_code == 303
    assert submitted.headers["location"] == "/?submitted=pending"
    requests = repository.request_rows(status="pending")
    assert len(requests) == 1
    assert requests[0]["release"]["filetypes"] == ["m4b"]
    assert repository.pending_selected() == []

    inbox = client.get("/requests")
    assert inbox.status_code == 200
    assert "Reader" in inbox.text
    assert "M4B please" in inbox.text
    assert "Open request portal" in inbox.text
    approved = client.post(
        "/requests/approve",
        data={"request_id": requests[0]["id"]},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    assert repository.request_record(requests[0]["id"])["status"] == "approved"
    assert repository.pending_selected()[0]["mam_id"] == 321


def test_goodreads_link_prefills_filters_and_searches_mam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_lookup(_: str) -> dict:
        return {
            "goodreads_id": 54659324,
            "title": "Dungeon Crawler Carl",
            "authors": ["Matt Dinniman"],
            "series": "Dungeon Crawler Carl",
            "series_position": "1",
            "language": "English",
            "isbn": "",
            "cover_url": "",
            "format": "Audiobook",
            "url": "https://www.goodreads.com/book/show/54659324",
        }

    monkeypatch.setattr("mlm.web.lookup_goodreads_book", fake_lookup)
    config_path = tmp_path / "config.toml"
    portal_config(config_path)
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    app = create_app(config_path, database)
    services = PortalServices(load_config(config_path))
    app.state.services = services
    client = TestClient(app)

    response = client.get(
        "/",
        params={
            "goodreads_url": "https://www.goodreads.com/book/show/54659324",
            "filetype": "m4b",
        },
        headers={"host": "requests.example.test"},
    )

    assert response.status_code == 200
    assert "Goodreads metadata loaded" in response.text
    assert 'name="title" value="Dungeon Crawler Carl"' in response.text
    assert 'name="author" value="Matt Dinniman"' in response.text
    assert 'name="series" value="Dungeon Crawler Carl"' in response.text
    assert services.mam.queries[0]["tor"]["text"] == "Matt Dinniman"
    assert services.mam.queries[0]["tor"]["srchIn"] == ["author"]


def test_remote_request_portal_uses_shared_access_cookie(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    portal_config(config_path, access_code="family-only")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    app = create_app(config_path, database)
    app.state.services = PortalServices(load_config(config_path))

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=app,
            client=("203.0.113.20", 41234),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://requests.example.test",
        ) as client:
            locked = await client.get("/")
            assert locked.status_code == 200
            assert "private request portal" in locked.text
            wrong = await client.post("/request/unlock", data={"access_code": "wrong"})
            assert wrong.status_code == 403
            unlocked = await client.post(
                "/request/unlock",
                data={"access_code": "family-only"},
                follow_redirects=False,
            )
            assert unlocked.status_code == 303
            assert "mysuite_request_access=" in unlocked.headers["set-cookie"]
            portal = await client.get("/", params={"q": "Dungeon"})
            assert "Goodreads link reader" in portal.text

    asyncio.run(exercise())


def test_remote_request_portal_uses_username_and_password(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    portal_config(
        config_path,
        username="family",
        password="correct horse",
    )
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    app = create_app(config_path, database)
    app.state.services = PortalServices(load_config(config_path))

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=app,
            client=("203.0.113.20", 41234),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://requests.example.test",
        ) as client:
            locked = await client.get("/")
            assert locked.status_code == 200
            assert 'name="username"' in locked.text
            assert 'name="password"' in locked.text
            assert 'name="access_code"' not in locked.text

            wrong = await client.post(
                "/request/unlock",
                data={"username": "family", "password": "wrong password"},
            )
            assert wrong.status_code == 403
            assert "not valid" in wrong.text

            unlocked = await client.post(
                "/request/unlock",
                data={"username": "family", "password": "correct horse"},
                follow_redirects=False,
            )
            assert unlocked.status_code == 303
            assert "mysuite_request_access=" in unlocked.headers["set-cookie"]

            portal = await client.get("/", params={"q": "Dungeon"})
            assert "Goodreads link reader" in portal.text
            assert 'name="requester_name"' in portal.text
            assert 'value="family"' in portal.text
            assert "Sign out" in portal.text

            logged_out = await client.post("/request/logout", follow_redirects=False)
            assert logged_out.status_code == 303
            locked_again = await client.get("/")
            assert 'name="password"' in locked_again.text

    asyncio.run(exercise())


def test_named_request_accounts_apply_auto_approval_permission(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    portal_config(
        config_path,
        require_account_login=True,
        users=(
            {
                "username": "admin",
                "password": "admin password",
                "display_name": "Library Admin",
                "permissions": ("auto_approve",),
                "weekly_request_limit": 10,
            },
            {
                "username": "reader",
                "password": "reader password",
                "display_name": "Regular Reader",
                "permissions": (),
                "weekly_request_limit": 1,
            },
        ),
    )
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    app = create_app(config_path, database)
    services = PortalServices(load_config(config_path))
    app.state.services = services
    local_preview = TestClient(app).get("/request")
    assert 'name="username"' in local_preview.text
    assert "Goodreads link reader" not in local_preview.text

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=app,
            client=("203.0.113.20", 41234),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://requests.example.test",
        ) as client:
            locked = await client.get("/")
            assert 'name="username"' in locked.text
            assert "Goodreads link reader" not in locked.text
            admin_login = await client.post(
                "/request/unlock",
                data={"username": "admin", "password": "admin password"},
                follow_redirects=False,
            )
            assert admin_login.status_code == 303
            admin_portal = await client.get("/", params={"q": "Dungeon"})
            assert "Library Admin · auto-approval enabled" in admin_portal.text
            assert "Request and schedule automatically" in admin_portal.text
            assert "10</strong> requests left" in admin_portal.text
            assert "0 of 10 used" in admin_portal.text

            auto_submitted = await client.post(
                "/request/submit",
                data={
                    "mam_id": "321",
                    "requester_name": "Library Admin",
                    "requester_contact": "",
                    "note": "Trusted request",
                    "goodreads_url": "",
                    "website": "",
                },
                follow_redirects=False,
            )
            assert auto_submitted.status_code == 303
            assert auto_submitted.headers["location"] == "/?submitted=approved"
            await asyncio.sleep(0)

            auto_record = repository.request_rows()[0]
            assert auto_record["status"] == "approved"
            assert auto_record["requester_username"] == "admin"
            assert auto_record["requester_permissions"] == ["auto_approve"]
            assert auto_record["requester_weekly_limit"] == 10
            assert auto_record["decision_by"] == "admin"
            assert "Automatically approved" in auto_record["decision_note"]
            assert repository.pending_selected()[0]["mam_id"] == 321
            assert services.triggered == ["downloader"]

            await client.post("/request/logout")
            reader_login = await client.post(
                "/request/unlock",
                data={"username": "reader", "password": "reader password"},
                follow_redirects=False,
            )
            assert reader_login.status_code == 303
            services.mam.row = {
                **services.mam.row,
                "id": 322,
                "title": "The Eye of the Bedlam Bride",
            }
            reader_portal = await client.get("/", params={"q": "Bedlam"})
            assert "Regular Reader · auto-approval enabled" not in reader_portal.text
            assert "Send request for approval" in reader_portal.text
            assert "1</strong> request left" in reader_portal.text

            pending_submitted = await client.post(
                "/request/submit",
                data={
                    "mam_id": "322",
                    "requester_name": "Regular Reader",
                    "requester_contact": "",
                    "note": "Please approve",
                    "goodreads_url": "",
                    "website": "",
                },
                follow_redirects=False,
            )
            assert pending_submitted.headers["location"] == "/?submitted=pending"

            pending_record = repository.request_rows(status="pending")[0]
            assert pending_record["requester_username"] == "reader"
            assert pending_record["requester_permissions"] == []
            assert pending_record["requester_weekly_limit"] == 1
            assert repository.has_pending_mam_id(322) is False

            quota_page = await client.get("/")
            assert "0</strong> requests left" in quota_page.text
            assert "1 of 1 used" in quota_page.text

            services.mam.row = {
                **services.mam.row,
                "id": 323,
                "title": "The Butcher's Masquerade",
            }
            blocked = await client.post(
                "/request/submit",
                data={
                    "mam_id": "323",
                    "requester_name": "Regular Reader",
                    "requester_contact": "",
                    "note": "Over the limit",
                    "goodreads_url": "",
                    "website": "",
                },
                follow_redirects=False,
            )
            assert blocked.status_code == 303
            assert (
                "request_error=Weekly+request+limit+reached"
                in blocked.headers["location"]
            )
            blocked_page = await client.get(blocked.headers["location"])
            assert "has used all 1 requests" in blocked_page.text
            assert len(repository.request_rows()) == 2
            assert repository.has_pending_mam_id(323) is False

    asyncio.run(exercise())

    inbox = TestClient(app).get("/requests?status=all")
    assert "Account: admin · trusted for auto-approval" in inbox.text
    assert "Decision by admin" in inbox.text
    assert "Account: reader" in inbox.text


def test_account_only_portal_fails_closed_and_rejects_shared_cookie(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    portal_config(
        config_path,
        access_code="old-family-code",
        require_account_login=True,
        users=(
            {
                "username": "reader",
                "password": "reader password",
                "display_name": "Tracked Reader",
            },
        ),
    )
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    app = create_app(config_path, database)
    app.state.services = PortalServices(load_config(config_path))
    legacy_cookie = hmac.new(
        b"session",
        b"old-family-code",
        hashlib.sha256,
    ).hexdigest()

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=app,
            client=("203.0.113.20", 41234),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://requests.example.test",
            cookies={"mysuite_request_access": legacy_cookie},
        ) as client:
            locked = await client.get("/")
            assert 'name="username"' in locked.text
            assert "Goodreads link reader" not in locked.text
            rejected = await client.post(
                "/request/submit",
                data={"mam_id": "321"},
            )
            assert rejected.status_code == 403
            assert repository.request_rows() == []

            logged_in = await client.post(
                "/request/unlock",
                data={"username": "reader", "password": "reader password"},
                follow_redirects=False,
            )
            assert logged_in.status_code == 303
            submitted = await client.post(
                "/request/submit",
                data={"mam_id": "321"},
                follow_redirects=False,
            )
            assert submitted.status_code == 303
            assert repository.request_rows()[0]["requester_username"] == "reader"

    asyncio.run(exercise())


def test_account_only_portal_needs_an_account_and_honors_forwarded_host(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    portal_config(
        config_path,
        require_account_login=True,
        domain="bookrequest.randyplungis.com",
    )
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    app = create_app(config_path, database)
    app.state.services = PortalServices(load_config(config_path))

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app, client=("172.20.0.4", 41234))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://mlm:3157",
            headers={
                "x-forwarded-host": "bookrequest.randyplungis.com",
                "x-forwarded-proto": "https",
            },
        ) as client:
            locked = await client.get("/")
            assert locked.status_code == 503
            assert "Account setup required" in locked.text
            assert 'name="access_code"' not in locked.text
            assert (await client.get("/config")).status_code == 404

    asyncio.run(exercise())


def test_loopback_reverse_proxy_does_not_bypass_shared_access_code(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    portal_config(config_path, access_code="family-only")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    app = create_app(config_path, database)
    app.state.services = PortalServices(load_config(config_path))

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41234))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://requests.example.test",
            headers={
                "x-forwarded-for": "203.0.113.20",
                "x-forwarded-proto": "https",
            },
        ) as client:
            locked = await client.get("/")
            assert locked.status_code == 200
            assert "private request portal" in locked.text
            assert "Goodreads link reader" not in locked.text
            assert 'href="/static/app.css?v=0.5.0b62"' in locked.text
            assert "http://requests.example.test/static/" not in locked.text

    asyncio.run(exercise())
