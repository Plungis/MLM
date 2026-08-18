from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mlm.config import load_config
from mlm.database import connect, ensure_database
from mlm.migration import canonical_json
from mlm.repository import Repository
from mlm.request_auth import verify_request_password
from mlm.scheduler import JobStatus
from mlm.web import create_app


class FakeServices:
    def __init__(self, config) -> None:
        self.config = config
        self.jobs = {}
        self.triggered: list[str] = []
        self.mam_stats = {
            "slots_used": 100,
            "slots_total": 150,
            "slot_cap": 140,
            "wedges": 8,
            "wedge_buffer": 3,
        }

    async def reconfigure(self, config) -> None:
        self.config = config

    async def trigger(self, name: str) -> None:
        self.triggered.append(name)


class FakeSearchMam:
    def __init__(self) -> None:
        self.query = None

    async def search(self, query: dict) -> dict:
        self.query = query
        if query.get("tor", {}).get("startNumber", 0):
            return {"found": "237", "data": []}
        return {
            "found": "237",
            "data": [
                {
                    "id": "321",
                    "title": "The Search Result",
                    "author_info": '{"1":"An Author"}',
                    "narrator_info": '{"2":"A Narrator"}',
                    "series_info": '{"3":["A Series","4"]}',
                    "filetype": "m4b",
                    "size": "734.5 MiB",
                    "catname": "Audiobook",
                    "language": "1",
                    "numfiles": "1",
                    "seeders": "12",
                    "leechers": "2",
                    "times_completed": "99",
                    "owner_name": "BookSeeder",
                    "added": "2026-08-05 12:00:00",
                    "personal_freeleech": "1",
                    "vip": "0",
                }
            ],
        }


