from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .autograbber import select_row
from .config import Config
from .mam import MamClient
from .repository import Repository
from .search import search_pages

SERIES_PATTERN = re.compile(r"(.*?) \(([^)]*?),? #?(\d+(?:\.\d+)?)\)$")
BOOK_LINK = re.compile(r'href=["\']((?:https://www\.goodreads\.com)?/book/show/[^"\']+)')
COVER_LINK = re.compile(r'<img[^>]+src=["\']([^"\']+)')


def _text(element: ET.Element, name: str) -> str | None:
    child = next((item for item in element if item.tag.rsplit("}", 1)[-1] == name), None)
    return child.text.strip() if child is not None and child.text else None


def goodreads_list_id(url: str) -> str:
    parsed = urlparse(url)
    user_id = parsed.path.rstrip("/").split("/")[-1]
    shelf = parse_qs(parsed.query).get("shelf", [""])[0]
    return f"{user_id}:{shelf}"


def _allow_media(grabs: list[dict[str, Any]], key: str) -> bool:
    for grab in grabs:
        categories = grab.get("categories", {})
        value = categories.get(key, True)
        if value is True or (isinstance(value, list) and value):
            return True
    return False


async def run_goodreads_import(
    config: Config,
    repository: Repository,
    mam: MamClient,
    definition: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    owns_client = client is None
    http = client or httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 MLM-Python"}, timeout=30
    )
    try:
        response = await http.get(definition["url"])
        response.raise_for_status()
    finally:
        if owns_client:
            await http.aclose()
    root = ET.fromstring(response.content)
    channel = next(
        (item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "channel"),
        root,
    )
    title = _text(channel, "title") or definition.get("name") or "Goodreads"
    list_id = goodreads_list_id(definition["url"])
    now = datetime.now(UTC).isoformat()
    if not definition.get("dry_run", False):
        repository.upsert_list(
            {"id": list_id, "title": title, "updated_at": now, "build_date": now}
        )
    selected = 0
    for xml_item in [
        item for item in channel if item.tag.rsplit("}", 1)[-1] == "item"
    ]:
        guid_value = _text(xml_item, "guid") or _text(xml_item, "book_id")
        item_title = html.unescape(_text(xml_item, "title") or "")
        if not guid_value or not item_title:
            continue
        series: list[list[Any]] = []
        match = SERIES_PATTERN.fullmatch(item_title)
        if match:
            item_title = match.group(1)
            series = [[html.unescape(match.group(2)), float(match.group(3))]]
        description = _text(xml_item, "description") or ""
        author = html.unescape(_text(xml_item, "author_name") or "").replace(".", " ")
        cover_match = COVER_LINK.search(description)
        book_match = BOOK_LINK.search(description)
        book_id_text = _text(xml_item, "book_id")
        book_id = int(book_id_text) if book_id_text and book_id_text.isdigit() else None
        guid = [list_id, guid_value]
        list_item = repository.list_item(guid) or {
            "guid": guid,
            "list_id": list_id,
            "created_at": now,
            "audio_torrent": None,
            "ebook_torrent": None,
            "marked_done_at": None,
        }
        list_item.update(
            {
                "title": item_title,
                "authors": [author] if author else [],
                "series": series,
                "cover_url": _text(xml_item, "book_large_image_url")
                or (cover_match.group(1) if cover_match else ""),
                "book_url": (
                    "https://www.goodreads.com" + book_match.group(1)
                    if book_match and book_match.group(1).startswith("/")
                    else (book_match.group(1) if book_match else None)
                ),
                "isbn": _text(xml_item, "isbn"),
                "prefer_format": definition.get("prefer_format"),
                "allow_audio": _allow_media(definition.get("grab", []), "audio"),
                "allow_ebook": _allow_media(definition.get("grab", []), "ebook"),
            }
        )
        if not definition.get("dry_run", False):
            repository.upsert_list_item(list_item)
        query = " ".join(filter(None, [f'"{item_title}"', f'"{author}"' if author else ""]))
        for grab in definition.get("grab", []):
            rule = {
                **grab,
                "type": "new",
                "query": query,
                "search_in": ["title", "author"],
                "max_pages": 1,
                "unsat_buffer": definition.get("unsat_buffer"),
                "wedge_buffer": definition.get("wedge_buffer"),
                "dry_run": definition.get("dry_run", False),
                "name": definition.get("name", title),
            }
            async for row in search_pages(mam, rule):
                if await select_row(
                    config, repository, row, rule, goodreads_id=book_id
                ):
                    selected += 1
                    break
            else:
                continue
            break
    return selected


async def run_notion_import(
    config: Config,
    repository: Repository,
    mam: MamClient,
    definition: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30)
    selected = 0
    cursor: str | None = None
    try:
        while True:
            body = {"start_cursor": cursor} if cursor else {}
            response = await http.post(
                f"https://api.notion.com/v1/data_sources/{definition['data_source']}/query",
                headers={
                    "Notion-Version": "2025-09-03",
                    "Authorization": f"Bearer {definition['token']}",
                },
                json=body,
            )
            response.raise_for_status()
            result = response.json()
            for item in result.get("results", []):
                properties = item.get("properties", {})
                for field in definition.get("mam_fields", []):
                    value = properties.get(field, {})
                    url = value.get("url") if value.get("type") == "url" else None
                    if not url:
                        continue
                    try:
                        torrent_id = int(url.rstrip("/").split("/")[-1])
                    except ValueError:
                        continue
                    if repository.has_mam_id(torrent_id):
                        continue
                    row = await mam.get_torrent_info_by_id(torrent_id)
                    if not row:
                        continue
                    for grab in definition.get("grab", []):
                        rule = {
                            **grab,
                            "unsat_buffer": definition.get("unsat_buffer"),
                            "wedge_buffer": definition.get("wedge_buffer"),
                            "dry_run": definition.get("dry_run", False),
                            "name": definition.get("name", "Notion"),
                        }
                        if await select_row(config, repository, row, rule):
                            selected += 1
                            break
            if not result.get("has_more") or not result.get("next_cursor"):
                break
            cursor = result["next_cursor"]
    finally:
        if owns_client:
            await http.aclose()
    return selected
