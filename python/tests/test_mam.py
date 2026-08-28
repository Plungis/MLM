from __future__ import annotations

import asyncio

import httpx
import pytest

from mlm.mam import MamClient, MamWedgeError

TORRENT = b"d4:infod6:lengthi12e4:name4:bookee"


def test_wedge_uses_documented_torrent_download_api() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=TORRENT,
            headers={"content-type": "application/x-bittorrent"},
        )

    async def run() -> bytes:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            return await mam.get_torrent_file(42, use_wedge=True)

    assert asyncio.run(run()) == TORRENT
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/tor/download.php"
    assert request.url.params["tid"] == "42"
    assert "fl" in request.url.params
    assert request.url.query == b"tid=42&fl"


def test_wedge_error_page_keeps_safe_http_diagnostics() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="API session cannot use this request",
            headers={"content-type": "text/html"},
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            with pytest.raises(MamWedgeError) as caught:
                await mam.get_torrent_file(42, use_wedge=True)
            assert caught.value.reason == "download_rejected"
            assert caught.value.context["stage"] == "wedge_download_response"
            assert caught.value.context["http_status"] == 200
            assert caught.value.context["content_type"] == "text/html"
            assert caught.value.context["response_preview"] == (
                "API session cannot use this request"
            )

    asyncio.run(run())


def test_wedge_http_failure_keeps_status_and_endpoint() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            with pytest.raises(MamWedgeError) as caught:
                await mam.get_torrent_file(42, use_wedge=True)
            assert caught.value.reason == "http_error"
            assert caught.value.context["http_status"] == 403
            assert caught.value.context["endpoint"].endswith("?tid=42&fl")

    asyncio.run(run())
