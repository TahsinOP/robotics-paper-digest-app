"""Most-cited papers of a year, from the Semantic Scholar Graph API (bulk search).

arXiv has no citation data. Semantic Scholar merges a paper's arXiv and published versions, so its
counts reflect real impact, and it also reports "influential" citations (citing papers that build
substantially on the work). Only results with an arXiv id are kept, so every paper can be opened
and digested like the rest of the library.

Without an API key requests share a public pool that often answers HTTP 429; the client waits
and retries. A free key (SEMANTIC_SCHOLAR_API_KEY) makes it reliable.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from paper_radar import __version__
from paper_radar.sources.arxiv_source import RateLimiter
from paper_radar.sources.base import SourceError

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
KEY_VAR = "SEMANTIC_SCHOLAR_API_KEY"
FIELDS = "title,citationCount,influentialCitationCount,externalIds,venue,year,authors"
ROBOTICS_QUERY = (
    '(robot* | SLAM | odometry | humanoid | quadruped | legged | "mobile manipulation" | grasping '
    '| "vision-language-action" | UAV | drone | locomotion | manipulator)'
)
SHARED_RATE_LIMITER = RateLimiter(min_interval=1.1)


@dataclass(frozen=True)
class CitedPaper:
    title: str
    arxiv_id: str
    citation_count: int
    influential_count: int
    venue: str
    year: int | None
    authors: tuple[str, ...]


def build_query(topic: str) -> str:
    """A user topic narrows the robotics query; bulk search syntax: | for OR, + for AND, quotes for phrases."""
    topic = topic.strip()
    if not topic:
        return ROBOTICS_QUERY
    phrase = f'"{topic}"' if " " in topic and not topic.startswith('"') else topic
    return f"{phrase} + {ROBOTICS_QUERY}"


def clean_title(title: str) -> str:
    """Tidy titles as Semantic Scholar stores them, e.g. "π_0.5" -> "π0.5"."""
    title = re.sub(r"(?<=[^\W\d_])_(?=\d)", "", title)
    return " ".join(title.split())


class SemanticScholar:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        attempts: int = 5,
        rate_limiter: RateLimiter = SHARED_RATE_LIMITER,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else os.environ.get(KEY_VAR) or None
        self.timeout = timeout
        self.attempts = attempts
        self.rate_limiter = rate_limiter
        self._sleep = sleep

    def most_cited(self, year: int, *, topic: str = "", limit: int = 25) -> list[CitedPaper]:
        params = {
            "query": build_query(topic),
            "year": str(year),
            "fieldsOfStudy": "Computer Science",
            "sort": "citationCount:desc",
            "fields": FIELDS,
        }
        payload = self._get(params)
        results: list[CitedPaper] = []
        for item in payload.get("data") or []:
            arxiv_id = ((item.get("externalIds") or {}).get("ArXiv") or "").strip()
            if not arxiv_id:
                continue
            venue = (item.get("venue") or "").strip()
            results.append(
                CitedPaper(
                    title=clean_title(item.get("title") or "Untitled"),
                    arxiv_id=arxiv_id.lower(),
                    citation_count=int(item.get("citationCount") or 0),
                    influential_count=int(item.get("influentialCitationCount") or 0),
                    venue="" if venue.lower() in ("arxiv", "arxiv.org", "corr") else venue,
                    year=item.get("year"),
                    authors=tuple(a.get("name", "") for a in (item.get("authors") or [])[:6]),
                )
            )
            if len(results) >= limit:
                break
        return results

    def _get(self, params: dict) -> dict:
        headers = {"User-Agent": f"robotics-paper-radar/{__version__}"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        problem = "Semantic Scholar could not be reached"
        for attempt in range(self.attempts):
            if attempt:
                self._sleep(min(30.0, 2.0 * 2 ** (attempt - 1)))
            self.rate_limiter.wait()
            try:
                response = self.session.get(API_URL, params=params, headers=headers, timeout=self.timeout)
            except requests.Timeout:
                problem = f"Semantic Scholar didn't respond within {self.timeout:.0f} seconds"
                continue
            except requests.ConnectionError:
                problem = "Semantic Scholar could not be reached; check your internet connection"
                continue
            if response.status_code == 429:
                problem = (
                    "Semantic Scholar's shared rate limit was hit"
                    + ("" if self.api_key else ". A free API key (Settings → Citations) avoids this")
                )
                continue
            if response.status_code >= 500:
                problem = f"Semantic Scholar had a temporary problem (HTTP {response.status_code})"
                continue
            if response.status_code in (401, 403):
                raise SourceError("Semantic Scholar rejected the API key. Check it in Settings → Citations.")
            if response.status_code != 200:
                raise SourceError(f"Semantic Scholar rejected the request (HTTP {response.status_code}).")
            try:
                return response.json()
            except ValueError:
                problem = "Semantic Scholar sent a response that could not be read"
                continue
        raise SourceError(f"{problem}. Try again in a minute.")
