from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from mlm.config import Config
from mlm.database import ensure_database
from mlm.lists import goodreads_list_id, run_goodreads_import
from mlm.repository import Repository


def test_goodreads_list_id_legacy_shape() -> None:
    assert (
        goodreads_list_id(
            "https://www.goodreads.com/review/list_rss/12345?shelf=want-to-read"
        )
        == "12345:want-to-read"
    )


class FakeFeedClient:
    async def get(self, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            content=b"""
            <rss><channel><title>Reading List</title><item>
              <guid>book-123</guid><book_id>123</book_id>
              <title>Example Book</title><author_name>Example Author</author_name>
              <isbn>9780000000000</isbn>
            </item></channel></rss>
            """,
        )


class FakeMam:
    searches = 0

    async def search(self, _: dict) -> dict:
        self.searches += 1
        return {
            "found": 1,
            "data": [
                {
                    "id": 987,
                    "dl": "download-hash",
                    "mediatype": 2,
                    "main_cat": 14,
                    "language": 1,
                    "filetype": "epub",
                    "title": "Example Book",
                    "author_info": {"1": "Example Author"},
                    "size": 1024,
                }
            ],
        }


def test_goodreads_refresh_remembers_previously_selected_items(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)
    mam = FakeMam()
    definition = {
        "url": "https://www.goodreads.com/review/list_rss/123?shelf=read",
        "grab": [{"cost": "all"}],
    }

    first = asyncio.run(
        run_goodreads_import(
            Config(mam_id="cookie"),
            repository,
            mam,
            definition,
            client=FakeFeedClient(),
        )
    )
    second = asyncio.run(
        run_goodreads_import(
            Config(mam_id="cookie"),
            repository,
            mam,
            definition,
            client=FakeFeedClient(),
        )
    )

    assert first.selected == 1
    assert second.selected == 0
    assert second.already_grabbed == 1
    assert mam.searches == 1
    item = repository.table_rows("list_items")[0]
    assert item["status"] == "already_grabbed"
    assert item["selected_mam_ids"] == [987]
    assert item["check_count"] == 1
    assert any(
        entry["message"] == "Already grabbed: Example Book"
        for entry in repository.recent_activity()
    )
