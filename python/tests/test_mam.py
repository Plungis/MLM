from __future__ import annotations

import asyncio

import httpx
import pytest

from mlm.mam import MamClient, MamWedgeError


def test_wedge_request_matches_mam_bonus_buy_api() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"success": True, "error": None})

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            await mam.wedge_torrent(42)

    asyncio.run(run())

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path.startswith("/json/bonusBuy.php/")
    assert request.url.params["spendtype"] == "personalFL"
    assert request.url.params["torrentid"] == "42"
    assert request.url.params["timestamp"] == request.url.path.rsplit("/", 1)[-1]


def test_wedge_string_false_is_not_mistaken_for_success() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": "false", "error": "Not enough wedges"},
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            with pytest.raises(MamWedgeError) as caught:
                await mam.wedge_torrent(42)
            assert caught.value.reason == "rejected"
            assert caught.value.context["http_status"] == 200
            assert caught.value.context["tracker_response"]["success"] == "false"

    asyncio.run(run())


def test_wedge_invalid_response_keeps_safe_http_diagnostics() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="not json", headers={"content-type": "text/html"}
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            with pytest.raises(MamWedgeError) as caught:
                await mam.wedge_torrent(42)
            assert caught.value.reason == "invalid_response"
            assert caught.value.context["content_type"] == "text/html"
            assert caught.value.context["response_preview"] == "not json"

    asyncio.run(run())
