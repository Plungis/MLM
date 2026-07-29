from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mlm.config import load_config
from mlm.database import connect, ensure_database
from mlm.migration import canonical_json
from mlm.scheduler import JobStatus
from mlm.web import create_app


class FakeServices:
    def __init__(self, config) -> None:
        self.config = config
        self.jobs = {}
        self.mam_stats = {
            "slots_used": 100,
            "slots_total": 150,
            "slot_cap": 140,
            "wedges": 8,
            "wedge_buffer": 3,
        }

    async def reconfigure(self, config) -> None:
        self.config = config


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
    assert "HeavyMLM" in dashboard.text
    assert "Run pipeline" in dashboard.text
    assert 'class="nav-link active"' in dashboard.text
    assert "Show background activity" in triggered_dashboard.text
    assert 'data-focus-job="organizer"' in triggered_dashboard.text
    assert client.get("/static/app.css").status_code == 200
    events = client.get("/records/events")
    assert events.status_code == 200
    assert "Raw record" in events.text
    assert client.get("/config").status_code == 200
    diagnostics = client.get("/diagnostics?live=0")
    assert diagnostics.status_code == 200
    assert "Activity console" in diagnostics.text
    assert "Auto-refresh paused" in diagnostics.text

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
    assert updated.grab_both_formats is True


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
