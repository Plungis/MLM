from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from mlm.config import Config
from mlm.database import ensure_database
from mlm.library import reconcile_library
from mlm.modules.heavymlm.catalog import get_unified_book_catalog
from mlm.repository import Repository
from mlm.web import create_app


def add_test_torrent(
    repo: Repository,
    torrent_id: int,
    title: str,
    author: str,
    filetype: str,
    *,
    mam_id: int | None = None,
    series_name: str | None = None,
    series_vol: str | None = None,
    target_path: str | None = None,
    deleted: bool = False,
) -> dict[str, Any]:
    series = [{"name": series_name, "entries": [series_vol]}] if series_name else []
    record = {
        "id": torrent_id,
        "mam_id": mam_id or torrent_id,
        "title": title,
        "title_search": title.lower(),
        "authors": [author],
        "filetypes": [filetype],
        "series": series,
        "library_path": target_path,
        "category": "audiobook" if filetype.lower() in {"m4b", "mp3"} else "ebook",
        "created_at": "2026-11-03T00:00:00Z",
    }
    if deleted:
        record["deleted_from_disk_at"] = "2026-11-03T00:00:00Z"
    repo.record_linked(record, selected_mam_id=None)
    return record


def test_unified_book_catalog_aggregation(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    ensure_database(db_file)
    repo = Repository(db_file)
    config = Config(mam_id="test")

    add_test_torrent(
        repo,
        1,
        "The Way of Kings",
        "Brandon Sanderson",
        "m4b",
        mam_id=1,
    )
    add_test_torrent(
        repo,
        2,
        "The Way of Kings",
        "Brandon Sanderson",
        "epub",
        mam_id=2,
    )
    add_test_torrent(
        repo,
        3,
        "Words of Radiance",
        "Brandon Sanderson",
        "m4b",
        mam_id=3,
    )
    add_test_torrent(
        repo,
        4,
        "Oathbringer",
        "Brandon Sanderson",
        "epub",
        mam_id=4,
    )

    catalog = get_unified_book_catalog(
        repo,
        config,
        filter_format="all",
        search_query="",
    )

    assert len(catalog) == 3

    # Book 1: Way of Kings has both
    wok = next(b for b in catalog if b["title"] == "The Way of Kings")
    assert wok["audio"]["present"] is True
    assert wok["ebook"]["present"] is True
    assert wok["format_status"] == "both"

    # Book 2: Words of Radiance
    wor = next(b for b in catalog if b["title"] == "Words of Radiance")
    assert wor["audio"]["present"] is True
    assert wor["ebook"]["present"] is False
    assert wor["format_status"] == "audiobook"

    # Book 3: Oathbringer
    oath = next(b for b in catalog if b["title"] == "Oathbringer")
    assert oath["audio"]["present"] is False
    assert oath["ebook"]["present"] is True
    assert oath["format_status"] == "ebook"


def test_unified_book_catalog_filters(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    ensure_database(db_file)
    repo = Repository(db_file)
    config = Config(mam_id="test")

    add_test_torrent(repo, 1, "The Way of Kings", "Brandon Sanderson", "m4b", mam_id=1)
    add_test_torrent(repo, 2, "The Way of Kings", "Brandon Sanderson", "epub", mam_id=2)
    add_test_torrent(repo, 3, "Words of Radiance", "Brandon Sanderson", "m4b", mam_id=3)
    add_test_torrent(repo, 4, "Oathbringer", "Brandon Sanderson", "epub", mam_id=4)

    both_books = get_unified_book_catalog(repo, config, filter_format="both")
    assert len(both_books) == 1
    assert both_books[0]["title"] == "The Way of Kings"

    audio_books = get_unified_book_catalog(repo, config, filter_format="audiobook")
    assert len(audio_books) == 2  # Way of Kings, Words of Radiance

    ebooks = get_unified_book_catalog(repo, config, filter_format="ebook")
    assert len(ebooks) == 2  # Way of Kings, Oathbringer

    missing_audio = get_unified_book_catalog(
        repo, config, filter_format="missing_audio"
    )
    assert len(missing_audio) == 1
    assert missing_audio[0]["title"] == "Oathbringer"

    missing_ebook = get_unified_book_catalog(
        repo, config, filter_format="missing_ebook"
    )
    assert len(missing_ebook) == 1
    assert missing_ebook[0]["title"] == "Words of Radiance"


def test_unified_book_catalog_query_search(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    ensure_database(db_file)
    repo = Repository(db_file)
    config = Config(mam_id="test")

    add_test_torrent(
        repo,
        1,
        "Dune",
        "Frank Herbert",
        "m4b",
        series_name="Dune Chronicles",
        series_vol="1",
    )
    add_test_torrent(
        repo,
        2,
        "Dune Messiah",
        "Frank Herbert",
        "m4b",
        series_name="Dune Chronicles",
        series_vol="2",
    )
    add_test_torrent(
        repo,
        3,
        "Neuromancer",
        "William Gibson",
        "epub",
    )

    res = get_unified_book_catalog(repo, config, search_query="Chronicles")
    assert len(res) == 2

    res = get_unified_book_catalog(repo, config, search_query="Gibson")
    assert len(res) == 1
    assert res[0]["title"] == "Neuromancer"


def test_reconcile_library_pruning_and_restoring(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    ensure_database(db_file)
    repo = Repository(db_file)

    # Create dummy folders on disk
    book1_dir = tmp_path / "Brandon Sanderson" / "The Way of Kings"
    book1_dir.mkdir(parents=True)
    (book1_dir / "audio.m4b").write_text("audio content")

    book2_dir = tmp_path / "Brandon Sanderson" / "Words of Radiance"
    book2_dir.mkdir(parents=True)
    (book2_dir / "audio.m4b").write_text("audio content")

    add_test_torrent(
        repo,
        101,
        "The Way of Kings",
        "Brandon Sanderson",
        "m4b",
        target_path=str(book1_dir),
    )
    add_test_torrent(
        repo,
        102,
        "Words of Radiance",
        "Brandon Sanderson",
        "m4b",
        target_path=str(book2_dir),
    )

    # Initial check: both present in library
    assert len(repo.library_torrents()) == 2
    assert (
        repo.is_book_present(
            101, {"title": "The Way of Kings", "authors": ["Brandon Sanderson"]}
        )
        is True
    )

    # Delete book2 from disk
    (book2_dir / "audio.m4b").unlink()
    book2_dir.rmdir()

    config = Config(mam_id="test")
    summary = reconcile_library(config, repo)
    assert summary["pruned"] == 1
    assert len(repo.library_torrents()) == 1

    # Book 2 should no longer be in active library
    active = repo.library_torrents()
    assert len(active) == 1
    assert active[0]["title"] == "The Way of Kings"
    assert (
        repo.is_book_present(
            102, {"title": "Words of Radiance", "authors": ["Brandon Sanderson"]}
        )
        is False
    )

    # Now restore book2 on disk and run reconcile again
    book2_dir.mkdir(parents=True)
    (book2_dir / "audio.m4b").write_text("audio content")

    summary2 = reconcile_library(config, repo)
    assert summary2["restored"] == 1
    assert len(repo.library_torrents()) == 2

    active2 = repo.library_torrents()
    assert len(active2) == 2
    assert (
        repo.is_book_present(
            102, {"title": "Words of Radiance", "authors": ["Brandon Sanderson"]}
        )
        is True
    )


def test_library_web_catalog_and_reconcile(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('mam_id = "test"\n', encoding="utf-8")
    database_file = tmp_path / "data.sqlite3"
    ensure_database(database_file)
    repo = Repository(database_file)

    book_dir = tmp_path / "Test Author" / "Test Title"
    book_dir.mkdir(parents=True)
    (book_dir / "test.m4b").write_text("audio content")

    add_test_torrent(
        repo,
        201,
        "Test Title",
        "Test Author",
        "m4b",
        target_path=str(book_dir),
    )

    client = TestClient(create_app(config_file, database_file))

    # Test GET /library?view=books
    res = client.get("/library?view=books")
    assert res.status_code == 200
    assert "Unified Books Catalog" in res.text
    assert "Test Title" in res.text

    # Delete book_dir from disk
    (book_dir / "test.m4b").unlink()
    book_dir.rmdir()

    # Test POST /library/reconcile
    post_res = client.post("/library/reconcile", follow_redirects=True)
    assert post_res.status_code == 200
    assert "Library verified against filesystem" in post_res.text
    assert "Pruned" in post_res.text

    # After reconcile, book is pruned from active library
    res2 = client.get("/library?view=books")
    assert res2.status_code == 200
    assert "Test Title" not in res2.text
