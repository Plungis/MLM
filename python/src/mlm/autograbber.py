from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .config import Config
from .mam import MamClient
from .repository import Repository
from .search import (
    as_bool,
    matches_filter,
    metadata_matches,
    normalize_title,
    search_pages,
    torrent_meta,
)


def _preferred_types(config: Config, media_type: str) -> tuple[str, ...]:
    if media_type in {"audiobook", "periodical_audiobook"}:
        return config.audio_types
    if media_type in {"ebook", "manga", "comic_book", "periodical_ebook"}:
        return config.ebook_types
    if media_type == "musicology":
        return config.music_types
    if media_type == "radio":
        return config.radio_types
    return ()


def _preference(formats: list[str], preferred: tuple[str, ...]) -> int | None:
    positions = [preferred.index(fmt) for fmt in formats if fmt in preferred]
    return min(positions) if positions else None


def _cost(row: dict[str, Any], requested: str) -> str:
    if as_bool(row.get("vip")):
        return "Vip"
    if as_bool(row.get("personal_freeleech")):
        return "PersonalFreeleech"
    if as_bool(row.get("free")):
        return "GlobalFreeleech"
    if requested == "wedge":
        return "UseWedge"
    if requested == "try_wedge":
        return "TryWedge"
    return "Ratio"


def _tagging(config: Config, row: dict[str, Any]) -> tuple[str | None, list[str]]:
    category = None
    tags: list[str] = []
    for rule in config.tags:
        if matches_filter(row, rule):
            category = category or rule.get("category")
            tags.extend(str(tag) for tag in rule.get("tags", []))
    return category, list(dict.fromkeys(tags))


def resolve_torrent_category_and_tags(
    config: Config,
    row: dict[str, Any] | None = None,
    *,
    meta: dict[str, Any] | None = None,
    rule: dict[str, Any] | None = None,
) -> tuple[str | None, list[str]]:
    """Resolve the qBittorrent category and tags for a torrent.

    Precedence:
    1. Explicit rule['category'] + matching [[tag]] rules in config.tags.
    2. Configured [[library]] entries matching the torrent's media type
       (audiobook vs ebook), falling back to the first configured [[library]]
       with a category so organized libraries always pick up queued downloads.
    3. Configured [[tag]], [[autograb]], [[goodreads_list]], [[notion_list]],
       or [[snatchlist]] category if no [[library]] defines a category.
    """
    raw_row = row or {}
    rule_dict = rule or {}
    tag_category, tags = _tagging(config, raw_row) if raw_row else (None, [])
    for extra_tag in rule_dict.get("tags", []) or []:
        t_str = str(extra_tag).strip()
        if t_str and t_str not in tags:
            tags.append(t_str)

    explicit_cat = str(rule_dict.get("category") or "").strip() or None
    category = explicit_cat or (str(tag_category).strip() if tag_category else None)

    if meta is None and raw_row:
        try:
            meta = torrent_meta(raw_row)
        except Exception:
            meta = {}
    meta_dict = meta or {}
    media_type = str(
        meta_dict.get("media_type") or raw_row.get("media_type") or ""
    ).strip().lower()
    is_audio = "audio" in media_type
    is_ebook = media_type in {
        "ebook",
        "manga",
        "comic_book",
        "periodical_ebook",
    }

    libraries = list(getattr(config, "libraries", ()) or ())
    if category and libraries:
        for lib in libraries:
            lib_cat = str(lib.get("category") or "").strip()
            if lib_cat and lib_cat.casefold() == category.casefold():
                category = lib_cat
                break

    if not category and libraries:
        matched_lib: dict[str, Any] | None = None
        for lib in libraries:
            lib_cat = str(lib.get("category") or "").strip()
            if not lib_cat:
                continue
            deny_tags = {
                str(t).strip() for t in (lib.get("deny_tags") or []) if str(t).strip()
            }
            if deny_tags and deny_tags.intersection(tags):
                continue
            has_audio = lib.get("audio_types") is not None
            has_ebook = lib.get("ebook_types") is not None
            audio_list = lib.get("audio_types")
            ebook_list = lib.get("ebook_types")
            cat_lower = lib_cat.casefold()

            if is_audio:
                if has_audio and isinstance(audio_list, list) and len(audio_list) == 0:
                    continue
                if "ebook" in cat_lower and "audio" not in cat_lower and not has_audio:
                    continue
                if (has_audio and audio_list) or "audio" in cat_lower or not has_ebook:
                    matched_lib = lib
                    break
            elif is_ebook:
                if has_ebook and isinstance(ebook_list, list) and len(ebook_list) == 0:
                    continue
                if "audio" in cat_lower and "ebook" not in cat_lower and not has_ebook:
                    continue
                if (
                    (has_ebook and ebook_list)
                    or "ebook" in cat_lower
                    or "book" in cat_lower
                    or not has_audio
                ):
                    matched_lib = lib
                    break

        if matched_lib is None:
            for lib in libraries:
                lib_cat = str(lib.get("category") or "").strip()
                if lib_cat:
                    matched_lib = lib
                    break

        if matched_lib is not None:
            category = str(matched_lib.get("category") or "").strip() or None
            allow_tags = [
                str(t).strip()
                for t in (matched_lib.get("allow_tags") or [])
                if str(t).strip()
            ]
            if allow_tags and not set(allow_tags).intersection(tags):
                tags.append(allow_tags[0])

    if not category:
        fallback_sources = (
            list(getattr(config, "tags", ()) or [])
            + list(getattr(config, "autograbs", ()) or [])
            + list(getattr(config, "goodreads_lists", ()) or [])
            + list(getattr(config, "notion_lists", ()) or [])
            + list(getattr(config, "snatchlist", ()) or [])
        )
        for src in fallback_sources:
            cand_cat = str(src.get("category") or "").strip()
            if not cand_cat:
                continue
            src_cats = src.get("categories") or {}
            if is_audio and isinstance(src_cats, dict) and src_cats.get("audio"):
                category = cand_cat
                break
            if is_ebook and isinstance(src_cats, dict) and src_cats.get("ebook"):
                category = cand_cat
                break
            if not category:
                category = cand_cat

    return category, list(dict.fromkeys(tags))


