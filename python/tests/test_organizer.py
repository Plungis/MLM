from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mlm.config import Config, QbitConfig
from mlm.database import ensure_database
from mlm.library import organize_completed
from mlm.repository import Repository


class FakeQbit:
    def __init__(self, save_path: Path) -> None:
        self.save_path = save_path

    async def torrents(self) -> list[dict]:
        return [
            {
                "hash": "abc123",
                "name": "Book",
                "progress": 1,
                "save_path": str(self.save_path),
                "category": "Audiobooks",
                "tags": "",
            }
        ]

    async def files(self, _: str) -> list[dict]:
        return [{"name": "download/book.m4b"}]

    async def trackers(self, _: str) -> list[dict]:
        return []


class FakeMam:
    async def get_torrent_info(self, _: str) -> dict:
        return {
            "id": 88,
            "added": "2025-01-01 00:00:00",
            "author_info": {"1": "An Author"},
            "narrator_info": {"1": "A Narrator"},
            "series_info": {},
            "browseflags": 0,
            "main_cat": 13,
            "category": 13,
            "mediatype": 1,
            "maincat": 1,
            "categories": [],
            "catname": "Audiobook",
            "filetype": "m4b",
            "language": 1,
            "numfiles": 1,
            "size": "12 B",
            "title": "Book",
            "vip": False,
        }


def test_organizer_hardlinks_completed_torrent(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    source = downloads / "download" / "book.m4b"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    library_root = tmp_path / "library"
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    config = Config(
        mam_id="cookie",
        qbittorrent=(QbitConfig(url="http://qbit"),),
        libraries=(
            {
                "category": "Audiobooks",
                "library_dir": str(library_root),
                "method": "hardlink",
            },
        ),
    )

    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            FakeQbit(downloads),
            FakeMam(),
        )
    )

    destination = library_root / "An Author" / "Book {A Narrator}" / "book.m4b"
    assert result.linked == 1
    assert result.scanned == 1
    assert destination.read_bytes() == b"audio"
    assert os.path.samefile(source, destination)
    stored = repository.torrent("abc123")
    assert stored is not None
    assert stored["library_path"] == str(destination.parent)
