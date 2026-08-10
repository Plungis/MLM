from __future__ import annotations

import difflib
import json
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_VERSION = "Beta V.91.1"

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
    "run": {
        "dryRun": True,
        "limit": 0,
        "pageSize": 100,
        "sort": "media.metadata.title",
        "sortDesc": False,
        "requestDelayMs": 150,
        "timeoutSeconds": 30,
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
    return data


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    value = str(value).lower()
    replacements = {
        "&": " and ",
        "+": " and ",
        "'": "",
        "`": "",
        "-": " ",
        "_": " ",
        ".": " ",
        ",": " ",
        ":": " ",
        ";": " ",
        "(": " ",
        ")": " ",
        "[": " ",
        "]": " ",
        "{": " ",
        "}": " ",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    words = [
        word
        for word in value.split()
        if word not in {"a", "an", "the", "book", "audiobook"}
    ]
    return " ".join(words)


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
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm and not b_norm:
        return 100.0
    if not a_norm or not b_norm:
        return 0.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio() * 100.0


def best_people_ratio(left: Any, right: Any) -> float:
    left_people = split_people(left)
    right_people = split_people(right)
    if not left_people and not right_people:
        return 100.0
    if not left_people or not right_people:
        return 0.0
    return max(ratio(a, b) for a in left_people for b in right_people)


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
    return str(
        first_present(metadata.get("authorName"), metadata.get("authors"), "") or ""
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


def title_token_gate(item_title_value: str, candidate_title_value: str) -> bool:
    item_words = set(normalize_text(item_title_value).split())
    candidate_words = set(normalize_text(candidate_title_value).split())
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
    series = first_present(metadata.get("seriesName"), metadata.get("series"))
    narrator = first_present(metadata.get("narratorName"), metadata.get("narrators"))

    candidate_title = candidate.get("title") or ""
    parts = {
        "title": ratio(title, candidate_title),
        "author": best_people_ratio(author, candidate_author(candidate)),
        "series": ratio(series, candidate_series(candidate))
        if series or candidate_series(candidate)
        else 100.0,
        "narrator": best_people_ratio(narrator, candidate_narrator(candidate))
        if narrator or candidate_narrator(candidate)
        else 100.0,
        "year": year_score(
            metadata.get("publishedYear"), candidate.get("publishedYear")
        ),
        "duration": duration_score(
            item.get("media", {}).get("duration"),
            candidate.get("duration"),
            int(matching.get("durationToleranceMinutes", 7)),
        ),
    }

    if matching.get("requireTitleToken", True) and not title_token_gate(
        title, str(candidate_title)
    ):
        parts["title"] = min(parts["title"], 35.0)

    if matching.get("requireAuthor", False) and parts["author"] < 70:
        parts["author"] = 0.0
        parts["title"] = min(parts["title"], 60.0)

    total_weight = 0.0
    weighted = 0.0
    for key, value in parts.items():
        weight = float(weights.get(key, 0) or 0)
        if weight <= 0:
            continue
        total_weight += weight
        weighted += value * weight
    score = weighted / total_weight if total_weight else 0.0

    return {
        "index": index,
        "score": round(score, 2),
        "parts": {key: round(value, 2) for key, value in parts.items()},
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
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored


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


class ABSClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_seconds: int = 30,
        max_retries: int = 2,
        request_delay_ms: int = 150,
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

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.request_delay_ms > 0:
                time.sleep(self.request_delay_ms / 1000.0)
            request = urllib.request.Request(
                url, data=data, headers=headers, method=method.upper()
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
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
                if (
                    error.code in {429, 500, 502, 503, 504}
                    and attempt < self.max_retries
                ):
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
                if attempt < self.max_retries:
                    time.sleep(min(8, 2**attempt))
                    continue
                raise ABSAPIError(
                    f"ABS API request failed for {path}: {error}"
                ) from error
        raise ABSAPIError(f"ABS API request failed for {path}: {last_error}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

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
    token = token or connection.get("token") or ""
    return ABSClient(
        base_url=connection.get("baseUrl", ""),
        token=token,
        timeout_seconds=int(run.get("timeoutSeconds", 30)),
        max_retries=int(run.get("maxRetries", 2)),
        request_delay_ms=int(run.get("requestDelayMs", 150)),
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


def search_candidates(
    client: ABSClient, item: dict[str, Any], settings: dict[str, Any]
) -> list[dict[str, Any]]:
    connection = settings.get("connection", {})
    matching = settings.get("matching", {})
    title = item_title(item)
    author = item_author(item)
    results = client.get(
        "/api/search/books",
        params={
            "title": title,
            "author": author,
            "provider": connection.get("provider") or "google",
        },
    )
    if not isinstance(results, list):
        return []
    candidate_limit = max(1, int(matching.get("candidateLimit", 8)))
    return [
        candidate
        for candidate in results[:candidate_limit]
        if isinstance(candidate, dict)
    ]


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
    item: dict[str, Any], ranked: list[dict[str, Any]], settings: dict[str, Any]
) -> dict[str, Any]:
    review_settings = settings.get("review", {})
    candidate_limit = max(1, int(review_settings.get("candidateLimit", 6)))
    return {
        "item": summarize_item(item),
        "candidates": ranked[:candidate_limit],
        "createdAt": utc_now(),
    }


def scan_review_items(
    client: ABSClient,
    settings: dict[str, Any],
    limit: int | None = None,
    excluded_ids: set[str] | None = None,
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
    items = fetch_library_items(client, scan_settings)
    rows = []
    for item in items:
        if str(item.get("id")) in excluded_ids:
            continue
        candidates = search_candidates(client, item, scan_settings)
        ranked = rank_candidates(item, candidates, scan_settings)
        rows.append(build_review_row(item, ranked, scan_settings))
        if len(rows) >= scan_limit:
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
        if matching.get("quickMatchFirstResultOnly", True) and scored.get("index") != 0:
            raise ABSAPIError(
                "Quick match mode is limited to ABS's first provider result for safety"
            )
        client.post(
            f"/api/items/{item_id}/match",
            params={
                "provider": settings.get("connection", {}).get("provider") or "google",
                "title": item_title(item),
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

            threshold = float(self.settings.get("matching", {}).get("threshold", 80))
            review_floor = float(
                self.settings.get("matching", {}).get("reviewFloor", 65)
            )

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
                    ranked = rank_candidates(item, candidates, self.settings)
                    best = ranked[0] if ranked else None
                    best_score = float(best["score"]) if best else 0.0

                    if best and best_score >= threshold:
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
                            result=apply_result,
                        )
                    elif best and best_score >= review_floor:
                        mark_unmatched(self.client, item, self.settings, review=True)
                        with self.lock:
                            self.stats["review"] += 1
                            self.review_items.append(
                                build_review_row(item, ranked, self.settings)
                            )
                        self.log(
                            "warning",
                            f"Needs review: {title}",
                            itemId=item.get("id"),
                            title=title,
                            author=author,
                            score=best_score,
                            candidate=best["candidate"].get("title"),
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
            }
        )
    return {"totalEligible": len(items), "rows": rows}
