from __future__ import annotations

import hashlib


class TorrentFormatError(ValueError):
    pass


def _skip_value(data: bytes, offset: int) -> int:
    if offset >= len(data):
        raise TorrentFormatError("unexpected end of bencoded data")
    marker = data[offset : offset + 1]
    if marker == b"i":
        end = data.find(b"e", offset + 1)
        if end < 0:
            raise TorrentFormatError("unterminated integer")
        int(data[offset + 1 : end])
        return end + 1
    if marker == b"l":
        offset += 1
        while data[offset : offset + 1] != b"e":
            offset = _skip_value(data, offset)
        return offset + 1
    if marker == b"d":
        offset += 1
        while data[offset : offset + 1] != b"e":
            offset = _skip_value(data, offset)
            offset = _skip_value(data, offset)
        return offset + 1
    if b"0" <= marker <= b"9":
        colon = data.find(b":", offset)
        if colon < 0:
            raise TorrentFormatError("invalid byte string")
        length = int(data[offset:colon])
        end = colon + 1 + length
        if end > len(data):
            raise TorrentFormatError("truncated byte string")
        return end
    raise TorrentFormatError(f"invalid bencode marker at byte {offset}")


def info_hash(torrent_file: bytes) -> str:
    """Return the BitTorrent v1 SHA-1 info hash without re-encoding the payload."""
    if not torrent_file.startswith(b"d"):
        raise TorrentFormatError("torrent root is not a dictionary")
    offset = 1
    while torrent_file[offset : offset + 1] != b"e":
        key_start = offset
        key_end = _skip_value(torrent_file, key_start)
        colon = torrent_file.find(b":", key_start)
        key = torrent_file[colon + 1 : key_end]
        value_start = key_end
        value_end = _skip_value(torrent_file, value_start)
        if key == b"info":
            return hashlib.sha1(torrent_file[value_start:value_end]).hexdigest()
        offset = value_end
    raise TorrentFormatError("torrent has no info dictionary")
