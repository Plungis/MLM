from __future__ import annotations

import html
import re
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


def _normalize_tokens(text: str) -> list[str]:
    """Lowercase, unescape, and split into clean alphanumeric tokens."""
    cleaned = re.sub(r"[^\w\s]", " ", html.unescape(text).casefold())
    return [t for t in cleaned.split() if t]


def _field_matches(values: list[str], wanted: str) -> bool:
    """Match a specific field against user input with initialism tolerance."""
    if not wanted or not wanted.strip():
        return True
    wanted_clean = html.unescape(wanted).strip().casefold()
    wanted_tokens = _normalize_tokens(wanted)
    if not wanted_tokens:
        return True

    for val in values:
        val_clean = html.unescape(val).strip().casefold()
        if wanted_clean in val_clean:
            return True
        val_tokens = set(_normalize_tokens(val))
        val_collapsed = "".join(_normalize_tokens(val))
        # Check if all wanted tokens match in the field or collapsed initials
        if all(
            t in val_tokens or (len(t) <= 3 and t in val_collapsed)
            for t in wanted_tokens
        ):
            return True
    return False


def _matches_q(searchable: list[str], q: str) -> bool:
    """Check if query tokens match anywhere across searchable fields."""
    if not q or not q.strip():
        return True

    # Strip volume notation from strict token requirements
    cleaned_q = re.sub(r"#\s*[\d.]+", " ", q)
    cleaned_q = re.sub(
        r"\b(?:book|vol(?:ume)?)\s*#?\s*[\d.]+\b",
        " ",
        cleaned_q,
        flags=re.IGNORECASE,
    )
    query_tokens = _normalize_tokens(cleaned_q)
    if not query_tokens:
        return True

    combined_text = " ".join(html.unescape(v) for v in searchable).casefold()
    combined_clean = " ".join(_normalize_tokens(combined_text))
    combined_words = set(combined_clean.split())
    collapsed = combined_clean.replace(" ", "")

    stop_words = {
        "of",
        "the",
        "in",
        "and",
        "a",
        "an",
        "on",
        "to",
        "for",
        "with",
        "by",
    }
    matched_any = False

    for token in query_tokens:
        if token in combined_words:
            matched_any = True
            continue
        if len(token) >= 3 and token in combined_clean:
            matched_any = True
            continue
        if len(token) <= 3 and token in collapsed:
            matched_any = True
            continue
        if token in stop_words:
            continue
        return False
    return matched_any or not any(t not in stop_words for t in query_tokens)


def _contains(values: list[str], wanted: str) -> bool:
    """Legacy helper maintained for backward compatibility."""
    return _field_matches(values, wanted)


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
            if name == "q":
                # Clean volume markers like #2 to prevent MAM search failures
                cleaned = re.sub(r"#\s*[\d.]+", " ", value)
                cleaned = re.sub(
                    r"\b(?:book|vol(?:ume)?)\s*#?\s*[\d.]+\b",
                    " ",
                    cleaned,
                    flags=re.IGNORECASE,
                )
                cleaned = " ".join(cleaned.split())
                return cleaned or value, fields
            return value, fields
    return "", []


def compute_relevance(row: dict[str, Any], filters: dict[str, Any]) -> float:
    """Compute an intuitive relevance score for ranking MAM search results."""
    score = 0.0
    q = str(filters.get("q", "")).strip()
    if not q:
        return float(row.get("seeders", 0))

    cleaned_q = re.sub(r"#\s*[\d.]+", " ", q)
    q_tokens = _normalize_tokens(cleaned_q)
    stop_words = {
        "of",
        "the",
        "in",
        "and",
        "a",
        "an",
        "on",
        "to",
        "for",
        "with",
        "by",
    }
    content_tokens = [t for t in q_tokens if t not in stop_words]

    title_norm = " ".join(_normalize_tokens(row.get("title", "")))
    title_words = set(title_norm.split())

    authors_norm = " ".join(
        _normalize_tokens(" ".join(row.get("authors", [])))
    )
    authors_words = set(authors_norm.split())
    authors_collapsed = authors_norm.replace(" ", "")

    matched_author_tokens = 0
    for t in content_tokens:
        if t in authors_words or (len(t) <= 3 and t in authors_collapsed):
            score += 150.0
            matched_author_tokens += 1

    matched_title_tokens = 0
    for t in content_tokens:
        if t in title_words:
            score += 100.0
            matched_title_tokens += 1

    # Exact phrase or sub-phrase match in title
    if cleaned_q.strip().casefold() in title_norm:
        score += 300.0

    # Volume match bonus
    vol_match = re.search(r"#\s*([\d.]+)", q)
    if vol_match:
        vol = vol_match.group(1)
        for s in row.get("series", []):
            if vol in s.get("entries", []):
                score += 80.0
        if f"#{vol}" in row.get("title", "") or f"book {vol}" in title_norm:
            score += 80.0

    # Synergy bonus if both author AND title matched
    if matched_author_tokens > 0 and matched_title_tokens > 0:
        score += 600.0

    # Seeder tie-breaker
    score += min(float(row.get("seeders", 0)), 100.0) / 10.0
    return score


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
        if not _matches_q(searchable, str(filters.get("q", ""))):
            continue
        if not _field_matches([row["title"]], str(filters.get("title", ""))):
            continue
        if not _field_matches(row["authors"], str(filters.get("author", ""))):
            continue
        if not _field_matches(
            [series["name"] for series in row["series"]],
            str(filters.get("series", "")),
        ):
            continue
        if not _field_matches(
            row["narrators"], str(filters.get("narrator", ""))
        ):
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
        "author": (
            lambda row: row["authors"][0].casefold() if row["authors"] else ""
        ),
        "series": lambda row: (
            row["series"][0]["name"].casefold() if row["series"] else ""
        ),
        "filetype": lambda row: (
            row["filetypes"][0] if row["filetypes"] else ""
        ),
        "size_asc": lambda row: row["size_bytes"],
    }
    if sort_by == "relevance":
        matched.sort(
            key=lambda row: compute_relevance(row, filters), reverse=True
        )
    elif sort_by in sorters:
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
        "title": (
            html.unescape(str(row.get("title", ""))).strip()
            or "Untitled release"
        ),
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
