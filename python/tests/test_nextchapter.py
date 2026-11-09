from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mlm.config import load_config
from mlm.database import ensure_database
from mlm.modules.nextchapter.clients import (
    GeminiClient,
    GoogleBooksClient,
    OpenLibraryClient,
)
from mlm.modules.nextchapter.models import (
    BookRecommendation,
    MissingBook,
    NextChapterSettings,
    SeriesHubEntry,
)
from mlm.modules.nextchapter.service import NextChapterService
from mlm.repository import Repository
from mlm.web import create_app


def test_models():
    missing = MissingBook(
        series_name="Dune",
        volume_number="2",
        title="Dune Messiah",
        author="Frank Herbert",
        summary="Sequel to Dune",
        cover_url="https://example.com/cover.jpg",
        published_date="1969",
        source="google_books",
    )
    d = missing.to_dict()
    assert d["series_name"] == "Dune"
    assert d["volume_number"] == "2"
    assert d["title"] == "Dune Messiah"

    rec = BookRecommendation(
        title="Children of Time",
        author="Adrian Tchaikovsky",
        series_name="Children of Time",
        volume="1",
        reason="Because you loved sci-fi epics",
        summary="Spiders in space",
        cover_url="https://example.com/cot.jpg",
        source="gemini_ai",
    )
    rd = rec.to_dict()
    assert rd["title"] == "Children of Time"
    assert rd["source"] == "gemini_ai"

    hub = SeriesHubEntry(
        series_name="Dune",
        authors=["Frank Herbert"],
        owned_count=1,
        total_known_count=2,
        owned_volumes=[{"title": "Dune", "volume": "1"}],
        missing_volumes=[missing],
        percent_complete=50,
        status="incomplete",
    )
    hd = hub.to_dict()
    assert hd["percent_complete"] == 50
    assert len(hd["missing_volumes"]) == 1

    settings = NextChapterSettings(
        gemini_api_key="test_key",
        google_books_api_key="gb_key",
        openlibrary_enabled=True,
        preferred_media="audiobook",
    )
    sd = settings.to_dict()
    assert sd["gemini_api_key"] == "test_key"
    assert sd["preferred_media"] == "audiobook"


@pytest.mark.asyncio
async def test_google_books_client():
    mock_http = AsyncMock()
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {
        "items": [
            {
                "volumeInfo": {
                    "title": "Dune Messiah: Book 2",
                    "authors": ["Frank Herbert"],
                    "publishedDate": "1969",
                    "description": "Epic sequel",
                    "imageLinks": {"thumbnail": "http://example.com/thumb.jpg"},
                }
            }
        ]
    }
    mock_http.get.return_value = mock_res

    client = GoogleBooksClient(api_key="dummy_key", client=mock_http)
    results = await client.search_series_books("Dune", "Frank Herbert")
    assert len(results) == 1
    assert "Dune Messiah" in results[0]["title"]
    assert results[0]["volume"] == "2"
    assert results[0]["cover_url"] == "https://example.com/thumb.jpg"


@pytest.mark.asyncio
async def test_openlibrary_client():
    mock_http = AsyncMock()
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {
        "docs": [
            {
                "title": "Children of Dune #3",
                "author_name": ["Frank Herbert"],
                "cover_i": 12345,
                "first_publish_year": 1976,
            }
        ]
    }
    mock_http.get.return_value = mock_res

    client = OpenLibraryClient(client=mock_http)
    results = await client.search_series("Dune", "Frank Herbert")
    assert len(results) == 1
    assert results[0]["volume"] == "3"
    assert "12345-M.jpg" in results[0]["cover_url"]


