from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_VERSION = "Beta V.91.1"
GOOGLE_TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}
GOOGLE_TRANSIENT_FAILURE_LIMIT = 3

PROVIDERS = [
    "google",
    "openlibrary",
    "itunes",
    "audible",
    "audible.ca",
    "audible.uk",
    "audible.au",
    "audible.fr",
    "audible.de",
    "audible.jp",
    "audible.it",
    "audible.in",
    "audible.es",
    "fantlab",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "connection": {
        "baseUrl": "",
        "libraryId": "",
        "provider": "audible",
        "rememberConnection": False,
    },
    "providers": {
        "googleBooksApiKey": "",
        "googleBooksApiKeyValidated": False,
        "googleBooksApiKeyFingerprint": "",
        "googleBooksApiKeyValidatedAt": "",
        "googleBooksLastError": "",
    },
    "run": {
        "dryRun": True,
        "limit": 0,
        "pageSize": 100,
        "sort": "media.metadata.title",
        "sortDesc": False,
        "requestDelayMs": 150,
        "timeoutSeconds": 30,
        "searchTimeoutSeconds": 12,
        "maxRetries": 2,
        "stopOnError": False,
    },
    "targeting": {
        "mode": "unprocessed",
        "includeAuthors": [],
        "excludeAuthors": [],
        "includeTags": [],
        "excludeTags": [],
        "includeTagMode": "any",
        "titleContains": "",
        "pathContains": "",
        "missingMetadataOnly": False,
        "missingCoverOnly": False,
        "noAsinOnly": False,
        "noIsbnOnly": False,
        "skipMissingItems": True,
        "skipInvalidItems": True,
    },
    "matching": {
        "threshold": 80,
        "reviewFloor": 65,
        "candidateLimit": 8,
        "adaptiveSearch": True,
        "automaticFallbackProviders": False,
        "fallbackProviders": [],
        "strictAutoMatch": True,
        "minimumTitleScore": 86,
        "minimumAuthorScore": 78,
        "minimumWinnerMargin": 6,
        "minimumStrongSignals": 2,
        "applyMode": "metadata_patch",
        "overwriteMetadata": False,
        "coverMode": "if_missing",
        "quickMatchFirstResultOnly": True,
        "requireAuthor": False,
        "requireTitleToken": True,
        "durationToleranceMinutes": 7,
    },
    "weights": {
        "title": 50,
        "author": 25,
        "series": 8,
        "narrator": 6,
        "year": 6,
        "duration": 5,
    },
    "tags": {
        "matchedTag": "ABSidekick: AutoMatched",
        "unmatchedTag": "ABSidekick: AutoMatch Unmatched",
        "reviewTag": "ABSidekick: Needs Review",
        "tagMatched": True,
        "tagUnmatched": True,
        "tagReview": True,
        "clearUnmatchedOnMatch": True,
        "clearReviewOnMatch": True,
        "clearMatchedOnUnmatched": False,
    },
    "review": {
        "scanLimit": 25,
        "candidateLimit": 6,
        "rejectAddsUnmatchedTag": True,
        "rejectClearsReviewTag": True,
    },
}

