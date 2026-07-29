from __future__ import annotations

import asyncio
import ipaddress
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .autograbber import select_row
from .config import Config, ConfigError, load_config, save_root_config_values
from .database import ensure_database
from .mam import authenticated_mam_client
from .repository import Repository
from .scheduler import ServiceState

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


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
    return value


def create_app(config_path: Path, database_path: Path) -> FastAPI:
    ensure_database(database_path)
    config = load_config(config_path)
    repository = Repository(database_path)
    snapshot_cache: dict[str, object] = {"expires": 0.0, "value": None}
    snapshot_lock = asyncio.Lock()

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

    app = FastAPI(title="Myanonamouse Library Manager", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    def active_config() -> Config:
        if hasattr(app.state, "services"):
            return app.state.services.config
        return config

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
        return {
            "request": request,
            "counts": counts,
            "pipeline": snapshot["pipeline"],
            "list_tracking": snapshot["list_tracking"],
            "record_total": sum(counts.values()),
            "jobs": app.state.services.jobs if hasattr(app.state, "services") else {},
            "mam_stats": (
                app.state.services.mam_stats if hasattr(app.state, "services") else {}
            ),
            "version": __version__,
            **values,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            await context(
                request,
                title="Dashboard",
                triggered=request.query_params.get("triggered"),
            ),
        )

    @app.get("/records/{table}", response_class=HTMLResponse)
    async def records(request: Request, table: str, page: int = 1) -> HTMLResponse:
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
            ),
        )

    @app.get("/config", response_class=HTMLResponse)
    async def show_config(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "config.html",
            await context(
                request,
                title="Configuration",
                config=_redacted_config(active_config()),
                saved=request.query_params.get("saved") == "1",
                error=None,
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
        grab_both_formats: str | None = Form(None),
        add_torrents_stopped: str | None = Form(None),
        search_interval: str = Form(...),
        import_interval: str = Form(...),
        link_interval: str = Form(...),
    ) -> HTMLResponse:
        client_host = request.client.host if request.client else ""
        try:
            if (
                client_host != "testclient"
                and not ipaddress.ip_address(client_host).is_loopback
            ):
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
                "grab_both_formats": grab_both_formats is not None,
                "add_torrents_stopped": add_torrents_stopped is not None,
                "search_interval": int(search_interval),
                "import_interval": int(import_interval),
                "link_interval": int(link_interval),
            }
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
                ),
                status_code=400,
            )
        return RedirectResponse("/config?saved=1", status_code=303)

    @app.get("/diagnostics", response_class=HTMLResponse)
    async def diagnostics(request: Request, component: str = "") -> HTMLResponse:
        activity = await asyncio.to_thread(
            repository.recent_activity, limit=300, component=component or None
        )
        return templates.TemplateResponse(
            request,
            "diagnostics.html",
            await context(
                request,
                title="Diagnostics",
                activity=activity,
                component=component,
                live=request.query_params.get("live", "1") != "0",
            ),
        )

    @app.get("/search", response_class=HTMLResponse)
    async def search(request: Request, q: str = "") -> HTMLResponse:
        rows = []
        error = None
        if q:
            try:
                result = await app.state.services.mam.search(
                    {
                        "dlLink": True,
                        "description": True,
                        "isbn": True,
                        "perpage": 100,
                        "tor": {"text": q},
                    }
                )
                rows = result.get("data", [])
            except Exception as caught:  # noqa: BLE001 - display API failure in UI
                error = str(caught)
        return templates.TemplateResponse(
            request,
            "search.html",
            await context(request, title="Search MaM", query=q, rows=rows, error=error),
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
