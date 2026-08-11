from __future__ import annotations

import re
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

COLLECTION_PATTERN = re.compile(
    r"\b(collection|box(?:ed)?\s+set|bundle|omnibus|complete\s+series|"
    r"books?\s+\d+\s*[-–]\s*\d+|volumes?\s+\d+\s*[-–]\s*\d+)\b",
    re.IGNORECASE,
)
PART_PATTERN = re.compile(
    r"(?:^|[\s._-])(part|pt|disc|disk|cd|chapter|track)\s*\d+\b",
    re.IGNORECASE,
)
DISC_PATTERN = re.compile(r"^(?:CD|Disc|Disk)\s*(\d+)$", re.IGNORECASE)
ORDER_PREFIX = re.compile(
    r"^\s*(?:(?:book|vol(?:ume)?)\s*)?[#\[(]?"
    r"(\d{1,3}(?:\.\d+)?)[\])]?\s*[-._:]\s*",
    re.IGNORECASE,
)
SINGLE_FILE_AUDIO = {".m4b", ".m4a", ".mp4"}
STRUCTURAL_FOLDERS = {
    "audio",
    "audiobook",
    "audiobooks",
    "ebook",
    "ebooks",
    "epub",
    "m4a",
    "m4b",
    "mp3",
    "pdf",
}


@dataclass(frozen=True)
class CollectionBook:
    title: str
    meta: dict[str, Any]
    files: tuple[tuple[Path, Path], ...]
    detection: str


def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _first_text(values: Any) -> str | None:
    if isinstance(values, (list, tuple)):
        values = values[0] if values else None
    if values is None:
        return None
    value = str(values).strip()
    return value or None