def test_dashboard_and_health_on_fresh_database(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('mam_id = ""\n', encoding="utf-8")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    event = {
        "id": "event-1",
        "torrent_id": "torrent-1",
        "mam_id": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "event": "RemovedFromMam",
    }
    with connect(database) as connection:
        connection.execute(
            """INSERT INTO events
               (id_json, torrent_id, mam_id, created_at_json, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                canonical_json(event["id"]),
                event["torrent_id"],
                event["mam_id"],
                canonical_json(event["created_at"]),
                canonical_json(event),
            ),
        )
    Repository(database).add_selected(
        {
            "mam_id": 1001,
            "title_search": "A queued book",
            "created_at": "2025-01-01T00:00:00Z",
            "started_at": None,
            "removed_at": None,
            "meta": {"title": "A queued book"},
        }
    )
    app = create_app(config, database)
    client = TestClient(app)

    health = client.get("/health")
    dashboard = client.get("/")
    triggered_dashboard = client.get("/?triggered=organizer")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert dashboard.status_code == 200
    assert "MyAnonaSuite" in dashboard.text
    assert "HeavyMLM" in dashboard.text
    assert "Request Portal" in dashboard.text
    assert 'href="/request"' in dashboard.text
    assert "Library Control" not in dashboard.text
    assert "Your library, at a glance" in dashboard.text
    assert "How HeavyMLM works" in dashboard.text
    assert "There is no download queue to manage" in dashboard.text
    assert "Download &amp; organize" in dashboard.text
    assert "HeavyMLM sends selected releases automatically" in dashboard.text
    assert "Download Queue" not in dashboard.text
    assert 'href="/records/selected_torrents"' not in dashboard.text
    assert 'action="/actions/lists"' in dashboard.text
    assert 'action="/actions/autograb"' in dashboard.text
    assert 'action="/actions/downloader"' in dashboard.text
    assert 'action="/actions/organizer"' in dashboard.text
    assert 'action="/actions/cleaner"' in dashboard.text
    assert "Run HeavyMLM now" in dashboard.text
    assert "Schedules remain enabled" in dashboard.text
    assert Repository(database).has_pending_mam_id(1001) is True
    assert 'class="nav-link active"' in dashboard.text
    assert "View live details" in triggered_dashboard.text
    assert 'data-focus-job="organizer"' in triggered_dashboard.text
    assert client.get("/static/app.css").status_code == 200
    events = client.get("/records/events")
    assert events.status_code == 200
    assert "Lifecycle events" in events.text
    assert "Event details" in events.text
    config_page = client.get("/config")
    assert config_page.status_code == 200
    assert "Complete configuration" in config_page.text
    assert "Download if wedge fails" in config_page.text
    assert "Enable request portal" in config_page.text
    assert 'name="request_portal_domains"' in config_page.text
    assert 'name="request_portal_require_account_login"' in config_page.text
    assert 'name="request_portal_username"' in config_page.text
    assert 'name="request_portal_password"' in config_page.text
    assert "Request login accounts" in config_page.text
    assert 'action="/config/request-users/save"' in config_page.text
    assert 'name="weekly_request_limit"' in config_page.text
    assert 'name="config_toml"' in config_page.text
    diagnostics = client.get("/diagnostics?live=0")
    assert diagnostics.status_code == 200
    assert "Activity console" in diagnostics.text
    assert "Auto-refresh paused" in diagnostics.text
    absidekick = client.get("/suite/absidekick")
    assert absidekick.status_code == 200
    assert 'data-suite="absidekick"' in absidekick.text
    assert "ABSidekick" in absidekick.text
    assert "Beta V.91.1 integrated" in absidekick.text
    assert "Run Controls" in absidekick.text
    assert "Live Log" in absidekick.text
    assert 'id="activityPanel"' in absidekick.text
    assert 'id="activityProgress"' in absidekick.text
    assert "manual search" in absidekick.text.lower()
    assert '<div id="settingsForm">' in absidekick.text
    assert '<form id="settingsForm">' not in absidekick.text
    assert 'id="searchTimeoutSeconds"' in absidekick.text
    assert 'id="automaticFallbackProviders"' in absidekick.text
    assert 'id="matchPolicyPreset"' in absidekick.text
    assert 'id="policySummary"' in absidekick.text
    assert 'id="useEmbeddedFileMetadata"' in absidekick.text
    assert 'id="repairSeries"' in absidekick.text
    assert 'src="/static/absidekick.js?v=0.5.0b60"' in absidekick.text
    assert 'href="/static/absidekick.css?v=0.5.0b60"' in absidekick.text
    absidekick_script = client.get("/static/absidekick.js")
    assert absidekick_script.status_code == 200
    assert 'api("/api/review/search"' in absidekick_script.text
    assert "MATCH FOUND" in absidekick_script.text
    assert "NO CONFIDENT MATCH" in absidekick_script.text
    assert "NO MATCH FOUND" in absidekick_script.text
    assert "data-manual-search-outcome" in absidekick_script.text
    assert 'api("/api/activity"' in absidekick_script.text
    assert "Scanning Review Tags" in absidekick_script.text
    assert "runVisibleAction" in absidekick_script.text
    assert "renderPolicySummary" in absidekick_script.text
    assert "renderSearchAttempts" in absidekick_script.text
    assert "Search path" in absidekick_script.text
    for page in (
        "run",
        "review",
        "targeting",
        "matching",
        "tags",
        "config",
    ):
        assert client.get(f"/suite/absidekick/{page}").status_code == 200
    absidekick_config = client.get("/suite/absidekick/config")
    assert "Connection" in absidekick_config.text
    assert 'id="baseUrl"' in absidekick_config.text
    assert 'id="token"' in absidekick_config.text
    assert 'id="absTokenStatus"' in absidekick_config.text
    assert "Paste token once; it will be saved privately" in absidekick_config.text
    assert 'id="googleBooksApiKey"' in absidekick_config.text
    assert 'id="testGoogleBooksBtn"' in absidekick_config.text
    assert "How to create and secure a Google Books API key" in absidekick_config.text
    assert "books.googleapis.com" in absidekick_config.text
    assert 'id="openLibraryEnabled"' in absidekick_config.text
    assert 'id="openLibraryContactEmail"' in absidekick_config.text
    assert "official Open Library API guidelines" in absidekick_config.text
    assert client.get("/suite/absidekick/not-real").status_code == 404
    absidekick_state = client.get("/api/absidekick/state")
    assert absidekick_state.status_code == 200
    assert absidekick_state.json()["version"] == "Beta V.91.1"
    assert absidekick_state.json()["activity"]["status"] == "idle"
    assert client.get("/api/absidekick/activity").json()["activity"]["status"] == "idle"
    missing_google_key = client.post("/api/absidekick/provider/google/test", json={})
    assert missing_google_key.status_code == 400
    assert "Enter a Google Books API key" in missing_google_key.json()["error"]
    assert 'api("/api/provider/google/test"' in absidekick_script.text
    assert "No Google request was sent" in absidekick_script.text
    saved_absidekick = client.post(
        "/api/absidekick/settings",
        json={
            "settings": {
                "connection": {
                    "baseUrl": "http://localhost:13378",
                    "libraryId": "library-1",
                    "provider": "audible",
                    "rememberConnection": True,
                },
                "matching": {"threshold": 88},
            },
            "token": "abs-secret-token",
            "openLibraryEnabled": True,
            "openLibraryContactEmail": "owner@example.com",
        },
    )
    assert saved_absidekick.status_code == 200
    assert saved_absidekick.json()["settings"]["connection"]["hasToken"] is True
    assert "token" not in saved_absidekick.json()["settings"]["connection"]
    assert (
        saved_absidekick.json()["settings"]["providers"]["openLibraryEnabled"] is True
    )
    assert (
        saved_absidekick.json()["settings"]["providers"]["openLibraryContactEmail"]
        == "owner@example.com"
    )
    absidekick_settings = tmp_path / "absidekick" / "settings.json"
    assert absidekick_settings.exists()
    saved_settings_text = absidekick_settings.read_text(encoding="utf-8")
    assert "abs-secret-token" in saved_settings_text
    assert "owner@example.com" in saved_settings_text
    invalid_open_library_contact = client.post(
        "/api/absidekick/settings",
        json={
            "settings": {},
            "openLibraryContactEmail": "not-an-email",
        },
    )
    assert invalid_open_library_contact.status_code == 400
    assert (
        "valid Open Library contact email"
        in invalid_open_library_contact.json()["error"]
    )
    spender = client.get("/suite/mam-spender")
    assert spender.status_code == 200
    assert 'data-suite="mam-spender"' in spender.text
    assert "Spend deliberately" in spender.text
    assert "SPEND_AUDIT.log" in spender.text
    assert "What should the spender buy?" in spender.text
    assert "Changes apply immediately" in spender.text
    assert 'data-spender-setting-value="10000"' in spender.text
    assert "Module theme" not in spender.text
    for page in ("dashboard", "config", "history", "analytics", "mam-data"):
        assert client.get(f"/suite/mam-spender/{page}").status_code == 200
    spender_config = client.get("/suite/mam-spender/config")
    assert "MAM-Spender configuration" in spender_config.text
    assert "Import old config.json" in spender_config.text
    assert "MAM-Spender Web Edition v1.4.0" in spender_config.text
    assert "0.5.0b60" in spender_config.text
    assert "What should the spender buy?" in spender_config.text
    assert "Module theme" not in spender_config.text
    assert 'href="/suite/mam-spender/config"' in spender_config.text
    # Keep bookmarks from the first integrated beta working.
    assert (
        "MAM-Spender configuration"
        in client.get("/suite/mam-spender?view=settings").text
    )
    spender_history = client.get("/suite/mam-spender/history")
    assert "MaM bonus history" in spender_history.text
    assert client.get("/suite/mam-spender/not-real").status_code == 404
    assert client.get("/suite/not-real").status_code == 404

    app.state.services = FakeServices(load_config(config))
    app.state.services.jobs["organizer:0"] = JobStatus(
        running=True,
        progress=[
            {
                "created_at": "2025-01-01T00:00:00Z",
                "level": "info",
                "message": "Inspecting files: Book",
                "context": {"current": 1, "total": 2},
            }
        ],
    )
    live_dashboard = client.get("/")
    assert "40 left" in live_dashboard.text
    assert "5 spendable" in live_dashboard.text
    assert "1 running now" in live_dashboard.text
    job_status = client.get("/api/jobs")
    assert job_status.status_code == 200
    assert job_status.json()["jobs"]["organizer:0"]["running"] is True
    assert (
        job_status.json()["jobs"]["organizer:0"]["progress"][0]["message"]
        == "Inspecting files: Book"
    )
    saved = client.post(
        "/config",
        data={
            "min_ratio": "2.5",
            "unsat_buffer": "10",
            "max_unsat_slots": "140",
            "wedge_buffer": "3",
            "prefer_wedges": "true",
            "download_on_wedge_failure": "true",
            "grab_both_formats": "true",
            "request_portal_enabled": "true",
            "request_portal_require_account_login": "true",
            "request_portal_domains": "requests.example.test",
            "request_portal_title": "Family Requests",
            "request_portal_rate_limit": "15",
            "request_portal_username": "family",
            "request_portal_password": "correct horse",
            "request_portal_access_code": "family-only",
            "search_interval": "20",
            "import_interval": "60",
            "link_interval": "5",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    updated = load_config(config)
    assert updated.max_unsat_slots == 140
    assert updated.wedge_buffer == 3
    assert updated.prefer_wedges is True
    assert updated.download_on_wedge_failure is True
    assert updated.grab_both_formats is True
    assert updated.request_portal_enabled is True
    assert updated.request_portal_require_account_login is True
    assert updated.request_portal_domains == ("requests.example.test",)
    assert updated.request_portal_title == "Family Requests"
    assert updated.request_portal_username == "family"
    assert verify_request_password(
        "correct horse", updated.request_portal_password_hash
    )
    assert "correct horse" not in config.read_text(encoding="utf-8")
    assert updated.request_portal_access_code == "family-only"
    assert updated.request_portal_rate_limit == 15

    account_created = client.post(
        "/config/request-users/save",
        data={
            "username": "trusted-reader",
            "display_name": "Trusted Reader",
            "password": "reader password",
            "auto_approve": "true",
            "weekly_request_limit": "10",
        },
        follow_redirects=False,
    )
    assert account_created.status_code == 303
    account_config = load_config(config)
    assert len(account_config.request_portal_users) == 1
    account = account_config.request_portal_users[0]
    assert account.username == "trusted-reader"
    assert account.display_name == "Trusted Reader"
    assert account.permissions == ("auto_approve",)
    assert account.weekly_request_limit == 10
    assert verify_request_password("reader password", account.password_hash)
    account_page = client.get("/config#request-accounts")
    assert "Trusted Reader" in account_page.text
    assert "Auto-approve" in account_page.text
    assert "10 requests per rolling week" in account_page.text
    assert "does not grant access to HeavyMLM administration" in account_page.text

    account_deleted = client.post(
        "/config/request-users/delete",
        data={"username": "trusted-reader"},
        follow_redirects=False,
    )
    assert account_deleted.status_code == 303
    assert load_config(config).request_portal_users == ()

    full_saved = client.post(
        "/config/full",
        data={
            "config_toml": r"""
mam_id = "replacement-secret"
prefer_wedges = true
download_on_wedge_failure = true
wedge_buffer = 100
audio_types = ["m4b"]

[[qbittorrent]]
url = "http://localhost:8090"
password = "replacement-password"

[[library]]
category = "Audiobooks"
library_dir = 'E:\MLM Audio'
method = "copy"
"""
        },
        follow_redirects=False,
    )
    assert full_saved.status_code == 303
    fully_updated = load_config(config)
    assert fully_updated.mam_id == "replacement-secret"
    assert fully_updated.wedge_buffer == 100
    assert fully_updated.download_on_wedge_failure is True
    assert fully_updated.qbittorrent[0].password == "replacement-password"
    assert app.state.services.config.wedge_buffer == 100

    invalid_full_save = client.post(
        "/config/full",
        data={"config_toml": 'mam_id = "nope"\nunknown_setting = true\n'},
    )
    assert invalid_full_save.status_code == 400
    assert "unknown configuration fields" in invalid_full_save.text
    assert load_config(config).mam_id == "replacement-secret"


def test_errors_explain_recovery_and_support_retry_and_dismiss(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('mam_id = ""\n', encoding="utf-8")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    selected = {
        "mam_id": 42,
        "goodreads_id": None,
        "hash": None,
        "dl_link": "private-hash",
        "unsat_buffer": 0,
        "wedge_buffer": 3,
        "cost": "UseWedge",
        "category": "Audiobooks",
        "tags": [],
        "title_search": "example book",
        "meta": {"mam_id": 42, "title": "Example Book", "authors": ["A. Writer"]},
        "grabber": "test",
        "created_at": "2025-01-01T00:00:00Z",
        "started_at": None,
        "removed_at": None,
    }
    repository.add_selected(selected)
    repository.record_grab_error(
        selected, RuntimeError("wedge reserve reached (3 available, 3 reserved)")
    )

    app = create_app(config_path, database)
    services = FakeServices(load_config(config_path))
    app.state.services = services
    client = TestClient(app)

    page = client.get("/records/errored_torrents")
    assert page.status_code == 200
    assert "Freeleech wedge reserve reached" in page.text
    assert "wedge reserve reached (3 available, 3 reserved)" in page.text
    assert "Path forward" in page.text
    assert "lower the wedge buffer" in page.text
    assert "Retry now" in page.text
    assert "/operations?view=diagnostics&component=downloader" in page.text

    retried = client.post(
        "/errors/retry",
        data={"error_id": '{"Grabber":42}', "mam_id": "42"},
        follow_redirects=False,
    )
    assert retried.status_code == 303
    assert repository.table_rows("errored_torrents") == []
    assert services.triggered == ["downloader"]

    repository.record_grab_error(selected, RuntimeError("temporary failure"))
    repository.delete_selected(42)
    stale_page = client.get("/records/errored_torrents")
    assert "No longer pending" in stale_page.text
    assert "Retry now" not in stale_page.text
    stale_retry = client.post(
        "/errors/retry",
        data={"error_id": '{"Grabber":42}', "mam_id": "42"},
    )
    assert stale_retry.status_code == 409
    assert len(repository.table_rows("errored_torrents")) == 1
    dismissed = client.post(
        "/errors/dismiss",
        data={"error_id": '{"Grabber":42}'},
        follow_redirects=False,
    )
    assert dismissed.status_code == 303
    assert repository.table_rows("errored_torrents") == []
    assert (
        client.post("/errors/dismiss", data={"error_id": "not-json"}).status_code == 400
    )

    repository.record_grab_error(
        selected,
        RuntimeError("wedge rejected by tracker"),
        context={
            "wedge_attempted": True,
            "prefer_wedges": True,
            "wedges_before": 250,
            "wedges_after": 250,
            "wedge_buffer": 100,
            "download_on_wedge_failure": False,
            "stage": "wedge_response",
            "wedge_reason": "rejected",
            "http_status": 200,
            "content_type": "application/json",
            "endpoint": "/tor/download.php?tid=42&fl",
            "tracker_response": {"success": False, "error": "test rejection"},
        },
    )
    wedge_page = client.get("/records/errored_torrents")
    assert "Wedge attempt debug" in wedge_page.text
    assert "250 before" in wedge_page.text
    assert "100 reserved" in wedge_page.text
    assert "wedge_response / rejected" in wedge_page.text
    assert "Disabled (strict)" in wedge_page.text
    assert "/tor/download.php?tid=42&amp;fl" in wedge_page.text
    assert "test rejection" in wedge_page.text

    repository.add_selected(selected)
    repository.record_grab_error(selected, RuntimeError("wedge rejected by tracker"))
    repository.record_organizer_error(
        "organizer-hash",
        "Another Book",
        "copy failed",
        {"source": "D:\\Book.m4b", "destination": "E:\\Library\\Book.m4b"},
    )
    dismiss_all_page = client.get("/operations?view=errors")
    assert "unresolved" in dismiss_all_page.text
    assert 'action="/errors/dismiss-all"' in dismiss_all_page.text
    assert (
        "will not delete torrents, downloads, files, or job history"
        in dismiss_all_page.text
    )

    dismissed_all = client.post("/errors/dismiss-all", follow_redirects=False)
    assert dismissed_all.status_code == 303
    assert dismissed_all.headers["location"].endswith("dismissed_all=2")
    assert repository.table_rows("errored_torrents") == []
    assert repository.has_pending_mam_id(42) is True

    dismissal_confirmation = client.get(dismissed_all.headers["location"])
    assert "Dismissed 2 stored errors" in dismissal_confirmation.text
    assert (
        "Torrents, downloads, files, and job history were not changed"
        in dismissal_confirmation.text
    )


def test_organizer_copy_failure_is_visible_on_dashboard_and_errors(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('mam_id = ""\n', encoding="utf-8")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    source = "D:\\Downloads\\Book\\book.m4b"
    destination = "E:\\MLM Audio\\Writer\\Book\\book.m4b"
    failure = {
        "torrent": "Book",
        "hash": "abc123",
        "error": "FilePlacementError: file placement failed: disk is full",
        "error_type": "FilePlacementError",
        "source": source,
        "destination": destination,
        "method": "copy",
        "remediation": "Free space on the library drive and run the organizer again.",
    }
    repository.record_organizer_error("abc123", "Book", failure["error"], failure)
    app = create_app(config_path, database)
    services = FakeServices(load_config(config_path))
    services.jobs["organizer:0"] = JobStatus(
        last_result={
            "scanned": 1,
            "linked": 0,
            "already_existing": 2,
            "incomplete": 0,
            "skipped": 0,
            "failed": 1,
            "skip_reasons": {},
            "failures": [failure],
        }
    )
    app.state.services = services
    client = TestClient(app)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Organizer could not publish 1 library item(s)" in dashboard.text
    assert "2 already existed" in dashboard.text
    assert "disk is full" in dashboard.text
    assert source in dashboard.text
    assert destination in dashboard.text
    assert "/operations?view=errors" in dashboard.text

    errors = client.get("/records/errored_torrents")
    assert errors.status_code == 200
    assert "Library file placement failed" in errors.text
    assert source in errors.text
    assert destination in errors.text
    assert "Free space on the library drive" in errors.text
    assert "/operations?view=diagnostics&component=organizer" in errors.text


def test_search_renders_rich_heavymlm_release_cards(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('mam_id = ""\n', encoding="utf-8")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    app = create_app(config_path, database)
    services = FakeServices(load_config(config_path))
    services.mam = FakeSearchMam()  # type: ignore[attr-defined]
    app.state.services = services
    client = TestClient(app)

    response = client.get("/search?q=Search+Result")

    assert response.status_code == 200
    assert "1 filtered match" in response.text
    assert "Scanned 1 of 237 MaM releases" in response.text
    assert "The Search Result" in response.text
    assert "An Author" in response.text
    assert "A Narrator" in response.text
    assert "A Series" in response.text
    assert "Personal freeleech" in response.text
    assert "734.5 MiB" in response.text
    assert "12</strong> seeders" in response.text
    assert services.mam.query["mediaInfo"] is True  # type: ignore[attr-defined]
    assert "description" not in services.mam.query  # type: ignore[operator]


def test_search_combines_author_series_and_filetype_filters(tmp_path: Path) -> None:
    class FilterMam:
        def __init__(self) -> None:
            self.queries: list[dict] = []

        async def search(self, query: dict) -> dict:
            self.queries.append(query)
            return {
                "found": 3,
                "data": [
                    {
                        "id": 1,
                        "title": "Dungeon Crawler Carl",
                        "author_info": '{"1":"Matt Dinniman"}',
                        "series_info": '{"1":["Dungeon Crawler Carl","1"]}',
                        "filetype": "m4b",
                        "catname": "Audiobook",
                        "language": 1,
                        "size": "500 MiB",
                        "seeders": 15,
                    },
                    {
                        "id": 2,
                        "title": "Carl's Doomsday Scenario",
                        "author_info": '{"1":"Matt Dinniman"}',
                        "series_info": '{"1":["Dungeon Crawler Carl","2"]}',
                        "filetype": "mp3",
                        "catname": "Audiobook",
                        "language": 1,
                        "size": "600 MiB",
                        "seeders": 12,
                    },
                    {
                        "id": 3,
                        "title": "Dominion of Blades",
                        "author_info": '{"1":"Matt Dinniman"}',
                        "series_info": '{"2":["Dominion of Blades","1"]}',
                        "filetype": "m4b",
                        "catname": "Audiobook",
                        "language": 1,
                        "size": "700 MiB",
                        "seeders": 8,
                    },
                ],
            }

    config_path = tmp_path / "config.toml"
    config_path.write_text('mam_id = ""\n', encoding="utf-8")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    app = create_app(config_path, database)
    services = FakeServices(load_config(config_path))
    mam = FilterMam()
    services.mam = mam  # type: ignore[attr-defined]
    app.state.services = services
    client = TestClient(app)

    response = client.get(
        "/search",
        params={
            "author": "Matt Dinniman",
            "series": "Dungeon Crawler Carl",
            "filetype": "m4b",
        },
    )

    assert response.status_code == 200
    assert "1 filtered match" in response.text
    assert "Dungeon Crawler Carl" in response.text
    assert "Carl&#39;s Doomsday Scenario" not in response.text
    assert "Dominion of Blades" not in response.text
    assert mam.queries[0]["tor"]["text"] == "Matt Dinniman"
    assert mam.queries[0]["tor"]["srchIn"] == ["author"]
    assert 'name="series" value="Dungeon Crawler Carl"' in response.text
    assert '<option value="m4b" selected>M4B</option>' in response.text


def test_record_pages_are_paginated(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('mam_id = ""\n', encoding="utf-8")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    with connect(database) as connection:
        for index in range(55):
            event = {
                "id": f"event-{index}",
                "torrent_id": f"torrent-{index}",
                "mam_id": index,
                "created_at": f"2025-01-01T00:00:{index:02d}Z",
                "event": "Test",
            }
            connection.execute(
                """INSERT INTO events
                   (id_json, torrent_id, mam_id, created_at_json, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    canonical_json(event["id"]),
                    event["torrent_id"],
                    event["mam_id"],
                    canonical_json(event["created_at"]),
                    canonical_json(event),
                ),
            )

    client = TestClient(create_app(config, database))
    first = client.get("/records/events")
    second = client.get("/records/events?page=2")

    assert first.status_code == 200
    assert "Page 1 / 2" in first.text
    assert "/operations?view=events&page=2" in first.text
    assert second.status_code == 200
    assert "Page 2 / 2" in second.text


def test_lists_and_processed_library_are_consolidated(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('mam_id = ""\n', encoding="utf-8")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    repository.upsert_list(
        {
            "id": "179120590:abs",
            "title": "ABS Reading List",
            "updated_at": "2026-08-06T12:00:00Z",
        }
    )
    repository.upsert_list_item(
        {
            "guid": ["179120590:abs", "book-123"],
            "list_id": "179120590:abs",
            "title": "Tracked Book",
            "authors": ["Example Author"],
            "created_at": "2026-08-06T12:00:00Z",
            "status": "already_grabbed",
            "selected_mam_ids": [123],
            "check_count": 1,
            "last_result": "Skipped: already selected in an earlier run",
        }
    )
    repository.record_linked(
        {
            "id": "hash-123",
            "mam_id": 123,
            "title_search": "tracked book",
            "created_at": "2026-08-06T12:00:00Z",
            "meta": {"title": "Tracked Book", "authors": ["Example Author"]},
            "library_path": "E:\\MLM Ebook\\Example Author\\Tracked Book",
        },
        None,
    )
    repository.add_duplicate(
        {
            "mam_id": 124,
            "title_search": "tracked book",
            "meta": {
                "title": "Tracked Book replacement",
                "authors": ["Example Author"],
            },
        },
        duplicate_of="hash-123",
    )

    client = TestClient(create_app(config, database))
    lists = client.get("/lists")
    filtered = client.get("/lists?list_id=179120590%3Aabs")
    library = client.get("/library")
    duplicates = client.get("/library?view=duplicates")
    old_lists = client.get("/records/list_items")
    old_duplicates = client.get("/records/duplicate_torrents")

    assert lists.status_code == 200
    assert "ABS Reading List" in lists.text
    assert "Tracked Book" in lists.text
    assert "1 sources" in lists.text
    assert filtered.status_code == 200
    assert "Skipped: already selected" in filtered.text
    assert library.status_code == 200
    assert "MLM Processed Library" in library.text
    assert "Tracked Book" in library.text
    assert duplicates.status_code == 200
    assert "Tracked Book replacement" in duplicates.text
    assert "Duplicate of hash-123" in duplicates.text
    assert old_lists.url.path == "/lists"
    assert old_duplicates.url.path == "/library"
    assert old_duplicates.url.query == b"view=duplicates"
