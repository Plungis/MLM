from __future__ import annotations

import contextlib
from pathlib import Path

from .config import Config
from .qbittorrent import QbitClient
from .repository import Repository


def _preference(config: Config, torrent: dict) -> int:
    media = torrent.get("meta", {}).get("media_type")
    preferred = (
        config.audio_types
        if media
        in {"audiobook", "periodical_audiobook", "Audiobook", "PeriodicalAudiobook"}
        else config.ebook_types
    )
    formats = torrent.get("meta", {}).get("filetypes", [])
    positions = [preferred.index(value) for value in formats if value in preferred]
    return min(positions) if positions else len(preferred) + 1


def remove_library_files(torrent: dict) -> None:
    for item in torrent.get("collection_items", []):
        remove_library_files(item)
    library_path_value = torrent.get("library_path")
    if not library_path_value:
        return
    library_path = Path(library_path_value)
    for relative in torrent.get("library_files", []):
        path = library_path / relative
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != library_path:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    remaining = list(library_path.iterdir()) if library_path.exists() else []
    if all(path.name in {"cover.jpg", "metadata.json"} for path in remaining):
        for path in remaining:
            path.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        library_path.rmdir()


def _library_size(torrent: dict) -> int:
    rows = torrent.get("collection_items") or [torrent]
    total = 0
    for row in rows:
        library_path_value = row.get("library_path")
        if not library_path_value:
            continue
        library_path = Path(library_path_value)
        total += sum(
            path.stat().st_size
            for relative in row.get("library_files", [])
            if (path := library_path / relative).exists()
        )
    return total


async def clean_superseded(
    config: Config,
    repository: Repository,
    qbit_clients: list[tuple[dict, QbitClient]],
) -> int:
    grouped: dict[str, list[dict]] = {}
    for torrent in repository.library_torrents():
        grouped.setdefault(torrent["title_search"], []).append(torrent)
    cleaned = 0
    for rows in grouped.values():
        if len(rows) < 2:
            continue
        rows.sort(
            key=lambda row: (
                _preference(config, row),
                -_library_size(row),
            )
        )
        keep, *remove_rows = rows
        for torrent in remove_rows:
            for qbit_config, qbit in qbit_clients:
                update = qbit_config.get("on_cleaned")
                if not update or not torrent.get("id_is_hash"):
                    continue
                if update.get("category"):
                    await qbit.set_category([torrent["id"]], update["category"])
                await qbit.add_tags([torrent["id"]], update.get("tags", []))
            remove_library_files(torrent)
            torrent["replaced_with"] = [keep["id"], keep["created_at"]]
            torrent["library_path"] = None
            torrent["library_files"] = []
            torrent["collection_items"] = []
            torrent["library_mismatch"] = None
            torrent["abs_id"] = None
            repository.update_torrent(torrent)
            cleaned += 1
    return cleaned