@pytest.mark.asyncio
async def test_gemini_client():
    mock_http = AsyncMock()
    mock_res = MagicMock()
    mock_res.status_code = 200
    gemini_json_content = json.dumps(
        {
            "missing_books": [
                {
                    "series_name": "Dune",
                    "volume_number": "2",
                    "title": "Dune Messiah",
                    "author": "Frank Herbert",
                    "summary": "The continuation of Paul Atreides' story.",
                }
            ],
            "recommendations": [
                {
                    "title": "Hyperion",
                    "author": "Dan Simmons",
                    "series_name": "Hyperion Cantos",
                    "volume": "1",
                    "reason": "Rich space opera worldbuilding like Dune.",
                    "summary": "Seven pilgrims travel to Time Tombs.",
                }
            ],
        }
    )
    mock_res.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": gemini_json_content}]}}
        ]
    }
    mock_http.post.return_value = mock_res

    client = GeminiClient(api_key="gemini_dummy", client=mock_http)
    analysis = await client.analyze_and_recommend(
        [
            {
                "series_name": "Dune",
                "authors": ["Frank Herbert"],
                "volumes": ["1"],
                "gaps": ["#2"],
            }
        ],
        ["Frank Herbert"],
    )
    assert len(analysis["missing_books"]) == 1
    assert analysis["missing_books"][0]["title"] == "Dune Messiah"
    assert len(analysis["recommendations"]) == 1
    assert analysis["recommendations"][0]["title"] == "Hyperion"


@pytest.mark.asyncio
async def test_nextchapter_service(tmp_path):
    db_path = tmp_path / "data.sqlite3"
    ensure_database(db_path)
    repo = Repository(db_path)

    # Insert ABS books for Dune books 1 and 3 (gap at #2)
    repo.upsert_abs_books(
        [
            {
                "id": "abs-item-1",
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "series": [{"name": "Dune", "sequence": "1"}],
            },
            {
                "id": "abs-item-3",
                "title": "Children of Dune",
                "authors": ["Frank Herbert"],
                "series": [{"name": "Dune", "sequence": "3"}],
            },
        ]
    )

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('mam_id = "test_mam_id"\n', encoding="utf-8")
    cfg = load_config(cfg_file)
    mam_mock = AsyncMock()
    mam_mock.get_torrent_info_by_id.return_value = {
        "id": 202,
        "title": "Dune Messiah [Audiobook]",
        "authors": ["Frank Herbert"],
        "size": "500 MB",
        "filetypes": ["m4b"],
        "media_type": "audiobook",
        "seeders": 15,
        "free": True,
        "vip": False,
        "personal_freeleech": False,
    }

    service = NextChapterService(cfg, repo, mam_mock)
    service.settings.gemini_api_key = "dummy_gemini"

    # Test scan_series with Gemini AI series analysis
    with patch(
        "mlm.modules.nextchapter.service.GeminiClient.analyze_series",
        new=AsyncMock(
            return_value={
                "series": [
                    {
                        "series_name": "Dune",
                        "canonical_total_books": 3,
                        "status": "incomplete",
                        "missing_books": [
                            {
                                "volume_number": "2",
                                "title": "Dune Messiah",
                                "author": "Frank Herbert",
                                "summary": "Paul Atreides faces galactic intrigue.",
                            }
                        ],
                    }
                ],
                "recommendations": [],
            }
        ),
    ):
        scan_result = await service.scan_series(force=True)
        assert scan_result["total_series"] == 1
        assert scan_result["missing_count"] == 1
        assert scan_result["series"][0]["series_name"] == "Dune"
        assert len(scan_result["series"][0]["missing_volumes"]) == 1
        assert scan_result["series"][0]["missing_volumes"][0]["volume_number"] == "2"
        assert scan_result["series"][0]["missing_volumes"][0]["title"] == "Dune Messiah"

    # Mock fetch_series_releases
    with patch(
        "mlm.modules.nextchapter.service.fetch_series_releases",
        new=AsyncMock(
            return_value=[
                {
                    "id": 202,
                    "title": "Dune Messiah [Audiobook]",
                    "authors": ["Frank Herbert"],
                    "series": [{"name": "Dune Chronicles", "entries": ["2"]}],
                    "size": "500 MB",
                    "filetypes": ["m4b"],
                    "media_type": "audiobook",
                    "seeders": 15,
                    "free": True,
                    "vip": False,
                    "personal_freeleech": False,
                }
            ]
        ),
    ):
        candidates = await service.search_mam_for_missing(
            "Dune", "2", "Dune Messiah"
        )
        assert len(candidates) == 1
        assert candidates[0]["mam_id"] == 202
        assert candidates[0]["series_display"] == "Dune Chronicles #2"

        # Test queue download
        res = await service.queue_download(202, "Dune", "2")
        assert res["ok"] is True
        assert res["mam_id"] == 202

        # Test queue all missing
        qall = await service.queue_all_missing("Dune")
        assert qall["ok"] is True
        assert qall["queued_count"] == 1


