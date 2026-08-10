from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import Config, QbitConfig
from .mam import MamClient
from .qbittorrent import QbitClient
from .repository import Repository
from .search import normalize_title, torrent_meta

INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DISC_PATTERN = re.compile(r"(?:CD|Disc|Disk)\s*(\d+)", re.IGNORECASE)
ProgressCallback = Callable[
    [str, str, dict[str, object] | None],
    None,
]


@dataclass
class OrganizerRun:
    scanned: int = 0
    linked: int = 0
    already_existing: int = 0
    incomplete: int = 0
    skipped: int = 0
    failed: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


class FilePlacementError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        source: Path | None = None,
        destination: Path | None = None,
        method: str | None = None,
        remediation: str,
    ) -> None:
        super().__init__(message)
        self.context: dict[str, object] = {
            "remediation": remediation,
        }
        if source is not None:
            self.context["source"] = str(source)
        if destination is not None:
            self.context["destination"] = str(destination)
        if method is not None:
            self.context["method"] = method


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
        tag.strip().casefold()
        for tag in str(torrent.get("tags", "")).split(",")
        if tag.strip()
    }
    torrent_category = str(torrent.get("category", "")).strip().casefold()
    for library in config.libraries:
        by_category = (
            "category" in library
            and torrent_category == str(library["category"]).strip().casefold()
        )
        by_directory = "download_dir" in library and (
            Path(torrent.get("save_path", "")) == Path(library["download_dir"])
            or Path(library["download_dir"])
            in Path(torrent.get("save_path", "")).parents
        )
        if not (by_category or by_directory):
            continue
        denied = {str(tag).strip().casefold() for tag in library.get("deny_tags", [])}
        if torrent_tags.intersection(denied):
            continue
        allowed = {str(tag).strip().casefold() for tag in library.get("allow_tags", [])}
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
        relative = (
            Path(author) / sanitize_filename(series_name) / sanitize_filename(leaf)
        )
    else:
        relative = Path(author) / sanitize_filename(title)
    edition = meta.get("edition")
    if edition:
        edition_name = edition[0] if isinstance(edition, list) else str(edition)
        relative = relative.with_name(
            sanitize_filename(f"{relative.name}, {edition_name}")
        )
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
    if not parts or any(part == ".." or ":" in part for part in parts):
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
        try:
            os.link(source, destination)
        except OSError as error:
            raise OSError(
                f"could not hardlink {source} to {destination}; if the download and "
                "library are on different drives, set method = "
                '"hardlink_or_copy" in that [[library]] section'
            ) from error
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


def _source_size(source: Path, destination: Path, method: str) -> int:
    try:
        if not source.exists():
            raise FileNotFoundError(source)
        if not source.is_file():
            raise IsADirectoryError(source)
        size = source.stat().st_size
    except OSError as error:
        raise FilePlacementError(
            f"source file is unavailable: {source} ({error})",
            source=source,
            destination=destination,
            method=method,
            remediation=(
                "Check qBittorrent's save_path, the configured path_mapping, and "
                "that the download drive is mounted and readable."
            ),
        ) from error
    if size <= 0:
        raise FilePlacementError(
            f"source file is zero bytes: {source}",
            source=source,
            destination=destination,
            method=method,
            remediation=(
                "Recheck or redownload this torrent in qBittorrent before running "
                "the organizer again."
            ),
        )
    return size


def _validate_placed_file(
    source: Path,
    destination: Path,
    method: str,
    source_size: int,
) -> None:
    try:
        if not destination.exists() or not destination.is_file():
            raise FileNotFoundError(destination)
        destination_size = destination.stat().st_size
    except OSError as error:
        raise FilePlacementError(
            f"placed file could not be verified: {destination} ({error})",
            source=source,
            destination=destination,
            method=method,
            remediation=(
                "Check the library drive and Windows permissions, then run the "
                "organizer again."
            ),
        ) from error
    if destination_size != source_size:
        raise FilePlacementError(
            "file placement size mismatch: "
            f"source has {source_size} bytes but destination has "
            f"{destination_size} bytes",
            source=source,
            destination=destination,
            method=method,
            remediation=(
                "Check the library drive for free space or I/O errors, remove any "
                "partial output, and run the organizer again."
            ),
        )


