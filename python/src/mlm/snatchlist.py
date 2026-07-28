from __future__ import annotations

import time
from typing import Any

from .config import Config
from .mam import MamClient
from .repository import Repository
from .search import as_bool, as_int, matches_filter, parse_size


def user_torrent_meta(row: dict[str, Any]) -> dict[str, Any]:
    authors = [
        str(item.get("name", ""))
        for item in sorted(
            row.get("author", []), key=lambda item: as_int(item.get("id"))
        )
    ]
    narrators = [
        str(item.get("name", ""))
        for item in sorted(
            row.get("narrator", []), key=lambda item: as_int(item.get("id"))
        )
    ]
    series = [
        {"name": item.get("name", ""), "entries": [item.get("number", "")]}
        for item in sorted(
            row.get("series", []), key=lambda item: as_int(item.get("id"))
        )
    ]
    category = as_int(row.get("category"))
    return {
        "mam_id": as_int(row.get("id")),
        "vip_status": "Permanent" if as_bool(row.get("vip")) else "NotVip",
        "cat": {"id": category, "name": row.get("catname", "")},
        "media_type": "unknown",
        "main_cat": None,
        "categories": [as_int(item.get("id")) for item in row.get("categories", [])],
        "language": None,
        "flags": as_int(row.get("browseFlags")),
        "filetypes": [
            str(item.get("name", "")).lower() for item in row.get("fileTypes", [])
        ],
        "num_files": 0,
        "size": parse_size(row.get("size", 0)),
        "title": str(row.get("title", "")),
        "edition": None,
        "authors": authors,
        "narrators": narrators,
        "series": series,
        "source": "Mam",
        "uploaded_at": "1970-01-01T00:00:00Z",
    }


def _search_compatible_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "browseflags": row.get("browseFlags"),
        "owner_name": row.get("uploaderName"),
        "personal_freeleech": row.get("personalFree"),
    }


async def run_snatchlist_search(
    config: Config,
    repository: Repository,
    mam: MamClient,
    definition: dict[str, Any],
) -> int:
    if definition.get("languages"):
        raise ValueError("language filtering is not supported for snatchlists")
    if definition.get("uploaded_after") or definition.get("uploaded_before"):
        raise ValueError("upload date filtering is not supported for snatchlists")
    cost = definition.get("cost", "metadata_only")
    if cost not in {"metadata_only", "metadata_only_add"}:
        raise ValueError("snatchlists only support metadata_only costs")

    added = 0
    timestamp = int(time.time())
    max_pages = int(definition.get("max_pages", 100))
    for page in range(max_pages):
        result = await mam.snatchlist(definition["type"], page, timestamp)
        rows = result.get("rows", [])
        for row in rows:
            torrent_id = as_int(row.get("id"))
            if (
                not torrent_id
                or torrent_id in config.ignore_torrents
                or repository.has_mam_id(torrent_id)
                or not matches_filter(_search_compatible_row(row), definition)
            ):
                continue
            if cost == "metadata_only_add" and not definition.get("dry_run", False):
                repository.add_metadata_torrent(
                    user_torrent_meta(row), row.get("uploaderName") or None
                )
                added += 1
        if len(rows) != 250:
            break
    return added
