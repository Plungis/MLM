from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mlm.database import connect, ensure_database
from mlm.migration import canonical_json
from mlm.web import create_app


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

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert dashboard.status_code == 200
    assert "HeavyMLM" in dashboard.text
    assert "Run pipeline" in dashboard.text
    assert 'class="nav-link active"' in dashboard.text
    assert client.get("/static/app.css").status_code == 200
    events = client.get("/records/events")
    assert events.status_code == 200
    assert "Raw record" in events.text
    assert client.get("/config").status_code == 200
    diagnostics = client.get("/diagnostics?live=0")
    assert diagnostics.status_code == 200
    assert "Activity console" in diagnostics.text
    assert "Auto-refresh paused" in diagnostics.text
