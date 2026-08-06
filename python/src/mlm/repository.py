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

    def has_pending_mam_id(self, mam_id: int) -> bool:
        with connect(self.path) as connection:
            row = connection.execute(
                """SELECT 1 FROM selected_torrents
                   WHERE mam_id = ?
                     AND json_extract(payload_json, '$.started_at') IS NULL
                     AND json_extract(payload_json, '$.removed_at') IS NULL""",
                (mam_id,),
            ).fetchone()
            return row is not None

    def selected_pipeline_status(self) -> dict[str, int]:
        with connect(self.path) as connection:
            row = connection.execute(
                """SELECT
                     SUM(CASE
                       WHEN json_extract(payload_json, '$.started_at') IS NULL
                        AND json_extract(payload_json, '$.removed_at') IS NULL
                       THEN 1 ELSE 0 END),
                     SUM(CASE
                       WHEN json_extract(payload_json, '$.started_at') IS NOT NULL
                        AND json_extract(payload_json, '$.removed_at') IS NULL
                       THEN 1 ELSE 0 END),
                     SUM(CASE
                       WHEN json_extract(payload_json, '$.started_at') IS NOT NULL
                        AND json_extract(payload_json, '$.removed_at') IS NULL
                       THEN COALESCE(
                         CAST(json_extract(payload_json, '$.meta.size') AS INTEGER), 0
                       ) ELSE 0 END)
                   FROM selected_torrents"""
            ).fetchone()
        return {
            "awaiting": int(row[0] or 0),
            "downloading": int(row[1] or 0),
            "downloading_bytes": int(row[2] or 0),
        }

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

    def log_activity(
        self,
        component: str,
        message: str,
        *,
        level: str = "info",
        context: dict[str, Any] | None = None,
    ) -> None:
        with connect(self.path) as connection, connection:
            connection.execute(
                """INSERT INTO activity_log
                   (created_at, level, component, message, context_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    datetime.now(UTC).isoformat(),
                    level,
                    component,
                    message,
                    canonical_json(context or {}),
                ),
            )
            connection.execute(
                """DELETE FROM activity_log WHERE id NOT IN (
                     SELECT id FROM activity_log ORDER BY id DESC LIMIT 2000
                   )"""
            )

    def recent_activity(
        self, *, limit: int = 200, component: str | None = None
    ) -> list[dict[str, Any]]:
        query = """SELECT id, created_at, level, component, message, context_json
                   FROM activity_log"""
        parameters: tuple[Any, ...]
        if component:
            query += " WHERE component = ?"
            parameters = (component, limit)
        else:
            parameters = (limit,)
        query += " ORDER BY id DESC LIMIT ?"
        with connect(self.path) as connection:
            rows = connection.execute(query, parameters)
            return [
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "level": row["level"],
                    "component": row["component"],
                    "message": row["message"],
                    "context": json.loads(row["context_json"]),
                }
                for row in rows
            ]

    def create_request(
        self,
        *,
        mam_id: int,
        release: dict[str, Any],
        requester_name: str = "",
        requester_contact: str = "",
        note: str = "",
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        row = {
            "id": str(uuid4()),
            "mam_id": mam_id,
            "status": "pending",
            "requester_name": requester_name.strip(),
            "requester_contact": requester_contact.strip(),
            "note": note.strip(),
            "source": source or {},
            "release": release,
            "created_at": now,
            "updated_at": now,
            "decision_note": "",
        }
        with connect(self.path) as connection:
            connection.execute(
                """INSERT INTO requests
                   (id, mam_id, status, created_at, updated_at, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["mam_id"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    canonical_json(row),
                ),
            )
        return row

    def request_record(self, request_id: str) -> dict[str, Any] | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def request_rows(
        self,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM requests"
        parameters: tuple[Any, ...]
        if status:
            query += " WHERE status = ?"
            parameters = (status, limit)
        else:
            parameters = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with connect(self.path) as connection:
            rows = connection.execute(query, parameters)
            return [json.loads(row[0]) for row in rows]

    def update_request(
        self,
        request_id: str,
        status: str,
        *,
        decision_note: str = "",
    ) -> dict[str, Any] | None:
        with connect(self.path) as connection:
            stored = connection.execute(
                "SELECT payload_json FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
            if not stored:
                return None
            row = json.loads(stored[0])
            row.update(
                status=status,
                decision_note=decision_note.strip(),
                updated_at=datetime.now(UTC).isoformat(),
            )
            connection.execute(
                """UPDATE requests
                   SET status = ?, updated_at = ?, payload_json = ?
                   WHERE id = ?""",
                (
                    row["status"],
                    row["updated_at"],
                    canonical_json(row),
                    request_id,
                ),
            )
            return row

    def request_counts(self) -> dict[str, int]:
        with connect(self.path) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM requests GROUP BY status"
            )
            counts = {str(row[0]): int(row[1]) for row in rows}
            counts["total"] = sum(counts.values())
            return counts

    def has_mam_id(self, mam_id: int) -> bool:
        with connect(self.path) as connection:
            selected = connection.execute(
                "SELECT 1 FROM selected_torrents WHERE mam_id = ?", (mam_id,)
            ).fetchone()
            library = connection.execute(
                "SELECT 1 FROM torrents WHERE mam_id = ?", (mam_id,)
            ).fetchone()
            return selected is not None or library is not None

    def has_goodreads_id(self, goodreads_id: int) -> bool:
        with connect(self.path) as connection:
            for table in ("selected_torrents", "torrents"):
                if connection.execute(
                    f"""SELECT 1 FROM {table}
                        WHERE CAST(json_extract(payload_json, '$.goodreads_id')
                                   AS INTEGER) = ?
                        LIMIT 1""",
                    (goodreads_id,),
                ).fetchone():
                    return True
        return False

    def goodreads_formats(self, goodreads_id: int) -> set[str]:
        formats: set[str] = set()
        with connect(self.path) as connection:
            for table in ("selected_torrents", "torrents"):
                rows = connection.execute(
                    f"""SELECT json_extract(payload_json, '$.meta.media_type')
                        FROM {table}
                        WHERE CAST(json_extract(payload_json, '$.goodreads_id')
                                   AS INTEGER) = ?""",
                    (goodreads_id,),
                )
                for row in rows:
                    media_type = str(row[0] or "")
                    if media_type in {"audiobook", "periodical_audiobook"}:
                        formats.add("audio")
                    elif media_type in {
                        "ebook",
                        "manga",
                        "comic_book",
                        "periodical_ebook",
                    }:
                        formats.add("ebook")
        return formats

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
                "DELETE FROM torrents WHERE mam_id = ? AND id <> ?",
                (torrent["mam_id"], torrent["id"]),
            )
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
            connection.execute(
                "DELETE FROM errored_torrents WHERE id_json = ?",
                (canonical_json({"Organizer": torrent["id"]}),),
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

    def list_items_for_list(self, list_id: str) -> list[dict[str, Any]]:
        with connect(self.path) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM list_items WHERE list_id = ?",
                (list_id,),
            )
            return [json.loads(row[0]) for row in rows]

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

    def list_tracking_counts(self) -> dict[str, int]:
        with connect(self.path) as connection:
            rows = connection.execute(
                """SELECT COALESCE(json_extract(payload_json, '$.status'), 'legacy'),
                          COUNT(*)
                   FROM list_items GROUP BY 1"""
            )
            return {str(row[0]): int(row[1]) for row in rows}

    def table_rows(
        self, table: str, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
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
                f"SELECT payload_json FROM {table} ORDER BY {order} LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [json.loads(row[0]) for row in rows]

    def ui_snapshot(self) -> dict[str, Any]:
        tables = (
            "torrents",
            "selected_torrents",
            "duplicate_torrents",
            "errored_torrents",
            "events",
            "lists",
            "list_items",
            "requests",
        )
        with connect(self.path) as connection:
            counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in tables
            }
            pipeline_row = connection.execute(
                """SELECT
                     SUM(CASE
                       WHEN json_extract(payload_json, '$.started_at') IS NULL
                        AND json_extract(payload_json, '$.removed_at') IS NULL
                       THEN 1 ELSE 0 END),
                     SUM(CASE
                       WHEN json_extract(payload_json, '$.started_at') IS NOT NULL
                        AND json_extract(payload_json, '$.removed_at') IS NULL
                       THEN 1 ELSE 0 END),
                     SUM(CASE
                       WHEN json_extract(payload_json, '$.started_at') IS NOT NULL
                        AND json_extract(payload_json, '$.removed_at') IS NULL
                       THEN COALESCE(
                         CAST(json_extract(payload_json, '$.meta.size') AS INTEGER), 0
                       ) ELSE 0 END)
                   FROM selected_torrents"""
            ).fetchone()
            tracking_rows = connection.execute(
                """SELECT COALESCE(json_extract(payload_json, '$.status'), 'legacy'),
                          COUNT(*)
                   FROM list_items GROUP BY 1"""
            )
            tracking = {str(row[0]): int(row[1]) for row in tracking_rows}
        return {
            "counts": counts,
            "pipeline": {
                "awaiting": int(pipeline_row[0] or 0),
                "downloading": int(pipeline_row[1] or 0),
                "downloading_bytes": int(pipeline_row[2] or 0),
            },
            "list_tracking": tracking,
            "request_tracking": self.request_counts(),
        }

    def counts(self) -> dict[str, int]:
        return self.ui_snapshot()["counts"]

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
            connection.execute(
                "DELETE FROM errored_torrents WHERE id_json = ?",
                (canonical_json({"Grabber": selected["mam_id"]}),),
            )

    def record_grab_error(
        self,
        selected: dict[str, Any],
        error: Exception,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        identifier = {"Grabber": selected["mam_id"]}
        row = {
            "id": identifier,
            "title": selected.get("meta", {}).get("title", selected["title_search"]),
            "error": str(error),
            "meta": selected.get("meta"),
            "context": context or {},
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

    def record_organizer_error(
        self,
        torrent_hash: str,
        title: str,
        error: str,
        context: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        identifier = {"Organizer": torrent_hash}
        row = {
            "id": identifier,
            "title": title,
            "error": error,
            "context": context,
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
