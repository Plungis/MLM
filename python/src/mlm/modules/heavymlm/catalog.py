from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ...config import Config
from ...repository import Repository
from ...search import normalize_title


@dataclass
class BookFormatInfo:
    present: bool = False
    media_type: str = ""
    filetypes: list[str] = field(default_factory=list)
    size: str = ""
    library_path: str = ""
    hardlinked: bool | None = None
    mam_id: int | None = None
    abs_id: str | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnifiedBook:
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    series_name: str = ""
    series_volume: str = ""
    narrators: list[str] = field(default_factory=list)
    published_date: str = ""
    cover_url: str = ""
    audio: BookFormatInfo = field(
        default_factory=lambda: BookFormatInfo(media_type="audiobook")
    )
    ebook: BookFormatInfo = field(
        default_factory=lambda: BookFormatInfo(media_type="ebook")
    )

    @property
    def format_status(self) -> str:
        if self.audio.present and self.ebook.present:
            return "both"
        if self.audio.present:
            return "audiobook"
        if self.ebook.present:
            return "ebook"
        return "none"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["format_status"] = self.format_status
        return data


def normalize_author_name(author: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", author.casefold())
    return " ".join(cleaned.split())


def is_record_on_disk(record: dict[str, Any]) -> bool:
    """Check if a library record is active and not marked as deleted from disk."""
    return not bool(record.get("deleted_from_disk_at"))


def get_unified_book_catalog(
    repository: Repository,
    config: Config | None = None,
    filter_format: str = "all",
    search_query: str = "",
) -> list[dict[str, Any]]:
    """Group all active library items into books with audio/ebook format status."""
    processed_torrents = repository.table_rows("torrents", limit=20000, offset=0)
    abs_books = repository.abs_book_rows(limit=50000, offset=0)

    audio_types = set(config.audio_types if config else ("m4b", "mp3", "m4a"))
    ebook_types = set(
        config.ebook_types if config else ("epub", "pdf", "azw3", "mobi", "cbz")
    )

    books_map: dict[str, UnifiedBook] = {}

    # 1. Process HeavyMLM torrents
    for row in processed_torrents:
        if not is_record_on_disk(row):
            continue

        meta = row.get("meta") or {}
        title = meta.get("title") or row.get("title") or "Untitled"
        authors = meta.get("authors") or row.get("authors") or []
        series_list = meta.get("series") or row.get("series") or []
        series_name = ""
        series_vol = ""
        if series_list:
            s0 = series_list[0]
            series_name = str(s0.get("name", "")).strip()
            entries = s0.get("entries") or (
                [s0["sequence"]] if s0.get("sequence") else []
            )
            series_vol = str(entries[0]).strip() if entries else ""

        author_str = authors[0] if authors else ""
        canonical_key = f"{normalize_title(title)}::{normalize_author_name(author_str)}"

        if canonical_key not in books_map:
            books_map[canonical_key] = UnifiedBook(
                id=canonical_key,
                title=title,
                authors=list(authors),
                series_name=series_name,
                series_volume=series_vol,
                narrators=meta.get("narrators") or [],
                published_date=meta.get("published_date") or "",
            )

        book = books_map[canonical_key]
        if not book.series_name and series_name:
            book.series_name = series_name
            book.series_volume = series_vol

        # Detect format from filetypes, category, or selected format
        selected_audio = row.get("selected_audio_format")
        selected_ebook = row.get("selected_ebook_format")
        filetypes = [str(ft).casefold() for ft in row.get("filetypes") or []]
        cat = str(row.get("category") or meta.get("catname") or "").casefold()

        has_audio = bool(
            selected_audio
            or any(ft in audio_types for ft in filetypes)
            or "audio" in cat
        )
        has_ebook = bool(
            selected_ebook
            or any(ft in ebook_types for ft in filetypes)
            or "ebook" in cat
            or "comic" in cat
        )

        lib_path = str(row.get("library_path") or "")
        mam_id = row.get("mam_id")
        size = str(row.get("size") or meta.get("size") or "")

        if has_audio:
            book.audio.present = True
            book.audio.media_type = "audiobook"
            book.audio.library_path = lib_path
            book.audio.mam_id = mam_id
            book.audio.filetypes = list(set(book.audio.filetypes + filetypes))
            if size:
                book.audio.size = size
            book.audio.source = "mlm"

        if has_ebook:
            book.ebook.present = True
            book.ebook.media_type = "ebook"
            book.ebook.library_path = lib_path
            book.ebook.mam_id = mam_id
            book.ebook.filetypes = list(set(book.ebook.filetypes + filetypes))
            if size:
                book.ebook.size = size
            book.ebook.source = "mlm"

    # 2. Process Audiobookshelf books
    for abs_row in abs_books:
        abs_path = abs_row.get("library_path") or abs_row.get("path")

        title = abs_row.get("title") or "Untitled"
        authors = abs_row.get("authors") or []
        series_list = abs_row.get("series") or []
        series_name = ""
        series_vol = ""
        if series_list:
            s0 = series_list[0]
            series_name = str(s0.get("name", "")).strip()
            entries = s0.get("entries") or (
                [s0["sequence"]] if s0.get("sequence") else []
            )
            series_vol = str(entries[0]).strip() if entries else ""

        author_str = authors[0] if authors else ""
        canonical_key = f"{normalize_title(title)}::{normalize_author_name(author_str)}"

        if canonical_key not in books_map:
            books_map[canonical_key] = UnifiedBook(
                id=canonical_key,
                title=title,
                authors=list(authors),
                series_name=series_name,
                series_volume=series_vol,
                narrators=abs_row.get("narrators") or [],
                published_date=abs_row.get("published_date") or "",
            )

        book = books_map[canonical_key]
        if not book.audio.present:
            book.audio.present = True
            book.audio.media_type = "audiobook"
            book.audio.library_path = str(abs_path or "")
            book.audio.abs_id = str(abs_row.get("abs_id") or abs_row.get("id"))
            book.audio.source = "audiobookshelf"

    all_books = [b.to_dict() for b in books_map.values()]

    # Apply search filter
    if search_query and search_query.strip():
        q = search_query.strip().casefold()
        all_books = [
            b
            for b in all_books
            if q in b["title"].casefold()
            or any(q in a.casefold() for a in b["authors"])
            or q in b["series_name"].casefold()
        ]

    def matches_format(b: dict[str, Any]) -> bool:
        if filter_format == "all":
            return True
        if filter_format == "both":
            return b["format_status"] == "both"
        if filter_format == "audiobook":
            return bool(b["audio"]["present"])
        if filter_format == "ebook":
            return bool(b["ebook"]["present"])
        if filter_format == "missing_audio":
            return bool(b["ebook"]["present"] and not b["audio"]["present"])
        if filter_format == "missing_ebook":
            return bool(b["audio"]["present"] and not b["ebook"]["present"])
        return True

    filtered_books = [b for b in all_books if matches_format(b)]

    # Sort: series name + volume if in series, else title
    filtered_books.sort(
        key=lambda b: (
            b["series_name"].casefold() if b["series_name"] else b["title"].casefold(),
            b["series_volume"] or "",
            b["title"].casefold(),
        )
    )

    return filtered_books
