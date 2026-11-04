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

            owned_str = "; ".join(owned_titles[:15]) if owned_titles else "None recorded"
            summary_lines.append(
                f"- Series: '{name}' by {authors or 'Unknown'}. Currently Owned: [{owned_str}]"
            )

        authors_context = ", ".join((top_authors or [])[:15])

        prompt = (
            "You are an expert literary bibliography authority and book series intelligence engine.\n"
            "Below is a list of book series currently in the user's library along with the titles they currently own:\n\n"
            + "\n".join(summary_lines)
            + "\n\n"
            + (f"Favorite/Top Authors: {authors_context}\n\n" if authors_context else "")
            + "Tasks:\n"
            "1. For EACH series provided, evaluate its official canonical publication order and total canonical books.\n"
            "2. Identify all MISSING canonical books and released or announced sequels the user does not own yet. "
            "Do NOT include books the user already owns (match intelligently by title meaning, subtitles, or volume number).\n"
            "3. For each missing book, provide:\n"
            "   - volume_number: canonical volume number (e.g. '1', '2', '2.5', '4', etc.)\n"
            "   - title: official standalone title of the missing book\n"
            "   - author: author name\n"
            "   - summary: concise 1-2 sentence synopsis\n"
            "4. Determine canonical_total_books and status ('complete' or 'ongoing').\n"
            "5. Recommend 6 to 10 exciting new book series or standalone books matching their taste.\n\n"
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

        # Try gemini-2.5-flash with fallback to gemini-1.5-flash
        models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]
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
                    if text.endswith("```"):
                        text = text[:-3]
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
                                f.get("series_name", "").casefold() == s_name.casefold()
                                and str(f.get("volume_number")) == str(m_item.get("volume_number"))
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
                        f"Gemini {model} returned HTTP {res.status_code}: {res.text[:180]}"
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