def test_series_normalization_and_mam_display():
    from mlm.modules.heavymlm.series import normalize_series_name
    from mlm.modules.nextchapter.service import format_mam_series

    assert (
        normalize_series_name("Stormlight Archive, The (Audiobook)")
        == "The Stormlight Archive"
    )
    assert (
        normalize_series_name("Dungeon Crawler Carl Series")
        == "Dungeon Crawler Carl"
    )
    assert (
        normalize_series_name("Wheel of Time, The [Box Set]")
        == "The Wheel of Time"
    )

    display = format_mam_series(
        [
            {"name": "Dungeon Crawler Carl", "entries": ["4"]},
            {"name": "LitRPG Favorites", "entries": []},
        ]
    )
    assert display == "Dungeon Crawler Carl #4; LitRPG Favorites"


def test_nextchapter_web_routes(tmp_path):
    db_path = tmp_path / "data.sqlite3"
    ensure_database(db_path)
    repo = Repository(db_path)

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('mam_id = "test_mam_id"\n', encoding="utf-8")
    cfg = load_config(cfg_path)

    mam_mock = AsyncMock()
    app = create_app(cfg_path, db_path)

    nc_service = NextChapterService(cfg, repo, mam_mock)
    services_mock = MagicMock()
    services_mock.config = cfg
    services_mock.nextchapter = nc_service
    services_mock.reconfigure = AsyncMock()
    app.state.services = services_mock

    client = TestClient(app)

    # Test pages
    res_series = client.get("/suite/nextchapter/series")
    assert res_series.status_code == 200
    assert "NextChapter Hub" in res_series.text
    assert "Series Hub" in res_series.text

    res_missing = client.get("/suite/nextchapter/missing")
    assert res_missing.status_code == 200
    assert "Missing Volumes" in res_missing.text

    res_recs = client.get("/suite/nextchapter/recommendations")
    assert res_recs.status_code == 200
    assert "Recommendations" in res_recs.text

    res_settings = client.get("/suite/nextchapter/settings")
    assert res_settings.status_code == 200
    assert "NextChapter Settings" in res_settings.text

    # Test API state
    res_state = client.get("/api/nextchapter/state")
    assert res_state.status_code == 200
    data = res_state.json()
    assert "state" in data
    assert "settings" in data

    # Test API settings save via Form
    res_save = client.post(
        "/api/nextchapter/settings",
        data={
            "gemini_api_key": "new_gemini_key",
            "google_books_api_key": "new_gb_key",
            "preferred_media": "audiobook",
        },
        follow_redirects=False,
    )
    assert res_save.status_code == 303
    assert "/suite/nextchapter/series?saved=1" in res_save.headers["location"]



@pytest.mark.asyncio
async def test_search_mam_for_missing_prioritizes_author_and_title(tmp_path):
    db_path = tmp_path / "data.sqlite3"
    ensure_database(db_path)
    repo = Repository(db_path)

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('mam_id = "test_mam_id"\n', encoding="utf-8")
    cfg = load_config(cfg_file)

    mam_mock = AsyncMock()
    mam_mock.search.return_value = {
        "found": 2,
        "data": [
            {
                "id": 901,
                "title": "End of Watch [Audiobook]",
                "authors": ["Stephen King"],
                "size": "600 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 100,
                "free": True,
            },
            {
                "id": 902,
                "title": "End of Story",
                "authors": ["A. J. Finn"],
                "size": "500 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 10,
                "free": True,
            },
        ],
    }

    service = NextChapterService(cfg, repo, mam_mock)
    candidates = await service.search_mam_for_missing(
        series_name="",
        volume="2",
        title="End of Story",
        author="aJ finn",
    )

    # Exactly the A. J. Finn release should be top candidate, Stephen King filtered out
    assert len(candidates) == 1
    assert candidates[0]["mam_id"] == 902
    assert candidates[0]["title"] == "End of Story"
    assert candidates[0]["authors"] == ["A. J. Finn"]
    assert "✓ Author Match" in candidates[0]["match_reasons"]

    # Verify MAM was queried with explicit srchIn fields and stripped initial 'aJ'
    queried_calls = [c.args[0]["tor"] for c in mam_mock.search.call_args_list]
    assert any("srchIn" in tor and tor["srchIn"] for tor in queried_calls)
    assert all("aJ" not in tor.get("text", "") for tor in queried_calls)


