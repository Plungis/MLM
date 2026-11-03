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
        self.client = client or httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def analyze_and_recommend(
        self,
        series_data: list[dict[str, Any]],
        top_authors: list[str],
    ) -> dict[str, Any]:
        if not self.api_key:
            return {"missing_books": [], "recommendations": []}

        summary_lines = []
        for s in series_data[:25]:
            owned = ", ".join(f"#{v}" for v in s.get("volumes", []) if v)
            gaps = ", ".join(s.get("gaps", []))
            summary_lines.append(
                f"- Series: '{s['series_name']}' by {', '.join(s.get('authors', []))}. "
                f"Owned: [{owned}]. Detected gaps: [{gaps}]."
            )

        prompt = (
            "You are an expert book and audiobook librarian.\n"
            "Here is a list of book series currently in the user's library:\n"
            + "\n".join(summary_lines)
            + "\n\n"
            f"Top Authors they enjoy: {', '.join(top_authors[:15])}\n\n"
            "Tasks:\n"
            "1. Identify any missing books or sequels for these series that the "
            "user does not have yet (e.g. gaps between owned books or sequels).\n"
            "2. Recommend 6 to 10 exciting new book series or standalone books "
            "based on their taste, explaining why they will love each one.\n\n"
            "Return ONLY valid JSON matching this schema:\n"
            "{\n"
            '  "missing_books": [\n'
            "    {\n"
            '      "series_name": "Series Name",\n'
            '      "volume_number": "4",\n'
            '      "title": "Title of Missing Book",\n'
            '      "author": "Author Name",\n'
            '      "summary": "Brief 1-sentence synopsis"\n'
            "    }\n"
            "  ],\n"
            '  "recommendations": [\n'
            "    {\n"
            '      "title": "Book or First Book Title",\n'
            '      "author": "Author Name",\n'
            '      "series_name": "Series Name (or empty if standalone)",\n'
            '      "volume": "1",\n'
            '      "reason": "Why the user will love this based on their library",\n'
            '      "summary": "Brief 1-sentence synopsis"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3,
            },
        }

        try:
            res = await self.client.post(url, json=payload)
            if res.status_code != 200:
                return {
                    "error": (
                        f"Gemini API returned status {res.status_code}: "
                        f"{res.text[:200]}"
                    ),
                    "missing_books": [],
                    "recommendations": [],
                }
            data = res.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return {"missing_books": [], "recommendations": []}

            candidate_parts = (
                candidates[0].get("content", {}).get("parts", [{}])
            )
            text = candidate_parts[0].get("text", "")
            parsed = json.loads(text)
            return {
                "missing_books": parsed.get("missing_books", []),
                "recommendations": parsed.get("recommendations", []),
            }
        except Exception as error:
            return {
                "error": str(error),
                "missing_books": [],
                "recommendations": [],
            }
