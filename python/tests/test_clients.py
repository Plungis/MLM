from __future__ import annotations

import asyncio

import httpx

from mlm.mam import USER_AGENT, MamClient


def test_mam_uses_approved_heavy_mlm_identity() -> None:
    mam = MamClient("cookie")
    try:
        assert mam.client.headers["User-Agent"] == USER_AGENT
        assert USER_AGENT.startswith("HeavyMLM/")
        assert "github.com/Plungis/MLM" in USER_AGENT
    finally:
        asyncio.run(mam.close())


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
