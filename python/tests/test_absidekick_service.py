from __future__ import annotations

import json
from pathlib import Path

import mlm.modules.absidekick.service as absidekick_service
from mlm.modules.absidekick.service import ABSidekickService


def test_absidekick_settings_round_trip_without_exposing_token(
    tmp_path: Path,
) -> None:
    service = ABSidekickService(tmp_path / "absidekick")

    result = service.save(
        {
            "settings": {
                "connection": {
                    "baseUrl": "http://localhost:13378",
                    "libraryId": "audiobooks",
                    "provider": "audible",
                    "rememberConnection": True,
                },
                "matching": {"threshold": 91},
            },
            "token": "secret-token",
        }
    )

    assert result["settings"]["connection"]["hasToken"] is True
    assert "token" not in result["settings"]["connection"]
    assert result["settings"]["matching"]["threshold"] == 91
    saved = json.loads(service.settings_path.read_text(encoding="utf-8"))
    assert saved["connection"]["token"] == "secret-token"

    restored = ABSidekickService(tmp_path / "absidekick")
    assert restored.token == "secret-token"
    assert restored.public_state()["settings"]["matching"]["threshold"] == 91


def test_absidekick_does_not_persist_token_when_remember_is_disabled(
    tmp_path: Path,
) -> None:
    service = ABSidekickService(tmp_path / "absidekick")

    service.save(
        {
            "settings": {
                "connection": {
                    "baseUrl": "http://localhost:13378",
                    "rememberConnection": False,
                }
            },
            "token": "session-only",
        }
    )

    saved = json.loads(service.settings_path.read_text(encoding="utf-8"))
    assert "token" not in saved["connection"]
    assert service.public_state()["settings"]["connection"]["hasToken"] is True


def test_google_books_key_must_pass_live_test_and_is_never_returned(
    tmp_path: Path, monkeypatch
) -> None:
    service = ABSidekickService(tmp_path / "absidekick")

    saved_result = service.save(
        {
            "settings": {
                "providers": {
                    "googleBooksApiKeyValidated": True,
                    "googleBooksApiKeyFingerprint": "browser-cannot-enable-this",
                }
            },
            "googleBooksApiKey": "private-google-key",
        }
    )

    assert saved_result["settings"]["providers"]["hasGoogleBooksApiKey"] is True
    assert saved_result["settings"]["providers"]["googleBooksReady"] is False
    assert "googleBooksApiKey" not in saved_result["settings"]["providers"]
    assert "private-google-key" in service.settings_path.read_text(encoding="utf-8")

    monkeypatch.setattr(
        absidekick_service,
        "test_google_books_api_key",
        lambda api_key, timeout_seconds: {
            "valid": True,
            "sampleResults": 1,
            "message": f"Live test accepted {len(api_key)} key characters.",
        },
    )
    tested_result = service.test_google_books({})

    assert tested_result["valid"] is True
    assert tested_result["settings"]["providers"]["googleBooksReady"] is True
    assert "googleBooksApiKey" not in tested_result["settings"]["providers"]
    assert "googleBooksApiKeyFingerprint" not in tested_result["settings"]["providers"]

    cleared_result = service.clear_google_books()
    assert cleared_result["settings"]["providers"]["hasGoogleBooksApiKey"] is False
    assert cleared_result["settings"]["providers"]["googleBooksReady"] is False
    assert "private-google-key" not in service.settings_path.read_text(encoding="utf-8")


def test_absidekick_review_search_uses_normal_service_connection(
    tmp_path: Path, monkeypatch
) -> None:
    class SearchClient:
        def get(self, path, params=None):
            if path == "/api/items/item-1":
                return {
                    "id": "item-1",
                    "path": "/books/The Book",
                    "media": {
                        "duration": 3600,
                        "metadata": {"title": "The Book", "authorName": "An Author"},
                    },
                }
            if path == "/api/search/books":
                assert params["provider"] == "google"
                return [{"title": "The Book", "author": "An Author"}]
            raise AssertionError(path)

    service = ABSidekickService(tmp_path / "absidekick")
    monkeypatch.setattr(service, "client", lambda settings, token: SearchClient())

    result = service.search_review(
        {
            "itemId": "item-1",
            "query": {
                "title": "Book",
                "author": "",
                "provider": "google",
                "limit": 12,
            },
        }
    )

    assert result["ok"] is True
    assert result["resultCount"] == 1
    assert result["query"]["limit"] == 12
    assert result["candidates"][0]["candidate"]["title"] == "The Book"
    assert result["manualMatch"]["bestCandidate"]["candidate"]["title"] == "The Book"
    assert result["manualMatch"]["status"] == result["decision"]["action"]


def test_review_scan_publishes_live_activity_and_completion(
    tmp_path: Path, monkeypatch
) -> None:
    service = ABSidekickService(tmp_path / "absidekick")
    observed_running = []

    def fake_scan(_client, _settings, limit, excluded_ids, progress):
        assert limit == 3
        assert excluded_ids == set()
        progress(
            {
                "phase": "searching",
                "detail": "Searching providers for The Book",
                "current": 0,
                "total": 1,
                "currentTitle": "The Book",
            }
        )
        observed_running.append(service.activity_snapshot()["activity"])
        progress(
            {
                "phase": "searching",
                "detail": "Finished The Book",
                "current": 1,
                "total": 1,
                "currentTitle": "The Book",
            }
        )
        return {"totalReviewItems": 1, "rows": [{"item": {"id": "item-1"}}]}

    monkeypatch.setattr(service, "client", lambda settings, token: object())
    monkeypatch.setattr(absidekick_service, "scan_review_items", fake_scan)

    result = service.scan_review({"limit": 3})
    activity = service.public_state()["activity"]

    assert result["ok"] is True
    assert observed_running[0]["status"] == "running"
    assert observed_running[0]["currentTitle"] == "The Book"
    assert activity["status"] == "success"
    assert activity["current"] == 1
    assert activity["total"] == 1
    assert "loaded 1 item" in activity["detail"]
