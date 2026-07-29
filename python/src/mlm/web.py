from __future__ import annotations

import asyncio
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
from .config import Config, load_config
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

    def context(request: Request, **values: object) -> dict:
        counts = repository.counts()
        return {
            "request": request,
            "counts": counts,
            "pipeline": repository.selected_pipeline_status(),
            "record_total": sum(counts.values()),
            "jobs": app.state.services.jobs if hasattr(app.state, "services") else {},
            "version": __version__,
            **values,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            context(
                request,
                title="Dashboard",
                triggered=request.query_params.get("triggered"),
            ),
        )

    @app.get("/records/{table}", response_class=HTMLResponse)
    async def records(request: Request, table: str) -> HTMLResponse:
        try:
            rows = repository.table_rows(table)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        return templates.TemplateResponse(
            request,
            "records.html",
            context(
                request, title=table.replace("_", " ").title(), table=table, rows=rows
            ),
        )

    @app.get("/config", response_class=HTMLResponse)
    async def show_config(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "config.html",
            context(request, title="Configuration", config=_redacted_config(config)),
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
            context(request, title="Search MaM", query=q, rows=rows, error=error),
        )

    @app.post("/search/select")
    async def select_search_result(mam_id: int = Form(...)) -> RedirectResponse:
        row = await app.state.services.mam.get_torrent_info_by_id(mam_id)
        if not row:
            raise HTTPException(404, f"MaM torrent {mam_id} was not found")
        selected = await select_row(
            config,
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
        if name not in {"autograb", "downloader", "organizer", "cleaner"}:
            raise HTTPException(404, f"unknown job: {name}")
        asyncio.create_task(app.state.services.trigger(name))
        return RedirectResponse(f"/?triggered={name}", status_code=303)

    @app.post("/selected/remove")
    async def remove_selected(mam_id: int = Form(...)) -> RedirectResponse:
        repository.delete_selected(mam_id)
        return RedirectResponse("/records/selected_torrents", status_code=303)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "counts": repository.counts()}

    return app
