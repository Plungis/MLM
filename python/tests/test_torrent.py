from __future__ import annotations

import hashlib

from mlm.torrent import info_hash


def test_info_hash_uses_original_info_bytes() -> None:
    info = b"d6:lengthi12e4:name4:booke"
    torrent = b"d4:info" + info + b"e"

    assert info_hash(torrent) == hashlib.sha1(info).hexdigest()
