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

STOP_WORDS = {
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
    "at",
    "from",
    "into",
}

COMMON_SHORT_WORDS = {
    "of",
    "in",
    "to",
    "on",
    "at",
    "by",
    "no",
    "my",
    "we",
    "he",
    "it",
    "is",
    "be",
    "as",
    "or",
    "so",
    "up",
    "go",
    "do",
    "an",
    "am",
    "me",
    "us",
    "if",
    "oz",
    "ai",
    "dr",
    "mr",
    "ms",
    "st",
}

KNOWN_3CHAR_INITIALS = {
    "jrr",
    "grr",
    "lrh",
    "chs",
    "kjh",
    "arm",
    "elb",
    "jml",
}


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


def _is_initial_token(token: str, raw_token: str = "") -> bool:
    """Return True if a token looks like an author initial (e.g. 'a', 'aj', 'aJ')."""
    low = token.casefold()
    if not low.isalpha():
        return False
    if len(low) == 1:
        return low != "a"
    if len(low) == 2:
        if (
            raw_token
            and len(raw_token) == 2
            and raw_token[0].islower()
            and raw_token[1].isupper()
        ):
            return True
        return low not in COMMON_SHORT_WORDS
    return len(low) == 3 and low in KNOWN_3CHAR_INITIALS


def clean_query_for_mam(text: str) -> str:
    """Clean a query string for MAM Sphinx search without initial/volume breakage."""
    if not text or not text.strip():
        return ""
    raw = html.unescape(text).strip()
    # Strip bracketed/parenthetical noise like [Unabridged], (Book 2), (Audiobook)
    raw = re.sub(
        r"[\(\[][^\)\]]*(?:unabridged|abridged|audiobooks?|ebooks?|retail|book\s*#?\d+|vol(?:ume)?\s*#?\d+|series|omnibus|edition)[^\)\]]*[\)\]]",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    # Strip generic subtitle suffixes like ": A Novel", ": A Thriller"
    raw = re.sub(
        r":\s*a\s+(?:novel|thriller|mystery|memoir|story|novella|litrpg|progression\s+fantasy)\b.*$",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    # Strip volume markers like #2, Book 2, Vol 2, Part 2
    raw = re.sub(r"#\s*[\d.]+", " ", raw)
    raw = re.sub(
        r"\b(?:book|vol(?:ume)?|part|pt)\s*#?\s*[\d.]+\b",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    # Strip dotted initials like "A.J." or "A. J." or "J.R.R."
    raw = re.sub(r"\b(?:[A-Za-z]\.\s*)+", " ", raw)
    # Replace punctuation (except apostrophes inside words) with spaces
    raw = re.sub(r"[^\w\s']", " ", raw)
    raw_words = [w.strip("'") for w in raw.split() if w.strip("'")]
    if not raw_words:
        return ""

    # If there are substantial words (>= 3 chars), drop fused 1-2 char initials
    substantial = [
        w
        for w in raw_words
        if len(w) >= 3 and not _is_initial_token(w, w)
    ]
    if substantial:
        filtered = [
            w for w in raw_words if not _is_initial_token(w, w)
        ]
        if filtered:
            return " ".join(filtered)
    return " ".join(raw_words)


def extract_author_mam_terms(author: str) -> list[str]:
    """Return clean search terms for querying MAM's author field."""
    if not author or not author.strip():
        return []
    # Take primary author if multiple are comma/semicolon separated
    primary = re.split(r"[,;&]|\band\b", html.unescape(author), maxsplit=1)[0].strip()
    if not primary:
        primary = author.strip()

    cleaned = clean_query_for_mam(primary)
    terms: list[str] = []
    if cleaned:
        terms.append(cleaned)

    honorifics = {"jr", "sr", "iii", "ii", "dr", "md", "phd"}
    tokens = [
        t
        for t in _normalize_tokens(primary)
        if len(t) >= 3 and t not in honorifics and not _is_initial_token(t)
    ]
    if tokens:
        surname = tokens[-1]
        full_clean = " ".join(tokens)
        if full_clean and full_clean not in terms:
            terms.append(full_clean)
        if surname and surname not in terms:
            terms.append(surname)

    if not terms and primary.strip():
        terms.append(primary.strip())
    return terms


def extract_title_mam_terms(title: str, series_name: str = "") -> list[str]:
    """Return clean book title variants for querying MAM's title field."""
    if not title or not title.strip():
        return []
    raw = html.unescape(title).strip()
    # Strip bracketed/parenthetical noise
    raw = re.sub(r"[\(\[][^\)\]]+[\)\]]", " ", raw)
    # Strip generic subtitle suffixes
    raw = re.sub(
        r":\s*a\s+(?:novel|thriller|mystery|memoir|story|novella|litrpg)\b.*$",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    # Strip volume tags
    raw = re.sub(r"#\s*[\d.]+", " ", raw)
    raw = re.sub(
        r"\b(?:book|vol(?:ume)?)\s*#?\s*[\d.]+\b",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    raw = " ".join(raw.split()).strip(" -:,")
    if not raw:
        return []

    terms: list[str] = []
    series_norm = " ".join(_normalize_tokens(series_name)) if series_name else ""

    # If title has ':' or ' - ', check segments for standalone title vs series
    if ":" in raw or " - " in raw:
        parts = [
            p.strip(" -:,")
            for p in re.split(r":|\s+-\s+", raw)
            if p.strip(" -:,")
        ]
        for part in parts:
            part_norm = " ".join(_normalize_tokens(part))
            if not part_norm or len(part_norm) < 3:
                continue
            if series_norm and part_norm == series_norm:
                continue
            if part not in terms:
                terms.append(part)

    if raw not in terms:
        terms.append(raw)
    return terms


def parse_composite_query(q: str) -> dict[str, Any]:
    """Decompose a user search string into volume, author, title, and segments."""
    if not q or not q.strip():
        return {
            "volume": "",
            "author_hint": "",
            "title_hint": "",
            "segments": [],
            "clean_q": "",
        }
    raw_q = html.unescape(q).strip()
    vol_match = re.search(
        r"(?:#|\b(?:book|vol(?:ume)?)\s*#?)\s*([\d.]+)",
        raw_q,
        flags=re.IGNORECASE,
    )
    volume = vol_match.group(1) if vol_match else ""

    author_hint = ""
    title_hint = ""
    segments: list[str] = []

    # Check explicit 'Title by Author' pattern
    by_split = re.split(r"\s+\bby\b\s+", raw_q, maxsplit=1, flags=re.IGNORECASE)
    if len(by_split) == 2 and by_split[0].strip() and by_split[1].strip():
        title_hint = clean_query_for_mam(by_split[0])
        author_hint = by_split[1].strip()
        segments = [title_hint, clean_query_for_mam(author_hint)]
    else:
        # Check split by volume marker (#2, Book 2) or dash ' - '
        parts = [
            p.strip(" -:,")
            for p in re.split(
                r"#\s*[\d.]+|\b(?:book|vol(?:ume)?)\s*#?\s*[\d.]+\b|\s+-\s+",
                raw_q,
                flags=re.IGNORECASE,
            )
            if p.strip(" -:,")
        ]
        if len(parts) >= 2:
            author_hint = parts[0]
            title_hint = parts[1]
            for p in parts:
                cleaned_p = clean_query_for_mam(p)
                if cleaned_p and cleaned_p not in segments:
                    segments.append(cleaned_p)

    clean_q = clean_query_for_mam(raw_q)
    return {
        "volume": volume,
        "author_hint": author_hint,
        "title_hint": title_hint,
        "segments": [s for s in segments if s],
        "clean_q": clean_q,
    }


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
        if token in STOP_WORDS:
            continue
        return False
    return matched_any or not any(t not in STOP_WORDS for t in query_tokens)


def _matches_q_relaxed(row: dict[str, Any], q: str) -> bool:
    """Fallback matcher when strict all-token matching yields 0 results.

    Matches if the row matches the parsed author, series, or at least half of
    the non-stopword query tokens.
    """
    if not q or not q.strip():
        return True
    parsed = parse_composite_query(q)
    if parsed["author_hint"] and _field_matches(
        row.get("authors", []), parsed["author_hint"]
    ):
        return True
    if parsed["title_hint"] and _field_matches(
        [row.get("title", "")], parsed["title_hint"]
    ):
        return True

    content_tokens = [
        t for t in _normalize_tokens(parsed["clean_q"] or q) if t not in STOP_WORDS
    ]
    if not content_tokens:
        return True

    searchable = [
        row.get("title", ""),
        *row.get("authors", []),
        *(s.get("name", "") for s in row.get("series", [])),
    ]
    combined_norm = " ".join(_normalize_tokens(" ".join(searchable)))
    combined_words = set(combined_norm.split())
    collapsed = combined_norm.replace(" ", "")

    # Check if author matches any query token of length >= 4
    author_words = set(
        _normalize_tokens(" ".join(row.get("authors", [])))
    )
    if any(len(t) >= 4 and t in author_words for t in content_tokens):
        return True

    hits = sum(
        1
        for t in content_tokens
        if t in combined_words or (len(t) <= 3 and t in collapsed)
    )
    return hits >= max(1, (len(content_tokens) + 1) // 2)


def _contains(values: list[str], wanted: str) -> bool:
    """Legacy helper maintained for backward compatibility."""
    return _field_matches(values, wanted)


def search_seed(filters: dict[str, Any]) -> tuple[str, list[str]]:
    """Pick one broad MaM query; remaining fields are ANDed locally."""
    candidates = (
        ("q", ["title", "author", "series", "narrator"]),
        ("author", ["author"]),
        ("series", ["series"]),
        ("title", ["title"]),
        ("narrator", ["narrator"]),
    )
    for name, fields in candidates:
        value = str(filters.get(name, "")).strip()
        if value:
            if name == "q":
                cleaned = clean_query_for_mam(value)
                return cleaned or value, fields
            if name == "author":
                terms = extract_author_mam_terms(value)
                return (terms[0] if terms else value), fields
            return value, fields
    return "", []


def _volume_matches_row(row: dict[str, Any], vol: str) -> tuple[bool, bool]:
    """Return (matched_target_volume, has_different_volume) for a row."""
    if not vol:
        return False, False
    try:
        target_num = float(vol)
    except ValueError:
        target_num = -1.0

    has_any_vol = False
    for s in row.get("series", []):
        for entry in s.get("entries", []):
            has_any_vol = True
            if str(entry).strip() == vol:
                return True, False
            try:
                if target_num >= 0 and float(str(entry).strip()) == target_num:
                    return True, False
            except ValueError:
                pass

    title_str = str(row.get("title", ""))
    title_norm = " ".join(_normalize_tokens(title_str))
    vol_patterns = [
        rf"#\s*0*{re.escape(vol)}\b",
        rf"\b(?:book|vol(?:ume)?)\s*#?\s*0*{re.escape(vol)}\b",
    ]
    for pat in vol_patterns:
        if re.search(pat, title_str, flags=re.IGNORECASE) or re.search(
            pat, title_norm, flags=re.IGNORECASE
        ):
            return True, False

    other_vol = re.search(
        r"(?:#|\b(?:book|vol(?:ume)?)\s*#?)\s*([\d.]+)\b",
        title_str,
        flags=re.IGNORECASE,
    )
    if other_vol:
        has_any_vol = True
    return False, has_any_vol


def compute_relevance(row: dict[str, Any], filters: dict[str, Any]) -> float:
    """Compute an intuitive relevance score for ranking MAM search results."""
    score = 0.0
    q = str(filters.get("q", "")).strip()
    author_filter = str(filters.get("author", "")).strip()
    series_filter = str(filters.get("series", "")).strip()
    title_filter = str(filters.get("title", "")).strip()

    if not q and not author_filter and not series_filter and not title_filter:
        return float(row.get("seeders", 0))

    parsed = parse_composite_query(q) if q else {
        "volume": "",
        "author_hint": "",
        "title_hint": "",
        "clean_q": "",
    }
    q_tokens = _normalize_tokens(parsed["clean_q"] or q)
    content_tokens = [t for t in q_tokens if t not in STOP_WORDS]

    title_norm = " ".join(_normalize_tokens(row.get("title", "")))
    title_words = set(title_norm.split())

    authors_norm = " ".join(
        _normalize_tokens(" ".join(row.get("authors", [])))
    )
    authors_words = set(authors_norm.split())
    authors_collapsed = authors_norm.replace(" ", "")

    series_names = [s.get("name", "") for s in row.get("series", [])]
    series_norm = " ".join(_normalize_tokens(" ".join(series_names)))
    series_words = set(series_norm.split())

    matched_author_tokens = 0
    for t in content_tokens:
        if t in authors_words or (len(t) <= 3 and t in authors_collapsed):
            score += 180.0
            matched_author_tokens += 1

    if parsed["author_hint"] and _field_matches(
        row.get("authors", []), parsed["author_hint"]
    ):
        score += 500.0
        matched_author_tokens += 1

    matched_title_tokens = 0
    for t in content_tokens:
        if t in title_words:
            score += 120.0
            matched_title_tokens += 1

    matched_series_tokens = 0
    for t in content_tokens:
        if t in series_words:
            score += 140.0
            matched_series_tokens += 1

    # Exact phrase or parsed title match in title
    if parsed["title_hint"]:
        hint_norm = " ".join(_normalize_tokens(parsed["title_hint"]))
        if hint_norm and hint_norm in title_norm:
            score += 600.0
            matched_title_tokens += 1
    elif parsed["clean_q"]:
        clean_norm = " ".join(_normalize_tokens(parsed["clean_q"]))
        if clean_norm and clean_norm in title_norm:
            score += 400.0

    # Volume match bonus / mismatch penalty
    vol = parsed["volume"]
    if vol:
        vol_matched, vol_different = _volume_matches_row(row, vol)
        if vol_matched:
            score += 500.0
        elif vol_different:
            score -= 250.0

    # Synergy bonus if both author/series AND title matched
    if (
        matched_author_tokens > 0 or matched_series_tokens > 0
    ) and matched_title_tokens > 0:
        score += 800.0

    # Seeder tie-breaker
    score += min(float(row.get("seeders", 0)), 100.0) / 10.0
    return score


def extract_related_entities(
    rows: list[dict[str, Any]], filters: dict[str, Any] | None = None
) -> dict[str, list[str]]:
    """Extract top unique authors and series from search results for exploration."""
    authors: list[str] = []
    series_list: list[str] = []
    seen_authors: set[str] = set()
    seen_series: set[str] = set()

    for row in rows[:25]:
        for a in row.get("authors", []):
            a_clean = a.strip()
            if a_clean and a_clean.casefold() not in seen_authors:
                seen_authors.add(a_clean.casefold())
                authors.append(a_clean)
                if len(authors) >= 6:
                    break
        for s in row.get("series", []):
            s_name = str(s.get("name", "")).strip()
            if s_name and s_name.casefold() not in seen_series:
                seen_series.add(s_name.casefold())
                series_list.append(s_name)
                if len(series_list) >= 6:
                    break

    return {"authors": authors[:6], "series": series_list[:6]}


def _passes_non_q_filters(
    row: dict[str, Any], filters: dict[str, Any]
) -> bool:
    """Check all explicit field filters other than the freeform 'q' query."""
    if not _field_matches([row["title"]], str(filters.get("title", ""))):
        return False
    if not _field_matches(row["authors"], str(filters.get("author", ""))):
        return False
    if not _field_matches(
        [series["name"] for series in row["series"]],
        str(filters.get("series", "")),
    ):
        return False
    if not _field_matches(
        row["narrators"], str(filters.get("narrator", ""))
    ):
        return False
    filetype = str(filters.get("filetype", "")).strip().casefold()
    if filetype and filetype not in row["filetypes"]:
        return False
    category = str(filters.get("category", "")).strip().casefold()
    if category and category not in row["category"].casefold():
        return False
    language = str(filters.get("language", "")).strip().casefold()
    if language and language != row["language"].casefold():
        return False
    availability = str(filters.get("availability", "")).strip().casefold()
    if availability and availability not in row["availability_keys"]:
        return False
    min_seeders = as_int(filters.get("min_seeders"))
    return not (min_seeders and row["seeders"] < min_seeders)


def filter_search_results(
    rows: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply manual-search filters with strict match first and related fallback."""
    base_candidates = [
        row for row in rows if _passes_non_q_filters(row, filters)
    ]
    q_val = str(filters.get("q", "")).strip()
    if not q_val:
        matched = base_candidates
    else:
        matched = []
        for row in base_candidates:
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
            if _matches_q(searchable, q_val):
                matched.append(row)

        # Fallback to related author/series/title matches if strict found nothing
        if not matched and base_candidates:
            matched = [
                row
                for row in base_candidates
                if _matches_q_relaxed(row, q_val)
            ]

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
