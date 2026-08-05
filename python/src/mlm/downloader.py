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
    failures: list[dict[str, object]] = field(default_factory=list)


ALREADY_FREE_WEDGE_REASONS = {
    "already_vip",
    "already_global_freeleech",
    "already_personal_freeleech",
}


async def _confirm_wedge(
    mam: MamClient,
    torrent_id: int,
    wedges_before: int,
) -> dict[str, object]:
    confirmation: dict[str, object] = {
        "torrent_id": torrent_id,
        "wedges_before": wedges_before,
        "verified": False,
    }
    try:
        user = await mam.user_info()
        wedges_after = max(0, int(user.get("wedges", 0)))
        confirmation["wedges_after"] = wedges_after
        if wedges_after < wedges_before:
            confirmation.update(verified=True, verified_by="wedge_balance")
            return confirmation
    except Exception as error:  # noqa: BLE001 - retain confirmation diagnostics
        confirmation["balance_check_error"] = f"{type(error).__name__}: {error}"

    try:
        current = await mam.get_torrent_info_by_id(torrent_id)
        personal_freeleech = bool(
            current and as_bool(current.get("personal_freeleech"))
        )
        confirmation["personal_freeleech"] = personal_freeleech
        if personal_freeleech:
            confirmation.update(verified=True, verified_by="personal_freeleech")
            return confirmation
    except Exception as error:  # noqa: BLE001 - retain confirmation diagnostics
        confirmation["torrent_check_error"] = f"{type(error).__name__}: {error}"
    return confirmation


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
    failures: list[dict[str, object]] = []
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
        diagnostic_context: dict[str, object] = {
            "mam_id": selected.get("mam_id"),
            "title": selected.get("meta", {}).get("title"),
        }
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
            wedge_context: dict[str, object] = {
                "stage": "wedge_decision",
                "wedge_attempted": False,
                "mam_id": torrent_id,
                "cost": cost,
                "prefer_wedges": config.prefer_wedges,
                "currently_free": currently_free,
                "already_present": bool(existing),
                "wants_wedge": wants_wedge,
                "wedges_before": wedges_remaining,
                "wedge_buffer": wedge_buffer,
                "raw_freeleech_flags": {
                    field: current.get(field) if current else None
                    for field in ("free", "personal_freeleech", "fl_vip", "vip")
                },
            }
            diagnostic_context.update(wedge_context)
            repository.log_activity(
                "downloader",
                f"Evaluated freeleech state for MaM #{torrent_id}",
                level="debug",
                context=wedge_context,
            )
            if wants_wedge and wedges_remaining > wedge_buffer:
                wedge_context.update(stage="wedge_request", wedge_attempted=True)
                diagnostic_context.update(wedge_context)
                repository.log_activity(
                    "downloader",
                    f"Applying freeleech wedge to MaM #{torrent_id}",
                    context=wedge_context,
                )
                try:
                    receipt = await mam.wedge_torrent(torrent_id)
                    confirmation = await _confirm_wedge(
                        mam, torrent_id, wedges_remaining
                    )
                    wedge_context.update(
                        stage="wedge_confirmation",
                        tracker_receipt=receipt,
                        **confirmation,
                    )
                    diagnostic_context.update(wedge_context)
                    if not confirmation["verified"]:
                        raise MamWedgeError(
                            "MaM reported wedge success, but HeavyMLM could not "
                            "confirm a reduced balance or personal freeleech status",
                            reason="unconfirmed",
                            context=wedge_context,
                        )
                    wedged = True
                    confirmed_balance = confirmation.get("wedges_after")
                    wedges_remaining = (
                        int(confirmed_balance)
                        if confirmed_balance is not None
                        and int(confirmed_balance) < wedges_remaining
                        else wedges_remaining - 1
                    )
                    user["wedges"] = wedges_remaining
                    repository.log_activity(
                        "downloader",
                        f"Applied freeleech wedge to MaM #{torrent_id}",
                        level="success",
                        context={
                            **wedge_context,
                            "wedges_remaining": wedges_remaining,
                        },
                    )
                except MamWedgeError as error:
                    failure_context = {
                        **wedge_context,
                        **error.context,
                        "wedge_reason": error.reason,
                        "error": f"{type(error).__name__}: {error}",
                    }
                    diagnostic_context.update(failure_context)
                    if error.reason in ALREADY_FREE_WEDGE_REASONS:
                        currently_free = True
                        repository.log_activity(
                            "downloader",
                            (
                                f"No wedge consumed for MaM #{torrent_id}: "
                                f"tracker reports it is already free"
                            ),
                            level="warning",
                            context=failure_context,
                        )
                    elif config.prefer_wedges or cost == "UseWedge":
                        repository.log_activity(
                            "downloader",
                            f"Freeleech wedge failed for MaM #{torrent_id}",
                            level="error",
                            context=failure_context,
                        )
                        raise MamWedgeError(
                            str(error),
                            reason=error.reason,
                            context=failure_context,
                        ) from error
                    else:
                        repository.log_activity(
                            "downloader",
                            (
                                f"Optional wedge failed for MaM #{torrent_id}; "
                                "continuing with its ratio policy"
                            ),
                            level="warning",
                            context=failure_context,
                        )
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
            error_context = dict(diagnostic_context)
            if isinstance(error, MamWedgeError):
                error_context.update(error.context)
                error_context["wedge_reason"] = error.reason
            error_context["error"] = f"{type(error).__name__}: {error}"
            repository.record_grab_error(
                selected,
                error,
                context=error_context,
            )
            failures.append(error_context)
            failed += 1
            repository.log_activity(
                "downloader",
                f"Failed MaM #{selected.get('mam_id')}",
                level="error",
                context=error_context,
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
        failures=failures,
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
            "failures": result.failures,
        },
    )
    return result
