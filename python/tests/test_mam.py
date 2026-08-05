from __future__ import annotations

import asyncio

import httpx

from mlm.mam import MamClient


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
