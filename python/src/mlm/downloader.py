from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from .config import Config
from .mam import MamClient, MamRateLimitError, MamWedgeError
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
    other_qbits: Iterable[QbitClient] = (),
) -> DownloadRun:
    downloaded = failed = skipped = 0
    user = await mam.user_info()
    unsat = user.get("unsat", {})
    available_slots = max(0, int(unsat.get("limit", 0)) - int(unsat.get("count", 0)))
    downloading_size = sum(
        int(row.get("meta", {}).get("size", 0))
        for row in repository.pending_selected()
        if row.get("started_at") is not None
    )
    remaining_buffer = (
        float(user.get("uploaded_bytes", 0))
        - float(user.get("downloaded_bytes", 0))
        - downloading_size
    ) / config.min_ratio
    for selected in repository.pending_selected():
        try:
            torrent_id = int(selected["mam_id"])
            slot_buffer = int(
                selected.get("unsat_buffer")
                if selected.get("unsat_buffer") is not None
                else config.unsat_buffer
            )
            size = int(selected.get("meta", {}).get("size", 0))
            if (
                available_slots - downloaded <= slot_buffer
                or remaining_buffer - size <= 0
            ):
                skipped += 1
                continue
            torrent_file = await _torrent_file_with_backoff(
                mam, selected["dl_link"], torrent_id
            )
            torrent_hash = info_hash(torrent_file)
            existing = await qbit.torrents(hashes=[torrent_hash])
            if not existing:
                for other_qbit in other_qbits:
                    existing = await other_qbit.torrents(hashes=[torrent_hash])
                    if existing:
                        break
            wedged = False
            cost = selected.get("cost")
            if not existing and cost in {"UseWedge", "TryWedge"}:
                wedge_buffer = int(
                    selected.get("wedge_buffer")
                    if selected.get("wedge_buffer") is not None
                    else config.wedge_buffer
                )
                if int(user.get("wedges", 0)) <= wedge_buffer:
                    raise MamWedgeError(
                        f"fewer wedges than configured wedge buffer ({wedge_buffer})"
                    )
                try:
                    await mam.wedge_torrent(torrent_id)
                    wedged = True
                    user["wedges"] = max(0, int(user.get("wedges", 0)) - 1)
                except MamWedgeError:
                    if cost == "UseWedge":
                        raise
            elif not existing and cost != "Ratio":
                current = await mam.get_torrent_info(torrent_hash)
                if not current or not any(
                    current.get(field)
                    for field in ("free", "personal_freeleech", "fl_vip", "vip")
                ):
                    raise RuntimeError("torrent is no longer free")
            if not existing:
                await qbit.add_torrent(
                    torrent_file,
                    category=selected.get("category"),
                    tags=selected.get("tags", []),
                    paused=config.add_torrents_stopped,
                )
            repository.record_started(selected, torrent_hash, wedged=wedged)
            downloaded += 1
            remaining_buffer -= size
        except Exception as error:  # noqa: BLE001 - isolate failures per torrent
            repository.record_grab_error(selected, error)
            failed += 1
        await asyncio.sleep(1)
    return DownloadRun(downloaded=downloaded, failed=failed, skipped=skipped)
