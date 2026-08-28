from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ...autograbber import _preference, _preferred_types
from ...config import Config
from ...mam import MamClient
from ...repository import Repository
from ...search import (
    as_bool,
    as_int,
    metadata_matches,
    normalize_title,
    torrent_meta,
)
from .search import present_search_result

ENTRY_NUMBER_PATTERN = re.compile(r"^#?\s*([\d.]+)", re.IGNORECASE)
TITLE_SERIES_PATTERN = re.compile(r"(?:book|vol(?:ume)?|#)\s*([\d.]+)", re.IGNORECASE)


def parse_entry_number(value: str) -> tuple[int, float, str]:
    """Return a sortable tuple for series volume numbers (e.g. '1', '2.5', 'Book 1')."""
    cleaned = value.strip().casefold()
    match = ENTRY_NUMBER_PATTERN.search(cleaned) or TITLE_SERIES_PATTERN.search(cleaned)
    if match:
        try:
            return (0, float(match.group(1)), cleaned)
        except ValueError:
            pass
    return (1, 999999.0, cleaned)


def extract_series_volume(row: dict[str, Any], series_name: str) -> str:
    """Find the specific volume/entry string for a series in a release."""
    normalized_target = series_name.strip().casefold()
    for series in row.get("series", []):
        name = str(series.get("name", "")).strip().casefold()
        if name == normalized_target or normalized_target in name:
            entries = series.get("entries", [])
            if entries and str(entries[0]).strip():
                return str(entries[0]).strip()

    title = str(row.get("title", ""))
    match = TITLE_SERIES_PATTERN.search(title)
    if match:
        return match.group(1).strip()
    return normalize_title(title)


def score_series_candidate(
    row: dict[str, Any],
    config: Config,
    preferred_media: str | None = None,
    preferred_filetype: str | None = None,
) -> tuple[int, int, int, int]:
    """Score a release candidate to select the best release for a given volume."""
    meta = row if "media_type" in row else torrent_meta(row)
    media_type = meta.get("media_type", "")
    filetypes = meta.get("filetypes", [])

    media_score = 0
    if preferred_media:
        media_score = 1 if media_type == preferred_media else 0

    preferred_formats = _preferred_types(config, media_type)
    format_pref = _preference(filetypes, preferred_formats)
    format_score = (
        100 - format_pref
        if format_pref is not None
        else (50 if preferred_filetype and preferred_filetype in filetypes else 0)
    )

    cost_score = 0
    if as_bool(row.get("free")) or as_bool(row.get("personal_freeleech")):
        cost_score = 3
    elif as_bool(row.get("vip")) or as_bool(row.get("fl_vip")):
        cost_score = 2
    else:
        cost_score = 1

    seeder_score = min(as_int(row.get("seeders", 0)), 1000)
    return (media_score, format_score, cost_score, seeder_score)


