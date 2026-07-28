from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .database import connect
from .migration import canonical_json


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def pending_selected(self) -> list[dict[str, Any]]:
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT payload_json FROM selected_torrents
                   WHERE json_extract(payload_json, '$.started_at') IS NULL
                     AND json_extract(payload_json, '$.removed_at') IS NULL
                   ORDER BY mam_id"""
            )
            return [json.loads(row[0]) for row in rows]

    def record_started(
        self, selected: dict[str, Any], torrent_hash: str, *, wedged: bool = False
    ) -> None:
        now = datetime.now(UTC).isoformat()
        selected = dict(selected)
        selected["hash"] = torrent_hash
        selected["started_at"] = now
        torrent = {
            "id": torrent_hash,
            "id_is_hash": True,
            "mam_id": selected["mam_id"],
            "abs_id": None,
            "goodreads_id": selected.get("goodreads_id"),
            "library_path": None,
            "library_files": [],
            "linker": None,
            "category": selected.get("category"),
            "selected_audio_format": None,
            "selected_ebook_format": None,
            "title_search": selected["title_search"],
            "meta": selected.get("meta", {}),
            "created_at": now,
            "replaced_with": None,
            "request_matadata_update": False,
            "library_mismatch": None,
            "client_status": None,
        }
        event = {
            "id": str(uuid4()),
            "torrent_id": torrent_hash,
            "mam_id": selected["mam_id"],
            "created_at": now,
            "event": {
                "Grabbed": {
                    "grabber": selected.get("grabber"),
                    "cost": selected.get("cost"),
                    "wedged": wedged,
                }
            },
        }
        with connect(self.path) as connection:
            with connection:
                connection.execute(
                    """UPDATE selected_torrents
                       SET hash = ?, payload_json = ? WHERE mam_id = ?""",
                    (torrent_hash, canonical_json(selected), selected["mam_id"]),
                )
                connection.execute(
                    """INSERT INTO torrents
                       (id, mam_id, title_search, created_at_json, payload_json)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json""",
                    (
                        torrent_hash,
                        selected["mam_id"],
                        selected["title_search"],
                        canonical_json(now),
                        canonical_json(torrent),
                    ),
                )
                connection.execute(
                    """INSERT INTO events
                       (id_json, torrent_id, mam_id, created_at_json, payload_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        canonical_json(event["id"]),
                        torrent_hash,
                        selected["mam_id"],
                        canonical_json(now),
                        canonical_json(event),
                    ),
                )

    def record_grab_error(self, selected: dict[str, Any], error: Exception) -> None:
        now = datetime.now(UTC).isoformat()
        identifier = {"Grabber": selected["mam_id"]}
        row = {
            "id": identifier,
            "title": selected.get("meta", {}).get("title", selected["title_search"]),
            "error": str(error),
            "meta": selected.get("meta"),
            "created_at": now,
        }
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO errored_torrents
                   (id_json, created_at_json, payload_json) VALUES (?, ?, ?)
                   ON CONFLICT(id_json) DO UPDATE SET
                     created_at_json=excluded.created_at_json,
                     payload_json=excluded.payload_json""",
                (canonical_json(identifier), canonical_json(now), canonical_json(row)),
            )
