from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .repository import Repository


class AudiobookshelfClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "User-Agent": "MLM"},
            timeout=30,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def find_book(self, torrent: dict[str, Any]) -> dict[str, Any] | None:
        library_path = torrent.get("library_path")
        authors = torrent.get("meta", {}).get("authors", [])
        if not library_path or not authors:
            return None
        response = await self.client.get("/api/libraries")
        response.raise_for_status()
        libraries = [
            library
            for library in response.json().get("libraries", [])
            if any(
                Path(library_path) == Path(folder.get("fullPath", ""))
                or Path(folder.get("fullPath", "")) in Path(library_path).parents
                for folder in library.get("folders", [])
            )
        ]
        for library in libraries:
            response = await self.client.get(
                f"/api/libraries/{library['id']}/search", params={"q": authors[0]}
            )
            response.raise_for_status()
            for author in response.json().get("authors", []):
                response = await self.client.get(
                    f"/api/authors/{author['id']}", params={"include": "items"}
                )
                response.raise_for_status()
                for book in response.json().get("libraryItems", []):
                    if Path(book.get("path", "")) == Path(library_path):
                        return book
        return None

    async def update_book(
        self, book_id: str, mam_row: dict[str, Any], meta: dict[str, Any]
    ) -> None:
        title_parts = str(meta.get("title", "")).split(":", 1)
        title = title_parts[0] if len(title_parts[0]) >= 4 else meta.get("title", "")
        subtitle = (
            title_parts[1].strip()
            if len(title_parts) > 1 and title != meta.get("title")
            else None
        )
        isbn_value = str(mam_row.get("isbn") or "").strip()
        payload = {
            "metadata": {
                "title": title,
                "subtitle": subtitle,
                "authors": [{"name": name} for name in meta.get("authors", [])],
                "series": [
                    {
                        "name": series.get("name"),
                        "sequence": str(series.get("entries", [""])[0])
                        if series.get("entries")
                        else None,
                    }
                    for series in meta.get("series", [])
                ],
                "narrators": meta.get("narrators", []),
                "description": mam_row.get("description"),
                "isbn": None
                if not isbn_value or isbn_value.startswith("ASIN:")
                else isbn_value,
                "asin": isbn_value.removeprefix("ASIN:").strip()
                if isbn_value.startswith("ASIN:")
                else None,
                "genres": [meta.get("cat", {}).get("name")]
                if isinstance(meta.get("cat"), dict)
                else [],
                "language": meta.get("language"),
                "explicit": bool(int(meta.get("flags") or 0) & (1 << 4)),
                "abridged": bool(int(meta.get("flags") or 0) & (1 << 5)),
            }
        }
        response = await self.client.patch(f"/api/items/{book_id}/media", json=payload)
        response.raise_for_status()

    async def delete_book(self, book_id: str) -> None:
        response = await self.client.delete(f"/api/items/{book_id}")
        response.raise_for_status()


async def match_torrents_to_audiobookshelf(
    repository: Repository, client: AudiobookshelfClient
) -> int:
    matched = 0
    for torrent in repository.library_torrents():
        collection_items = torrent.get("collection_items") or []
        if collection_items:
            changed = False
            for item in collection_items:
                if item.get("abs_id"):
                    continue
                book = await client.find_book(item)
                if not book:
                    continue
                item["abs_id"] = book["id"]
                matched += 1
                changed = True
            if changed:
                repository.update_torrent(torrent)
            continue
        if torrent.get("abs_id"):
            continue
        book = await client.find_book(torrent)
        if not book:
            continue
        torrent["abs_id"] = book["id"]
        repository.update_torrent(torrent)
        matched += 1
    return matched
