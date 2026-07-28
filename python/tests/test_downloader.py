from __future__ import annotations

import asyncio
from pathlib import Path

from mlm.config import Config
from mlm.database import ensure_database
from mlm.downloader import grab_selected_torrents
from mlm.repository import Repository


class FakeMam:
    requested: list[tuple[str, int]]

    def __init__(self) -> None:
        self.requested = []

    async def user_info(self) -> dict:
        return {
            "uploaded_bytes": 10_000,
            "downloaded_bytes": 0,
            "wedges": 2,
            "unsat": {"limit": 10, "count": 0},
        }

    async def get_torrent_file(self, download_hash: str, torrent_id: int) -> bytes:
        self.requested.append((download_hash, torrent_id))
        return b"d4:infod6:lengthi12e4:name4:bookee"


class FakeQbit:
    added: list[bytes]

    def __init__(self) -> None:
        self.added = []

    async def torrents(self, *, hashes=()) -> list[dict]:
        return []

    async def add_torrent(self, torrent_file: bytes, **_: object) -> None:
        self.added.append(torrent_file)


def test_download_job_moves_selected_record_into_library_catalog(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    repository.add_selected(
        {
            "mam_id": 42,
            "goodreads_id": None,
            "hash": None,
            "dl_link": "secret-hash",
            "unsat_buffer": 0,
            "wedge_buffer": None,
            "cost": "Ratio",
            "category": None,
            "tags": [],
            "title_search": "book",
            "meta": {"mam_id": 42, "title": "Book", "size": 12},
            "grabber": "test",
            "created_at": "2025-01-01T00:00:00Z",
            "started_at": None,
            "removed_at": None,
        }
    )
    mam = FakeMam()
    qbit = FakeQbit()

    result = asyncio.run(
        grab_selected_torrents(Config(mam_id="cookie"), repository, mam, qbit)
    )

    assert result.downloaded == 1
    assert mam.requested == [("secret-hash", 42)]
    assert len(qbit.added) == 1
    assert repository.pending_selected() == []
    assert repository.table_rows("torrents")[0]["mam_id"] == 42
