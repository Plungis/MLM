from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .database import connect
from .migration import canonical_json
from .search import normalize_title


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

    def config_value(self, key: str) -> str | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
            return str(row[0]) if row else None

    def set_config_value(self, key: str, value: str) -> None:
        payload = {"key": key, "value": value}
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO config(key, value, payload_json) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value, payload_json=excluded.payload_json""",
                (key, value, canonical_json(payload)),
            )

    def has_mam_id(self, mam_id: int) -> bool:
        with connect(self.path) as connection:
            selected = connection.execute(
                "SELECT 1 FROM selected_torrents WHERE mam_id = ?", (mam_id,)
            ).fetchone()
            library = connection.execute(
                "SELECT 1 FROM torrents WHERE mam_id = ?", (mam_id,)
            ).fetchone()
            return selected is not None or library is not None

    def records_with_title(self, title_search: str) -> list[dict[str, Any]]:
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT payload_json FROM selected_torrents WHERE title_search = ?
                   UNION ALL
                   SELECT payload_json FROM torrents WHERE title_search = ?""",
                (title_search, title_search),
            )
            return [json.loads(row[0]) for row in rows]

    def add_selected(self, selected: dict[str, Any]) -> None:
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO selected_torrents
                   (mam_id, hash, title_search, created_at_json, payload_json)
                   VALUES (?, NULL, ?, ?, ?)""",
                (
                    selected["mam_id"],
                    selected["title_search"],
                    canonical_json(selected["created_at"]),
                    canonical_json(selected),
                ),
            )

    def add_duplicate(
        self, torrent: dict[str, Any], duplicate_of: str | None = None
    ) -> None:
        row = {
            "mam_id": torrent["mam_id"],
            "dl_link": torrent.get("dl_link"),
            "title_search": torrent["title_search"],
            "meta": torrent["meta"],
            "created_at": datetime.now(UTC).isoformat(),
            "duplicate_of": duplicate_of,
        }
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO duplicate_torrents
                   (mam_id, title_search, created_at_json, payload_json)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(mam_id) DO UPDATE SET
                     payload_json=excluded.payload_json""",
                (
                    row["mam_id"],
                    row["title_search"],
                    canonical_json(row["created_at"]),
                    canonical_json(row),
                ),
            )

    def torrent(self, torrent_id: str) -> dict[str, Any] | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM torrents WHERE id = ?", (torrent_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def library_torrents(self) -> list[dict[str, Any]]:
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT payload_json FROM torrents
                   WHERE json_extract(payload_json, '$.library_path') IS NOT NULL
                   ORDER BY title_search"""
            )
            return [json.loads(row[0]) for row in rows]

    def record_linked(
        self, torrent: dict[str, Any], selected_mam_id: int | None
    ) -> None:
        event = {
            "id": str(uuid4()),
            "torrent_id": torrent["id"],
            "mam_id": torrent["mam_id"],
            "created_at": datetime.now(UTC).isoformat(),
            "event": {
                "Linked": {
                    "linker": torrent.get("linker"),
                    "library_path": torrent.get("library_path"),
                }
            },
        }
        with connect(self.path) as connection, connection:
            connection.execute(
                """INSERT INTO torrents
                       (id, mam_id, title_search, created_at_json, payload_json)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         mam_id=excluded.mam_id,
                         title_search=excluded.title_search,
                         payload_json=excluded.payload_json""",
                (
                    torrent["id"],
                    torrent["mam_id"],
                    torrent["title_search"],
                    canonical_json(torrent["created_at"]),
                    canonical_json(torrent),
                ),
            )
            if selected_mam_id is not None:
                connection.execute(
                    "DELETE FROM selected_torrents WHERE mam_id = ?",
                    (selected_mam_id,),
                )
            connection.execute(
                """INSERT INTO events
                       (id_json, torrent_id, mam_id, created_at_json, payload_json)
                       VALUES (?, ?, ?, ?, ?)""",
                (
                    canonical_json(event["id"]),
                    event["torrent_id"],
                    event["mam_id"],
                    canonical_json(event["created_at"]),
                    canonical_json(event),
                ),
            )

    def update_torrent(self, torrent: dict[str, Any]) -> None:
        with connect(self.path) as connection:
            connection.execute(
                """UPDATE torrents SET mam_id=?, title_search=?, payload_json=?
                   WHERE id=?""",
                (
                    torrent["mam_id"],
                    torrent["title_search"],
                    canonical_json(torrent),
                    torrent["id"],
                ),
            )

    def mark_removed_from_mam(self, torrent: dict[str, Any]) -> None:
        if torrent.get("client_status") == "RemovedFromMam":
            return
        torrent = dict(torrent)
        torrent["client_status"] = "RemovedFromMam"
        event = {
            "id": str(uuid4()),
            "torrent_id": torrent["id"],
            "mam_id": torrent["mam_id"],
            "created_at": datetime.now(UTC).isoformat(),
            "event": "RemovedFromMam",
        }
        with connect(self.path) as connection:
            connection.execute(
                "UPDATE torrents SET payload_json=? WHERE id=?",
                (canonical_json(torrent), torrent["id"]),
            )
            connection.execute(
                """INSERT INTO events
                   (id_json, torrent_id, mam_id, created_at_json, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    canonical_json(event["id"]),
                    event["torrent_id"],
                    event["mam_id"],
                    canonical_json(event["created_at"]),
                    canonical_json(event),
                ),
            )

    def upsert_list(self, row: dict[str, Any]) -> None:
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO lists(id, title, payload_json) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, payload_json=excluded.payload_json""",
                (row["id"], row["title"], canonical_json(row)),
            )

    def list_item(self, guid: list[str]) -> dict[str, Any] | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM list_items WHERE guid_json = ?",
                (canonical_json(guid),),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def upsert_list_item(self, row: dict[str, Any]) -> None:
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO list_items
                   (guid_json, list_id, title, created_at_json, payload_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(guid_json) DO UPDATE SET
                     title=excluded.title, payload_json=excluded.payload_json""",
                (
                    canonical_json(row["guid"]),
                    row["list_id"],
                    row["title"],
                    canonical_json(row["created_at"]),
                    canonical_json(row),
                ),
            )

    def table_rows(self, table: str, *, limit: int = 500) -> list[dict[str, Any]]:
        allowed = {
            "torrents",
            "selected_torrents",
            "duplicate_torrents",
            "errored_torrents",
            "events",
            "lists",
            "list_items",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        order = "created_at_json DESC" if table != "lists" else "title"
        with connect(self.path) as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM {table} ORDER BY {order} LIMIT ?",
                (limit,),
            )
            return [json.loads(row[0]) for row in rows]

    def counts(self) -> dict[str, int]:
        tables = (
            "torrents",
            "selected_torrents",
            "duplicate_torrents",
            "errored_torrents",
            "events",
            "lists",
            "list_items",
        )
        with connect(self.path) as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }

    def delete_selected(self, mam_id: int) -> None:
        with connect(self.path) as connection:
            connection.execute(
                "DELETE FROM selected_torrents WHERE mam_id = ?", (mam_id,)
            )

    def delete_error(self, id_json: Any) -> None:
        with connect(self.path) as connection:
            connection.execute(
                "DELETE FROM errored_torrents WHERE id_json = ?",
                (canonical_json(id_json),),
            )

    def add_metadata_torrent(self, meta: dict[str, Any], linker: str | None) -> str:
        torrent_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        row = {
            "id": torrent_id,
            "id_is_hash": False,
            "mam_id": meta["mam_id"],
            "abs_id": None,
            "goodreads_id": None,
            "library_path": None,
            "library_files": [],
            "linker": linker,
            "category": None,
            "selected_audio_format": None,
            "selected_ebook_format": None,
            "title_search": normalize_title(meta["title"]),
            "meta": meta,
            "created_at": now,
            "replaced_with": None,
            "request_matadata_update": False,
            "library_mismatch": None,
            "client_status": None,
        }
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO torrents
                   (id, mam_id, title_search, created_at_json, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["mam_id"],
                    row["title_search"],
                    canonical_json(now),
                    canonical_json(row),
                ),
            )
        return torrent_id

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
        with connect(self.path) as connection, connection:
            connection.execute(
                """UPDATE selected_torrents
                       SET hash = ?, payload_json = ? WHERE mam_id = ?""",
                (torrent_hash, canonical_json(selected), selected["mam_id"]),
            )
            connection.execute(
                """INSERT INTO torrents
                       (id, mam_id, title_search, created_at_json, payload_json)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         payload_json=excluded.payload_json""",
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
