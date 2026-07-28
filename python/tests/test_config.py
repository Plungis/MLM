from __future__ import annotations

from pathlib import Path

from mlm.config import load_config


def test_loads_legacy_names_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
mam_id = "secret"
goodreads_interval = 90

[[qbittorrent]]
url = "http://localhost:8080"

[[autograb]]
type = "freeleech"
dry_run = true
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.import_interval == 90
    assert config.web_port == 3157
    assert config.qbittorrent[0].url == "http://localhost:8080"
    assert config.autograbs[0]["dry_run"] is True
