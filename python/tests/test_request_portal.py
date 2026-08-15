from __future__ import annotations

import asyncio
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
        return self.row if mam_id == 321 else None


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
) -> None:
    password_hash = hash_request_password(password) if password else ""
    path.write_text(
        f"""
mam_id = "session"
request_portal_enabled = true
request_portal_domains = ["requests.example.test"]
request_portal_title = "Randy's Library Requests"
request_portal_access_code = {json.dumps(access_code)}
request_portal_username = {json.dumps(username)}
request_portal_password_hash = {json.dumps(password_hash)}
request_portal_rate_limit = 20
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
    assert client.get("/").text.find("Dashboard") != -1

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
    assert submitted.headers["location"] == "/?submitted=1"
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
            assert 'href="/static/app.css?v=0.5.0b52"' in locked.text
            assert "http://requests.example.test/static/" not in locked.text

    asyncio.run(exercise())
