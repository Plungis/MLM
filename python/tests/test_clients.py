from __future__ import annotations

import asyncio

import httpx
import pytest

import mlm.mam as mam_module
from mlm.mam import USER_AGENT, MamClient, MamError, authenticated_mam_client


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
        return httpx.Response(200, content=b"d4:infode")

    async def exercise() -> bytes:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(handler),
        ) as http:
            mam = MamClient("cookie", client=http)
            return await mam.get_torrent_file(123456)

    assert asyncio.run(exercise()) == b"d4:infode"
    assert observed[0].url.path == "/tor/download.php"
    assert observed[0].url.params["tid"] == "123456"


def test_rejected_cookie_is_not_persisted() -> None:
    stored: list[str] = []

    async def exercise() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"Success": False})
            ),
        ) as http:
            mam = MamClient("rejected", client=http, cookie_store=stored.append)
            with pytest.raises(MamError, match="did not report success"):
                await mam.check_mam_id()

    asyncio.run(exercise())
    assert stored == []


def test_non_json_success_response_is_accepted() -> None:
    stored: list[str] = []

    async def exercise() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    text='PHP notice\n{"Success" : true}',
                    headers={"content-type": "text/html"},
                )
            ),
        ) as http:
            mam = MamClient("accepted", client=http, cookie_store=stored.append)
            await mam.check_mam_id()

    asyncio.run(exercise())
    assert stored == ["accepted"]


def test_ip_locked_api_session_response_is_accepted() -> None:
    stored: list[str] = []

    async def exercise() -> None:
        async with httpx.AsyncClient(
            base_url="https://www.myanonamouse.net",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    text="Session type - API session with IP locking",
                    headers={"content-type": "text/html; charset=UTF-8"},
                )
            ),
        ) as http:
            mam = MamClient("accepted", client=http, cookie_store=stored.append)
            await mam.check_mam_id()

    asyncio.run(exercise())
    assert stored == ["accepted"]


def test_authentication_falls_back_to_config_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    class FakeMamClient:
        def __init__(self, mam_id: str, **_: object) -> None:
            self.mam_id = mam_id

        async def check_mam_id(self) -> None:
            attempts.append(self.mam_id)
            if self.mam_id == "stale":
                raise MamError("stale")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(mam_module, "MamClient", FakeMamClient)
    result = asyncio.run(authenticated_mam_client("configured", stored_mam_id="stale"))
    assert result.mam_id == "configured"
    assert attempts == ["stale", "configured"]
