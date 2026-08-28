from __future__ import annotations

import urllib.parse
from typing import Any

from ...repository import Repository
from ..absidekick.core import (
    create_client,
    first_present,
    item_metadata,
    item_series_entries,
    item_title,
    split_people,
)


def fetch_all_abs_library_books(
    settings: dict[str, Any],
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all audiobooks from an Audiobookshelf library without filtering."""
    connection = settings.get("connection", {})
    library_id = connection.get("libraryId")
    if not library_id:
        raise ValueError(
            "Audiobookshelf library ID is not configured. "
            "Please select a library in ABSidekick settings."
        )

    base_url = connection.get("baseUrl")
    auth_token = token or connection.get("token") or ""
    if not base_url or not auth_token:
        raise ValueError(
            "Audiobookshelf URL or API token is missing. "
            "Please configure your ABS connection in ABSidekick settings."
        )

    client = create_client(settings, token=auth_token)
    page_size = 100
    page = 0
    normalized_books: list[dict[str, Any]] = []

    while True:
        payload = client.get(
            f"/api/libraries/{urllib.parse.quote(str(library_id))}/items",
            params={
                "limit": page_size,
                "page": page,
                "minified": 0,
                "collapseseries": 0,
            },
        )
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not results:
            break

        for item in results:
            metadata = item_metadata(item)
            title = item_title(item)
            if not title:
                continue

            authors = split_people(
                first_present(metadata.get("authors"), metadata.get("authorName"), "")
            )

            raw_series = item_series_entries(item)
            series_list = [
                {"name": s_name, "entries": [s_seq] if s_seq else []}
                for s_name, s_seq in raw_series
                if s_name
            ]

            library_path = str(
                first_present(item.get("path"), item.get("relPath"), "")
            ).strip()

            asin = str(metadata.get("asin") or "").strip()
            isbn = str(metadata.get("isbn") or "").strip()

            normalized_books.append(
                {
                    "id": str(item.get("id")),
                    "abs_id": str(item.get("id")),
                    "title": title,
                    "authors": authors,
                    "series": series_list,
                    "library_path": library_path,
                    "asin": asin,
                    "isbn": isbn,
                    "media_type": "audiobook",
                    "meta": {
                        "title": title,
                        "authors": authors,
                        "series": series_list,
                        "media_type": "audiobook",
                        "asin": asin,
                        "isbn": isbn,
                    },
                    "source": "audiobookshelf",
                }
            )

        total = int(payload.get("total", 0) or 0)
        page += 1
        if page * page_size >= total:
            break

    return normalized_books


def sync_audiobookshelf_library(
    repository: Repository,
    settings: dict[str, Any],
    token: str | None = None,
) -> dict[str, Any]:
    """Synchronize Audiobookshelf library books into the HeavyMLM repository."""
    books = fetch_all_abs_library_books(settings, token=token)
    count = repository.upsert_abs_books(books)

    repository.log_activity(
        "absidekick",
        f"Synced {count} audiobook(s) from Audiobookshelf library into catalog",
        level="success",
        context={"count": count},
    )

    return {
        "ok": True,
        "total_synced": count,
        "books": books,
    }
