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
        self.wedged: list[int] = []

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

    async def get_torrent_info(self, _: str) -> dict:
        return {"free": False}

    async def wedge_torrent(self, torrent_id: int) -> None:
        self.wedged.append(torrent_id)


class FakeQbit:
    added: list[bytes]

    def __init__(self) -> None:
        self.added = []

    async def torrents(self, *, hashes=()) -> list[dict]:
        return []

    async def add_torrent(self, torrent_file: bytes, **_: object) -> None:
        self.added.append(torrent_file)


class StringFlagMam(FakeMam):
    def __init__(self, flag: str) -> None:
        super().__init__()
        self.flag = flag

    async def get_torrent_info(self, _: str) -> dict:
        return {
            "free": self.flag,
            "personal_freeleech": "0",
            "fl_vip": "0",
            "vip": "0",
        }


def add_selected(repository: Repository, *, size: int = 12) -> None:
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
            "meta": {"mam_id": 42, "title": "Book", "size": size},
            "grabber": "test",
            "created_at": "2025-01-01T00:00:00Z",
            "started_at": None,
            "removed_at": None,
        }
    )


def test_download_job_moves_selected_record_into_library_catalog(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository)
    mam = FakeMam()
    qbit = FakeQbit()

    result = asyncio.run(
        grab_selected_torrents(Config(mam_id="cookie"), repository, mam, qbit)
    )

    assert result.downloaded == 1
    assert mam.requested == [("secret-hash", 42)]
    assert len(qbit.added) == 1
    assert repository.pending_selected() == []
    assert repository.selected_pipeline_status() == {
        "awaiting": 0,
        "downloading": 1,
        "downloading_bytes": 12,
    }
    assert repository.table_rows("torrents")[0]["mam_id"] == 42


def test_download_job_reports_unsatisfied_slot_deferral(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository)
    mam = FakeMam()

    async def no_slots() -> dict:
        user = await FakeMam.user_info(mam)
        user["unsat"] = {"limit": 1, "count": 1}
        return user

    mam.user_info = no_slots  # type: ignore[method-assign]

    result = asyncio.run(
        grab_selected_torrents(Config(mam_id="cookie"), repository, mam, FakeQbit())
    )

    assert result.downloaded == 0
    assert result.skipped == 1
    assert result.skip_reasons == {"unsat_slots": 1}
    assert result.available_slots == 0


def test_download_job_reports_ratio_reserve_deferral(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository, size=6_000)

    result = asyncio.run(
        grab_selected_torrents(
            Config(mam_id="cookie", min_ratio=2),
            repository,
            FakeMam(),
            FakeQbit(),
        )
    )

    assert result.downloaded == 0
    assert result.skipped == 1
    assert result.skip_reasons == {"ratio_buffer": 1}
    assert result.ratio_buffer_bytes == 5_000


def test_download_job_obeys_direct_slot_cap_before_requesting_file(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository)
    mam = FakeMam()

    async def at_cap() -> dict:
        user = await FakeMam.user_info(mam)
        user["unsat"] = {"limit": 150, "count": 140}
        return user

    mam.user_info = at_cap  # type: ignore[method-assign]
    result = asyncio.run(
        grab_selected_torrents(
            Config(mam_id="cookie", unsat_buffer=0, max_unsat_slots=140),
            repository,
            mam,
            FakeQbit(),
        )
    )

    assert result.downloaded == 0
    assert result.skipped == 1
    assert result.slot_cap == 140
    assert result.slots_used == 140
    assert mam.requested == []


def test_wedge_first_spends_only_above_reserve_and_bypasses_ratio(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository, size=6_000)
    mam = FakeMam()

    result = asyncio.run(
        grab_selected_torrents(
            Config(
                mam_id="cookie",
                min_ratio=2,
                unsat_buffer=0,
                prefer_wedges=True,
                wedge_buffer=1,
            ),
            repository,
            mam,
            FakeQbit(),
        )
    )

    assert result.downloaded == 1
    assert result.skipped == 0
    assert result.wedges_remaining == 1
    assert result.wedge_buffer == 1
    assert mam.wedged == [42]


def test_wedge_first_falls_back_to_ratio_at_reserve(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository)
    mam = FakeMam()

    async def at_reserve() -> dict:
        user = await FakeMam.user_info(mam)
        user["wedges"] = 1
        return user

    mam.user_info = at_reserve  # type: ignore[method-assign]
    result = asyncio.run(
        grab_selected_torrents(
            Config(
                mam_id="cookie",
                unsat_buffer=0,
                prefer_wedges=True,
                wedge_buffer=1,
            ),
            repository,
            mam,
            FakeQbit(),
        )
    )

    assert result.downloaded == 1
    assert result.wedges_remaining == 1
    assert mam.wedged == []


def test_wedge_first_treats_mam_string_zero_flags_as_false(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository)
    mam = StringFlagMam("0")

    result = asyncio.run(
        grab_selected_torrents(
            Config(
                mam_id="cookie",
                unsat_buffer=0,
                prefer_wedges=True,
                wedge_buffer=1,
            ),
            repository,
            mam,
            FakeQbit(),
        )
    )

    assert result.downloaded == 1
    assert result.wedges_remaining == 1
    assert mam.wedged == [42]
    activity = repository.recent_activity(component="downloader")
    assert any(
        entry["message"] == "Applied freeleech wedge to MaM #42" for entry in activity
    )


def test_wedge_first_does_not_spend_on_string_one_freeleech(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    add_selected(repository)
    mam = StringFlagMam("1")

    result = asyncio.run(
        grab_selected_torrents(
            Config(
                mam_id="cookie",
                unsat_buffer=0,
                prefer_wedges=True,
                wedge_buffer=1,
            ),
            repository,
            mam,
            FakeQbit(),
        )
    )

    assert result.downloaded == 1
    assert result.wedges_remaining == 2
    assert mam.wedged == []
