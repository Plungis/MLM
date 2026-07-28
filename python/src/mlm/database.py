from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

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
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
