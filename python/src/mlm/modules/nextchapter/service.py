from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from ...autograbber import select_row
from ...config import Config
from ...mam import MamClient
from ...repository import Repository
from ...search import (
    as_bool,
    as_int,
    decode_name_mapping,
    decode_series_info,
    parse_size,
    torrent_meta,
)
from ..heavymlm.search import _normalize_tokens
from ..heavymlm.series import (
    detect_library_series,
    fetch_series_releases,
    score_series_candidate,
)
from .clients import GeminiClient, GoogleBooksClient
from .models import (
    BookRecommendation,
    MissingBook,
    NextChapterSettings,
    SeriesHubEntry,
)


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

    def get_cached_state(self) -> dict[str, Any]:
        if self._state_cache is not None:
            return self._state_cache
        saved = self.repository.config_value("nextchapter_state")
        if saved:
            try:
                self._state_cache = json.loads(saved)
                return self._state_cache
            except json.JSONDecodeError:
                pass
        return {
            "series": [],
            "missing_count": 0,
            "complete_count": 0,
            "total_series": 0,
            "recommendations": [],
            "last_scan": 0,
        }

    async def scan_series(self, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            cached = self.get_cached_state()
            now = time.time()
            if (
                not force
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

            # Collect top authors for context
            all_authors: list[str] = []
            for s in raw_series:
                all_authors.extend(s.get("authors", []))
            top_authors = list(dict.fromkeys(all_authors))[:20]

            ai_series_map: dict[str, dict[str, Any]] = {}
            ai_recommendations: list[dict[str, Any]] = []
            has_gemini = bool(self.settings.gemini_api_key)

            last_error = ""
            if has_gemini:
                gemini_client = GeminiClient(self.settings.gemini_api_key)
                try:
                    ai_result = await gemini_client.analyze_series(
                        raw_series, top_authors
                    )
                    last_error = ai_result.get("error", "")
                    for s_item in ai_result.get("series", []):
                        name_key = s_item.get("series_name", "").strip().casefold()
                        if name_key:
                            ai_series_map[name_key] = s_item
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
                s_key = series_name.strip().casefold()
                ai_data = ai_series_map.get(s_key)

                missing_items: list[MissingBook] = []

                if ai_data:
                    canonical_total = int(ai_data.get("canonical_total_books") or 0)
                    for m in ai_data.get("missing_books", []):
                        vol_num = str(m.get("volume_number", "")).strip()
                        m_title = str(m.get("title", "")).strip()
                        m_author = (
                            str(m.get("author", "")).strip()
                            or (authors[0] if authors else "")
                        )
                        m_summary = str(m.get("summary", "")).strip()
                        missing_items.append(
                            MissingBook(
                                series_name=series_name,
                                volume_number=vol_num,
                                title=m_title,
                                author=m_author,
                                summary=m_summary,
                                source="gemini_ai",
                            )
                        )
                elif not has_gemini:
                    canonical_total = len(owned_books)
                else:
                    canonical_total = len(owned_books)

                total_known = max(
                    len(owned_books) + len(missing_items), canonical_total
                )
                pct = (
                    int((len(owned_books) / total_known) * 100)
                    if total_known
                    else 100
                )
                if not has_gemini:
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
                "series": [se.to_dict() for se in series_entries],
                "missing_count": total_missing,
                "complete_count": complete_count,
                "total_series": len(series_entries),
                "recommendations": recs,
                "gemini_configured": has_gemini,
                "last_error": last_error,
                "last_scan": int(now),
            }
            self._state_cache = state
            self.repository.set_config_value(
                "nextchapter_state", json.dumps(state)
            )
            return state

    async def search_mam_for_missing(
        self,
        series_name: str,
        volume: str,
        title: str | None = None,
        author: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.mam:
            return []

        if q and q.strip():
            raw_q = q.strip()
            vol_m = re.search(r"#\s*([\d.]+)", raw_q)
            if vol_m:
                volume = volume or vol_m.group(1)
            parts = [
                p.strip()
                for p in re.split(r"#\s*[\d.]+|\s+-\s+", raw_q)
                if p.strip()
            ]
            if len(parts) >= 2:
                if not author:
                    author = parts[0]
                if not title:
                    title = parts[1]
            elif not title and not series_name:
                title = raw_q

        preferred_media = (
            None
            if self.settings.preferred_media == "both"
            else self.settings.preferred_media
        )

        seen_ids: set[int] = set()
        releases: list[dict[str, Any]] = []

        async def _query_mam(
            text: str, srch_in: list[str] | None = None
        ) -> None:
            if not text or not text.strip():
                return
            clean_text = re.sub(r"#\s*[\d.]+", " ", text)
            clean_text = " ".join(clean_text.split())
            if not clean_text:
                return
            tor: dict[str, Any] = {"text": clean_text, "startNumber": 0}
            if srch_in:
                tor["srchIn"] = srch_in
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
                    return
                for row in res.get("data", []):
                    if isinstance(row, dict):
                        tid = as_int(row.get("id"))
                        if tid and tid not in seen_ids:
                            seen_ids.add(tid)
                            releases.append(row)
            except Exception:
                pass

        # 1. Author + Title search
        if author and title:
            await _query_mam(f"{author} {title}")
        if title:
            await _query_mam(title)
        if q and q.strip():
            await _query_mam(q)
        if series_name:
            await _query_mam(series_name, srch_in=["series"])
            if volume:
                await _query_mam(f"{series_name} {volume}")

        # Fallback to fetch_series_releases
        if not releases:
            search_query = f"{series_name} {volume}"
            if title:
                search_query += f" {title}"
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

        # Filter & score candidates
        scored_candidates: list[dict[str, Any]] = []
        author_matched_found = False

        for r in releases:
            meta = (
                r if "media_type" in r and "authors" in r else torrent_meta(r)
            )
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

            # Match relevance scoring
            match_score = 0.0
            target_author = (author or "").strip().casefold()
            has_author_penalty = False

            if target_author and authors:
                target_tokens = _normalize_tokens(target_author)
                authors_clean = " ".join(
                    _normalize_tokens(" ".join(authors))
                )
                authors_collapsed = authors_clean.replace(" ", "")
                if all(
                    t in authors_clean.split()
                    or (len(t) <= 3 and t in authors_collapsed)
                    for t in target_tokens
                ):
                    author_matched_found = True
                    match_score += 10000.0
                elif any(
                    t in authors_clean.split()
                    for t in target_tokens
                    if len(t) > 2
                ):
                    author_matched_found = True
                    match_score += 4000.0
                else:
                    has_author_penalty = True
                    match_score -= 8000.0

            cand_title = str(meta.get("title") or r.get("title", ""))
            target_title = (title or "").strip().casefold()
            if target_title:
                if target_title in cand_title.casefold():
                    match_score += 5000.0
                else:
                    cand_tokens = set(_normalize_tokens(cand_title))
                    for t in _normalize_tokens(target_title):
                        if (
                            t
                            not in {
                                "of",
                                "the",
                                "in",
                                "and",
                                "a",
                                "an",
                                "on",
                                "to",
                            }
                            and t in cand_tokens
                        ):
                            match_score += 800.0

            total_score = (
                match_score
                + (base_score[0] * 500)
                + base_score[1]
                + (base_score[2] * 50)
                + min(base_score[3], 50)
            )

            scored_candidates.append(
                {
                    "mam_id": r.get("id") or meta.get("mam_id"),
                    "title": meta.get("title") or r.get("title", "Untitled"),
                    "authors": authors,
                    "narrators": narrators,
                    "series": series_info,
                    "series_display": series_display,
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
                }
            )

        if author_matched_found:
            scored_candidates = [
                c for c in scored_candidates if not c.get("author_penalized")
            ]

        scored_candidates.sort(key=lambda c: c["score"], reverse=True)
        return scored_candidates[:10]

    async def queue_download(
        self, mam_id: int, series_name: str, volume: str | None = None
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
            },
        )
        _ = selected
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
                "volume": volume,
            },
        )
        return {"ok": True, "mam_id": mam_id, "title": raw_row.get("title")}

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

        missing_list = target_series.get("missing_volumes", [])
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
                    best["mam_id"], series_name, vol
                )
                if res.get("ok"):
                    queued_count += 1
                    queued_items.append(
                        {
                            "volume": vol,
                            "mam_id": best["mam_id"],
                            "title": best["title"],
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
