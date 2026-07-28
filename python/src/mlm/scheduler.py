from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from .audiobookshelf import AudiobookshelfClient, match_torrents_to_audiobookshelf
from .autograbber import run_autograbber
from .cleaner import clean_superseded
from .config import Config
from .downloader import grab_selected_torrents
from .library import organize_completed
from .lists import run_goodreads_import, run_notion_import
from .mam import MamClient
from .qbittorrent import QbitClient
from .repository import Repository
from .snatchlist import run_snatchlist_search


@dataclass
class JobStatus:
    last_run: str | None = None
    last_error: str | None = None
    running: bool = False


@dataclass
class ServiceState:
    config: Config
    repository: Repository
    mam: MamClient
    jobs: dict[str, JobStatus] = field(default_factory=dict)
    tasks: list[asyncio.Task] = field(default_factory=list)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def run_job(self, name: str, job: Callable[[], Awaitable[object]]) -> None:
        status = self.jobs.setdefault(name, JobStatus())
        if status.running:
            return
        status.running = True
        status.last_run = datetime.now(UTC).isoformat()
        status.last_error = None
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - jobs must not stop the scheduler
            status.last_error = f"{type(error).__name__}: {error}"
        finally:
            status.running = False

    async def _periodic(
        self,
        name: str,
        interval_minutes: int,
        job: Callable[[], Awaitable[object]],
    ) -> None:
        while not self.stop_event.is_set():
            await self.run_job(name, job)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=max(1, interval_minutes * 60)
                )

    async def _qbit(self, index: int) -> tuple[QbitClient, object]:
        qbit_config = self.config.qbittorrent[index]
        qbit = QbitClient(qbit_config.url)
        await qbit.login(qbit_config.username, qbit_config.password)
        return qbit, qbit_config

    async def downloader(self) -> None:
        if not self.config.qbittorrent:
            return
        qbit, _ = await self._qbit(0)
        try:
            await grab_selected_torrents(self.config, self.repository, self.mam, qbit)
        finally:
            await qbit.close()

    async def organizer(self, index: int) -> None:
        qbit, qbit_config = await self._qbit(index)
        try:
            await organize_completed(
                self.config, self.repository, qbit_config, qbit, self.mam
            )
        finally:
            await qbit.close()

    async def cleaner(self) -> None:
        clients: list[tuple[dict, QbitClient]] = []
        try:
            for index in range(len(self.config.qbittorrent)):
                qbit, qbit_config = await self._qbit(index)
                clients.append((asdict(qbit_config), qbit))
            await clean_superseded(self.config, self.repository, clients)
        finally:
            for _, qbit in clients:
                await qbit.close()

    async def audiobookshelf(self) -> None:
        definition = self.config.audiobookshelf
        if not definition:
            return
        client = AudiobookshelfClient(definition["url"], definition["token"])
        try:
            await match_torrents_to_audiobookshelf(self.repository, client)
        finally:
            await client.close()

    def start(self) -> None:
        for index, rule in enumerate(self.config.autograbs):
            interval = int(rule.get("search_interval") or self.config.search_interval)
            name = f"autograb:{index}"
            self.tasks.append(
                asyncio.create_task(
                    self._periodic(
                        name,
                        interval,
                        lambda rule=rule, index=index: run_autograbber(
                            self.config,
                            self.repository,
                            self.mam,
                            rule,
                            index=index,
                        ),
                    ),
                    name=name,
                )
            )
        for index, rule in enumerate(self.config.goodreads_lists):
            interval = int(rule.get("search_interval") or self.config.import_interval)
            name = f"goodreads:{index}"
            self.tasks.append(
                asyncio.create_task(
                    self._periodic(
                        name,
                        interval,
                        lambda rule=rule: run_goodreads_import(
                            self.config, self.repository, self.mam, rule
                        ),
                    ),
                    name=name,
                )
            )
        for index, rule in enumerate(self.config.notion_lists):
            interval = int(rule.get("search_interval") or self.config.import_interval)
            name = f"notion:{index}"
            self.tasks.append(
                asyncio.create_task(
                    self._periodic(
                        name,
                        interval,
                        lambda rule=rule: run_notion_import(
                            self.config, self.repository, self.mam, rule
                        ),
                    ),
                    name=name,
                )
            )
        for index, rule in enumerate(self.config.snatchlist):
            interval = int(rule.get("search_interval") or self.config.search_interval)
            name = f"snatchlist:{index}"
            self.tasks.append(
                asyncio.create_task(
                    self._periodic(
                        name,
                        interval,
                        lambda rule=rule: run_snatchlist_search(
                            self.config, self.repository, self.mam, rule
                        ),
                    ),
                    name=name,
                )
            )
        self.tasks.append(
            asyncio.create_task(
                self._periodic("downloader", 1, self.downloader), name="downloader"
            )
        )
        if self.config.audiobookshelf:
            self.tasks.append(
                asyncio.create_task(
                    self._periodic(
                        "audiobookshelf",
                        int(self.config.audiobookshelf.get("interval", 10)),
                        self.audiobookshelf,
                    ),
                    name="audiobookshelf",
                )
            )
        for index in range(len(self.config.qbittorrent)):
            self.tasks.append(
                asyncio.create_task(
                    self._periodic(
                        f"organizer:{index}",
                        self.config.link_interval,
                        lambda index=index: self.organizer(index),
                    ),
                    name=f"organizer:{index}",
                )
            )
        self.tasks.append(
            asyncio.create_task(
                self._periodic("cleaner", self.config.link_interval, self.cleaner),
                name="cleaner",
            )
        )

    async def close(self) -> None:
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.mam.close()

    async def trigger(self, name: str) -> None:
        if name == "downloader":
            await self.run_job(name, self.downloader)
        elif name == "cleaner":
            await self.run_job(name, self.cleaner)
        elif name == "organizer":
            for index in range(len(self.config.qbittorrent)):
                await self.run_job(
                    f"organizer:{index}", lambda index=index: self.organizer(index)
                )
        elif name == "autograb":
            for index, rule in enumerate(self.config.autograbs):
                await self.run_job(
                    f"autograb:{index}",
                    lambda rule=rule, index=index: run_autograbber(
                        self.config,
                        self.repository,
                        self.mam,
                        rule,
                        index=index,
                    ),
                )
        else:
            raise ValueError(f"unknown job: {name}")
