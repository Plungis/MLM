from __future__ import annotations

import asyncio
import contextlib
import html
import json
import re
import time
from typing import Any

from ...autograbber import select_row
from ...config import Config
from ...database import connect
from ...mam import MamClient
from ...qbittorrent import QbitClient
from ...repository import Repository
from ...search import (
    as_bool,
    as_int,
    decode_name_mapping,
    decode_series_info,
    normalize_title,
    parse_size,
    torrent_meta,
)
from ..heavymlm.search import (
    STOP_WORDS,
    _field_matches,
    _normalize_tokens,
    _volume_matches_row,
    clean_query_for_mam,
    extract_author_mam_terms,
    extract_title_mam_terms,
    parse_composite_query,
)
from ..heavymlm.series import (
    detect_library_series,
    extract_series_volume,
    fetch_series_releases,
    normalize_series_name,
    parse_entry_number,
    score_series_candidate,
)
from .clients import GeminiClient, GoogleBooksClient
from .models import (
    BookRecommendation,
    MissingBook,
    NextChapterSettings,
    SeriesHubEntry,
    VolumeDisposition,
)

STATE_SCHEMA_VERSION = 2

OMNIBUS_TITLE_RE = re.compile(
    r"\b(?:omnibus|box\s*set|anthology|collection|complete\s+series|"
    r"books?\s+\d+\s*(?:[\-–—]|to|through)\s*\d+|"
    r"vols?(?:umes?)?\s+\d+\s*(?:[\-–—]|to|through)\s*\d+)\b",
    re.IGNORECASE,
)
RANGE_VOL_RE = re.compile(
    r"(?:books?|vols?(?:umes?)?|#)?\s*(\d+)\s*(?:[\-–—]|to|through)\s*(\d+)\b",
    re.IGNORECASE,
)


