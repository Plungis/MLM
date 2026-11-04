from __future__ import annotations

import urllib.parse
from typing import Any

from ...repository import Repository
from ..absidekick.core import (
    ABSAPIError,
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
    base_url = connection.get("baseUrl")
    auth_token = token or connection.get("token") or ""
    if not base_url or not auth_token:
        raise ValueError(
            "Audiobookshelf URL or API token is missing. "
            "Please configure your ABS connection in ABSidekick settings."
        )

    client = create_client(settings, token=auth_token)

    # 1. Discover all accessible libraries to validate or auto-detect IDs
    configured_lib_id = connection.get("libraryId")
    libraries_to_query: list[str] = []

    try:
        libs_payload = client.get("/api/libraries")
        libs = (
            libs_payload.get("libraries", [])
            if isinstance(libs_payload, dict)
            else []
        )
        valid_lib_ids = {str(lib.get("id")): lib for lib in libs if lib.get("id")}
        book_libs = [lib for lib in libs if lib.get("mediaType") == "book"]

        if configured_lib_id and str(configured_lib_id) in valid_lib_ids:
            libraries_to_query = [str(configured_lib_id)]
        elif book_libs:
            # Stale/invalid library ID or missing: auto-select all active book libraries
            libraries_to_query = [str(lib["id"]) for lib in book_libs]
            connection["libraryId"] = libraries_to_query[0]
        elif libs:
            libraries_to_query = [str(libs[0]["id"])]
            connection["libraryId"] = libraries_to_query[0]
        elif configured_lib_id:
            libraries_to_query = [str(configured_lib_id)]
    except Exception as err:
        if configured_lib_id:
            libraries_to_query = [str(configured_lib_id)]
        else:
            raise ValueError(
                f"Failed to query Audiobookshelf libraries: {err}"
            ) from err

    if not libraries_to_query:
        raise ValueError(
            "No libraries found on Audiobookshelf for this API key. "
            "Please check Audiobookshelf permissions for this user/key."
        )

    page_size = 100
    normalized_books: list[dict[str, Any]] = []

    for lib_id in libraries_to_query:
        page = 0
        while True:
            try:
                payload = client.get(
                    f"/api/libraries/{urllib.parse.quote(str(lib_id))}/items",
                    params={
                        "limit": page_size,
                        "page": page,
                        "minified": 0,
                        "collapseseries": 0,
                    },
                )
            except ABSAPIError as api_err:
                if "404" in str(api_err):
                    break
                raise

            results = payload.get("results", []) if isinstance(payload, dict) else []
            if not results:
                break

            for item in results:
                metadata = item_metadata(item)
                title = item_title(item)
                if not title:
                    continue

                authors = split_people(
                    first_present(
                        metadata.get("authors"), metadata.get("authorName"), ""
                    )
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
    count = repository.upsert_abs_books(books, prune_missing=True)

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
