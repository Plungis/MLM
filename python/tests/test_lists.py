from __future__ import annotations

from mlm.lists import goodreads_list_id


def test_goodreads_list_id_legacy_shape() -> None:
    assert (
        goodreads_list_id(
            "https://www.goodreads.com/review/list_rss/12345?shelf=want-to-read"
        )
        == "12345:want-to-read"
    )
