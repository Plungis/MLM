from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mlm.collection import detect_collection_books


def parent_meta(title: str = "Complete Collection") -> dict:
    return {
        "mam_id": 88,
        "title": title,
        "authors": ["An Author"],
        "narrators": ["A Narrator"],
        "series": [],
        "cat": {"name": "Audiobook"},
        "media_type": "audiobook",
    }


def detect(files: list[dict], root: Path, *, title: str = "Complete Collection"):
    return detect_collection_books(
        files,
        download_root=root,
        parent_meta=parent_meta(title),
        audio_types=("m4b", "mp3"),
        ebook_types=("epub", "pdf"),
    )


def test_detects_book_folders_and_keeps_chapters_together(tmp_path: Path) -> None:
    files = [
        {"name": "Author Collection/Book One/01.mp3"},
        {"name": "Author Collection/Book One/02.mp3"},
        {"name": "Author Collection/Book Two/01.mp3"},
        {"name": "Author Collection/Book Two/02.mp3"},
    ]

    books = detect(files, tmp_path)

    assert [book.title for book in books] == ["Book One", "Book Two"]
    assert books[0].detection == "folders"
    assert [relative for _, relative in books[0].files] == [
        Path("01.mp3"),
        Path("02.mp3"),
    ]
    assert books[0].meta["authors"] == ["An Author"]


def test_detects_flat_single_file_books(tmp_path: Path) -> None:
    files = [
        {"name": "Collection/01 - Book One.m4b"},
        {"name": "Collection/02 - Book Two.m4b"},
        {"name": "Collection/Book One.epub"},
        {"name": "Collection/Book Two.epub"},
    ]

    books = detect(files, tmp_path)

    assert [book.title for book in books] == ["Book One", "Book Two"]
    assert books[0].detection == "flat files"
    assert {path.suffix for path in (relative for _, relative in books[0].files)} == {
        ".m4b",
        ".epub",
    }


def test_does_not_split_disc_or_track_layouts(tmp_path: Path) -> None:
    discs = [
        {"name": "Novel/Disc 1/01.mp3"},
        {"name": "Novel/Disc 2/02.mp3"},
    ]
    parts = [
        {"name": "Novel Part 1.m4b"},
        {"name": "Novel Part 2.m4b"},
    ]
    chapters = [
        {"name": "Novel/Chapter 1/01.mp3"},
        {"name": "Novel/Chapter 2/02.mp3"},
    ]

    assert detect(discs, tmp_path, title="A Novel") == []
    assert detect(parts, tmp_path, title="A Novel") == []
    assert detect(chapters, tmp_path, title="A Novel") == []


def test_rejects_unsafe_collection_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe torrent path"):
        detect(
            [{"name": "../escape.m4b"}, {"name": "Book Two.m4b"}],
            tmp_path,
        )


def test_reads_individual_epub_title_and_author(tmp_path: Path) -> None:
    first = tmp_path / "Collection" / "Unhelpful One.epub"
    second = tmp_path / "Collection" / "Unhelpful Two.epub"
    for path, title, author in (
        (first, "Embedded First", "First Writer"),
        (second, "Embedded Second", "Second Writer"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                  <rootfiles><rootfile full-path="content.opf"/></rootfiles>
                </container>""",
            )
            archive.writestr(
                "content.opf",
                f"""<package xmlns:dc="http://purl.org/dc/elements/1.1/">
                  <metadata><dc:title>{title}</dc:title>
                  <dc:creator>{author}</dc:creator></metadata>
                </package>""",
            )

    books = detect(
        [
            {"name": "Collection/Unhelpful One.epub"},
            {"name": "Collection/Unhelpful Two.epub"},
        ],
        tmp_path,
    )

    assert [book.title for book in books] == ["Embedded First", "Embedded Second"]
    assert [book.meta["authors"] for book in books] == [
        ["First Writer"],
        ["Second Writer"],
    ]