async def fetch_series_releases(
    mam: MamClient,
    series_name: str,
    *,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Search MyAnonamouse for all torrents belonging to a series."""
    cleaned_name = series_name.strip()
    if not cleaned_name:
        return []

    releases: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    start = 0

    for _ in range(max_pages):
        query = {
            "dlLink": True,
            "mediaInfo": True,
            "isbn": True,
            "perpage": 100,
            "tor": {
                "text": cleaned_name,
                "srchIn": ["series"],
                "startNumber": start,
            },
        }
        result = await mam.search(query)
        rows = result.get("data", [])
        if not rows:
            break
        before_count = len(seen_ids)
        for row in rows:
            if not isinstance(row, dict):
                continue
            torrent_id = as_int(row.get("id"))
            if torrent_id and torrent_id not in seen_ids:
                seen_ids.add(torrent_id)
                releases.append(row)
        start += len(rows)
        found = as_int(result.get("found"), len(rows))
        if len(seen_ids) == before_count or start >= found:
            break

    # If searching in series returned zero matches, try a broader query fallback
    if not releases:
        for _ in range(2):
            query = {
                "dlLink": True,
                "mediaInfo": True,
                "isbn": True,
                "perpage": 100,
                "tor": {
                    "text": cleaned_name,
                    "startNumber": start,
                },
            }
            result = await mam.search(query)
            rows = result.get("data", [])
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                torrent_id = as_int(row.get("id"))
                if torrent_id and torrent_id not in seen_ids:
                    seen_ids.add(torrent_id)
                    releases.append(row)
            start += len(rows)
            found = as_int(result.get("found"), len(rows))
            if start >= found:
                break

    return releases


def group_series_books(
    releases: list[dict[str, Any]],
    series_name: str,
    config: Config,
    *,
    preferred_media: str | None = None,
    preferred_filetype: str | None = None,
) -> list[dict[str, Any]]:
    """Group releases by series entry/book, picking the best release for each volume."""
    target_normalized = series_name.strip().casefold()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in releases:
        meta = torrent_meta(row)
        matches_series = any(
            target_normalized in str(s.get("name", "")).strip().casefold()
            for s in meta.get("series", [])
        ) or target_normalized in normalize_title(meta["title"])

        if not matches_series:
            continue

        volume = extract_series_volume(meta, series_name)
        groups[volume].append(row)

    books: list[dict[str, Any]] = []
    for volume, candidates in groups.items():
        best_row = max(
            candidates,
            key=lambda row: score_series_candidate(
                row,
                config,
                preferred_media=preferred_media,
                preferred_filetype=preferred_filetype,
            ),
        )
        presented = present_search_result(best_row)
        meta = torrent_meta(best_row)
        books.append(
            {
                "volume": volume,
                "sort_key": parse_entry_number(volume),
                "mam_id": presented["id"],
                "title": presented["title"],
                "authors": presented["authors"],
                "release": presented,
                "raw_row": best_row,
                "meta": meta,
                "candidates_count": len(candidates),
            }
        )

    books.sort(key=lambda item: item["sort_key"])
    return books


async def resolve_series_selection(
    config: Config,
    repository: Repository,
    mam: MamClient,
    series_name: str,
    *,
    preferred_media: str | None = None,
    preferred_filetype: str | None = None,
) -> dict[str, Any]:
    """Resolve all distinct books for a series, partitioning missing vs present."""
    releases = await fetch_series_releases(mam, series_name)
    grouped_books = group_series_books(
        releases,
        series_name,
        config,
        preferred_media=preferred_media,
        preferred_filetype=preferred_filetype,
    )

    missing_books: list[dict[str, Any]] = []
    already_present: list[dict[str, Any]] = []

    for book in grouped_books:
        torrent_id = book["mam_id"]
        meta = book["meta"]
        title_search = normalize_title(meta["title"])

        is_present = repository.has_mam_id(torrent_id)
        if not is_present:
            for existing in repository.records_with_title(title_search):
                if metadata_matches(meta, existing.get("meta", {})):
                    is_present = True
                    break

        if is_present:
            book["status"] = "already_present"
            already_present.append(book)
        else:
            book["status"] = "missing"
            missing_books.append(book)

    return {
        "series_name": series_name.strip(),
        "total_books": len(grouped_books),
        "missing_books": missing_books,
        "already_present": already_present,
        "all_books": grouped_books,
    }


def detect_series_gaps(volumes: list[str]) -> list[str]:
    """Detect missing volume numbers in a collection of volume entries."""
    parsed_nums: list[float] = []
    for vol in volumes:
        sort_info = parse_entry_number(vol)
        if sort_info[0] == 0:
            parsed_nums.append(sort_info[1])

    if not parsed_nums or len(parsed_nums) < 1:
        return []

    int_nums = {int(n) for n in parsed_nums if n.is_integer()}
    if not int_nums:
        return []

    min_vol = min(int_nums)
    max_vol = max(int_nums)
    start_check = 1 if (1 < min_vol <= 3) else min_vol

    gaps: list[str] = []
    for expected in range(start_check, max_vol + 1):
        if expected not in int_nums:
            gaps.append(f"#{expected}")
            if len(gaps) >= 10:
                break
    return gaps


def detect_library_series(repository: Repository) -> list[dict[str, Any]]:
    """Scan library and queued records to discover series and check missing books."""
    processed_torrents = repository.table_rows("torrents", limit=5000, offset=0)
    selected_torrents = repository.table_rows("selected_torrents", limit=5000, offset=0)
    pending_ids = {as_int(t.get("mam_id")) for t in repository.pending_selected()}

    grouped_series: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"name": "", "authors": set(), "books": [], "volumes": []}
    )

    all_records = [*processed_torrents, *selected_torrents]
    seen_ids: set[int] = set()

    for record in all_records:
        mam_id = as_int(record.get("mam_id"))
        if mam_id and mam_id in seen_ids:
            continue
        if mam_id:
            seen_ids.add(mam_id)

        meta = record.get("meta") or torrent_meta(record)
        series_list = meta.get("series", [])
        title = meta.get("title") or record.get("title") or "Untitled"
        authors = meta.get("authors", [])

        if not series_list:
            match = TITLE_SERIES_PATTERN.search(title)
            if match:
                prefix = title[: match.start()].strip(" -:,")
                if prefix:
                    series_list = [
                        {"name": prefix, "entries": [match.group(1).strip()]}
                    ]

        for s in series_list:
            s_name = str(s.get("name", "")).strip()
            if not s_name:
                continue
            key = s_name.casefold()
            group = grouped_series[key]
            if not group["name"]:
                group["name"] = s_name
            for author in authors:
                if author:
                    group["authors"].add(author)

            entries = s.get("entries", [])
            volume = (
                str(entries[0]).strip()
                if entries
                else extract_series_volume(meta, s_name)
            )
            group["volumes"].append(volume)
            group["books"].append(
                {
                    "title": title,
                    "volume": volume,
                    "sort_key": parse_entry_number(volume),
                    "mam_id": mam_id,
                    "media_type": meta.get("media_type", "audiobook"),
                    "filetypes": meta.get("filetypes", []),
                    "pending": mam_id in pending_ids if mam_id else False,
                }
            )

    results: list[dict[str, Any]] = []
    for group in grouped_series.values():
        if not group["books"]:
            continue
        group["books"].sort(key=lambda b: b["sort_key"])
        gaps = detect_series_gaps(group["volumes"])
        authors_list = sorted(group["authors"])
        results.append(
            {
                "series_name": group["name"],
                "authors": authors_list,
                "volumes_count": len(group["books"]),
                "volumes": [b["volume"] for b in group["books"]],
                "gaps": gaps,
                "books": group["books"],
            }
        )

    results.sort(key=lambda s: s["series_name"].casefold())
    return results
