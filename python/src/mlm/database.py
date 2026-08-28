from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 5

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE migration_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE torrents (
    id TEXT PRIMARY KEY,
    mam_id INTEGER NOT NULL UNIQUE,
    title_search TEXT NOT NULL,
    created_at_json TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE selected_torrents (
    mam_id INTEGER PRIMARY KEY,
    hash TEXT UNIQUE,
    title_search TEXT NOT NULL,
    created_at_json TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE duplicate_torrents (
    mam_id INTEGER PRIMARY KEY,
    title_search TEXT NOT NULL,
    created_at_json TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE errored_torrents (
    id_json TEXT PRIMARY KEY,
    created_at_json TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE events (
    id_json TEXT PRIMARY KEY,
    torrent_id TEXT,
    mam_id INTEGER,
    created_at_json TEXT,
    payload_json TEXT NOT NULL
);

CREATE INDEX events_torrent_id_idx ON events(torrent_id);
CREATE INDEX events_mam_id_idx ON events(mam_id);

CREATE TABLE lists (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE list_items (
    guid_json TEXT PRIMARY KEY,
    list_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at_json TEXT,
    payload_json TEXT NOT NULL
);

CREATE INDEX list_items_list_id_idx ON list_items(list_id);

CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    context_json TEXT NOT NULL
);

CREATE INDEX activity_log_created_at_idx ON activity_log(created_at DESC);

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    mam_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX requests_status_created_idx ON requests(status, created_at DESC);
CREATE INDEX requests_mam_id_idx ON requests(mam_id);

CREATE TABLE abs_books (
    abs_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_search TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    series_json TEXT NOT NULL,
    library_path TEXT,
    asin TEXT,
    isbn TEXT,
    payload_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE INDEX abs_books_title_search_idx ON abs_books(title_search);
CREATE INDEX abs_books_asin_idx ON abs_books(asin);

CREATE TABLE mam_spender_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE mam_spender_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX mam_spender_history_created_idx
    ON mam_spender_history(created_at DESC);

CREATE TABLE mam_spender_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    category TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX mam_spender_events_created_idx
    ON mam_spender_events(created_at DESC);
"""

DATA_TABLES = (
    "config",
    "torrents",
    "selected_torrents",
    "duplicate_torrents",
    "errored_torrents",
    "events",
    "lists",
    "list_items",
)


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def ensure_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        if (
            not path.exists()
            or not connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='migration_meta'"
            ).fetchone()
        ):
            initialize(connection)
            connection.executemany(
                "INSERT INTO migration_meta(key, value) VALUES (?, ?)",
                [("schema_version", str(SCHEMA_VERSION)), ("created_fresh", "true")],
            )
        else:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    component TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS activity_log_created_at_idx
                    ON activity_log(created_at DESC);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    mam_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS requests_status_created_idx
                    ON requests(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS requests_mam_id_idx
                    ON requests(mam_id);
                CREATE TABLE IF NOT EXISTS abs_books (
                    abs_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    title_search TEXT NOT NULL,
                    authors_json TEXT NOT NULL,
                    series_json TEXT NOT NULL,
                    library_path TEXT,
                    asin TEXT,
                    isbn TEXT,
                    payload_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS abs_books_title_search_idx
                    ON abs_books(title_search);
                CREATE INDEX IF NOT EXISTS abs_books_asin_idx
                    ON abs_books(asin);
                CREATE TABLE IF NOT EXISTS mam_spender_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mam_spender_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mam_spender_history_created_idx
                    ON mam_spender_history(created_at DESC);
                CREATE TABLE IF NOT EXISTS mam_spender_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mam_spender_events_created_idx
                    ON mam_spender_events(created_at DESC);
                """
            )
            connection.execute(
                """INSERT INTO migration_meta(key, value)
                   VALUES ('schema_version', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (str(SCHEMA_VERSION),),
            )
        connection.commit()
    finally:
        connection.close()