def _epub_metadata(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        package_path: str | None = None
        try:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            package_path = next(
                (
                    str(node.attrib.get("full-path"))
                    for node in container.iter()
                    if _tag_name(node.tag) == "rootfile"
                    and node.attrib.get("full-path")
                ),
                None,
            )
        except (KeyError, ElementTree.ParseError):
            package_path = None
        if not package_path:
            package_path = next(
                (name for name in archive.namelist() if name.lower().endswith(".opf")),
                None,
            )
        if not package_path:
            return {}
        package = ElementTree.fromstring(archive.read(package_path))
        values: dict[str, str] = {}
        for node in package.iter():
            name = _tag_name(node.tag)
            if name in {"title", "creator"} and node.text and name not in values:
                values[name] = node.text.strip()
        return {
            key: value
            for key, value in {
                "title": values.get("title"),
                "author": values.get("creator"),
            }.items()
            if value
        }


def _cbz_metadata(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        name = next(
            (
                value
                for value in archive.namelist()
                if value.casefold().endswith("comicinfo.xml")
            ),
            None,
        )
        if not name:
            return {}
        root = ElementTree.fromstring(archive.read(name))
        values = {
            _tag_name(node.tag): (node.text or "").strip()
            for node in root.iter()
            if node.text
        }
        return {
            key: value
            for key, value in {
                "title": values.get("title"),
                "author": values.get("writer"),
                "series": values.get("series"),
                "sequence": values.get("number"),
            }.items()
            if value
        }


def _audio_metadata(path: Path, *, multiple_files: bool) -> dict[str, str]:
    try:
        from mutagen import File as MutagenFile

        media = MutagenFile(path, easy=True)
    except Exception:  # noqa: BLE001 - malformed tags must fall back to the path
        return {}
    if media is None or media.tags is None:
        return {}
    tags = media.tags
    title = (
        _first_text(tags.get("album"))
        if multiple_files
        else _first_text(tags.get("title")) or _first_text(tags.get("album"))
    )
    author = _first_text(tags.get("albumartist")) or _first_text(tags.get("artist"))
    return {
        key: value for key, value in {"title": title, "author": author}.items() if value
    }


def read_book_metadata(paths: list[Path]) -> dict[str, str]:
    for path in paths:
        try:
            suffix = path.suffix.casefold()
            if suffix == ".epub":
                values = _epub_metadata(path)
            elif suffix == ".cbz":
                values = _cbz_metadata(path)
            elif suffix in {".m4b", ".m4a", ".mp4", ".mp3", ".ogg"}:
                values = _audio_metadata(path, multiple_files=len(paths) > 1)
            else:
                values = {}
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
            values = {}
        if values:
            return values
    return {}


def _clean_label(value: str) -> str:
    value = ORDER_PREFIX.sub("", value)
    value = re.sub(r"[_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    return value or "Untitled book"


def _sequence(value: str) -> str | None:
    match = ORDER_PREFIX.match(value)
    return match.group(1) if match else None


def _strip_known_author(title: str, authors: list[str]) -> tuple[str, str | None]:
    for author in authors:
        escaped = re.escape(author.strip())
        prefix = re.compile(rf"^{escaped}\s*[-–—_:]\s*", re.IGNORECASE)
        suffix = re.compile(rf"\s*[-–—_:]\s*{escaped}$", re.IGNORECASE)
        if prefix.search(title):
            return _clean_label(prefix.sub("", title)), author
        if suffix.search(title):
            return _clean_label(suffix.sub("", title)), author
    return title, None


def _common_directory_parts(paths: list[Path]) -> tuple[str, ...]:
    if not paths:
        return ()
    common: list[str] = []
    maximum = min(len(path.parts) - 1 for path in paths)
    for index in range(maximum):
        values = {path.parts[index].casefold() for path in paths}
        if len(values) != 1:
            break
        common.append(paths[0].parts[index])
    return tuple(common)


def _relative_after_common(path: Path, common: tuple[str, ...]) -> Path:
    return Path(*path.parts[len(common) :])


def _destination_relative(path: Path) -> Path:
    parent = path.parent.name
    match = DISC_PATTERN.fullmatch(parent)
    if match:
        return Path(f"Disc {match.group(1)}") / path.name
    return Path(path.name)


def _safe_torrent_path(name: str) -> Path:
    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." or ":" in part for part in parts):
        raise ValueError(f"unsafe torrent path: {name!r}")
    return Path(*parts)


def _book_folder(name: str) -> bool:
    return (
        name.casefold() not in STRUCTURAL_FOLDERS
        and not DISC_PATTERN.fullmatch(name)
        and not PART_PATTERN.search(name)
    )


def _preferred_group_files(
    paths: list[Path],
    audio_types: tuple[str, ...],
    ebook_types: tuple[str, ...],
) -> list[Path]:
    selected: list[Path] = []
    for wanted in (audio_types, ebook_types):
        extension = next(
            (
                "." + value.casefold().lstrip(".")
                for value in wanted
                if any(
                    path.suffix.casefold() == "." + value.casefold().lstrip(".")
                    for path in paths
                )
            ),
            None,
        )
        if extension:
            selected.extend(
                path for path in paths if path.suffix.casefold() == extension
            )
    return list(dict.fromkeys(selected))


def _collection_signal(meta: dict[str, Any]) -> bool:
    category = meta.get("cat") if isinstance(meta.get("cat"), dict) else {}
    text = f"{meta.get('title', '')} {category.get('name', '')}"
    return bool(COLLECTION_PATTERN.search(text))


def _book_meta(
    parent_meta: dict[str, Any],
    *,
    label: str,
    selected_paths: list[Path],
    source_paths: list[Path],
) -> dict[str, Any]:
    embedded = read_book_metadata(source_paths)
    known_authors = [str(value) for value in parent_meta.get("authors", []) if value]
    title = _clean_label(embedded.get("title") or label)
    title, matched_author = _strip_known_author(title, known_authors)
    embedded_author = embedded.get("author")
    known_embedded = next(
        (
            author
            for author in known_authors
            if embedded_author and author.casefold() == embedded_author.casefold()
        ),
        None,
    )
    embedded_is_authoritative = any(
        path.suffix.casefold() in {".epub", ".cbz"} for path in selected_paths
    )
    author = (
        embedded_author
        if embedded_author and embedded_is_authoritative
        else known_authors[0]
        if len(known_authors) == 1
        else known_embedded or embedded_author or matched_author
    )
    meta = deepcopy(parent_meta)
    meta["title"] = title
    meta["authors"] = [author] if author else []
    if len(meta.get("narrators", [])) > 1:
        meta["narrators"] = []
    if embedded.get("series"):
        meta["series"] = [
            {
                "name": embedded["series"],
                "entries": [embedded["sequence"]] if embedded.get("sequence") else [],
            }
        ]
    elif meta.get("series"):
        sequence = _sequence(label)
        meta["series"] = [dict(value) for value in meta["series"]]
        meta["series"][0]["entries"] = [sequence] if sequence else []
    meta["filetypes"] = list(
        dict.fromkeys(path.suffix.casefold().lstrip(".") for path in selected_paths)
    )
    meta["num_files"] = len(selected_paths)
    meta["size"] = sum(
        path.stat().st_size for path in source_paths if path.exists() and path.is_file()
    )
    meta["source"] = "MamCollection"
    meta["collection_parent"] = {
        "mam_id": parent_meta.get("mam_id"),
        "title": parent_meta.get("title"),
    }
    return meta


def detect_collection_books(
    files: list[dict[str, Any]],
    *,
    download_root: Path,
    parent_meta: dict[str, Any],
    audio_types: tuple[str, ...],
    ebook_types: tuple[str, ...],
) -> list[CollectionBook]:
    audio_extensions = {"." + value.casefold().lstrip(".") for value in audio_types}
    ebook_extensions = {"." + value.casefold().lstrip(".") for value in ebook_types}
    allowed = audio_extensions | ebook_extensions
    torrent_paths = []
    for row in files:
        path = _safe_torrent_path(str(row.get("name", "")))
        if path.suffix.casefold() in allowed:
            torrent_paths.append(path)
    if len(torrent_paths) < 2:
        return []

    common = _common_directory_parts(torrent_paths)
    relatives = {path: _relative_after_common(path, common) for path in torrent_paths}
    folder_names = {
        relative.parts[0]
        for relative in relatives.values()
        if len(relative.parts) >= 2 and _book_folder(relative.parts[0])
    }
    folder_layout = len(folder_names) >= 2 and all(
        len(relative.parts) >= 2 and _book_folder(relative.parts[0])
        for relative in relatives.values()
    )

    grouped: dict[str, list[Path]] = {}
    labels: dict[str, str] = {}
    detection = "folders" if folder_layout else "flat files"
    if folder_layout:
        for path, relative in relatives.items():
            label = relative.parts[0]
            key = label.casefold()
            labels[key] = label
            grouped.setdefault(key, []).append(path)
    else:
        suffixes = {path.suffix.casefold() for path in torrent_paths}
        labels_list = [_clean_label(path.stem) for path in torrent_paths]
        flat_is_safe = (
            suffixes.issubset(ebook_extensions)
            or suffixes.issubset(SINGLE_FILE_AUDIO | ebook_extensions)
        ) and not any(PART_PATTERN.search(label) for label in labels_list)
        if not flat_is_safe:
            return []
        for path, label in zip(torrent_paths, labels_list, strict=True):
            key = re.sub(r"\W+", " ", label).casefold().strip()
            labels[key] = label
            grouped.setdefault(key, []).append(path)

    if len(grouped) < 2:
        return []
    if (
        not folder_layout
        and not _collection_signal(parent_meta)
        # Flat ebooks and single-container audiobooks are independently usable books,
        # even when the uploader did not label the torrent as a collection.
        and not all(
            path.suffix.casefold() in ebook_extensions | SINGLE_FILE_AUDIO
            for path in torrent_paths
        )
    ):
        return []

    books: list[CollectionBook] = []
    for key, group_paths in grouped.items():
        selected = _preferred_group_files(group_paths, audio_types, ebook_types)
        if not selected:
            continue
        if folder_layout:
            relative_files = [
                _destination_relative(Path(*relatives[path].parts[1:]))
                for path in selected
            ]
        else:
            relative_files = [Path(path.name) for path in selected]
        source_paths = [download_root / path for path in selected]
        meta = _book_meta(
            parent_meta,
            label=labels[key],
            selected_paths=selected,
            source_paths=source_paths,
        )
        books.append(
            CollectionBook(
                title=str(meta["title"]),
                meta=meta,
                files=tuple(zip(selected, relative_files, strict=True)),
                detection=detection,
            )
        )

    titles: dict[str, int] = {}
    unique_books: list[CollectionBook] = []
    for book in books:
        key = book.title.casefold()
        occurrence = titles.get(key, 0) + 1
        titles[key] = occurrence
        if occurrence == 1:
            unique_books.append(book)
            continue
        meta = deepcopy(book.meta)
        meta["title"] = f"{book.title} ({occurrence})"
        unique_books.append(
            CollectionBook(
                title=str(meta["title"]),
                meta=meta,
                files=book.files,
                detection=book.detection,
            )
        )
    return unique_books if len(unique_books) >= 2 else []
