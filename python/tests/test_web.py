from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mlm.web import create_app


def test_dashboard_and_health_on_fresh_database(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('mam_id = ""\n', encoding="utf-8")
    app = create_app(config, tmp_path / "data.sqlite3")
    client = TestClient(app)

    health = client.get("/health")
    dashboard = client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert dashboard.status_code == 200
    assert "MLM Python" in dashboard.text