@pytest.mark.asyncio
async def test_search_mam_series_and_author_modes(tmp_path):
    db_path = tmp_path / "data.sqlite3"
    ensure_database(db_path)
    repo = Repository(db_path)
    repo.upsert_abs_books(
        [
            {
                "id": "abs-dcc-1",
                "title": "Dungeon Crawler Carl",
                "authors": ["Matt Dinniman"],
                "series": [{"name": "Dungeon Crawler Carl", "sequence": "1"}],
            }
        ]
    )

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('mam_id = "test_mam_id"\n', encoding="utf-8")
    cfg = load_config(cfg_file)

    mam_mock = AsyncMock()
    mam_mock.search.return_value = {
        "found": 3,
        "data": [
            {
                "id": 503,
                "title": "The Dungeon Anarchist's Cookbook",
                "authors": ["Matt Dinniman"],
                "series": [{"name": "Dungeon Crawler Carl", "entries": ["3"]}],
                "size": "700 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 30,
                "free": True,
            },
            {
                "id": 501,
                "title": "Dungeon Crawler Carl",
                "authors": ["Matt Dinniman"],
                "series": [{"name": "Dungeon Crawler Carl", "entries": ["1"]}],
                "size": "600 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 50,
                "free": True,
            },
            {
                "id": 502,
                "title": "Carl's Doomsday Scenario",
                "authors": ["Matt Dinniman"],
                "series": [{"name": "Dungeon Crawler Carl", "entries": ["2"]}],
                "size": "650 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 40,
                "free": True,
            },
        ],
    }

    service = NextChapterService(cfg, repo, mam_mock)
    series_cands = await service.search_mam_for_missing(
        series_name="Dungeon Crawler Carl",
        author="Matt Dinniman",
        mode="series",
    )
    assert [c["mam_id"] for c in series_cands] == [501, 502, 503]
    assert series_cands[0]["in_library"] is True
    assert series_cands[1]["in_library"] is False

    author_cands = await service.search_mam_for_missing(
        series_name="Dungeon Crawler Carl",
        author="Matt Dinniman",
        mode="author",
    )
    assert len(author_cands) == 3
    assert [c["mam_id"] for c in author_cands] == [501, 502, 503]