def _prepare_staging_directory(target_dir: Path) -> Path:
    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            if not target_dir.is_dir():
                raise FilePlacementError(
                    f"library destination is not a directory: {target_dir}",
                    destination=target_dir,
                    remediation=(
                        "Move or rename the existing item at this path, then run the "
                        "organizer again."
                    ),
                )
            if any(target_dir.iterdir()):
                raise FilePlacementError(
                    "library destination already exists and is not empty: "
                    f"{target_dir}",
                    destination=target_dir,
                    remediation=(
                        "Review the existing library folder. HeavyMLM will not "
                        "overwrite it; merge or rename it before retrying."
                    ),
                )
            target_dir.rmdir()
        staging = target_dir.parent / (
            f".{target_dir.name}.heavymlm-staging-{uuid4().hex}"
        )
        staging.mkdir()
        return staging
    except FilePlacementError:
        raise
    except OSError as error:
        raise FilePlacementError(
            f"could not prepare library staging folder: {error}",
            destination=target_dir,
            remediation=(
                "Check that the library drive is mounted, has free space, and grants "
                "HeavyMLM permission to create folders."
            ),
        ) from error


def _publish_staging_directory(staging: Path, target_dir: Path) -> None:
    if target_dir.exists():
        raise FilePlacementError(
            f"library destination appeared while files were being placed: {target_dir}",
            destination=target_dir,
            remediation=(
                "Review the competing library process or existing folder, then run "
                "the organizer again."
            ),
        )
    try:
        staging.replace(target_dir)
    except OSError as error:
        raise FilePlacementError(
            f"could not publish completed library folder: {error}",
            destination=target_dir,
            remediation=(
                "Check the library drive and permissions, then run the organizer again."
            ),
        ) from error


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _progress(
    callback: ProgressCallback | None,
    message: str,
    *,
    level: str = "info",
    context: dict[str, object] | None = None,
) -> None:
    if callback:
        callback(message, level, context)


def _organizer_progress(
    callback: ProgressCallback | None,
    result: OrganizerRun,
    *,
    current: int,
    total: int,
) -> None:
    counts: dict[str, object] = {
        "current": current,
        "total": total,
        "organized": result.linked,
        "already_existing": result.already_existing,
        "downloading": result.incomplete,
        "skipped": result.skipped,
        "errors": result.failed,
    }
    _progress(
        callback,
        (
            f"Progress {current}/{total} | {result.linked} organized | "
            f"{result.already_existing} already existed | "
            f"{result.incomplete} downloading | {result.skipped} skipped | "
            f"{result.failed} errors"
        ),
        level="warning" if result.failed else "info",
        context=counts,
    )


async def _library_scope_torrents(
    config: Config,
    qbit: QbitClient,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, object]]:
    categories = list(
        dict.fromkeys(
            str(library["category"]).strip()
            for library in config.libraries
            if str(library.get("category", "")).strip()
        )
    )
    uses_directory_rules = any(
        str(library.get("download_dir", "")).strip() for library in config.libraries
    )
    if uses_directory_rules:
        candidates = await qbit.torrents()
        query_mode = "all_for_directory_rules"
    elif categories:
        category_results = await asyncio.gather(
            *(qbit.torrents(category=category) for category in categories)
        )
        candidates = [
            torrent for category_rows in category_results for torrent in category_rows
        ]
        query_mode = "configured_categories"
    else:
        candidates = []
        query_mode = "no_library_scope"

    unique: dict[str, dict[str, Any]] = {}
    for torrent in candidates:
        torrent_hash = str(torrent.get("hash", ""))
        if torrent_hash:
            unique[torrent_hash] = torrent
    scoped = [
        (torrent, library)
        for torrent in unique.values()
        if (library := find_library(config, torrent)) is not None
    ]
    return scoped, {
        "categories": categories,
        "directory_rules": uses_directory_rules,
        "query_mode": query_mode,
        "returned_by_qbittorrent": len(candidates),
        "eligible": len(scoped),
    }


