from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Config, load_config
from .database import ensure_database
from .mam import MamClient
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
        mam = MamClient(config.mam_id)
        state = ServiceState(config, repository, mam)
        app.state.services = state
        try:
            await mam.check_mam_id()
            state.start()
            yield
        finally:
            await state.close()

    app = FastAPI(title="Myanonamouse Library Manager", lifespan=lifespan)

    def context(request: Request, **values: object) -> dict:
        return {
            "request": request,
            "counts": repository.counts(),
            "jobs": app.state.services.jobs if hasattr(app.state, "services") else {},
            **values,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "index.html", context(request, title="Dashboard")
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
            context(request, title=table.replace("_", " ").title(), table=table, rows=rows),
        )

    @app.get("/config", response_class=HTMLResponse)
    async def show_config(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "config.html",
            context(request, title="Configuration", config=_redacted_config(config)),
        )

    @app.post("/actions/{name}")
    async def action(name: str) -> RedirectResponse:
        try:
            asyncio.create_task(app.state.services.trigger(name))
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        return RedirectResponse("/", status_code=303)

    @app.post("/selected/remove")
    async def remove_selected(mam_id: int = Form(...)) -> RedirectResponse:
        repository.delete_selected(mam_id)
        return RedirectResponse("/records/selected_torrents", status_code=303)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "counts": repository.counts()}

    return app
