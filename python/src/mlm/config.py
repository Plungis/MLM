from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class QbitConfig:
    url: str
    username: str = ""
    password: str = ""
    on_cleaned: dict[str, Any] | None = None
    on_invalid_torrent: dict[str, Any] | None = None
    path_mapping: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    mam_id: str
    web_host: str = "0.0.0.0"
    web_port: int = 3157
    min_ratio: float = 2.0
    unsat_buffer: int = 10
    max_unsat_slots: int | None = None
    wedge_buffer: int = 0
    prefer_wedges: bool = False
    download_on_wedge_failure: bool = False
    grab_both_formats: bool = False
    add_torrents_stopped: bool = False
    exclude_narrator_in_library_dir: bool = False
    search_interval: int = 30
    link_interval: int = 10
    import_interval: int = 135
    ignore_torrents: tuple[int, ...] = ()
    audio_types: tuple[str, ...] = ("m4b", "m4a", "mp4", "mp3", "ogg")
    ebook_types: tuple[str, ...] = ("cbz", "epub", "pdf", "mobi", "azw3", "azw", "cbr")
    music_types: tuple[str, ...] = ("pdf", "mp3")
    radio_types: tuple[str, ...] = ("mp3",)
    qbittorrent: tuple[QbitConfig, ...] = ()
    search: dict[str, Any] = field(default_factory=dict)
    audiobookshelf: dict[str, Any] | None = None
    autograbs: tuple[dict[str, Any], ...] = ()
    snatchlist: tuple[dict[str, Any], ...] = ()
    goodreads_lists: tuple[dict[str, Any], ...] = ()
    notion_lists: tuple[dict[str, Any], ...] = ()
    tags: tuple[dict[str, Any], ...] = ()
    libraries: tuple[dict[str, Any], ...] = ()


_ALIASES = {
    "goodreads_interval": "import_interval",
    "autograb": "autograbs",
    "goodreads_list": "goodreads_lists",
    "notion_list": "notion_lists",
    "tag": "tags",
    "library": "libraries",
}


def _environment_overrides(environment: Mapping[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key, value in environment.items():
        if not key.startswith("MLM_CONF_"):
            continue
        name = key.removeprefix("MLM_CONF_").lower()
        try:
            overrides[name] = tomllib.loads(f"value = {value}")["value"]
        except tomllib.TOMLDecodeError:
            overrides[name] = value
    return overrides


def load_config(path: Path, *, environment: Mapping[str, str] | None = None) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"could not read config {path}: {error}") from error
    for old, new in _ALIASES.items():
        if old not in raw:
            continue
        legacy_value = raw.pop(old)
        if new not in raw:
            raw[new] = legacy_value
    raw.update(
        _environment_overrides(os.environ if environment is None else environment)
    )

    allowed = set(Config.__dataclass_fields__)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unknown configuration fields: {', '.join(unknown)}")
    if "mam_id" not in raw:
        raise ConfigError("missing required configuration field: mam_id")

    qbit_rows = raw.pop("qbittorrent", [])
    try:
        qbit = tuple(QbitConfig(**row) for row in qbit_rows)
        tuple_fields = {
            "ignore_torrents",
            "audio_types",
            "ebook_types",
            "music_types",
            "radio_types",
            "autograbs",
            "snatchlist",
            "goodreads_lists",
            "notion_lists",
            "tags",
            "libraries",
        }
        for name in tuple_fields & raw.keys():
            raw[name] = tuple(raw[name])
        config = Config(qbittorrent=qbit, **raw)
        if config.min_ratio <= 0:
            raise ConfigError("min_ratio must be greater than zero")
        if config.unsat_buffer < 0:
            raise ConfigError("unsat_buffer cannot be negative")
        if config.max_unsat_slots is not None and config.max_unsat_slots < 0:
            raise ConfigError("max_unsat_slots cannot be negative")
        if config.wedge_buffer < 0:
            raise ConfigError("wedge_buffer cannot be negative")
        for name in ("search_interval", "link_interval", "import_interval"):
            if int(getattr(config, name)) < 1:
                raise ConfigError(f"{name} must be at least 1 minute")
        return config
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid configuration: {error}") from error


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise ConfigError(f"unsupported editable value: {value!r}")


def save_root_config_values(path: Path, values: Mapping[str, object | None]) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"could not read config {path}: {error}") from error

    table = re.search(r"(?m)^[ \t]*\[", text)
    position = table.start() if table else len(text)
    root = text[:position]
    suffix = text[position:]
    for legacy, canonical in _ALIASES.items():
        if canonical in values:
            legacy_pattern = re.compile(
                rf"(?m)^[ \t]*{re.escape(legacy)}[ \t]*=.*(?:\r?\n|$)"
            )
            root = legacy_pattern.sub("", root)
    missing: list[str] = []
    for key, value in values.items():
        pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*(?:\r?\n|$)")
        if value is None:
            root = pattern.sub("", root)
            continue
        replacement = f"{key} = {_toml_scalar(value)}\n"
        root, count = pattern.subn(replacement, root, count=1)
        if count == 0:
            missing.append(replacement)

    if missing:
        root = root.rstrip() + "\n" + "".join(missing) + "\n"
    text = root + suffix.lstrip("\r\n")

    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        load_config(temporary, environment={})
        os.replace(temporary, path)
    except (OSError, ConfigError) as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(f"could not save config {path}: {error}") from error
    return load_config(path)


def save_config_text(path: Path, text: str) -> Config:
    """Validate and atomically replace the complete editable TOML document."""
    if not text.strip():
        raise ConfigError("configuration cannot be empty")
    if not text.endswith("\n"):
        text += "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        load_config(temporary, environment={})
        os.replace(temporary, path)
    except (OSError, ConfigError) as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(f"could not save config {path}: {error}") from error
    return load_config(path)
