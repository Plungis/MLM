from __future__ import annotations

from collections.abc import Iterable
from typing import Self

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

    async def __aenter__(self) -> Self:
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
        if category and str(category).strip():
            resolved_category = await self.resolve_category_name(
                str(category).strip()
            )
            data["category"] = resolved_category
        tags_value = ",".join(str(t) for t in tags if str(t).strip())
        if tags_value:
            data["tags"] = tags_value
        response = await self.client.post(
            "/api/v2/torrents/add",
            data=data,
            files={
                "torrents": (
                    "download.torrent",
                    torrent_file,
                    "application/x-bittorrent",
                )
            },
        )
        self._check(response)

    async def torrents(
        self, *, hashes: Iterable[str] = (), category: str | None = None
    ) -> list[dict]:
        hashes_value = "|".join(hashes)
        params = {}
        if hashes_value:
            params["hashes"] = hashes_value
        if category is not None:
            params["category"] = category
        response = self._check(
            await self.client.get("/api/v2/torrents/info", params=params or None)
        )
        return response.json()

    async def files(self, torrent_hash: str) -> list[dict]:
        response = self._check(
            await self.client.get(
                "/api/v2/torrents/files", params={"hash": torrent_hash}
            )
        )
        return response.json()

    async def trackers(self, torrent_hash: str) -> list[dict]:
        response = self._check(
            await self.client.get(
                "/api/v2/torrents/trackers", params={"hash": torrent_hash}
            )
        )
        return response.json()

    async def categories(self) -> dict[str, dict]:
        response = self._check(await self.client.get("/api/v2/torrents/categories"))
        return response.json()

    async def resolve_category_name(self, category: str) -> str:
        clean = str(category or "").strip()
        if not clean:
            return ""
        existing = await self.categories()
        if clean in existing:
            return clean
        target_fold = clean.casefold()
        for existing_name in existing:
            if str(existing_name).strip().casefold() == target_fold:
                return str(existing_name)
        response = await self.client.post(
            "/api/v2/torrents/createCategory", data={"category": clean}
        )
        self._check(response)
        return clean

    async def ensure_category(self, category: str) -> None:
        await self.resolve_category_name(category)

    async def set_category(self, hashes: Iterable[str], category: str) -> None:
        hash_list = [h for h in hashes if h]
        clean = str(category or "").strip()
        if not hash_list or not clean:
            return
        resolved = await self.resolve_category_name(clean)
        response = await self.client.post(
            "/api/v2/torrents/setCategory",
            data={"hashes": "|".join(hash_list), "category": resolved},
        )
        self._check(response)

    async def add_tags(self, hashes: Iterable[str], tags: Iterable[str]) -> None:
        hash_list = [h for h in hashes if h]
        tags_value = ",".join(str(t) for t in tags if str(t).strip())
        if not hash_list or not tags_value:
            return
        response = await self.client.post(
            "/api/v2/torrents/addTags",
            data={"hashes": "|".join(hash_list), "tags": tags_value},
        )
        self._check(response)

    async def resume_torrents(self, hashes: Iterable[str]) -> None:
        hash_list = [h for h in hashes if h]
        if not hash_list:
            return
        response = await self.client.post(
            "/api/v2/torrents/resume",
            data={"hashes": "|".join(hash_list)},
        )
        self._check(response)

    async def recheck_torrents(self, hashes: Iterable[str]) -> None:
        hash_list = [h for h in hashes if h]
        if not hash_list:
            return
        response = await self.client.post(
            "/api/v2/torrents/recheck",
            data={"hashes": "|".join(hash_list)},
        )
        self._check(response)

    async def default_save_path(self) -> str:
        try:
            response = self._check(await self.client.get("/api/v2/app/defaultSavePath"))
            return response.text.strip()
        except Exception:
            return ""
