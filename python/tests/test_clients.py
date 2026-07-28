from __future__ import annotations

import asyncio

import httpx

from mlm.mam import MamClient


def test_torrent_download_always_includes_tid() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, content=b"torrent bytes")

    async def exercise() -> bytes:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            return await mam.get_torrent_file("download-hash", 123456)

    assert asyncio.run(exercise()) == b"torrent bytes"
    assert observed[0].url.path == "/tor/download.php/download-hash"
    assert observed[0].url.params["tid"] == "123456"
