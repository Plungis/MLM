import unittest
from copy import deepcopy

from mlm.modules.absidekick.core import (
    DEFAULT_SETTINGS,
    ABSAPIError,
    ABSClient,
    MatchJob,
    add_remove_tags,
    build_review_row,
    candidate_metadata_payload,
    match_decision,
    normalize_title,
    rank_candidates,
    score_candidate,
    search_candidates,
    search_review_candidates,
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
    def test_title_normalization_handles_edition_noise_unicode_and_roman_numbers(self):
        self.assertEqual(
            normalize_title("The Café—Book IV (Unabridged Audiobook Edition)"),
            "cafe 4",
        )

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

    def test_sparse_provider_metadata_does_not_penalize_obvious_match(self):
        ranked = rank_candidates(
            sample_item(),
            [{"title": "Wizard's First Rule", "author": "Terry Goodkind"}],
            DEFAULT_SETTINGS,
        )
        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(ranked[0]["parts"]["year"], None)
        self.assertEqual(ranked[0]["parts"]["duration"], None)
        self.assertGreaterEqual(ranked[0]["score"], 99)
        self.assertEqual(decision["action"], "auto")

    def test_title_subtitle_and_unabridged_noise_still_match(self):
        candidate = {
            "title": "Wizard's First Rule: Sword of Truth, Book I",
            "author": "Terry Goodkind",
            "narrator": "Sam Tsoutsouvas",
        }
        scored = score_candidate(sample_item(), candidate, DEFAULT_SETTINGS)

        self.assertGreaterEqual(scored["parts"]["title"], 92)
        self.assertEqual(match_decision([scored], DEFAULT_SETTINGS)["action"], "auto")

    def test_contained_but_different_title_is_not_automatched(self):
        item = sample_item()
        item["media"]["metadata"]["title"] = "Dune Messiah"
        item["media"]["metadata"]["authorName"] = "Frank Herbert"
        ranked = rank_candidates(
            item,
            [{"title": "Dune", "author": "Frank Herbert"}],
            DEFAULT_SETTINGS,
        )

        self.assertLess(ranked[0]["parts"]["title"], 86)
        self.assertEqual(match_decision(ranked, DEFAULT_SETTINGS)["action"], "review")

    def test_author_contradiction_forces_review_even_with_exact_title(self):
        ranked = rank_candidates(
            sample_item(),
            [{"title": "Wizard's First Rule", "author": "A Different Author"}],
            DEFAULT_SETTINGS,
        )
        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(decision["action"], "review")
        self.assertTrue(
            any("author evidence" in reason for reason in decision["reasons"])
        )

    def test_close_runner_up_blocks_automatic_write(self):
        candidates = [
            {
                "title": "Wizard's First Rule",
                "author": "Terry Goodkind",
                "publishedYear": "1994",
            },
            {
                "title": "Wizards First Rule: Special Edition",
                "author": "Terry Goodkind",
                "publishedYear": "1994",
            },
        ]
        ranked = rank_candidates(sample_item(), candidates, DEFAULT_SETTINGS)
        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(decision["action"], "review")
        self.assertTrue(
            any("winner margin" in reason for reason in decision["reasons"])
        )

    def test_exact_identifier_can_confirm_sparse_candidate(self):
        item = sample_item()
        item["media"]["metadata"]["asin"] = "B00-ABC-123"
        ranked = rank_candidates(
            item,
            [{"title": "Wizards First Rule", "asin": "B00ABC123"}],
            DEFAULT_SETTINGS,
        )

        self.assertEqual(ranked[0]["exactIdentifiers"], ["ASIN"])
        self.assertEqual(match_decision(ranked, DEFAULT_SETTINGS)["action"], "auto")

    def test_identifier_conflict_blocks_automatic_write(self):
        item = sample_item()
        item["media"]["metadata"]["asin"] = "B00ABC123"
        ranked = rank_candidates(
            item,
            [
                {
                    "title": "Wizard's First Rule",
                    "author": "Terry Goodkind",
                    "asin": "B00WRONG",
                }
            ],
            DEFAULT_SETTINGS,
        )

        decision = match_decision(ranked, DEFAULT_SETTINGS)
        self.assertEqual(decision["action"], "review")
        self.assertIn("ASIN differs", decision["reasons"])

    def test_series_sequence_conflict_blocks_automatic_write(self):
        item = sample_item()
        item["media"]["metadata"]["series"] = [
            {"name": "Sword of Truth", "sequence": "1"}
        ]
        ranked = rank_candidates(
            item,
            [
                {
                    "title": "Wizard's First Rule",
                    "author": "Terry Goodkind",
                    "series": [{"name": "Sword of Truth", "sequence": "2"}],
                }
            ],
            DEFAULT_SETTINGS,
        )

        decision = match_decision(ranked, DEFAULT_SETTINGS)
        self.assertEqual(decision["action"], "review")
        self.assertIn("series sequence differs", decision["reasons"])

    def test_adaptive_search_broadens_and_uses_fallback_provider(self):
        class AdaptiveClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                if params["provider"] == "google":
                    return [
                        {
                            "title": "Wizard's First Rule",
                            "author": "Terry Goodkind",
                        }
                    ]
                return []

        client = AdaptiveClient()
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["connection"]["provider"] = "audible"
        settings["matching"]["automaticFallbackProviders"] = True
        candidates = search_candidates(client, sample_item(), settings)

        self.assertEqual(candidates[0]["title"], "Wizard's First Rule")
        self.assertTrue(any(query["provider"] == "audible" for query in client.queries))
        self.assertTrue(any(query["provider"] == "google" for query in client.queries))

    def test_automatic_fallback_providers_are_disabled_by_default(self):
        class EmptyClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                return []

        client = EmptyClient()
        search_candidates(client, sample_item(), DEFAULT_SETTINGS)

        self.assertTrue(client.queries)
        self.assertEqual({query["provider"] for query in client.queries}, {"audible"})

    def test_candidate_stops_broader_searches(self):
        class PreciseClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                return [{"title": "Wizard's First Rule", "author": "Terry Goodkind"}]

        client = PreciseClient()
        candidates = search_candidates(client, sample_item(), DEFAULT_SETTINGS)

        self.assertEqual(len(client.queries), 1)
        self.assertEqual(candidates[0]["title"], "Wizard's First Rule")

    def test_track_number_is_removed_after_empty_precise_search(self):
        class NumberedClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                if params["title"] == "Northern Lights":
                    return [{"title": "Northern Lights", "author": "Philip Pullman"}]
                return []

        item = sample_item()
        item["media"]["metadata"]["title"] = "01 Northern Lights"
        item["media"]["metadata"]["authorName"] = "Philip Pullman"
        client = NumberedClient()
        candidates = search_candidates(client, item, DEFAULT_SETTINGS)

        self.assertEqual(
            [query["title"] for query in client.queries],
            ["Northern Lights"],
        )
        self.assertEqual(candidates[0]["title"], "Northern Lights")

    def test_timed_out_provider_is_disabled_without_retries(self):
        client = ABSClient("http://localhost:13378", "token", max_retries=5)
        calls = []

        def fail_request(*args, **kwargs):
            calls.append(kwargs)
            raise ABSAPIError("timed out")

        client.request = fail_request
        first = search_candidates(client, sample_item(), DEFAULT_SETTINGS)
        second = search_candidates(client, sample_item(), DEFAULT_SETTINGS)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_retries"], 0)
        self.assertEqual(calls[0]["timeout_seconds"], 12)
        self.assertEqual(first.diagnostics[0]["provider"], "audible")
        self.assertEqual(second.diagnostics, [])

    def test_primary_provider_timeout_stops_job_without_tagging_remaining_items(self):
        class DownClient(ABSClient):
            def __init__(self):
                super().__init__("http://localhost:13378", "token")
                self.search_calls = 0

            def get(self, path, params=None):
                if path.endswith("/items"):
                    first = sample_item()
                    second = sample_item()
                    second["id"] = "li_second"
                    return {"results": [first, second], "total": 2}
                raise AssertionError(path)

            def request(self, *args, **kwargs):
                self.search_calls += 1
                raise ABSAPIError("timed out")

        settings = deepcopy(DEFAULT_SETTINGS)
        settings["connection"]["libraryId"] = "library"
        client = DownClient()
        job = MatchJob("test", client, settings)

        job.run()

        snapshot = job.snapshot()
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["stats"]["processed"], 1)
        self.assertEqual(snapshot["stats"]["errors"], 1)
        self.assertEqual(client.search_calls, 1)
        self.assertIn(
            "No remaining items were changed", snapshot["logs"][-1]["message"]
        )

    def test_manual_review_search_uses_custom_terms_and_rescores_results(self):
        class SearchClient:
            def __init__(self):
                self.search_params = None

            def get(self, path, params=None):
                if path == "/api/items/li_test":
                    return sample_item(["ABSidekick: Needs Review"])
                if path == "/api/search/books":
                    self.search_params = params
                    return [
                        {"title": "Stone of Tears", "author": "Terry Goodkind"},
                        {
                            "title": "Wizards First Rule",
                            "author": "Terry Goodkind",
                            "publishedYear": "1994",
                        },
                    ]
                raise AssertionError(path)

        client = SearchClient()
        result = search_review_candidates(
            client,
            "li_test",
            {
                "title": "wizards first",
                "author": "",
                "provider": "openlibrary",
                "limit": 20,
            },
            DEFAULT_SETTINGS,
        )

        self.assertEqual(
            client.search_params,
            {
                "title": "wizards first",
                "author": "",
                "provider": "openlibrary",
                "limit": 20,
            },
        )
        self.assertEqual(result["resultCount"], 2)
        self.assertEqual(
            result["candidates"][0]["candidate"]["title"],
            "Wizards First Rule",
        )
        self.assertEqual(result["candidates"][0]["searchSource"], "manual")
        self.assertEqual(result["candidates"][0]["searchProvider"], "openlibrary")
        self.assertEqual(result["item"]["id"], "li_test")
        self.assertEqual(result["manualMatch"]["status"], result["decision"]["action"])
        self.assertIs(
            result["manualMatch"]["isConfidentMatch"],
            result["decision"]["action"] == "auto",
        )
        self.assertEqual(
            result["manualMatch"]["bestCandidate"]["candidate"]["title"],
            "Wizards First Rule",
        )

    def test_manual_review_search_explicitly_reports_no_match(self):
        class EmptySearchClient:
            def get(self, path, params=None):
                if path == "/api/items/li_test":
                    return sample_item(["ABSidekick: Needs Review"])
                if path == "/api/search/books":
                    return []
                raise AssertionError(path)

        result = search_review_candidates(
            EmptySearchClient(),
            "li_test",
            {"title": "missing book", "provider": "google"},
            DEFAULT_SETTINGS,
        )

        self.assertEqual(result["resultCount"], 0)
        self.assertEqual(result["manualMatch"]["status"], "unmatched")
        self.assertFalse(result["manualMatch"]["isConfidentMatch"])
        self.assertTrue(result["manualMatch"]["requiresReview"])
        self.assertIsNone(result["manualMatch"]["bestCandidate"])
        self.assertIn("No metadata candidates", result["manualMatch"]["message"])

    def test_manual_review_search_scores_against_edited_fields(self):
        class EditedSearchClient:
            def get(self, path, params=None):
                if path == "/api/items/li_test":
                    return sample_item(["ABSidekick: Needs Review"])
                if path == "/api/search/books":
                    return [
                        {"title": "A Completely Different Book", "author": "New Author"},
                        {"title": "Wizard's First Rule", "author": "Terry Goodkind"},
                    ]
                raise AssertionError(path)

        result = search_review_candidates(
            EditedSearchClient(),
            "li_test",
            {
                "title": "A Completely Different Book",
                "author": "New Author",
                "provider": "google",
            },
            DEFAULT_SETTINGS,
        )

        self.assertEqual(
            result["candidates"][0]["candidate"]["title"],
            "A Completely Different Book",
        )
        self.assertEqual(
            result["manualMatch"]["scoredAgainst"],
            {"title": "A Completely Different Book", "author": "New Author"},
        )

    def test_manual_review_search_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "unknown Audiobookshelf"):
            search_review_candidates(
                object(),
                "li_test",
                {"title": "Wizard", "provider": "not-real"},
                DEFAULT_SETTINGS,
            )


if __name__ == "__main__":
    unittest.main()
