from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mlm.migration import MigrationError, canonical_json, migrate


def sample_export() -> dict:
    collections = {
        "config": [{"key": "last-run", "value": "now"}],
        "torrents": [
            {
                "id": "abc",
                "mam_id": 101,
                "title_search": "a book",
                "created_at": [1700000000, 0],
                "meta": {"title": "A Book", "authors": ["Writer"]},
            }
        ],
        "selected_torrents": [
            {
                "mam_id": 102,
                "hash": None,
                "title_search": "queued",
                "created_at": [1700000001, 0],
                "dl_link": "https://example.invalid/download?tid=102",
            }
        ],
        "duplicate_torrents": [],
        "errored_torrents": [
            {
                "id": {"Grabber": 103},
                "created_at": [1700000002, 0],
                "title": "Failed",
                "error": "example",
                "meta": None,
            }
        ],
        "events": [
            {
                "id": "event-id",
                "torrent_id": "abc",
                "mam_id": 101,
                "created_at": [1700000003, 0],
                "event": {"RemovedFromMam": None},
            }
        ],
        "lists": [{"id": "list-1", "title": "Reading"}],
        "list_items": [
            {
                "guid": ["list-1", "item-1"],
                "list_id": "list-1",
                "title": "Wanted",
                "created_at": [1700000004, 0],
            }
        ],
    }
    return {
        "format": "mlm-native-db-export",
        "version": 1,
        "counts": {key: len(value) for key, value in collections.items()},
        **collections,
    }


def write_export(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_migration_preserves_payloads_and_counts(tmp_path: Path) -> None:
    source = tmp_path / "data.db"
    source.write_bytes(b"legacy database")
    export_path = tmp_path / "export.json"
    document = sample_export()
    write_export(export_path, document)
    destination = tmp_path / "data.sqlite3"

    result = migrate(source, destination, export_json=export_path)

    assert result.source_backup.read_bytes() == b"legacy database"
    assert result.counts == document["counts"]
    with sqlite3.connect(destination) as connection:
        payload = connection.execute(
            "SELECT payload_json FROM torrents WHERE id = 'abc'"
        ).fetchone()[0]
        assert payload == canonical_json(document["torrents"][0])
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_count_mismatch_does_not_create_destination(tmp_path: Path) -> None:
    source = tmp_path / "data.db"
    source.write_bytes(b"legacy database")
    export_path = tmp_path / "export.json"
    document = sample_export()
    document["counts"]["events"] = 99
    write_export(export_path, document)
    destination = tmp_path / "data.sqlite3"

    with pytest.raises(MigrationError, match="export count mismatch"):
        migrate(source, destination, export_json=export_path)

    assert not destination.exists()


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "data.db"
    source.write_bytes(b"legacy database")
    destination = tmp_path / "data.sqlite3"
    destination.write_bytes(b"keep me")
    export_path = tmp_path / "export.json"
    write_export(export_path, sample_export())

    with pytest.raises(MigrationError, match="destination already exists"):
        migrate(source, destination, export_json=export_path)

    assert destination.read_bytes() == b"keep me"