TITLE_NOISE = {
    "audiobook",
    "audio",
    "book",
    "edition",
    "retail",
    "unabridged",
    "abridged",
    "complete",
    "download",
    "epub",
    "m4a",
    "m4b",
    "mobi",
    "mp3",
    "pdf",
}
GENERIC_WORDS = {"a", "an", "the", "book", "audiobook"}
ROMAN_NUMERALS = {
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
    "xvi": "16",
    "xvii": "17",
    "xviii": "18",
    "xix": "19",
    "xx": "20",
}
COLLECTION_PATTERN = re.compile(
    r"\b(?:box(?:ed)?\s+set|omnibus|complete\s+series|collection|books?\s+\d+\s*[-–]\s*\d+)\b",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def deep_merge(base: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(base)
    if not incoming:
        return merged
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return deepcopy(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_SETTINGS)
    return deep_merge(DEFAULT_SETTINGS, data)


def save_settings(
    path: Path, settings: dict[str, Any], token: str | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = deepcopy(settings)
    data.setdefault("connection", {})
    if token and data["connection"].get("rememberConnection"):
        data["connection"]["token"] = token
    else:
        data["connection"].pop("token", None)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def public_settings(settings: dict[str, Any], has_token: bool) -> dict[str, Any]:
    data = deepcopy(settings)
    token = data.get("connection", {}).pop("token", None)
    data.setdefault("connection", {})
    data["connection"]["hasToken"] = bool(has_token or token)
    providers = data.setdefault("providers", {})
    google_key = str(providers.pop("googleBooksApiKey", "") or "")
    fingerprint = str(providers.pop("googleBooksApiKeyFingerprint", "") or "")
    providers["hasGoogleBooksApiKey"] = bool(google_key)
    providers["googleBooksReady"] = bool(
        google_key
        and providers.get("googleBooksApiKeyValidated")
        and fingerprint == google_books_key_fingerprint(google_key)
    )
    return data


def google_books_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()


def google_books_key_is_ready(settings: dict[str, Any]) -> bool:
    providers = settings.get("providers", {})
    api_key = str(providers.get("googleBooksApiKey") or "")
    return bool(
        api_key
        and providers.get("googleBooksApiKeyValidated")
        and str(providers.get("googleBooksApiKeyFingerprint") or "")
        == google_books_key_fingerprint(api_key)
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    value = (
        html.unescape(str(value)).lower().replace("&", " and ").replace("+", " and ")
    )
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character for character in value if not unicodedata.combining(character)
    )
    value = re.sub(r"['’`]", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    words = [ROMAN_NUMERALS.get(word, word) for word in value.split()]
    words = [word for word in words if word not in GENERIC_WORDS]
    return " ".join(words)


def normalize_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def normalize_title(value: Any) -> str:
    return " ".join(
        word for word in normalize_text(value).split() if word not in TITLE_NOISE
    )


def title_tokens(value: Any) -> set[str]:
    return {word for word in normalize_title(value).split() if word}


def token_similarity(left: Any, right: Any) -> float:
    left_tokens = title_tokens(left)
    right_tokens = title_tokens(right)
    if not left_tokens and not right_tokens:
        return 100.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    precision = intersection / len(right_tokens)
    recall = intersection / len(left_tokens)
    return (
        100.0 * (2 * precision * recall / (precision + recall)) if intersection else 0.0
    )


def split_people(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        people: list[str] = []
        for item in value:
            if isinstance(item, dict):
                people.append(str(item.get("name") or item.get("author") or ""))
            else:
                people.append(str(item))
        return [person.strip() for person in people if person and person.strip()]
    text = str(value)
    for sep in [";", " and ", " & ", " / "]:
        text = text.replace(sep, ",")
    return [person.strip() for person in text.split(",") if person.strip()]


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def ratio(a: Any, b: Any) -> float:
    a_norm = normalize_title(a)
    b_norm = normalize_title(b)
    if not a_norm and not b_norm:
        return 100.0
    if not a_norm or not b_norm:
        return 0.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio() * 100.0


def title_similarity(left: Any, right: Any) -> float:
    # Do not reward pure containment here. A short title such as "Dune" is fully
    # contained in "Dune Messiah", but they are different books. Known packaging
    # suffixes are handled explicitly by title_match_variants instead.
    return max(ratio(left, right), token_similarity(left, right))


def title_match_variants(value: Any) -> list[str]:
    raw = html.unescape(str(value or "")).strip()
    if not raw:
        return []
    variants = [raw]
    suffix_marker = re.compile(
        r"\b(?:audio(?:book)?|abridged|unabridged|book|edition|series|saga|"
        r"trilogy|vol(?:ume)?|narrated)\b",
        re.IGNORECASE,
    )
    for match in re.finditer(r"\s*(?::|\s(?:-|\||\u2013|\u2014)\s)\s*", raw):
        head = raw[: match.start()].strip()
        tail = raw[match.end() :].strip()
        if len(title_tokens(head)) >= 2 and suffix_marker.search(tail):
            variants.append(head)
    parenthetical = re.match(r"^(.*?)\s*[\(\[]([^\)\]]+)[\)\]]\s*$", raw)
    if parenthetical and suffix_marker.search(parenthetical.group(2)):
        variants.append(parenthetical.group(1).strip())
    return list(dict.fromkeys(variant for variant in variants if variant))


def best_people_ratio(left: Any, right: Any) -> float:
    left_people = split_people(left)
    right_people = split_people(right)
    if not left_people and not right_people:
        return 100.0
    if not left_people or not right_people:
        return 0.0
    return max(
        max(ratio(a, b), token_similarity(a, b))
        for a in left_people
        for b in right_people
    )


def year_score(left: Any, right: Any) -> float:
    try:
        left_year = int(str(left or "")[:4])
        right_year = int(str(right or "")[:4])
    except ValueError:
        return 0.0 if left or right else 100.0
    delta = abs(left_year - right_year)
    if delta == 0:
        return 100.0
    if delta == 1:
        return 80.0
    if delta <= 3:
        return 55.0
    return 0.0


def duration_score(
    item_seconds: Any, candidate_duration: Any, tolerance_minutes: int
) -> float:
    if not item_seconds or not candidate_duration:
        return 0.0
    try:
        item_minutes = float(item_seconds) / 60.0
        candidate_minutes = float(candidate_duration)
        if candidate_minutes > 20000:
            candidate_minutes = candidate_minutes / 60.0
    except (TypeError, ValueError):
        return 0.0
    delta = abs(item_minutes - candidate_minutes)
    tolerance = max(1.0, float(tolerance_minutes))
    if delta <= tolerance:
        return 100.0
    if delta <= tolerance * 2:
        return 75.0
    if delta <= tolerance * 4:
        return 35.0
    return 0.0


def item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("media", {}).get("metadata", {}) or {}


def item_tags(item: dict[str, Any]) -> list[str]:
    tags = item.get("media", {}).get("tags", []) or []
    return [str(tag) for tag in tags if tag]


def item_title(item: dict[str, Any]) -> str:
    metadata = item_metadata(item)
    return str(
        first_present(
            metadata.get("title"),
            item.get("title"),
            Path(str(item.get("path") or "")).name,
            "",
        )
        or ""
    )


def item_author(item: dict[str, Any]) -> str:
    metadata = item_metadata(item)
    return ", ".join(
        split_people(
            first_present(metadata.get("authorName"), metadata.get("authors"), "")
        )
    )


def candidate_author(candidate: dict[str, Any]) -> Any:
    return first_present(
        candidate.get("author"), candidate.get("authors"), candidate.get("authorName")
    )


def candidate_narrator(candidate: dict[str, Any]) -> Any:
    return first_present(candidate.get("narrator"), candidate.get("narrators"))


def candidate_series(candidate: dict[str, Any]) -> Any:
    series = candidate.get("series")
    if isinstance(series, list):
        return ", ".join(
            str(s.get("name") if isinstance(s, dict) else s) for s in series
        )
    if isinstance(series, dict):
        return series.get("name")
    return series


def _series_entries(value: Any) -> list[tuple[str, str]]:
    if not value:
        return []
    if not isinstance(value, list):
        value = [value]
    entries: list[tuple[str, str]] = []
    for entry in value:
        if isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("series") or "").strip()
            sequence = str(
                entry.get("sequence") or entry.get("seq") or entry.get("number") or ""
            ).strip()
        else:
            name = str(entry).strip()
            sequence = ""
        if name:
            entries.append((name, normalize_identifier(sequence)))
    return entries


def item_series_entries(item: dict[str, Any]) -> list[tuple[str, str]]:
    metadata = item_metadata(item)
    entries = _series_entries(metadata.get("series"))
    if entries:
        return entries
    name = first_present(metadata.get("seriesName"), metadata.get("series"))
    sequence = first_present(
        metadata.get("seriesSequence"),
        metadata.get("sequence"),
        metadata.get("seriesNumber"),
    )
    return _series_entries({"name": name, "sequence": sequence})


def candidate_series_entries(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    return _series_entries(candidate.get("series"))


def best_series_score(
    left: list[tuple[str, str]], right: list[tuple[str, str]]
) -> float | None:
    if not left or not right:
        return None
    return max(title_similarity(a[0], b[0]) for a in left for b in right)


def series_sequence_conflict(
    left: list[tuple[str, str]], right: list[tuple[str, str]]
) -> bool:
    for left_name, left_sequence in left:
        for right_name, right_sequence in right:
            if (
                left_sequence
                and right_sequence
                and title_similarity(left_name, right_name) >= 80
                and left_sequence != right_sequence
            ):
                return True
    return False


def item_identifier(item: dict[str, Any], name: str) -> str:
    metadata = item_metadata(item)
    return normalize_identifier(
        first_present(
            metadata.get(name),
            metadata.get(f"{name}13"),
            metadata.get(f"{name}10"),
        )
    )


def candidate_identifier(candidate: dict[str, Any], name: str) -> str:
    return normalize_identifier(
        first_present(
            candidate.get(name),
            candidate.get(f"{name}13"),
            candidate.get(f"{name}10"),
        )
    )


def title_values(item: dict[str, Any]) -> list[str]:
    metadata = item_metadata(item)
    title = item_title(item)
    subtitle = str(metadata.get("subtitle") or "").strip()
    values = [title]
    if subtitle:
        values.append(f"{title} {subtitle}")
    raw_path = str(first_present(item.get("path"), item.get("relPath"), "") or "")
    path_title = re.split(r"[/\\]", raw_path.rstrip("/\\"))[-1].strip()
    if path_title and normalize_title(path_title) != normalize_title(title):
        values.append(path_title)
    return list(
        dict.fromkeys(
            variant
            for value in values
            for variant in title_match_variants(value)
            if variant
        )
    )


def candidate_title_values(candidate: dict[str, Any]) -> list[str]:
    title = str(candidate.get("title") or "").strip()
    subtitle = str(candidate.get("subtitle") or "").strip()
    values = [title, f"{title} {subtitle}" if subtitle else title]
    return list(
        dict.fromkeys(
            variant
            for value in values
            for variant in title_match_variants(value)
            if variant
        )
    )


def best_title_score(item: dict[str, Any], candidate: dict[str, Any]) -> float:
    source_titles = title_values(item)
    searched_title = str(
        (candidate.get("_absidekickSearch") or {}).get("queryTitle") or ""
    ).strip()
    if searched_title:
        source_titles = list(dict.fromkeys([*source_titles, searched_title]))
    return max(
        title_similarity(left, right)
        for left in source_titles
        for right in candidate_title_values(candidate)
    )


def title_token_gate(item_title_value: str, candidate_title_value: str) -> bool:
    item_words = title_tokens(item_title_value)
    candidate_words = title_tokens(candidate_title_value)
    if not item_words or not candidate_words:
        return True
    important = {word for word in item_words if len(word) >= 4}
    if not important:
        important = item_words
    return bool(important & candidate_words)


def score_candidate(
    item: dict[str, Any],
    candidate: dict[str, Any],
    settings: dict[str, Any],
    index: int = 0,
) -> dict[str, Any]:
    metadata = item_metadata(item)
    weights = settings.get("weights", {})
    matching = settings.get("matching", {})

    title = item_title(item)
    author = item_author(item)
    narrator = first_present(metadata.get("narratorName"), metadata.get("narrators"))

    candidate_title = candidate.get("title") or ""
    candidate_author_value = candidate_author(candidate)
    candidate_narrator_value = candidate_narrator(candidate)
    item_series = item_series_entries(item)
    result_series = candidate_series_entries(candidate)
    item_year = metadata.get("publishedYear")
    candidate_year = candidate.get("publishedYear")
    item_duration = item.get("media", {}).get("duration")
    candidate_duration = candidate.get("duration")
    title_score = best_title_score(item, candidate)
    parts: dict[str, float | None] = {
        "title": title_score,
        "author": (
            best_people_ratio(author, candidate_author_value)
            if author and candidate_author_value
            else None
        ),
        "series": best_series_score(item_series, result_series),
        "narrator": (
            best_people_ratio(narrator, candidate_narrator_value)
            if narrator and candidate_narrator_value
            else None
        ),
        "year": (
            year_score(item_year, candidate_year)
            if item_year and candidate_year
            else None
        ),
        "duration": (
            duration_score(
                item_duration,
                candidate_duration,
                int(matching.get("durationToleranceMinutes", 7)),
            )
            if item_duration and candidate_duration
            else None
        ),
    }

    searched_title = str(
        (candidate.get("_absidekickSearch") or {}).get("queryTitle") or title
    )
    if matching.get("requireTitleToken", True) and not title_token_gate(
        searched_title, str(candidate_title)
    ):
        parts["title"] = min(float(parts["title"] or 0), 35.0)

    if (
        matching.get("requireAuthor", False)
        and parts["author"] is not None
        and parts["author"] < 70
    ):
        parts["author"] = 0.0
        parts["title"] = min(float(parts["title"] or 0), 60.0)

    total_weight = 0.0
    weighted = 0.0
    for key, value in parts.items():
        if value is None:
            continue
        weight = float(weights.get(key, 0) or 0)
        if weight <= 0:
            continue
        total_weight += weight
        weighted += value * weight
    score = weighted / total_weight if total_weight else 0.0

    exact_identifiers: list[str] = []
    conflicts: list[str] = []
    for name in ("asin", "isbn"):
        left_identifier = item_identifier(item, name)
        right_identifier = candidate_identifier(candidate, name)
        if left_identifier and right_identifier:
            if left_identifier == right_identifier:
                exact_identifiers.append(name.upper())
            else:
                conflicts.append(f"{name.upper()} differs")
    if exact_identifiers:
        score = max(score, 99.0)

    primary_title_score = max(
        title_similarity(source_title, result_title)
        for source_title in {item_title(item), searched_title}
        for result_title in candidate_title_values(candidate)
    )
    if primary_title_score < 55 <= title_score:
        conflicts.append("folder title and current ABS title disagree")
    if series_sequence_conflict(item_series, result_series):
        conflicts.append("series sequence differs")
    item_collection = bool(COLLECTION_PATTERN.search(" ".join(title_values(item))))
    candidate_collection = bool(
        COLLECTION_PATTERN.search(" ".join(candidate_title_values(candidate)))
    )
    if item_collection != candidate_collection:
        conflicts.append("collection/box-set status differs")
    if parts["duration"] == 0.0:
        conflicts.append("duration differs substantially")

    signals: list[str] = []
    if exact_identifiers:
        signals.append("exact identifier")
    for name, minimum in (
        ("title", 92),
        ("author", 90),
        ("series", 90),
        ("narrator", 90),
        ("year", 80),
        ("duration", 75),
    ):
        value = parts.get(name)
        if value is not None and value >= minimum:
            signals.append(name)

    return {
        "index": index,
        "score": round(score, 2),
        "parts": {
            key: round(value, 2) if value is not None else None
            for key, value in parts.items()
        },
        "evidenceCount": sum(value is not None for value in parts.values()),
        "strongSignals": signals,
        "exactIdentifiers": exact_identifiers,
        "conflicts": conflicts,
        "search": deepcopy(candidate.get("_absidekickSearch") or {}),
        "source": {"title": title, "author": author},
        "candidate": candidate,
    }


def rank_candidates(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    scored = [
        score_candidate(item, candidate, settings, index)
        for index, candidate in enumerate(candidates)
    ]
    scored.sort(
        key=lambda row: (
            row["score"],
            len(row.get("exactIdentifiers") or []),
            len(row.get("strongSignals") or []),
            int(row.get("evidenceCount") or 0),
            -int(row.get("index") or 0),
        ),
        reverse=True,
    )
    return scored


def candidates_represent_same_work(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return true when two scored results are duplicate listings of one work."""

    left_candidate = left.get("candidate") or {}
    right_candidate = right.get("candidate") or {}

    for name in ("asin", "isbn"):
        left_identifier = candidate_identifier(left_candidate, name)
        right_identifier = candidate_identifier(right_candidate, name)
        if left_identifier and left_identifier == right_identifier:
            return True

    left_title = normalize_title(left_candidate.get("title"))
    right_title = normalize_title(right_candidate.get("title"))
    if not left_title or left_title != right_title:
        return False

    left_collection = bool(
        COLLECTION_PATTERN.search(" ".join(candidate_title_values(left_candidate)))
    )
    right_collection = bool(
        COLLECTION_PATTERN.search(" ".join(candidate_title_values(right_candidate)))
    )
    if left_collection != right_collection:
        return False

    if series_sequence_conflict(
        candidate_series_entries(left_candidate),
        candidate_series_entries(right_candidate),
    ):
        return False

    left_author = candidate_author(left_candidate)
    right_author = candidate_author(right_candidate)
    if left_author and right_author:
        return best_people_ratio(left_author, right_author) >= 95
    if not left_author and not right_author:
        return False

    # One provider may omit the author. It is not a competing match when the
    # other result verifies the author and both return the exact same title.
    known_author_score = max(
        float((left.get("parts") or {}).get("author") or 0),
        float((right.get("parts") or {}).get("author") or 0),
    )
    return known_author_score >= 90


def match_decision(
    ranked: list[dict[str, Any]], settings: dict[str, Any]
) -> dict[str, Any]:
    matching = settings.get("matching", {})
    threshold = float(matching.get("threshold", 80))
    review_floor = float(matching.get("reviewFloor", 65))
    strict = bool(matching.get("strictAutoMatch", True))
    title_minimum = float(matching.get("minimumTitleScore", 86))
    author_minimum = float(matching.get("minimumAuthorScore", 78))
    margin_minimum = float(matching.get("minimumWinnerMargin", 6))
    signal_minimum = int(matching.get("minimumStrongSignals", 2))
    policy = {
        "autoThreshold": threshold,
        "reviewFloor": review_floor,
        "strict": strict,
        "minimumTitleScore": title_minimum,
        "minimumAuthorScore": author_minimum,
        "minimumWinnerMargin": margin_minimum,
        "minimumStrongSignals": signal_minimum,
    }
    if not ranked:
        return {
            "action": "unmatched",
            "confidence": "none",
            "margin": 0.0,
            "score": 0.0,
            "scorePassed": False,
            "safetyPassed": False,
            "strongSignalCount": 0,
            "strongSignals": [],
            "equivalentCandidateCount": 0,
            "competingCandidateCount": 0,
            "policy": policy,
            "reasons": ["no metadata candidates returned"],
        }

    best = ranked[0]
    equivalent_candidates = [
        row for row in ranked[1:] if candidates_represent_same_work(best, row)
    ]
    competing_candidates = [
        row for row in ranked[1:] if not candidates_represent_same_work(best, row)
    ]
    runner_score = (
        float(competing_candidates[0]["score"]) if competing_candidates else 0.0
    )
    margin = round(float(best["score"]) - runner_score, 2)
    parts = best.get("parts") or {}
    safety_reasons = list(best.get("conflicts") or [])
    score = float(best["score"])
    strong_signals = list(best.get("strongSignals") or [])

    exact_identifier = bool(best.get("exactIdentifiers"))
    title_score = float(parts.get("title") or 0)
    if title_score < title_minimum and not exact_identifier:
        safety_reasons.append(
            f"title similarity {title_score:g} is below the required {title_minimum:g}"
        )
    author_value = parts.get("author")
    item_has_author = bool(item_author_from_scored(best))
    candidate_has_author = bool(candidate_author(best.get("candidate") or {}))
    if (
        item_has_author
        and candidate_has_author
        and float(author_value or 0) < author_minimum
    ):
        safety_reasons.append(
            "author evidence: similarity "
            f"{float(author_value or 0):g} is below the required "
            f"{author_minimum:g}"
        )
    elif item_has_author and not candidate_has_author and not exact_identifier:
        safety_reasons.append("candidate returned no author to verify")
    if margin < margin_minimum and not exact_identifier:
        safety_reasons.append(
            f"winner margin: lead {margin:g} is below the required {margin_minimum:g}; "
            "the top meaningfully different candidates are too close"
        )
    if len(strong_signals) < signal_minimum and not exact_identifier:
        signal_label = ", ".join(strong_signals) if strong_signals else "none"
        safety_reasons.append(
            f"only {len(strong_signals)} strong signal(s) ({signal_label}); "
            f"{signal_minimum} required"
        )
    quick_match_compatible = matching.get("applyMode") != "quick_match" or bool(
        (best.get("search") or {}).get("quickMatchEligible")
    )
    if not quick_match_compatible:
        safety_reasons.append(
            "candidate requires metadata patch mode because it did not come from "
            "the first precise Audiobookshelf provider result"
        )

    auto_allowed = (
        score >= threshold
        and quick_match_compatible
        and (not strict or not safety_reasons)
    )
    reasons = list(safety_reasons)
    if score < threshold:
        reasons.insert(
            0,
            f"similarity score {score:g} is below the auto-match threshold "
            f"{threshold:g}",
        )
    decision_details = {
        "score": score,
        "scorePassed": score >= threshold,
        "safetyPassed": not safety_reasons,
        "titleScore": title_score,
        "authorScore": (float(author_value) if author_value is not None else None),
        "margin": margin,
        "strongSignalCount": len(strong_signals),
        "strongSignals": strong_signals,
        "equivalentCandidateCount": len(equivalent_candidates),
        "competingCandidateCount": len(competing_candidates),
        "exactIdentifier": exact_identifier,
        "policy": policy,
    }
    if auto_allowed:
        return {
            "action": "auto",
            "confidence": "high",
            "reasons": [],
            **decision_details,
        }
    if score >= review_floor or exact_identifier:
        return {
            "action": "review",
            "confidence": "review",
            "reasons": list(dict.fromkeys(reasons)) or ["below automatic threshold"],
            **decision_details,
        }
    return {
        "action": "unmatched",
        "confidence": "low",
        "reasons": list(dict.fromkeys(reasons)) or ["insufficient matching evidence"],
        **decision_details,
    }


def item_author_from_scored(scored: dict[str, Any]) -> str:
    return str((scored.get("source") or {}).get("author") or "")


def has_any(haystack: list[str], needles: list[str]) -> bool:
    hay = {normalize_text(value) for value in haystack}
    return any(normalize_text(needle) in hay for needle in needles if needle)


def has_all(haystack: list[str], needles: list[str]) -> bool:
    hay = {normalize_text(value) for value in haystack}
    return all(normalize_text(needle) in hay for needle in needles if needle)


def item_authors_for_filter(item: dict[str, Any]) -> list[str]:
    metadata = item_metadata(item)
    authors = split_people(
        first_present(metadata.get("authors"), metadata.get("authorName"))
    )
    return authors


def should_process_item(
    item: dict[str, Any], settings: dict[str, Any]
) -> tuple[bool, str]:
    targeting = settings.get("targeting", {})
    tags_settings = settings.get("tags", {})
    tags = item_tags(item)
    tags_norm = {normalize_text(tag) for tag in tags}
    mode = targeting.get("mode", "unprocessed")
    matched_tag = normalize_text(tags_settings.get("matchedTag"))
    unmatched_tag = normalize_text(tags_settings.get("unmatchedTag"))
    review_tag = normalize_text(tags_settings.get("reviewTag"))

    if targeting.get("skipMissingItems", True) and item.get("isMissing"):
        return False, "missing item"
    if targeting.get("skipInvalidItems", True) and item.get("isInvalid"):
        return False, "invalid item"
    if item.get("mediaType") != "book":
        return False, "not a book"

    if mode == "unprocessed" and ({matched_tag, unmatched_tag, review_tag} & tags_norm):
        return False, "already processed"
    if mode == "unmatched" and unmatched_tag not in tags_norm:
        return False, "not tagged unmatched"
    if mode == "matched" and matched_tag not in tags_norm:
        return False, "not tagged matched"
    if mode == "review" and review_tag not in tags_norm:
        return False, "not tagged review"
    if mode == "not_matched" and matched_tag in tags_norm:
        return False, "tagged matched"

    authors = item_authors_for_filter(item)
    include_authors = targeting.get("includeAuthors") or []
    exclude_authors = targeting.get("excludeAuthors") or []
    if include_authors and not has_any(authors, include_authors):
        return False, "author not included"
    if exclude_authors and has_any(authors, exclude_authors):
        return False, "author excluded"

    include_tags = targeting.get("includeTags") or []
    exclude_tags = targeting.get("excludeTags") or []
    if include_tags:
        if targeting.get("includeTagMode") == "all":
            if not has_all(tags, include_tags):
                return False, "required tags missing"
        elif not has_any(tags, include_tags):
            return False, "required tag missing"
    if exclude_tags and has_any(tags, exclude_tags):
        return False, "excluded tag"

    title_filter = normalize_text(targeting.get("titleContains"))
    if title_filter and title_filter not in normalize_text(item_title(item)):
        return False, "title filter"

    path_filter = normalize_text(targeting.get("pathContains"))
    path_value = normalize_text(
        first_present(item.get("path"), item.get("relPath"), "")
    )
    if path_filter and path_filter not in path_value:
        return False, "path filter"

    metadata = item_metadata(item)
    if (
        targeting.get("missingMetadataOnly")
        and metadata.get("title")
        and item_author(item)
    ):
        return False, "metadata present"
    if targeting.get("missingCoverOnly") and item.get("media", {}).get("coverPath"):
        return False, "cover present"
    if targeting.get("noAsinOnly") and metadata.get("asin"):
        return False, "asin present"
    if targeting.get("noIsbnOnly") and metadata.get("isbn"):
        return False, "isbn present"

    return True, "eligible"


def add_remove_tags(
    existing: list[str], add: list[str] | None = None, remove: list[str] | None = None
) -> list[str]:
    add = [tag for tag in (add or []) if tag]
    remove_norm = {normalize_text(tag) for tag in (remove or []) if tag}
    tags: list[str] = []
    seen: set[str] = set()
    for tag in existing:
        norm = normalize_text(tag)
        if not norm or norm in remove_norm or norm in seen:
            continue
        tags.append(tag)
        seen.add(norm)
    for tag in add:
        norm = normalize_text(tag)
        if norm and norm not in seen:
            tags.append(tag)
            seen.add(norm)
    return tags


def empty_value(value: Any) -> bool:
    return value in (None, "", [], {})


def series_payload(candidate: dict[str, Any]) -> list[dict[str, str | None]]:
    series = candidate.get("series")
    if isinstance(series, list):
        payload = []
        for item in series:
            if isinstance(item, dict) and item.get("name"):
                payload.append(
                    {"name": str(item.get("name")), "sequence": item.get("sequence")}
                )
            elif item:
                payload.append({"name": str(item), "sequence": None})
        return payload
    if isinstance(series, dict) and series.get("name"):
        return [{"name": str(series.get("name")), "sequence": series.get("sequence")}]
    if isinstance(series, str) and series.strip():
        return [{"name": series.strip(), "sequence": None}]
    return []


def candidate_metadata_payload(
    existing_metadata: dict[str, Any],
    candidate: dict[str, Any],
    overwrite: bool,
) -> dict[str, Any]:
    mapped: dict[str, Any] = {
        "title": candidate.get("title"),
        "subtitle": candidate.get("subtitle"),
        "authors": [
            {"name": person} for person in split_people(candidate_author(candidate))
        ],
        "narrators": split_people(candidate_narrator(candidate)),
        "series": series_payload(candidate),
        "genres": candidate.get("genres")
        if isinstance(candidate.get("genres"), list)
        else split_people(candidate.get("genres")),
        "publishedYear": candidate.get("publishedYear"),
        "publishedDate": candidate.get("publishedDate"),
        "publisher": candidate.get("publisher"),
        "description": candidate.get("description"),
        "isbn": candidate.get("isbn"),
        "asin": candidate.get("asin"),
        "language": candidate.get("language"),
    }
    if "explicit" in candidate:
        mapped["explicit"] = bool(candidate.get("explicit"))

    payload: dict[str, Any] = {}
    for key, value in mapped.items():
        if empty_value(value):
            continue
        existing_value = existing_metadata.get(key)
        if key == "authors" and existing_metadata.get("authorName"):
            existing_value = existing_metadata.get("authorName")
        if key == "narrators" and existing_metadata.get("narratorName"):
            existing_value = existing_metadata.get("narratorName")
        if key == "series" and existing_metadata.get("seriesName"):
            existing_value = existing_metadata.get("seriesName")
        if overwrite or empty_value(existing_value):
            payload[key] = value
    return payload


class ABSAPIError(RuntimeError):
    def __init__(
        self, message: str, status: int | None = None, body: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _google_error_message(status: int, body: str) -> str:
    detail = ""
    try:
        payload = json.loads(body)
        detail = str((payload.get("error") or {}).get("message") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        detail = ""
    if status in {401, 403}:
        guidance = (
            "Google rejected the API key. Enable the Books API and check the "
            "key's API and IP restrictions."
        )
    elif status == 429:
        guidance = "Google Books quota is exhausted or temporarily rate-limited."
    elif status == 400:
        guidance = "Google rejected the Books API request or API key."
    else:
        guidance = f"Google Books returned HTTP {status}."
    return f"{guidance}{f' Google says: {detail}' if detail else ''}"


def google_error_is_transient(error: ABSAPIError) -> bool:
    return error.status is None or error.status in GOOGLE_TRANSIENT_STATUSES


def _google_books_payload(
    api_key: str,
    query: str,
    *,
    limit: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not api_key:
        raise ABSAPIError(
            "Native Google Books is disabled: add and test an API key in "
            "ABSidekick Config before selecting Google."
        )
    params = urllib.parse.urlencode(
        {
            "q": query,
            "key": api_key,
            "maxResults": max(1, min(40, int(limit))),
            "printType": "books",
            "projection": "full",
        }
    )
    request = urllib.request.Request(
        f"https://www.googleapis.com/books/v1/volumes?{params}",
        headers={"Accept": "application/json", "User-Agent": "MyAnonaSuite/ABSidekick"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=max(3, min(60, int(timeout_seconds)))
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise ABSAPIError(
            _google_error_message(error.code, body), error.code, body
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ABSAPIError(f"Google Books request failed: {error}") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ABSAPIError("Google Books returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ABSAPIError("Google Books returned an invalid response")
    if payload.get("error"):
        body = json.dumps(payload)
        raise ABSAPIError(_google_error_message(400, body), 400, body)
    return payload


def _google_image_url(image_links: Any) -> str | None:
    if not isinstance(image_links, dict):
        return None
    for name in (
        "extraLarge",
        "large",
        "medium",
        "small",
        "thumbnail",
        "smallThumbnail",
    ):
        value = image_links.get(name)
        if value:
            return str(value).replace("http://", "https://", 1)
    return None


def _google_isbn(identifiers: Any) -> str | None:
    if not isinstance(identifiers, list):
        return None
    by_type = {
        str(row.get("type") or ""): str(row.get("identifier") or "")
        for row in identifiers
        if isinstance(row, dict)
    }
    return by_type.get("ISBN_13") or by_type.get("ISBN_10") or None


def _google_candidate(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not isinstance(item.get("volumeInfo"), dict):
        return None
    info = item["volumeInfo"]
    title = str(info.get("title") or "").strip()
    if not title:
        return None
    authors = info.get("authors") or []
    return {
        "id": item.get("id"),
        "title": title,
        "subtitle": info.get("subtitle"),
        "author": ", ".join(str(author) for author in authors) if authors else None,
        "publisher": info.get("publisher"),
        "publishedYear": str(info.get("publishedDate") or "")[:4] or None,
        "publishedDate": info.get("publishedDate"),
        "description": info.get("description"),
        "cover": _google_image_url(info.get("imageLinks")),
        "genres": info.get("categories")
        if isinstance(info.get("categories"), list)
        else None,
        "isbn": _google_isbn(info.get("industryIdentifiers")),
        "language": info.get("language"),
    }


def search_google_books(
    api_key: str,
    title: str,
    author: str = "",
    *,
    limit: int = 10,
    timeout_seconds: int = 12,
) -> list[dict[str, Any]]:
    query_parts = [f"intitle:{title}"]
    if author:
        query_parts.append(f"inauthor:{author}")
    payload = _google_books_payload(
        api_key,
        " ".join(query_parts),
        limit=limit,
        timeout_seconds=timeout_seconds,
    )
    candidates = [_google_candidate(item) for item in payload.get("items") or []]
    return [candidate for candidate in candidates if candidate is not None]


def test_google_books_api_key(
    api_key: str, *, timeout_seconds: int = 12
) -> dict[str, Any]:
    for attempt in range(2):
        try:
            payload = _google_books_payload(
                api_key,
                "isbn:9780547928227",
                limit=1,
                timeout_seconds=timeout_seconds,
            )
            break
        except ABSAPIError as error:
            if google_error_is_transient(error) and attempt == 0:
                time.sleep(0.5)
                continue
            raise
    return {
        "valid": True,
        "sampleResults": len(payload.get("items") or []),
        "message": "Google Books API key tested successfully.",
    }


class ABSClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_seconds: int = 30,
        max_retries: int = 2,
        request_delay_ms: int = 150,
        google_books_api_key: str = "",
        google_books_ready: bool = False,
    ) -> None:
        if not base_url:
            raise ValueError("Audiobookshelf base URL is required")
        if not token:
            raise ValueError("Audiobookshelf API token is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.request_delay_ms = request_delay_ms
        self.google_books_api_key = google_books_api_key
        self.google_books_ready = google_books_ready
        self.disabled_search_providers: dict[str, str] = {}
        self.transient_search_failures: dict[str, int] = {}

    def url(self, path: str, params: dict[str, Any] | None = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        query = ""
        if params:
            clean_params = {
                key: value
                for key, value in params.items()
                if value is not None and value != "" and value != []
            }
            query = urllib.parse.urlencode(clean_params, doseq=True)
        return f"{self.base_url}{path}" + (f"?{query}" if query else "")

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> Any:
        url = self.url(path, params)
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request_timeout = max(1, int(timeout_seconds or self.timeout_seconds))
        retry_count = self.max_retries if max_retries is None else max(0, max_retries)
        last_error: Exception | None = None
        for attempt in range(retry_count + 1):
            if self.request_delay_ms > 0:
                time.sleep(self.request_delay_ms / 1000.0)
            request = urllib.request.Request(
                url, data=data, headers=headers, method=method.upper()
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=request_timeout
                ) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    content_type = response.headers.get("Content-Type", "")
                    if "json" in content_type or raw[:1] in (b"{", b"["):
                        return json.loads(raw.decode("utf-8"))
                    return raw.decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                body_text = error.read().decode("utf-8", errors="replace")
                if error.code in {429, 500, 502, 503, 504} and attempt < retry_count:
                    time.sleep(min(8, 2**attempt))
                    last_error = error
                    continue
                raise ABSAPIError(
                    f"ABS API returned HTTP {error.code} for {path}",
                    error.code,
                    body_text,
                ) from error
            except (urllib.error.URLError, TimeoutError) as error:
                if "WRONG_VERSION_NUMBER" in str(error):
                    raise ABSAPIError(
                        "SSL failed while connecting to Audiobookshelf. Your "
                        "ABS URL probably uses https:// for a server that only "
                        "speaks http://. Try changing the ABS URL to http://...",
                    ) from error
                last_error = error
                if attempt < retry_count:
                    time.sleep(min(8, 2**attempt))
                    continue
                raise ABSAPIError(
                    f"ABS API request failed for {path}: {error}"
                ) from error
        raise ABSAPIError(f"ABS API request failed for {path}: {last_error}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def search_books(
        self, params: dict[str, Any], timeout_seconds: int
    ) -> list[dict[str, Any]]:
        """Run one provider search with a small Google transient-error budget."""

        provider = str(params.get("provider") or "")
        if provider in self.disabled_search_providers:
            return []
        try:
            if provider == "google":
                if not self.google_books_ready:
                    raise ABSAPIError(
                        "Native Google Books is disabled: add and successfully "
                        "test an API key in ABSidekick Config first. No Google "
                        "request was sent."
                    )
                for attempt in range(2):
                    try:
                        result = search_google_books(
                            self.google_books_api_key,
                            str(params.get("title") or ""),
                            str(params.get("author") or ""),
                            limit=int(params.get("limit") or 10),
                            timeout_seconds=timeout_seconds,
                        )
                        break
                    except ABSAPIError as error:
                        if google_error_is_transient(error) and attempt == 0:
                            time.sleep(0.5)
                            continue
                        raise
            else:
                result = self.request(
                    "GET",
                    "/api/search/books",
                    params=params,
                    timeout_seconds=timeout_seconds,
                    max_retries=0,
                )
        except ABSAPIError as error:
            if provider != "google" or not google_error_is_transient(error):
                self.disabled_search_providers[provider] = str(error)
            else:
                failures = self.transient_search_failures.get(provider, 0) + 1
                self.transient_search_failures[provider] = failures
                if failures >= GOOGLE_TRANSIENT_FAILURE_LIMIT:
                    self.disabled_search_providers[provider] = (
                        f"{error} ({failures} consecutive transient failures)"
                    )
            raise
        self.transient_search_failures.pop(provider, None)
        return result if isinstance(result, list) else []

    def post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        return self.request("POST", path, params=params, body=body)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PATCH", path, body=body)


def create_client(settings: dict[str, Any], token: str | None = None) -> ABSClient:
    connection = settings.get("connection", {})
    run = settings.get("run", {})
    providers = settings.get("providers", {})
    token = token or connection.get("token") or ""
    return ABSClient(
        base_url=connection.get("baseUrl", ""),
        token=token,
        timeout_seconds=int(run.get("timeoutSeconds", 30)),
        max_retries=int(run.get("maxRetries", 2)),
        request_delay_ms=int(run.get("requestDelayMs", 150)),
        google_books_api_key=str(providers.get("googleBooksApiKey") or ""),
        google_books_ready=google_books_key_is_ready(settings),
    )


def fetch_library_items(
    client: ABSClient, settings: dict[str, Any]
) -> list[dict[str, Any]]:
    connection = settings.get("connection", {})
    run = settings.get("run", {})
    library_id = connection.get("libraryId")
    if not library_id:
        raise ValueError("Select a library before running a job")

    page_size = max(1, int(run.get("pageSize", 100)))
    limit = max(0, int(run.get("limit", 0)))
    page = 0
    items: list[dict[str, Any]] = []
    while True:
        payload = client.get(
            f"/api/libraries/{urllib.parse.quote(str(library_id))}/items",
            params={
                "limit": page_size,
                "page": page,
                "sort": run.get("sort") or "media.metadata.title",
                "desc": 1 if run.get("sortDesc") else 0,
                "minified": 0,
                "collapseseries": 0,
            },
        )
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not results:
            break
        for item in results:
            ok, _reason = should_process_item(item, settings)
            if ok:
                items.append(item)
                if limit and len(items) >= limit:
                    return items
        total = int(payload.get("total", 0) or 0)
        page += 1
        if page * page_size >= total:
            break
    return items


class CandidateResults(list[dict[str, Any]]):
    def __init__(
        self,
        values: list[dict[str, Any]] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(values or [])
        self.diagnostics = diagnostics or []
        self.attempts = attempts or []


def _series_prefix_title(
    value: Any,
    series_entries: list[tuple[str, str]] | None = None,
    *,
    require_evidence: bool,
) -> str:
    def normalized_sequence(value: Any) -> str:
        normalized = normalize_text(value)
        if re.fullmatch(r"\d+(?:\.\d+)?", normalized):
            whole, dot, fraction = normalized.partition(".")
            fraction = fraction.rstrip("0")
            return f"{int(whole)}{dot}{fraction}" if fraction else str(int(whole))
        return normalized

    title = html.unescape(str(value or "")).strip()
    match = re.match(
        r"^\s*(?P<series>.{1,80}?)\s+"
        r"(?:(?:book|volume|vol)\s*#?\s*|#\s*)?"
        r"(?P<sequence>\d{1,3}(?:\.\d+)?|[ivxlcdm]{1,8})\s*"
        r"(?:-|–|—|:|\|)\s*(?P<title>.+?)\s*$",
        title,
        flags=re.IGNORECASE,
    )
    if not match:
        return title

    series_name = match.group("series").strip()
    sequence = normalized_sequence(match.group("sequence"))
    remainder = match.group("title").strip()
    remainder_tokens = title_tokens(remainder)
    if not remainder_tokens:
        return title

    metadata_match = False
    for known_name, known_sequence in series_entries or []:
        normalized_known_sequence = normalized_sequence(known_sequence)
        name_matches = title_similarity(series_name, known_name) >= 88
        sequence_matches = (
            not normalized_known_sequence or normalized_known_sequence == sequence
        )
        if name_matches and sequence_matches:
            metadata_match = True
            break

    series_words = {
        word
        for word in title_tokens(series_name)
        if len(word) >= 4 and word not in {"saga", "series", "volume"}
    }
    repeated_series_word = bool(series_words & title_tokens(remainder))
    if len(remainder_tokens) < 2 and not metadata_match:
        return title
    if require_evidence and not (metadata_match or repeated_series_word):
        return title
    return remainder


def clean_search_title(
    value: Any, series_entries: list[tuple[str, str]] | None = None
) -> str:
    title = html.unescape(str(value or "")).strip()
    # Track/disc numbering is common in folder names but poisons ABS provider
    # searches (for example, "01 Northern Lights"). Do not touch titles that
    # are themselves a number, such as "1984".
    number_cleaned = re.sub(
        r"^\s*(?:(?:book|disc|disk|track|cd)\s*)?0*\d{1,3}\s*[-_.:]?\s+",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    if number_cleaned and number_cleaned != title:
        return number_cleaned
    return _series_prefix_title(
        title,
        series_entries,
        require_evidence=True,
    )


def _search_books_once(
    client: ABSClient,
    params: dict[str, Any],
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], Exception | None]:
    provider = str(params.get("provider") or "unknown")
    if isinstance(client, ABSClient):
        if provider in client.disabled_search_providers:
            return [], None
        try:
            return client.search_books(params, timeout_seconds), None
        except ABSAPIError as error:
            return [], error
    try:
        results = client.get("/api/search/books", params=params)
    except Exception as error:  # noqa: BLE001 - provider failure is recoverable
        return [], error
    return (
        [candidate for candidate in results if isinstance(candidate, dict)]
        if isinstance(results, list)
        else [],
        None,
    )


def _search_failure_message(
    client: ABSClient,
    provider: str,
    *,
    fallback: bool,
) -> str:
    label = "Google Books" if provider == "google" else provider
    operation = "fallback" if fallback else "metadata search"
    if not isinstance(client, ABSClient):
        return f"{label} {operation} failed for this item"
    if provider in client.disabled_search_providers:
        failures = client.transient_search_failures.get(provider, 0)
        if provider == "google" and failures >= GOOGLE_TRANSIENT_FAILURE_LIMIT:
            return (
                f"{label} {operation} disabled for the rest of this run after "
                f"{failures} consecutive transient failures"
            )
        return (
            f"{label} {operation} disabled for the rest of this run because "
            "Google rejected the request or provider configuration"
        )
    failures = client.transient_search_failures.get(provider, 0)
    if provider == "google" and failures:
        return (
            f"{label} {operation} failed for this item; it will retry on the "
            f"next item ({failures}/{GOOGLE_TRANSIENT_FAILURE_LIMIT} transient "
            "failures)"
        )
    return f"{label} {operation} failed for this item"


def search_candidates(
    client: ABSClient, item: dict[str, Any], settings: dict[str, Any]
) -> CandidateResults:
    connection = settings.get("connection", {})
    matching = settings.get("matching", {})
    title = item_title(item)
    author = item_author(item)
    candidate_limit = max(1, int(matching.get("candidateLimit", 8)))
    primary_provider = str(connection.get("provider") or "audible")
    adaptive = bool(matching.get("adaptiveSearch", True))
    search_timeout = max(
        3, min(60, int(settings.get("run", {}).get("searchTimeoutSeconds", 12)))
    )

    series_entries = item_series_entries(item)
    clean_title = clean_search_title(title, series_entries)
    possible_series_title = _series_prefix_title(
        title,
        series_entries,
        require_evidence=False,
    )
    title_was_cleaned = normalize_title(clean_title) != normalize_title(title)
    has_series_prefix_fallback = not title_was_cleaned and normalize_title(
        possible_series_title
    ) != normalize_title(title)
    broad_title = possible_series_title if has_series_prefix_fallback else clean_title
    primary_queries: list[tuple[str, str, str, str, bool]] = [
        (
            primary_provider,
            clean_title if title_was_cleaned else title,
            author,
            "parsed title + author" if title_was_cleaned else "precise title + author",
            False,
        ),
    ]
    if adaptive:
        if title_was_cleaned:
            primary_queries.append(
                (
                    primary_provider,
                    title,
                    author,
                    "original unparsed title + author",
                    True,
                )
            )
        elif has_series_prefix_fallback:
            primary_queries.append(
                (
                    primary_provider,
                    possible_series_title,
                    author,
                    "possible series-prefix title + author",
                    True,
                )
            )
        primary_queries.append(
            (primary_provider, broad_title, "", "parsed title only", True)
        )

    configured_fallbacks = matching.get("fallbackProviders") or []
    if isinstance(configured_fallbacks, str):
        configured_fallbacks = [
            value.strip() for value in configured_fallbacks.split(",") if value.strip()
        ]
    second_pass_providers: list[str] = []
    google_ready = google_books_key_is_ready(settings)
    if google_ready and primary_provider != "google":
        # Google is an automatic second pass, not an optional fallback. It runs
        # immediately whenever the primary ABS provider cannot auto-match.
        second_pass_providers.append("google")
    if adaptive and bool(matching.get("automaticFallbackProviders", False)):
        for configured_provider in configured_fallbacks:
            provider = str(configured_provider).strip()
            if (
                provider in PROVIDERS
                and provider != primary_provider
                and (provider != "google" or google_ready)
                and provider not in second_pass_providers
            ):
                second_pass_providers.append(provider)

    candidates: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    seen_queries: set[tuple[str, str, str]] = set()
    for provider, query_title, query_author, strategy, only_if_empty in primary_queries:
        if only_if_empty and candidates:
            continue
        query_key = (
            provider,
            normalize_title(query_title),
            normalize_text(query_author),
        )
        if query_key in seen_queries or not query_key[1]:
            continue
        seen_queries.add(query_key)
        params = {
            "title": query_title,
            "author": query_author,
            "provider": provider,
            "limit": candidate_limit,
        }
        was_disabled = (
            isinstance(client, ABSClient)
            and provider in client.disabled_search_providers
        )
        results, error = _search_books_once(client, params, search_timeout)
        attempt = {
            "provider": provider,
            "stage": "ABS primary search",
            "strategy": strategy,
            "queryTitle": query_title,
            "queryAuthor": query_author,
            "resultCount": len(results),
            "status": (
                "disabled"
                if was_disabled
                else "error"
                if error
                else "results"
                if results
                else "no_results"
            ),
        }
        if error:
            attempt["error"] = str(error)
            attempt["message"] = _search_failure_message(
                client, provider, fallback=False
            )
            attempts.append(attempt)
            diagnostics.append(
                {
                    "provider": provider,
                    "strategy": strategy,
                    "error": str(error),
                    "message": _search_failure_message(
                        client, provider, fallback=False
                    ),
                }
            )
            continue
        if was_disabled:
            attempt["message"] = _search_failure_message(
                client, provider, fallback=False
            )
        attempts.append(attempt)
        for provider_rank, candidate in enumerate(results[:candidate_limit]):
            if not isinstance(candidate, dict):
                continue
            candidate = deepcopy(candidate)
            candidate["_absidekickSearch"] = {
                "provider": provider,
                "strategy": strategy,
                "queryTitle": query_title,
                "originalTitle": title,
                "providerRank": provider_rank + 1,
                "quickMatchEligible": (
                    provider == primary_provider
                    and strategy in {"precise title + author", "parsed title + author"}
                    and provider_rank == 0
                ),
            }
            identity = candidate_identity(candidate)
            if identity not in candidates:
                candidates[identity] = candidate

        if candidates:
            break

    primary_ranked = rank_candidates(item, list(candidates.values()), settings)
    if match_decision(primary_ranked, settings)["action"] == "auto":
        return CandidateResults(
            [row["candidate"] for row in primary_ranked[:candidate_limit]],
            diagnostics,
            attempts,
        )

    if primary_provider != "google" and not google_ready:
        attempts.append(
            {
                "provider": "google",
                "stage": "Google second pass",
                "strategy": "after ABS did not auto-match",
                "queryTitle": broad_title,
                "queryAuthor": author,
                "resultCount": 0,
                "status": "skipped",
                "message": "Google Books skipped: add and test an API key in Providers",
            }
        )

    for provider in second_pass_providers:
        query_key = (provider, normalize_title(broad_title), normalize_text(author))
        if query_key in seen_queries or not query_key[1]:
            continue
        seen_queries.add(query_key)
        strategy = (
            "immediate Google second pass after no confident Audiobookshelf match "
            "(ABS did not auto-match)"
            if provider == "google"
            else "fallback title + author"
        )
        params = {
            "title": broad_title,
            "author": author,
            "provider": provider,
            "limit": candidate_limit,
        }
        was_disabled = (
            isinstance(client, ABSClient)
            and provider in client.disabled_search_providers
        )
        results, error = _search_books_once(client, params, search_timeout)
        attempt = {
            "provider": provider,
            "stage": (
                "Google second pass"
                if provider == "google"
                else "additional provider fallback"
            ),
            "strategy": strategy,
            "queryTitle": broad_title,
            "queryAuthor": author,
            "resultCount": len(results),
            "status": (
                "disabled"
                if was_disabled
                else "error"
                if error
                else "results"
                if results
                else "no_results"
            ),
        }
        if error:
            attempt["error"] = str(error)
            attempt["message"] = _search_failure_message(
                client, provider, fallback=True
            )
            attempts.append(attempt)
            diagnostics.append(
                {
                    "provider": provider,
                    "strategy": strategy,
                    "error": str(error),
                    "message": _search_failure_message(client, provider, fallback=True),
                }
            )
            continue
        if was_disabled:
            attempt["message"] = _search_failure_message(
                client, provider, fallback=True
            )
        attempts.append(attempt)

        fallback_candidates: list[dict[str, Any]] = []
        for provider_rank, candidate in enumerate(results[:candidate_limit]):
            if not isinstance(candidate, dict):
                continue
            candidate = deepcopy(candidate)
            candidate["_absidekickSearch"] = {
                "provider": provider,
                "strategy": strategy,
                "queryTitle": broad_title,
                "originalTitle": title,
                "providerRank": provider_rank + 1,
                "quickMatchEligible": False,
            }
            fallback_candidates.append(candidate)

        fallback_ranked = rank_candidates(item, fallback_candidates, settings)
        if match_decision(fallback_ranked, settings)["action"] == "auto":
            return CandidateResults(
                [row["candidate"] for row in fallback_ranked[:candidate_limit]],
                diagnostics,
                attempts,
            )
        for candidate in fallback_candidates:
            identity = candidate_identity(candidate)
            if identity not in candidates:
                candidates[identity] = candidate

    ranked = rank_candidates(item, list(candidates.values()), settings)
    return CandidateResults(
        [row["candidate"] for row in ranked[:candidate_limit]],
        diagnostics,
        attempts,
    )


def candidate_identity(candidate: dict[str, Any]) -> str:
    for name in ("asin", "isbn", "id", "bookId", "audibleId"):
        value = normalize_identifier(candidate.get(name))
        if value:
            return f"{name}:{value}"
    return "|".join(
        (
            normalize_title(candidate.get("title")),
            normalize_text(candidate_author(candidate)),
            normalize_identifier(candidate.get("publishedYear")),
            normalize_identifier(candidate.get("duration")),
        )
    )


def search_review_candidates(
    client: ABSClient,
    item_id: str,
    query: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Run a reviewer-controlled ABS metadata search for one library item."""

    if not isinstance(query, dict):
        raise ValueError("query must be an object")
    title = str(query.get("title") or "").strip()
    author = str(query.get("author") or "").strip()
    provider = str(
        query.get("provider")
        or settings.get("connection", {}).get("provider")
        or "audible"
    ).strip()
    try:
        limit = int(query.get("limit") or 20)
    except (TypeError, ValueError) as error:
        raise ValueError("manual search limit must be a number") from error

    if not title and not author:
        raise ValueError("enter a title or author to search")
    if len(title) > 300 or len(author) > 200:
        raise ValueError("manual search terms are too long")
    if provider not in PROVIDERS:
        raise ValueError(f"unknown Audiobookshelf metadata provider: {provider}")
    if not 1 <= limit <= 30:
        raise ValueError("manual search limit must be between 1 and 30")

    item = get_library_item(client, item_id)
    results, search_error = _search_books_once(
        client,
        {
            "title": title,
            "author": author,
            "provider": provider,
            "limit": limit,
        },
        max(
            3,
            min(
                60,
                int(settings.get("run", {}).get("searchTimeoutSeconds", 12)),
            ),
        ),
    )
    if search_error:
        raise ValueError(f"{provider} metadata search failed: {search_error}")
    scoring_item = deepcopy(item)
    scoring_media = scoring_item.setdefault("media", {})
    if not isinstance(scoring_media, dict):
        scoring_media = {}
        scoring_item["media"] = scoring_media
    scoring_metadata = scoring_media.setdefault("metadata", {})
    if not isinstance(scoring_metadata, dict):
        scoring_metadata = {}
        scoring_media["metadata"] = scoring_metadata
    scoring_metadata["title"] = title
    scoring_metadata["authorName"] = author
    scoring_metadata.pop("authors", None)
    ranked = rank_candidates(
        scoring_item,
        [candidate for candidate in results if isinstance(candidate, dict)][:limit],
        settings,
    )
    candidates = [
        {
            **scored,
            "searchSource": "manual",
            "searchProvider": provider,
        }
        for scored in ranked
    ]
    decision = match_decision(ranked, settings)
    best_candidate = candidates[0] if candidates else None
    if decision["action"] == "auto":
        outcome_message = "This result passes the automatic matching policy."
    elif candidates:
        outcome_message = (
            "No result passed the automatic matching policy. Review the candidates "
            "and approve one manually, or reject this item."
        )
    else:
        outcome_message = (
            "No metadata candidates were returned. Change the search terms or "
            "provider, or reject this item."
        )
    return {
        "item": summarize_item(item),
        "query": {
            "title": title,
            "author": author,
            "provider": provider,
            "limit": limit,
        },
        "candidates": candidates,
        "resultCount": len(candidates),
        "decision": decision,
        "manualMatch": {
            "status": decision["action"],
            "isConfidentMatch": decision["action"] == "auto",
            "requiresReview": decision["action"] != "auto",
            "message": outcome_message,
            "bestCandidate": best_candidate,
            "scoredAgainst": {"title": title, "author": author},
        },
    }


def get_library_item(client: ABSClient, item_id: str) -> dict[str, Any]:
    payload = client.get(f"/api/items/{urllib.parse.quote(str(item_id))}")
    if isinstance(payload, dict) and isinstance(payload.get("libraryItem"), dict):
        return payload["libraryItem"]
    if isinstance(payload, dict) and payload.get("id"):
        return payload
    raise ValueError(f"Could not load library item {item_id}")


def summarize_item(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item_metadata(item)
    item_id = item.get("id")
    return {
        "id": item_id,
        "title": item_title(item),
        "author": item_author(item),
        "series": first_present(metadata.get("seriesName"), metadata.get("series"), ""),
        "narrator": first_present(
            metadata.get("narratorName"), metadata.get("narrators"), ""
        ),
        "year": metadata.get("publishedYear"),
        "asin": metadata.get("asin"),
        "isbn": metadata.get("isbn"),
        "publisher": metadata.get("publisher"),
        "description": metadata.get("description"),
        "tags": item_tags(item),
        "path": first_present(item.get("path"), item.get("relPath"), ""),
        "duration": item.get("media", {}).get("duration"),
        "coverPath": item.get("media", {}).get("coverPath"),
        "coverUrl": (
            f"/api/absidekick/item-cover/{urllib.parse.quote(str(item_id))}"
            if item_id
            else ""
        ),
    }


def build_review_row(
    item: dict[str, Any],
    ranked: list[dict[str, Any]],
    settings: dict[str, Any],
    search_diagnostics: list[dict[str, Any]] | None = None,
    search_attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    review_settings = settings.get("review", {})
    candidate_limit = max(1, int(review_settings.get("candidateLimit", 6)))
    return {
        "item": summarize_item(item),
        "candidates": ranked[:candidate_limit],
        "decision": match_decision(ranked, settings),
        "searchDiagnostics": search_diagnostics or [],
        "searchAttempts": search_attempts or [],
        "createdAt": utc_now(),
    }


def scan_review_items(
    client: ABSClient,
    settings: dict[str, Any],
    limit: int | None = None,
    excluded_ids: set[str] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    scan_settings = deepcopy(settings)
    scan_settings.setdefault("targeting", {})
    scan_settings.setdefault("run", {})
    scan_settings.setdefault("matching", {})
    scan_limit = int(limit or scan_settings.get("review", {}).get("scanLimit", 25))
    excluded_ids = {str(item_id) for item_id in (excluded_ids or set())}
    scan_settings["targeting"]["mode"] = "review"
    scan_settings["run"]["dryRun"] = True
    scan_settings["run"]["limit"] = 0
    scan_settings["matching"]["candidateLimit"] = int(
        scan_settings.get("review", {}).get("candidateLimit", 6)
    )
    if progress:
        progress(
            {
                "phase": "loading",
                "detail": "Loading review-tagged items from Audiobookshelf…",
                "current": 0,
                "total": 0,
                "currentTitle": "",
            }
        )
    items = fetch_library_items(client, scan_settings)
    scan_items = [item for item in items if str(item.get("id")) not in excluded_ids][
        :scan_limit
    ]
    if progress:
        progress(
            {
                "phase": "searching",
                "detail": (
                    f"Loaded {len(items)} review-tagged item(s); searching "
                    f"metadata for {len(scan_items)} pending item(s)."
                ),
                "current": 0,
                "total": len(scan_items),
                "currentTitle": "",
            }
        )
    rows = []
    for index, item in enumerate(scan_items, start=1):
        title = item_title(item)
        if progress:
            progress(
                {
                    "phase": "searching",
                    "detail": f"Searching providers for {title}",
                    "current": index - 1,
                    "total": len(scan_items),
                    "currentTitle": title,
                }
            )
        candidates = search_candidates(client, item, scan_settings)
        ranked = rank_candidates(item, candidates, scan_settings)
        rows.append(
            build_review_row(
                item,
                ranked,
                scan_settings,
                candidates.diagnostics,
                candidates.attempts,
            )
        )
        if progress:
            progress(
                {
                    "phase": "searching",
                    "detail": f"Finished {title}",
                    "current": index,
                    "total": len(scan_items),
                    "currentTitle": title,
                }
            )
        primary_provider = str(
            scan_settings.get("connection", {}).get("provider") or "audible"
        )
        if (
            isinstance(client, ABSClient)
            and primary_provider in client.disabled_search_providers
            and not ranked
        ):
            break
    return {"totalReviewItems": len(items), "rows": rows}


def patch_item_tags(client: ABSClient, item: dict[str, Any], tags: list[str]) -> Any:
    return client.patch(
        f"/api/items/{urllib.parse.quote(str(item['id']))}/media", {"tags": tags}
    )


def writable_review_settings(settings: dict[str, Any]) -> dict[str, Any]:
    writable = deepcopy(settings)
    writable.setdefault("run", {})
    writable["run"]["dryRun"] = False
    return writable


def approve_review_candidate(
    client: ABSClient,
    item_id: str,
    scored_candidate: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    if not scored_candidate or not isinstance(scored_candidate.get("candidate"), dict):
        raise ValueError("A candidate is required to approve a review match")
    item = get_library_item(client, item_id)
    writable = writable_review_settings(settings)
    writable.setdefault("matching", {})
    if writable["matching"].get("applyMode") == "quick_match":
        writable["matching"]["applyMode"] = "metadata_patch"
    return apply_match(client, item, scored_candidate, writable)


def reject_review_item(
    client: ABSClient, item_id: str, settings: dict[str, Any]
) -> list[str]:
    item = get_library_item(client, item_id)
    review_settings = settings.get("review", {})
    tags_settings = settings.get("tags", {})
    add_tags: list[str] = []
    remove_tags: list[str] = []
    if review_settings.get("rejectAddsUnmatchedTag", True):
        add_tags.append(
            tags_settings.get("unmatchedTag", "ABSidekick: AutoMatch Unmatched")
        )
    if review_settings.get("rejectClearsReviewTag", True):
        remove_tags.append(tags_settings.get("reviewTag", "ABSidekick: Needs Review"))
    if tags_settings.get("clearMatchedOnUnmatched", False):
        remove_tags.append(tags_settings.get("matchedTag", "ABSidekick: AutoMatched"))
    new_tags = add_remove_tags(item_tags(item), add=add_tags, remove=remove_tags)
    patch_item_tags(client, item, new_tags)
    return new_tags


def apply_match(
    client: ABSClient,
    item: dict[str, Any],
    scored: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    matching = settings.get("matching", {})
    tags_settings = settings.get("tags", {})
    candidate = scored["candidate"]
    apply_mode = matching.get("applyMode", "metadata_patch")
    item_id = urllib.parse.quote(str(item["id"]))

    existing_tags = item_tags(item)
    remove_tags: list[str] = []
    add_tags: list[str] = []
    if tags_settings.get("tagMatched", True):
        add_tags.append(tags_settings.get("matchedTag", "ABSidekick: AutoMatched"))
    if tags_settings.get("clearUnmatchedOnMatch", True):
        remove_tags.append(
            tags_settings.get("unmatchedTag", "ABSidekick: AutoMatch Unmatched")
        )
    if tags_settings.get("clearReviewOnMatch", True):
        remove_tags.append(tags_settings.get("reviewTag", "ABSidekick: Needs Review"))
    new_tags = add_remove_tags(existing_tags, add=add_tags, remove=remove_tags)

    if settings.get("run", {}).get("dryRun", True):
        return {"updated": False, "dryRun": True, "tags": new_tags}

    if apply_mode == "quick_match":
        search = candidate.get("_absidekickSearch") or {}
        if matching.get("quickMatchFirstResultOnly", True) and not search.get(
            "quickMatchEligible"
        ):
            raise ABSAPIError(
                "Quick match mode is limited to the first result from an exact "
                "or evidence-backed parsed primary-provider search for safety"
            )
        client.post(
            f"/api/items/{item_id}/match",
            params={
                "provider": settings.get("connection", {}).get("provider") or "google",
                "title": search.get("queryTitle") or item_title(item),
                "author": item_author(item),
                "overrideDefaults": "true"
                if matching.get("overwriteMetadata")
                else "false",
            },
        )
        return patch_item_tags(client, item, new_tags) or {
            "updated": True,
            "tags": new_tags,
        }

    if apply_mode == "tags_only":
        return patch_item_tags(client, item, new_tags) or {
            "updated": True,
            "tags": new_tags,
        }

    payload: dict[str, Any] = {"tags": new_tags}
    metadata = candidate_metadata_payload(
        item_metadata(item),
        candidate,
        overwrite=bool(matching.get("overwriteMetadata", False)),
    )
    if metadata:
        payload["metadata"] = metadata
    result = client.patch(f"/api/items/{item_id}/media", payload)

    cover_mode = matching.get("coverMode", "if_missing")
    has_cover = bool(item.get("media", {}).get("coverPath"))
    cover_url = candidate.get("cover")
    if (
        cover_url
        and cover_mode in {"always", "if_missing"}
        and (cover_mode == "always" or not has_cover)
    ):
        client.post(f"/api/items/{item_id}/cover", body={"url": cover_url})
    return result or {"updated": True, "tags": new_tags}


def mark_unmatched(
    client: ABSClient,
    item: dict[str, Any],
    settings: dict[str, Any],
    review: bool = False,
) -> list[str]:
    tags_settings = settings.get("tags", {})
    if settings.get("run", {}).get("dryRun", True):
        return item_tags(item)
    add_tags: list[str] = []
    remove_tags: list[str] = []
    if review and tags_settings.get("tagReview", True):
        add_tags.append(tags_settings.get("reviewTag", "ABSidekick: Needs Review"))
    if not review and tags_settings.get("tagUnmatched", True):
        add_tags.append(
            tags_settings.get("unmatchedTag", "ABSidekick: AutoMatch Unmatched")
        )
    if tags_settings.get("clearMatchedOnUnmatched", False):
        remove_tags.append(tags_settings.get("matchedTag", "ABSidekick: AutoMatched"))
    new_tags = add_remove_tags(item_tags(item), add=add_tags, remove=remove_tags)
    patch_item_tags(client, item, new_tags)
    return new_tags


class MatchJob:
    def __init__(
        self, job_id: str, client: ABSClient, settings: dict[str, Any]
    ) -> None:
        self.job_id = job_id
        self.client = client
        self.settings = deepcopy(settings)
        self.thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.lock = threading.Lock()
        self.status = "queued"
        self.created_at = utc_now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.stats = {
            "total": 0,
            "processed": 0,
            "matched": 0,
            "unmatched": 0,
            "review": 0,
            "skipped": 0,
            "errors": 0,
        }
        self.logs: list[dict[str, Any]] = []
        self.review_items: list[dict[str, Any]] = []
        self.latest: dict[str, Any] | None = None

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self.run, name=f"absidekick-{self.job_id}", daemon=True
        )
        self.thread.start()

    def log(self, level: str, message: str, **extra: Any) -> None:
        entry = {"time": utc_now(), "level": level, "message": message, **extra}
        with self.lock:
            self.logs.append(entry)
            self.logs = self.logs[-500:]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.job_id,
                "status": self.status,
                "createdAt": self.created_at,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "stats": deepcopy(self.stats),
                "latest": deepcopy(self.latest),
                "logs": deepcopy(self.logs[-500:]),
                "reviewQueue": deepcopy(self.review_items[-250:]),
                "settings": public_settings(self.settings, has_token=True),
            }

    def remove_review_item(self, item_id: str) -> None:
        with self.lock:
            self.review_items = [
                row
                for row in self.review_items
                if str(row.get("item", {}).get("id")) != str(item_id)
            ]

    def pause(self) -> None:
        self.pause_event.set()
        with self.lock:
            if self.status == "running":
                self.status = "paused"
        self.log("info", "Job paused")

    def resume(self) -> None:
        self.pause_event.clear()
        with self.lock:
            if self.status == "paused":
                self.status = "running"
        self.log("info", "Job resumed")

    def cancel(self) -> None:
        self.cancel_event.set()
        self.pause_event.clear()
        self.log("warning", "Cancel requested")

    def wait_if_paused(self) -> None:
        while self.pause_event.is_set() and not self.cancel_event.is_set():
            time.sleep(0.2)

    def run(self) -> None:
        with self.lock:
            self.status = "running"
            self.started_at = utc_now()
        try:
            self.log("info", "Loading target items from Audiobookshelf")
            items = fetch_library_items(self.client, self.settings)
            with self.lock:
                self.stats["total"] = len(items)
            self.log("info", f"Loaded {len(items)} eligible item(s)")

            for item in items:
                self.wait_if_paused()
                if self.cancel_event.is_set():
                    with self.lock:
                        self.status = "cancelled"
                    self.log("warning", "Job cancelled")
                    return

                title = item_title(item)
                author = item_author(item)
                with self.lock:
                    self.latest = {
                        "id": item.get("id"),
                        "title": title,
                        "author": author,
                    }

                try:
                    candidates = search_candidates(self.client, item, self.settings)
                    for diagnostic in candidates.diagnostics:
                        self.log(
                            "warning",
                            diagnostic["message"],
                            itemId=item.get("id"),
                            title=title,
                            provider=diagnostic["provider"],
                            strategy=diagnostic["strategy"],
                            error=diagnostic["error"],
                        )
                    ranked = rank_candidates(item, candidates, self.settings)
                    best = ranked[0] if ranked else None
                    best_score = float(best["score"]) if best else 0.0
                    decision = match_decision(ranked, self.settings)
                    primary_provider = str(
                        self.settings.get("connection", {}).get("provider") or "audible"
                    )
                    if (
                        isinstance(self.client, ABSClient)
                        and primary_provider in self.client.disabled_search_providers
                        and not best
                    ):
                        with self.lock:
                            self.stats["errors"] += 1
                            self.stats["processed"] += 1
                            self.status = "failed"
                        self.log(
                            "error",
                            (
                                f"Matching stopped: primary provider "
                                f"{primary_provider} is unavailable. No remaining "
                                "items were changed."
                            ),
                            itemId=item.get("id"),
                            title=title,
                            provider=primary_provider,
                            error=self.client.disabled_search_providers[
                                primary_provider
                            ],
                        )
                        return

                    if best and decision["action"] == "auto":
                        apply_result = apply_match(
                            self.client, item, best, self.settings
                        )
                        with self.lock:
                            self.stats["matched"] += 1
                        self.log(
                            "success",
                            f"Matched {title}",
                            itemId=item.get("id"),
                            title=title,
                            author=author,
                            score=best_score,
                            candidate=best["candidate"].get("title"),
                            confidence=decision["confidence"],
                            margin=decision["margin"],
                            signals=best.get("strongSignals", []),
                            search=best.get("search", {}),
                            searchAttempts=candidates.attempts,
                            decision=decision,
                            result=apply_result,
                        )
                    elif best and decision["action"] == "review":
                        mark_unmatched(self.client, item, self.settings, review=True)
                        with self.lock:
                            self.stats["review"] += 1
                            self.review_items.append(
                                build_review_row(
                                    item,
                                    ranked,
                                    self.settings,
                                    candidates.diagnostics,
                                    candidates.attempts,
                                )
                            )
                        self.log(
                            "warning",
                            f"Needs review: {title}",
                            itemId=item.get("id"),
                            title=title,
                            author=author,
                            score=best_score,
                            candidate=best["candidate"].get("title"),
                            reasons=decision["reasons"],
                            margin=decision["margin"],
                            signals=best.get("strongSignals", []),
                            search=best.get("search", {}),
                            searchAttempts=candidates.attempts,
                            decision=decision,
                        )
                    else:
                        mark_unmatched(self.client, item, self.settings, review=False)
                        with self.lock:
                            self.stats["unmatched"] += 1
                        self.log(
                            "info",
                            f"No confident match: {title}",
                            itemId=item.get("id"),
                            title=title,
                            author=author,
                            score=best_score,
                            candidate=best["candidate"].get("title") if best else None,
                            reasons=decision["reasons"],
                            margin=decision["margin"],
                            searchAttempts=candidates.attempts,
                            decision=decision,
                        )
                    with self.lock:
                        self.stats["processed"] += 1
                except Exception as error:  # noqa: BLE001 - job runner must isolate item failures
                    with self.lock:
                        self.stats["errors"] += 1
                        self.stats["processed"] += 1
                    self.log(
                        "error",
                        f"Error processing {title}: {error}",
                        itemId=item.get("id"),
                        title=title,
                        traceback=traceback.format_exc(limit=4),
                    )
                    if self.settings.get("run", {}).get("stopOnError", False):
                        raise
            with self.lock:
                self.status = "completed"
            self.log("success", "Job completed")
        except Exception as error:  # noqa: BLE001
            with self.lock:
                self.status = "failed"
                self.stats["errors"] += 1
            self.log("error", f"Job failed: {error}", traceback=traceback.format_exc())
        finally:
            with self.lock:
                if self.status == "running":
                    self.status = "completed"
                self.finished_at = utc_now()


def preview_matches(
    client: ABSClient, settings: dict[str, Any], limit: int = 10
) -> dict[str, Any]:
    preview_settings = deepcopy(settings)
    preview_settings.setdefault("run", {})
    preview_settings["run"]["dryRun"] = True
    if limit:
        preview_settings["run"]["limit"] = limit
    items = fetch_library_items(client, preview_settings)
    rows = []
    for item in items[:limit]:
        candidates = search_candidates(client, item, preview_settings)
        ranked = rank_candidates(item, candidates, preview_settings)
        rows.append(
            {
                "id": item.get("id"),
                "title": item_title(item),
                "author": item_author(item),
                "tags": item_tags(item),
                "best": ranked[0] if ranked else None,
                "candidateCount": len(candidates),
                "decision": match_decision(ranked, preview_settings),
                "searchDiagnostics": candidates.diagnostics,
                "searchAttempts": candidates.attempts,
            }
        )
        primary_provider = str(
            preview_settings.get("connection", {}).get("provider") or "audible"
        )
        if (
            isinstance(client, ABSClient)
            and primary_provider in client.disabled_search_providers
            and not ranked
        ):
            break
    return {"totalEligible": len(items), "rows": rows}