async def run_autograbber(
    config: Config,
    repository: Repository,
    mam: MamClient,
    rule: dict[str, Any],
    *,
    index: int = 0,
) -> int:
    user = await mam.user_info()
    raw_unsat = user.get("unsat")
    if not isinstance(raw_unsat, dict):
        raw_unsat = user.get("unsats") or user.get("unsatisfied")
    if isinstance(raw_unsat, dict):
        limit_val = raw_unsat.get("limit")
        site_limit = max(0, int(limit_val)) if limit_val is not None else 0
        used = max(0, int(raw_unsat.get("count") or 0))
    elif isinstance(raw_unsat, (int, str)) and str(raw_unsat).isdigit():
        site_limit = 0
        used = int(raw_unsat)
    else:
        site_limit = 0
        used = 0
    if site_limit == 0:
        if config.max_unsat_slots is not None:
            site_limit = config.max_unsat_slots
        else:
            site_limit = max(150, used + 10)
    slot_buffer = int(rule.get("unsat_buffer", config.unsat_buffer))
    slot_cap = max(0, site_limit - slot_buffer)
    if config.max_unsat_slots is not None:
        slot_cap = min(slot_cap, config.max_unsat_slots)
    maximum = max(0, slot_cap - used)
    if rule.get("max_active_downloads") is not None:
        active = sum(
            selected.get("grabber") == rule.get("name", str(index))
            for selected in repository.pending_selected()
        )
        maximum = min(maximum, max(0, int(rule["max_active_downloads"]) - active))
    if maximum == 0 and rule.get("cost", "free") not in {
        "metadata_only",
        "metadata_only_add",
    }:
        return 0

    selected_count = 0
    async for row in search_pages(mam, rule):
        if await select_row(config, repository, row, rule, index=index):
            selected_count += 1
        if selected_count >= maximum:
            break
    return selected_count


async def select_row(
    config: Config,
    repository: Repository,
    row: dict[str, Any],
    rule: dict[str, Any],
    *,
    index: int = 0,
    goodreads_id: int | None = None,
) -> bool:
    torrent_id = int(row.get("id", 0))
    force_download = bool(
        rule.get("force_download")
        or rule.get("allow_duplicate")
        or rule.get("name", "").startswith(
            ("manual", "series:", "request:", "nextchapter:")
        )
    )
    if not torrent_id or (
        not force_download and torrent_id in config.ignore_torrents
    ):
        return False
    if not matches_filter(row, rule):
        return False
    if not force_download and repository.has_mam_id(torrent_id):
        return False
    requested_cost = rule.get("cost", "free")
    if requested_cost == "free" and not any(
        as_bool(row.get(field))
        for field in ("vip", "personal_freeleech", "free", "fl_vip")
    ):
        return False
    if requested_cost in {"metadata_only", "metadata_only_add"}:
        return False

    meta = torrent_meta(row)
    title_search = normalize_title(meta["title"])
    preferred = _preferred_types(config, meta["media_type"])
    preference = _preference(meta["filetypes"], preferred)
    if preference is None:
        if rule.get("allow_any_format") or force_download:
            preference = len(preferred)
        else:
            return False
    duplicate = False
    if not rule.get("allow_duplicate", False) and not force_download:
        for existing in repository.records_with_title(title_search):
            old_meta = existing.get("meta", {})
            if not metadata_matches(meta, old_meta):
                continue
            old_preference = _preference(old_meta.get("filetypes", []), preferred)
            if old_preference is not None and old_preference <= preference:
                duplicate = True
                break
    category, tags = resolve_torrent_category_and_tags(
        config, row, meta=meta, rule=rule
    )
    dl_link = row.get("dl") or f"/tor/download.php?tid={torrent_id}"
    candidate = {
        "mam_id": torrent_id,
        "goodreads_id": goodreads_id,
        "hash": None,
        "dl_link": dl_link,
        "unsat_buffer": rule.get("unsat_buffer"),
        "wedge_buffer": rule.get("wedge_buffer"),
        "cost": _cost(row, requested_cost),
        "category": category,
        "tags": tags,
        "title_search": title_search,
        "meta": meta,
        "grabber": rule.get("name", str(index)),
        "created_at": datetime.now(UTC).isoformat(),
        "started_at": None,
        "removed_at": None,
        "force_download": bool(rule.get("force_download") or force_download),
    }
    if duplicate:
        if not rule.get("dry_run", False):
            repository.add_duplicate(candidate)
        return False
    if not candidate["dl_link"]:
        return False
    if not rule.get("dry_run", False):
        repository.add_selected(candidate)
    return True
