from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mlm.database import ensure_database
from mlm.modules.heavymlm.abs_sync import (
    fetch_all_abs_library_books,
    sync_audiobookshelf_library,
)
from mlm.repository import Repository


def test_fetch_all_abs_library_books_validation():
    # Missing libraryId
    with pytest.raises(ValueError, match="Audiobookshelf library ID is not configured"):
        fetch_all_abs_library_books({"connection": {}})

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
