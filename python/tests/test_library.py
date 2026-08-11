from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mlm.audiobookshelf import match_torrents_to_audiobookshelf
from mlm.cleaner import remove_library_files
from mlm.library import library_directory, map_path, safe_torrent_path, select_format


def test_longest_path_mapping_wins() -> None:
    mapped = map_path(
        {"/downloads": "/books", "/downloads/audio": "/audiobooks"},
        "/downloads/audio/new",
    )
    assert mapped == Path("/audiobooks/new")


def test_library_directory_series_and_narrator() -> None:
    result = library_directory(
        False,
        {"library_dir": "/library"},
        {
            "authors": ["An Author"],
            "title": "The Book",
            "series": [{"name": "Saga", "entries": ["2"]}],
            "narrators": ["A Narrator"],
            "edition": None,
        },
    )
    assert result == Path("/library/An Author/Saga/Saga #2 - The Book {A Narrator}")


def test_format_preference_and_path_traversal() -> None:
    files = [{"name": "book/book.mp3"}, {"name": "book/book.m4b"}]
    assert select_format(None, ("m4b", "mp3"), files) == ".m4b"
    with pytest.raises(ValueError):
        safe_torrent_path("../escape.mp3")
    with pytest.raises(ValueError):
        safe_torrent_path("C:/escape.mp3")


def test_collection_library_files_are_removed_per_book(tmp_path: Path) -> None:
    items = []
    for title in ("Book One", "Book Two"):
        target = tmp_path / "An Author" / title
        target.mkdir(parents=True)
        (target / "book.m4b").write_bytes(b"audio")
        (target / "metadata.json").write_text("{}", encoding="utf-8")
        items.append(
            {
                "library_path": str(target),
                "library_files": ["book.m4b"],
            }
        )

    remove_library_files(
        {"library_path": None, "library_files": [], "collection_items": items}
    )

    assert not (tmp_path / "An Author" / "Book One").exists()
    assert not (tmp_path / "An Author" / "Book Two").exists()


def test_audiobookshelf_matches_each_collection_book() -> None:
    torrent = {
        "collection_items": [
            {
                "title": "Book One",
                "library_path": "/library/Book One",
                "meta": {"authors": ["An Author"]},
                "abs_id": None,
            },
            {
                "title": "Book Two",
                "library_path": "/library/Book Two",
                "meta": {"authors": ["An Author"]},
                "abs_id": None,
            },
        ]
    }

    class FakeRepository:
        updated: dict[str, Any] | None = None

        def library_torrents(self) -> list[dict[str, Any]]:
            return [torrent]

        def update_torrent(self, row: dict[str, Any]) -> None:
            self.updated = row

    class FakeClient:
        async def find_book(self, row: dict[str, Any]) -> dict[str, str]:
            return {"id": "abs-" + row["title"].casefold().replace(" ", "-")}

    repository = FakeRepository()
    matched = asyncio.run(match_torrents_to_audiobookshelf(repository, FakeClient()))

    assert matched == 2
    assert repository.updated is torrent
    assert [item["abs_id"] for item in torrent["collection_items"]] == [
        "abs-book-one",
        "abs-book-two",
    ]
