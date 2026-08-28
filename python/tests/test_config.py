from __future__ import annotations

from pathlib import Path

import pytest

from mlm.config import (
    ConfigError,
    load_config,
    save_config_text,
    save_root_config_values,
)
from mlm.request_auth import hash_request_password


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
    assert config.download_on_wedge_failure is False
    assert config.web_port == 3157
    assert config.qbittorrent[0].url == "http://localhost:8080"
    assert config.autograbs[0]["dry_run"] is True


def test_repository_example_config_is_valid() -> None:
    example = Path(__file__).parents[2] / "config.example.toml"
    config = load_config(example)

    assert config.autograbs[0]["type"] == "bookmarks"
    assert config.download_on_wedge_failure is False
    assert len(config.libraries) == 2


def test_canonical_interval_wins_when_legacy_name_is_also_present(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
mam_id = "secret"
goodreads_interval = 1
import_interval = 60
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.import_interval == 60


def test_beta_29_collection_setting_is_accepted_but_disabled(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'mam_id = "secret"\nsplit_collections = true\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert not hasattr(config, "split_collections")


def test_saves_editable_root_values_without_touching_nested_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
mam_id = "keep-this-secret"
min_ratio = 2 # preserve the rest of the document
goodreads_interval = 1

[[qbittorrent]]
url = "http://localhost:8080"
password = "also-keep-this"

[[autograb]]
type = "freeleech"
wedge_buffer = 99
""",
        encoding="utf-8",
    )

    config = save_root_config_values(
        path,
        {
            "min_ratio": 3.5,
            "max_unsat_slots": 140,
            "wedge_buffer": 4,
            "prefer_wedges": True,
            "download_on_wedge_failure": True,
            "import_interval": 45,
        },
    )

    text = path.read_text(encoding="utf-8")
    assert config.min_ratio == 3.5
    assert config.max_unsat_slots == 140
    assert config.wedge_buffer == 4
    assert config.prefer_wedges is True
    assert config.download_on_wedge_failure is True
    assert config.import_interval == 45
    assert 'mam_id = "keep-this-secret"' in text
    assert 'password = "also-keep-this"' in text
    assert "wedge_buffer = 99" in text
    assert "goodreads_interval" not in text
    assert "import_interval = 45" in text
    assert text.index("max_unsat_slots") < text.index("[[qbittorrent]]")


def test_full_config_editor_validates_and_atomically_replaces_every_section(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('mam_id = "old"\n', encoding="utf-8")
    replacement = r"""
mam_id = "new-secret"
prefer_wedges = true
download_on_wedge_failure = true
wedge_buffer = 100
audio_types = ["m4b"]

[[qbittorrent]]
url = "http://localhost:8090"
password = "new-password"

[[library]]
category = "Audiobooks"
library_dir = 'E:\MLM Audio'
method = "copy"
"""

    config = save_config_text(path, replacement)

    assert config.mam_id == "new-secret"
    assert config.prefer_wedges is True
    assert config.download_on_wedge_failure is True
    assert config.wedge_buffer == 100
    assert config.audio_types == ("m4b",)
    assert config.qbittorrent[0].password == "new-password"
    assert config.libraries[0]["method"] == "copy"

    with pytest.raises(ConfigError):
        save_config_text(path, 'mam_id = "broken"\nnot_a_setting = true\n')
    assert load_config(path).mam_id == "new-secret"


def test_request_portal_config_requires_clean_custom_domains(tmp_path: Path) -> None:
    password_hash = hash_request_password("correct horse")
    path = tmp_path / "config.toml"
    path.write_text(
        f"""
mam_id = "secret"
request_portal_enabled = true
request_portal_domains = ["requests.example.com"]
request_portal_title = "Family Requests"
request_portal_access_code = "shared-secret"
request_portal_username = "family"
request_portal_password_hash = "{password_hash}"
request_portal_rate_limit = 12
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.request_portal_enabled is True
    assert config.request_portal_require_account_login is True
    assert config.request_portal_domains == ("requests.example.com",)
    assert config.request_portal_title == "Family Requests"
    assert config.request_portal_username == "family"
    assert config.request_portal_password_hash == password_hash
    assert config.request_portal_rate_limit == 12

    path.write_text(
        'mam_id = "secret"\nrequest_portal_enabled = true\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="request_portal_domains"):
        load_config(path)

    path.write_text(
        'mam_id = "secret"\nrequest_portal_username = "family"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="request_portal_username"):
        load_config(path)


def test_request_portal_named_accounts_validate_permissions_and_round_trip(
    tmp_path: Path,
) -> None:
    admin_hash = hash_request_password("admin password")
    reader_hash = hash_request_password("reader password")
    path = tmp_path / "config.toml"
    path.write_text('mam_id = "secret"\n', encoding="utf-8")

    config = save_root_config_values(
        path,
        {
            "request_portal_users": (
                {
                    "username": "admin",
                    "password_hash": admin_hash,
                    "display_name": "Library Admin",
                    "permissions": ("auto_approve",),
                    "weekly_request_limit": 10,
                },
                {
                    "username": "reader",
                    "password_hash": reader_hash,
                    "display_name": "Reader",
                    "permissions": (),
                    "weekly_request_limit": 20,
                },
            )
        },
    )

    assert [user.username for user in config.request_portal_users] == [
        "admin",
        "reader",
    ]
    assert config.request_portal_users[0].permissions == ("auto_approve",)
    assert config.request_portal_users[1].permissions == ()
    assert config.request_portal_users[0].weekly_request_limit == 10
    assert config.request_portal_users[1].weekly_request_limit == 20
    text = path.read_text(encoding="utf-8")
    assert "request_portal_users = [" in text
    assert 'permissions = ["auto_approve"]' in text
    assert "weekly_request_limit = 10" in text
    assert "admin password" not in text

    duplicate = f'''
mam_id = "secret"
request_portal_users = [
  {{ username = "Reader", password_hash = "{reader_hash}" }},
  {{ username = "reader", password_hash = "{reader_hash}" }}
]
'''
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate request portal account"):
        load_config(path)

    unknown_permission = (
        'mam_id = "secret"\n'
        'request_portal_users = [{ username = "reader", '
        f'password_hash = "{reader_hash}", '
        'permissions = ["admin_everything"] }]\n'
    )
    path.write_text(unknown_permission, encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown request portal permissions"):
        load_config(path)


def test_enabled_modules_and_absidekick_auto_sync_defaults_and_save(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('mam_id = "secret"\n', encoding="utf-8")

    config = load_config(path)
    assert config.enabled_modules == ("heavymlm", "absidekick", "mam_spender")
    assert config.absidekick_auto_sync is True

    updated = save_root_config_values(
        path,
        {
            "enabled_modules": ("mam_spender",),
            "absidekick_auto_sync": False,
        },
    )
    assert updated.enabled_modules == ("mam_spender",)
    assert updated.absidekick_auto_sync is False

    reloaded = load_config(path)
    assert reloaded.enabled_modules == ("mam_spender",)
    assert reloaded.absidekick_auto_sync is False
