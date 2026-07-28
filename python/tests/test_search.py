from __future__ import annotations

from mlm.search import build_search_query, matches_filter, normalize_title, parse_size


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


def test_normalize_title_matches_legacy_intent() -> None:
    assert normalize_title("The Café & Book: Vol. 2") == "cafe and book vol 2"
