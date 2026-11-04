from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .mam import USER_AGENT

GOODREADS_HOSTS = {"goodreads.com", "www.goodreads.com"}
GOODREADS_BOOK_PATH = re.compile(r"/(?:[a-z]{2}/)?book/show/(\d+)")
NEXT_DATA = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
META_TAG = re.compile(r"<meta\s+([^>]+)>", re.IGNORECASE)
ATTRIBUTE = re.compile(
    r"([:\w-]+)\s*=\s*(?:[\"']([^\"']*)[\"']|([^\s>]+))",
    re.IGNORECASE,
)
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SERIES_SUFFIX = re.compile(r"^(.*?)\s+\((.*?)(?:,\s*)?#\s*([\d.]+)\)\s*$")


class GoodreadsLookupError(RuntimeError):
    pass


def validate_goodreads_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() not in GOODREADS_HOSTS
    ):
        raise GoodreadsLookupError(
            "enter a full https://www.goodreads.com/book/show/... URL"
        )
    if not GOODREADS_BOOK_PATH.search(parsed.path):
        raise GoodreadsLookupError("the Goodreads URL must point to a book page")
    return url


def goodreads_book_id(value: str) -> int:
    validated = validate_goodreads_url(value)
    match = GOODREADS_BOOK_PATH.search(urlparse(validated).path)
    if not match:  # pragma: no cover - validation above guarantees this
        raise GoodreadsLookupError("the Goodreads URL must point to a book page")
    return int(match.group(1))


def _reference(state: dict[str, Any], value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("__ref"), str):
        resolved = state.get(value["__ref"])
        return resolved if isinstance(resolved, dict) else {}
    return value if isinstance(value, dict) else {}


def _next_data_book(document: str, book_id: int) -> dict[str, Any]:
    match = NEXT_DATA.search(document)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
        state = payload["props"]["pageProps"]["apolloState"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(state, dict):
        return {}
    book = next(
        (
            value
            for key, value in state.items()
            if key.startswith("Book:")
            and isinstance(value, dict)
            and str(value.get("legacyId")) == str(book_id)
            and value.get("title")
        ),
        None,
    )
    if not book:
        return {}

    contributor_edge = book.get("primaryContributorEdge")
    contributor = _reference(
        state,
        contributor_edge.get("node") if isinstance(contributor_edge, dict) else None,
    )
    authors = [str(contributor.get("name", "")).strip()] if contributor else []
    authors = [author for author in authors if author]

    series_name = ""
    series_position = ""
    book_series = book.get("bookSeries")
    if isinstance(book_series, list) and book_series:
        edge = book_series[0] if isinstance(book_series[0], dict) else {}
        series = _reference(state, edge.get("series"))
        series_name = str(series.get("title") or series.get("name") or "").strip()
        series_position = str(edge.get("userPosition") or "").strip()

    details = book.get("details") if isinstance(book.get("details"), dict) else {}
    language_value = details.get("language")
    language = (
        str(language_value.get("name", "")).strip()
        if isinstance(language_value, dict)
        else ""
    )
    title = str(book.get("title", "")).strip()
    title_complete = str(book.get("titleComplete", "")).strip()
    if not series_name and title_complete:
        series_match = SERIES_SUFFIX.match(title_complete)
        if series_match:
            title = title or series_match.group(1).strip()
            series_name = series_match.group(2).strip()
            series_position = series_match.group(3).strip()
    return {
        "goodreads_id": book_id,
        "title": title,
        "authors": authors,
        "series": series_name,
        "series_position": series_position,
        "language": language,
        "isbn": str(details.get("isbn13") or details.get("isbn") or "").strip(),
        "cover_url": str(book.get("imageUrl") or "").strip(),
        "format": str(details.get("format") or "").strip(),
    }


def _meta_values(document: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for tag in META_TAG.findall(document):
        attributes = {
            name.casefold(): html.unescape(quoted or bare)
            for name, quoted, bare in ATTRIBUTE.findall(tag)
        }
        key = attributes.get("property") or attributes.get("name")
        if key and attributes.get("content"):
            values[key.casefold()] = attributes["content"].strip()
    return values


def parse_goodreads_book(document: str, url: str) -> dict[str, Any]:
    match = GOODREADS_BOOK_PATH.search(urlparse(url).path)
    if not match:
        raise GoodreadsLookupError("Goodreads did not return a recognizable book URL")
    book_id = int(match.group(1))
    book = _next_data_book(document, book_id)
    meta = _meta_values(document)
    page_title = meta.get("og:title", "")
    if not page_title:
        title_match = TITLE_TAG.search(document)
        page_title = (
            html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
            if title_match
            else ""
        )
    title_author = re.match(r"^(.*?)\s+by\s+(.*?)\s*\|\s*Goodreads", page_title)
    if not book.get("title") and title_author:
        complete_title = title_author.group(1).strip()
        series_match = SERIES_SUFFIX.match(complete_title)
        if series_match:
            book.update(
                title=series_match.group(1).strip(),
                series=series_match.group(2).strip(),
                series_position=series_match.group(3).strip(),
            )
        else:
            book["title"] = complete_title
    if not book.get("authors") and title_author:
        book["authors"] = [title_author.group(2).strip()]
    book.setdefault("goodreads_id", book_id)
    book.setdefault("series", "")
    book.setdefault("series_position", "")
    book.setdefault("language", "")
    book.setdefault("isbn", "")
    book.setdefault("format", "")
    book["cover_url"] = book.get("cover_url") or meta.get("og:image", "")
    book["url"] = url
    if not book.get("title"):
        raise GoodreadsLookupError("Goodreads returned the page, but no book title")
    return book


async def lookup_goodreads_book(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    current = validate_goodreads_url(url)
    owns_client = client is None
    http = client or httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    try:
        for _ in range(4):
            response = await http.get(current, follow_redirects=False)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise GoodreadsLookupError("Goodreads returned an empty redirect")
                current = validate_goodreads_url(urljoin(current, location))
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.casefold():
                raise GoodreadsLookupError(
                    "Goodreads returned unexpected content "
                    f"({content_type or 'unknown'})"
                )
            return parse_goodreads_book(response.text, current)
        raise GoodreadsLookupError("Goodreads redirected too many times")
    except httpx.HTTPError as error:
        raise GoodreadsLookupError(
            f"could not read the Goodreads page: {error}"
        ) from error
    finally:
        if owns_client:
            await http.aclose()
