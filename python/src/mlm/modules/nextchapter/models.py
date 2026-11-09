from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MissingBook:
    series_name: str
    volume_number: str
    title: str = ""
    author: str = ""
    published_date: str = ""
    cover_url: str = ""
    summary: str = ""
    source: str = "gap_detector"  # gap_detector, google_books, gemini, openlibrary
    mam_candidates: list[dict[str, Any]] = field(default_factory=list)
    best_candidate: dict[str, Any] | None = None
    status: str = "missing"  # missing, queued, in_library, not_wanted, unable_to_find
    disposition_key: str = ""
    disposition_reason: str = ""
    disposition_notes: str = ""
    disposition_updated_at: int = 0
    last_checked_at: int = 0
    mam_available_count: int = 0
    queued_mam_id: int = 0
    queued_title: str = ""
    queued_at: int = 0
    torrent_hash: str = ""
    qbit_state: str = ""
    qbit_state_label: str = ""
    qbit_progress: float = 0.0
    qbit_file: str = ""
    qbit_save_path: str = ""
    qbit_dlspeed: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VolumeDisposition:
    disposition_key: str
    series_name: str
    volume_number: str
    title: str = ""
    author: str = ""
    cover_url: str = ""
    summary: str = ""
    status: str = "not_wanted"  # not_wanted, unable_to_find
    reason: str = ""
    notes: str = ""
    updated_at: int = 0
    last_checked_at: int = 0
    mam_available_count: int = 0
    best_candidate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SeriesHubEntry:
    series_name: str
    authors: list[str] = field(default_factory=list)
    owned_count: int = 0
    total_known_count: int = 0
    owned_volumes: list[dict[str, Any]] = field(default_factory=list)
    missing_volumes: list[MissingBook] = field(default_factory=list)
    dismissed_volumes: list[MissingBook] = field(default_factory=list)
    all_missing_volumes: list[MissingBook] = field(default_factory=list)
    percent_complete: int = 0
    status: str = "incomplete"  # complete, incomplete

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["missing_volumes"] = [m.to_dict() for m in self.missing_volumes]
        data["dismissed_volumes"] = [
            m.to_dict() for m in self.dismissed_volumes
        ]
        data["all_missing_volumes"] = [
            m.to_dict() for m in self.all_missing_volumes
        ]
        return data


@dataclass
class BookRecommendation:
    title: str
    author: str
    series_name: str = ""
    volume: str = ""
    reason: str = ""
    source: str = "author_catalogue"  # gemini_ai, author_catalogue, genre_match
    cover_url: str = ""
    summary: str = ""
    mam_candidates: list[dict[str, Any]] = field(default_factory=list)
    best_candidate: dict[str, Any] | None = None
    status: str = "recommended"  # recommended, queued, in_library

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NextChapterSettings:
    gemini_api_key: str = ""
    google_books_api_key: str = ""
    openlibrary_enabled: bool = True
    preferred_media: str = "both"  # audio, ebook, both
    auto_search_mam: bool = True
    min_series_books: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
