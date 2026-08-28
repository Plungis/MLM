from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mlm.config import Config
from mlm.database import ensure_database
from mlm.mam import MamClient
from mlm.modules.heavymlm.series import (
    extract_series_volume,
    group_series_books,
    parse_entry_number,
    resolve_series_selection,
    score_series_candidate,
)
from mlm.repository import Repository


def test_parse_entry_number():
    assert parse_entry_number("1") == (0, 1.0, "1")
    assert parse_entry_number("2.5") == (0, 2.5, "2.5")
    assert parse_entry_number("#3") == (0, 3.0, "#3")
    assert parse_entry_number("Book 4") == (0, 4.0, "book 4")
    assert parse_entry_number("Volume 10") == (0, 10.0, "volume 10")
    assert parse_entry_number("Special Prequel") == (1, 999999.0, "special prequel")

    entries = ["10", "1", "2.5", "Book 2", "Prequel"]
    sorted_entries = sorted(entries, key=parse_entry_number)
    assert sorted_entries == ["1", "Book 2", "2.5", "10", "Prequel"]


def test_extract_series_volume():
    row_with_series = {
        "title": "The Eye of the Bedlam Bride",
        "series": [{"name": "Dungeon Crawler Carl", "entries": ["5"]}],
    }
    assert extract_series_volume(row_with_series, "Dungeon Crawler Carl") == "5"

    row_with_title_only = {
        "title": "Dungeon Crawler Carl Book 3",
        "series": [],
    }
    assert extract_series_volume(row_with_title_only, "Dungeon Crawler Carl") == "3"


def test_score_series_candidate():
    config = Config(
        mam_id="secret",
        audio_types=("m4b", "mp3"),
        ebook_types=("epub", "pdf"),
    )

    # M4B Freeleech with 20 seeders vs MP3 Ratio with 5 seeders
    m4b_free = {
        "media_type": "audiobook",
        "filetypes": ["m4b"],
        "free": 1,
        "seeders": 20,
    }
    mp3_ratio = {
        "media_type": "audiobook",
        "filetypes": ["mp3"],
        "free": 0,
        "seeders": 5,
    }

    score_m4b = score_series_candidate(m4b_free, config)
    score_mp3 = score_series_candidate(mp3_ratio, config)
    assert score_m4b > score_mp3


def test_group_series_books():
    config = Config(mam_id="secret", audio_types=("m4b", "mp3"))

    raw_releases = [
        {
            "id": 101,
            "title": "Dungeon Crawler Carl",
            "filetype": "mp3",
            "catname": "Audiobooks",
            "mediatype": 1,
            "main_cat": 13,
            "series_info": json.dumps({"1": ["Dungeon Crawler Carl", "1"]}),
            "author_info": json.dumps({"1": "Matt Dinniman"}),
            "seeders": 10,
            "free": 0,
        },
        {
            "id": 102,
            "title": "Dungeon Crawler Carl (M4B)",
            "filetype": "m4b",
            "catname": "Audiobooks",
            "mediatype": 1,
            "main_cat": 13,
            "series_info": json.dumps({"1": ["Dungeon Crawler Carl", "1"]}),
            "author_info": json.dumps({"1": "Matt Dinniman"}),
            "seeders": 25,
            "free": 1,
        },
        {
            "id": 201,
            "title": "Carl's Doomsday Scenario",
            "filetype": "m4b",
            "catname": "Audiobooks",
            "mediatype": 1,
            "main_cat": 13,
            "series_info": json.dumps({"1": ["Dungeon Crawler Carl", "2"]}),
            "author_info": json.dumps({"1": "Matt Dinniman"}),
            "seeders": 15,
            "free": 0,
        },
        {
            "id": 301,
            "title": "The Dungeon Anarchist's Cookbook",
            "filetype": "m4b",
            "catname": "Audiobooks",
            "mediatype": 1,
            "main_cat": 13,
            "series_info": json.dumps({"1": ["Dungeon Crawler Carl", "3"]}),
            "author_info": json.dumps({"1": "Matt Dinniman"}),
            "seeders": 30,
            "free": 1,
        },
    ]

    books = group_series_books(raw_releases, "Dungeon Crawler Carl", config)
    assert len(books) == 3
    assert [b["volume"] for b in books] == ["1", "2", "3"]

    # Book 1 should pick M4B freeleech (ID 102) over MP3 release (ID 101)
    assert books[0]["mam_id"] == 102
    assert books[0]["candidates_count"] == 2
    assert books[1]["mam_id"] == 201
    assert books[2]["mam_id"] == 301


def test_resolve_series_selection(tmp_path: Path):
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repo = Repository(database)

    config = Config(mam_id="secret", audio_types=("m4b", "mp3"))

    raw_releases = [
        {
            "id": 101,
            "title": "Dungeon Crawler Carl",
            "filetype": "m4b",
            "catname": "Audiobooks",
            "mediatype": 1,
            "main_cat": 13,
            "series_info": json.dumps({"1": ["Dungeon Crawler Carl", "1"]}),
            "author_info": json.dumps({"1": "Matt Dinniman"}),
            "seeders": 20,
            "free": 1,
        },
        {
            "id": 201,
            "title": "Carl's Doomsday Scenario",
            "filetype": "m4b",
            "catname": "Audiobooks",
            "mediatype": 1,
            "main_cat": 13,
            "series_info": json.dumps({"1": ["Dungeon Crawler Carl", "2"]}),
            "author_info": json.dumps({"1": "Matt Dinniman"}),
            "seeders": 15,
            "free": 0,
        },
    ]

    # Pre-populate Book 1 as already in library
    repo.add_selected(
        {
            "mam_id": 101,
            "title_search": "dungeon crawler carl",
            "meta": {
                "title": "Dungeon Crawler Carl",
                "media_type": "audiobook",
                "authors": ["Matt Dinniman"],
                "series": [{"name": "Dungeon Crawler Carl", "entries": ["1"]}],
            },
            "started_at": "2025-01-01T00:00:00Z",
            "created_at": "2025-01-01T00:00:00Z",
        }
    )

    class MockMam(MamClient):
        def __init__(self):
            pass

        async def search(self, query: dict):
            return {"data": raw_releases, "found": len(raw_releases)}

    mam = MockMam()

    resolution = asyncio.run(
        resolve_series_selection(config, repo, mam, "Dungeon Crawler Carl")
    )

    assert resolution["series_name"] == "Dungeon Crawler Carl"
    assert resolution["total_books"] == 2
    assert len(resolution["already_present"]) == 1
    assert resolution["already_present"][0]["mam_id"] == 101
    assert len(resolution["missing_books"]) == 1
    assert resolution["missing_books"][0]["mam_id"] == 201
