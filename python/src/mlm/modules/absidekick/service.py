from __future__ import annotations

import contextlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

from .core import (
    APP_VERSION,
    ABSAPIError,
    ABSClient,
    MatchJob,
    approve_review_candidate,
    create_client,
    deep_merge,
    google_books_key_fingerprint,
    google_books_key_is_ready,
    google_error_is_transient,
    load_settings,
    preview_matches,
    public_settings,
    reject_review_item,
    save_settings,
    scan_review_items,
    search_review_candidates,
    test_google_books_api_key,
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
        self.activity: dict[str, Any] = {
            "id": "",
            "kind": "",
            "status": "idle",
            "title": "Ready",
            "detail": "No ABSidekick operation is currently running.",
            "phase": "idle",
            "current": 0,
            "total": 0,
            "currentTitle": "",
            "startedAt": "",
            "finishedAt": "",
        }

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
                "activity": dict(self.activity),
            }

    def activity_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"ok": True, "activity": dict(self.activity)}

    def _begin_activity(self, kind: str, title: str, detail: str) -> str:
        activity_id = str(time.time_ns())
        with self._lock:
            self.activity = {
                "id": activity_id,
                "kind": kind,
                "status": "running",
                "title": title,
                "detail": detail,
                "phase": "starting",
                "current": 0,
                "total": 0,
                "currentTitle": "",
                "startedAt": utc_now(),
                "finishedAt": "",
            }
        return activity_id

    def _update_activity(self, activity_id: str, update: dict[str, Any]) -> None:
        with self._lock:
            if self.activity.get("id") != activity_id:
                return
            for key in ("phase", "detail", "current", "total", "currentTitle"):
                if key in update:
                    self.activity[key] = update[key]

    def _finish_activity(self, activity_id: str, status: str, detail: str) -> None:
        with self._lock:
            if self.activity.get("id") != activity_id:
                return
            self.activity.update(
                {
                    "status": status,
                    "detail": detail,
                    "phase": "finished",
                    "finishedAt": utc_now(),
                }
            )

    def _incoming(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        incoming = payload.get("settings") or {}
        if not isinstance(incoming, dict):
            raise ValueError("settings must be an object")
        incoming = json.loads(json.dumps(incoming))
        incoming.pop("providers", None)
        settings = self.merged_settings(incoming)
        incoming_token = incoming.get("connection", {}).get("token", "")
        token = str(payload.get("token") or incoming_token or self.token)
        return settings, token

    def _apply_google_key_input(
        self, settings: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        if "googleBooksApiKey" not in payload:
            return
        api_key = str(payload.get("googleBooksApiKey") or "").strip()
        if len(api_key) > 500:
            raise ValueError("Google Books API key is too long")
        providers = settings.setdefault("providers", {})
        current = str(providers.get("googleBooksApiKey") or "")
        if api_key == current:
            return
        providers.update(
            {
                "googleBooksApiKey": api_key,
                "googleBooksApiKeyValidated": False,
                "googleBooksApiKeyFingerprint": "",
                "googleBooksApiKeyValidatedAt": "",
                "googleBooksLastError": (
                    "API key has not been tested." if api_key else ""
                ),
            }
        )

    def _apply_open_library_input(
        self, settings: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        providers_payload = (
            payload.get("providers")
            if isinstance(payload.get("providers"), dict)
            else {}
        )
        incoming_settings = (
            payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        )
        incoming_providers = (
            incoming_settings.get("providers")
            if isinstance(incoming_settings.get("providers"), dict)
            else {}
        )
        has_enabled = (
            "openLibraryEnabled" in payload
            or "openLibraryEnabled" in providers_payload
            or "openLibraryEnabled" in incoming_providers
        )
        has_contact = (
            "openLibraryContactEmail" in payload
            or "openLibraryContactEmail" in providers_payload
            or "openLibraryContactEmail" in incoming_providers
        )
        if not has_enabled and not has_contact:
            return

        providers = settings.setdefault("providers", {})
        enabled_val = (
            payload.get("openLibraryEnabled")
            if "openLibraryEnabled" in payload
            else providers_payload.get("openLibraryEnabled")
            if "openLibraryEnabled" in providers_payload
            else incoming_providers.get(
                "openLibraryEnabled", providers.get("openLibraryEnabled", True)
            )
        )
        contact_val = (
            payload.get("openLibraryContactEmail")
            if "openLibraryContactEmail" in payload
            else providers_payload.get("openLibraryContactEmail")
            if "openLibraryContactEmail" in providers_payload
            else incoming_providers.get(
                "openLibraryContactEmail",
                providers.get("openLibraryContactEmail", ""),
            )
        )
        enabled = bool(enabled_val)
        contact_email = str(contact_val or "").strip()
        if len(contact_email) > 254:
            raise ValueError("Open Library contact email is too long")
        if contact_email and not re.fullmatch(
            r"[^\s@]+@[^\s@]+\.[^\s@]+", contact_email
        ):
            raise ValueError("Enter a valid Open Library contact email")
        providers.update(
            {
                "openLibraryEnabled": enabled,
                "openLibraryContactEmail": contact_email,
            }
        )

    def _store(self, settings: dict[str, Any], token: str) -> None:
        with self._lock:
            self.settings = settings
            if token:
                self.token = token
            save_settings(self.settings_path, self.settings, token=self.token)

    def connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        self._apply_google_key_input(settings, payload)
        self._apply_open_library_input(settings, payload)
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
        self._apply_google_key_input(settings, payload)
        self._apply_open_library_input(settings, payload)
        self._store(settings, token)
        return {
            "ok": True,
            "settings": public_settings(self.settings, bool(self.token)),
        }

    def test_google_books(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        self._apply_google_key_input(settings, payload)
        providers = settings.setdefault("providers", {})
        api_key = str(providers.get("googleBooksApiKey") or "").strip()
        if not api_key:
            raise ValueError("Enter a Google Books API key before testing it")
        was_ready = google_books_key_is_ready(settings)
        try:
            result = test_google_books_api_key(
                api_key,
                timeout_seconds=int(
                    settings.get("run", {}).get("searchTimeoutSeconds", 12)
                ),
            )
        except ABSAPIError as error:
            providers["googleBooksLastError"] = str(error)
            if not (was_ready and google_error_is_transient(error)):
                providers.update(
                    {
                        "googleBooksApiKeyValidated": False,
                        "googleBooksApiKeyFingerprint": "",
                        "googleBooksApiKeyValidatedAt": "",
                    }
                )
            self._store(settings, token)
            raise
        providers.update(
            {
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(api_key),
                "googleBooksApiKeyValidatedAt": utc_now(),
                "googleBooksLastError": "",
            }
        )
        self._store(settings, token)
        return {
            "ok": True,
            **result,
            "settings": public_settings(self.settings, bool(self.token)),
        }

    def clear_google_books(self) -> dict[str, Any]:
        settings = self.merged_settings()
        settings.setdefault("providers", {}).update(
            {
                "googleBooksApiKey": "",
                "googleBooksApiKeyValidated": False,
                "googleBooksApiKeyFingerprint": "",
                "googleBooksApiKeyValidatedAt": "",
                "googleBooksLastError": "",
            }
        )
        self._store(settings, self.token)
        return {
            "ok": True,
            "message": "Google Books API key removed; Google searches are disabled.",
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
        activity_id = self._begin_activity(
            "review-scan",
            "Scanning Review Tags",
            "Connecting to Audiobookshelf and loading review-tagged items…",
        )
        try:
            rows = scan_review_items(
                self.client(settings, token),
                settings,
                limit=limit,
                excluded_ids=self.reviewed_ids(),
                progress=lambda update: self._update_activity(activity_id, update),
            )
        except Exception as error:
            self._finish_activity(
                activity_id,
                "error",
                f"Review scan failed: {error}",
            )
            raise
        self._finish_activity(
            activity_id,
            "success",
            f"Review scan finished: loaded {len(rows['rows'])} item(s).",
        )
        return {
            "ok": True,
            "review": rows,
            "jobReviewQueue": (self.job_snapshot() or {}).get("reviewQueue", []),
        }

    def search_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings, token = self._incoming(payload)
        item_id = str(payload.get("itemId") or "").strip()
        if not item_id:
            raise ValueError("itemId is required")
        result = search_review_candidates(
            self.client(settings, token),
            item_id,
            payload.get("query") or {},
            settings,
        )
        return {"ok": True, **result}

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

    def auto_sync_library(self) -> dict[str, Any]:
        """Trigger an Audiobookshelf library scan and start an auto-match job."""
        with self._lock:
            if self.job and self.job.status in {"queued", "running", "paused"}:
                return {
                    "ok": False,
                    "message": "An ABSidekick matching job is already running",
                }
            library_id = str(
                self.settings.get("connection", {}).get("libraryId", "")
            ).strip()
            if not library_id:
                return {
                    "ok": False,
                    "message": "No library selected in ABSidekick configuration",
                }
            client = self.client()
        with contextlib.suppress(Exception):
            client.post(f"/api/libraries/{urllib.parse.quote(library_id)}/scan")

        job_settings = deepcopy(self.settings)
        job_settings.setdefault("targeting", {})["mode"] = "unprocessed"
        job = MatchJob(
            str(int(time.time() * 1000)),
            client,
            job_settings,
        )
        with self._lock:
            self.job = job
            job.start()
        return {"ok": True, "job": self.job_snapshot()}
