from __future__ import annotations

from pathlib import Path

import pytest

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
