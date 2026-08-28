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


async def run_autograbber(
    config: Config,
    repository: Repository,
    mam: MamClient,
    rule: dict[str, Any],
    *,
    index: int = 0,
) -> int:
    user = await mam.user_info()
    unsat = user.get("unsat", {})
    site_limit = max(0, int(unsat.get("limit", 0)))
    used = max(0, int(unsat.get("count", 0)))
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
    if not torrent_id or torrent_id in config.ignore_torrents:
        return False
    if not matches_filter(row, rule) or repository.has_mam_id(torrent_id):
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
        return False
    duplicate = False
    for existing in repository.records_with_title(title_search):
        old_meta = existing.get("meta", {})
        if not metadata_matches(meta, old_meta):
            continue
        old_preference = _preference(old_meta.get("filetypes", []), preferred)
        if old_preference is not None and old_preference <= preference:
            duplicate = True
            break
    category, tags = _tagging(config, row)
    candidate = {
        "mam_id": torrent_id,
        "goodreads_id": goodreads_id,
        "hash": None,
        "dl_link": row.get("dl"),
        "unsat_buffer": rule.get("unsat_buffer"),
        "wedge_buffer": rule.get("wedge_buffer"),
        "cost": _cost(row, requested_cost),
        "category": rule.get("category") or category,
        "tags": tags,
        "title_search": title_search,
        "meta": meta,
        "grabber": rule.get("name", str(index)),
        "created_at": datetime.now(UTC).isoformat(),
        "started_at": None,
        "removed_at": None,
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
