from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .config import Config
from .mam import MamClient, MamRateLimitError
from .qbittorrent import QbitClient
from .repository import Repository
from .torrent import info_hash


@dataclass(frozen=True)
class DownloadRun:
    downloaded: int = 0
    failed: int = 0
    skipped: int = 0


async def _torrent_file_with_backoff(
    mam: MamClient, download_hash: str, torrent_id: int
) -> bytes:
    delay = 30
    while True:
        try:
            return await mam.get_torrent_file(download_hash, torrent_id)
        except MamRateLimitError:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


async def grab_selected_torrents(
    config: Config,
    repository: Repository,
    mam: MamClient,
    qbit: QbitClient,
) -> DownloadRun:
    downloaded = failed = skipped = 0
    for selected in repository.pending_selected():
        try:
            torrent_id = int(selected["mam_id"])
            torrent_file = await _torrent_file_with_backoff(
                mam, selected["dl_link"], torrent_id
            )
            torrent_hash = info_hash(torrent_file)
            existing = await qbit.torrents(hashes=[torrent_hash])
            if not existing:
                await qbit.add_torrent(
                    torrent_file,
                    category=selected.get("category"),
                    tags=selected.get("tags", []),
                    paused=config.add_torrents_stopped,
                )
            repository.record_started(selected, torrent_hash)
            downloaded += 1
        except Exception as error:
            repository.record_grab_error(selected, error)
            failed += 1
        await asyncio.sleep(1)
    return DownloadRun(downloaded=downloaded, failed=failed, skipped=skipped)
