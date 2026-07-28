from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import DATA_TABLES, SCHEMA_VERSION, connect, initialize

EXPORT_FORMAT = "mlm-native-db-export"
EXPORT_VERSION = 1


class MigrationError(RuntimeError):
    """Raised when a migration cannot be proven complete and consistent."""


@dataclass(frozen=True)
class MigrationResult:
    destination: Path
    source_backup: Path
    counts: dict[str, int]
    export_sha256: str


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _timestamped_backup_path(source: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = source.with_name(f"{source.name}.{stamp}.bak")
    suffix = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.{stamp}.{suffix}.bak")
        suffix += 1
    return candidate


def back_up_source(source: Path) -> Path:
    source = source.resolve()
    if not source.is_file():
        raise MigrationError(f"source database does not exist: {source}")
    backup = _timestamped_backup_path(source)
    shutil.copy2(source, backup)
    return backup


def export_legacy_database(executable: Path, database_backup: Path, output: Path) -> None:
    executable = executable.resolve()
    if not executable.is_file():
        raise MigrationError(f"legacy executable does not exist: {executable}")

    with tempfile.TemporaryDirectory(prefix="mlm-export-config-") as temp_dir:
        config_path = Path(temp_dir) / "config.toml"
        config_path.write_text('mam_id = ""\n', encoding="utf-8")
        environment = os.environ.copy()
        environment["MLM_DB_FILE"] = str(database_backup)
        environment["MLM_CONFIG_FILE"] = str(config_path)
        completed = subprocess.run(
            [str(executable), "--export-db", str(output)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no error output"
        raise MigrationError(f"legacy export failed ({completed.returncode}): {detail}")
    if not output.is_file():
        raise MigrationError("legacy exporter reported success but produced no JSON file")


def _load_export(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise MigrationError(f"invalid export JSON: {error}") from error
    if not isinstance(document, dict):
        raise MigrationError("export root must be a JSON object")
    if document.get("format") != EXPORT_FORMAT:
        raise MigrationError(f"unsupported export format: {document.get('format')!r}")
    if document.get("version") != EXPORT_VERSION:
        raise MigrationError(f"unsupported export version: {document.get('version')!r}")

    for table in DATA_TABLES:
        if not isinstance(document.get(table), list):
            raise MigrationError(f"export field {table!r} must be an array")
    declared = document.get("counts")
    actual = {table: len(document[table]) for table in DATA_TABLES}
    if declared != actual:
        raise MigrationError(f"export count mismatch: declared={declared!r}, actual={actual!r}")
    return document, digest


def _json_field(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    return None if value is None else canonical_json(value)


def _insert_records(connection: sqlite3.Connection, export: dict[str, Any]) -> None:
    for row in export["config"]:
        connection.execute(
            "INSERT INTO config(key, value, payload_json) VALUES (?, ?, ?)",
            (row["key"], row["value"], canonical_json(row)),
        )
    for row in export["torrents"]:
        connection.execute(
            """INSERT INTO torrents
               (id, mam_id, title_search, created_at_json, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["mam_id"],
                row["title_search"],
                _json_field(row, "created_at"),
                canonical_json(row),
            ),
        )
    for row in export["selected_torrents"]:
        connection.execute(
            """INSERT INTO selected_torrents
               (mam_id, hash, title_search, created_at_json, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                row["mam_id"],
                row.get("hash"),
                row["title_search"],
                _json_field(row, "created_at"),
                canonical_json(row),
            ),
        )
    for row in export["duplicate_torrents"]:
        connection.execute(
            """INSERT INTO duplicate_torrents
               (mam_id, title_search, created_at_json, payload_json)
               VALUES (?, ?, ?, ?)""",
            (
                row["mam_id"],
                row["title_search"],
                _json_field(row, "created_at"),
                canonical_json(row),
            ),
        )
    for row in export["errored_torrents"]:
        connection.execute(
            """INSERT INTO errored_torrents
               (id_json, created_at_json, payload_json) VALUES (?, ?, ?)""",
            (
                canonical_json(row["id"]),
                _json_field(row, "created_at"),
                canonical_json(row),
            ),
        )
    for row in export["events"]:
        connection.execute(
            """INSERT INTO events
               (id_json, torrent_id, mam_id, created_at_json, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                canonical_json(row["id"]),
                row.get("torrent_id"),
                row.get("mam_id"),
                _json_field(row, "created_at"),
                canonical_json(row),
            ),
        )
    for row in export["lists"]:
        connection.execute(
            "INSERT INTO lists(id, title, payload_json) VALUES (?, ?, ?)",
            (row["id"], row["title"], canonical_json(row)),
        )
    for row in export["list_items"]:
        connection.execute(
            """INSERT INTO list_items
               (guid_json, list_id, title, created_at_json, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                canonical_json(row["guid"]),
                row["list_id"],
                row["title"],
                _json_field(row, "created_at"),
                canonical_json(row),
            ),
        )


def _validate(connection: sqlite3.Connection, expected: dict[str, int]) -> None:
    actual = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in DATA_TABLES
    }
    if actual != expected:
        raise MigrationError(f"SQLite count mismatch: expected={expected!r}, actual={actual!r}")
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise MigrationError(f"SQLite integrity check failed: {result}")


def migrate(
    source_database: Path,
    destination: Path,
    *,
    export_json: Path | None = None,
    legacy_executable: Path | None = None,
) -> MigrationResult:
    source_database = source_database.resolve()
    destination = destination.resolve()
    if source_database == destination:
        raise MigrationError("source and destination must be different files")
    if destination.exists():
        raise MigrationError(f"destination already exists: {destination}")
    if (export_json is None) == (legacy_executable is None):
        raise MigrationError("provide exactly one of export_json or legacy_executable")

    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = back_up_source(source_database)

    with tempfile.TemporaryDirectory(prefix="mlm-migration-", dir=destination.parent) as temp_dir:
        temp_dir_path = Path(temp_dir)
        if legacy_executable is not None:
            selected_export = temp_dir_path / "legacy-export.json"
            export_legacy_database(legacy_executable, backup, selected_export)
        else:
            assert export_json is not None
            selected_export = export_json.resolve()
            if not selected_export.is_file():
                raise MigrationError(f"export JSON does not exist: {selected_export}")

        export, digest = _load_export(selected_export)
        expected = {table: len(export[table]) for table in DATA_TABLES}
        temporary_database = temp_dir_path / "data.sqlite3"
        connection = connect(temporary_database)
        try:
            initialize(connection)
            with connection:
                _insert_records(connection, export)
                metadata = {
                    "schema_version": str(SCHEMA_VERSION),
                    "migrated_at": datetime.now(UTC).isoformat(),
                    "source_database": str(source_database),
                    "source_backup": str(backup),
                    "export_sha256": digest,
                    "source_counts": canonical_json(expected),
                }
                connection.executemany(
                    "INSERT INTO migration_meta(key, value) VALUES (?, ?)",
                    metadata.items(),
                )
            _validate(connection, expected)
        except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
            raise MigrationError(f"could not import export: {error}") from error
        finally:
            connection.close()
        os.replace(temporary_database, destination)

    return MigrationResult(destination, backup, expected, digest)
