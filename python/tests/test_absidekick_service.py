from __future__ import annotations

import json
from pathlib import Path

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
