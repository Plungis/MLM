from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import math
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .autograbber import select_row
from .config import (
    Config,
    ConfigError,
    load_config,
    save_config_text,
    save_root_config_values,
)
from .database import ensure_database
from .error_guidance import error_guidance
from .mam import authenticated_mam_client
from .modules.absidekick import SOURCE_VERSION, ABSidekickService
from .modules.absidekick.core import ABSAPIError
from .modules.heavymlm.search import (
    filter_search_results,
    present_search_result,
    search_seed,
)
from .modules.mam_spender import default_public_state
from .repository import Repository
from .request_auth import hash_request_password, verify_request_password
from .request_portal import (
    GoodreadsLookupError,
    goodreads_book_id,
    lookup_goodreads_book,
)
from .scheduler import ServiceState
from .search import as_int

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

SUITE_MODULES = {
    "heavymlm": {
        "name": "HeavyMLM",
        "icon": "H_",
        "status": "Active",
        "summary": "Automated MyAnonamouse library acquisition and organization.",
    },
    "absidekick": {
        "name": "ABSidekick",
        "icon": "A_",
        "status": "Active",
        "summary": "Audiobookshelf metadata matching, review, and library repair.",
    },
    "mam-spender": {
        "name": "MAM-Spender",
        "icon": "M$",
        "status": "Active",
        "summary": "Automated bonus-point spending, history, and account intelligence.",
    },
}


def _redacted_config(config: Config) -> dict:
    value = asdict(config)
    value["mam_id"] = "***" if config.mam_id else ""
    if value.get("audiobookshelf"):
        value["audiobookshelf"]["token"] = "***"
    for row in value.get("qbittorrent", []):
        if row.get("password"):
            row["password"] = "***"
    for row in value.get("notion_lists", []):
        if row.get("token"):
            row["token"] = "***"
    if value.get("request_portal_access_code"):
        value["request_portal_access_code"] = "***"
    if value.get("request_portal_password_hash"):
        value["request_portal_password_hash"] = "***"
    return value


