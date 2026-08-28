from __future__ import annotations

import re
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
    def __init__(
        self,
        message: str,
        *,
        reason: str = "unknown",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.context = context or {}


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

    def set_mam_id(self, mam_id: str) -> None:
        """Replace the live API-session cookie after an in-app config save."""
        self.client.cookies.set(
            "mam_id",
            mam_id,
            domain="www.myanonamouse.net",
        )

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
        text = response.text.lstrip("\ufeff")
        result: dict[str, Any] | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                result = parsed
        except ValueError:
            pass
        success = result is not None and result.get("Success") is True
        if not success:
            success = (
                re.search(
                    r"""["']?Success["']?\s*:\s*true\b""",
                    text,
                    flags=re.IGNORECASE,
                )
                is not None
            )
        if not success:
            success = text.strip().casefold().startswith("session type - api session")
        if not success:
            detail = None
            if result is not None:
                detail = result.get("Error") or result.get("Message")
            if detail:
                raise MamError(f"session check rejected the mam_id cookie: {detail}")
            content_type = response.headers.get("content-type", "unknown")
            raise MamError(
                "session check did not report success "
                f"(content-type={content_type}, response-bytes={len(response.content)})"
            )
        self._remember_cookie()

    async def user_info(self) -> dict[str, Any]:
        response = await self.client.get(
            "/jsonLoad.php", params={"snatch_summary": "true"}
        )
        self._raise_for_status(response)
        self._remember_cookie()
        return response.json()

    async def request_json(
        self,
        path: str,
        *,
        params: Any = None,
    ) -> Any:
        """Make an authenticated JSON GET request for suite modules."""
        response = await self.client.get(path, params=params)
        self._raise_for_status(response)
        self._remember_cookie()
        try:
            return response.json()
        except ValueError as error:
            raise MamError(
                "Myanonamouse returned a non-JSON response "
                f"from {path} (content-type="
                f"{response.headers.get('content-type', 'unknown')})"
            ) from error

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

    async def get_torrent_file(
        self,
        torrent_id: int,
        *,
        use_wedge: bool = False,
    ) -> bytes:
        """Download a torrent through MaM's documented automation endpoint."""
        endpoint = f"/tor/download.php?tid={torrent_id}"
        if use_wedge:
            # MaM documents `fl` as an empty GET argument. Keep it bare instead of
            # using an unrelated bonus-purchase API that rejects API sessions.
            endpoint += "&fl"
        try:
            response = await self.client.get(endpoint)
        except Exception as error:
            if use_wedge:
                raise MamWedgeError(
                    f"wedge download could not reach MaM: {error}",
                    reason="request_failed",
                    context={
                        "stage": "wedge_download_request",
                        "endpoint": endpoint,
                        "torrent_id": torrent_id,
                    },
                ) from error
            raise
        try:
            self._raise_for_status(response)
        except MamRateLimitError:
            raise
        except Exception as error:
            if use_wedge:
                raise MamWedgeError(
                    (
                        "MaM wedge download returned HTTP "
                        f"{response.status_code}: {error}"
                    ),
                    reason="http_error",
                    context={
                        "stage": "wedge_download_http_response",
                        "endpoint": endpoint,
                        "torrent_id": torrent_id,
                        "http_status": response.status_code,
                        "content_type": response.headers.get("content-type"),
                        "response_preview": response.text[:500],
                    },
                ) from error
            raise
        self._remember_cookie()
        if not response.content.startswith(b"d"):
            context = {
                "stage": (
                    "wedge_download_response"
                    if use_wedge
                    else "torrent_download_response"
                ),
                "endpoint": endpoint,
                "torrent_id": torrent_id,
                "http_status": response.status_code,
                "content_type": response.headers.get("content-type"),
                "response_preview": response.text[:500],
            }
            if not use_wedge:
                raise MamError(
                    "MaM torrent download did not return a torrent file "
                    f"(content-type={context['content_type']})"
                )
            raise MamWedgeError(
                "MaM did not return a torrent file after the wedge request",
                reason="download_rejected",
                context=context,
            )
        return response.content

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


async def authenticated_mam_client(
    configured_mam_id: str,
    *,
    stored_mam_id: str | None = None,
    cookie_store: Callable[[str], None] | None = None,
) -> MamClient:
    """Authenticate with a stored cookie, then fall back to the configured one."""
    candidates = [stored_mam_id, configured_mam_id]
    attempted: set[str] = set()
    last_error: MamError | None = None
    for candidate in candidates:
        if not candidate or candidate in attempted:
            continue
        attempted.add(candidate)
        client = MamClient(candidate, cookie_store=cookie_store)
        try:
            await client.check_mam_id()
        except MamError as error:
            last_error = error
            await client.close()
            continue
        return client
    if last_error is not None:
        raise last_error
    raise MamError("no mam_id cookie is configured")
