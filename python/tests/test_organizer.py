from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mlm.config import Config, QbitConfig
from mlm.database import ensure_database
from mlm.library import organize_completed
from mlm.repository import Repository


class FakeQbit:
    def __init__(self, save_path: Path) -> None:
        self.save_path = save_path
        self.requested_categories: list[str | None] = []

    async def torrents(self, *, category: str | None = None) -> list[dict]:
        self.requested_categories.append(category)
        rows = [
            {
                "hash": "abc123",
                "name": "Book",
                "progress": 1,
                "save_path": str(self.save_path),
                "category": "Audiobooks",
                "tags": "",
            }
        ]
        if category is not None:
            return [row for row in rows if row["category"] == category]
        return rows

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


class NestedJsonMam(FakeMam):
    async def get_torrent_info(self, torrent_hash: str) -> dict:
        row = await super().get_torrent_info(torrent_hash)
        row["author_info"] = '{"1":"An Author"}'
        row["narrator_info"] = '{"1":"A Narrator"}'
        row["series_info"] = "{}"
        row["categories"] = "[]"
        return row


class MixedQbit(FakeQbit):
    async def torrents(self, *, category: str | None = None) -> list[dict]:
        self.requested_categories.append(category)
        rows = [
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
        if category is not None:
            return [row for row in rows if row["category"] == category]
        return rows

    async def files(self, torrent_hash: str) -> list[dict]:
        if torrent_hash == "missing123":
            return [{"name": "missing/book.m4b"}]
        return [{"name": "download/book.m4b"}]


class ScopedQbit(FakeQbit):
    async def torrents(self, *, category: str | None = None) -> list[dict]:
        self.requested_categories.append(category)
        rows = [
            {
                "hash": "audio123",
                "name": "Audio",
                "progress": 0.5,
                "save_path": str(self.save_path),
                "category": "Audiobooks",
                "tags": "",
            },
            {
                "hash": "ebook123",
                "name": "Ebook",
                "progress": 0.5,
                "save_path": str(self.save_path),
                "category": "Ebooks",
                "tags": "",
            },
            {
                "hash": "video123",
                "name": "Video",
                "progress": 1,
                "save_path": "Z:\\Videos",
                "category": "Movies",
                "tags": "",
            },
        ]
        if category is not None:
            return [row for row in rows if row["category"] == category]
        return rows


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
    qbit = FakeQbit(downloads)
    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            qbit,
            FakeMam(),
            progress=lambda message, level, context: progress.append(
                (message, level, context)
            ),
        )
    )

    destination = library_root / "An Author" / "Book {A Narrator}" / "book.m4b"
    assert result.linked == 1
    assert result.scanned == 1
    assert qbit.requested_categories == ["Audiobooks"]
    assert destination.read_bytes() == b"audio"
    assert os.path.samefile(source, destination)
    stored = repository.torrent("abc123")
    assert stored is not None
    assert stored["library_path"] == str(destination.parent)
    assert any(message.startswith("Organizer scope: 1") for message, _, _ in progress)
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

    qbit = MixedQbit(downloads)
    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            qbit,
            FakeMam(),
            progress=lambda message, level, context: progress.append(
                (message, level, context)
            ),
        )
    )

    assert result.scanned == 2
    assert result.failed == 1
    assert result.linked == 1
    assert qbit.requested_categories == ["Audiobooks"]
    assert repository.torrent("abc123") is not None
    assert any(message.startswith("Failed Missing Book") for message, _, _ in progress)
    failures = repository.table_rows("errored_torrents")
    assert len(failures) == 1
    assert failures[0]["id"] == {"Organizer": "missing123"}
    assert Path(failures[0]["context"]["source"]).parts[-2:] == (
        "missing",
        "book.m4b",
    )
    assert failures[0]["context"]["destination"].endswith("book.m4b")
    assert failures[0]["context"]["method"] == "copy"
    assert result.failures[0]["remediation"]
    assert not list((tmp_path / "library").rglob("*.heavymlm-staging-*"))


def test_organizer_rolls_back_partial_copy_and_records_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = tmp_path / "downloads"
    source = downloads / "download" / "book.m4b"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"complete audio")
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
                "method": "copy",
            },
        ),
    )

    def fail_after_partial_copy(_: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise OSError("simulated disk write failure")

    monkeypatch.setattr("mlm.library.shutil.copy2", fail_after_partial_copy)
    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            FakeQbit(downloads),
            FakeMam(),
        )
    )

    target = library_root / "An Author" / "Book {A Narrator}"
    assert result.failed == 1
    assert result.linked == 0
    assert not target.exists()
    assert not list(library_root.rglob("*.heavymlm-staging-*"))
    assert repository.torrent("abc123") is None
    failure = repository.table_rows("errored_torrents")[0]
    assert "simulated disk write failure" in failure["error"]
    assert failure["context"]["source"] == str(source)
    assert failure["context"]["destination"] == str(target / "book.m4b")
    assert failure["context"]["method"] == "copy"


def test_organizer_replaces_empty_ghost_folder_and_clears_old_error(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "downloads"
    source = downloads / "download" / "book.m4b"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    library_root = tmp_path / "library"
    target = library_root / "An Author" / "Book {A Narrator}"
    target.mkdir(parents=True)
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    repository.record_organizer_error(
        "abc123",
        "Book",
        "old copy failure",
        {"destination": str(target)},
    )
    config = Config(
        mam_id="cookie",
        qbittorrent=(QbitConfig(url="http://qbit"),),
        libraries=(
            {
                "category": "Audiobooks",
                "library_dir": str(library_root),
                "method": "copy",
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

    assert result.linked == 1
    assert (target / "book.m4b").read_bytes() == b"audio"
    assert repository.table_rows("errored_torrents") == []


def test_organizer_accepts_live_mam_nested_json_metadata(tmp_path: Path) -> None:
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

    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            FakeQbit(downloads),
            NestedJsonMam(),
        )
    )

    assert result.linked == 1
    assert result.skip_reasons.get("missing_author", 0) == 0


def test_organizer_only_requests_configured_library_categories(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    config = Config(
        mam_id="cookie",
        qbittorrent=(QbitConfig(url="http://qbit"),),
        libraries=(
            {
                "category": "Audiobooks",
                "library_dir": str(tmp_path / "audio"),
            },
            {
                "category": "Ebooks",
                "library_dir": str(tmp_path / "ebooks"),
            },
        ),
    )
    qbit = ScopedQbit(tmp_path / "downloads")

    result = asyncio.run(
        organize_completed(
            config,
            repository,
            config.qbittorrent[0],
            qbit,
            FakeMam(),
        )
    )

    assert qbit.requested_categories == ["Audiobooks", "Ebooks"]
    assert result.scanned == 2
    assert result.incomplete == 2
