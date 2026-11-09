from __future__ import annotations

import json
import re
from typing import Any

import httpx

ENTRY_NUMBER_PATTERN = re.compile(r"^#?\s*([\d.]+)", re.IGNORECASE)
TITLE_SERIES_PATTERN = re.compile(
    r"(?:book|vol(?:ume)?|#)\s*([\d.]+)", re.IGNORECASE
)


class GoogleBooksClient:
    def __init__(
        self, api_key: str = "", client: httpx.AsyncClient | None = None
    ) -> None:
        self.api_key = api_key.strip()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=15)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def search_series_books(
        self, series_name: str, author: str | None = None
    ) -> list[dict[str, Any]]:
        clean_series = series_name.strip()
        if not clean_series:
            return []

        query = f'intitle:"{clean_series}"'
        if author and author.strip():
            query += f' inauthor:"{author.strip()}"'

        params: dict[str, Any] = {
            "q": query,
            "maxResults": 30,
            "orderBy": "relevance",
            "printType": "books",
        }
        if self.api_key:
            params["key"] = self.api_key

        try:
            res = await self.client.get(
                "https://www.googleapis.com/books/v1/volumes", params=params
            )
            if res.status_code != 200:
                return []
            data = res.json()
        except Exception:
            return []

        results: list[dict[str, Any]] = []
        for item in data.get("items", []):
            info = item.get("volumeInfo", {})
            title = info.get("title", "")
            subtitle = info.get("subtitle", "")
            full_title = f"{title}: {subtitle}" if subtitle else title
            authors = info.get("authors", [])
            published = info.get("publishedDate", "")
            description = info.get("description", "")
            images = info.get("imageLinks", {})
            cover_url = (
                images.get("thumbnail")
                or images.get("smallThumbnail")
                or ""
            )
            if cover_url.startswith("http://"):
                cover_url = "https://" + cover_url[7:]

            match = TITLE_SERIES_PATTERN.search(full_title)
            volume = match.group(1).strip() if match else ""

            results.append(
                {
                    "title": title,
                    "volume": volume,
                    "authors": authors,
                    "published_date": published,
                    "description": description[:600] if description else "",
                    "cover_url": cover_url,
                }
            )
        return results


class OpenLibraryClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=15)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def search_series(
        self, series_name: str, author: str | None = None
    ) -> list[dict[str, Any]]:
        clean_series = series_name.strip()
        if not clean_series:
            return []

        q = clean_series
        if author:
            q += f" {author}"

        params = {"q": q, "limit": 25}
        try:
            res = await self.client.get(
                "https://openlibrary.org/search.json", params=params
            )
            if res.status_code != 200:
                return []
            data = res.json()
        except Exception:
            return []

        books: list[dict[str, Any]] = []
        for doc in data.get("docs", []):
            title = doc.get("title", "")
            authors = doc.get("author_name", [])
            cover_id = doc.get("cover_i")
            cover_url = (
                f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"
                if cover_id
                else ""
            )
            first_year = str(doc.get("first_publish_year", ""))
            match = TITLE_SERIES_PATTERN.search(title)
            volume = match.group(1).strip() if match else ""

            books.append(
                {
                    "title": title,
                    "volume": volume,
                    "authors": authors,
                    "published_date": first_year,
                    "cover_url": cover_url,
                }
            )
        return books


