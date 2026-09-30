from __future__ import annotations

import asyncio

import httpx

from mlm.qbittorrent import QbitClient


def test_torrent_category_is_sent_to_qbittorrent() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="http://qbit", transport=transport
        ) as http:
            client = QbitClient("http://qbit", client=http)
            await client.torrents(category="Audiobooks")

    asyncio.run(run())

    assert len(requests) == 1
    assert requests[0].url.params["category"] == "Audiobooks"
