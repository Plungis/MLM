from __future__ import annotations

import asyncio
import html
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

    def get_cached_state(self) -> dict[str, Any]:
        if (
            self._state_cache is not None
            and self._state_cache.get("schema_version") == STATE_SCHEMA_VERSION
        ):
            return self._state_cache
        saved = self.repository.config_value("nextchapter_state")
        if saved:
            try:
                loaded = json.loads(saved)
                if (
                    isinstance(loaded, dict)
                    and loaded.get("schema_version") == STATE_SCHEMA_VERSION
                ):
                    self._state_cache = loaded
                    return self._state_cache
            except json.JSONDecodeError:
                pass
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "series": [],
            "missing_count": 0,
            "complete_count": 0,
            "total_series": 0,
            "recommendations": [],
            "last_scan": 0,
        }

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
