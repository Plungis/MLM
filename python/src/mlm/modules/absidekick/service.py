from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .core import (
    APP_VERSION,
    ABSClient,
    MatchJob,
    approve_review_candidate,
    create_client,
    deep_merge,
    load_settings,
    preview_matches,
    public_settings,
    reject_review_item,
    save_settings,
    scan_review_items,
    utc_now,
)


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


class ABSidekickService:
    """Owns ABSidekick state while the suite owns HTTP and process lifecycle."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.settings_path = data_dir / "settings.json"
        self.review_state_path = data_dir / "review_state.json"
        self.settings = load_settings(self.settings_path)
        self.token = str(self.settings.get("connection", {}).get("token", ""))
        self.job: MatchJob | None = None
        self.review_state = _load_json(self.review_state_path, {"decisions": {}})
        self._lock = threading.RLock()

    def merged_settings(self, incoming: dict[str, Any] | None = None) -> dict[str, Any]:
        return deep_merge(self.settings, incoming or {})

    def client(
        self,
        settings: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> ABSClient:
        return create_client(settings or self.settings, token=token or self.token)

    def reviewed_ids(self) -> set[str]:
        decisions = self.review_state.get("decisions", {})
        return {str(item_id) for item_id in decisions}

    def filter_review_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reviewed = self.reviewed_ids()
        return [
            row for row in rows if str(row.get("item", {}).get("id")) not in reviewed
        ]

    def job_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            if not self.job:
                return None
            snapshot = self.job.snapshot()
            snapshot["reviewQueue"] = self.filter_review_rows(
                snapshot.get("reviewQueue", [])
            )
            return snapshot

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "version": APP_VERSION,
                "settings": public_settings(self.settings, bool(self.token)),
                "job": self.job_snapshot(),
            }

    def _incoming(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        incoming = payload.get("settings") or {}
        if not isinstance(incoming, dict):
            raise ValueError("settings must be an object")
        settings = self.merged_settings(incoming)
        incoming_token = incoming.get("connection", {}).get("token", "")
        token = str(payload.get("token") or incoming_token or self.token)
        return settings, token

    def _store(self, settings: dict[str, Any], token: str) -> None:
        with self._lock:
            self.settings = settings
            if token:
                self.token = token
            save_settings(self.settings_path, self.settings, token=self.token)

    def connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        libraries = self.client(settings, token).get("/api/libraries")
        self._store(settings, token)
        return {
            "ok": True,
            "message": "Connected to Audiobookshelf",
            "libraries": libraries,
            "settings": public_settings(self.settings, bool(self.token)),
        }

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        self._store(settings, token)
        return {
            "ok": True,
            "settings": public_settings(self.settings, bool(self.token)),
        }

    def libraries(self) -> dict[str, Any]:
        return {"ok": True, "libraries": self.client().get("/api/libraries")}

    def filter_data(self, library_id: str) -> dict[str, Any]:
        library_id = library_id or str(
            self.settings.get("connection", {}).get("libraryId", "")
        )
        if not library_id:
            raise ValueError("libraryId is required")
        payload = self.client().get(
            f"/api/libraries/{urllib.parse.quote(library_id)}/filterdata"
        )
        return {"ok": True, "filterData": payload}

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        limit = int(payload.get("limit") or 10)
        preview = preview_matches(self.client(settings, token), settings, limit=limit)
        return {"ok": True, "preview": preview}

    def scan_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        limit = int(
            payload.get("limit") or settings.get("review", {}).get("scanLimit", 25)
        )
        rows = scan_review_items(
            self.client(settings, token),
            settings,
            limit=limit,
            excluded_ids=self.reviewed_ids(),
        )
        return {
            "ok": True,
            "review": rows,
            "jobReviewQueue": (self.job_snapshot() or {}).get("reviewQueue", []),
        }

    def _mark_review_decision(
        self,
        item_id: str,
        action: str,
        row: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            decisions = self.review_state.setdefault("decisions", {})
            decisions[str(item_id)] = {
                "action": action,
                "time": utc_now(),
                "item": (row or {}).get("item", {}),
            }
            if self.job:
                self.job.remove_review_item(str(item_id))
            _save_json(self.review_state_path, self.review_state)

    def approve_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        item_id = str(payload.get("itemId") or "")
        if not item_id:
            raise ValueError("itemId is required")
        result = approve_review_candidate(
            self.client(settings, token),
            item_id,
            payload.get("candidate"),
            settings,
        )
        self._mark_review_decision(item_id, "approved", payload.get("row"))
        return {
            "ok": True,
            "message": "Review match approved",
            "result": result,
        }

    def reject_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        item_id = str(payload.get("itemId") or "")
        if not item_id:
            raise ValueError("itemId is required")
        tags = reject_review_item(self.client(settings, token), item_id, settings)
        self._mark_review_decision(item_id, "rejected", payload.get("row"))
        return {
            "ok": True,
            "message": "Review match rejected",
            "tags": tags,
        }

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.job and self.job.status in {"queued", "running", "paused"}:
                raise RuntimeError("A job is already running")
        settings, token = self._incoming(payload)
        job = MatchJob(
            str(int(time.time() * 1000)),
            self.client(settings, token),
            settings,
        )
        self._store(settings, token)
        with self._lock:
            self.job = job
            job.start()
        return {"ok": True, "job": self.job_snapshot()}

    def job_action(self, action: str) -> dict[str, Any]:
        with self._lock:
            if self.job:
                if action == "pause":
                    self.job.pause()
                elif action == "resume":
                    self.job.resume()
                elif action == "cancel":
                    self.job.cancel()
                else:
                    raise ValueError(f"unknown job action: {action}")
        return {"ok": True, "job": self.job_snapshot()}

    def cover(self, item_id: str) -> tuple[bytes, str]:
        if not item_id:
            raise ValueError("item id is required")
        client = self.client()
        request = urllib.request.Request(
            client.url(f"/api/items/{urllib.parse.quote(item_id)}/cover"),
            headers={
                "Authorization": f"Bearer {client.token}",
                "Accept": "image/*,*/*",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=client.timeout_seconds
            ) as response:
                return (
                    response.read(),
                    response.headers.get("Content-Type", "image/jpeg"),
                )
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise LookupError("Audiobookshelf cover is unavailable") from error