async def _organize_torrent(
    config: Config,
    repository: Repository,
    qbit_config: QbitConfig,
    qbit: QbitClient,
    mam: MamClient,
    qbit_torrent: dict[str, Any],
    library: dict[str, Any],
    *,
    progress: ProgressCallback | None,
) -> str:
    torrent_name = str(qbit_torrent.get("name") or qbit_torrent.get("hash"))
    torrent_hash = str(qbit_torrent["hash"])
    context: dict[str, object] = {
        "torrent": torrent_name,
        "hash": torrent_hash,
        "category": qbit_torrent.get("category"),
        "save_path": qbit_torrent.get("save_path"),
    }
    existing = repository.torrent(torrent_hash)
    if existing and not existing.get("client_status"):
        _progress(
            progress,
            f"Checking tracker state: {torrent_name}",
            context=context,
        )
        trackers = await qbit.trackers(torrent_hash)
        if any(
            tracker.get("msg") == "torrent not registered with this tracker"
            for tracker in trackers
        ):
            repository.mark_removed_from_mam(existing)
    if existing and existing.get("library_path"):
        _progress(
            progress,
            f"Already organized: {torrent_name}",
            level="debug",
            context={**context, "library_path": existing.get("library_path")},
        )
        return "already_organized"

    _progress(progress, f"Inspecting files: {torrent_name}", context=context)
    files = await qbit.files(torrent_hash)
    audio = select_format(library.get("audio_types"), config.audio_types, files)
    ebook = select_format(library.get("ebook_types"), config.ebook_types, files)
    if not audio and not ebook:
        repository.log_activity(
            "organizer",
            f"Skipped {torrent_name}: no preferred audio or ebook file was found",
            level="warning",
            context={
                **context,
                "files": [row.get("name") for row in files],
                "audio_types": list(config.audio_types),
                "ebook_types": list(config.ebook_types),
            },
        )
        _progress(
            progress,
            f"Skipped {torrent_name}: no preferred audio or ebook files",
            level="warning",
            context={**context, "files": len(files)},
        )
        return "no_preferred_files"

    _progress(
        progress,
        f"Loading MaM metadata: {torrent_name}",
        context={**context, "audio_format": audio, "ebook_format": ebook},
    )
    mam_row = await mam.get_torrent_info(torrent_hash)
    if not mam_row:
        repository.log_activity(
            "organizer",
            f"Skipped {torrent_name}: MaM metadata lookup returned no torrent",
            level="warning",
            context=context,
        )
        _progress(
            progress,
            f"Skipped {torrent_name}: MaM metadata was not found",
            level="warning",
            context=context,
        )
        return "missing_mam_metadata"

    meta = torrent_meta(mam_row)
    method = str(library.get("method", "hardlink"))
    target_dir = (
        None
        if method == "no_link"
        else library_directory(config.exclude_narrator_in_library_dir, library, meta)
    )
    if method != "no_link" and target_dir is None:
        author_diagnostics = {
            **context,
            "author_info_type": type(mam_row.get("author_info")).__name__,
            "author_info": mam_row.get("author_info"),
            "decoded_authors": meta.get("authors", []),
            "metadata_keys": sorted(str(key) for key in mam_row),
        }
        repository.log_activity(
            "organizer",
            f"Skipped {torrent_name}: metadata has no author for the library path",
            level="warning",
            context=author_diagnostics,
        )
        _progress(
            progress,
            f"Skipped {torrent_name}: metadata has no author",
            level="warning",
            context=author_diagnostics,
        )
        return "missing_author"

    library_files: list[str] = []
    if target_dir is not None:
        download_root = map_path(
            qbit_config.path_mapping, str(qbit_torrent["save_path"])
        )
        selected_files: list[tuple[Path, Path]] = []
        for content in files:
            torrent_path = safe_torrent_path(str(content["name"]))
            lower_name = torrent_path.name.lower()
            if not (
                (audio and lower_name.endswith(audio))
                or (ebook and lower_name.endswith(ebook))
            ):
                continue
            relative = _destination_relative(torrent_path)
            selected_files.append((download_root / torrent_path, relative))
        if not selected_files:
            raise FilePlacementError(
                "no files remained after selecting the preferred format",
                destination=target_dir,
                method=method,
                remediation=(
                    "Review the preferred audio and ebook extensions for this "
                    "library, then run the organizer again."
                ),
            )

        planned: list[tuple[Path, Path, int]] = []
        destinations: set[Path] = set()
        for file_index, (source, relative) in enumerate(selected_files, start=1):
            destination = target_dir / relative
            if relative in destinations:
                raise FilePlacementError(
                    "multiple torrent files resolve to the same library path: "
                    f"{relative}",
                    source=source,
                    destination=destination,
                    method=method,
                    remediation=(
                        "Review the torrent's file layout and Disc folder names, then "
                        "organize this release manually if needed."
                    ),
                )
            destinations.add(relative)
            _progress(
                progress,
                f"Preflight file {file_index}/{len(selected_files)}: {relative}",
                context={
                    **context,
                    "method": method,
                    "source": str(source),
                    "destination": str(destination),
                },
            )
            source_size = await asyncio.to_thread(
                _source_size, source, destination, method
            )
            planned.append((source, relative, source_size))

        _progress(
            progress,
            f"Staging {len(planned)} file(s) for: {target_dir}",
            context={
                **context,
                "method": method,
                "target": str(target_dir),
                "files": len(planned),
            },
        )
        staging_dir: Path | None = None
        try:
            staging_dir = await asyncio.to_thread(
                _prepare_staging_directory, target_dir
            )
            for file_index, (source, relative, source_size) in enumerate(
                planned, start=1
            ):
                staging_destination = staging_dir / relative
                final_destination = target_dir / relative
                _progress(
                    progress,
                    f"Placing file {file_index}/{len(planned)}: {relative}",
                    context={
                        **context,
                        "method": method,
                        "source": str(source),
                        "destination": str(final_destination),
                        "source_bytes": source_size,
                    },
                )
                await asyncio.to_thread(
                    staging_destination.parent.mkdir, parents=True, exist_ok=True
                )
                try:
                    await asyncio.to_thread(
                        _place_file, source, staging_destination, method
                    )
                    await asyncio.to_thread(
                        _validate_placed_file,
                        source,
                        staging_destination,
                        method,
                        source_size,
                    )
                except FilePlacementError:
                    raise
                except OSError as error:
                    raise FilePlacementError(
                        f"file placement failed: {error}",
                        source=source,
                        destination=final_destination,
                        method=method,
                        remediation=(
                            "Check the library drive's free space and Windows "
                            "permissions. For different drives, use method = "
                            '"hardlink_or_copy" or "copy".'
                        ),
                    ) from error
                library_files.append(str(relative))
                _progress(
                    progress,
                    f"Verified: {relative} ({source_size} bytes)",
                    level="success",
                    context={
                        **context,
                        "source": str(source),
                        "destination": str(final_destination),
                        "bytes": source_size,
                    },
                )
            try:
                await asyncio.to_thread(
                    _write_metadata,
                    staging_dir / "metadata.json",
                    {"mam": mam_row, "meta": meta},
                )
            except OSError as error:
                raise FilePlacementError(
                    f"could not write library metadata: {error}",
                    destination=target_dir / "metadata.json",
                    method=method,
                    remediation=(
                        "Check the library drive's free space and Windows write "
                        "permissions, then run the organizer again."
                    ),
                ) from error
            await asyncio.to_thread(_publish_staging_directory, staging_dir, target_dir)
            staging_dir = None
            _progress(
                progress,
                f"Published complete library folder: {target_dir}",
                level="success",
                context={
                    **context,
                    "destination": str(target_dir),
                    "files": len(library_files),
                },
            )
        finally:
            if staging_dir is not None:
                await asyncio.to_thread(shutil.rmtree, staging_dir, True)
                _progress(
                    progress,
                    f"Rolled back incomplete library copy: {target_dir}",
                    level="warning",
                    context={**context, "target": str(target_dir)},
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
    repository.log_activity(
        "organizer",
        f"Organized {torrent_name} into the library",
        context={
            **context,
            "library_path": str(target_dir) if target_dir else None,
            "files": sorted(library_files),
            "method": method,
        },
    )
    _progress(
        progress,
        f"Organized: {torrent_name}",
        level="success",
        context={
            **context,
            "library_path": str(target_dir) if target_dir else None,
            "files": len(library_files),
            "method": method,
        },
    )
    return "linked"


async def organize_completed(
    config: Config,
    repository: Repository,
    qbit_config: QbitConfig,
    qbit: QbitClient,
    mam: MamClient,
    *,
    progress: ProgressCallback | None = None,
) -> OrganizerRun:
    result = OrganizerRun()
    scoped_torrents, scope = await _library_scope_torrents(config, qbit)
    _progress(
        progress,
        (
            f"Organizer scope: {len(scoped_torrents)} eligible torrents in "
            f"{', '.join(scope['categories']) or 'configured library paths'}"
        ),
        context=scope,
    )
    repository.log_activity(
        "organizer",
        "Loaded qBittorrent organizer scope",
        context=scope,
    )
    for index, (qbit_torrent, library) in enumerate(scoped_torrents, start=1):
        result.scanned += 1
        torrent_name = str(qbit_torrent.get("name") or qbit_torrent.get("hash"))
        context = {
            "torrent": torrent_name,
            "hash": qbit_torrent.get("hash"),
            "category": qbit_torrent.get("category"),
            "save_path": qbit_torrent.get("save_path"),
            "current": index,
            "total": len(scoped_torrents),
        }
        completion = float(qbit_torrent.get("progress", 0))
        _progress(
            progress,
            f"[{index}/{len(scoped_torrents)}] Checking {torrent_name}",
            context={**context, "completion_percent": round(completion * 100, 1)},
        )
        if completion < 1:
            result.incomplete += 1
            _progress(
                progress,
                f"Waiting for download: {torrent_name} ({completion:.0%})",
                level="debug",
                context=context,
            )
            _organizer_progress(
                progress,
                result,
                current=index,
                total=len(scoped_torrents),
            )
            continue
        try:
            outcome = await _organize_torrent(
                config,
                repository,
                qbit_config,
                qbit,
                mam,
                qbit_torrent,
                library,
                progress=progress,
            )
            if outcome == "linked":
                result.linked += 1
            elif outcome == "already_organized":
                result.already_existing += 1
            else:
                result.skip(outcome)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - isolate failures per torrent
            result.failed += 1
            error_text = f"{type(error).__name__}: {error}"
            failure = {
                **context,
                "error": error_text,
                "error_type": type(error).__name__,
            }
            if isinstance(error, FilePlacementError):
                failure.update(error.context)
            result.failures.append(failure)
            repository.record_organizer_error(
                str(qbit_torrent.get("hash", "")),
                torrent_name,
                error_text,
                failure,
            )
            repository.log_activity(
                "organizer",
                f"Failed to organize {torrent_name}",
                level="error",
                context=failure,
            )
            _progress(
                progress,
                f"Failed {torrent_name}: {error_text}",
                level="error",
                context=failure,
            )
        _organizer_progress(
            progress,
            result,
            current=index,
            total=len(scoped_torrents),
        )
    _progress(
        progress,
        (
            f"Organizer finished: {result.scanned}/{len(scoped_torrents)} checked | "
            f"{result.linked} organized | "
            f"{result.already_existing} already existed | "
            f"{result.incomplete} downloading | {result.skipped} skipped | "
            f"{result.failed} errors"
        ),
        level="success" if not result.failed else "warning",
        context={
            "scanned": result.scanned,
            "linked": result.linked,
            "already_existing": result.already_existing,
            "incomplete": result.incomplete,
            "skipped": result.skipped,
            "failed": result.failed,
            "skip_reasons": result.skip_reasons,
            "failures": result.failures,
        },
    )
    return result
