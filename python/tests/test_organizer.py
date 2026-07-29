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


class MixedQbit(FakeQbit):
    async def torrents(self) -> list[dict]:
        return [
            {
                "hash": "missing123",
                "name": "Missing Book",
                "progress": 1,
                "save_path": str(self.save_path),
                "category": "Audiobooks",
                "tags": "",
            },
            {
                "hash": "abc123",
                "name": "Book",
                "progress": 1,
                "save_path": str(self.save_path),
                "category": "Audiobooks",
                "tags": "",
            },
        ]

    async def files(self, torrent_hash: str) -> list[dict]:
        if torrent_hash == "missing123":
            return [{"name": "missing/book.m4b"}]
        return [{"name": "download/book.m4b"}]


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

    progress: list[tuple[str, str, dict | None]] = []
    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            FakeQbit(downloads),
            FakeMam(),
            progress=lambda message, level, context: progress.append(
                (message, level, context)
            ),
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
    assert any(message.startswith("Loaded 1 torrents") for message, _, _ in progress)
    assert any(message.startswith("Placing file 1/1") for message, _, _ in progress)
    assert any(message.startswith("Organizer finished") for message, _, _ in progress)


def test_organizer_continues_after_one_torrent_fails(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    source = downloads / "download" / "book.m4b"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    config = Config(
        mam_id="cookie",
        qbittorrent=(QbitConfig(url="http://qbit"),),
        libraries=(
            {
                "category": "Audiobooks",
                "library_dir": str(tmp_path / "library"),
                "method": "copy",
            },
        ),
    )
    progress: list[tuple[str, str, dict | None]] = []

    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            MixedQbit(downloads),
            FakeMam(),
            progress=lambda message, level, context: progress.append(
                (message, level, context)
            ),
        )
    )

    assert result.scanned == 2
    assert result.failed == 1
    assert result.linked == 1
    assert repository.torrent("abc123") is not None
    assert any(message.startswith("Failed Missing Book") for message, _, _ in progress)
