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
    service.settings.google_books_api_key = ""
    service.settings.openlibrary_enabled = False

    # Test scan_series
    scan_result = await service.scan_series(force=True)
    assert scan_result["total_series"] == 1
    assert scan_result["missing_count"] == 1
    assert scan_result["series"][0]["series_name"] == "Dune"
    assert len(scan_result["series"][0]["missing_volumes"]) == 1
    assert scan_result["series"][0]["missing_volumes"][0]["volume_number"] == "2"

    # Mock fetch_series_releases
    with patch(
        "mlm.modules.nextchapter.service.fetch_series_releases",
        new=AsyncMock(
            return_value=[
                {
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
            ]
        ),
    ):
        candidates = await service.search_mam_for_missing(
            "Dune", "2", "Dune Messiah"
        )
        assert len(candidates) == 1
        assert candidates[0]["mam_id"] == 202

        # Test queue download
        res = await service.queue_download(202, "Dune", "2")
        assert res["ok"] is True
        assert res["mam_id"] == 202

        # Test queue all missing
        qall = await service.queue_all_missing("Dune")
        assert qall["ok"] is True
        assert qall["queued_count"] == 1


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
    assert "/suite/nextchapter/settings?saved=1" in res_save.headers["location"]
