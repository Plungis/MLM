from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mlm.config import Config
from mlm.database import ensure_database
from mlm.modules.absidekick.core import ABSAPIError
from mlm.modules.heavymlm.abs_sync import (
    fetch_all_abs_library_books,
    sync_audiobookshelf_library,
)
from mlm.repository import Repository
from mlm.web import create_app, resolve_abs_library_settings


def test_fetch_all_abs_library_books_validation():
    # Missing baseUrl or token
    with pytest.raises(ValueError, match="Audiobookshelf URL or API token is missing"):
        fetch_all_abs_library_books({"connection": {"libraryId": "lib-1"}})


def test_sync_audiobookshelf_library(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test.db"
    ensure_database(db_file)
    repo = Repository(db_file)

    fake_items = [
        {
            "id": "abs-1",
            "media": {
                "metadata": {
                    "title": "Unsouled",
                    "authors": [{"name": "Will Wight"}],
                    "series": [{"name": "Cradle", "sequence": "1"}],
                    "asin": "B01LW8PT7M",
                    "isbn": "",
                }
            },
            "path": "/audiobooks/Will Wight/Cradle/01 - Unsouled",
        },
        {
            "id": "abs-2",
            "media": {
                "metadata": {
                    "title": "Soulsmith",
                    "authorName": "Will Wight",
                    "seriesName": "Cradle",
                    "seriesSequence": "2",
                    "asin": "B01LYP0528",
                    "isbn": "",
                }
            },
            "relPath": "Will Wight/Cradle/02 - Soulsmith",
        },
    ]

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get(
            self, path: str, params: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            if params and params.get("page", 0) == 0:
                return {"results": fake_items, "total": 2}
            return {"results": [], "total": 2}

    monkeypatch.setattr(
        "mlm.modules.heavymlm.abs_sync.create_client",
        lambda *args, **kwargs: FakeClient(),
    )

    settings = {
        "connection": {
            "baseUrl": "http://abs.local:13378",
            "token": "secret-token",
            "libraryId": "library-main",
        }
    }

    result = sync_audiobookshelf_library(repo, settings)
    assert result["ok"] is True
    assert result["total_synced"] == 2

    assert repo.abs_books_count() == 2
    books = repo.abs_book_rows()
    assert len(books) == 2
    assert books[0]["title"] in {"Unsouled", "Soulsmith"}
    assert books[1]["title"] in {"Unsouled", "Soulsmith"}


def test_resolve_abs_library_settings_with_dict():
    config = Config(
        mam_id="test",
        audiobookshelf={
            "url": "http://abs.example.com:13378",
            "token": "test-secret-token",
            "library": "lib-uuid-1234",
        },
    )
    settings, token, is_configured = resolve_abs_library_settings(None, config)
    assert is_configured is True
    assert token == "test-secret-token"
    assert settings["connection"]["baseUrl"] == "http://abs.example.com:13378"
    assert settings["connection"]["libraryId"] == "lib-uuid-1234"


def test_library_page_loads_with_dict_audiobookshelf(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """mam_id = "test"
[audiobookshelf]
url = "http://abs.example.com:13378"
token = "test-secret-token"
library = "lib-uuid-1234"
""",
        encoding="utf-8",
    )
    database_path = tmp_path / "data.sqlite3"
    ensure_database(database_path)

    app = create_app(config_path, database_path)
    client = TestClient(app)

    response = client.get("/library")
    assert response.status_code == 200
    assert "Unified Books Catalog" in response.text
    assert "Sync with ABS" in response.text


def test_sync_audiobookshelf_stale_library_id_auto_recovery(
    tmp_path: Path, monkeypatch
):
    db_file = tmp_path / "test.db"
    ensure_database(db_file)
    repo = Repository(db_file)

    fake_items = [
        {
            "id": "abs-real-1",
            "media": {
                "metadata": {
                    "title": "Dungeon Crawler Carl",
                    "authors": [{"name": "Matt Dinniman"}],
                    "series": [{"name": "Dungeon Crawler Carl", "sequence": "1"}],
                }
            },
            "path": "/audiobooks/Matt Dinniman/DCC 1",
        }
    ]

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get(
            self, path: str, params: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            if path == "/api/libraries":
                return {
                    "libraries": [
                        {
                            "id": "real-active-lib-uuid",
                            "name": "Audiobooks",
                            "mediaType": "book",
                        }
                    ]
                }
            if "real-active-lib-uuid" in path:
                if params and params.get("page", 0) == 0:
                    return {"results": fake_items, "total": 1}
                return {"results": [], "total": 1}
            raise ABSAPIError(404, f"ABS API returned HTTP 404 for {path}")

    monkeypatch.setattr(
        "mlm.modules.heavymlm.abs_sync.create_client",
        lambda *args, **kwargs: FakeClient(),
    )

    settings = {
        "connection": {
            "baseUrl": "http://abs.local:13378",
            "token": "secret-token",
            "libraryId": "6dd4e1b6-44d8-4bda-8733-52ace89b7c40",
        }
    }

    result = sync_audiobookshelf_library(repo, settings)
    assert result["ok"] is True
    assert result["total_synced"] == 1
    assert repo.abs_books_count() == 1
    assert settings["connection"]["libraryId"] == "real-active-lib-uuid"