@pytest.mark.asyncio
async def test_scan_series_grounds_titles_with_mam_and_rejects_hallucinations(
    tmp_path,
):
    db_path = tmp_path / "data.sqlite3"
    ensure_database(db_path)
    repo = Repository(db_path)
    repo.upsert_abs_books(
        [
            {
                "id": "abs-apgts-1",
                "title": "A Conjuring of Ravens",
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "sequence": "1"}
                ],
            },
            {
                "id": "abs-apgts-2",
                "title": "A Binding of Blood",
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "sequence": "2"}
                ],
            },
        ]
    )

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('mam_id = "test_mam_id"\n', encoding="utf-8")
    cfg = load_config(cfg_file)

    mam_mock = AsyncMock()
    mam_mock.search.return_value = {
        "found": 5,
        "data": [
            {
                "id": 701,
                "title": (
                    "A Practical Guide to Sorcery, Book 1: "
                    "A Conjuring of Ravens [Unabridged]"
                ),
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["1"]}
                ],
                "size": "500 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 25,
                "free": True,
            },
            {
                "id": 702,
                "title": (
                    "A Practical Guide to Sorcery, Book 2: "
                    "A Binding of Blood [Unabridged]"
                ),
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["2"]}
                ],
                "size": "520 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 22,
                "free": True,
            },
            {
                "id": 703,
                "title": (
                    "A Practical Guide to Sorcery, Book 3: "
                    "A Sacrifice of Light [Unabridged]"
                ),
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["3"]}
                ],
                "size": "550 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 30,
                "free": True,
            },
            {
                "id": 704,
                "title": "A Foreboding of Woe (A Practical Guide to Sorcery, Book 4)",
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["4"]}
                ],
                "size": "580 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 28,
                "free": True,
            },
            {
                "id": 705,
                "title": (
                    "A Cauldron of Bitterness: "
                    "A Practical Guide to Sorcery, Book 5"
                ),
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["5"]}
                ],
                "size": "600 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 35,
                "free": True,
            },
        ],
    }

    service = NextChapterService(cfg, repo, mam_mock)
    service.settings.gemini_api_key = "dummy_gemini"

    # Simulate Gemini returning a hallucinated title ("A Schism of Souls")
    with patch(
        "mlm.modules.nextchapter.service.GeminiClient.analyze_series",
        new=AsyncMock(
            return_value={
                "series": [
                    {
                        "series_name": "A Practical Guide to Sorcery",
                        "canonical_total_books": 6,
                        "status": "ongoing",
                        "missing_books": [
                            {
                                "volume_number": "3",
                                "title": "A Schism of Souls",
                                "author": "Azalea Ellis",
                                "summary": "Hallucinated book title for #3.",
                            },
                            {
                                "volume_number": "6",
                                "title": "A Schism of Souls",
                                "author": "Azalea Ellis",
                                "summary": "Hallucinated book title for #6.",
                            },
                        ],
                    }
                ],
                "recommendations": [],
            }
        ),
    ):
        scan_res = await service.scan_series(force=True)
        series_list = scan_res["series"]
        assert len(series_list) == 1
        missing = series_list[0]["missing_volumes"]
        assert [m["volume_number"] for m in missing] == ["3", "4", "5"]
        assert [m["title"] for m in missing] == [
            "A Sacrifice of Light",
            "A Foreboding of Woe",
            "A Cauldron of Bitterness",
        ]
        assert all("Schism of Souls" not in m["title"] for m in missing)


