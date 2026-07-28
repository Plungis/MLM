from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from .mam import MamClient

FLAG_BITS = {
    "crude_language": 1 << 1,
    "crude": 1 << 1,
    "language": 1 << 1,
    "violence": 1 << 2,
    "some_explicit": 1 << 3,
    "explicit": 1 << 4,
    "abridged": 1 << 5,
    "lgbt": 1 << 6,
}

MEDIA_TYPE_BY_ID = {
    1: "audiobook",
    2: "ebook",
    3: "musicology",
    4: "radio",
    5: "manga",
    6: "comic_book",
    7: "periodical_ebook",
    8: "periodical_audiobook",
}

MAIN_CATEGORY_BY_ID = {13: "audiobook", 14: "ebook", 15: "musicology", 16: "radio"}

LANGUAGE_BY_ID = {
    1: "english",
    2: "chinese",
    3: "gujarati",
    4: "spanish",
    5: "kannada",
    6: "burmese",
    7: "thai",
    8: "hindi",
    9: "marathi",
    10: "telugu",
    11: "tamil",
    12: "javanese",
    13: "vietnamese",
    14: "punjabi",
    15: "urdu",
    16: "russian",
    17: "afrikaans",
    18: "bulgarian",
    19: "catalan",
    20: "czech",
    21: "danish",
    22: "dutch",
    23: "finnish",
    24: "scottish",
    25: "ukrainian",
    26: "greek",
    27: "hebrew",
    28: "hungarian",
    29: "tagalog",
    30: "romanian",
    31: "serbian",
    32: "arabic",
    33: "malay",
    34: "portuguese",
    35: "bengali",
    36: "french",
    37: "german",
    38: "japanese",
    39: "farsi",
    40: "swedish",
    41: "korean",
    42: "turkish",
    43: "italian",
    44: "cantonese",
    45: "polish",
    46: "latin",
    47: "other",
    48: "norwegian",
    49: "croatian",
    50: "lithuanian",
    51: "bosnian",
    52: "brazilian",
    53: "indonesian",
    54: "slovenian",
    55: "castilian",
    56: "irish",
    57: "manx",
    58: "malayalam",
    59: "ancient greek",
    60: "sanskrit",
    61: "estonian",
    62: "latvian",
    63: "icelandic",
}

SIZE_UNITS = {
    "b": 1,
    "kb": 1_000,
    "kib": 1 << 10,
    "mb": 1_000_000,
    "mib": 1 << 20,
    "gb": 1_000_000_000,
    "gib": 1 << 30,
    "tb": 1_000_000_000_000,
    "tib": 1 << 40,
}


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "null"}
    return bool(value)


def parse_size(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    match = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?i?b)?\s*", str(value), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid size: {value!r}")
    return int(
        float(match.group(1)) * SIZE_UNITS.get((match.group(2) or "b").lower(), 1)
    )


def normalize_title(value: str) -> str:
    ascii_title = (
        unicodedata.normalize("NFKD", html.unescape(value))
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .replace(" & ", " and ")
    )
    ascii_title = re.sub(r"^(the|a|an)\s+|[^\w ]", "", ascii_title)
    return re.sub(r"(?i)(volume|vol\.)", "", ascii_title)


def _mapping_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(item) for item in value.values()]
    return []


def torrent_meta(row: dict[str, Any]) -> dict[str, Any]:
    media_id = as_int(row.get("mediatype"))
    main_id = as_int(row.get("main_cat"))
    media_type = MEDIA_TYPE_BY_ID.get(media_id) or MAIN_CATEGORY_BY_ID.get(
        main_id, "unknown"
    )
    series = []
    raw_series = row.get("series_info")
    if isinstance(raw_series, str):
        raw_series = {}
    if isinstance(raw_series, dict):
        for value in raw_series.values():
            if isinstance(value, list) and value:
                series.append({"name": str(value[0]), "entries": value[1:2]})
    filetypes = [part.lower() for part in str(row.get("filetype", "")).split() if part]
    return {
        "mam_id": as_int(row.get("id")),
        "vip_status": "Permanent" if as_bool(row.get("vip")) else "NotVip",
        "cat": {"id": as_int(row.get("category")), "name": str(row.get("catname", ""))},
        "media_type": media_type,
        "main_cat": as_int(row.get("maincat")) or None,
        "categories": [as_int(value) for value in row.get("categories", [])],
        "language": as_int(row.get("language")) or None,
        "flags": as_int(row.get("browseflags")),
        "filetypes": filetypes,
        "num_files": as_int(row.get("numfiles")),
        "size": parse_size(row.get("size", 0)),
        "title": html.unescape(str(row.get("title", ""))),
        "edition": None,
        "authors": _mapping_values(row.get("author_info")),
        "narrators": _mapping_values(row.get("narrator_info")),
        "series": series,
        "source": "Mam",
        "uploaded_at": str(row.get("added", "")),
    }


