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
    return {
        "id": as_int(row.get("id")),
        "title": html.unescape(str(row.get("title", ""))).strip() or "Untitled release",
        "authors": decode_name_mapping(row.get("author_info")),
        "narrators": decode_name_mapping(row.get("narrator_info")),
        "series": series,
        "availability": _availability(row),
        "filetypes": filetypes,
        "size": _format_bytes(row.get("size", 0)),
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
