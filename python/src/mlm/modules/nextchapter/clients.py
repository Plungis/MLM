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


class GeminiClient:
    def __init__(
        self, api_key: str = "", client: httpx.AsyncClient | None = None
    ) -> None:
        self.api_key = api_key.strip()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=35)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def analyze_series(
        self,
        series_data: list[dict[str, Any]],
        top_authors: list[str] | None = None,
    ) -> dict[str, Any]:
        """Analyze book series using Google Gemini AI.

        Identifies canonical reading order, canonical total volume count,
        all missing books/sequels with titles and summaries, and generates
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
            summary_lines.append(
                f"- Series: '{name}' by {authors or 'Unknown'}. "
                f"Currently Owned: [{owned_str}]"
            )

        authors_context = ", ".join((top_authors or [])[:15])

        prompt = (
            "You are an expert literary bibliography authority and intelligent "
            "library curator.\n"
            "Below is a list of book series currently in the user's library along with "
            "raw titles and filenames they currently own:\n\n"
            + "\n".join(summary_lines)
            + "\n\n"
            + (
                f"Favorite/Top Authors: {authors_context}\n\n"
                if authors_context
                else ""
            )
            + "CRITICAL INSTRUCTIONS FOR MISLEADING OR MESSY LIBRARY METADATA:\n"
            "1. NOISY TITLES & ARTIFACTS: Raw titles/filenames frequently contain "
            "rip noise, format tags ('Unabridged', 'Dramatized Adaptation'), publisher/"
            "studio names ('GraphicAudio', 'Soundbooth Theater'), disc/CD numbers "
            "('Disc 01', 'CD 02'), or narrator names. Mentally strip these out to "
            "identify the true canonical book.\n"
            "2. MULTI-PART / SPLIT RELEASES: If the user owns multiple parts or files "
            "of the same book (e.g. 'The Way of Kings Part 1' and 'Part 2'), "
            "recognize that these are parts of the SAME single book. The book "
            "is OWNED. Do NOT consider other parts of the same book as missing books!\n"
            "3. OMNIBUSES, BUNDLES & BOX SETS: If an entry represents an omnibus or "
            "collection (e.g. 'Dungeon Crawler Carl: Books 1-3 Omnibus'), recognize "
            "that ALL volumes contained in that omnibus (Books 1, 2, and 3) are "
            "ALREADY OWNED by the user. Do NOT report any of them as missing!\n"
            "4. SUBTITLES & UNNUMBERED TITLES: Many books do not have volume numbers "
            "in their metadata. Use your deep knowledge of author bibliographies to "
            "map each owned title to its canonical volume number and series order.\n"
            "5. ACCURATE SEQUELS & GAPS: Identify ONLY canonical books in the series "
            "(mainline numbered entries or canonical novellas) that the user does "
            "NOT own yet.\n"
            "6. For each missing book, provide:\n"
            "   - volume_number: canonical volume number (e.g. '1', '2', '2.5', '4')\n"
            "   - title: clean, official standalone book title "
            "(e.g. 'The Gate of the Feral Gods', NOT 'DCC Book 4 GraphicAudio')\n"
            "   - author: primary author name\n"
            "   - summary: concise 1-2 sentence synopsis\n"
            "7. Determine canonical_total_books and status ('complete' or 'ongoing').\n"
            "8. Recommend 6 to 10 exciting new book series or standalone books "
            "matching their taste.\n\n"
            "Return valid JSON matching this schema:\n"
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

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
            },
        }

        # Modern active Gemini Flash models
        models_to_try = [
            "gemini-3.5-flash-lite",
            "gemini-3-flash-preview",
            "gemini-3.8-flash",
            "gemini-flash-latest",
        ]
        last_error = ""

        for model in models_to_try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                f"?key={self.api_key}"
            )
            try:
                res = await self.client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        continue
                    text = (
                        candidates[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    text = text.strip()
                    if text.startswith("```json"):
                        text = text[7:]
                    elif text.startswith("```"):
                        text = text[3:]
                    text = text.removesuffix("```")
                    parsed = json.loads(text.strip())

                    # If missing_books is at top-level, distribute or include
                    top_missing = parsed.get("missing_books", [])
                    parsed_series = parsed.get("series", [])

                    # Ensure both formats work
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
                        "recommendations": parsed.get("recommendations", []),
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
