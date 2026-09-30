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


def _parse_bytes_value(val: object) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0


def _parse_int_value(val: object, default: int = 0) -> int:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return int(val)
    val_str = str(val).strip()
    try:
        return int(val_str)
    except ValueError:
        return default


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
    mam: MamClient,
    torrent_id: int,
    *,
    use_wedge: bool = False,
) -> bytes:
    delay = 30
    while True:
        try:
            return await mam.get_torrent_file(torrent_id, use_wedge=use_wedge)
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
    raw_unsat = user.get("unsat")
    if not isinstance(raw_unsat, dict):
        raw_unsat = user.get("unsats") or user.get("unsatisfied")
    if isinstance(raw_unsat, dict):
        limit_val = raw_unsat.get("limit")
        slots_total = _parse_int_value(limit_val, 0)
        slots_used = _parse_int_value(raw_unsat.get("count"), 0)
    elif isinstance(raw_unsat, (int, str)) and str(raw_unsat).isdigit():
        slots_total = 0
        slots_used = int(raw_unsat)
    else:
        slots_total = 0
        slots_used = 0

    if slots_total == 0:
        if config.max_unsat_slots is not None:
            slots_total = config.max_unsat_slots
        else:
            slots_total = max(150, slots_used + 10)

    available_slots = max(0, slots_total - slots_used)
    wedges_remaining = _parse_int_value(
        user.get("wedges")
        or user.get("fl_wedges")
        or user.get("flwedge")
        or user.get("freeleech_wedges")
    )
    downloading_size = repository.selected_pipeline_status()["downloading_bytes"]
    raw_uploaded = (
        user.get("uploaded_bytes")
        if user.get("uploaded_bytes") is not None
        else user.get("uploaded")
    )
    raw_downloaded = (
        user.get("downloaded_bytes")
        if user.get("downloaded_bytes") is not None
        else user.get("downloaded")
    )
    uploaded_bytes = _parse_bytes_value(raw_uploaded)
    downloaded_bytes = _parse_bytes_value(raw_downloaded)
    min_ratio = max(0.1, float(config.min_ratio))
    remaining_buffer = (
        uploaded_bytes - downloaded_bytes - downloading_size
    ) / min_ratio
    starting_ratio_buffer = max(0, int(remaining_buffer))
    for selected in pending:
        is_manual = bool(
            selected.get("force_download")
            or selected.get("grabber") == "manual"
            or str(selected.get("grabber", "")).startswith(("series:", "request:"))
            or selected.get("source") in {"manual", "request"}
        )
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
            if not is_manual and slots_used + downloaded >= slot_cap:
                skipped += 1
                skip_reasons["unsat_slots"] += 1
                repository.record_grab_deferral(
                    selected,
                    "No unsatisfied slots available",
                    f"{slots_used + downloaded}/{slot_cap} used",
                )
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
            torrent_file = await _torrent_file_with_backoff(mam, torrent_id)
            torrent_hash = info_hash(torrent_file)
            existing = await qbit.torrents(hashes=[torrent_hash])
            if not existing:
                for other_qbit in other_qbits:
                    existing = await other_qbit.torrents(hashes=[torrent_hash])
                    if existing:
                        break
            wedged = False
            wedge_failed_fallback = False
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
                "download_on_wedge_failure": config.download_on_wedge_failure,
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
                    wedged_torrent_file = await _torrent_file_with_backoff(
                        mam,
                        torrent_id,
                        use_wedge=True,
                    )
                    wedged_torrent_hash = info_hash(wedged_torrent_file)
                    if wedged_torrent_hash != torrent_hash:
                        raise MamWedgeError(
                            "MaM returned a different torrent after applying the wedge",
                            reason="torrent_mismatch",
                            context={
                                **wedge_context,
                                "expected_hash": torrent_hash,
                                "received_hash": wedged_torrent_hash,
                            },
                        )
                    confirmation = await _confirm_wedge(
                        mam, torrent_id, wedges_remaining
                    )
                    wedge_context.update(
                        stage="wedge_confirmation",
                        endpoint=f"/tor/download.php?tid={torrent_id}&fl",
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
                    torrent_file = wedged_torrent_file
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
                    confirmation = await _confirm_wedge(
                        mam, torrent_id, wedges_remaining
                    )
                    failure_context = {
                        **wedge_context,
                        **error.context,
                        **confirmation,
                        "wedge_reason": error.reason,
                        "error": f"{type(error).__name__}: {error}",
                    }
                    diagnostic_context.update(failure_context)
                    if confirmation["verified"]:
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
                            (
                                f"Confirmed freeleech wedge for MaM #{torrent_id} "
                                "despite an unexpected download response"
                            ),
                            level="warning",
                            context={
                                **failure_context,
                                "wedges_remaining": wedges_remaining,
                            },
                        )
                    elif not (config.download_on_wedge_failure or cost == "TryWedge"):
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
                        wedge_failed_fallback = True
                        failure_context["fallback_used"] = True
                        diagnostic_context.update(failure_context)
                        repository.log_activity(
                            "downloader",
                            (
                                f"Wedge failed for MaM #{torrent_id}; downloading "
                                "normally under ratio safeguards"
                            ),
                            level="warning",
                            context=failure_context,
                        )
            elif wants_wedge and cost == "UseWedge":
                if is_manual or config.download_on_wedge_failure:
                    wedge_failed_fallback = True
                    repository.log_activity(
                        "downloader",
                        (
                            f"Wedge reserve reached for MaM #{torrent_id}; "
                            "downloading normally under ratio safeguards"
                        ),
                        level="warning",
                    )
                else:
                    raise MamWedgeError(
                        f"wedge reserve reached ({wedges_remaining} available, "
                        f"{wedge_buffer} reserved)"
                    )
            if (
                not existing
                and not wedged
                and not wedge_failed_fallback
                and cost not in {"Ratio", "TryWedge"}
                and not currently_free
            ):
                if is_manual:
                    wedge_failed_fallback = True
                else:
                    raise RuntimeError("torrent is no longer free")
            uses_ratio = (
                not existing
                and not wedged
                and not currently_free
                and (cost in {"Ratio", "TryWedge"} or wedge_failed_fallback)
            )
            if uses_ratio and remaining_buffer - size <= 0:
                if is_manual:
                    buffer_bytes = max(0, int(remaining_buffer))
                    repository.log_activity(
                        "downloader",
                        (
                            f"Manual addition MaM #{torrent_id} exceeds ratio "
                            f"reserve (needs {size} B, buffer has {buffer_bytes} B); "
                            "downloading anyway per user request"
                        ),
                        level="info",
                        context={
                            "mam_id": torrent_id,
                            "torrent_bytes": size,
                            "ratio_buffer_bytes": buffer_bytes,
                        },
                    )
                else:
                    skipped += 1
                    skip_reasons["ratio_buffer"] += 1
                    buffer_bytes = max(0, int(remaining_buffer))
                    repository.record_grab_deferral(
                        selected,
                        "Ratio reserve",
                        f"Needs {size} bytes, buffer has {buffer_bytes} bytes",
                    )
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
            else:
                state = str(existing[0].get("state", "")).lower()
                if "pause" in state or "stop" in state:
                    await qbit.resume_torrents([torrent_hash])
                elif "error" in state or "missing" in state:
                    await qbit.recheck_torrents([torrent_hash])
                    await qbit.resume_torrents([torrent_hash])
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
                    "wedge_fallback_used": wedge_failed_fallback,
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