def _normalize_volume_str(vol: str) -> str:
    """Normalize a single numeric volume identifier (e.g. '01' -> '1')."""
    if not vol or not str(vol).strip():
        return ""
    cleaned = str(vol).strip()
    cleaned = re.sub(
        r"^(?:book|vol(?:ume)?\.?|part|pt|#)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if not re.match(r"^\d+(?:\.\d+)?$", cleaned):
        return ""
    try:
        num = float(cleaned)
        if num <= 0 or num > 500:
            return ""
        if num.is_integer():
            return str(int(num))
        return str(num)
    except ValueError:
        return ""


def _expand_owned_volumes(vol_str: str, title_str: str) -> set[str]:
    """Extract owned volume numbers, expanding omnibus ranges like 'Books 1-3'."""
    owned: set[str] = set()
    single = _normalize_volume_str(vol_str)
    if single:
        owned.add(single)

    for text in (vol_str, title_str):
        if not text:
            continue
        for match in RANGE_VOL_RE.finditer(str(text)):
            try:
                start_v = int(match.group(1))
                end_v = int(match.group(2))
                if 1 <= start_v < end_v <= 100 and (end_v - start_v) <= 25:
                    for v in range(start_v, end_v + 1):
                        owned.add(str(v))
            except ValueError:
                pass
    return owned


def make_disposition_key(
    series_name: str, volume_number: str = "", title: str = ""
) -> str:
    """Create a deterministic normalized key for a series + volume/title."""
    s_norm = (
        normalize_series_name(series_name).strip().casefold()
        if series_name
        else ""
    )
    if not s_norm and series_name:
        s_norm = series_name.strip().casefold()
    v_norm = _normalize_volume_str(volume_number) or (
        str(volume_number).strip().casefold() if volume_number else ""
    )
    if v_norm:
        return f"{s_norm}::vol:{v_norm}"
    t_norm = normalize_title(title) if title else ""
    return f"{s_norm}::title:{t_norm}"


def _format_speed(bytes_per_sec: float) -> str:
    val = float(bytes_per_sec or 0)
    if val <= 0:
        return ""
    if val >= 1024 * 1024:
        return f"{val / (1024 * 1024):.1f} MB/s"
    if val >= 1024:
        return f"{val / 1024:.0f} KB/s"
    return f"{int(val)} B/s"


def clean_mam_book_title(
    raw_title: str,
    series_name: str = "",
    volume: str = "",
    author: str = "",
) -> tuple[str, bool]:
    """Extract clean canonical book title from a MAM torrent title.

    Returns (clean_title, is_distinct_title) where is_distinct_title is True
    when the book has a real standalone title beyond 'Series Name #N'.
    """
    if not raw_title or not raw_title.strip():
        return "", False
    t = html.unescape(raw_title).strip()

    # 1. Strip bracketed tags [Unabridged], [M4B], [2024], etc.
    t = re.sub(r"\[[^\]]*\]", " ", t)
    # Strip parenthetical format or book-number tags
    t = re.sub(
        r"\([^\)]*(?:unabridged|abridged|audiobooks?|ebooks?|retail|"
        r"book\s*#?[\d.]+|vol(?:ume)?\s*\.?\s*#?[\d.]+|#\s*[\d.]+|"
        r"m4b|mp3|epub|azw3|graphic\s*audio|dramatized)[^\)]*\)",
        " ",
        t,
        flags=re.IGNORECASE,
    )

    # 2. Strip leading/trailing author name if present
    if author and author.strip():
        auth_esc = re.escape(author.strip())
        t = re.sub(
            rf"^\s*{auth_esc}\s*[\-–—:]+\s*", "", t, flags=re.IGNORECASE
        )
        t = re.sub(
            rf"\s+by\s+{auth_esc}\s*$", "", t, flags=re.IGNORECASE
        )

    # 3. Strip series name + volume prefix/suffix if present
    clean_s = normalize_series_name(series_name) if series_name else ""
    s_variants = [
        s.strip() for s in dict.fromkeys([series_name, clean_s]) if s and s.strip()
    ]
    for s_cand in s_variants:
        s_esc = re.escape(s_cand)
        t = re.sub(rf"\(\s*{s_esc}[^\)]*\)", " ", t, flags=re.IGNORECASE)
        prefix_pat = (
            rf"^\s*(?:the\s+)?{s_esc}\s*(?:series\s*)?"
            rf"(?:[,:\-–—]?\s*(?:book|vol(?:ume)?\.?|part|pt|#)?\s*0*[\d.]+\s*[,:\-–—]+|[,:\-–—]+\s*)"
        )
        stripped = re.sub(prefix_pat, "", t, flags=re.IGNORECASE).strip()
        if stripped:
            t = stripped

        suffix_pat = (
            rf"\s*[,:\-–—]+\s*(?:the\s+)?{s_esc}\s*(?:series\s*)?"
            rf"(?:[,:\-–—]?\s*(?:book|vol(?:ume)?\.?|#)?\s*0*[\d.]+)?\s*$"
        )
        stripped_suf = re.sub(suffix_pat, "", t, flags=re.IGNORECASE).strip()
        if stripped_suf:
            t = stripped_suf

    # 4. Strip standalone volume prefixes/suffixes like "Book 3: Title", "03 - Title"
    t = re.sub(
        r"^\s*(?:book|vol(?:ume)?\.?|#)\s*0*[\d.]+\s*[,:\-–—]+\s*",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(r"^\s*0*[\d.]+\s*[\-–—:]+\s*", "", t).strip()
    t = re.sub(
        r"\s*[,:\-–—]+\s*(?:book|vol(?:ume)?\.?|#)\s*0*[\d.]+\s*$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()

    # 5. Strip generic subtitle noise like ": A Novel"
    t = re.sub(
        r":\s*a\s+(?:novel|thriller|mystery|memoir|litrpg[^\w]*|"
        r"progression\s+fantasy[^\w]*)$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()

    t = re.sub(r"\s+", " ", t).strip(" -–—:,")

    # Check if what remains is just the series name + number or just a volume marker
    vol_norm = _normalize_volume_str(volume)
    for s_cand in s_variants:
        s_esc = re.escape(s_cand)
        if re.match(
            rf"^(?:the\s+)?{s_esc}\s*(?:[,:\-–—]?\s*(?:book|vol(?:ume)?\.?|#)?\s*0*[\d.]+)?$",
            t,
            flags=re.IGNORECASE,
        ):
            if vol_norm == "1" and t.casefold() == s_cand.casefold():
                return s_cand, True
            fallback = (
                f"{clean_s or s_cand} #{vol_norm}"
                if vol_norm
                else (clean_s or s_cand)
            )
            return fallback, False

    if not t or re.match(
        r"^(?:book|vol(?:ume)?\.?|part|pt|#)?\s*0*[\d.]+$",
        t,
        flags=re.IGNORECASE,
    ):
        base_s = clean_s or series_name.strip()
        if base_s and vol_norm:
            return f"{base_s} #{vol_norm}", False
        return html.unescape(raw_title).strip(), False

    return t, True


def format_mam_series(series_list: list[dict[str, Any]]) -> str:
    """Format MAM series list into a clean display string (e.g. 'Series Name #4')."""
    parts: list[str] = []
    for s in series_list:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        entries = s.get("entries") or (
            [s["sequence"]] if s.get("sequence") else []
        )
        clean_entries = [
            str(e).strip() for e in entries if str(e).strip()
        ]
        if clean_entries:
            parts.append(f"{name} #{', '.join(clean_entries)}")
        else:
            parts.append(name)
    return "; ".join(parts)


class NextChapterService:
    def __init__(
        self,
        config: Config,
        repository: Repository,
        mam: MamClient | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.mam = mam
        self.settings = self._load_settings()
        self._state_cache: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    def reconfigure(self, config: Config) -> None:
        self.config = config
        self.settings = self._load_settings()

    def _load_settings(self) -> NextChapterSettings:
        raw = getattr(self.config, "nextchapter", {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        return NextChapterSettings(
            gemini_api_key=str(raw.get("gemini_api_key", "")).strip(),
            google_books_api_key=str(raw.get("google_books_api_key", "")).strip(),
            openlibrary_enabled=bool(raw.get("openlibrary_enabled", True)),
            preferred_media=str(raw.get("preferred_media", "both")),
            auto_search_mam=bool(raw.get("auto_search_mam", True)),
            min_series_books=int(raw.get("min_series_books", 1)),
        )

    def get_settings(self) -> dict[str, Any]:
        data = self.settings.to_dict()
        data["has_gemini"] = bool(self.settings.gemini_api_key)
        data["has_google_books"] = bool(self.settings.google_books_api_key)
        return data

    def _load_dispositions(self) -> dict[str, dict[str, Any]]:
        raw = self.repository.config_value("nextchapter_dispositions")
        if not raw:
            return {}
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                return {
                    str(k): v
                    for k, v in loaded.items()
                    if isinstance(v, dict)
                }
        except json.JSONDecodeError:
            pass
        return {}

    def _save_dispositions(self, dispositions: dict[str, dict[str, Any]]) -> None:
        self.repository.set_config_value(
            "nextchapter_dispositions", json.dumps(dispositions)
        )

    def _load_queued_volumes(self) -> dict[str, dict[str, Any]]:
        raw = self.repository.config_value("nextchapter_queued_volumes")
        if not raw:
            return {}
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                return {
                    str(k): v
                    for k, v in loaded.items()
                    if isinstance(v, dict)
                }
        except json.JSONDecodeError:
            pass
        return {}

    def _save_queued_volumes(self, queued: dict[str, dict[str, Any]]) -> None:
        self.repository.set_config_value(
            "nextchapter_queued_volumes", json.dumps(queued)
        )

    def _collect_repo_queue_snapshot(self) -> dict[int, dict[str, Any]]:
        """Read selected_torrents and torrents from SQLite to map mam_id -> status."""
        by_mam_id: dict[int, dict[str, Any]] = {}
        try:
            with connect(self.repository.path) as conn:
                sel_rows = conn.execute(
                    """SELECT mam_id, hash, payload_json
                       FROM selected_torrents
                       WHERE json_extract(payload_json, '$.removed_at') IS NULL"""
                ).fetchall()
                for row in sel_rows:
                    mid = int(row["mam_id"] or 0)
                    if not mid:
                        continue
                    payload = json.loads(row["payload_json"])
                    thash = str(
                        row["hash"] or payload.get("hash") or ""
                    ).strip()
                    started = bool(payload.get("started_at"))
                    check_status = str(
                        payload.get("last_check_status") or ""
                    ).strip()
                    check_reason = str(
                        payload.get("last_check_reason") or ""
                    ).strip()
                    meta = payload.get("meta") or {}
                    qbit_state = "downloading" if (started or thash) else "queued_mam"
                    qbit_label = (
                        "Sent to qBittorrent"
                        if (started or thash)
                        else "Queued for MAM Grab"
                    )
                    if check_status == "error":
                        qbit_state = "error"
                        qbit_label = f"Error: {check_reason or 'Grab failed'}"
                    elif check_status == "deferred":
                        qbit_state = "queued_mam"
                        qbit_label = f"Queued ({check_reason or 'Waiting for slot'})"
                    by_mam_id[mid] = {
                        "mam_id": mid,
                        "title": str(meta.get("title") or ""),
                        "torrent_hash": thash,
                        "qbit_state": qbit_state,
                        "qbit_state_label": qbit_label,
                        "qbit_progress": 0.0,
                        "qbit_file": "",
                        "qbit_save_path": "",
                        "qbit_dlspeed": "",
                    }

                tor_rows = conn.execute(
                    """SELECT id, mam_id, payload_json
                       FROM torrents
                       WHERE json_extract(payload_json, '$.deleted_from_disk_at')
                             IS NULL"""
                ).fetchall()
                for row in tor_rows:
                    mid = int(row["mam_id"] or 0)
                    if not mid:
                        continue
                    payload = json.loads(row["payload_json"])
                    thash = (
                        str(row["id"] or "")
                        if payload.get("id_is_hash")
                        else ""
                    )
                    lib_path = str(payload.get("library_path") or "").strip()
                    lib_files = payload.get("library_files") or []
                    meta = payload.get("meta") or {}
                    file_label = ""
                    if lib_files:
                        file_label = str(lib_files[0])
                        if len(lib_files) > 1:
                            file_label += f" (+{len(lib_files) - 1} files)"
                    elif lib_path:
                        file_label = lib_path.replace("\\", "/").split("/")[-1]
                    if lib_path:
                        by_mam_id[mid] = {
                            "mam_id": mid,
                            "title": str(meta.get("title") or ""),
                            "torrent_hash": thash,
                            "qbit_state": "linked",
                            "qbit_state_label": "Imported to Library",
                            "qbit_progress": 100.0,
                            "qbit_file": file_label,
                            "qbit_save_path": lib_path,
                            "qbit_dlspeed": "",
                        }
                    elif mid not in by_mam_id or thash:
                        prev = by_mam_id.get(mid, {})
                        by_mam_id[mid] = {
                            "mam_id": mid,
                            "title": str(
                                meta.get("title") or prev.get("title") or ""
                            ),
                            "torrent_hash": thash or prev.get("torrent_hash", ""),
                            "qbit_state": prev.get("qbit_state") or "downloading",
                            "qbit_state_label": (
                                prev.get("qbit_state_label")
                                or "Active in qBittorrent"
                            ),
                            "qbit_progress": float(prev.get("qbit_progress") or 0.0),
                            "qbit_file": file_label or prev.get("qbit_file", ""),
                            "qbit_save_path": prev.get("qbit_save_path", ""),
                            "qbit_dlspeed": prev.get("qbit_dlspeed", ""),
                        }
        except Exception:
            pass
        return by_mam_id

    def _match_disposition(
        self,
        dispositions: dict[str, dict[str, Any]],
        series_name: str,
        volume_number: str,
        title: str,
    ) -> dict[str, Any] | None:
        if not dispositions:
            return None
        primary_key = make_disposition_key(series_name, volume_number, title)
        if primary_key in dispositions:
            return dispositions[primary_key]
        if title:
            title_key = make_disposition_key(series_name, "", title)
            if title_key in dispositions:
                return dispositions[title_key]
        v_norm = _normalize_volume_str(volume_number)
        t_norm = normalize_title(title) if title else ""
        for disp in dispositions.values():
            d_series = str(disp.get("series_name") or "")
            if not d_series or not (
                _field_matches([d_series], series_name)
                or _field_matches([series_name], d_series)
            ):
                continue
            d_vol = _normalize_volume_str(str(disp.get("volume_number") or ""))
            if v_norm and d_vol and v_norm == d_vol:
                return disp
            d_title_norm = normalize_title(str(disp.get("title") or ""))
            if t_norm and d_title_norm and t_norm == d_title_norm:
                return disp
        return None

    def _match_queued_volume(
        self,
        queued_map: dict[str, dict[str, Any]],
        series_name: str,
        volume_number: str,
        title: str,
    ) -> dict[str, Any] | None:
        if not queued_map:
            return None
        primary_key = make_disposition_key(series_name, volume_number, title)
        if primary_key in queued_map:
            return queued_map[primary_key]
        v_norm = _normalize_volume_str(volume_number)
        t_norm = normalize_title(title) if title else ""
        for q_entry in queued_map.values():
            q_series = str(q_entry.get("series_name") or "")
            if not q_series or not (
                _field_matches([q_series], series_name)
                or _field_matches([series_name], q_series)
            ):
                continue
            q_vol = _normalize_volume_str(
                str(q_entry.get("volume_number") or "")
            )
            if v_norm and q_vol and v_norm == q_vol:
                return q_entry
            q_title_norm = normalize_title(str(q_entry.get("title") or ""))
            if t_norm and q_title_norm and t_norm == q_title_norm:
                return q_entry
        return None

    def _apply_dispositions_to_state(
        self, state: dict[str, Any]
    ) -> dict[str, Any]:
        dispositions = self._load_dispositions()
        queued_map = self._load_queued_volumes()
        repo_queue = self._collect_repo_queue_snapshot()

        matched_disp_keys: set[str] = set()
        not_wanted_volumes: list[dict[str, Any]] = []
        unable_to_find_volumes: list[dict[str, Any]] = []
        queued_volumes: list[dict[str, Any]] = []

        total_missing = 0
        complete_count = 0

        for s in state.get("series", []):
            s_name = str(s.get("series_name") or "")
            s_authors = s.get("authors") or []
            default_author = s_authors[0] if s_authors else ""

            raw_all = s.get("all_missing_volumes")
            if not raw_all:
                combined = list(s.get("missing_volumes") or []) + list(
                    s.get("dismissed_volumes") or []
                )
                seen_keys: set[str] = set()
                raw_all = []
                for item in combined:
                    ik = make_disposition_key(
                        s_name,
                        str(item.get("volume_number") or ""),
                        str(item.get("title") or ""),
                    )
                    if ik not in seen_keys:
                        seen_keys.add(ik)
                        raw_all.append(dict(item))
            else:
                raw_all = [dict(item) for item in raw_all]

            s["all_missing_volumes"] = raw_all

            active_missing: list[dict[str, Any]] = []
            series_dismissed: list[dict[str, Any]] = []

            for item in raw_all:
                vol_str = str(item.get("volume_number") or "")
                title_str = str(item.get("title") or "")
                item_author = str(item.get("author") or default_author)
                d_key = make_disposition_key(s_name, vol_str, title_str)
                item["disposition_key"] = d_key
                item["series_name"] = s_name
                if not item.get("author") and item_author:
                    item["author"] = item_author

                # Check if queued
                q_info = self._match_queued_volume(
                    queued_map, s_name, vol_str, title_str
                )
                q_mam_id = int(
                    (q_info or {}).get("mam_id")
                    or item.get("queued_mam_id")
                    or 0
                )
                repo_q = repo_queue.get(q_mam_id) if q_mam_id else None

                if q_info or repo_q:
                    item["queued_mam_id"] = q_mam_id
                    item["queued_title"] = str(
                        (repo_q or {}).get("title")
                        or (q_info or {}).get("queued_title")
                        or item.get("queued_title")
                        or title_str
                    )
                    item["queued_at"] = int(
                        (q_info or {}).get("queued_at")
                        or item.get("queued_at")
                        or 0
                    )
                    is_linked = (repo_q or {}).get("qbit_state") == "linked"
                    primary_q = repo_q if is_linked else (q_info or repo_q or {})
                    secondary_q = (q_info or {}) if is_linked else (repo_q or {})

                    item["torrent_hash"] = str(
                        primary_q.get("torrent_hash")
                        or secondary_q.get("torrent_hash")
                        or item.get("torrent_hash")
                        or ""
                    )
                    item["qbit_state"] = str(
                        primary_q.get("qbit_state")
                        or secondary_q.get("qbit_state")
                        or item.get("qbit_state")
                        or "queued_mam"
                    )
                    item["qbit_state_label"] = str(
                        primary_q.get("qbit_state_label")
                        or secondary_q.get("qbit_state_label")
                        or item.get("qbit_state_label")
                        or "Queued for Download"
                    )
                    item["qbit_progress"] = float(
                        primary_q.get("qbit_progress")
                        if primary_q.get("qbit_progress") is not None
                        else (
                            secondary_q.get("qbit_progress")
                            or item.get("qbit_progress")
                            or 0.0
                        )
                    )
                    item["qbit_file"] = str(
                        primary_q.get("qbit_file")
                        or secondary_q.get("qbit_file")
                        or item.get("qbit_file")
                        or ""
                    )
                    item["qbit_save_path"] = str(
                        primary_q.get("qbit_save_path")
                        or secondary_q.get("qbit_save_path")
                        or item.get("qbit_save_path")
                        or ""
                    )
                    item["qbit_dlspeed"] = str(
                        primary_q.get("qbit_dlspeed")
                        or secondary_q.get("qbit_dlspeed")
                        or item.get("qbit_dlspeed")
                        or ""
                    )

                disp = self._match_disposition(
                    dispositions, s_name, vol_str, title_str
                )
                if disp and disp.get("status") in {
                    "not_wanted",
                    "unable_to_find",
                }:
                    matched_disp_keys.add(
                        str(disp.get("disposition_key") or d_key)
                    )
                    d_status = str(disp["status"])
                    item["status"] = d_status
                    item["disposition_key"] = str(
                        disp.get("disposition_key") or d_key
                    )
                    item["disposition_reason"] = str(disp.get("reason") or "")
                    item["disposition_notes"] = str(disp.get("notes") or "")
                    item["disposition_updated_at"] = int(
                        disp.get("updated_at") or 0
                    )
                    item["last_checked_at"] = int(
                        disp.get("last_checked_at") or 0
                    )
                    item["mam_available_count"] = int(
                        disp.get("mam_available_count") or 0
                    )
                    if disp.get("best_candidate"):
                        item["best_candidate"] = disp["best_candidate"]

                    series_dismissed.append(item)
                    if d_status == "not_wanted":
                        not_wanted_volumes.append(item)
                    else:
                        unable_to_find_volumes.append(item)
                else:
                    if q_info or repo_q:
                        item["status"] = "queued"
                        queued_volumes.append(item)
                    else:
                        item["status"] = "missing"
                    item["disposition_reason"] = ""
                    item["disposition_notes"] = ""
                    item["disposition_updated_at"] = 0
                    active_missing.append(item)

            s["missing_volumes"] = active_missing
            s["dismissed_volumes"] = series_dismissed
            s["not_wanted_count"] = sum(
                1 for d in series_dismissed if d.get("status") == "not_wanted"
            )
            s["unable_to_find_count"] = sum(
                1
                for d in series_dismissed
                if d.get("status") == "unable_to_find"
            )
            s["queued_count"] = sum(
                1 for m in active_missing if m.get("status") == "queued"
            )

            owned_cnt = int(
                s.get("owned_count") or len(s.get("owned_volumes") or [])
            )
            raw_total = max(
                owned_cnt + len(raw_all),
                int(
                    s.get("raw_total_known_count")
                    or s.get("total_known_count")
                    or 0
                ),
            )
            s["raw_total_known_count"] = raw_total
            wanted_total = max(
                owned_cnt + len(active_missing) + s["unable_to_find_count"],
                raw_total - s["not_wanted_count"],
            )
            s["total_known_count"] = wanted_total
            s["percent_complete"] = (
                min(100, int((owned_cnt / wanted_total) * 100))
                if wanted_total
                else 100
            )

            unqueued_missing = [
                m for m in active_missing if m.get("status") != "queued"
            ]
            if s.get("status") != "unconfigured":
                if not unqueued_missing and s["unable_to_find_count"] == 0:
                    s["status"] = "complete"
                else:
                    s["status"] = "incomplete"

            if s.get("status") == "complete":
                complete_count += 1
            total_missing += len(unqueued_missing)

        # Include any standalone dispositions not yet in scanned series
        for d_key, disp in dispositions.items():
            if d_key in matched_disp_keys:
                continue
            d_status = str(disp.get("status") or "")
            if d_status not in {"not_wanted", "unable_to_find"}:
                continue
            standalone = {
                "series_name": str(disp.get("series_name") or ""),
                "volume_number": str(disp.get("volume_number") or ""),
                "title": str(disp.get("title") or ""),
                "author": str(disp.get("author") or ""),
                "cover_url": str(disp.get("cover_url") or ""),
                "summary": str(disp.get("summary") or ""),
                "status": d_status,
                "disposition_key": d_key,
                "disposition_reason": str(disp.get("reason") or ""),
                "disposition_notes": str(disp.get("notes") or ""),
                "disposition_updated_at": int(disp.get("updated_at") or 0),
                "last_checked_at": int(disp.get("last_checked_at") or 0),
                "mam_available_count": int(
                    disp.get("mam_available_count") or 0
                ),
                "best_candidate": disp.get("best_candidate"),
            }
            if d_status == "not_wanted":
                not_wanted_volumes.append(standalone)
            else:
                unable_to_find_volumes.append(standalone)

        unable_to_find_volumes.sort(
            key=lambda x: (
                0 if int(x.get("mam_available_count") or 0) > 0 else 1,
                -int(x.get("disposition_updated_at") or 0),
                str(x.get("series_name") or "").casefold(),
                parse_entry_number(str(x.get("volume_number") or "")),
            )
        )
        not_wanted_volumes.sort(
            key=lambda x: (
                -int(x.get("disposition_updated_at") or 0),
                str(x.get("series_name") or "").casefold(),
                parse_entry_number(str(x.get("volume_number") or "")),
            )
        )

        state["missing_count"] = total_missing
        state["complete_count"] = complete_count
        state["not_wanted_count"] = len(not_wanted_volumes)
        state["unable_to_find_count"] = len(unable_to_find_volumes)
        state["dismissed_count"] = len(not_wanted_volumes) + len(
            unable_to_find_volumes
        )
        state["queued_count"] = len(queued_volumes)
        state["not_wanted_volumes"] = not_wanted_volumes
        state["unable_to_find_volumes"] = unable_to_find_volumes
        state["dismissed_volumes"] = (
            unable_to_find_volumes + not_wanted_volumes
        )
        state["queued_volumes"] = queued_volumes
        return state

    def get_cached_state(self) -> dict[str, Any]:
        if (
            self._state_cache is not None
            and self._state_cache.get("schema_version") == STATE_SCHEMA_VERSION
        ):
            return self._apply_dispositions_to_state(self._state_cache)
        saved = self.repository.config_value("nextchapter_state")
        if saved:
            try:
                loaded = json.loads(saved)
                if (
                    isinstance(loaded, dict)
                    and loaded.get("schema_version") == STATE_SCHEMA_VERSION
                ):
                    self._state_cache = self._apply_dispositions_to_state(
                        loaded
                    )
                    return self._state_cache
            except json.JSONDecodeError:
                pass
        empty = {
            "schema_version": STATE_SCHEMA_VERSION,
            "series": [],
            "missing_count": 0,
            "complete_count": 0,
            "total_series": 0,
            "recommendations": [],
            "last_scan": 0,
        }
        return self._apply_dispositions_to_state(empty)

    async def _fetch_mam_series_catalog(
        self, series_entry: dict[str, Any]
    ) -> dict[str, Any]:
        """Query MyAnonamouse for ground-truth series volumes and book titles."""
        raw_series_name = str(series_entry.get("series_name") or "").strip()
        authors = [
            str(a).strip()
            for a in (series_entry.get("authors") or [])
            if str(a).strip()
        ]
        clean_series = (
            normalize_series_name(raw_series_name) or raw_series_name
        )
        if authors and clean_series:
            auth_esc = re.escape(authors[0])
            stripped_s = re.sub(
                rf"^\s*{auth_esc}\s*[\-–—:]+\s*",
                "",
                clean_series,
                flags=re.IGNORECASE,
            ).strip()
            if stripped_s:
                clean_series = stripped_s

        empty_result: dict[str, Any] = {
            "has_data": False,
            "volumes": {},
            "author_titles": set(),
            "canonical_series_name": clean_series,
            "authors": authors,
        }
        if not self.mam:
            return empty_result

        series_is_author = bool(
            authors
            and clean_series
            and (
                _field_matches(authors, clean_series)
                or _field_matches([clean_series], authors[0])
            )
        )

        rows_by_id: dict[int, dict[str, Any]] = {}

        async def _q_mam(text_val: str, srch_in: list[str]) -> None:
            q_str = text_val.strip()
            if not q_str:
                return
            try:
                res = await self.mam.search(
                    {
                        "dlLink": True,
                        "mediaInfo": True,
                        "isbn": True,
                        "perpage": 100,
                        "tor": {
                            "text": q_str,
                            "srchIn": srch_in,
                            "startNumber": 0,
                        },
                    }
                )
                if not isinstance(res, dict) or not isinstance(
                    res.get("data"), list
                ):
                    return
                for row in res["data"]:
                    if isinstance(row, dict):
                        tid = as_int(row.get("id"))
                        if tid and tid not in rows_by_id:
                            rows_by_id[tid] = row
            except Exception:
                pass

        if clean_series and not series_is_author:
            q_s = clean_query_for_mam(clean_series) or clean_series
            await _q_mam(q_s, ["series"])
            if len(rows_by_id) < 3:
                await _q_mam(q_s, ["title", "series"])

        if authors:
            a_terms = extract_author_mam_terms(authors[0])
            if a_terms and (len(rows_by_id) < 4 or series_is_author):
                await _q_mam(a_terms[0], ["author"])

        if not rows_by_id:
            return empty_result

        pref = self.settings.preferred_media
        pref_media = pref if pref in ("audiobook", "ebook") else None

        volumes: dict[str, dict[str, Any]] = {}
        author_titles: set[str] = set()
        discovered_authors: list[str] = list(authors)
        series_name_counts: dict[str, int] = {}

        for tid, r in rows_by_id.items():
            meta = torrent_meta(r) if "media_type" not in r else r
            r_authors = meta.get("authors") or r.get("authors") or []
            if not r_authors and "author_info" in r:
                r_authors = decode_name_mapping(r.get("author_info"))
            r_series = meta.get("series") or r.get("series") or []
            if not r_series and "series_info" in r:
                r_series = decode_series_info(r.get("series_info"))
            r_title = str(meta.get("title") or r.get("title") or "").strip()
            if not r_title:
                continue

            # If series has known authors, skip torrents by unrelated authors
            if authors and r_authors:
                if not any(
                    _field_matches(r_authors, a)
                    or _field_matches([a], ra)
                    for a in authors
                    for ra in r_authors
                ):
                    continue
            elif not discovered_authors and r_authors:
                discovered_authors = list(r_authors)

            primary_author = (
                r_authors[0]
                if r_authors
                else (discovered_authors[0] if discovered_authors else "")
            )
            cleaned_any_title, _ = clean_mam_book_title(
                r_title, clean_series, "", primary_author
            )
            if cleaned_any_title:
                norm_any = normalize_title(cleaned_any_title)
                if norm_any:
                    author_titles.add(norm_any)
            norm_raw = normalize_title(r_title)
            if norm_raw:
                author_titles.add(norm_raw)

            # Skip omnibus/box-set torrents when mapping single canonical volume numbers
            if OMNIBUS_TITLE_RE.search(r_title):
                continue

            matched_s_obj: dict[str, Any] | None = None
            for s_obj in r_series:
                if not isinstance(s_obj, dict):
                    continue
                s_name = str(s_obj.get("name") or "").strip()
                if not s_name:
                    continue
                if series_is_author or (
                    clean_series
                    and (
                        _field_matches([s_name], clean_series)
                        or _field_matches([clean_series], s_name)
                    )
                ):
                    matched_s_obj = s_obj
                    series_name_counts[s_name] = (
                        series_name_counts.get(s_name, 0) + 1
                    )
                    break

            if matched_s_obj is None:
                # Check if series name appears directly in title
                if clean_series and not series_is_author and _field_matches(
                    [r_title], clean_series
                ):
                    matched_s_obj = {"name": clean_series, "entries": []}
                else:
                    continue

            s_name_used = str(
                matched_s_obj.get("name") or clean_series
            ).strip()
            entries = matched_s_obj.get("entries") or (
                [matched_s_obj["sequence"]]
                if matched_s_obj.get("sequence")
                else []
            )
            clean_entries = [
                str(e).strip() for e in entries if str(e).strip()
            ]
            # Skip multi-entry omnibus tags like ["1", "2", "3"] or ["1-3"]
            if len(clean_entries) > 1:
                continue
            if clean_entries and RANGE_VOL_RE.search(clean_entries[0]):
                continue

            raw_vol = (
                clean_entries[0]
                if clean_entries
                else extract_series_volume(
                    {"series": r_series, "title": r_title}, s_name_used
                )
            )
            vol_num = _normalize_volume_str(raw_vol)
            if not vol_num:
                continue

            clean_t, is_distinct = clean_mam_book_title(
                r_title, s_name_used, vol_num, primary_author
            )
            if not clean_t:
                continue

            base_sc = score_series_candidate(
                r, self.config, preferred_media=pref_media
            )
            cand_score = (
                (10000 if is_distinct else 0)
                + (base_sc[0] * 1000)
                + (base_sc[1] * 10)
                + (base_sc[2] * 50)
                + min(base_sc[3], 200)
            )

            existing = volumes.get(vol_num)
            if existing is None or cand_score > existing["score"]:
                volumes[vol_num] = {
                    "volume_number": vol_num,
                    "title": clean_t,
                    "is_distinct_title": is_distinct,
                    "author": primary_author,
                    "mam_id": tid,
                    "seeders": as_int(r.get("seeders", 0)),
                    "score": cand_score,
                }

        best_series_name = clean_series
        if series_name_counts:
            best_series_name = max(
                series_name_counts.items(), key=lambda kv: kv[1]
            )[0]

        return {
            "has_data": True,
            "volumes": volumes,
            "author_titles": author_titles,
            "canonical_series_name": best_series_name,
            "authors": discovered_authors,
        }

    async def scan_series(self, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            cached = self.get_cached_state()
            now = time.time()
            if (
                not force
                and cached.get("schema_version") == STATE_SCHEMA_VERSION
                and cached.get("series")
                and (now - cached.get("last_scan", 0) < 300)
            ):
                return cached

            # 1. Detect series across library & ABS
            raw_series = await asyncio.to_thread(
                detect_library_series, self.repository
            )
            series_entries: list[SeriesHubEntry] = []
            total_missing = 0
            complete_count = 0

            # 2. Fetch Ground-Truth MAM Series Catalog for each series first
            sem = asyncio.Semaphore(4)

            async def _enrich_with_mam(
                s_item: dict[str, Any],
            ) -> dict[str, Any]:
                async with sem:
                    cat = await self._fetch_mam_series_catalog(s_item)
                    s_item["_mam_cat"] = cat
                    if not s_item.get("authors") and cat.get("authors"):
                        s_item["authors"] = cat["authors"]
                    s_item["mam_catalog"] = [
                        {
                            "volume_number": v_num,
                            "title": v_info["title"],
                            "author": v_info["author"],
                        }
                        for v_num, v_info in sorted(
                            cat["volumes"].items(),
                            key=lambda kv: parse_entry_number(kv[0]),
                        )
                    ]
                    return s_item

            if raw_series:
                await asyncio.gather(
                    *(_enrich_with_mam(s) for s in raw_series)
                )

            # Collect top authors for context
            all_authors: list[str] = []
            for s in raw_series:
                all_authors.extend(s.get("authors", []))
            top_authors = list(dict.fromkeys(all_authors))[:20]

            ai_series_map: dict[str, dict[str, Any]] = {}
            ai_recommendations: list[dict[str, Any]] = []
            has_gemini = bool(self.settings.gemini_api_key)

            last_error = ""
            if has_gemini and raw_series:
                gemini_client = GeminiClient(self.settings.gemini_api_key)
                try:
                    ai_result = await gemini_client.analyze_series(
                        raw_series, top_authors
                    )
                    last_error = ai_result.get("error", "")
                    for s_item in ai_result.get("series", []):
                        raw_k = s_item.get("series_name", "").strip()
                        if raw_k:
                            ai_series_map[raw_k.casefold()] = s_item
                            norm_k = normalize_series_name(raw_k).casefold()
                            if norm_k:
                                ai_series_map[norm_k] = s_item
                    raw_recs = ai_result.get("recommendations", [])
                    for rec in raw_recs:
                        ai_recommendations.append(
                            BookRecommendation(
                                title=rec.get("title", ""),
                                author=rec.get("author", ""),
                                series_name=rec.get("series_name", ""),
                                volume=rec.get("volume", ""),
                                reason=rec.get(
                                    "reason",
                                    "Recommended based on your library series",
                                ),
                                summary=rec.get("summary", ""),
                                source="gemini_ai",
                            ).to_dict()
                        )
                finally:
                    await gemini_client.close()

            for s in raw_series:
                series_name = s["series_name"]
                authors = s.get("authors", [])
                owned_books = s.get("books", [])
                mam_cat = s.get("_mam_cat") or {
                    "has_data": False,
                    "volumes": {},
                    "author_titles": set(),
                }
                mam_volumes: dict[str, dict[str, Any]] = mam_cat.get(
                    "volumes", {}
                )
                author_titles: set[str] = mam_cat.get(
                    "author_titles", set()
                )

                s_key = series_name.strip().casefold()
                norm_s_key = normalize_series_name(series_name).casefold()
                ai_data = (
                    ai_series_map.get(s_key)
                    or ai_series_map.get(norm_s_key)
                )

                # Reconcile owned books & volume numbers against MAM catalog
                owned_vol_set: set[str] = set()
                owned_title_norms: set[str] = set()

                for b in owned_books:
                    b_title = str(b.get("title") or "").strip()
                    b_vol = str(b.get("volume") or "").strip()
                    expanded = _expand_owned_volumes(b_vol, b_title)
                    owned_vol_set.update(expanded)

                    clean_b_title, _ = clean_mam_book_title(
                        b_title,
                        series_name,
                        "",
                        authors[0] if authors else "",
                    )
                    for t_cand in (b_title, clean_b_title):
                        nt = normalize_title(t_cand)
                        if nt:
                            owned_title_norms.add(nt)

                    norm_v = _normalize_volume_str(b_vol)
                    if norm_v:
                        b["volume"] = norm_v
                    else:
                        # Try to match unnumbered owned book title to MAM catalog volume
                        matched_v = ""
                        for v_num, v_info in mam_volumes.items():
                            if v_info.get("is_distinct_title") and (
                                _field_matches([b_title], v_info["title"])
                                or _field_matches(
                                    [v_info["title"]], clean_b_title or b_title
                                )
                            ):
                                matched_v = v_num
                                break
                        b["volume"] = matched_v
                        if matched_v:
                            owned_vol_set.add(matched_v)

                # Also mark any MAM volume as owned if its title or mam_id is in library
                for v_num, v_info in mam_volumes.items():
                    if v_num in owned_vol_set:
                        continue
                    v_title_norm = normalize_title(v_info["title"])
                    if (
                        v_info.get("is_distinct_title")
                        and v_title_norm
                        and v_title_norm in owned_title_norms
                    ):
                        owned_vol_set.add(v_num)
                        continue
                    for ob in owned_books:
                        ob_t = str(ob.get("title") or "")
                        if v_info.get("is_distinct_title") and _field_matches(
                            [ob_t], v_info["title"]
                        ):
                            owned_vol_set.add(v_num)
                            break

                # Index AI missing books by normalized volume number
                ai_missing_by_vol: dict[str, dict[str, Any]] = {}
                ai_missing_list = (
                    ai_data.get("missing_books", []) if ai_data else []
                )
                for m in ai_missing_list:
                    v_norm = _normalize_volume_str(
                        str(m.get("volume_number", ""))
                    )
                    if v_norm:
                        ai_missing_by_vol[v_norm] = m

                missing_by_vol: dict[str, MissingBook] = {}

                # A. Ground-Truth Missing Volumes from MyAnonamouse
                for v_num, v_info in mam_volumes.items():
                    if v_num in owned_vol_set:
                        continue
                    ai_m = ai_missing_by_vol.get(v_num, {})
                    # Ground-truth MAM title ALWAYS takes precedence over AI title
                    if v_info.get("is_distinct_title"):
                        verified_title = v_info["title"]
                    else:
                        verified_title = (
                            str(ai_m.get("title") or "").strip()
                            or v_info["title"]
                        )
                    verified_author = (
                        v_info.get("author")
                        or str(ai_m.get("author") or "").strip()
                        or (authors[0] if authors else "")
                    )
                    summary_str = str(ai_m.get("summary") or "").strip()
                    missing_by_vol[v_num] = MissingBook(
                        series_name=series_name,
                        volume_number=v_num,
                        title=verified_title,
                        author=verified_author,
                        summary=summary_str,
                        source="mam_verified",
                    )

                # B. Additional Missing Volumes from Gemini AI (with verification)
                for m in ai_missing_list:
                    raw_vol = str(m.get("volume_number", "")).strip()
                    vol_num = _normalize_volume_str(raw_vol) or raw_vol
                    if not vol_num or vol_num in owned_vol_set:
                        continue
                    if vol_num in missing_by_vol:
                        continue

                    m_title = str(m.get("title", "")).strip()
                    m_author = (
                        str(m.get("author", "")).strip()
                        or (authors[0] if authors else "")
                    )
                    m_summary = str(m.get("summary", "")).strip()
                    if not m_title:
                        continue

                    # Skip if user already owns a book with this title
                    m_norm = normalize_title(m_title)
                    if m_norm and m_norm in owned_title_norms:
                        continue
                    if any(
                        _field_matches([ob.get("title", "")], m_title)
                        for ob in owned_books
                    ):
                        continue

                    # Skip if m_title duplicates a volume already in mam_volumes
                    if any(
                        _field_matches([v_info["title"]], m_title)
                        or _field_matches([m_title], v_info["title"])
                        for v_info in mam_volumes.values()
                        if v_info.get("is_distinct_title")
                    ):
                        continue

                    # If MAM has real catalog data for this series/author, verify
                    # that Gemini's extra book title exists in the MAM catalog
                    if mam_cat.get("has_data") and (mam_volumes or author_titles):
                        in_author_catalog = bool(
                            m_norm
                            and any(
                                m_norm in at or at in m_norm
                                for at in author_titles
                            )
                        )
                        if not in_author_catalog:
                            continue

                    missing_by_vol[vol_num] = MissingBook(
                        series_name=series_name,
                        volume_number=vol_num,
                        title=m_title,
                        author=m_author,
                        summary=m_summary,
                        source="gemini_ai",
                    )

                missing_items = sorted(
                    missing_by_vol.values(),
                    key=lambda item: parse_entry_number(item.volume_number),
                )

                effective_owned_count = max(
                    len(owned_books), len(owned_vol_set)
                )
                if ai_data and not mam_volumes:
                    canonical_total = int(
                        ai_data.get("canonical_total_books") or 0
                    )
                elif mam_volumes:
                    canonical_total = len(
                        set(mam_volumes.keys())
                        | owned_vol_set
                        | set(missing_by_vol.keys())
                    )
                else:
                    canonical_total = effective_owned_count

                total_known = max(
                    effective_owned_count + len(missing_items), canonical_total
                )
                pct = (
                    min(100, int((effective_owned_count / total_known) * 100))
                    if total_known
                    else 100
                )
                if not has_gemini and not mam_volumes:
                    status = "unconfigured"
                elif missing_items:
                    status = "incomplete"
                else:
                    status = "complete"

                if status == "complete":
                    complete_count += 1
                total_missing += len(missing_items)

                series_entries.append(
                    SeriesHubEntry(
                        series_name=series_name,
                        authors=authors,
                        owned_count=len(owned_books),
                        total_known_count=total_known,
                        owned_volumes=owned_books,
                        missing_volumes=missing_items,
                        all_missing_volumes=missing_items,
                        percent_complete=pct,
                        status=status,
                    )
                )

            # Rank series: incomplete series with missing books first
            series_entries.sort(
                key=lambda x: (
                    0 if x.status == "incomplete" else 1,
                    x.series_name.casefold(),
                )
            )

            recs = ai_recommendations or cached.get("recommendations", [])
            state = {
                "schema_version": STATE_SCHEMA_VERSION,
                "series": [se.to_dict() for se in series_entries],
                "missing_count": total_missing,
                "complete_count": complete_count,
                "total_series": len(series_entries),
                "recommendations": recs,
                "gemini_configured": has_gemini,
                "last_error": last_error,
                "last_scan": int(now),
            }
            state = self._apply_dispositions_to_state(state)
            self._state_cache = state
            self.repository.set_config_value(
                "nextchapter_state", json.dumps(state)
            )
            return state

    async def search_mam_for_missing(
        self,
        series_name: str = "",
        volume: str = "",
        title: str | None = None,
        author: str | None = None,
        q: str | None = None,
        mode: str = "smart",
        media_filter: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        if not self.mam:
            return []

        search_mode = (mode or "smart").strip().casefold()
        if search_mode not in {"smart", "custom", "series", "author"}:
            search_mode = "smart"

        # Detect if user typed a custom query in the modal search box
        default_q1 = f"{author} {title}".strip() if (author and title) else ""
        default_q2 = (title or "").strip()
        default_q3 = f"{series_name} {volume}".strip()
        raw_q = (q or "").strip()

        if raw_q and (
            search_mode == "custom"
            or raw_q.casefold()
            not in {
                default_q1.casefold(),
                default_q2.casefold(),
                default_q3.casefold(),
            }
        ):
            parsed = parse_composite_query(raw_q)
            if parsed["volume"]:
                volume = parsed["volume"]
            if parsed["author_hint"] and parsed["title_hint"]:
                author = parsed["author_hint"]
                title = parsed["title_hint"]
            elif search_mode == "custom":
                # User typed a completely custom search string
                title = parsed["clean_q"] or raw_q
                if (
                    not parsed["author_hint"]
                    and author
                    and not _field_matches([raw_q], author)
                ):
                    author = ""

        # Check if series_name is actually the author's name (common in folder metadata)
        series_is_author = False
        if series_name and author:
            if _field_matches([author], series_name) or _field_matches(
                [series_name], author
            ):
                series_is_author = True
        elif series_name and not author and not title:
            series_is_author = False

        effective_author = (
            author or (series_name if series_is_author else "")
        ).strip()

        preferred_media = (
            None
            if self.settings.preferred_media == "both"
            else self.settings.preferred_media
        )
        if media_filter and media_filter.strip().casefold() in {
            "audiobook",
            "ebook",
        }:
            preferred_media = media_filter.strip().casefold()

        seen_ids: set[int] = set()
        releases: list[dict[str, Any]] = []

        async def _query_mam(
            text: str,
            srch_in: list[str] | None = None,
            max_pages: int = 1,
        ) -> None:
            if not text or not text.strip():
                return
            clean_text = re.sub(r"#\s*[\d.]+", " ", text)
            clean_text = " ".join(clean_text.split())
            if not clean_text:
                return
            fields = (
                srch_in
                if srch_in is not None
                else ["title", "author", "series", "narrator"]
            )
            start = 0
            for _ in range(max_pages):
                tor: dict[str, Any] = {
                    "text": clean_text,
                    "startNumber": start,
                }
                if fields:
                    tor["srchIn"] = fields
                try:
                    res = await self.mam.search(
                        {
                            "dlLink": True,
                            "mediaInfo": True,
                            "isbn": True,
                            "perpage": 100,
                            "tor": tor,
                        }
                    )
                    if hasattr(res, "__await__"):
                        res = await res
                    if not isinstance(res, dict):
                        break
                    rows = res.get("data", [])
                    if not rows or not isinstance(rows, list):
                        break
                    added = 0
                    for row in rows:
                        if isinstance(row, dict):
                            tid = as_int(row.get("id"))
                            if tid and tid not in seen_ids:
                                seen_ids.add(tid)
                                releases.append(row)
                                added += 1
                    start += len(rows)
                    found = as_int(res.get("found"), len(rows))
                    if added == 0 or start >= found:
                        break
                except Exception:
                    break

        title_terms = extract_title_mam_terms(title or "", series_name)
        author_terms = extract_author_mam_terms(effective_author)
        clean_series = (
            normalize_series_name(series_name) if series_name else ""
        )

        if search_mode == "author":
            target_a = effective_author or series_name or raw_q
            for a_term in extract_author_mam_terms(target_a):
                await _query_mam(a_term, srch_in=["author"], max_pages=2)
                if len(releases) >= 20:
                    break
            if not releases and target_a:
                await _query_mam(
                    clean_query_for_mam(target_a) or target_a,
                    srch_in=["author", "title"],
                    max_pages=1,
                )

        elif search_mode == "series":
            target_s = clean_series or clean_query_for_mam(raw_q) or title or ""
            if target_s and not series_is_author:
                await _query_mam(target_s, srch_in=["series"], max_pages=2)
                await _query_mam(
                    f'"{target_s}"', srch_in=["title", "series"], max_pages=1
                )
            if (series_is_author or not releases) and author_terms:
                for a_term in author_terms[:2]:
                    await _query_mam(a_term, srch_in=["author"], max_pages=2)
                    if releases:
                        break

        else:
            # Smart & Custom multi-strategy search
            # 1. Clean Title + Clean Author in ["title", "author"]
            if title_terms and author_terms:
                for t_term in title_terms[:2]:
                    for a_term in author_terms[:2]:
                        await _query_mam(
                            f'"{t_term}" {a_term}',
                            srch_in=["title", "author"],
                        )
                        if not releases:
                            await _query_mam(
                                f"{t_term} {a_term}",
                                srch_in=["title", "author", "series"],
                            )

            # 2. Clean Title in ["title"] and ["title", "series"]
            for t_term in title_terms[:2]:
                await _query_mam(f'"{t_term}"', srch_in=["title"])
                if len(releases) < 5:
                    await _query_mam(t_term, srch_in=["title", "series"])

            # 3. Freeform query q if provided
            if raw_q:
                cleaned_q = clean_query_for_mam(raw_q)
                if cleaned_q:
                    await _query_mam(
                        cleaned_q,
                        srch_in=["title", "author", "series", "narrator"],
                    )

            # 4. Series + Volume in ["series"] and ["title", "series"]
            if clean_series and not series_is_author:
                await _query_mam(clean_series, srch_in=["series"])
                if volume:
                    await _query_mam(
                        f"{clean_series} {volume}",
                        srch_in=["title", "series"],
                    )

            # 5. Author catalog in ["author"] for related books & alternate titles
            if author_terms:
                await _query_mam(author_terms[0], srch_in=["author"])
                if len(releases) < 3 and len(author_terms) > 1:
                    await _query_mam(author_terms[1], srch_in=["author"])

        # Fallback to fetch_series_releases if still empty
        if not releases:
            search_query = f"{series_name} {volume}".strip()
            if title:
                search_query = f"{search_query} {title}".strip()
            if search_query:
                try:
                    fallback_rows = await fetch_series_releases(
                        self.mam, search_query, max_pages=2
                    )
                    for r in fallback_rows:
                        if isinstance(r, dict):
                            tid = as_int(r.get("id"))
                            if tid and tid not in seen_ids:
                                seen_ids.add(tid)
                                releases.append(r)
                except Exception:
                    pass

        # Score and enrich candidates
        scored_candidates: list[dict[str, Any]] = []
        author_matched_found = False
        strict_media_filter = (
            media_filter.strip().casefold()
            if media_filter
            and media_filter.strip().casefold() in {"audiobook", "ebook"}
            else None
        )

        for r in releases:
            meta = (
                r if "media_type" in r and "authors" in r else torrent_meta(r)
            )
            cand_media = str(
                meta.get("media_type") or r.get("media_type", "audiobook")
            ).casefold()
            if strict_media_filter and strict_media_filter not in cand_media:
                continue

            base_score = score_series_candidate(
                r, self.config, preferred_media=preferred_media
            )

            authors = meta.get("authors") or r.get("authors") or []
            if not authors and "author_info" in r:
                authors = decode_name_mapping(r.get("author_info"))

            narrators = meta.get("narrators") or r.get("narrators") or []
            if not narrators and "narrator_info" in r:
                narrators = decode_name_mapping(r.get("narrator_info"))

            filetypes = meta.get("filetypes") or r.get("filetypes") or []
            if not filetypes and "filetype" in r:
                filetypes = [
                    part.lower()
                    for part in str(r.get("filetype", "")).split()
                    if part
                ]

            size_val = meta.get("size") or r.get("size", "0 B")
            if isinstance(size_val, (int, float)):
                size_str = parse_size(size_val)
            else:
                size_str = str(size_val)

            series_info = meta.get("series") or []
            if not series_info and "series_info" in r:
                series_info = decode_series_info(r.get("series_info"))
            series_display = format_mam_series(series_info)
            series_name_primary = (
                str(series_info[0].get("name") or "").strip()
                if series_info and isinstance(series_info[0], dict)
                else ""
            )
            cand_vol = extract_series_volume(
                {
                    "series": series_info,
                    "title": meta.get("title") or r.get("title", ""),
                },
                clean_series or series_name_primary,
            )

            match_score = 0.0
            match_reasons: list[str] = []
            has_author_penalty = False
            author_is_match = False

            if effective_author and authors:
                if _field_matches(authors, effective_author):
                    author_matched_found = True
                    author_is_match = True
                    match_score += 10000.0
                    match_reasons.append("✓ Author Match")
                else:
                    target_tokens = [
                        t
                        for t in _normalize_tokens(effective_author)
                        if len(t) >= 3
                    ]
                    authors_words = set(
                        _normalize_tokens(" ".join(authors))
                    )
                    if target_tokens and any(
                        t in authors_words for t in target_tokens
                    ):
                        author_matched_found = True
                        author_is_match = True
                        match_score += 5000.0
                        match_reasons.append("✓ Author Match")
                    else:
                        has_author_penalty = True
                        match_score -= 12000.0

            cand_title = str(meta.get("title") or r.get("title", ""))
            cand_title_norm = " ".join(_normalize_tokens(cand_title))
            cand_title_words = set(cand_title_norm.split())
            title_is_match = False

            if title_terms:
                for t_variant in title_terms:
                    t_norm = " ".join(_normalize_tokens(t_variant))
                    if not t_norm:
                        continue
                    if t_norm in cand_title_norm or (
                        len(cand_title_norm) >= 4 and cand_title_norm in t_norm
                    ):
                        title_is_match = True
                        match_score += 8000.0
                        match_reasons.append("✓ Exact Title")
                        break
                if not title_is_match:
                    sig_tokens = [
                        t
                        for t in _normalize_tokens(title_terms[0])
                        if t not in STOP_WORDS and len(t) >= 3
                    ]
                    if sig_tokens:
                        hits = sum(
                            1 for t in sig_tokens if t in cand_title_words
                        )
                        if hits == len(sig_tokens):
                            title_is_match = True
                            match_score += 6500.0
                            match_reasons.append("✓ Title Match")
                        elif hits >= max(1, (len(sig_tokens) + 1) // 2):
                            match_score += 800.0 * hits

            # Series match scoring
            series_is_match = False
            if clean_series and not series_is_author:
                mam_series_names = [
                    str(s.get("name", ""))
                    for s in series_info
                    if isinstance(s, dict)
                ]
                if _field_matches(mam_series_names, clean_series) or (
                    " ".join(_normalize_tokens(clean_series)) in cand_title_norm
                ):
                    series_is_match = True
                    match_score += 3500.0
                    match_reasons.append("✓ Series Match")

            # Volume match scoring
            if volume and search_mode in {"smart", "custom"}:
                vol_matched, vol_different = _volume_matches_row(
                    {"title": cand_title, "series": series_info}, volume
                )
                if vol_matched:
                    match_score += 6000.0
                    match_reasons.append(f"✓ Book #{volume}")
                elif vol_different and not title_is_match:
                    match_score -= 5000.0

            total_score = (
                match_score
                + (base_score[0] * 500)
                + base_score[1]
                + (base_score[2] * 50)
                + min(base_score[3], 50)
            )

            tid = as_int(r.get("id") or meta.get("mam_id"))
            cand_title_str = str(
                meta.get("title") or r.get("title", "Untitled")
            )
            try:
                in_library = bool(
                    tid and self.repository.is_book_present(tid, meta)
                )
                if not in_library and cand_title_str:
                    norm_t = normalize_title(cand_title_str)
                    if norm_t:
                        existing_recs = self.repository.records_with_title(
                            norm_t
                        )
                        if existing_recs:
                            in_library = True
            except Exception:
                in_library = False

            scored_candidates.append(
                {
                    "mam_id": r.get("id") or meta.get("mam_id"),
                    "title": meta.get("title") or r.get("title", "Untitled"),
                    "authors": authors,
                    "narrators": narrators,
                    "series": series_info,
                    "series_display": series_display,
                    "series_name_primary": series_name_primary,
                    "series_volume": cand_vol,
                    "size": size_str,
                    "filetypes": [ft.upper() for ft in filetypes],
                    "media_type": (
                        meta.get("media_type")
                        or r.get("media_type", "audiobook")
                    ),
                    "language": meta.get("language") or "English",
                    "num_files": as_int(
                        meta.get("num_files") or r.get("numfiles", 0)
                    ),
                    "seeders": as_int(r.get("seeders", 0)),
                    "leechers": as_int(r.get("leechers", 0)),
                    "free": as_bool(r.get("free")),
                    "vip": as_bool(r.get("vip")),
                    "personal_freeleech": as_bool(r.get("personal_freeleech")),
                    "score": total_score,
                    "author_penalized": has_author_penalty,
                    "author_matched": author_is_match,
                    "title_matched": title_is_match,
                    "series_matched": series_is_match,
                    "match_reasons": match_reasons,
                    "in_library": in_library,
                }
            )

        if search_mode == "author":
            if author_matched_found:
                scored_candidates = [
                    c for c in scored_candidates if not c.get("author_penalized")
                ]
            scored_candidates.sort(
                key=lambda c: (
                    0 if c.get("author_matched") else 1,
                    0 if c.get("series_name_primary") else 1,
                    str(c.get("series_name_primary") or "").casefold(),
                    parse_entry_number(str(c.get("series_volume") or "")),
                    str(c.get("title") or "").casefold(),
                    -float(c.get("score") or 0),
                )
            )
            return scored_candidates[: max(limit, 50)]

        if search_mode == "series":
            series_filtered = [
                c
                for c in scored_candidates
                if c.get("series_matched") or not c.get("author_penalized")
            ]
            if series_filtered:
                scored_candidates = series_filtered
            scored_candidates.sort(
                key=lambda c: (
                    0 if c.get("series_matched") else 1,
                    parse_entry_number(str(c.get("series_volume") or "")),
                    -float(c.get("score") or 0),
                )
            )
            return scored_candidates[: max(limit, 50)]

        # Default: smart / custom mode
        if author_matched_found:
            scored_candidates = [
                c for c in scored_candidates if not c.get("author_penalized")
            ]
        elif effective_author and search_mode == "smart":
            # Keep candidates matching title/series if author didn't match
            relevant = [
                c
                for c in scored_candidates
                if c.get("title_matched")
                or c.get("series_matched")
                or not c.get("author_penalized")
            ]
            if relevant:
                scored_candidates = relevant

        scored_candidates.sort(key=lambda c: c["score"], reverse=True)
        return scored_candidates[:limit]

    async def queue_download(
        self,
        mam_id: int,
        series_name: str,
        volume: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        if not self.mam:
            return {"ok": False, "error": "MAM client not initialized"}

        raw_row = await self.mam.get_torrent_info_by_id(mam_id)
        if not raw_row:
            return {"ok": False, "error": f"Torrent #{mam_id} not found on MAM"}

        selected = await select_row(
            self.config,
            self.repository,
            raw_row,
            {
                "cost": "ratio",
                "name": f"nextchapter:{series_name}",
                "allow_any_format": True,
                "allow_duplicate": True,
                "force_download": True,
            },
        )
        _ = selected
        vol_str = str(volume or "").strip()
        raw_title = str(raw_row.get("title") or title or "").strip()

        # If volume wasn't explicitly passed, try extracting from torrent series
        if not vol_str:
            meta = torrent_meta(raw_row)
            r_series = meta.get("series") or []
            if r_series and isinstance(r_series[0], dict):
                entries = r_series[0].get("entries") or (
                    [r_series[0]["sequence"]]
                    if r_series[0].get("sequence")
                    else []
                )
                if entries:
                    vol_str = _normalize_volume_str(str(entries[0]))

        d_key = make_disposition_key(series_name, vol_str, title or raw_title)
        now = int(time.time())
        queued_map = self._load_queued_volumes()
        q_record = {
            "disposition_key": d_key,
            "mam_id": mam_id,
            "series_name": series_name,
            "volume_number": vol_str,
            "title": str(title or raw_title),
            "queued_title": raw_title,
            "queued_at": now,
            "torrent_hash": "",
            "qbit_state": "queued_mam",
            "qbit_state_label": "Queued • Sending to qBittorrent...",
            "qbit_progress": 0.0,
            "qbit_file": "",
            "qbit_save_path": "",
            "qbit_dlspeed": "",
        }
        queued_map[d_key] = q_record
        self._save_queued_volumes(queued_map)

        # Auto-resolve any "unable_to_find" disposition when user queues the volume
        dispositions = self._load_dispositions()
        existing_disp = self._match_disposition(
            dispositions, series_name, vol_str, title or raw_title
        )
        if existing_disp and existing_disp.get("status") == "unable_to_find":
            ex_key = str(existing_disp.get("disposition_key") or d_key)
            dispositions.pop(ex_key, None)
            dispositions.pop(d_key, None)
            self._save_dispositions(dispositions)

        state = self._apply_dispositions_to_state(self.get_cached_state())
        self._state_cache = state
        self.repository.set_config_value(
            "nextchapter_state", json.dumps(state)
        )

        log_msg = (
            f"NextChapter queued #{mam_id} for series '{series_name}' "
            f"({raw_row.get('title')})"
        )
        self.repository.log_activity(
            "nextchapter",
            log_msg,
            level="success",
            context={
                "mam_id": mam_id,
                "series": series_name,
                "volume": vol_str,
            },
        )
        return {
            "ok": True,
            "mam_id": mam_id,
            "title": raw_row.get("title"),
            "series_name": series_name,
            "volume_number": vol_str,
            "disposition_key": d_key,
            "queued_item": q_record,
            "counts": self._summary_counts(state),
        }

    def _summary_counts(self, state: dict[str, Any]) -> dict[str, int]:
        return {
            "missing_count": int(state.get("missing_count") or 0),
            "complete_count": int(state.get("complete_count") or 0),
            "not_wanted_count": int(state.get("not_wanted_count") or 0),
            "unable_to_find_count": int(state.get("unable_to_find_count") or 0),
            "dismissed_count": int(state.get("dismissed_count") or 0),
            "queued_count": int(state.get("queued_count") or 0),
            "total_series": int(state.get("total_series") or 0),
        }

    def get_dispositions(self) -> dict[str, Any]:
        state = self.get_cached_state()
        return {
            "ok": True,
            "not_wanted": state.get("not_wanted_volumes", []),
            "unable_to_find": state.get("unable_to_find_volumes", []),
            "dismissed": state.get("dismissed_volumes", []),
            "queued": state.get("queued_volumes", []),
            "counts": self._summary_counts(state),
        }

    def set_volume_disposition(
        self,
        series_name: str,
        volume_number: str = "",
        title: str = "",
        author: str = "",
        status: str = "not_wanted",
        reason: str = "",
        notes: str = "",
        cover_url: str = "",
        summary: str = "",
        disposition_key: str = "",
    ) -> dict[str, Any]:
        norm_status = str(status or "").strip().casefold()
        if norm_status in {"missing", "restore", "active", ""}:
            return self.restore_volume_disposition(
                disposition_key=disposition_key,
                series_name=series_name,
                volume_number=volume_number,
                title=title,
            )
        if norm_status not in {"not_wanted", "unable_to_find"}:
            return {
                "ok": False,
                "error": f"Invalid disposition status: {status}",
            }

        state = self.get_cached_state()
        vol_str = _normalize_volume_str(volume_number) or str(
            volume_number or ""
        ).strip()

        # Enrich metadata from cached series if missing
        for s in state.get("series", []):
            s_name = str(s.get("series_name") or "")
            if not (
                _field_matches([s_name], series_name)
                or _field_matches([series_name], s_name)
            ):
                continue
            series_name = s_name
            if not author and s.get("authors"):
                author = str(s["authors"][0])
            for m in s.get("all_missing_volumes") or s.get(
                "missing_volumes", []
            ):
                m_vol = _normalize_volume_str(
                    str(m.get("volume_number") or "")
                ) or str(m.get("volume_number") or "").strip()
                if (vol_str and m_vol == vol_str) or (
                    title and _field_matches([m.get("title", "")], title)
                ):
                    if not title:
                        title = str(m.get("title") or "")
                    if not author:
                        author = str(m.get("author") or "")
                    if not cover_url:
                        cover_url = str(m.get("cover_url") or "")
                    if not summary:
                        summary = str(m.get("summary") or "")
                    break
            break

        dispositions = self._load_dispositions()
        existing = self._match_disposition(
            dispositions, series_name, vol_str, title
        )
        d_key = (
            str(existing.get("disposition_key"))
            if existing and existing.get("disposition_key")
            else (
                disposition_key.strip()
                if disposition_key and disposition_key.strip()
                else make_disposition_key(series_name, vol_str, title)
            )
        )

        default_reason = (
            "Not needed / Skipped"
            if norm_status == "not_wanted"
            else "Unable to find on MAM"
        )
        clean_reason = str(reason or "").strip() or (
            str((existing or {}).get("reason") or "")
            if existing and existing.get("status") == norm_status
            else default_reason
        )
        clean_notes = (
            str(notes).strip()
            if notes is not None
            else str((existing or {}).get("notes") or "")
        )

        disp_obj = VolumeDisposition(
            disposition_key=d_key,
            series_name=series_name,
            volume_number=vol_str,
            title=title or (f"{series_name} #{vol_str}" if vol_str else series_name),
            author=author,
            cover_url=cover_url,
            summary=summary,
            status=norm_status,
            reason=clean_reason,
            notes=clean_notes,
            updated_at=int(time.time()),
            last_checked_at=int((existing or {}).get("last_checked_at") or 0),
            mam_available_count=int(
                (existing or {}).get("mam_available_count") or 0
            ),
            best_candidate=(existing or {}).get("best_candidate"),
        )
        dispositions[d_key] = disp_obj.to_dict()
        self._save_dispositions(dispositions)

        state = self._apply_dispositions_to_state(state)
        self._state_cache = state
        self.repository.set_config_value(
            "nextchapter_state", json.dumps(state)
        )
        return {
            "ok": True,
            "disposition": disp_obj.to_dict(),
            "counts": self._summary_counts(state),
        }

    def restore_volume_disposition(
        self,
        disposition_key: str = "",
        series_name: str = "",
        volume_number: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        dispositions = self._load_dispositions()
        removed = False
        if disposition_key and disposition_key in dispositions:
            dispositions.pop(disposition_key, None)
            removed = True
        else:
            matched = self._match_disposition(
                dispositions, series_name, volume_number, title
            )
            if matched:
                mk = str(matched.get("disposition_key") or "")
                if mk in dispositions:
                    dispositions.pop(mk, None)
                    removed = True
            cand_key = make_disposition_key(series_name, volume_number, title)
            if cand_key in dispositions:
                dispositions.pop(cand_key, None)
                removed = True

        self._save_dispositions(dispositions)
        state = self._apply_dispositions_to_state(self.get_cached_state())
        self._state_cache = state
        self.repository.set_config_value(
            "nextchapter_state", json.dumps(state)
        )
        return {
            "ok": True,
            "removed": removed,
            "counts": self._summary_counts(state),
        }

    def set_series_disposition(
        self,
        series_name: str,
        status: str = "not_wanted",
        reason: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        norm_status = str(status or "").strip().casefold()
        state = self.get_cached_state()
        target_series = next(
            (
                s
                for s in state.get("series", [])
                if _field_matches([s.get("series_name", "")], series_name)
                or _field_matches([series_name], s.get("series_name", ""))
            ),
            None,
        )
        if not target_series:
            return {"ok": False, "error": f"Series '{series_name}' not found"}

        canonical_s = str(target_series.get("series_name") or series_name)
        s_authors = target_series.get("authors") or []
        default_author = str(s_authors[0]) if s_authors else ""
        dispositions = self._load_dispositions()
        updated_count = 0

        if norm_status in {"missing", "restore", "active"}:
            to_remove = [
                k
                for k, v in dispositions.items()
                if _field_matches([v.get("series_name", "")], canonical_s)
                or _field_matches([canonical_s], v.get("series_name", ""))
            ]
            for k in to_remove:
                dispositions.pop(k, None)
                updated_count += 1
        elif norm_status in {"not_wanted", "unable_to_find"}:
            default_reason = (
                reason.strip()
                if reason and reason.strip()
                else (
                    "Skipped remaining in series"
                    if norm_status == "not_wanted"
                    else "Unable to find on MAM"
                )
            )
            now = int(time.time())
            for m in target_series.get("missing_volumes", []):
                if m.get("status") == "queued":
                    continue
                vol_str = str(m.get("volume_number") or "")
                m_title = str(m.get("title") or "")
                d_key = make_disposition_key(canonical_s, vol_str, m_title)
                disp_obj = VolumeDisposition(
                    disposition_key=d_key,
                    series_name=canonical_s,
                    volume_number=vol_str,
                    title=m_title or f"{canonical_s} #{vol_str}",
                    author=str(m.get("author") or default_author),
                    cover_url=str(m.get("cover_url") or ""),
                    summary=str(m.get("summary") or ""),
                    status=norm_status,
                    reason=default_reason,
                    notes=notes.strip() if notes else "",
                    updated_at=now,
                )
                dispositions[d_key] = disp_obj.to_dict()
                updated_count += 1
        else:
            return {"ok": False, "error": f"Invalid status: {status}"}

        self._save_dispositions(dispositions)
        state = self._apply_dispositions_to_state(state)
        self._state_cache = state
        self.repository.set_config_value(
            "nextchapter_state", json.dumps(state)
        )
        return {
            "ok": True,
            "series_name": canonical_s,
            "updated_count": updated_count,
            "counts": self._summary_counts(state),
        }

    async def recheck_unable_to_find(
        self, disposition_key: str = ""
    ) -> dict[str, Any]:
        dispositions = self._load_dispositions()
        if disposition_key:
            target_keys = (
                [disposition_key] if disposition_key in dispositions else []
            )
        else:
            target_keys = [
                k
                for k, v in dispositions.items()
                if v.get("status") == "unable_to_find"
            ]

        if not target_keys:
            state = self.get_cached_state()
            return {
                "ok": True,
                "checked_count": 0,
                "found_count": 0,
                "items": [],
                "counts": self._summary_counts(state),
            }

        sem = asyncio.Semaphore(4)
        now = int(time.time())
        found_count = 0
        updated_items: list[dict[str, Any]] = []

        async def _check_one(k: str) -> None:
            nonlocal found_count
            disp = dispositions.get(k)
            if not disp:
                return
            async with sem:
                try:
                    candidates = await self.search_mam_for_missing(
                        series_name=str(disp.get("series_name") or ""),
                        volume=str(disp.get("volume_number") or ""),
                        title=str(disp.get("title") or ""),
                        author=str(disp.get("author") or ""),
                        mode="smart",
                        limit=10,
                    )
                except Exception:
                    candidates = []
                disp["last_checked_at"] = now
                disp["mam_available_count"] = len(candidates)
                disp["best_candidate"] = candidates[0] if candidates else None
                if candidates:
                    found_count += 1
                updated_items.append(dict(disp))

        await asyncio.gather(*[_check_one(k) for k in target_keys])
        self._save_dispositions(dispositions)

        state = self._apply_dispositions_to_state(self.get_cached_state())
        self._state_cache = state
        self.repository.set_config_value(
            "nextchapter_state", json.dumps(state)
        )
        return {
            "ok": True,
            "checked_count": len(target_keys),
            "found_count": found_count,
            "items": updated_items,
            "counts": self._summary_counts(state),
        }

    async def get_live_queue_status(self) -> dict[str, Any]:
        """Connect to qBittorrent and SQLite to track queued NextChapter volumes."""
        queued_map = self._load_queued_volumes()
        repo_queue = self._collect_repo_queue_snapshot()

        # Connect to configured qBittorrent client(s) to get live progress & files
        qbit_by_hash: dict[str, dict[str, Any]] = {}
        qbit_configs = getattr(self.config, "qbittorrent", ()) or ()
        for q_cfg in qbit_configs:
            q_url = getattr(q_cfg, "url", None) or (
                q_cfg.get("url") if isinstance(q_cfg, dict) else None
            )
            if not q_url:
                continue
            q_user = getattr(q_cfg, "username", "") or (
                q_cfg.get("username", "") if isinstance(q_cfg, dict) else ""
            )
            q_pass = getattr(q_cfg, "password", "") or (
                q_cfg.get("password", "") if isinstance(q_cfg, dict) else ""
            )
            client = QbitClient(str(q_url), timeout=8.0)
            try:
                await client.login(str(q_user), str(q_pass))
                torrents_list = await client.torrents()
                for t in torrents_list:
                    th = str(t.get("hash") or "").strip().lower()
                    if not th:
                        continue
                    raw_state = str(t.get("state") or "").strip()
                    st_lower = raw_state.lower()
                    prog = round(float(t.get("progress") or 0.0) * 100.0, 1)
                    dlspeed = _format_speed(float(t.get("dlspeed") or 0))
                    content_path = str(
                        t.get("content_path") or t.get("save_path") or ""
                    ).strip()
                    t_name = str(t.get("name") or "").strip()

                    if any(
                        w in st_lower
                        for w in ("error", "missing")
                    ):
                        q_state = "error"
                        q_label = f"qBittorrent Error ({raw_state})"
                    elif prog >= 100.0 or any(
                        w in st_lower
                        for w in ("up", "seed")
                    ):
                        q_state = "completed"
                        q_label = "Completed in qBittorrent (100%)"
                        prog = 100.0
                    elif any(
                        w in st_lower
                        for w in ("pause", "stop")
                    ):
                        q_state = "paused"
                        q_label = f"Paused in qBittorrent ({prog:.0f}%)"
                    elif any(
                        w in st_lower
                        for w in ("down", "meta", "stall")
                    ):
                        q_state = "downloading"
                        q_label = f"Downloading in qBittorrent ({prog:.1f}%)"
                    else:
                        q_state = "queued_qbit"
                        q_label = f"Queued in qBittorrent ({prog:.0f}%)"

                    file_display = t_name
                    if content_path:
                        leaf = (
                            content_path.replace("\\", "/")
                            .rstrip("/")
                            .split("/")[-1]
                        )
                        if leaf:
                            file_display = leaf

                    qbit_by_hash[th] = {
                        "torrent_hash": th,
                        "qbit_state": q_state,
                        "qbit_state_label": q_label,
                        "qbit_progress": prog,
                        "qbit_file": file_display,
                        "qbit_save_path": content_path,
                        "qbit_dlspeed": dlspeed,
                        "name": t_name,
                    }

                # For any tracked hash in queued_map or repo_queue, fetch files
                tracked_hashes: set[str] = set()
                queued_title_norms = {
                    normalize_title(str(qv.get("queued_title") or ""))
                    for qv in queued_map.values()
                    if qv.get("queued_title")
                }
                for qv in queued_map.values():
                    th = str(qv.get("torrent_hash") or "").strip().lower()
                    if th:
                        tracked_hashes.add(th)
                for rq in repo_queue.values():
                    th = str(rq.get("torrent_hash") or "").strip().lower()
                    if th:
                        tracked_hashes.add(th)
                for th, qb_entry in qbit_by_hash.items():
                    if normalize_title(qb_entry.get("name", "")) in queued_title_norms:
                        tracked_hashes.add(th)

                for th in tracked_hashes:
                    if th in qbit_by_hash:
                        try:
                            files = await client.files(th)
                            if files and isinstance(files, list):
                                first_f = str(
                                    files[0].get("name") or ""
                                ).strip()
                                if first_f:
                                    leaf_f = first_f.replace("\\", "/").split(
                                        "/"
                                    )[-1]
                                    if len(files) > 1:
                                        leaf_f += (
                                            f" (+{len(files) - 1} files)"
                                        )
                                    qbit_by_hash[th]["qbit_file"] = leaf_f
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                with contextlib.suppress(Exception):
                    await client.close()

        # Merge live qBittorrent data into queued_map
        changed = False
        for _d_key, qv in queued_map.items():
            mid = int(qv.get("mam_id") or 0)
            rq = repo_queue.get(mid) if mid else None
            if rq:
                if rq.get("torrent_hash") and not qv.get("torrent_hash"):
                    qv["torrent_hash"] = rq["torrent_hash"]
                    changed = True
                if rq.get("qbit_state") == "linked":
                    qv["qbit_state"] = "linked"
                    qv["qbit_state_label"] = "Imported to Library"
                    qv["qbit_progress"] = 100.0
                    qv["qbit_file"] = rq.get("qbit_file") or qv.get(
                        "qbit_file", ""
                    )
                    qv["qbit_save_path"] = rq.get("qbit_save_path") or qv.get(
                        "qbit_save_path", ""
                    )
                    changed = True
                    continue

            th = str(qv.get("torrent_hash") or "").strip().lower()
            qb = qbit_by_hash.get(th) if th else None
            if not qb and qv.get("queued_title"):
                # Fallback match by normalized torrent name in qBittorrent
                qt_norm = normalize_title(str(qv.get("queued_title") or ""))
                if qt_norm:
                    for cand_qb in qbit_by_hash.values():
                        if normalize_title(cand_qb.get("name", "")) == qt_norm:
                            qb = cand_qb
                            qv["torrent_hash"] = cand_qb["torrent_hash"]
                            break

            if qb:
                qv["qbit_state"] = qb["qbit_state"]
                qv["qbit_state_label"] = qb["qbit_state_label"]
                qv["qbit_progress"] = qb["qbit_progress"]
                qv["qbit_file"] = qb["qbit_file"] or qv.get("qbit_file", "")
                qv["qbit_save_path"] = qb["qbit_save_path"] or qv.get(
                    "qbit_save_path", ""
                )
                qv["qbit_dlspeed"] = qb["qbit_dlspeed"]
                changed = True
            elif rq:
                qv["qbit_state"] = rq["qbit_state"]
                qv["qbit_state_label"] = rq["qbit_state_label"]
                changed = True

        if changed:
            self._save_queued_volumes(queued_map)

        state = self._apply_dispositions_to_state(self.get_cached_state())
        self._state_cache = state
        return {
            "ok": True,
            "queued_volumes": state.get("queued_volumes", []),
            "by_key": queued_map,
            "counts": self._summary_counts(state),
        }

    async def queue_all_missing(self, series_name: str) -> dict[str, Any]:
        state = self.get_cached_state()
        target_series = next(
            (
                s
                for s in state.get("series", [])
                if s["series_name"].casefold() == series_name.casefold()
            ),
            None,
        )
        if not target_series:
            return {"ok": False, "error": f"Series '{series_name}' not found"}

        missing_list = [
            m
            for m in target_series.get("missing_volumes", [])
            if m.get("status") not in {"not_wanted", "unable_to_find"}
        ]
        queued_count = 0
        queued_items: list[dict[str, Any]] = []

        for item in missing_list:
            vol = item.get("volume_number", "")
            title = item.get("title", "")
            author = item.get("author", "")
            candidates = await self.search_mam_for_missing(
                series_name, vol, title, author=author
            )
            if candidates:
                best = candidates[0]
                res = await self.queue_download(
                    best["mam_id"], series_name, vol, title=title
                )
                if res.get("ok"):
                    queued_count += 1
                    queued_items.append(
                        {
                            "volume": vol,
                            "mam_id": best["mam_id"],
                            "title": best["title"],
                            "disposition_key": res.get("disposition_key", ""),
                            "queued_item": res.get("queued_item"),
                        }
                    )

        return {
            "ok": True,
            "queued_count": queued_count,
            "items": queued_items,
            "series_name": series_name,
        }

    async def refresh_recommendations(
        self, force: bool = False
    ) -> list[dict[str, Any]]:
        async with self._lock:
            state = self.get_cached_state()
            if not force and state.get("recommendations"):
                return state["recommendations"]

            series_list = state.get("series", [])
            if not series_list:
                state = await self.scan_series(force=True)
                series_list = state.get("series", [])

            all_authors: list[str] = []
            for s in series_list:
                all_authors.extend(s.get("authors", []))
            top_authors = list(dict.fromkeys(all_authors))[:20]

            recommendations: list[dict[str, Any]] = []

            # 1. Try Gemini AI recommendations if key configured
            if self.settings.gemini_api_key:
                gemini_client = GeminiClient(self.settings.gemini_api_key)
                try:
                    ai_result = await gemini_client.analyze_and_recommend(
                        series_list[:25], top_authors
                    )
                    raw_recs = ai_result.get("recommendations", [])
                    for rec in raw_recs:
                        rec_item = BookRecommendation(
                            title=rec.get("title", ""),
                            author=rec.get("author", ""),
                            series_name=rec.get("series_name", ""),
                            volume=rec.get("volume", ""),
                            reason=rec.get(
                                "reason",
                                "Recommended based on your library series",
                            ),
                            summary=rec.get("summary", ""),
                            source="gemini_ai",
                        )
                        recommendations.append(rec_item.to_dict())
                finally:
                    await gemini_client.close()

            # 2. Fallback / supplementary author catalogue recommendations
            if len(recommendations) < 5 and top_authors:
                google_client = GoogleBooksClient(
                    self.settings.google_books_api_key
                )
                try:
                    for author in top_authors[:4]:
                        books = await google_client.search_series_books(
                            "", author=author
                        )
                        for b in books[:2]:
                            if not any(
                                r["title"].casefold() == b["title"].casefold()
                                for r in recommendations
                            ):
                                recommendations.append(
                                    BookRecommendation(
                                        title=b["title"],
                                        author=author,
                                        volume=b.get("volume", ""),
                                        summary=b.get("description", ""),
                                        cover_url=b.get("cover_url", ""),
                                        reason=(
                                            f"Popular book by {author}, an "
                                            "author in your collection"
                                        ),
                                        source="author_catalogue",
                                    ).to_dict()
                                )
                finally:
                    await google_client.close()

            # For each recommendation, find if available on MAM
            for rec in recommendations:
                if self.mam:
                    try:
                        matches = await self.search_mam_for_missing(
                            rec.get("series_name") or rec["title"],
                            rec.get("volume", ""),
                            title=rec["title"],
                            author=rec.get("author"),
                        )
                        if matches:
                            rec["best_candidate"] = matches[0]
                    except Exception:
                        pass

            state["recommendations"] = recommendations
            self._state_cache = state
            self.repository.set_config_value(
                "nextchapter_state", json.dumps(state)
            )
            return recommendations
