from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from mlm.database import ensure_database
from mlm.modules.mam_spender import MamSpenderService, extract_mam_id
from mlm.repository import Repository


class FakeMam:
    def __init__(self, points_after: int = 50_000) -> None:
        self.points_after = points_after
        self.requests: list[tuple[str, Any]] = []
        self.session_id = ""

    async def user_info(self) -> dict[str, Any]:
        return {
            "uid": "42",
            "username": "HeavyHarlow",
            "vip_until": "2030-01-01 00:00:00",
            "ratio": "12.0",
        }

    async def request_json(self, path: str, *, params: Any = None) -> Any:
        self.requests.append((path, params))
        if path == "/jsonLoad.php":
            return {"seedbonus": self.points_after}
        return {"success": True}

    def set_mam_id(self, value: str) -> None:
        self.session_id = value


def service(tmp_path: Path, mam: FakeMam) -> MamSpenderService:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    return MamSpenderService(Repository(database), mam)  # type: ignore[arg-type]


def test_session_id_import_accepts_common_exports() -> None:
    value = "a" * 32
    assert extract_mam_id(value) == value
    assert extract_mam_id(f"Cookie: foo=1; mam_id={value}; theme=dark") == value
    browser_export = json.dumps([{"name": "mam_id", "value": value}])
    assert extract_mam_id(browser_export) == value
    assert extract_mam_id(f".myanonamouse.net TRUE / TRUE 0 mam_id {value}") == value


def test_wedge_purchase_uses_bonus_store_and_verifies_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mam = FakeMam(points_after=50_000)
    spender = service(tmp_path, mam)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("mlm.modules.mam_spender.service.asyncio.sleep", no_sleep)
    after, purchased = asyncio.run(spender._maybe_buy_wedge("42", 100_000))

    assert purchased is True
    assert after == 50_000
    assert mam.requests[0] == (
        "/json/bonusBuy.php/",
        {
            "spendtype": "wedges",
            "source": "points",
            "_": mam.requests[0][1]["_"],
        },
    )
    assert spender.public_state()["spend_events"][0]["category"] == ("freeleech_wedge")


def test_wedge_purchase_respects_points_buffer(tmp_path: Path) -> None:
    mam = FakeMam()
    spender = service(tmp_path, mam)
    spender.settings.points_buffer = 25_000

    after, purchased = asyncio.run(spender._maybe_buy_wedge("42", 74_999))

    assert purchased is False
    assert after == 74_999
    assert mam.requests == []


def test_module_theme_is_saved_and_invalid_values_are_normalized(
    tmp_path: Path,
) -> None:
    mam = FakeMam()
    spender = service(tmp_path, mam)

    assert spender.update_settings({"theme": "mouse"})["settings"]["theme"] == ("mouse")
    assert spender.update_settings({"theme": "not-a-theme"})["settings"]["theme"] == (
        "ember"
    )


def test_wedge_success_is_not_lost_to_points_earned_during_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mam = FakeMam(points_after=50_002)
    spender = service(tmp_path, mam)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("mlm.modules.mam_spender.service.asyncio.sleep", no_sleep)
    after, purchased = asyncio.run(spender._maybe_buy_wedge("42", 100_000))

    assert purchased is True
    assert after == 50_002
    event = spender.public_state()["spend_events"][0]
    assert event["points_spent"] == 50_000


def test_legacy_web_config_import_preserves_module_state(tmp_path: Path) -> None:
    mam = FakeMam()
    spender = service(tmp_path, mam)
    state = spender.import_legacy(
        {
            "settings": {"fl_only": True, "points_buffer": 12_000},
            "totals": {
                "cumulative_upload_gb": 350,
                "cumulative_freeleech_wedges": 4,
            },
            "history": [{"created_at": "2026-01-01T00:00:00Z", "result": "Old run"}],
            "spend_events": [
                {
                    "created_at": "2026-01-01T00:00:00Z",
                    "category": "upload_credit",
                    "points_spent": 25_000,
                }
            ],
        }
    )

    assert state["settings"]["fl_only"] is True
    assert state["settings"]["buy_upload_credit"] is False
    assert state["totals"]["cumulative_upload_gb"] == 350
    assert state["history"][0]["result"] == "Old run"
    assert state["spend_events"][0]["points_spent"] == 25_000
