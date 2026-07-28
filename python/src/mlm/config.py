from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


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
    wedge_buffer: int = 0
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


def load_config(
    path: Path, *, environment: Mapping[str, str] | None = None
) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"could not read config {path}: {error}") from error
    for old, new in _ALIASES.items():
        if old in raw and new not in raw:
            raw[new] = raw.pop(old)
    raw.update(_environment_overrides(os.environ if environment is None else environment))

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
        return Config(qbittorrent=qbit, **raw)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid configuration: {error}") from error
