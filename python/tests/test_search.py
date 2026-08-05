from __future__ import annotations

from mlm.modules.heavymlm.search import (
    filter_search_results,
    present_search_result,
    search_seed,
)
from mlm.search import (
    build_search_query,
    matches_filter,
    metadata_matches,
    normalize_title,
    parse_size,
    torrent_meta,
)


def test_search_payload_matches_mam_shape() -> None:
    query = build_search_query(
        {
            "type": "bookmarks",
            "cost": "free",
            "query": "writer",
            "search_in": ["author"],
        }
    )
    assert query["dlLink"] is True
    assert "fields" not in query
    assert query["tor"]["searchIn"] == "bookmarks"
    assert query["tor"]["searchType"] == "fl-VIP"
    assert query["tor"]["srchIn"] == ["author"]


def test_filter_size_flags_dates_and_peers() -> None:
    row = {
        "size": "100 MiB",
        "browseflags": 1 << 5,
        "added": "2025-07-06 05:40:54",
        "seeders": 8,
        "owner_name": "someone",
        "language": 1,
    }
    assert matches_filter(
        row,
        {
            "min_size": "90 MiB",
            "max_size": "110 MiB",
            "flags": {"abridged": True, "explicit": False},
            "uploaded_after": "2025-07-06",
            "uploaded_before": "2025-07-06",
            "min_seeders": 8,
            "languages": ["English"],
        },
    )
    assert parse_size("1.5 GiB") == int(1.5 * (1 << 30))
    assert parse_size("1,018.3 KiB") == int(1018.3 * (1 << 10))


def test_normalize_title_matches_legacy_intent() -> None:
    assert normalize_title("The Café & Book: Vol. 2") == "cafe and book vol 2"


def test_metadata_matching_requires_author_and_narrator_compatibility() -> None:
    base = {
        "media_type": "Audiobook",
        "language": "English",
        "edition": None,
        "authors": ["Writer"],
        "narrators": ["Narrator"],
    }
    assert metadata_matches(base, {**base, "media_type": "audiobook"})
    assert not metadata_matches(base, {**base, "authors": ["Someone Else"]})
    assert not metadata_matches(base, {**base, "narrators": []})


def test_torrent_meta_decodes_mam_nested_json_fields() -> None:
    meta = torrent_meta(
        {
            "id": "123",
            "added": "2026-07-29 12:00:00",
            "author_info": '{"42":"Julian Barnes"}',
            "narrator_info": '{"7":"A Narrator &amp; Reader"}',
            "series_info": '{"9":["Example Series","2"]}',
            "categories": "[4, 6]",
            "browseflags": "0",
            "main_cat": "14",
            "category": "39",
            "mediatype": "2",
            "maincat": "2",
            "catname": "Ebook",
            "filetype": "epub",
            "language": "1",
            "numfiles": "1",
            "size": "1,018.3 KiB",
            "title": "Departure(s)",
            "vip": "0",
        }
    )

    assert meta["authors"] == ["Julian Barnes"]
    assert meta["narrators"] == ["A Narrator & Reader"]
    assert meta["series"] == [{"name": "Example Series", "entries": ["2"]}]
    assert meta["categories"] == [4, 6]


def test_heavymlm_search_card_decodes_metadata_and_availability() -> None:
    result = present_search_result(
        {
            "id": "123",
            "title": "Departure(s) &amp; Other Stories",
            "author_info": '{"42":"Julian Barnes"}',
            "narrator_info": '{"7":"A Narrator"}',
            "series_info": '{"9":["Example Series","2"]}',
            "filetype": "m4b mp3",
            "size": "1,018.3 KiB",
            "catname": "Audiobook",
            "language": "1",
            "numfiles": "2",
            "seeders": "9",
            "leechers": "1",
            "times_completed": "27",
            "owner_name": "Uploader",
            "added": "2026-08-05 12:00:00",
            "free": "1",
            "fl_vip": "1",
            "vip": "0",
        }
    )

    assert result["title"] == "Departure(s) & Other Stories"
    assert result["authors"] == ["Julian Barnes"]
    assert result["narrators"] == ["A Narrator"]
    assert result["series"] == [{"name": "Example Series", "entries": ["2"]}]
    assert result["filetypes"] == ["m4b", "mp3"]
    assert result["size"] == "1018.3 KiB"
    assert result["language"] == "English"
    assert result["availability"] == [
        {"label": "Global freeleech", "tone": "free"},
        {"label": "VIP freeleech", "tone": "vip"},
    ]


def test_heavymlm_search_card_falls_back_for_unknown_values() -> None:
    result = present_search_result(
        {"id": 1, "title": "Book", "size": "unknown", "free": "0"}
    )

    assert result["size"] == "unknown"
    assert result["availability"] == [{"label": "Ratio download", "tone": "ratio"}]


def test_heavymlm_manual_filters_use_and_policy() -> None:
    raw_rows = [
        {
            "id": 1,
            "title": "Dungeon Crawler Carl",
            "author_info": {"1": "Matt Dinniman"},
            "series_info": {"1": ["Dungeon Crawler Carl", "1"]},
            "filetype": "m4b",
            "catname": "Audiobook",
            "language": 1,
            "size": "500 MiB",
            "seeders": 10,
        },
        {
            "id": 2,
            "title": "Carl's Doomsday Scenario",
            "author_info": {"1": "Matt Dinniman"},
            "series_info": {"1": ["Dungeon Crawler Carl", "2"]},
            "filetype": "mp3",
            "catname": "Audiobook",
            "language": 1,
            "size": "600 MiB",
            "seeders": 12,
        },
    ]
    filters = {
        "author": "Matt Dinniman",
        "series": "Dungeon Crawler Carl",
        "filetype": "m4b",
        "category": "audiobook",
        "language": "English",
        "min_seeders": 5,
        "sort": "series",
    }

    seed, fields = search_seed(filters)
    results = filter_search_results(
        [present_search_result(row) for row in raw_rows], filters
    )

    assert seed == "Matt Dinniman"
    assert fields == ["author"]
    assert [row["id"] for row in results] == [1]