def _extract_json_dict(raw_text: str) -> dict[str, Any]:
    """Extract and parse a JSON object from raw model text or fenced blocks."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    cleaned = cleaned.removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(cleaned[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("No valid JSON object found in model output")


class GeminiClient:
    def __init__(
        self, api_key: str = "", client: httpx.AsyncClient | None = None
    ) -> None:
        self.api_key = api_key.strip()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=45)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def analyze_series(
        self,
        series_data: list[dict[str, Any]],
        top_authors: list[str] | None = None,
    ) -> dict[str, Any]:
        """Analyze book series using Gemini AI with Search Grounding & MAM Catalog.

        Identifies canonical reading order, canonical total volume count,
        all missing books/sequels with verified titles and summaries, and generates
        personalized reading recommendations.
        """
        if not self.api_key:
            return {"series": [], "missing_books": [], "recommendations": []}

        summary_lines = []
        for s in series_data[:30]:
            name = s.get("series_name", "")
            authors = ", ".join(s.get("authors", []))
            owned_titles = []
            for b in s.get("books", []):
                title = b.get("title", "")
                vol = b.get("volume", "")
                if vol and vol not in title:
                    owned_titles.append(f"#{vol} {title}")
                else:
                    owned_titles.append(title)
            if not owned_titles and s.get("volumes"):
                owned_titles = [f"Vol {v}" for v in s["volumes"]]

            owned_str = (
                "; ".join(owned_titles[:15]) if owned_titles else "None recorded"
            )
            line = (
                f"- Series: '{name}' by {authors or 'Unknown'}. "
                f"Currently Owned in Library: [{owned_str}]"
            )
            mam_cat = s.get("mam_catalog") or []
            if mam_cat:
                cat_items = [
                    f"#{c.get('volume_number')}: {c.get('title')}"
                    for c in mam_cat[:25]
                    if c.get("volume_number") and c.get("title")
                ]
                if cat_items:
                    cat_str = "; ".join(cat_items)
                    line += (
                        " | Verified Real Releases on MyAnonamouse "
                        f"(GROUND TRUTH — USE THESE EXACT TITLES): [{cat_str}]"
                    )
            summary_lines.append(line)

        authors_context = ", ".join((top_authors or [])[:15])

        prompt = (
            "You are an expert literary bibliography authority and intelligent "
            "library curator.\n"
            "Below is a list of book series currently in the user's library along with "
            "raw titles/filenames they own, plus Verified Real Releases discovered on "
            "MyAnonamouse where available:\n\n"
            + "\n".join(summary_lines)
            + "\n\n"
            + (
                f"Favorite/Top Authors: {authors_context}\n\n"
                if authors_context
                else ""
            )
            + "CRITICAL ANTI-HALLUCINATION & ACCURACY RULES:\n"
            "1. NEVER INVENT OR GUESS BOOK TITLES: Never fabricate a title that "
            "merely sounds like the series pattern (for example, NEVER invent a "
            "non-existent title like 'A Schism of Souls' for 'A Practical Guide to "
            "Sorcery' by Azalea Ellis, whose real books are 'A Conjuring of Ravens', "
            "'A Binding of Blood', 'A Sacrifice of Light', 'A Foreboding of Woe', "
            "and 'A Cauldron of Bitterness').\n"
            "2. TRUST VERIFIED MAM RELEASES: When 'Verified Real Releases on "
            "MyAnonamouse' are listed for a series, those are 100% real verified "
            "books. You MUST use those exact book titles for those volume numbers!\n"
            "3. VERIFY VIA GOOGLE SEARCH: For any series or volume not listed in the "
            "MAM catalog, use Google Search to verify the exact real published title "
            "and volume order on Goodreads/Audible/author bibliography. If you are "
            "not 100% certain of a book's real published title, DO NOT include a "
            "guessed title.\n"
            "4. NOISY TITLES & ARTIFACTS: Raw owned titles/filenames often contain "
            "rip noise, format tags ('Unabridged', 'Dramatized Adaptation'), publisher/"
            "studio names ('GraphicAudio', 'Soundbooth Theater'), disc/CD numbers "
            "('Disc 01', 'CD 02'), or narrator names. Mentally strip these out to "
            "identify the true canonical book the user owns.\n"
            "5. MULTI-PART / SPLIT RELEASES: If the user owns multiple parts or files "
            "of the same book (e.g. 'The Way of Kings Part 1' and 'Part 2'), "
            "recognize that these are parts of the SAME single book. The book "
            "is OWNED.\n"
            "6. OMNIBUSES, BUNDLES & BOX SETS: If an entry represents an omnibus or "
            "collection (e.g. 'Dungeon Crawler Carl: Books 1-3 Omnibus'), recognize "
            "that ALL volumes contained in that omnibus (Books 1, 2, and 3) are "
            "ALREADY OWNED by the user. Do NOT report any of them as missing!\n"
            "7. SUBTITLES & UNNUMBERED TITLES: Map each owned title to its canonical "
            "volume number so owned books are never reported as missing.\n"
            "8. For each missing book that the user does NOT own yet, provide:\n"
            "   - volume_number: canonical volume number (e.g. '1', '2', '3', '4')\n"
            "   - title: exact, verified official book title\n"
            "   - author: primary author name\n"
            "   - summary: concise 1-2 sentence synopsis\n"
            "9. Determine canonical_total_books and status ('complete' or 'ongoing').\n"
            "10. Recommend 6 to 10 exciting real book series or standalone books "
            "matching their taste.\n\n"
            "Return ONLY valid JSON matching this schema (no markdown commentary):\n"
            "{\n"
            '  "series": [\n'
            "    {\n"
            '      "series_name": "Series Name",\n'
            '      "canonical_total_books": 7,\n'
            '      "status": "ongoing",\n'
            '      "missing_books": [\n'
            "        {\n"
            '          "volume_number": "4",\n'
            '          "title": "Book Title",\n'
            '          "author": "Author Name",\n'
            '          "summary": "Concise 1-2 sentence synopsis"\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ],\n"
            '  "recommendations": [\n'
            "    {\n"
            '      "title": "Recommended Title",\n'
            '      "author": "Author Name",\n'
            '      "series_name": "Series Name or empty",\n'
            '      "volume": "1",\n'
            '      "reason": "Why the user will enjoy this",\n'
            '      "summary": "Concise synopsis"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )

        grounded_payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "temperature": 0.1,
            },
        }
        ungrounded_payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }

        # Prioritize full Flash models with strong bibliographic knowledge & grounding
        models_to_try = [
            "gemini-3.8-flash",
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
        ]
        last_error = ""

        for model in models_to_try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                f"?key={self.api_key}"
            )
            for payload in (grounded_payload, ungrounded_payload):
                try:
                    res = await self.client.post(url, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if not candidates:
                            continue
                        parts = (
                            candidates[0]
                            .get("content", {})
                            .get("parts", [])
                        )
                        text = "\n".join(
                            str(p.get("text", ""))
                            for p in parts
                            if isinstance(p, dict) and p.get("text")
                        )
                        if not text.strip():
                            continue
                        parsed = _extract_json_dict(text)

                        top_missing = parsed.get("missing_books", [])
                        parsed_series = parsed.get("series", [])

                        flat_missing: list[dict[str, Any]] = list(top_missing)
                        for s_item in parsed_series:
                            s_name = s_item.get("series_name", "")
                            for m_item in s_item.get("missing_books", []):
                                if not m_item.get("series_name"):
                                    m_item["series_name"] = s_name
                                if not any(
                                    f.get("series_name", "").casefold()
                                    == s_name.casefold()
                                    and str(f.get("volume_number"))
                                    == str(m_item.get("volume_number"))
                                    for f in flat_missing
                                ):
                                    flat_missing.append(m_item)

                        return {
                            "series": parsed_series,
                            "missing_books": flat_missing,
                            "recommendations": parsed.get(
                                "recommendations", []
                            ),
                        }
                    else:
                        last_error = (
                            f"Gemini {model} returned HTTP {res.status_code}: "
                            f"{res.text[:180]}"
                        )
                except Exception as error:
                    last_error = f"Gemini error with {model}: {error}"

        return {
            "error": last_error,
            "series": [],
            "missing_books": [],
            "recommendations": [],
        }

    async def analyze_and_recommend(
        self,
        series_data: list[dict[str, Any]],
        top_authors: list[str],
    ) -> dict[str, Any]:
        """Backward-compatible proxy to analyze_series."""
        return await self.analyze_series(series_data, top_authors)
