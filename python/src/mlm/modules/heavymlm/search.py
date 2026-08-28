from __future__ import annotations

import html
from typing import Any

from ...search import (
    LANGUAGE_BY_ID,
    as_bool,
    as_int,
    decode_name_mapping,
    decode_series_info,
    parse_size,
)


def _format_bytes(value: Any) -> str:
    try:
        size = max(0, parse_size(value))
    except ValueError:
        return str(value).strip() or "Unknown"
    amount = float(size)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if amount < 1024 or candidate == units[-1]:
            break
        amount /= 1024
    precision = 0 if amount.is_integer() else 1
    return f"{amount:.{precision}f} {unit}"


def _availability(row: dict[str, Any]) -> list[dict[str, str]]:
    badges = []
    if as_bool(row.get("free")):
        badges.append({"label": "Global freeleech", "tone": "free"})
    if as_bool(row.get("personal_freeleech")):
        badges.append({"label": "Personal freeleech", "tone": "free"})
    if as_bool(row.get("vip")) or as_bool(row.get("fl_vip")):
        badges.append({"label": "VIP freeleech", "tone": "vip"})
    if not badges:
        badges.append({"label": "Ratio download", "tone": "ratio"})
    return badges


def _availability_keys(row: dict[str, Any]) -> list[str]:
    keys = []
    if as_bool(row.get("free")):
        keys.append("global")
    if as_bool(row.get("personal_freeleech")):
        keys.append("personal")
    if as_bool(row.get("vip")) or as_bool(row.get("fl_vip")):
        keys.append("vip")
    if keys:
        keys.append("freeleech")
    else:
        keys.append("ratio")
    return keys


def _contains(values: list[str], wanted: str) -> bool:
    needle = html.unescape(wanted).strip().casefold()
    return not needle or any(needle in value.casefold() for value in values)


def search_seed(filters: dict[str, Any]) -> tuple[str, list[str]]:
    """Pick one broad MaM query; remaining fields are ANDed locally."""
    candidates = (
        ("q", []),
        ("author", ["author"]),
        ("series", ["series"]),
        ("title", ["title"]),
        ("narrator", ["narrator"]),
    )
    for name, fields in candidates:
        value = str(filters.get(name, "")).strip()
        if value:
            return value, fields
    return "", []


def filter_search_results(
    rows: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply independent manual-search dimensions with AND semantics."""
    matched = []
    for row in rows:
        searchable = [
            row["title"],
            *row["authors"],
            *row["narrators"],
            *(series["name"] for series in row["series"]),
            *row["filetypes"],
            row["category"],
            row["language"],
            row["uploader"],
            str(row["id"]),
        ]
        if not _contains(searchable, str(filters.get("q", ""))):
            continue
        if not _contains([row["title"]], str(filters.get("title", ""))):
            continue
        if not _contains(row["authors"], str(filters.get("author", ""))):
            continue
        if not _contains(
            [series["name"] for series in row["series"]],
            str(filters.get("series", "")),
        ):
            continue
        if not _contains(row["narrators"], str(filters.get("narrator", ""))):
            continue
        filetype = str(filters.get("filetype", "")).strip().casefold()
        if filetype and filetype not in row["filetypes"]:
            continue
        category = str(filters.get("category", "")).strip().casefold()
        if category and category not in row["category"].casefold():
            continue
        language = str(filters.get("language", "")).strip().casefold()
        if language and language != row["language"].casefold():
            continue
        availability = str(filters.get("availability", "")).strip().casefold()
        if availability and availability not in row["availability_keys"]:
            continue
        min_seeders = as_int(filters.get("min_seeders"))
        if min_seeders and row["seeders"] < min_seeders:
            continue
        matched.append(row)

    sort_by = str(filters.get("sort", "relevance"))
    sorters = {
        "title": lambda row: row["title"].casefold(),
        "author": lambda row: row["authors"][0].casefold() if row["authors"] else "",
        "series": lambda row: (
            row["series"][0]["name"].casefold() if row["series"] else ""
        ),
        "filetype": lambda row: row["filetypes"][0] if row["filetypes"] else "",
        "size_asc": lambda row: row["size_bytes"],
    }
    if sort_by in sorters:
        matched.sort(key=sorters[sort_by])
    elif sort_by == "size_desc":
        matched.sort(key=lambda row: row["size_bytes"], reverse=True)
    elif sort_by == "seeders":
        matched.sort(key=lambda row: row["seeders"], reverse=True)
    elif sort_by == "newest":
        matched.sort(key=lambda row: row["uploaded_at"], reverse=True)
    return matched


def present_search_result(row: dict[str, Any]) -> dict[str, Any]:
    """Create a stable HeavyMLM search-card model from a MaM API row."""
    series = decode_series_info(row.get("series_info"))
    language = str(row.get("lang_code", "")).strip()
    if not language:
        language = LANGUAGE_BY_ID.get(as_int(row.get("language")), "").title()
    filetypes = [
        value.strip().lower()
        for value in str(row.get("filetype", "")).replace(",", " ").split()
        if value.strip()
    ]
    try:
        size_bytes = max(0, parse_size(row.get("size", 0)))
    except ValueError:
        size_bytes = 0
    return {
        "id": as_int(row.get("id")),
        "title": html.unescape(str(row.get("title", ""))).strip() or "Untitled release",
        "authors": decode_name_mapping(row.get("author_info")),
        "narrators": decode_name_mapping(row.get("narrator_info")),
        "series": series,
        "availability": _availability(row),
        "availability_keys": _availability_keys(row),
        "filetypes": filetypes,
        "size": _format_bytes(row.get("size", 0)),
        "size_bytes": size_bytes,
        "category": str(
            row.get("catname") or row.get("category_name") or "Uncategorized"
        ).strip(),
        "language": language or "Unknown",
        "num_files": as_int(row.get("numfiles")),
        "seeders": as_int(row.get("seeders")),
        "leechers": as_int(row.get("leechers")),
        "snatched": as_int(row.get("times_completed")),
        "uploaded_at": str(row.get("added", "")).strip(),
        "uploader": str(row.get("owner_name", "")).strip(),
    }
