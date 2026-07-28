from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from .config import Config, QbitConfig
from .mam import MamClient
from .qbittorrent import QbitClient
from .repository import Repository
from .search import normalize_title, torrent_meta

INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DISC_PATTERN = re.compile(r"(?:CD|Disc|Disk)\s*(\d+)", re.I)


def sanitize_filename(value: str) -> str:
    cleaned = INVALID_FILENAME.sub("_", value).strip().rstrip(". ")
    return cleaned or "_"


def map_path(path_mapping: dict[str, str], save_path: str) -> Path:
    source = Path(save_path)
    matches = sorted(
        (
            (Path(old), Path(new))
            for old, new in path_mapping.items()
            if source == Path(old) or Path(old) in source.parents
        ),
        key=lambda pair: len(pair[0].parts),
        reverse=True,
    )
    if not matches:
        return source
    old, new = matches[0]
    return new.joinpath(source.relative_to(old))


def find_library(config: Config, torrent: dict[str, Any]) -> dict[str, Any] | None:
    torrent_tags = {
        tag.strip() for tag in str(torrent.get("tags", "")).split(",") if tag.strip()
    }
    for library in config.libraries:
        by_category = "category" in library and torrent.get("category") == library["category"]
        by_directory = "download_dir" in library and (
            Path(torrent.get("save_path", "")) == Path(library["download_dir"])
            or Path(library["download_dir"]) in Path(torrent.get("save_path", "")).parents
        )
        if not (by_category or by_directory):
            continue
        if torrent_tags.intersection(library.get("deny_tags", [])):
            continue
        allowed = set(library.get("allow_tags", []))
        if allowed and not torrent_tags.intersection(allowed):
            continue
        return library
    return None


def _series_parts(meta: dict[str, Any]) -> tuple[str, str] | None:
    series_rows = meta.get("series", [])
    if not series_rows:
        return None
    series = next(
        (row for row in series_rows if row.get("entries")),
        series_rows[0],
    )
    name = str(series.get("name", "")).strip()
    entries = series.get("entries", [])
    number = str(entries[0]) if entries else ""
    return name, number


def library_directory(
    exclude_narrator: bool, library: dict[str, Any], meta: dict[str, Any]
) -> Path | None:
    authors = meta.get("authors", [])
    if not authors:
        return None
    author = sanitize_filename(str(authors[0]))
    title = str(meta.get("title", "")).strip()
    series = _series_parts(meta)
    if series:
        series_name, number = series
        leaf = f"{series_name} #{number} - {title}" if number else title
        relative = Path(author) / sanitize_filename(series_name) / sanitize_filename(leaf)
    else:
        relative = Path(author) / sanitize_filename(title)
    edition = meta.get("edition")
    if edition:
        edition_name = edition[0] if isinstance(edition, list) else str(edition)
        relative = relative.with_name(sanitize_filename(f"{relative.name}, {edition_name}"))
    narrators = meta.get("narrators", [])
    if narrators and not exclude_narrator:
        relative = relative.with_name(
            sanitize_filename(f"{relative.name} {{{narrators[0]}}}")
        )
    return Path(library["library_dir"]) / relative


def select_format(
    override: list[str] | None, preferred: tuple[str, ...], files: list[dict[str, Any]]
) -> str | None:
    for extension in override or list(preferred):
        suffix = "." + extension.lower().lstrip(".")
        if any(str(row.get("name", "")).lower().endswith(suffix) for row in files):
            return suffix
    return None


def safe_torrent_path(name: str) -> Path:
    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"unsafe torrent path: {name!r}")
    return Path(*parts)


def _destination_relative(torrent_path: Path) -> Path:
    parent = torrent_path.parent.name
    match = DISC_PATTERN.search(parent)
    return (
        Path(f"Disc {match.group(1)}") / torrent_path.name
        if match
        else Path(torrent_path.name)
    )


def _place_file(source: Path, destination: Path, method: str) -> None:
    if destination.exists():
        if method.startswith("hardlink") and os.path.samefile(source, destination):
            return
        raise FileExistsError(f"library file already exists: {destination}")
    if method == "hardlink":
        os.link(source, destination)
    elif method == "hardlink_or_copy":
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
    elif method == "hardlink_or_symlink":
        try:
            os.link(source, destination)
        except OSError:
            destination.symlink_to(source)
    elif method == "copy":
        shutil.copy2(source, destination)
    elif method == "symlink":
        destination.symlink_to(source)
    elif method != "no_link":
        raise ValueError(f"unknown library method: {method}")


async def organize_completed(
    config: Config,
    repository: Repository,
    qbit_config: QbitConfig,
    qbit: QbitClient,
    mam: MamClient,
) -> int:
    linked = 0
    for qbit_torrent in await qbit.torrents():
        if float(qbit_torrent.get("progress", 0)) < 1:
            continue
        library = find_library(config, qbit_torrent)
        if library is None:
            continue
        torrent_hash = str(qbit_torrent["hash"])
        existing = repository.torrent(torrent_hash)
        if existing and existing.get("library_path"):
            continue
        files = await qbit.files(torrent_hash)
        audio = select_format(library.get("audio_types"), config.audio_types, files)
        ebook = select_format(library.get("ebook_types"), config.ebook_types, files)
        if not audio and not ebook:
            continue
        mam_row = await mam.get_torrent_info(torrent_hash)
        if not mam_row:
            continue
        meta = torrent_meta(mam_row)
        method = str(library.get("method", "hardlink"))
        target_dir = (
            None
            if method == "no_link"
            else library_directory(
                config.exclude_narrator_in_library_dir, library, meta
            )
        )
        if method != "no_link" and target_dir is None:
            continue
        library_files: list[str] = []
        if target_dir is not None:
            target_dir.mkdir(parents=True, exist_ok=True)
            download_root = map_path(qbit_config.path_mapping, str(qbit_torrent["save_path"]))
            for content in files:
                torrent_path = safe_torrent_path(str(content["name"]))
                lower_name = torrent_path.name.lower()
                if not ((audio and lower_name.endswith(audio)) or (ebook and lower_name.endswith(ebook))):
                    continue
                relative = _destination_relative(torrent_path)
                destination = target_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                _place_file(download_root / torrent_path, destination, method)
                library_files.append(str(relative))
            metadata = {"mam": mam_row, "meta": meta}
            (target_dir / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        now = datetime.now(UTC).isoformat()
        torrent = {
            "id": torrent_hash,
            "id_is_hash": True,
            "mam_id": meta["mam_id"],
            "abs_id": existing.get("abs_id") if existing else None,
            "goodreads_id": existing.get("goodreads_id") if existing else None,
            "library_path": str(target_dir) if target_dir else None,
            "library_files": sorted(library_files),
            "linker": library.get("name"),
            "category": qbit_torrent.get("category") or None,
            "selected_audio_format": audio.lstrip(".") if audio else None,
            "selected_ebook_format": ebook.lstrip(".") if ebook else None,
            "title_search": normalize_title(meta["title"]),
            "meta": meta,
            "created_at": existing.get("created_at", now) if existing else now,
            "replaced_with": existing.get("replaced_with") if existing else None,
            "request_matadata_update": False,
            "library_mismatch": None,
            "client_status": existing.get("client_status") if existing else None,
        }
        repository.record_linked(torrent, meta["mam_id"])
        linked += 1
    return linked
