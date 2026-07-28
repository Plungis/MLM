from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Self

import httpx

from . import __version__

USER_AGENT = f"HeavyMLM/{__version__} (+https://github.com/Plungis/MLM)"


class MamError(RuntimeError):
    pass


class MamRateLimitError(MamError):
    pass


class MamWedgeError(MamError):
    pass


class MamClient:
    BASE_URL = "https://www.myanonamouse.net"

    def __init__(
        self,
        mam_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20,
        cookie_store: Callable[[str], None] | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
        self.cookie_store = cookie_store
        self.client.cookies.set("mam_id", mam_id, domain="www.myanonamouse.net")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 429:
            raise MamRateLimitError("Myanonamouse rate limit reached")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise MamError(str(error)) from error

    def _remember_cookie(self) -> None:
        if self.cookie_store is None:
            return
        for cookie in self.client.cookies.jar:
            if cookie.name == "mam_id":
                self.cookie_store(cookie.value)
                return

    async def check_mam_id(self) -> None:
        response = await self.client.get("/json/checkCookie.php")
        self._raise_for_status(response)
        self._remember_cookie()
        if '"Success":true' not in response.text:
            raise MamError("session check failed (Success was false)")

    async def user_info(self) -> dict[str, Any]:
        response = await self.client.get(
            "/jsonLoad.php", params={"snatch_summary": "true"}
        )
        self._raise_for_status(response)
        self._remember_cookie()
        return response.json()

    async def search(self, query: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post("/tor/js/loadSearchJSONbasic.php", json=query)
        self._raise_for_status(response)
        self._remember_cookie()
        result = response.json()
        if isinstance(result, dict) and result.get("error"):
            if result["error"] == "Nothing returned, out of 0":
                return {"data": [], "found": 0}
            raise MamError(str(result["error"]))
        return result

    async def get_torrent_info_by_id(self, torrent_id: int) -> dict[str, Any] | None:
        result = await self.search(
            {
                "description": True,
                "mediaInfo": True,
                "isbn": True,
                "dlLink": True,
                "tor": {"id": torrent_id},
            }
        )
        rows = result.get("data", [])
        return rows[-1] if rows else None

    async def get_torrent_info(self, torrent_hash: str) -> dict[str, Any] | None:
        result = await self.search(
            {
                "description": True,
                "isbn": True,
                "tor": {"hash": torrent_hash},
            }
        )
        rows = result.get("data", [])
        return rows[-1] if rows else None

    async def get_torrent_file(self, download_hash: str, torrent_id: int) -> bytes:
        """Download a .torrent; MaM requires the numeric tid query argument."""
        response = await self.client.get(
            f"/tor/download.php/{download_hash}",
            params={"tid": torrent_id},
        )
        self._raise_for_status(response)
        self._remember_cookie()
        return response.content

    async def wedge_torrent(self, torrent_id: int) -> None:
        timestamp = int(time.time() * 1000)
        response = await self.client.get(
            f"/json/bonusBuy.php/{timestamp}",
            params={
                "spendtype": "personalFL",
                "torrentid": torrent_id,
                "timestamp": timestamp,
            },
        )
        self._raise_for_status(response)
        self._remember_cookie()
        result = response.json()
        if not result.get("success"):
            raise MamWedgeError(str(result.get("error") or "unknown wedge error"))

    async def snatchlist(
        self, kind: str, page: int, cache_timestamp: int
    ) -> dict[str, Any]:
        user = await self.user_info()
        kinds = {
            "unsat": "unsat",
            "inact_unsat": "inactUnsat",
            "seed_unsat": "seedUnsat",
            "seed_sat": "sSat",
            "inact_sat": "inactSat",
            "uploads_active": "upAct",
        }
        response = await self.client.get(
            "https://cdn.myanonamouse.net/json/loadUserDetailsTorrents.php",
            params={
                "uid": user["uid"],
                "iteration": page,
                "type": kinds[kind],
                "cacheTime": cache_timestamp,
            },
        )
        self._raise_for_status(response)
        self._remember_cookie()
        return response.json()