def _date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def matches_filter(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    media_types = {str(value).lower() for value in rule.get("media_type", [])}
    actual_media = MEDIA_TYPE_BY_ID.get(as_int(row.get("mediatype")))
    if media_types and actual_media not in media_types:
        return False

    categories = rule.get("categories", {})
    if categories:
        main = MAIN_CATEGORY_BY_ID.get(as_int(row.get("main_cat")))
        category_rule = categories.get({"audiobook": "audio"}.get(main, main), False)
        if category_rule is False:
            return False
        if isinstance(category_rule, list):
            names = {str(value).lower().replace(" ", "_") for value in category_rule}
            actual = (
                str(row.get("catname", row.get("cat", ""))).lower().replace(" ", "_")
            )
            if not any(actual == name or actual.endswith(f"_{name}") for name in names):
                return False

    languages = {str(value).lower() for value in rule.get("languages", [])}
    if languages:
        actual_languages = {
            str(row.get("lang_code", "")).lower(),
            LANGUAGE_BY_ID.get(as_int(row.get("language")), ""),
        }
        if languages.isdisjoint(actual_languages):
            return False

    bitfield = as_int(row.get("browseflags"))
    for name, required in rule.get("flags", {}).items():
        bit = FLAG_BITS.get(name.lower().replace(" ", "_"))
        if bit is None or bool(bitfield & bit) != bool(required):
            return False

    size = parse_size(row.get("size", 0))
    if rule.get("min_size") and size < parse_size(rule["min_size"]):
        return False
    if rule.get("max_size") and size > parse_size(rule["max_size"]):
        return False
    if str(row.get("owner_name", "")) in rule.get("exclude_uploader", []):
        return False
    if rule.get("uploaded_after") and _date(row.get("added")) < _date(
        rule["uploaded_after"]
    ):
        return False
    if rule.get("uploaded_before") and _date(row.get("added")) > _date(
        rule["uploaded_before"]
    ):
        return False

    comparisons = {
        "seeders": "seeders",
        "leechers": "leechers",
        "snatched": "times_completed",
    }
    for label, field in comparisons.items():
        value = as_int(row.get(field))
        minimum = rule.get(f"min_{label}")
        maximum = rule.get(f"max_{label}")
        if minimum is not None and value < int(minimum):
            return False
        if maximum is not None and value > int(maximum):
            return False
    return True


def build_search_query(rule: dict[str, Any], start: int = 0) -> dict[str, Any]:
    kind = rule.get("type", "new")
    target = None
    search_type = None
    if kind == "bookmarks":
        target = "bookmarks"
    elif kind == "mine":
        target = "mine"
    elif isinstance(kind, dict) and "uploader" in kind:
        target = f"u{kind['uploader']}"
    if kind == "freeleech":
        search_type = "fl"
    elif rule.get("cost", "free") == "free":
        search_type = "fl-VIP"
    sort_types = {
        "low_seeders": "seedersAsc",
        "low_snatches": "snatchedAsc",
        "oldest_first": "dateAsc",
        "random": "random",
    }
    tor = {
        "text": rule.get("query", ""),
        "srchIn": rule.get("search_in", []),
        "sortType": sort_types.get(
            rule.get("sort_by"), "dateDesc" if kind == "new" else ""
        ),
        "startNumber": start,
    }
    if target:
        tor["searchIn"] = target
    if search_type:
        tor["searchType"] = search_type
    return {"dlLink": True, "perpage": 100, "tor": {k: v for k, v in tor.items() if v}}


async def search_pages(
    mam: MamClient, rule: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    kind = rule.get("type", "new")
    default_pages = 50 if kind in {"bookmarks", "freeleech", "mine"} else 1
    max_pages = int(rule.get("max_pages") or default_pages)
    start = 0
    for _ in range(max_pages):
        result = await mam.search(build_search_query(rule, start))
        rows = result.get("data", [])
        for row in rows:
            if isinstance(row, dict):
                yield row
        start += len(rows)
        found = as_int(result.get("found", len(rows)))
        if not rows or start >= found:
            break
