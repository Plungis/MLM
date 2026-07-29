from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .autograbber import select_row
from .config import Config
from .mam import MamClient
from .repository import Repository
from .search import search_pages, torrent_meta

SERIES_PATTERN = re.compile(r"(.*?) \(([^)]*?),? #?(\d+(?:\.\d+)?)\)$")
BOOK_LINK = re.compile(
    r'href=["\']((?:https://www\.goodreads\.com)?/book/show/[^"\']+)'
)
COVER_LINK = re.compile(r'<img[^>]+src=["\']([^"\']+)')
FORMAT_MEDIA_TYPES = {
    "audio": ["audiobook", "periodical_audiobook"],
    "ebook": ["ebook", "manga", "comic_book", "periodical_ebook"],
}


@dataclass(frozen=True)
class ListImportRun:
    refreshed: int = 0
    selected: int = 0
    already_grabbed: int = 0
    no_match: int = 0
    removed: int = 0


def _text(element: ET.Element, name: str) -> str | None:
    child = next(
        (item for item in element if item.tag.rsplit("}", 1)[-1] == name), None
    )
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
) -> ListImportRun:
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
    dry_run = definition.get("dry_run", False)
    component = "lists"
    if not dry_run:
        repository.log_activity(
            component,
            "Refreshing Goodreads source",
            context={"list_id": list_id, "title": title},
        )
    if not dry_run:
        repository.upsert_list(
            {"id": list_id, "title": title, "updated_at": now, "build_date": now}
        )
    selected = already_grabbed = no_match = 0
    seen_guids: set[str] = set()
    for xml_item in [item for item in channel if item.tag.rsplit("}", 1)[-1] == "item"]:
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
        seen_guids.add(guid_value)
        list_item = repository.list_item(guid) or {
            "guid": guid,
            "list_id": list_id,
            "created_at": now,
            "audio_torrent": None,
            "ebook_torrent": None,
            "marked_done_at": None,
            "selected_mam_ids": [],
            "selected_formats": [],
            "check_count": 0,
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
                "last_seen_at": now,
                "source_status": "present",
            }
        )
        grab_both = bool(definition.get("grab_both_formats", config.grab_both_formats))
        known_formats = set(list_item.get("selected_formats", []))
        if list_item.get("audio_torrent"):
            known_formats.add("audio")
        if list_item.get("ebook_torrent"):
            known_formats.add("ebook")
        if book_id is not None:
            known_formats.update(repository.goodreads_formats(book_id))
        previously_grabbed = bool(
            list_item.get("marked_done_at")
            or list_item.get("selected_mam_ids")
            or list_item.get("audio_torrent")
            or list_item.get("ebook_torrent")
            or (book_id is not None and repository.has_goodreads_id(book_id))
        )
        desired_formats = [
            format_name
            for format_name, allowed in (
                ("audio", list_item["allow_audio"]),
                ("ebook", list_item["allow_ebook"]),
            )
            if allowed
        ]
        missing_formats = [
            format_name
            for format_name in desired_formats
            if format_name not in known_formats
        ]
        if (not grab_both and previously_grabbed) or (
            grab_both and not missing_formats
        ):
            already_grabbed += 1
            list_item["status"] = "already_grabbed"
            list_item["selected_formats"] = sorted(known_formats)
            list_item["last_result"] = (
                "Skipped: audiobook and ebook already selected"
                if grab_both
                else "Skipped: already selected in an earlier run"
            )
            if not dry_run:
                repository.upsert_list_item(list_item)
                repository.log_activity(
                    component,
                    f"Already grabbed: {item_title}",
                    level="debug",
                    context={
                        "guid": guid_value,
                        "book_id": book_id,
                        "formats": sorted(known_formats),
                    },
                )
            continue

        targets = missing_formats if grab_both else ["any"]
        list_item["last_checked_at"] = now
        list_item["check_count"] = int(list_item.get("check_count", 0)) + 1
        if not dry_run:
            repository.upsert_list_item(list_item)
        query = " ".join(
            filter(None, [f'"{item_title}"', f'"{author}"' if author else ""])
        )
        matched_ids: list[int] = []
        failed_targets: list[str] = []
        for target in targets:
            if not dry_run:
                repository.log_activity(
                    component,
                    f"Searching MaM for {target}: {item_title}",
                    level="debug",
                    context={
                        "guid": guid_value,
                        "book_id": book_id,
                        "format": target,
                        "query": query,
                        "rules": len(definition.get("grab", [])),
                    },
                )
            matched_torrent_id: int | None = None
            matched_format: str | None = None
            candidate_count = 0
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
                if target != "any":
                    rule["media_type"] = FORMAT_MEDIA_TYPES[target]
                async for row in search_pages(mam, rule):
                    candidate_count += 1
                    if await select_row(
                        config, repository, row, rule, goodreads_id=book_id
                    ):
                        selected += 1
                        matched_torrent_id = int(row["id"])
                        media_type = torrent_meta(row)["media_type"]
                        matched_format = (
                            "audio"
                            if media_type in FORMAT_MEDIA_TYPES["audio"]
                            else "ebook"
                        )
                        break
                else:
                    continue
                break
            if matched_torrent_id is not None and matched_format is not None:
                matched_ids.append(matched_torrent_id)
                known_formats.add(matched_format)
                list_item[f"{matched_format}_torrent"] = matched_torrent_id
                message = f"Selected {matched_format}: {item_title}"
                activity_level = "success"
            else:
                failed_targets.append(target)
                message = f"No {target} match: {item_title}"
                activity_level = "warning"
            if not dry_run:
                repository.log_activity(
                    component,
                    message,
                    level=activity_level,
                    context={
                        "guid": guid_value,
                        "book_id": book_id,
                        "format": target,
                        "mam_id": matched_torrent_id,
                        "query": query,
                        "candidates_evaluated": candidate_count,
                    },
                )

        list_item["selected_mam_ids"] = list(
            dict.fromkeys([*list_item.get("selected_mam_ids", []), *matched_ids])
        )
        list_item["selected_formats"] = sorted(known_formats)
        all_formats_found = bool(desired_formats) and set(desired_formats).issubset(
            known_formats
        )
        if matched_ids:
            list_item["status"] = (
                "selected" if not grab_both or all_formats_found else "partial"
            )
            if not grab_both or all_formats_found:
                list_item["marked_done_at"] = now
            formats_text = ", ".join(sorted(known_formats))
            list_item["last_result"] = f"Selected formats: {formats_text}"
        elif known_formats:
            list_item["status"] = "partial"
            missing_text = ", ".join(failed_targets)
            list_item["last_result"] = f"Still missing: {missing_text}"
        else:
            no_match += 1
            list_item["status"] = "no_match"
            list_item["last_result"] = (
                f"No configured {' or '.join(failed_targets)} release matched"
            )
        if not dry_run:
            repository.upsert_list_item(list_item)

    removed = 0
    for previous in repository.list_items_for_list(list_id) if not dry_run else []:
        previous_guid = previous.get("guid", [None, None])
        guid_value = str(previous_guid[1]) if len(previous_guid) > 1 else ""
        if guid_value in seen_guids or previous.get("source_status") == "removed":
            continue
        previous["source_status"] = "removed"
        previous["removed_from_source_at"] = now
        repository.upsert_list_item(previous)
        removed += 1
        repository.log_activity(
            component,
            f"Removed from source: {previous.get('title', guid_value)}",
            level="warning",
            context={"guid": guid_value},
        )

    result = ListImportRun(
        refreshed=len(seen_guids),
        selected=selected,
        already_grabbed=already_grabbed,
        no_match=no_match,
        removed=removed,
    )
    if not dry_run:
        repository.upsert_list(
            {
                "id": list_id,
                "title": title,
                "updated_at": now,
                "build_date": now,
                "last_result": {
                    "refreshed": result.refreshed,
                    "selected": result.selected,
                    "already_grabbed": result.already_grabbed,
                    "no_match": result.no_match,
                    "removed": result.removed,
                },
            }
        )
        repository.log_activity(
            component,
            "Goodreads refresh complete",
            level="success",
            context={
                "refreshed": result.refreshed,
                "selected": result.selected,
                "already_grabbed": result.already_grabbed,
                "no_match": result.no_match,
                "removed": result.removed,
            },
        )
    return result


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
