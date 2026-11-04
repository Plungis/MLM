from __future__ import annotations

import asyncio
from pathlib import Path

from mlm.cleaner import clean_superseded
from mlm.config import Config
from mlm.database import ensure_database
from mlm.repository import Repository


def test_clean_superseded_preserves_series_books_with_same_title_search(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)

    lib_dir1 = tmp_path / "lib" / "Author" / "Series #1 - Title"
    lib_dir1.mkdir(parents=True)
    file1 = lib_dir1 / "book1.m4b"
    file1.write_bytes(b"audio 1")

    lib_dir2 = tmp_path / "lib" / "Author" / "Series #2 - Title"
    lib_dir2.mkdir(parents=True)
    file2 = lib_dir2 / "book2.m4b"
    file2.write_bytes(b"audio 2")

    book1 = {
        "id": "hash1",
        "id_is_hash": True,
        "mam_id": 101,
        "title_search": "my eldritch horror",
        "created_at": "2026-08-01T00:00:00Z",
        "library_path": str(lib_dir1),
        "library_files": ["book1.m4b"],
        "meta": {
            "title": "My Eldritch Horror",
            "media_type": "Audiobook",
            "language": "English",
            "authors": ["Author"],
            "narrators": ["Narrator"],
            "series": [{"name": "My Eldritch Horror", "entries": ["1"]}],
            "filetypes": ["m4b"],
        },
    }

    book2 = {
        "id": "hash2",
        "id_is_hash": True,
        "mam_id": 102,
        "title_search": "my eldritch horror",
        "created_at": "2026-08-01T00:00:00Z",
        "library_path": str(lib_dir2),
        "library_files": ["book2.m4b"],
        "meta": {
            "title": "My Eldritch Horror",
            "media_type": "Audiobook",
            "language": "English",
            "authors": ["Author"],
            "narrators": ["Narrator"],
            "series": [{"name": "My Eldritch Horror", "entries": ["2"]}],
            "filetypes": ["m4b"],
        },
    }

    repository.record_linked(book1, 101)
    repository.record_linked(book2, 102)

    config = Config(
        mam_id="cookie",
        audio_types=("m4b", "mp3"),
    )

    cleaned = asyncio.run(clean_superseded(config, repository, []))
    assert cleaned == 0
    assert file1.exists()
    assert file2.exists()
    t1 = repository.torrent("hash1")
    t2 = repository.torrent("hash2")
    assert t1 is not None and t1["library_path"] == str(lib_dir1)
    assert t2 is not None and t2["library_path"] == str(lib_dir2)


def test_clean_superseded_cleans_only_true_duplicate_formats(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    ensure_database(database)
    repository = Repository(database)

    lib_dir_mp3 = tmp_path / "lib" / "Author" / "Series #1 - Title mp3"
    lib_dir_mp3.mkdir(parents=True)
    file_mp3 = lib_dir_mp3 / "book1.mp3"
    file_mp3.write_bytes(b"audio mp3")

    lib_dir_m4b = tmp_path / "lib" / "Author" / "Series #1 - Title"
    lib_dir_m4b.mkdir(parents=True)
    file_m4b = lib_dir_m4b / "book1.m4b"
    file_m4b.write_bytes(b"audio m4b")

    book1_mp3 = {
        "id": "hash_mp3",
        "id_is_hash": True,
        "mam_id": 101,
        "title_search": "my eldritch horror",
        "created_at": "2026-08-01T00:00:00Z",
        "library_path": str(lib_dir_mp3),
        "library_files": ["book1.mp3"],
        "meta": {
            "title": "My Eldritch Horror",
            "media_type": "Audiobook",
            "language": "English",
            "authors": ["Author"],
            "narrators": ["Narrator"],
            "series": [{"name": "My Eldritch Horror", "entries": ["1"]}],
            "filetypes": ["mp3"],
        },
    }

    book1_m4b = {
        "id": "hash_m4b",
        "id_is_hash": True,
        "mam_id": 102,
        "title_search": "my eldritch horror",
        "created_at": "2026-08-02T00:00:00Z",
        "library_path": str(lib_dir_m4b),
        "library_files": ["book1.m4b"],
        "meta": {
            "title": "My Eldritch Horror",
            "media_type": "Audiobook",
            "language": "English",
            "authors": ["Author"],
            "narrators": ["Narrator"],
            "series": [{"name": "My Eldritch Horror", "entries": ["1"]}],
            "filetypes": ["m4b"],
        },
    }

    repository.record_linked(book1_mp3, 101)
    repository.record_linked(book1_m4b, 102)

    config = Config(
        mam_id="cookie",
        audio_types=("m4b", "mp3"),
    )

    cleaned = asyncio.run(clean_superseded(config, repository, []))
    assert cleaned == 1
    assert file_m4b.exists()
    assert not file_mp3.exists()
    t_mp3 = repository.torrent("hash_mp3")
    t_m4b = repository.torrent("hash_m4b")
    assert t_mp3 is not None and t_mp3["library_path"] is None
    assert t_m4b is not None and t_m4b["library_path"] == str(lib_dir_m4b)
