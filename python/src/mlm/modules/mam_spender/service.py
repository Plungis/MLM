from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import unquote

from ...mam import MamClient, MamError
from ...repository import Repository
from .models import (
    FL_WEDGE_COST,
    GB_PER_BLOCK,
    MAX_POINTS_BUFFER,
    MAX_UPLOAD_BLOCKS_PER_RUN,
    MAX_UPLOAD_POINTS_PER_RUN,
    MIN_INTERVAL_MINUTES,
    POINTS_PER_BLOCK,
    VIP_RENEW_DAYS,
    Settings,
    Totals,
    UserSummary,
    dataclass_from_dict,
)
from .storage import MamSpenderStore

COMPONENT = "mam_spender"
BONUS_HISTORY_TYPES = (
    "giftPoints",
    "giftWedge",
    "wedgePF",
    "wedgeGFL",
    "torrentThanks",
    "millionaires",
)


def now() -> datetime:
    return datetime.now(UTC)


def parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%b %d, %Y %I:%M %p"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def clean_cookie_value(value: str) -> str:
    cleaned = unquote(value.strip().strip("'\""))
    if cleaned.casefold().startswith("mam_id="):
        cleaned = cleaned.split("=", 1)[1]
    return cleaned.split(";", 1)[0].strip()


def extract_mam_id(value: str) -> str:
    """Extract mam_id from raw values and common browser/curl exports."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    def walk(item: Any) -> str:
        if isinstance(item, dict):
            lowered = {str(key).casefold(): val for key, val in item.items()}
            if "mam_id" in lowered:
                return clean_cookie_value(str(lowered["mam_id"]))
            if str(lowered.get("name", "")).casefold() == "mam_id":
                return clean_cookie_value(str(lowered.get("value", "")))
            for child in item.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(item, list):
            for child in item:
                found = walk(child)
                if found:
                    return found
        return ""

    from_json = walk(parsed)
    if from_json:
        return from_json
    patterns = (
        r"(?:^|[;\s,])mam_id\s*=\s*['\"]?([^;,\s'\"]+)",
        r"['\"]name['\"]\s*:\s*['\"]mam_id['\"][\s\S]{0,240}?"
        r"['\"]value['\"]\s*:\s*['\"]([^'\"]+)",
        r"myanonamouse\.net\s+\S+\s+\S+\s+\S+\s+\S+\s+mam_id\s+([^\s]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return clean_cookie_value(match.group(1))
    for line in raw.splitlines():
        parts = line.strip().split()
        if len(parts) >= 7 and parts[-2].casefold() == "mam_id":
            return clean_cookie_value(parts[-1])
    if "\n" not in raw and "=" not in raw and ";" not in raw and len(raw) >= 12:
        return clean_cookie_value(raw)
    return ""


class MamSpenderService:
    def __init__(self, repository: Repository, mam: MamClient) -> None:
        self.repository = repository
        self.mam = mam
        self.store = MamSpenderStore(repository)
        self.settings = dataclass_from_dict(Settings, self.store.value("settings", {}))
        self.settings.normalize()
        self.totals = dataclass_from_dict(Totals, self.store.value("totals", {}))
        self.user = dataclass_from_dict(
            UserSummary, self.store.value("user_summary", {})
        )
        self.scheduler_enabled = bool(self.store.value("scheduler_enabled", False))
        self.next_run_time = parse_datetime(self.store.value("next_run_time"))
        self.last_scan_points = self.store.value("last_scan_points")
        self.last_scan_time = parse_datetime(self.store.value("last_scan_time"))
        self.points_per_min = self.store.value("points_per_min")
        self.mam_user_data = dict(self.store.value("mam_user_data", {}))
        self.mam_user_error = str(self.store.value("mam_user_error", ""))
        self.mam_user_fetched_at = str(self.store.value("mam_user_fetched_at", ""))
        self.bonus_history = list(self.store.value("bonus_history", []))[-500:]
        self.bonus_history_error = str(self.store.value("bonus_history_error", ""))
        self.bonus_history_fetched_at = str(
            self.store.value("bonus_history_fetched_at", "")
        )
        self.automation_running = False
        self._closed = False
        self._scheduler_task: asyncio.Task[None] | None = None
        self._automation_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.scheduler_enabled and self.next_run_time is None:
            self._schedule_next()
        if not self._scheduler_task or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(
                self._scheduler_loop(), name="mam-spender-scheduler"
            )
        self.log("MAM-Spender module started.")

    async def close(self) -> None:
        self._closed = True
        for task in (self._scheduler_task, self._automation_task):
            if task and not task.done():
                task.cancel()
        for task in (self._scheduler_task, self._automation_task):
            if task:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def log(
        self,
        message: str,
        *,
        level: str = "info",
        context: dict[str, Any] | None = None,
    ) -> None:
        safe = re.sub(
            r"mam_id\s*=\s*[^;,\s]+",
            "mam_id=[redacted]",
            str(message),
            flags=re.IGNORECASE,
        )
        self.repository.log_activity(
            COMPONENT, safe, level=level, context=context or {}
        )

    def _persist_runtime(self) -> None:
        values = {
            "settings": asdict(self.settings),
            "totals": asdict(self.totals),
            "user_summary": asdict(self.user),
            "scheduler_enabled": self.scheduler_enabled,
            "next_run_time": (
                self.next_run_time.isoformat() if self.next_run_time else None
            ),
            "last_scan_points": self.last_scan_points,
            "last_scan_time": (
                self.last_scan_time.isoformat() if self.last_scan_time else None
            ),
            "points_per_min": self.points_per_min,
            "mam_user_data": self.mam_user_data,
            "mam_user_error": self.mam_user_error,
            "mam_user_fetched_at": self.mam_user_fetched_at,
            "bonus_history": self.bonus_history[-500:],
            "bonus_history_error": self.bonus_history_error,
            "bonus_history_fetched_at": self.bonus_history_fetched_at,
        }
        for key, value in values.items():
            self.store.set_value(key, value)

    def public_state(self) -> dict[str, Any]:
        remaining = None
        if self.next_run_time:
            remaining = max(0, int((self.next_run_time - now()).total_seconds()))
        return {
            "settings": asdict(self.settings),
            "totals": asdict(self.totals),
            "user": asdict(self.user),
            "scheduler_enabled": self.scheduler_enabled,
            "automation_running": self.automation_running,
            "next_run_time": (
                self.next_run_time.isoformat() if self.next_run_time else None
            ),
            "next_run_seconds": remaining,
            "last_scan_points": self.last_scan_points,
            "points_per_min": self.points_per_min,
            "history": self.store.history(),
            "spend_events": self.store.events(),
            "mam_user_data": self.mam_user_data,
            "mam_user_error": self.mam_user_error,
            "mam_user_fetched_at": self.mam_user_fetched_at,
            "bonus_history": self.bonus_history,
            "bonus_history_error": self.bonus_history_error,
            "bonus_history_fetched_at": self.bonus_history_fetched_at,
            "logs": list(
                reversed(
                    self.repository.recent_activity(limit=300, component=COMPONENT)
                )
            ),
            "session_id_saved": bool(self.repository.config_value("mam_id")),
            "constants": {
                "points_per_block": POINTS_PER_BLOCK,
                "gb_per_block": GB_PER_BLOCK,
                "max_upload_blocks_per_run": MAX_UPLOAD_BLOCKS_PER_RUN,
                "max_upload_points_per_run": MAX_UPLOAD_POINTS_PER_RUN,
                "fl_wedge_cost": FL_WEDGE_COST,
                "vip_renew_days": VIP_RENEW_DAYS,
                "min_interval_minutes": MIN_INTERVAL_MINUTES,
                "max_points_buffer": MAX_POINTS_BUFFER,
            },
        }

    def update_settings(self, incoming: dict[str, Any]) -> dict[str, Any]:
        for key in ("buy_vip", "buy_upload_credit", "alternate_fl_upload", "fl_only"):
            if key in incoming:
                setattr(self.settings, key, bool(incoming[key]))
        if "alternate_next_purchase" in incoming:
            self.settings.alternate_next_purchase = str(
                incoming["alternate_next_purchase"]
            )
        if "theme" in incoming:
            self.settings.theme = str(incoming["theme"])
        if "points_buffer" in incoming:
            self.settings.points_buffer = int(incoming["points_buffer"])
        if "next_run_delay_minutes" in incoming:
            self.settings.next_run_delay_minutes = int(
                incoming["next_run_delay_minutes"]
            )
        self.settings.normalize()
        if self.scheduler_enabled:
            self._schedule_next()
        self._persist_runtime()
        self.log("MAM-Spender settings saved and applied.")
        return self.public_state()

    def start_scheduler(self) -> dict[str, Any]:
        self.scheduler_enabled = True
        self._schedule_next()
        self._persist_runtime()
        self.log("Scheduled spending started.")
        return self.public_state()

    def pause_scheduler(self) -> dict[str, Any]:
        self.scheduler_enabled = False
        self.next_run_time = None
        self._persist_runtime()
        self.log("Scheduled spending paused.")
        return self.public_state()

    def reset_totals(self) -> dict[str, Any]:
        self.totals = Totals()
        self.store.clear_totals()
        self._persist_runtime()
        self.store.add_history({"kind": "manual", "result": "Cumulative totals reset."})
        self.log("Cumulative totals reset.")
        return self.public_state()

    def save_session_id(self, value: str) -> dict[str, Any]:
        mam_id = extract_mam_id(value)
        if not mam_id:
            raise ValueError(
                "Could not find mam_id in that value, Cookie header, or export."
            )
        self.mam.set_mam_id(mam_id)
        self.repository.set_config_value("mam_id", mam_id)
        self.log("Shared MyAnonaSuite API Session_ID updated.")
        return self.public_state()

    def import_legacy(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.settings = dataclass_from_dict(Settings, payload.get("settings", {}))
        self.settings.normalize()
        self.totals = dataclass_from_dict(Totals, payload.get("totals", {}))
        self.scheduler_enabled = bool(payload.get("scheduler_enabled", False))
        self.next_run_time = parse_datetime(payload.get("next_run_time"))
        self.last_scan_points = payload.get("last_scan_points")
        self.last_scan_time = parse_datetime(payload.get("last_scan_time"))
        self.mam_user_data = dict(payload.get("mam_user_data", {}))
        self.mam_user_error = str(payload.get("mam_user_error", ""))
        self.mam_user_fetched_at = str(payload.get("mam_user_fetched_at", ""))
        self.bonus_history = list(payload.get("bonus_history", []))[-500:]
        self.bonus_history_error = str(payload.get("bonus_history_error", ""))
        self.bonus_history_fetched_at = str(payload.get("bonus_history_fetched_at", ""))
        for row in list(payload.get("history", []))[-300:]:
            if isinstance(row, dict):
                self.store.add_history(row)
        for row in list(payload.get("spend_events", []))[-1000:]:
            if isinstance(row, dict):
                self.store.add_event(row)
        plain_session = str(payload.get("settings", {}).get("plain_session_id", ""))
        if plain_session:
            self.save_session_id(plain_session)
        self._persist_runtime()
        self.log("Imported settings and history from MAM-Spender Web Edition.")
        return self.public_state()

    def run_now(self, *, fl_only_override: bool = False) -> dict[str, Any]:
        if self.automation_running:
            return self.public_state()
        self.automation_running = True
        self._automation_task = asyncio.create_task(
            self._run_and_reschedule(fl_only_override),
            name="mam-spender-run",
        )
        self.log(
            "Manual spending scan requested.",
            context={"freeleech_only_override": fl_only_override},
        )
        return self.public_state()

    async def _scheduler_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(1)
            if (
                self.scheduler_enabled
                and not self.automation_running
                and self.next_run_time
                and now() >= self.next_run_time
            ):
                self.automation_running = True
                self._automation_task = asyncio.create_task(
                    self._run_and_reschedule(False), name="mam-spender-run"
                )

    async def _run_and_reschedule(self, fl_only_override: bool) -> None:
        try:
            await self.run_automation(fl_only_override=fl_only_override)
        finally:
            self.automation_running = False
            if self.scheduler_enabled:
                self._schedule_next()
            self._persist_runtime()

    def _schedule_next(self) -> None:
        self.next_run_time = now() + timedelta(
            minutes=self.settings.next_run_delay_minutes
        )

    async def run_automation(self, *, fl_only_override: bool = False) -> None:
        started_at = now()
        result = "Completed"
        points_start: int | None = None
        points_end: int | None = None
        purchased_gb = 0
        wedges = 0
        vip_purchased = False
        self.log("Starting spending scan.")
        try:
            summary_data = await self._summary_data()
            uid = str(summary_data.get("uid") or summary_data.get("id") or "")
            if not uid:
                raise MamError("MaM session did not return a user ID")
            self._update_user_summary(summary_data)
            points = await self._get_points(uid)
            if points <= 0:
                raise MamError("MaM did not return a usable bonus-point balance")
            points_start = points
            points_end = points
            self._update_points_rate(points)
            self.log(
                f"Current balance: {points:,} points.",
                context={"points": points, "uid": uid},
            )

            if self.settings.buy_vip:
                points, vip_purchased = await self._maybe_buy_vip(
                    uid, points, summary_data
                )
                points_end = points

            alternate_target = (
                self.settings.alternate_next_purchase
                if self.settings.alternate_fl_upload
                else ""
            )
            buy_wedge = (
                self.settings.fl_only
                or fl_only_override
                or alternate_target == "freeleech_wedge"
            )
            if buy_wedge:
                points, wedge_bought = await self._maybe_buy_wedge(uid, points)
                wedges += int(wedge_bought)
                points_end = points
                if wedge_bought and self.settings.alternate_fl_upload:
                    self.settings.alternate_next_purchase = "upload_credit"

            stop_after_wedge = (
                self.settings.fl_only
                or fl_only_override
                or (
                    self.settings.alternate_fl_upload
                    and alternate_target == "freeleech_wedge"
                )
            )
            if not stop_after_wedge and self.settings.buy_upload_credit:
                points, purchased_gb = await self._buy_upload_credit(uid, points)
                points_end = points
                if purchased_gb and self.settings.alternate_fl_upload:
                    self.settings.alternate_next_purchase = "freeleech_wedge"
            elif stop_after_wedge:
                self.log("Upload-credit purchase skipped by the active purchase mode.")
            else:
                self.log("Upload-credit purchases are disabled.")

            spent = max((points_start or points) - points, 0)
            self.totals.cumulative_upload_gb += purchased_gb
            self.totals.cumulative_points_spent += spent
            self.totals.cumulative_freeleech_wedges += wedges
            self.totals.cumulative_freeleech_points_spent += wedges * FL_WEDGE_COST
            self.totals.cumulative_vip_purchases += int(vip_purchased)
            if spent or purchased_gb or wedges or vip_purchased:
                refreshed = await self._summary_data()
                self._update_user_summary(refreshed)
            self.log(
                "Spending scan complete.",
                level="success",
                context={
                    "points_spent": spent,
                    "upload_gb": purchased_gb,
                    "freeleech_wedges": wedges,
                    "vip_purchased": vip_purchased,
                },
            )
        except Exception as error:  # noqa: BLE001 - preserve scheduler operation
            result = f"Error: {type(error).__name__}: {error}"
            self.log(
                "Spending scan failed.",
                level="error",
                context={"error": result},
            )
        finally:
            spent = (
                max(points_start - points_end, 0)
                if points_start is not None and points_end is not None
                else 0
            )
            self.store.add_history(
                {
                    "created_at": now().isoformat(),
                    "started_at": started_at.isoformat(),
                    "kind": "run",
                    "result": result,
                    "points_before": points_start,
                    "points_after": points_end,
                    "points_spent": spent,
                    "upload_gb": purchased_gb,
                    "freeleech_wedges": wedges,
                    "vip_purchased": vip_purchased,
                }
            )
            self._persist_runtime()

    async def _summary_data(self) -> dict[str, Any]:
        data = await self.mam.user_info()
        if not isinstance(data, dict):
            raise MamError("MaM returned an unexpected user summary")
        return data

    async def _get_points(self, uid: str) -> int:
        data = await self.mam.request_json("/jsonLoad.php", params={"uid": uid})
        if not isinstance(data, dict):
            return 0
        try:
            return int(float(str(data.get("seedbonus") or 0).replace(",", "")))
        except (TypeError, ValueError):
            return 0

    async def _maybe_buy_vip(
        self, uid: str, points: int, summary_data: dict[str, Any]
    ) -> tuple[int, bool]:
        expiry = parse_datetime(summary_data.get("vip_until"))
        remaining_days = (expiry - now()).total_seconds() / 86_400 if expiry else 0
        self.log(
            "Checked VIP renewal window.",
            context={
                "vip_expires": expiry.isoformat() if expiry else None,
                "days_remaining": round(remaining_days, 1),
                "renew_at_days": VIP_RENEW_DAYS,
            },
        )
        if expiry and remaining_days > VIP_RENEW_DAYS:
            return points, False
        response = await self.mam.request_json(
            "/json/bonusBuy.php/",
            params={
                "spendtype": "VIP",
                "duration": "max",
                "_": int(now().timestamp() * 1000),
            },
        )
        if isinstance(response, dict) and self._response_rejected(response):
            self.log(
                "VIP renewal was rejected by MaM.",
                level="error",
                context={"response": self._safe_response(response)},
            )
            return points, False
        await asyncio.sleep(0.8)
        after = await self._get_points(uid)
        if after <= 0 and self._response_succeeded(response):
            after = points
        spent = max(points - after, 0)
        if spent <= 0 and not self._response_succeeded(response):
            self.log(
                "VIP renewal could not be confirmed because points did not decrease.",
                level="error",
                context={"before": points, "after": after},
            )
            return points, False
        if spent > 0:
            self._add_spend_event("vip", "VIP Renewal", spent, 1, "renewal", after)
        self.log("VIP renewal confirmed.", level="success", context={"spent": spent})
        return after, True

    async def _maybe_buy_wedge(self, uid: str, points: int) -> tuple[int, bool]:
        threshold = FL_WEDGE_COST + self.settings.points_buffer
        if points < threshold:
            self.log(
                "Freeleech Wedge skipped: balance would cross the points buffer.",
                context={
                    "points": points,
                    "required": threshold,
                    "buffer": self.settings.points_buffer,
                },
            )
            return points, False
        self.log(
            "Requesting one Freeleech Wedge from the bonus store.",
            context={"cost": FL_WEDGE_COST, "buffer": self.settings.points_buffer},
        )
        response = await self.mam.request_json(
            "/json/bonusBuy.php/",
            params={
                "spendtype": "wedges",
                "source": "points",
                "_": int(now().timestamp() * 1000),
            },
        )
        if isinstance(response, dict) and self._response_rejected(response):
            self.log(
                "Freeleech Wedge purchase was rejected by MaM.",
                level="error",
                context={"response": self._safe_response(response)},
            )
            return points, False
        await asyncio.sleep(0.8)
        after = await self._get_points(uid)
        if after <= 0 and self._response_succeeded(response):
            after = max(points - FL_WEDGE_COST, 0)
        observed_spend = max(points - after, 0)
        if observed_spend < FL_WEDGE_COST and not self._response_succeeded(response):
            self.log(
                "Freeleech Wedge purchase failed verification.",
                level="error",
                context={
                    "before": points,
                    "after": after,
                    "expected_decrease": FL_WEDGE_COST,
                    "response": self._safe_response(response),
                },
            )
            return points, False
        # Points continue accruing while the confirmation request is in flight,
        # so the observed delta can be a few points below the fixed store cost.
        spent = FL_WEDGE_COST
        self._add_spend_event(
            "freeleech_wedge", "Freeleech Wedge", spent, 1, "wedge", after
        )
        self.log(
            "Freeleech Wedge purchase confirmed.",
            level="success",
            context={"before": points, "after": after, "spent": spent},
        )
        return after, True

    async def _buy_upload_credit(self, uid: str, points: int) -> tuple[int, int]:
        available = max(0, points - self.settings.points_buffer)
        blocks = min(MAX_UPLOAD_BLOCKS_PER_RUN, available // POINTS_PER_BLOCK)
        if blocks <= 0:
            self.log(
                "Upload credit skipped: not enough points above the buffer.",
                context={
                    "points": points,
                    "required": POINTS_PER_BLOCK + self.settings.points_buffer,
                    "buffer": self.settings.points_buffer,
                },
            )
            return points, 0
        upload_gb = blocks * GB_PER_BLOCK
        expected_spend = blocks * POINTS_PER_BLOCK
        self.log(
            f"Requesting {upload_gb} GiB upload credit.",
            context={"blocks": blocks, "expected_spend": expected_spend},
        )
        response = await self.mam.request_json(
            "/json/bonusBuy.php/",
            params={"spendtype": "upload", "amount": upload_gb},
        )
        if isinstance(response, dict) and self._response_rejected(response):
            self.log(
                "Upload-credit purchase was rejected by MaM.",
                level="error",
                context={"response": self._safe_response(response)},
            )
            return points, 0
        await asyncio.sleep(0.8)
        after = await self._get_points(uid)
        if after <= 0 and self._response_succeeded(response):
            after = max(points - expected_spend, 0)
        spent = max(points - after, 0)
        if spent < expected_spend and not self._response_succeeded(response):
            self.log(
                "Upload-credit purchase failed verification.",
                level="error",
                context={
                    "before": points,
                    "after": after,
                    "expected_spend": expected_spend,
                    "response": self._safe_response(response),
                },
            )
            return points, 0
        self._add_spend_event(
            "upload_credit",
            "Upload Credit",
            expected_spend,
            upload_gb,
            "GiB",
            after,
        )
        self.log(
            "Upload-credit purchase confirmed.",
            level="success",
            context={"upload_gb": upload_gb, "spent": spent, "balance": after},
        )
        return after, upload_gb

    def _update_points_rate(self, current: int) -> None:
        current_time = now()
        if self.last_scan_points is not None and self.last_scan_time:
            minutes = (current_time - self.last_scan_time).total_seconds() / 60
            earned = current - int(self.last_scan_points)
            self.points_per_min = earned / minutes if earned > 0 and minutes >= 1 else 0
        else:
            self.points_per_min = None
        self.last_scan_points = current
        self.last_scan_time = current_time

    def _update_user_summary(self, data: dict[str, Any]) -> None:
        self.user = UserSummary(
            username=str(data.get("username") or "N/A"),
            vip_expires=self._format_vip(data.get("vip_until")),
            downloaded=str(data.get("downloaded") or "N/A"),
            uploaded=str(data.get("uploaded") or "N/A"),
            ratio=str(data.get("ratio") or "N/A"),
        )

    def _add_spend_event(
        self,
        category: str,
        label: str,
        points_spent: int,
        units: int,
        unit_label: str,
        balance_after: int,
    ) -> None:
        self.store.add_event(
            {
                "created_at": now().isoformat(),
                "category": category,
                "label": label,
                "points_spent": points_spent,
                "units": units,
                "unit_label": unit_label,
                "balance_after": balance_after,
            }
        )

    async def refresh_mam_user_data(self) -> dict[str, Any]:
        try:
            data = await self.mam.request_json(
                "/jsonLoad.php",
                params={"notif": "true", "snatch_summary": "true"},
            )
            if not isinstance(data, dict):
                raise MamError("MaM returned unexpected user-data JSON")
            uid = str(data.get("uid") or data.get("id") or "")
            if uid:
                detail = await self.mam.request_json(
                    "/jsonLoad.php", params={"id": uid}
                )
                if isinstance(detail, dict):
                    data = {**detail, **data}
            self.mam_user_data = self._normalize_user_data(data)
            self.mam_user_error = ""
            self.mam_user_fetched_at = now().isoformat()
            self._update_user_summary(data)
            self.log("MaM account data refreshed.", level="success")
        except Exception as error:  # noqa: BLE001 - return cached data with error
            self.mam_user_error = f"{type(error).__name__}: {error}"
            self.mam_user_fetched_at = now().isoformat()
            self.log(
                "MaM account-data refresh failed.",
                level="error",
                context={"error": self.mam_user_error},
            )
        self._persist_runtime()
        return self.public_state()

    async def refresh_bonus_history(self) -> dict[str, Any]:
        params = [("type[]", item) for item in BONUS_HISTORY_TYPES]
        try:
            data = await self.mam.request_json(
                "/json/userBonusHistory.php", params=params
            )
            if not isinstance(data, list):
                raise MamError("MaM returned unexpected bonus-history JSON")
            self.bonus_history = [
                self._normalize_bonus_entry(item)
                for item in data[:500]
                if isinstance(item, dict)
            ]
            self.bonus_history_error = ""
            self.bonus_history_fetched_at = now().isoformat()
            self.log(
                f"MaM bonus history refreshed ({len(self.bonus_history)} rows).",
                level="success",
            )
        except Exception as error:  # noqa: BLE001 - return cached data with error
            self.bonus_history_error = f"{type(error).__name__}: {error}"
            self.bonus_history_fetched_at = now().isoformat()
            self.log(
                "MaM bonus-history refresh failed.",
                level="error",
                context={"error": self.bonus_history_error},
            )
        self._persist_runtime()
        return self.public_state()

    @staticmethod
    def _safe_response(response: Any) -> Any:
        if not isinstance(response, dict):
            return str(response)[:500]
        return {
            str(key): value
            for key, value in response.items()
            if str(key).casefold() not in {"mam_id", "session_id", "cookie"}
        }

    @staticmethod
    def _response_succeeded(response: Any) -> bool:
        if not isinstance(response, dict) or "success" not in response:
            return False
        return str(response.get("success")).strip().casefold() in {
            "1",
            "true",
            "yes",
        }

    @classmethod
    def _response_rejected(cls, response: dict[str, Any]) -> bool:
        return "success" in response and not cls._response_succeeded(response)

    @staticmethod
    def _first(data: dict[str, Any], *keys: str) -> str:
        lowered = {str(key).casefold(): value for key, value in data.items()}
        for key in keys:
            value = lowered.get(key.casefold())
            if value not in (None, ""):
                return str(value)
        return "N/A"

    @staticmethod
    def _strip_html(value: Any) -> str:
        without_tags = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
        return re.sub(r"\s+", " ", without_tags).strip()

    def _normalize_user_data(self, data: dict[str, Any]) -> dict[str, Any]:
        raw_notifications = data.get("notifs") or data.get("notifications") or []
        if isinstance(raw_notifications, dict):
            raw_notifications = list(raw_notifications.values())
        elif not isinstance(raw_notifications, list):
            raw_notifications = [raw_notifications] if raw_notifications else []
        notifications: list[str] = []
        for item in raw_notifications[:8]:
            if isinstance(item, dict):
                item = (
                    item.get("message")
                    or item.get("text")
                    or item.get("body")
                    or item.get("title")
                    or item
                )
            cleaned = self._strip_html(item)
            if cleaned:
                notifications.append(cleaned)
        unsats = data.get("unsats") or data.get("unsat") or data.get("unsatisfied")
        if isinstance(unsats, dict):
            count = unsats.get("count")
            limit = unsats.get("limit")
            unsats = f"{count} / {limit}" if limit is not None else str(count or "N/A")
            if bool(unsats) and data.get("red"):
                unsats += " (flagged)"
        return {
            "username": self._first(data, "username"),
            "uid": self._first(data, "uid", "id"),
            "class": self._first(data, "classname", "class"),
            "ratio": self._first(data, "ratio"),
            "downloaded": self._first(data, "downloaded"),
            "uploaded": self._first(data, "uploaded"),
            "bonus": self._first(
                data, "seedbonus", "bonus", "bonuspoints", "bonus_points"
            ),
            "invites": self._first(data, "invites"),
            "fl_wedges": self._first(
                data, "fl_wedges", "flwedge", "freeleech_wedges", "wedges"
            ),
            "unsats": str(unsats or "N/A"),
            "notifications": notifications,
            "loaded_keys": sorted(str(key) for key in data)[:60],
        }

    @staticmethod
    def _normalize_bonus_entry(item: dict[str, Any]) -> dict[str, Any]:
        timestamp = item.get("timestamp")
        try:
            timestamp_value = datetime.fromtimestamp(
                float(timestamp), tz=UTC
            ).isoformat()
        except (TypeError, ValueError, OSError):
            timestamp_value = str(timestamp or "")
        return {
            "timestamp": timestamp_value,
            "amount": item.get("amount"),
            "type": str(item.get("type") or "N/A"),
            "tid": item.get("tid"),
            "title": str(item.get("title") or "N/A"),
            "other_userid": item.get("other_userid"),
            "other_name": str(item.get("other_name") or "N/A"),
        }

    @staticmethod
    def _format_vip(value: Any) -> str:
        parsed = parse_datetime(value)
        return parsed.isoformat() if parsed else str(value or "N/A")


def default_public_state(repository: Repository) -> dict[str, Any]:
    store = MamSpenderStore(repository)
    settings = dataclass_from_dict(Settings, store.value("settings", {}))
    settings.normalize()
    totals = dataclass_from_dict(Totals, store.value("totals", {}))
    user = dataclass_from_dict(UserSummary, store.value("user_summary", {}))
    return {
        "settings": asdict(settings),
        "totals": asdict(totals),
        "user": asdict(user),
        "scheduler_enabled": bool(store.value("scheduler_enabled", False)),
        "automation_running": False,
        "next_run_time": store.value("next_run_time"),
        "next_run_seconds": None,
        "last_scan_points": store.value("last_scan_points"),
        "points_per_min": store.value("points_per_min"),
        "history": store.history(),
        "spend_events": store.events(),
        "mam_user_data": store.value("mam_user_data", {}),
        "mam_user_error": store.value("mam_user_error", ""),
        "mam_user_fetched_at": store.value("mam_user_fetched_at", ""),
        "bonus_history": store.value("bonus_history", []),
        "bonus_history_error": store.value("bonus_history_error", ""),
        "bonus_history_fetched_at": store.value("bonus_history_fetched_at", ""),
        "logs": list(
            reversed(repository.recent_activity(limit=300, component=COMPONENT))
        ),
        "session_id_saved": bool(repository.config_value("mam_id")),
        "constants": {
            "points_per_block": POINTS_PER_BLOCK,
            "gb_per_block": GB_PER_BLOCK,
            "max_upload_blocks_per_run": MAX_UPLOAD_BLOCKS_PER_RUN,
            "max_upload_points_per_run": MAX_UPLOAD_POINTS_PER_RUN,
            "fl_wedge_cost": FL_WEDGE_COST,
            "vip_renew_days": VIP_RENEW_DAYS,
            "min_interval_minutes": MIN_INTERVAL_MINUTES,
            "max_points_buffer": MAX_POINTS_BUFFER,
        },
    }
