from __future__ import annotations

from typing import Iterable

import httpx


class QbitError(RuntimeError):
    pass


class QbitClient:
    def __init__(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout
        )

    async def __aenter__(self) -> QbitClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    @staticmethod
    def _check(response: httpx.Response) -> httpx.Response:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise QbitError(str(error)) from error
        if response.text.strip() == "Fails.":
            raise QbitError("qBittorrent rejected the request")
        return response

    async def login(self, username: str = "", password: str = "") -> None:
        response = await self.client.post(
            "/api/v2/auth/login",
            data={"username": username, "password": password},
        )
        self._check(response)

    async def add_torrent(
        self,
        torrent_file: bytes,
        *,
        category: str | None = None,
        tags: Iterable[str] = (),
        paused: bool = False,
    ) -> None:
        data: dict[str, str] = {"paused": str(paused).lower()}
        if category:
            data["category"] = category
        tags_value = ",".join(tags)
        if tags_value:
            data["tags"] = tags_value
        response = await self.client.post(
            "/api/v2/torrents/add",
            data=data,
            files={"torrents": ("download.torrent", torrent_file, "application/x-bittorrent")},
        )
        self._check(response)

    async def torrents(self, *, hashes: Iterable[str] = ()) -> list[dict]:
        hashes_value = "|".join(hashes)
        params = {"hashes": hashes_value} if hashes_value else None
        response = self._check(
            await self.client.get("/api/v2/torrents/info", params=params)
        )
        return response.json()
