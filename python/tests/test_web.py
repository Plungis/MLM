from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mlm.config import load_config
from mlm.database import connect, ensure_database
from mlm.migration import canonical_json
from mlm.repository import Repository
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
    assert "Library Control" not in dashboard.text
    assert "Run pipeline" in dashboard.text
    assert 'class="nav-link active"' in dashboard.text
    assert "Show background activity" in triggered_dashboard.text
    assert 'data-focus-job="organizer"' in triggered_dashboard.text
    assert client.get("/static/app.css").status_code == 200
    events = client.get("/records/events")
    assert events.status_code == 200
    assert "Raw record" in events.text
    config_page = client.get("/config")
    assert config_page.status_code == 200
    assert "Complete configuration" in config_page.text
    assert "Download if wedge fails" in config_page.text
    assert 'name="config_toml"' in config_page.text
    diagnostics = client.get("/diagnostics?live=0")
    assert diagnostics.status_code == 200
    assert "Activity console" in diagnostics.text
    assert "Auto-refresh paused" in diagnostics.text
    absidekick = client.get("/suite/absidekick")
    assert absidekick.status_code == 200
    assert 'data-suite="absidekick"' in absidekick.text
    assert "Shell ready. Integration has not started yet." in absidekick.text
    spender = client.get("/suite/mam-spender")
    assert spender.status_code == 200
    assert 'data-suite="mam-spender"' in spender.text
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
    assert "/diagnostics?component=downloader" in page.text

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
    assert "No longer queued" in stale_page.text
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
            "incomplete": 0,
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
    assert "disk is full" in dashboard.text
    assert source in dashboard.text
    assert destination in dashboard.text
    assert "/records/errored_torrents" in dashboard.text

    errors = client.get("/records/errored_torrents")
    assert errors.status_code == 200
    assert "Library file placement failed" in errors.text
    assert source in errors.text
    assert destination in errors.text
    assert "Free space on the library drive" in errors.text
    assert "/diagnostics?component=organizer" in errors.text


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
    assert "/records/events?page=2" in first.text
    assert second.status_code == 200
    assert "Page 2 / 2" in second.text