@pytest.mark.asyncio
async def test_dispositions_revisit_and_qbit_queue_tracking(
    tmp_path,
) -> None:
    db_path = tmp_path / "test_disp_qbit.db"
    ensure_database(db_path)
    repo = Repository(db_path)

    repo.upsert_abs_books(
        [
            {
                "id": "abs-s1",
                "title": "A Conjuring of Ravens",
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "sequence": "1"}
                ],
                "library_path": "/books/Sorcery 1",
            },
            {
                "id": "abs-s2",
                "title": "A Binding of Blood",
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "sequence": "2"}
                ],
                "library_path": "/books/Sorcery 2",
            },
        ]
    )

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'mam_id = "test_mam_id"\n'
        "[[qbittorrent]]\n"
        'url = "http://127.0.0.1:8080"\n'
        'username = "admin"\n'
        'password = "secret"\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)

    mam_mock = AsyncMock()
    mam_mock.search.return_value = {
        "found": 4,
        "data": [
            {
                "id": 701,
                "title": (
                    "A Practical Guide to Sorcery, Book 1: "
                    "A Conjuring of Ravens"
                ),
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["1"]}
                ],
                "size": "500 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 25,
                "free": True,
            },
            {
                "id": 702,
                "title": (
                    "A Practical Guide to Sorcery, Book 2: A Binding of Blood"
                ),
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["2"]}
                ],
                "size": "520 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 22,
                "free": True,
            },
            {
                "id": 703,
                "title": (
                    "A Practical Guide to Sorcery, Book 3: "
                    "A Sacrifice of Light"
                ),
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["3"]}
                ],
                "size": "550 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 30,
                "free": True,
            },
            {
                "id": 704,
                "title": "A Foreboding of Woe (A Practical Guide to Sorcery #4)",
                "authors": ["Azalea Ellis"],
                "series": [
                    {"name": "A Practical Guide to Sorcery", "entries": ["4"]}
                ],
                "size": "580 MB",
                "filetypes": ["m4b"],
                "media_type": "audiobook",
                "seeders": 28,
                "free": True,
            },
        ],
    }
    mam_mock.get_torrent_info_by_id.return_value = {
        "id": 703,
        "title": "A Practical Guide to Sorcery, Book 3: A Sacrifice of Light",
        "dl": "dl_link_703",
        "vip": 0,
        "free": 1,
        "personal_freeleech": 0,
        "fl_vip": 0,
        "size": "550 MB",
        "numfiles": 1,
        "filetype": "m4b",
        "category": 13,
        "main_cat": 13,
        "author_info": '{"1": "Azalea Ellis"}',
        "narrator_info": "{}",
        "series_info": '{"1": ["A Practical Guide to Sorcery", "3", 3.0]}',
    }

    service = NextChapterService(cfg, repo, mam_mock)
    await service.scan_series(force=True)

    # Initially Books #3 and #4 are missing
    state = service.get_cached_state()
    assert state["missing_count"] == 2

    # 1. Mark Book #4 as "not_wanted" and Book #3 as "unable_to_find"
    res_nw = service.set_volume_disposition(
        series_name="A Practical Guide to Sorcery",
        volume_number="4",
        title="A Foreboding of Woe",
        status="not_wanted",
        reason="Already read / Own elsewhere",
        notes="Read on Kindle",
    )
    assert res_nw["ok"] is True
    assert res_nw["counts"]["not_wanted_count"] == 1
    assert res_nw["counts"]["missing_count"] == 1

    res_utf = service.set_volume_disposition(
        series_name="A Practical Guide to Sorcery",
        volume_number="3",
        title="A Sacrifice of Light",
        status="unable_to_find",
        reason="Wrong format",
        notes="Waiting for M4B",
    )
    assert res_utf["ok"] is True
    assert res_utf["counts"]["unable_to_find_count"] == 1
    assert res_utf["counts"]["missing_count"] == 0

    # Verify Re-Check on MAM finds Book #3
    recheck_res = await service.recheck_unable_to_find()
    assert recheck_res["ok"] is True
    assert recheck_res["checked_count"] == 1
    assert recheck_res["found_count"] == 1
    assert recheck_res["items"][0]["mam_available_count"] >= 1
    assert recheck_res["items"][0]["best_candidate"]["mam_id"] == 703

    # 2. Queue Book #3 -> should auto-clear unable_to_find and mark it queued
    q_res = await service.queue_download(
        703,
        "A Practical Guide to Sorcery",
        "3",
        title="A Sacrifice of Light",
    )
    assert q_res["ok"] is True
    assert q_res["counts"]["unable_to_find_count"] == 0
    assert q_res["counts"]["queued_count"] == 1

    # 3. Simulate qBittorrent live tracking
    with (
        patch("mlm.modules.nextchapter.service.QbitClient.login", new=AsyncMock()),
        patch(
            "mlm.modules.nextchapter.service.QbitClient.torrents",
            new=AsyncMock(
                return_value=[
                    {
                        "hash": "abc123hash",
                        "name": (
                            "A Practical Guide to Sorcery, Book 3: "
                            "A Sacrifice of Light"
                        ),
                        "state": "downloading",
                        "progress": 0.642,
                        "dlspeed": 3145728,
                        "content_path": (
                            "/downloads/Azalea Ellis - A Sacrifice of Light.m4b"
                        ),
                    }
                ]
            ),
        ),
        patch(
            "mlm.modules.nextchapter.service.QbitClient.files",
            new=AsyncMock(
                return_value=[
                    {"name": "Azalea Ellis - A Sacrifice of Light.m4b"}
                ]
            ),
        ),
    ):
        live_q = await service.get_live_queue_status()
        assert live_q["ok"] is True
        q_vols = live_q["queued_volumes"]
        assert len(q_vols) == 1
        assert q_vols[0]["qbit_state"] == "downloading"
        assert q_vols[0]["qbit_progress"] == 64.2
        assert (
            "Azalea Ellis - A Sacrifice of Light.m4b" in q_vols[0]["qbit_file"]
        )
        assert q_vols[0]["qbit_dlspeed"] == "3.0 MB/s"

    # 4. Restore Book #4 back to missing
    rest_res = service.restore_volume_disposition(
        series_name="A Practical Guide to Sorcery",
        volume_number="4",
    )
    assert rest_res["ok"] is True
    assert rest_res["counts"]["not_wanted_count"] == 0
    assert rest_res["counts"]["missing_count"] == 1



