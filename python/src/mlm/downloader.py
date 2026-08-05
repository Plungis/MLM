from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field

from .config import Config
from .mam import MamClient, MamRateLimitError, MamWedgeError
from .qbittorrent import QbitClient
from .repository import Repository
from .search import as_bool
from .torrent import info_hash


@dataclass(frozen=True)
class DownloadRun:
    downloaded: int = 0
    failed: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    available_slots: int = 0
    ratio_buffer_bytes: int = 0
    slots_used: int = 0
    slots_total: int = 0
    slot_cap: int = 0
    wedges_remaining: int = 0
    wedge_buffer: int = 0


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
    skip_reasons = {"unsat_slots": 0, "ratio_buffer": 0}
    pending = repository.pending_selected()
    repository.log_activity(
        "downloader",
        "Evaluating selected torrents",
        context={"pending": len(pending)},
    )
    user = await mam.user_info()
    unsat = user.get("unsat", {})
    slots_total = max(0, int(unsat.get("limit", 0)))
    slots_used = max(0, int(unsat.get("count", 0)))
    available_slots = max(0, slots_total - slots_used)
    wedges_remaining = max(0, int(user.get("wedges", 0)))
    downloading_size = repository.selected_pipeline_status()["downloading_bytes"]
    remaining_buffer = (
        float(user.get("uploaded_bytes", 0))
        - float(user.get("downloaded_bytes", 0))
        - downloading_size
    ) / config.min_ratio
    starting_ratio_buffer = max(0, int(remaining_buffer))
    for selected in pending:
        try:
            torrent_id = int(selected["mam_id"])
            slot_buffer = int(
                selected.get("unsat_buffer")
                if selected.get("unsat_buffer") is not None
                else config.unsat_buffer
            )
            slot_cap = max(0, slots_total - slot_buffer)
            if config.max_unsat_slots is not None:
                slot_cap = min(slot_cap, config.max_unsat_slots)
            size = int(selected.get("meta", {}).get("size", 0))
            if slots_used + downloaded >= slot_cap:
                skipped += 1
                skip_reasons["unsat_slots"] += 1
                repository.log_activity(
                    "downloader",
                    f"Deferred MaM #{torrent_id}: no unsatisfied slot available",
                    level="warning",
                    context={
                        "mam_id": torrent_id,
                        "slots_used": slots_used + downloaded,
                        "slots_total": slots_total,
                        "slot_cap": slot_cap,
                        "available_slots": available_slots,
                        "slot_buffer": slot_buffer,
                    },
                )
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
            wedge_buffer = int(
                selected.get("wedge_buffer")
                if selected.get("wedge_buffer") is not None
                else config.wedge_buffer
            )
            current = None
            currently_free = False
            if not existing and (config.prefer_wedges or cost != "Ratio"):
                current = await mam.get_torrent_info(torrent_hash)
                currently_free = bool(
                    current
                    and any(
                        as_bool(current.get(field))
                        for field in ("free", "personal_freeleech", "fl_vip", "vip")
                    )
                )
            wants_wedge = (
                not existing
                and not currently_free
                and (config.prefer_wedges or cost in {"UseWedge", "TryWedge"})
            )
            repository.log_activity(
                "downloader",
                f"Evaluated freeleech state for MaM #{torrent_id}",
                level="debug",
                context={
                    "mam_id": torrent_id,
                    "cost": cost,
                    "prefer_wedges": config.prefer_wedges,
                    "currently_free": currently_free,
                    "wants_wedge": wants_wedge,
                    "wedges_remaining": wedges_remaining,
                    "wedge_buffer": wedge_buffer,
                    "raw_freeleech_flags": {
                        field: current.get(field) if current else None
                        for field in ("free", "personal_freeleech", "fl_vip", "vip")
                    },
                },
            )
            if wants_wedge and wedges_remaining > wedge_buffer:
                try:
                    await mam.wedge_torrent(torrent_id)
                    wedged = True
                    wedges_remaining -= 1
                    user["wedges"] = wedges_remaining
                    repository.log_activity(
                        "downloader",
                        f"Applied freeleech wedge to MaM #{torrent_id}",
                        level="success",
                        context={
                            "mam_id": torrent_id,
                            "wedges_remaining": wedges_remaining,
                            "wedge_buffer": wedge_buffer,
                        },
                    )
                except MamWedgeError:
                    if cost == "UseWedge":
                        raise
            elif wants_wedge and cost == "UseWedge":
                raise MamWedgeError(
                    f"wedge reserve reached ({wedges_remaining} available, "
                    f"{wedge_buffer} reserved)"
                )
            if (
                not existing
                and not wedged
                and cost not in {"Ratio", "TryWedge"}
                and not currently_free
            ):
                raise RuntimeError("torrent is no longer free")
            uses_ratio = (
                not existing
                and not wedged
                and not currently_free
                and cost in {"Ratio", "TryWedge"}
            )
            if uses_ratio and remaining_buffer - size <= 0:
                skipped += 1
                skip_reasons["ratio_buffer"] += 1
                repository.log_activity(
                    "downloader",
                    f"Deferred MaM #{torrent_id}: ratio reserve",
                    level="warning",
                    context={
                        "mam_id": torrent_id,
                        "torrent_bytes": size,
                        "ratio_buffer_bytes": max(0, int(remaining_buffer)),
                    },
                )
                continue
            if not existing:
                await qbit.add_torrent(
                    torrent_file,
                    category=selected.get("category"),
                    tags=selected.get("tags", []),
                    paused=config.add_torrents_stopped,
                )
            repository.record_started(selected, torrent_hash, wedged=wedged)
            downloaded += 1
            if uses_ratio:
                remaining_buffer -= size
            repository.log_activity(
                "downloader",
                f"Added MaM #{torrent_id} to qBittorrent",
                level="success",
                context={
                    "mam_id": torrent_id,
                    "torrent_hash": torrent_hash,
                    "wedged": wedged,
                    "already_present": bool(existing),
                },
            )
        except Exception as error:  # noqa: BLE001 - isolate failures per torrent
            repository.record_grab_error(selected, error)
            failed += 1
            repository.log_activity(
                "downloader",
                f"Failed MaM #{selected.get('mam_id')}",
                level="error",
                context={
                    "mam_id": selected.get("mam_id"),
                    "error": f"{type(error).__name__}: {error}",
                },
            )
        await asyncio.sleep(1)
    result = DownloadRun(
        downloaded=downloaded,
        failed=failed,
        skipped=skipped,
        skip_reasons={key: value for key, value in skip_reasons.items() if value},
        available_slots=available_slots,
        ratio_buffer_bytes=starting_ratio_buffer,
        slots_used=min(slots_total, slots_used + downloaded),
        slots_total=slots_total,
        slot_cap=min(
            max(0, slots_total - config.unsat_buffer),
            config.max_unsat_slots
            if config.max_unsat_slots is not None
            else slots_total,
        ),
        wedges_remaining=wedges_remaining,
        wedge_buffer=config.wedge_buffer,
    )
    repository.log_activity(
        "downloader",
        "Download evaluation complete",
        level="success" if not failed else "warning",
        context={
            "downloaded": result.downloaded,
            "failed": result.failed,
            "skipped": result.skipped,
            "skip_reasons": result.skip_reasons,
            "slots_used": result.slots_used,
            "slot_cap": result.slot_cap,
            "wedges_remaining": result.wedges_remaining,
            "wedge_buffer": result.wedge_buffer,
        },
    )
    return result