def create_app(config_path: Path, database_path: Path) -> FastAPI:
    ensure_database(database_path)
    config = load_config(config_path)
    repository = Repository(database_path)
    absidekick = ABSidekickService(database_path.parent / "absidekick")
    snapshot_cache: dict[str, object] = {"expires": 0.0, "value": None}
    snapshot_lock = asyncio.Lock()
    request_rate_events: dict[str, deque[float]] = defaultdict(deque)
    request_rate_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        mam = await authenticated_mam_client(
            config.mam_id,
            stored_mam_id=repository.config_value("mam_id"),
            cookie_store=lambda value: repository.set_config_value("mam_id", value),
        )
        state = ServiceState(config, repository, mam)
        app.state.services = state
        try:
            state.start()
            yield
        finally:
            await state.close()

    app = FastAPI(title="MyAnonaSuite", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    def active_config() -> Config:
        if hasattr(app.state, "services"):
            return app.state.services.config
        return config

    def spender_service():
        services = getattr(app.state, "services", None)
        spender = getattr(services, "mam_spender", None)
        if spender is None:
            raise HTTPException(503, "MAM-Spender is still starting")
        return spender

    def local_request(request: Request) -> bool:
        client_host = request.client.host if request.client else ""
        if client_host == "testclient":
            return True
        try:
            return ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            return False

    def request_portal_host(request: Request) -> bool:
        hostname = (request.url.hostname or "").casefold()
        return hostname in {
            domain.strip().casefold()
            for domain in active_config().request_portal_domains
        }

    def request_portal_root(request: Request) -> str:
        return "/" if request_portal_host(request) else "/request"

    def request_access_token(config: Config) -> str:
        if config.request_portal_password_hash:
            credential = "\0".join(
                (
                    "credentials",
                    config.request_portal_username,
                    config.request_portal_password_hash,
                )
            )
        else:
            # Preserve tokens issued by the legacy shared-code login.
            credential = config.request_portal_access_code
        return hmac.new(
            config.mam_id.encode("utf-8"),
            credential.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def request_login_mode(config: Config) -> str:
        if config.request_portal_password_hash:
            return "credentials"
        if config.request_portal_access_code:
            return "access_code"
        return "public"

    def request_portal_authorized(request: Request) -> bool:
        # Keep localhost preview convenient, but never let a same-machine reverse
        # proxy make the configured public hostname look like a local request.
        if local_request(request) and not request_portal_host(request):
            return True
        current = active_config()
        if request_login_mode(current) == "public":
            return True
        supplied = request.cookies.get("mysuite_request_access", "")
        return hmac.compare_digest(supplied, request_access_token(current))

    def request_client_key(request: Request) -> str:
        client_host = request.client.host if request.client else "unknown"
        try:
            proxy_is_local = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            proxy_is_local = False
        forwarded = request.headers.get("x-forwarded-for", "") if proxy_is_local else ""
        candidate = forwarded.split(",", 1)[0].strip() if forwarded else client_host
        return candidate or "unknown"

    async def request_rate_allowed(request: Request, action: str) -> bool:
        now = time.monotonic()
        key = f"{action}:{request_client_key(request)}"
        limit = active_config().request_portal_rate_limit
        async with request_rate_lock:
            events = request_rate_events[key]
            while events and now - events[0] >= 60:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True

    @app.middleware("http")
    async def isolate_request_domain(request: Request, call_next):
        if not request_portal_host(request):
            return await call_next(request)
        if not active_config().request_portal_enabled:
            return HTMLResponse("Request portal is disabled", status_code=404)
        path = request.url.path
        allowed = path in {
            "/",
            "/request",
            "/request/unlock",
            "/request/logout",
            "/request/submit",
        } or path.startswith("/static/")
        if not allowed:
            return HTMLResponse("Not found", status_code=404)
        return await call_next(request)

    async def ui_snapshot() -> dict:
        now = time.monotonic()
        cached = snapshot_cache["value"]
        if cached is not None and now < float(snapshot_cache["expires"]):
            return cached  # type: ignore[return-value]
        async with snapshot_lock:
            now = time.monotonic()
            cached = snapshot_cache["value"]
            if cached is not None and now < float(snapshot_cache["expires"]):
                return cached  # type: ignore[return-value]
            snapshot = await asyncio.to_thread(repository.ui_snapshot)
            snapshot_cache.update(value=snapshot, expires=now + 0.75)
            return snapshot

    async def context(request: Request, **values: object) -> dict:
        snapshot = await ui_snapshot()
        counts = snapshot["counts"]
        suite_module = str(values.pop("suite_module", "heavymlm"))
        return {
            "request": request,
            "counts": counts,
            "pipeline": snapshot["pipeline"],
            "list_tracking": snapshot["list_tracking"],
            "request_tracking": snapshot["request_tracking"],
            "record_total": sum(counts.values()),
            "jobs": app.state.services.jobs if hasattr(app.state, "services") else {},
            "mam_stats": (
                app.state.services.mam_stats if hasattr(app.state, "services") else {}
            ),
            "version": __version__,
            "suite_module": suite_module,
            "suite_info": SUITE_MODULES[suite_module],
            "suite_modules": SUITE_MODULES,
            **values,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        if request_portal_host(request):
            return await request_portal_page(request)
        return templates.TemplateResponse(
            request,
            "index.html",
            await context(
                request,
                title="Dashboard",
                triggered=request.query_params.get("triggered"),
            ),
        )

    async def prepare_error_rows(rows: list[dict]) -> None:
        pending_mam_ids = {
            int(row["mam_id"])
            for row in await asyncio.to_thread(repository.pending_selected)
        }
        for row in rows:
            identifier = row.get("id")
            row["_error_id"] = json.dumps(
                identifier,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            row["_mam_id"] = (
                identifier.get("Grabber") if isinstance(identifier, dict) else None
            )
            row["_retryable"] = (
                row["_mam_id"] is not None and int(row["_mam_id"]) in pending_mam_ids
            )
            row["_guidance"] = error_guidance(str(row.get("error", "")))

    @app.get("/operations", response_class=HTMLResponse)
    async def operations(
        request: Request,
        view: str = "diagnostics",
        component: str = "",
        page: int = 1,
        live: str = "1",
    ) -> HTMLResponse:
        selected_view = (
            view if view in {"diagnostics", "events", "errors"} else "diagnostics"
        )
        page = max(1, page)
        page_size = 50
        activity: list[dict] = []
        rows: list[dict] = []
        total = 0
        if selected_view == "diagnostics":
            activity = await asyncio.to_thread(
                repository.recent_activity,
                limit=300,
                component=component or None,
            )
        else:
            table = "events" if selected_view == "events" else "errored_torrents"
            rows = await asyncio.to_thread(
                repository.table_rows,
                table,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            snapshot = await ui_snapshot()
            total = int(snapshot["counts"].get(table, 0))
            if selected_view == "errors":
                await prepare_error_rows(rows)
        page_count = max(1, math.ceil(total / page_size))
        if selected_view != "diagnostics" and page > page_count:
            return RedirectResponse(
                f"/operations?{urlencode({'view': selected_view, 'page': page_count})}",
                status_code=307,
            )
        return templates.TemplateResponse(
            request,
            "operations.html",
            await context(
                request,
                title="Operations",
                operations_view=selected_view,
                activity=activity,
                component=component,
                live=live != "0",
                rows=rows,
                page=page,
                page_count=page_count,
                total=total,
                first_record=(page - 1) * page_size + 1 if rows else 0,
                last_record=(page - 1) * page_size + len(rows),
                retried=request.query_params.get("retried") == "1",
                dismissed=request.query_params.get("dismissed") == "1",
            ),
        )

    @app.get("/lists", response_class=HTMLResponse)
    async def combined_lists(
        request: Request,
        list_id: str = "",
        page: int = 1,
    ) -> HTMLResponse:
        page = max(1, page)
        page_size = 50
        sources = await asyncio.to_thread(
            repository.table_rows, "lists", limit=500, offset=0
        )
        item_counts = await asyncio.to_thread(repository.list_item_counts_by_list)
        rows = await asyncio.to_thread(
            repository.list_item_rows,
            list_id=list_id or None,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await asyncio.to_thread(repository.list_item_count, list_id or None)
        page_count = max(1, math.ceil(total / page_size))
        if page > page_count:
            query = {"page": page_count}
            if list_id:
                query["list_id"] = list_id
            return RedirectResponse(f"/lists?{urlencode(query)}", status_code=307)
        selected_source = next(
            (source for source in sources if str(source.get("id")) == list_id),
            None,
        )
        return templates.TemplateResponse(
            request,
            "lists.html",
            await context(
                request,
                title="Lists",
                sources=sources,
                item_counts=item_counts,
                selected_list_id=list_id,
                selected_source=selected_source,
                rows=rows,
                page=page,
                page_count=page_count,
                total=total,
                first_record=(page - 1) * page_size + 1 if rows else 0,
                last_record=(page - 1) * page_size + len(rows),
            ),
        )

    @app.get("/library", response_class=HTMLResponse)
    async def combined_library(
        request: Request,
        view: str = "processed",
        page: int = 1,
    ) -> HTMLResponse:
        selected_view = view if view in {"processed", "duplicates"} else "processed"
        table = "torrents" if selected_view == "processed" else "duplicate_torrents"
        page = max(1, page)
        page_size = 50
        rows = await asyncio.to_thread(
            repository.table_rows,
            table,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        snapshot = await ui_snapshot()
        total = int(snapshot["counts"].get(table, 0))
        page_count = max(1, math.ceil(total / page_size))
        if page > page_count:
            return RedirectResponse(
                f"/library?{urlencode({'view': selected_view, 'page': page_count})}",
                status_code=307,
            )
        return templates.TemplateResponse(
            request,
            "library.html",
            await context(
                request,
                title="MLM Processed Library",
                library_view=selected_view,
                table=table,
                rows=rows,
                page=page,
                page_count=page_count,
                total=total,
                first_record=(page - 1) * page_size + 1 if rows else 0,
                last_record=(page - 1) * page_size + len(rows),
            ),
        )

    @app.get("/records/{table}", response_class=HTMLResponse)
    async def records(request: Request, table: str, page: int = 1) -> HTMLResponse:
        consolidated = {
            "events": ("/operations", "events"),
            "errored_torrents": ("/operations", "errors"),
            "lists": ("/lists", ""),
            "list_items": ("/lists", ""),
            "torrents": ("/library", "processed"),
            "duplicate_torrents": ("/library", "duplicates"),
        }
        if table in consolidated:
            target, view = consolidated[table]
            query = dict(request.query_params)
            if view:
                query["view"] = view
            suffix = f"?{urlencode(query)}" if query else ""
            return RedirectResponse(target + suffix, status_code=307)
        page = max(1, page)
        page_size = 50
        try:
            rows = await asyncio.to_thread(
                repository.table_rows,
                table,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        snapshot = await ui_snapshot()
        total = int(snapshot["counts"].get(table, 0))
        page_count = max(1, math.ceil(total / page_size))
        if page > page_count:
            return RedirectResponse(
                f"/records/{table}?page={page_count}", status_code=307
            )
        return templates.TemplateResponse(
            request,
            "records.html",
            await context(
                request,
                title=table.replace("_", " ").title(),
                table=table,
                rows=rows,
                page=page,
                page_count=page_count,
                total=total,
                first_record=(page - 1) * page_size + 1 if rows else 0,
                last_record=(page - 1) * page_size + len(rows),
                retried=request.query_params.get("retried") == "1",
                dismissed=request.query_params.get("dismissed") == "1",
            ),
        )

    @app.post("/errors/retry")
    async def retry_error(
        error_id: str = Form(...), mam_id: int | None = Form(None)
    ) -> RedirectResponse:
        try:
            identifier = json.loads(error_id)
        except json.JSONDecodeError as error:
            raise HTTPException(400, "invalid error identifier") from error
        if mam_id is None or not repository.has_pending_mam_id(mam_id):
            raise HTTPException(
                409, "release is no longer waiting in the download queue"
            )
        repository.delete_error(identifier)
        asyncio.create_task(app.state.services.trigger("downloader"))
        return RedirectResponse("/operations?view=errors&retried=1", status_code=303)

    @app.post("/errors/dismiss")
    async def dismiss_error(error_id: str = Form(...)) -> RedirectResponse:
        try:
            identifier = json.loads(error_id)
        except json.JSONDecodeError as error:
            raise HTTPException(400, "invalid error identifier") from error
        repository.delete_error(identifier)
        return RedirectResponse("/operations?view=errors&dismissed=1", status_code=303)

    mam_spender_views = {"dashboard", "config", "history", "analytics", "mam-data"}
    mam_spender_aliases = {
        "settings": "config",
        "graph": "analytics",
        "mamdata": "mam-data",
    }

    async def render_mam_spender(request: Request, view: str) -> HTMLResponse:
        normalized = mam_spender_aliases.get(view.casefold(), view.casefold())
        if normalized not in mam_spender_views:
            raise HTTPException(404, f"unknown MAM-Spender page: {view}")
        services = getattr(app.state, "services", None)
        spender = getattr(services, "mam_spender", None)
        spender_state = (
            spender.public_state()
            if spender is not None
            else default_public_state(repository)
        )
        current = active_config()
        labels = {
            "dashboard": "Dashboard",
            "config": "Config",
            "history": "History",
            "analytics": "Graph",
            "mam-data": "All MaM Data",
        }
        return templates.TemplateResponse(
            request,
            "mam_spender.html",
            await context(
                request,
                title=f"MAM-Spender {labels[normalized]}",
                suite_module="mam-spender",
                spender=spender_state,
                spender_view=normalized,
                spender_runtime={
                    "suite_version": __version__,
                    "source_version": "MAM-Spender Web Edition v1.4.0",
                    "host": current.web_host,
                    "port": current.web_port,
                    "config_path": str(config_path) if local_request(request) else None,
                },
            ),
        )

    @app.get("/suite/mam-spender/{view}", response_class=HTMLResponse)
    async def mam_spender_page(request: Request, view: str) -> HTMLResponse:
        return await render_mam_spender(request, view)

    absidekick_views = {
        "connection",
        "targeting",
        "matching",
        "tags",
        "run",
        "review",
    }
    absidekick_aliases = {"config": "connection", "dashboard": "run"}

    async def render_absidekick(request: Request, view: str) -> HTMLResponse:
        normalized = absidekick_aliases.get(view.casefold(), view.casefold())
        if normalized not in absidekick_views:
            raise HTTPException(404, f"unknown ABSidekick page: {view}")
        labels = {
            "connection": "Config",
            "targeting": "Targeting",
            "matching": "Matching",
            "tags": "Tags & Actions",
            "run": "Run Center",
            "review": "Review Desk",
        }
        return templates.TemplateResponse(
            request,
            "absidekick.html",
            await context(
                request,
                title=f"ABSidekick {labels[normalized]}",
                suite_module="absidekick",
                absidekick_view=normalized,
                absidekick_source_version=SOURCE_VERSION,
            ),
        )

    @app.get("/suite/absidekick/{view}", response_class=HTMLResponse)
    async def absidekick_page(request: Request, view: str) -> HTMLResponse:
        return await render_absidekick(request, view)

    @app.get("/suite/{module}", response_class=HTMLResponse)
    async def suite_module_page(
        request: Request, module: str, view: str = "dashboard"
    ) -> HTMLResponse:
        if module not in {"absidekick", "mam-spender"}:
            raise HTTPException(404, f"unknown suite module: {module}")
        if module == "mam-spender":
            return await render_mam_spender(request, view)
        return await render_absidekick(request, view)

    async def absidekick_payload(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
        except json.JSONDecodeError as error:
            raise ValueError("request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    async def absidekick_call(request: Request, function, *args):
        if not local_request(request):
            return JSONResponse(
                {"ok": False, "error": "ABSidekick controls are local-only"},
                status_code=403,
            )
        try:
            return await asyncio.to_thread(function, *args)
        except ABSAPIError as error:
            return JSONResponse(
                {"ok": False, "error": str(error), "body": error.body},
                status_code=error.status or 502,
            )
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        except RuntimeError as error:
            return JSONResponse(
                {
                    "ok": False,
                    "error": str(error),
                    "job": absidekick.job_snapshot(),
                },
                status_code=409,
            )
        except LookupError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=404)
        except Exception as error:  # noqa: BLE001 - return JSON to module UI
            return JSONResponse({"ok": False, "error": str(error)}, status_code=500)

    @app.get("/api/absidekick/state")
    async def absidekick_state(request: Request):
        return await absidekick_call(request, absidekick.public_state)

    @app.get("/api/absidekick/libraries")
    async def absidekick_libraries(request: Request):
        return await absidekick_call(request, absidekick.libraries)

    @app.get("/api/absidekick/filter-data")
    async def absidekick_filter_data(request: Request, libraryId: str = ""):
        return await absidekick_call(request, absidekick.filter_data, libraryId)

    @app.get("/api/absidekick/job")
    async def absidekick_job(request: Request):
        return await absidekick_call(
            request, lambda: {"ok": True, "job": absidekick.job_snapshot()}
        )

    @app.get("/api/absidekick/item-cover/{item_id}")
    async def absidekick_item_cover(request: Request, item_id: str):
        result = await absidekick_call(request, absidekick.cover, item_id)
        if isinstance(result, JSONResponse):
            return result
        content, content_type = result
        return Response(
            content,
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=300"},
        )

    @app.post("/api/absidekick/connect")
    async def absidekick_connect(request: Request):
        try:
            payload = await absidekick_payload(request)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        return await absidekick_call(request, absidekick.connect, payload)

    @app.post("/api/absidekick/settings")
    async def absidekick_settings(request: Request):
        try:
            payload = await absidekick_payload(request)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        return await absidekick_call(request, absidekick.save, payload)

    @app.post("/api/absidekick/preview")
    async def absidekick_preview(request: Request):
        try:
            payload = await absidekick_payload(request)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        return await absidekick_call(request, absidekick.preview, payload)

    @app.post("/api/absidekick/review/{action}")
    async def absidekick_review(request: Request, action: str):
        try:
            payload = await absidekick_payload(request)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        if action == "scan":
            function = absidekick.scan_review
        elif action == "approve":
            function = absidekick.approve_review
        elif action == "reject":
            function = absidekick.reject_review
        else:
            return JSONResponse(
                {"ok": False, "error": "unknown review action"},
                status_code=404,
            )
        return await absidekick_call(request, function, payload)

    @app.post("/api/absidekick/job/{action}")
    async def absidekick_job_action(request: Request, action: str):
        if action == "start":
            try:
                payload = await absidekick_payload(request)
            except ValueError as error:
                return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
            return await absidekick_call(request, absidekick.start, payload)
        if action in {"pause", "resume", "cancel"}:
            return await absidekick_call(request, absidekick.job_action, action)
        return JSONResponse(
            {"ok": False, "error": "unknown job action"}, status_code=404
        )

    @app.get("/api/mam-spender/state")
    async def mam_spender_state(request: Request) -> dict:
        if not local_request(request):
            raise HTTPException(403, "MAM-Spender controls are local-only")
        return spender_service().public_state()

    @app.post("/api/mam-spender/settings")
    async def save_mam_spender_settings(request: Request) -> dict:
        if not local_request(request):
            raise HTTPException(403, "MAM-Spender controls are local-only")
        try:
            payload = await request.json()
            return spender_service().update_settings(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/mam-spender/session")
    async def save_mam_spender_session(request: Request) -> dict:
        if not local_request(request):
            raise HTTPException(403, "MAM-Spender controls are local-only")
        try:
            payload = await request.json()
            return spender_service().save_session_id(str(payload.get("value", "")))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/mam-spender/import")
    async def import_mam_spender_config(request: Request) -> dict:
        if not local_request(request):
            raise HTTPException(403, "MAM-Spender controls are local-only")
        try:
            incoming = await request.json()
            payload = incoming.get("config", incoming)
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise ValueError("legacy config must be a JSON object")
            return spender_service().import_legacy(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/mam-spender/{action}")
    async def mam_spender_action(request: Request, action: str) -> dict:
        if not local_request(request):
            raise HTTPException(403, "MAM-Spender controls are local-only")
        spender = spender_service()
        if action == "start":
            return spender.start_scheduler()
        if action == "pause":
            return spender.pause_scheduler()
        if action == "run":
            payload = await request.json()
            return spender.run_now(
                fl_only_override=bool(payload.get("fl_only_override", False))
            )
        if action == "reset-totals":
            return spender.reset_totals()
        if action == "refresh-account":
            return await spender.refresh_mam_user_data()
        if action == "refresh-bonus-history":
            return await spender.refresh_bonus_history()
        raise HTTPException(404, "unknown MAM-Spender action")

    @app.get("/config", response_class=HTMLResponse)
    async def show_config(request: Request) -> HTMLResponse:
        editable = local_request(request)
        return templates.TemplateResponse(
            request,
            "config.html",
            await context(
                request,
                title="Configuration",
                config=_redacted_config(active_config()),
                saved=request.query_params.get("saved") == "1",
                error=None,
                config_path=str(config_path),
                config_toml=(
                    config_path.read_text(encoding="utf-8") if editable else None
                ),
            ),
        )

    @app.post("/config", response_class=HTMLResponse)
    async def save_config(
        request: Request,
        min_ratio: str = Form(...),
        unsat_buffer: str = Form(...),
        max_unsat_slots: str = Form(""),
        wedge_buffer: str = Form(...),
        prefer_wedges: str | None = Form(None),
        download_on_wedge_failure: str | None = Form(None),
        grab_both_formats: str | None = Form(None),
        add_torrents_stopped: str | None = Form(None),
        request_portal_enabled: str | None = Form(None),
        request_portal_domains: str = Form(""),
        request_portal_title: str = Form("Library Requests"),
        request_portal_rate_limit: str = Form("20"),
        request_portal_username: str = Form(""),
        request_portal_password: str = Form(""),
        clear_request_portal_credentials: str | None = Form(None),
        request_portal_access_code: str = Form(""),
        clear_request_portal_access_code: str | None = Form(None),
        search_interval: str = Form(...),
        import_interval: str = Form(...),
        link_interval: str = Form(...),
    ) -> HTMLResponse:
        try:
            if not local_request(request):
                raise ConfigError(
                    "configuration changes are only allowed from this computer"
                )
            values: dict[str, object | None] = {
                "min_ratio": float(min_ratio),
                "unsat_buffer": int(unsat_buffer),
                "max_unsat_slots": (
                    int(max_unsat_slots) if max_unsat_slots.strip() else None
                ),
                "wedge_buffer": int(wedge_buffer),
                "prefer_wedges": prefer_wedges is not None,
                "download_on_wedge_failure": (download_on_wedge_failure is not None),
                "grab_both_formats": grab_both_formats is not None,
                "add_torrents_stopped": add_torrents_stopped is not None,
                "request_portal_enabled": request_portal_enabled is not None,
                "request_portal_domains": tuple(
                    domain.strip()
                    for domain in request_portal_domains.replace("\n", ",").split(",")
                    if domain.strip()
                ),
                "request_portal_title": request_portal_title.strip()
                or "Library Requests",
                "request_portal_rate_limit": int(request_portal_rate_limit),
                "search_interval": int(search_interval),
                "import_interval": int(import_interval),
                "link_interval": int(link_interval),
            }
            current = active_config()
            if clear_request_portal_credentials is not None:
                values["request_portal_username"] = None
                values["request_portal_password_hash"] = None
            else:
                username = (
                    request_portal_username.strip() or current.request_portal_username
                )
                password_hash = current.request_portal_password_hash
                if request_portal_password:
                    password_hash = await asyncio.to_thread(
                        hash_request_password, request_portal_password
                    )
                if username or password_hash:
                    if not username or not password_hash:
                        raise ConfigError(
                            "enter both a requester username and password"
                        )
                    values["request_portal_username"] = username
                    values["request_portal_password_hash"] = password_hash
            if request_portal_access_code:
                values["request_portal_access_code"] = request_portal_access_code
            elif clear_request_portal_access_code is not None:
                values["request_portal_access_code"] = None
            updated = save_root_config_values(config_path, values)
            await app.state.services.reconfigure(updated)
        except (ConfigError, ValueError) as error:
            return templates.TemplateResponse(
                request,
                "config.html",
                await context(
                    request,
                    title="Configuration",
                    config=_redacted_config(active_config()),
                    saved=False,
                    error=str(error),
                    config_path=str(config_path),
                    config_toml=(
                        config_path.read_text(encoding="utf-8")
                        if local_request(request)
                        else None
                    ),
                ),
                status_code=400,
            )
        return RedirectResponse("/config?saved=1", status_code=303)

    @app.post("/config/full", response_class=HTMLResponse)
    async def save_full_config(
        request: Request,
        config_toml: str = Form(...),
    ) -> HTMLResponse:
        try:
            if not local_request(request):
                raise ConfigError(
                    "configuration changes are only allowed from this computer"
                )
            updated = save_config_text(config_path, config_toml)
            await app.state.services.reconfigure(updated)
        except (ConfigError, ValueError) as error:
            return templates.TemplateResponse(
                request,
                "config.html",
                await context(
                    request,
                    title="Configuration",
                    config=_redacted_config(active_config()),
                    saved=False,
                    error=str(error),
                    config_path=str(config_path),
                    config_toml=config_toml,
                ),
                status_code=400,
            )
        return RedirectResponse("/config?saved=1", status_code=303)

    @app.get("/diagnostics")
    async def diagnostics_redirect(request: Request) -> RedirectResponse:
        query = dict(request.query_params)
        query["view"] = "diagnostics"
        return RedirectResponse(f"/operations?{urlencode(query)}", status_code=307)

    def manual_search_filters(
        *,
        q: str = "",
        title: str = "",
        author: str = "",
        series: str = "",
        narrator: str = "",
        filetype: str = "",
        category: str = "",
        language: str = "",
        availability: str = "",
        min_seeders: int = 0,
        sort: str = "relevance",
    ) -> dict[str, object]:
        return {
            "q": q.strip(),
            "title": title.strip(),
            "author": author.strip(),
            "series": series.strip(),
            "narrator": narrator.strip(),
            "filetype": filetype.strip().casefold(),
            "category": category.strip().casefold(),
            "language": language.strip(),
            "availability": availability.strip().casefold(),
            "min_seeders": max(0, min_seeders),
            "sort": sort.strip() or "relevance",
        }

    def search_was_requested(filters: dict[str, object]) -> bool:
        return any(
            value
            for name, value in filters.items()
            if name != "sort" and value not in {"", 0}
        )

    async def run_manual_search(filters: dict[str, object]) -> dict[str, object]:
        rows: list[dict] = []
        found = 0
        scanned = 0
        error = None
        if search_was_requested(filters):
            try:
                seed, search_in = search_seed(filters)
                raw_by_id: dict[int, dict] = {}
                start = 0
                for _ in range(5):
                    tor: dict[str, object] = {"startNumber": start}
                    if seed:
                        tor["text"] = seed
                    if search_in:
                        tor["srchIn"] = search_in
                    result = await app.state.services.mam.search(
                        {
                            "dlLink": True,
                            "mediaInfo": True,
                            "isbn": True,
                            "perpage": 100,
                            "tor": tor,
                        }
                    )
                    raw_rows = [
                        row for row in result.get("data", []) if isinstance(row, dict)
                    ]
                    found = max(found, as_int(result.get("found"), len(raw_rows)))
                    before = len(raw_by_id)
                    for row in raw_rows:
                        raw_by_id[as_int(row.get("id"))] = row
                    start += len(raw_rows)
                    if not raw_rows or len(raw_by_id) == before or start >= found:
                        break
                scanned = len(raw_by_id)
                presented = [
                    present_search_result(row)
                    for torrent_id, row in raw_by_id.items()
                    if torrent_id
                ]
                rows = filter_search_results(presented, filters)
                found = max(found, scanned)
            except Exception as caught:  # noqa: BLE001 - display API failure in UI
                error = str(caught)
        return {
            "rows": rows,
            "found": found,
            "scanned": scanned,
            "error": error,
        }

    async def render_request_portal(request: Request) -> HTMLResponse:
        current = active_config()
        if not current.request_portal_enabled:
            raise HTTPException(404, "request portal is disabled")
        if not request_portal_authorized(request):
            return templates.TemplateResponse(
                request,
                "request_unlock.html",
                {
                    "request": request,
                    "version": __version__,
                    "portal_title": current.request_portal_title,
                    "login_mode": request_login_mode(current),
                    "portal_username": current.request_portal_username,
                    "error": None,
                },
            )

        params = request.query_params
        goodreads_url = params.get("goodreads_url", "").strip()
        goodreads = None
        goodreads_error = None
        values = {
            "q": params.get("q", ""),
            "title": params.get("title", ""),
            "author": params.get("author", ""),
            "series": params.get("series", ""),
            "narrator": params.get("narrator", ""),
            "filetype": params.get("filetype", ""),
            "category": params.get("category", ""),
            "language": params.get("language", ""),
            "availability": params.get("availability", ""),
            "min_seeders": as_int(params.get("min_seeders")),
            "sort": params.get("sort", "relevance"),
        }
        requested_lookup = bool(
            goodreads_url
            or any(
                value
                for name, value in values.items()
                if name != "sort" and value not in {"", 0}
            )
        )
        if requested_lookup and not await request_rate_allowed(request, "search"):
            return templates.TemplateResponse(
                request,
                "request_portal.html",
                {
                    "request": request,
                    "version": __version__,
                    "portal_title": current.request_portal_title,
                    "portal_root": request_portal_root(request),
                    "portal_username": current.request_portal_username,
                    "login_required": request_login_mode(current) != "public",
                    "filters": manual_search_filters(**values),
                    "searched": True,
                    "rows": [],
                    "found": 0,
                    "scanned": 0,
                    "error": "Too many searches. Wait one minute and try again.",
                    "goodreads_url": goodreads_url,
                    "goodreads": None,
                    "goodreads_error": None,
                    "requester_name": (
                        params.get("requester_name", "").strip()
                        or current.request_portal_username
                    )[:120],
                    "requester_contact": params.get("requester_contact", "")[:200],
                    "note": params.get("note", "")[:1000],
                    "submitted": False,
                },
                status_code=429,
            )
        if goodreads_url:
            try:
                goodreads = await lookup_goodreads_book(goodreads_url)
                values["title"] = values["title"] or goodreads["title"]
                values["author"] = values["author"] or (
                    goodreads["authors"][0] if goodreads["authors"] else ""
                )
                values["series"] = values["series"] or goodreads["series"]
                values["language"] = values["language"] or goodreads["language"]
                repository.log_activity(
                    "requests",
                    f"Read Goodreads request link: {goodreads['title']}",
                    level="success",
                    context={
                        "goodreads_id": goodreads["goodreads_id"],
                        "url": goodreads["url"],
                    },
                )
            except GoodreadsLookupError as caught:
                goodreads_error = str(caught)
                repository.log_activity(
                    "requests",
                    "Goodreads request lookup failed",
                    level="warning",
                    context={"url": goodreads_url, "error": goodreads_error},
                )
        filters = manual_search_filters(**values)
        searched = search_was_requested(filters)
        result = await run_manual_search(filters)
        return templates.TemplateResponse(
            request,
            "request_portal.html",
            {
                "request": request,
                "version": __version__,
                "portal_title": current.request_portal_title,
                "portal_root": request_portal_root(request),
                "portal_username": current.request_portal_username,
                "login_required": request_login_mode(current) != "public",
                "filters": filters,
                "searched": searched,
                "rows": result["rows"],
                "found": result["found"],
                "scanned": result["scanned"],
                "error": result["error"],
                "goodreads_url": goodreads_url,
                "goodreads": goodreads,
                "goodreads_error": goodreads_error,
                "requester_name": (
                    params.get("requester_name", "").strip()
                    or current.request_portal_username
                )[:120],
                "requester_contact": params.get("requester_contact", "")[:200],
                "note": params.get("note", "")[:1000],
                "submitted": params.get("submitted") == "1",
            },
        )

    @app.get("/request", response_class=HTMLResponse)
    async def request_portal_page(request: Request) -> HTMLResponse:
        return await render_request_portal(request)

    @app.post("/request/unlock", response_class=HTMLResponse)
    async def unlock_request_portal(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        access_code: str = Form(""),
    ) -> HTMLResponse:
        current = active_config()
        if not current.request_portal_enabled:
            raise HTTPException(404, "request portal is disabled")
        allowed = await request_rate_allowed(request, "unlock")
        login_mode = request_login_mode(current)
        if login_mode == "credentials":
            username_valid = hmac.compare_digest(
                username, current.request_portal_username
            )
            password_valid = verify_request_password(
                password, current.request_portal_password_hash
            )
            credentials_valid = username_valid and password_valid
        elif login_mode == "access_code":
            credentials_valid = hmac.compare_digest(
                access_code, current.request_portal_access_code
            )
        else:
            credentials_valid = True
        valid = allowed and credentials_valid
        if not valid:
            return templates.TemplateResponse(
                request,
                "request_unlock.html",
                {
                    "request": request,
                    "version": __version__,
                    "portal_title": current.request_portal_title,
                    "login_mode": login_mode,
                    "portal_username": current.request_portal_username,
                    "error": (
                        "Too many attempts. Wait one minute."
                        if not allowed
                        else (
                            "That username or password is not valid."
                            if login_mode == "credentials"
                            else "That access code is not valid."
                        )
                    ),
                },
                status_code=429 if not allowed else 403,
            )
        response = RedirectResponse(request_portal_root(request), status_code=303)
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0]
        response.set_cookie(
            "mysuite_request_access",
            request_access_token(current),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=request.url.scheme == "https" or forwarded_proto == "https",
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/request/logout")
    async def logout_request_portal(request: Request) -> RedirectResponse:
        response = RedirectResponse(request_portal_root(request), status_code=303)
        response.delete_cookie(
            "mysuite_request_access",
            path="/",
            httponly=True,
            samesite="strict",
        )
        return response

    @app.post("/request/submit")
    async def submit_request(
        request: Request,
        mam_id: int = Form(...),
        requester_name: str = Form(""),
        requester_contact: str = Form(""),
        note: str = Form(""),
        goodreads_url: str = Form(""),
        website: str = Form(""),
    ) -> RedirectResponse:
        current = active_config()
        if not current.request_portal_enabled:
            raise HTTPException(404, "request portal is disabled")
        if not request_portal_authorized(request):
            raise HTTPException(403, "request portal login required")
        if not await request_rate_allowed(request, "submit"):
            raise HTTPException(429, "too many requests; wait one minute")
        root = request_portal_root(request)
        if website.strip():
            return RedirectResponse(f"{root}?submitted=1", status_code=303)
        row = await app.state.services.mam.get_torrent_info_by_id(mam_id)
        if not row:
            raise HTTPException(404, f"MaM torrent {mam_id} was not found")
        release = present_search_result(row)
        source: dict[str, object] = {"kind": "manual"}
        if goodreads_url:
            try:
                source = {
                    "kind": "goodreads",
                    "url": goodreads_url,
                    "goodreads_id": goodreads_book_id(goodreads_url),
                }
            except GoodreadsLookupError:
                source = {"kind": "manual"}
        record = await asyncio.to_thread(
            repository.create_request,
            mam_id=mam_id,
            release=release,
            requester_name=requester_name[:120],
            requester_contact=requester_contact[:200],
            note=note[:1000],
            source=source,
        )
        if repository.has_mam_id(mam_id):
            await asyncio.to_thread(
                repository.update_request,
                record["id"],
                "fulfilled",
                decision_note="Already present in the library or download queue",
            )
        repository.log_activity(
            "requests",
            f"New request: {release['title']}",
            level="success",
            context={
                "request_id": record["id"],
                "mam_id": mam_id,
                "requester_name": requester_name[:120],
            },
        )
        snapshot_cache["expires"] = 0.0
        return RedirectResponse(f"{root}?submitted=1", status_code=303)

    @app.get("/requests", response_class=HTMLResponse)
    async def request_inbox(
        request: Request,
        status: str = "pending",
    ) -> HTMLResponse:
        selected_status = (
            status
            if status
            in {
                "pending",
                "approved",
                "rejected",
                "fulfilled",
                "all",
            }
            else "pending"
        )
        rows = await asyncio.to_thread(
            repository.request_rows,
            status=None if selected_status == "all" else selected_status,
        )
        return templates.TemplateResponse(
            request,
            "requests.html",
            await context(
                request,
                title="Request Inbox",
                rows=rows,
                selected_status=selected_status,
                approved=request.query_params.get("approved") == "1",
                rejected=request.query_params.get("rejected") == "1",
                error=request.query_params.get("error", ""),
            ),
        )

    @app.post("/requests/approve")
    async def approve_request(request_id: str = Form(...)) -> RedirectResponse:
        record = await asyncio.to_thread(repository.request_record, request_id)
        if not record:
            raise HTTPException(404, "request was not found")
        if record["status"] != "pending":
            raise HTTPException(409, "request has already been decided")
        mam_id = int(record["mam_id"])
        if repository.has_mam_id(mam_id):
            await asyncio.to_thread(
                repository.update_request,
                request_id,
                "fulfilled",
                decision_note="Already present in the library or download queue",
            )
        else:
            row = await app.state.services.mam.get_torrent_info_by_id(mam_id)
            if not row:
                return RedirectResponse(
                    "/requests?error=The+selected+MaM+release+no+longer+exists",
                    status_code=303,
                )
            selected = await select_row(
                app.state.services.config,
                repository,
                row,
                {"cost": "ratio", "name": f"request:{request_id}"},
            )
            if not selected:
                return RedirectResponse(
                    "/requests?error=Release+does+not+match+configured+formats",
                    status_code=303,
                )
            await asyncio.to_thread(
                repository.update_request,
                request_id,
                "approved",
                decision_note="Approved and added to the download queue",
            )
            asyncio.create_task(app.state.services.trigger("downloader"))
        repository.log_activity(
            "requests",
            f"Approved request: {record['release']['title']}",
            level="success",
            context={"request_id": request_id, "mam_id": mam_id},
        )
        snapshot_cache["expires"] = 0.0
        return RedirectResponse("/requests?approved=1", status_code=303)

    @app.post("/requests/reject")
    async def reject_request(
        request_id: str = Form(...),
        decision_note: str = Form(""),
    ) -> RedirectResponse:
        record = await asyncio.to_thread(repository.request_record, request_id)
        if not record:
            raise HTTPException(404, "request was not found")
        if record["status"] != "pending":
            raise HTTPException(409, "request has already been decided")
        await asyncio.to_thread(
            repository.update_request,
            request_id,
            "rejected",
            decision_note=decision_note[:500] or "Rejected by administrator",
        )
        repository.log_activity(
            "requests",
            f"Rejected request: {record['release']['title']}",
            level="warning",
            context={"request_id": request_id, "mam_id": record["mam_id"]},
        )
        snapshot_cache["expires"] = 0.0
        return RedirectResponse("/requests?rejected=1", status_code=303)

    @app.get("/search", response_class=HTMLResponse)
    async def search(
        request: Request,
        q: str = "",
        title: str = "",
        author: str = "",
        series: str = "",
        narrator: str = "",
        filetype: str = "",
        category: str = "",
        language: str = "",
        availability: str = "",
        min_seeders: int = 0,
        sort: str = "relevance",
    ) -> HTMLResponse:
        filters = manual_search_filters(
            q=q,
            title=title,
            author=author,
            series=series,
            narrator=narrator,
            filetype=filetype,
            category=category,
            language=language,
            availability=availability,
            min_seeders=min_seeders,
            sort=sort,
        )
        searched = search_was_requested(filters)
        result = await run_manual_search(filters)
        return templates.TemplateResponse(
            request,
            "heavymlm/search.html",
            await context(
                request,
                title="Search MaM",
                query=q,
                filters=filters,
                searched=searched,
                rows=result["rows"],
                found=result["found"],
                scanned=result["scanned"],
                error=result["error"],
            ),
        )

    @app.post("/search/select")
    async def select_search_result(mam_id: int = Form(...)) -> RedirectResponse:
        row = await app.state.services.mam.get_torrent_info_by_id(mam_id)
        if not row:
            raise HTTPException(404, f"MaM torrent {mam_id} was not found")
        selected = await select_row(
            app.state.services.config,
            repository,
            row,
            {"cost": "ratio", "name": "manual"},
        )
        if not selected and not repository.has_mam_id(mam_id):
            raise HTTPException(
                409, "torrent did not match a configured preferred format"
            )
        return RedirectResponse("/records/selected_torrents", status_code=303)

    @app.post("/actions/{name}")
    async def action(name: str) -> RedirectResponse:
        if name not in {"autograb", "lists", "downloader", "organizer", "cleaner"}:
            raise HTTPException(404, f"unknown job: {name}")
        asyncio.create_task(app.state.services.trigger(name))
        return RedirectResponse(f"/?triggered={name}", status_code=303)

    @app.post("/selected/remove")
    async def remove_selected(mam_id: int = Form(...)) -> RedirectResponse:
        repository.delete_selected(mam_id)
        return RedirectResponse("/records/selected_torrents", status_code=303)

    @app.get("/health")
    async def health() -> dict:
        snapshot = await ui_snapshot()
        return {"status": "ok", "counts": snapshot["counts"]}

    @app.get("/api/jobs")
    async def jobs() -> dict:
        services = getattr(app.state, "services", None)
        return {
            "jobs": (
                {name: asdict(status) for name, status in services.jobs.items()}
                if services
                else {}
            )
        }

    return app
