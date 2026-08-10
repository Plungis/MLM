import unittest

from mlm.modules.absidekick.core import (
    DEFAULT_SETTINGS,
    add_remove_tags,
    build_review_row,
    candidate_metadata_payload,
    rank_candidates,
    should_process_item,
    summarize_item,
)


def sample_item(tags=None):
    return {
        "id": "li_test",
        "mediaType": "book",
        "isMissing": False,
        "isInvalid": False,
        "path": "/audiobooks/Terry Goodkind/Sword of Truth/Wizards First Rule",
        "media": {
            "duration": 12000,
            "coverPath": None,
            "tags": tags or [],
            "metadata": {
                "title": "Wizard's First Rule",
                "authorName": "Terry Goodkind",
                "seriesName": "Sword of Truth",
                "narratorName": "Sam Tsoutsouvas",
                "publishedYear": "1994",
                "asin": None,
                "isbn": None,
            },
        },
    }


class ScoringTests(unittest.TestCase):
    def test_best_candidate_wins(self):
        candidates = [
            {
                "title": "Stone of Tears",
                "author": "Terry Goodkind",
                "publishedYear": "1995",
            },
            {
                "title": "Wizards First Rule",
                "author": "Terry Goodkind",
                "series": [{"name": "Sword of Truth", "sequence": "1"}],
                "narrator": "Sam Tsoutsouvas",
                "publishedYear": "1994",
                "duration": 200,
            },
        ]
        ranked = rank_candidates(sample_item(), candidates, DEFAULT_SETTINGS)
        self.assertEqual(ranked[0]["candidate"]["title"], "Wizards First Rule")
        self.assertGreaterEqual(ranked[0]["score"], 90)

    def test_unprocessed_skips_processed_tags(self):
        ok, reason = should_process_item(
            sample_item(["ABSidekick: AutoMatched"]), DEFAULT_SETTINGS
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "already processed")

    def test_review_mode_requires_review_tag(self):
        settings = {
            **DEFAULT_SETTINGS,
            "targeting": {**DEFAULT_SETTINGS["targeting"], "mode": "review"},
        }
        ok, reason = should_process_item(
            sample_item(["ABSidekick: Needs Review"]), settings
        )
        self.assertTrue(ok)
        ok, reason = should_process_item(
            sample_item(["ABSidekick: AutoMatch Unmatched"]), settings
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "not tagged review")

    def test_review_row_keeps_candidate_options(self):
        ranked = rank_candidates(
            sample_item(),
            [
                {"title": "Wrong Book", "author": "Someone Else"},
                {"title": "Wizards First Rule", "author": "Terry Goodkind"},
            ],
            DEFAULT_SETTINGS,
        )
        row = build_review_row(sample_item(), ranked, DEFAULT_SETTINGS)
        self.assertEqual(row["item"]["title"], "Wizard's First Rule")
        self.assertEqual(len(row["candidates"]), 2)
        self.assertEqual(row["item"]["series"], "Sword of Truth")
        self.assertEqual(row["item"]["narrator"], "Sam Tsoutsouvas")

    def test_summarize_item_exposes_compare_card_fields(self):
        item = summarize_item(sample_item(["ABSidekick: Needs Review"]))
        self.assertEqual(item["title"], "Wizard's First Rule")
        self.assertEqual(item["author"], "Terry Goodkind")
        self.assertEqual(item["year"], "1994")
        self.assertEqual(item["tags"], ["ABSidekick: Needs Review"])
        self.assertEqual(item["coverUrl"], "/api/absidekick/item-cover/li_test")

    def test_add_remove_tags_preserves_other_tags(self):
        tags = add_remove_tags(
            ["Favorite", "ABSidekick: AutoMatch Unmatched"],
            add=["ABSidekick: AutoMatched"],
            remove=["ABSidekick: AutoMatch Unmatched"],
        )
        self.assertEqual(tags, ["Favorite", "ABSidekick: AutoMatched"])

    def test_metadata_payload_does_not_overwrite_by_default(self):
        payload = candidate_metadata_payload(
            {"title": "Existing Title", "authorName": "Existing Author", "asin": None},
            {"title": "New Title", "author": "New Author", "asin": "B000123"},
            overwrite=False,
        )
        self.assertNotIn("title", payload)
        self.assertNotIn("authors", payload)
        self.assertEqual(payload["asin"], "B000123")


if __name__ == "__main__":
    unittest.main()
