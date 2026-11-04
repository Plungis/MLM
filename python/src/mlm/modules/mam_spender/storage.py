from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ...database import connect
from ...migration import canonical_json
from ...repository import Repository


class MamSpenderStore:
    """Persistent module state kept separate from HeavyMLM records."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def value(self, key: str, default: Any = None) -> Any:
        with connect(self.repository.path) as connection:
            row = connection.execute(
                "SELECT value_json FROM mam_spender_state WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def set_value(self, key: str, value: Any) -> None:
        with connect(self.repository.path) as connection:
            connection.execute(
                """INSERT INTO mam_spender_state(key, value_json) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (key, canonical_json(value)),
            )

    def add_history(self, payload: dict[str, Any]) -> None:
        created_at = str(payload.get("created_at") or datetime.now(UTC).isoformat())
        row = {"created_at": created_at, **payload}
        with connect(self.repository.path) as connection:
            connection.execute(
                """INSERT INTO mam_spender_history(created_at, payload_json)
                   VALUES (?, ?)""",
                (created_at, canonical_json(row)),
            )
            connection.execute(
                """DELETE FROM mam_spender_history WHERE id NOT IN (
                     SELECT id FROM mam_spender_history ORDER BY id DESC LIMIT 300
                   )"""
            )

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        with connect(self.repository.path) as connection:
            rows = connection.execute(
                """SELECT payload_json FROM mam_spender_history
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
            return [json.loads(row[0]) for row in rows]

    def add_event(self, payload: dict[str, Any]) -> None:
        created_at = str(payload.get("created_at") or datetime.now(UTC).isoformat())
        category = str(payload.get("category") or "other")
        row = {"created_at": created_at, **payload}
        with connect(self.repository.path) as connection:
            connection.execute(
                """INSERT INTO mam_spender_events
                   (created_at, category, payload_json) VALUES (?, ?, ?)""",
                (created_at, category, canonical_json(row)),
            )
            connection.execute(
                """DELETE FROM mam_spender_events WHERE id NOT IN (
                     SELECT id FROM mam_spender_events ORDER BY id DESC LIMIT 1000
                   )"""
            )

    def events(self, limit: int = 500) -> list[dict[str, Any]]:
        with connect(self.repository.path) as connection:
            rows = connection.execute(
                """SELECT payload_json FROM (
                     SELECT id, payload_json FROM mam_spender_events
                     ORDER BY id DESC LIMIT ?
                   ) ORDER BY id ASC""",
                (limit,),
            )
            return [json.loads(row[0]) for row in rows]

    def clear_totals(self) -> None:
        self.set_value("totals", {})
