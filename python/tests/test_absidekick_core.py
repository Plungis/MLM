import json
import unittest
import urllib.parse
from copy import deepcopy
from unittest.mock import patch

from mlm.modules.absidekick.core import (
    DEFAULT_SETTINGS,
    ABSAPIError,
    ABSClient,
    MatchJob,
    add_remove_tags,
    apply_match,
    build_review_row,
    candidate_metadata_payload,
    clean_search_title,
    google_books_key_fingerprint,
    match_decision,
    normalize_title,
    public_settings,
    rank_candidates,
    scan_review_items,
    score_candidate,
    search_candidates,
    search_review_candidates,
    should_process_item,
    summarize_item,
)
from mlm.modules.absidekick.core import (
    test_google_books_api_key as validate_google_books_api_key,
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


class GoogleBooksProviderTests(unittest.TestCase):
    def test_public_settings_hide_google_key_and_require_matching_validation(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["providers"].update(
            {
                "googleBooksApiKey": "private-google-key",
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(
                    "private-google-key"
                ),
            }
        )

        public = public_settings(settings, has_token=False)

        self.assertNotIn("googleBooksApiKey", public["providers"])
        self.assertNotIn("googleBooksApiKeyFingerprint", public["providers"])
        self.assertTrue(public["providers"]["hasGoogleBooksApiKey"])
        self.assertTrue(public["providers"]["googleBooksReady"])

        settings["providers"]["googleBooksApiKey"] = "replaced-without-test"
        self.assertFalse(
            public_settings(settings, has_token=False)["providers"]["googleBooksReady"]
        )

    @patch("mlm.modules.absidekick.core.urllib.request.urlopen")
    def test_google_search_never_contacts_google_without_validated_key(self, urlopen):
        client = ABSClient(
            "http://localhost:13378",
            "abs-token",
            google_books_api_key="saved-but-untested",
            google_books_ready=False,
        )

        with self.assertRaisesRegex(ABSAPIError, "No Google request was sent"):
            client.search_books(
                {"provider": "google", "title": "The Hobbit", "limit": 5},
                timeout_seconds=12,
            )

        urlopen.assert_not_called()

    @patch("mlm.modules.absidekick.core.urllib.request.urlopen")
    def test_validated_google_search_calls_native_api_and_maps_book(self, urlopen):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "items": [
                            {
                                "id": "google-volume-1",
                                "volumeInfo": {
                                    "title": "The Hobbit",
                                    "subtitle": "There and Back Again",
                                    "authors": ["J. R. R. Tolkien"],
                                    "publishedDate": "1937-09-21",
                                    "publisher": "George Allen & Unwin",
                                    "industryIdentifiers": [
                                        {
                                            "type": "ISBN_13",
                                            "identifier": "9780547928227",
                                        }
                                    ],
                                    "imageLinks": {
                                        "thumbnail": "http://books.google.com/cover.jpg"
                                    },
                                },
                            }
                        ]
                    }
                ).encode("utf-8")

        urlopen.return_value = Response()
        client = ABSClient(
            "http://localhost:13378",
            "abs-token",
            google_books_api_key="validated-key",
            google_books_ready=True,
        )

        results = client.search_books(
            {
                "provider": "google",
                "title": "The Hobbit",
                "author": "Tolkien",
                "limit": 5,
            },
            timeout_seconds=12,
        )

        request = urlopen.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(query["key"], ["validated-key"])
        self.assertEqual(query["maxResults"], ["5"])
        self.assertIn("intitle:The Hobbit", query["q"][0])
        self.assertEqual(results[0]["title"], "The Hobbit")
        self.assertEqual(results[0]["author"], "J. R. R. Tolkien")
        self.assertEqual(results[0]["publishedYear"], "1937")
        self.assertEqual(results[0]["isbn"], "9780547928227")
        self.assertEqual(results[0]["cover"], "https://books.google.com/cover.jpg")

    @patch("mlm.modules.absidekick.core.search_google_books")
    def test_transient_google_error_retries_once_without_disabling(self, search):
        search.side_effect = [
            ABSAPIError("Google Books returned HTTP 503.", status=503),
            [{"title": "The Hobbit", "author": "J. R. R. Tolkien"}],
        ]
        client = ABSClient(
            "http://localhost:13378",
            "abs-token",
            google_books_api_key="validated-key",
            google_books_ready=True,
        )

        with patch("mlm.modules.absidekick.core.time.sleep") as sleep:
            results = client.search_books(
                {"provider": "google", "title": "The Hobbit", "limit": 5},
                timeout_seconds=12,
            )

        self.assertEqual(results[0]["title"], "The Hobbit")
        self.assertEqual(search.call_count, 2)
        sleep.assert_called_once_with(0.5)
        self.assertNotIn("google", client.disabled_search_providers)
        self.assertNotIn("google", client.transient_search_failures)

    @patch("mlm.modules.absidekick.core.search_google_books")
    def test_single_exhausted_transient_google_search_retries_next_item(self, search):
        search.side_effect = ABSAPIError("Google Books returned HTTP 503.", status=503)
        client = ABSClient(
            "http://localhost:13378",
            "abs-token",
            google_books_api_key="validated-key",
            google_books_ready=True,
        )
        params = {"provider": "google", "title": "The Hobbit", "limit": 5}

        with (
            patch("mlm.modules.absidekick.core.time.sleep"),
            self.assertRaises(ABSAPIError),
        ):
            client.search_books(params, timeout_seconds=12)

        self.assertNotIn("google", client.disabled_search_providers)
        self.assertEqual(client.transient_search_failures["google"], 1)

        search.side_effect = None
        search.return_value = [{"title": "The Hobbit"}]
        results = client.search_books(params, timeout_seconds=12)

        self.assertEqual(results[0]["title"], "The Hobbit")
        self.assertNotIn("google", client.transient_search_failures)

    @patch("mlm.modules.absidekick.core.search_google_books")
    def test_three_exhausted_transient_google_searches_open_circuit(self, search):
        search.side_effect = ABSAPIError("Google Books returned HTTP 503.", status=503)
        client = ABSClient(
            "http://localhost:13378",
            "abs-token",
            google_books_api_key="validated-key",
            google_books_ready=True,
        )
        params = {"provider": "google", "title": "The Hobbit", "limit": 5}

        with patch("mlm.modules.absidekick.core.time.sleep"):
            for _ in range(3):
                with self.assertRaises(ABSAPIError):
                    client.search_books(params, timeout_seconds=12)

        self.assertIn("google", client.disabled_search_providers)
        self.assertIn(
            "3 consecutive transient failures",
            client.disabled_search_providers["google"],
        )
        self.assertEqual(search.call_count, 6)

    @patch("mlm.modules.absidekick.core.search_google_books")
    def test_google_auth_error_disables_immediately_without_retry(self, search):
        search.side_effect = ABSAPIError("Google rejected the API key.", status=403)
        client = ABSClient(
            "http://localhost:13378",
            "abs-token",
            google_books_api_key="validated-key",
            google_books_ready=True,
        )

        with self.assertRaises(ABSAPIError):
            client.search_books(
                {"provider": "google", "title": "The Hobbit", "limit": 5},
                timeout_seconds=12,
            )

        self.assertEqual(search.call_count, 1)
        self.assertIn("google", client.disabled_search_providers)

    @patch("mlm.modules.absidekick.core._google_books_payload")
    def test_live_key_test_retries_one_transient_google_failure(self, payload):
        payload.side_effect = [
            ABSAPIError("Google Books returned HTTP 503.", status=503),
            {"items": [{"id": "sample"}]},
        ]

        with patch("mlm.modules.absidekick.core.time.sleep") as sleep:
            result = validate_google_books_api_key("validated-key", timeout_seconds=12)

        self.assertTrue(result["valid"])
        self.assertEqual(payload.call_count, 2)
        sleep.assert_called_once_with(0.5)


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

    def test_passing_score_explains_which_safety_gate_requires_review(self):
        ranked = rank_candidates(
            sample_item(),
            [{"title": "Wizard's First Rule"}],
            DEFAULT_SETTINGS,
        )

        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(decision["score"], 100)
        self.assertTrue(decision["scorePassed"])
        self.assertFalse(decision["safetyPassed"])
        self.assertEqual(decision["action"], "review")
        self.assertEqual(decision["strongSignalCount"], 1)
        self.assertEqual(decision["policy"]["autoThreshold"], 80)
        self.assertTrue(
            any("only 1 strong signal" in reason for reason in decision["reasons"])
        )

    def test_below_threshold_always_names_the_score_gate(self):
        ranked = [
            {
                "score": 73.23,
                "parts": {"title": 100, "author": 100},
                "conflicts": [],
                "strongSignals": ["title", "author"],
                "exactIdentifiers": [],
                "source": {"author": "Terry Goodkind"},
                "candidate": {
                    "title": "Wizard's First Rule",
                    "author": "Terry Goodkind",
                },
                "search": {},
            }
        ]

        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(decision["action"], "review")
        self.assertFalse(decision["scorePassed"])
        self.assertIn(
            "similarity score 73.23 is below the auto-match threshold 80",
            decision["reasons"],
        )

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

    def test_duplicate_listings_of_same_work_do_not_force_review(self):
        candidates = [
            {
                "title": "Wizard's First Rule",
                "author": "Terry Goodkind",
                "asin": "ABS-EDITION",
            },
            {
                "title": "Wizards First Rule",
                "author": "Terry Goodkind",
                "id": "google-edition",
            },
        ]

        ranked = rank_candidates(sample_item(), candidates, DEFAULT_SETTINGS)
        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(decision["action"], "auto")
        self.assertEqual(decision["equivalentCandidateCount"], 1)
        self.assertEqual(decision["competingCandidateCount"], 0)
        self.assertEqual(decision["margin"], 100)

    def test_richer_duplicate_wins_when_other_provider_omits_author(self):
        candidates = [
            {"title": "Wizard's First Rule", "id": "sparse-result"},
            {
                "title": "Wizard's First Rule",
                "author": "Terry Goodkind",
                "id": "verified-result",
            },
        ]

        ranked = rank_candidates(sample_item(), candidates, DEFAULT_SETTINGS)
        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(ranked[0]["candidate"]["id"], "verified-result")
        self.assertEqual(decision["action"], "auto")
        self.assertEqual(decision["equivalentCandidateCount"], 1)

    def test_same_title_and_author_with_conflicting_series_numbers_stays_review(self):
        candidates = [
            {
                "title": "Wizard's First Rule",
                "author": "Terry Goodkind",
                "series": [{"name": "Sword of Truth", "sequence": "1"}],
            },
            {
                "title": "Wizard's First Rule",
                "author": "Terry Goodkind",
                "series": [{"name": "Sword of Truth", "sequence": "2"}],
            },
        ]

        ranked = rank_candidates(sample_item(), candidates, DEFAULT_SETTINGS)
        decision = match_decision(ranked, DEFAULT_SETTINGS)

        self.assertEqual(decision["action"], "review")
        self.assertEqual(decision["equivalentCandidateCount"], 0)
        self.assertEqual(decision["competingCandidateCount"], 1)
        self.assertIn("meaningfully different", " ".join(decision["reasons"]))

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
        settings["providers"].update(
            {
                "googleBooksApiKey": "tested-key",
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(
                    "tested-key"
                ),
            }
        )
        candidates = search_candidates(client, sample_item(), settings)

        self.assertEqual(candidates[0]["title"], "Wizard's First Rule")
        self.assertTrue(any(query["provider"] == "audible" for query in client.queries))
        self.assertTrue(any(query["provider"] == "google" for query in client.queries))

    def test_tested_google_key_falls_back_after_weak_abs_result(self):
        class FallbackClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                if params["provider"] == "google":
                    return [
                        {
                            "title": "Wizard's First Rule",
                            "author": "Terry Goodkind",
                            "id": "google-correct",
                        }
                    ]
                return [
                    {
                        "title": "A Different Wizard",
                        "author": "Someone Else",
                        "asin": "abs-wrong",
                    }
                ]

        client = FallbackClient()
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["providers"].update(
            {
                "googleBooksApiKey": "tested-key",
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(
                    "tested-key"
                ),
            }
        )
        settings["matching"]["automaticFallbackProviders"] = False

        candidates = search_candidates(client, sample_item(), settings)
        ranked = rank_candidates(sample_item(), candidates, settings)

        self.assertEqual(
            [query["provider"] for query in client.queries], ["audible", "google"]
        )
        self.assertEqual(candidates[0]["id"], "google-correct")
        self.assertEqual(candidates[0]["_absidekickSearch"]["provider"], "google")
        self.assertIn(
            "after no confident Audiobookshelf match",
            candidates[0]["_absidekickSearch"]["strategy"],
        )
        self.assertEqual(
            [attempt["provider"] for attempt in candidates.attempts],
            ["audible", "google"],
        )
        self.assertEqual(candidates.attempts[0]["stage"], "ABS primary search")
        self.assertEqual(candidates.attempts[1]["stage"], "Google second pass")
        self.assertEqual(candidates.attempts[1]["status"], "results")
        self.assertEqual(candidates.attempts[1]["resultCount"], 1)
        self.assertEqual(match_decision(ranked, settings)["action"], "auto")

    def test_tested_google_key_is_not_used_after_confident_abs_result(self):
        class ConfidentClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                return [
                    {
                        "title": "Wizard's First Rule",
                        "author": "Terry Goodkind",
                    }
                ]

        client = ConfidentClient()
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["providers"].update(
            {
                "googleBooksApiKey": "tested-key",
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(
                    "tested-key"
                ),
            }
        )

        candidates = search_candidates(client, sample_item(), settings)

        self.assertEqual(len(candidates), 1)
        self.assertEqual([query["provider"] for query in client.queries], ["audible"])
        self.assertEqual(
            [attempt["provider"] for attempt in candidates.attempts], ["audible"]
        )

    def test_google_second_pass_is_visible_when_it_returns_no_results(self):
        class WeakThenEmptyClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                if params["provider"] == "google":
                    return []
                return [{"title": "A Different Wizard", "author": "Someone Else"}]

        client = WeakThenEmptyClient()
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["providers"].update(
            {
                "googleBooksApiKey": "tested-key",
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(
                    "tested-key"
                ),
            }
        )

        candidates = search_candidates(client, sample_item(), settings)

        self.assertEqual(
            [query["provider"] for query in client.queries], ["audible", "google"]
        )
        self.assertEqual(candidates.attempts[-1]["provider"], "google")
        self.assertEqual(candidates.attempts[-1]["status"], "no_results")
        self.assertEqual(candidates.attempts[-1]["resultCount"], 0)

    def test_untested_google_key_is_never_used_automatically(self):
        class EmptyClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                return []

        client = EmptyClient()
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["providers"]["googleBooksApiKey"] = "not-tested"

        candidates = search_candidates(client, sample_item(), settings)

        self.assertTrue(client.queries)
        self.assertEqual({query["provider"] for query in client.queries}, {"audible"})
        self.assertEqual(candidates.attempts[-1]["provider"], "google")
        self.assertEqual(candidates.attempts[-1]["status"], "skipped")
        self.assertIn("add and test an API key", candidates.attempts[-1]["message"])

    def test_google_fallback_cannot_auto_apply_through_abs_quick_match(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["matching"]["applyMode"] = "quick_match"
        settings["matching"]["strictAutoMatch"] = False
        candidate = {
            "title": "Wizard's First Rule",
            "author": "Terry Goodkind",
            "_absidekickSearch": {
                "provider": "google",
                "strategy": "native Google fallback",
                "quickMatchEligible": False,
            },
        }

        decision = match_decision(
            rank_candidates(sample_item(), [candidate], settings), settings
        )

        self.assertEqual(decision["action"], "review")
        self.assertIn("metadata patch mode", " ".join(decision["reasons"]))

    @patch("mlm.modules.absidekick.core.search_google_books")
    def test_transient_google_fallback_log_says_it_will_retry(self, search):
        search.side_effect = ABSAPIError("Google Books returned HTTP 503.", status=503)
        client = ABSClient(
            "http://localhost:13378",
            "abs-token",
            google_books_api_key="tested-key",
            google_books_ready=True,
        )
        client.request = lambda *args, **kwargs: [
            {
                "title": "A Different Wizard",
                "author": "Someone Else",
                "asin": "abs-wrong",
            }
        ]
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["providers"].update(
            {
                "googleBooksApiKey": "tested-key",
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(
                    "tested-key"
                ),
            }
        )

        with patch("mlm.modules.absidekick.core.time.sleep"):
            candidates = search_candidates(client, sample_item(), settings)

        self.assertEqual(len(candidates.diagnostics), 1)
        self.assertIn(
            "will retry on the next item", candidates.diagnostics[0]["message"]
        )
        self.assertNotIn("disabled for the rest", candidates.diagnostics[0]["message"])
        self.assertEqual(candidates.diagnostics[0]["error"], str(search.side_effect))

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

    def test_parenthesized_track_numbers_are_removed_from_search_title(self):
        self.assertEqual(clean_search_title("01(3) Octopussy"), "Octopussy")
        self.assertEqual(clean_search_title("02 (7) Thunderball"), "Thunderball")
        self.assertEqual(clean_search_title("11(22)63"), "11(22)63")

    def test_cleaned_google_result_can_auto_match_parenthesized_track_title(self):
        class GoogleSecondPassClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                if params["provider"] == "google":
                    return [{"title": "Octopussy", "author": "Ian Fleming"}]
                return [{"title": "A Different Book", "author": "Ian Fleming"}]

        item = sample_item()
        item["media"]["metadata"]["title"] = "01(3) Octopussy"
        item["media"]["metadata"]["authorName"] = "Ian Fleming"
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["providers"].update(
            {
                "googleBooksApiKey": "tested-key",
                "googleBooksApiKeyValidated": True,
                "googleBooksApiKeyFingerprint": google_books_key_fingerprint(
                    "tested-key"
                ),
            }
        )
        client = GoogleSecondPassClient()

        candidates = search_candidates(client, item, settings)
        ranked = rank_candidates(item, candidates, settings)
        decision = match_decision(ranked, settings)

        self.assertEqual(
            [query["provider"] for query in client.queries], ["audible", "google"]
        )
        self.assertEqual(client.queries[-1]["title"], "Octopussy")
        self.assertEqual(ranked[0]["parts"]["title"], 100)
        self.assertEqual(ranked[0]["parts"]["author"], 100)
        self.assertEqual(decision["action"], "auto")

    def test_series_sequence_prefix_is_removed_before_searching(self):
        class PernClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                if params["title"] == "The Masterharper of Pern":
                    return [
                        {
                            "title": "The Masterharper of Pern",
                            "author": "Anne McCaffrey",
                        }
                    ]
                return []

        item = sample_item()
        item["media"]["metadata"]["title"] = "Pern 17 - The Masterharper of Pern"
        item["media"]["metadata"]["authorName"] = "Anne McCaffrey"
        client = PernClient()

        candidates = search_candidates(client, item, DEFAULT_SETTINGS)
        ranked = rank_candidates(item, candidates, DEFAULT_SETTINGS)

        self.assertEqual(
            [query["title"] for query in client.queries],
            ["The Masterharper of Pern"],
        )
        self.assertEqual(candidates[0]["title"], "The Masterharper of Pern")
        self.assertEqual(
            candidates[0]["_absidekickSearch"]["strategy"],
            "parsed title + author",
        )
        self.assertTrue(candidates[0]["_absidekickSearch"]["quickMatchEligible"])
        self.assertEqual(ranked[0]["parts"]["title"], 100.0)
        self.assertEqual(match_decision(ranked, DEFAULT_SETTINGS)["action"], "auto")

    def test_quick_match_uses_the_evidence_backed_parsed_title(self):
        class RecordingClient:
            def __init__(self):
                self.posts = []

            def post(self, path, params=None, body=None):
                self.posts.append((path, params, body))
                return {"ok": True}

            def patch(self, path, body):
                return {"ok": True, "path": path, "body": body}

        item = sample_item()
        item["media"]["metadata"]["title"] = "Pern 17 - The Masterharper of Pern"
        item["media"]["metadata"]["authorName"] = "Anne McCaffrey"
        candidate = {
            "title": "The Masterharper of Pern",
            "author": "Anne McCaffrey",
            "_absidekickSearch": {
                "strategy": "parsed title + author",
                "queryTitle": "The Masterharper of Pern",
                "quickMatchEligible": True,
            },
        }
        scored = score_candidate(item, candidate, DEFAULT_SETTINGS)
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["run"]["dryRun"] = False
        settings["matching"]["applyMode"] = "quick_match"
        client = RecordingClient()

        apply_match(client, item, scored, settings)

        self.assertEqual(client.posts[0][1]["title"], "The Masterharper of Pern")

    def test_abs_series_metadata_can_confirm_a_non_repeating_prefix(self):
        entries = [("Discworld", "4")]

        self.assertEqual(
            clean_search_title("Discworld 04 - Mort", entries),
            "Mort",
        )

    def test_numbered_real_title_is_tried_before_weak_prefix_fallback(self):
        class CatchClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                return [{"title": "Catch 22 - A Novel", "author": "Joseph Heller"}]

        item = sample_item()
        item["media"]["metadata"]["title"] = "Catch 22 - A Novel"
        item["media"]["metadata"]["authorName"] = "Joseph Heller"
        client = CatchClient()

        candidates = search_candidates(client, item, DEFAULT_SETTINGS)

        self.assertEqual(len(client.queries), 1)
        self.assertEqual(client.queries[0]["title"], "Catch 22 - A Novel")
        self.assertEqual(candidates[0]["title"], "Catch 22 - A Novel")

    def test_unconfirmed_series_prefix_is_used_only_after_original_is_empty(self):
        class FallbackClient:
            def __init__(self):
                self.queries = []

            def get(self, path, params=None):
                self.queries.append(dict(params or {}))
                if params["title"] == "The Hidden Star":
                    return [{"title": "The Hidden Star", "author": "A. Writer"}]
                return []

        item = sample_item()
        item["media"]["metadata"]["title"] = "Galaxy 07 - The Hidden Star"
        item["media"]["metadata"]["authorName"] = "A. Writer"
        client = FallbackClient()

        candidates = search_candidates(client, item, DEFAULT_SETTINGS)

        self.assertEqual(
            [query["title"] for query in client.queries],
            ["Galaxy 07 - The Hidden Star", "The Hidden Star"],
        )
        self.assertEqual(
            candidates[0]["_absidekickSearch"]["strategy"],
            "possible series-prefix title + author",
        )
        self.assertFalse(candidates[0]["_absidekickSearch"]["quickMatchEligible"])

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
                        {
                            "title": "A Completely Different Book",
                            "author": "New Author",
                        },
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

    def test_review_scan_reports_loading_and_per_item_progress(self):
        class ReviewScanClient:
            def get(self, path, params=None):
                if path == "/api/libraries/library-1/items":
                    return {
                        "results": [sample_item(["ABSidekick: Needs Review"])],
                        "total": 1,
                    }
                if path == "/api/search/books":
                    return [
                        {
                            "title": "Wizard's First Rule",
                            "author": "Terry Goodkind",
                        }
                    ]
                raise AssertionError(path)

        settings = deepcopy(DEFAULT_SETTINGS)
        settings["connection"]["libraryId"] = "library-1"
        updates = []

        result = scan_review_items(
            ReviewScanClient(),
            settings,
            limit=25,
            progress=lambda update: updates.append(dict(update)),
        )

        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(updates[0]["phase"], "loading")
        self.assertEqual(updates[1]["total"], 1)
        self.assertEqual(updates[2]["current"], 0)
        self.assertEqual(updates[2]["currentTitle"], "Wizard's First Rule")
        self.assertEqual(updates[-1]["current"], 1)
        self.assertIn("Finished Wizard's First Rule", updates[-1]["detail"])


if __name__ == "__main__":
    unittest.main()
