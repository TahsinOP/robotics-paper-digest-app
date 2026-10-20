"""arXiv adapter using the documented Atom API (https://info.arxiv.org/help/api/).

Terms of use: at most one request every three seconds over a single connection; results come
in pages of up to 2000. The throttle below is shared by every ArxivSource in the process, so
two browser tabs refreshing at once still respect the limit.

Two queries run per refresh:
1. everything in cs.RO submitted inside the window;
2. robotics-flavoured papers that are only listed in neighbouring categories (cs.CV, cs.LG, ...).
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from paper_radar import __version__
from paper_radar.models import PaperCandidate
from paper_radar.services.normalization import normalize_doi, parse_arxiv_id
from paper_radar.sources.base import FetchResult, ProgressCallback, SourceError
from paper_radar.text import collapse_whitespace

API_URL = "https://export.arxiv.org/api/query"
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ArxivFeedError(Exception):
    pass


class RateLimiter:
    """Enforces a minimum gap between request starts."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: float | None = None

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                remaining = self.min_interval - (self._clock() - self._last)
                if remaining > 0:
                    self._sleep(remaining)
            self._last = self._clock()


SHARED_RATE_LIMITER = RateLimiter(min_interval=3.0)


@dataclass(frozen=True)
class ParsedPage:
    total_results: int
    entries: list[PaperCandidate]


@dataclass(frozen=True)
class ArxivQuery:
    label: str
    search_query: str
    cap: int
    sort_by: str = "submittedDate"  # or "relevance" for keyword searches


_TOKEN = re.compile(r'"([^"]+)"|(\S+)')
_OPERATORS = {"and", "or", "andnot", "not"}


def keyword_clause(text: str) -> str:
    """'loop closure "pose graph"' -> 'all:loop AND all:closure AND all:"pose graph"' (arXiv stems words)."""
    terms = []
    for phrase, word in _TOKEN.findall(text):
        value = (phrase or word).replace('"', "").strip()
        if not value or value.lower() in _OPERATORS:
            continue
        simple = re.fullmatch(r"[\w.-]+", value) and " " not in value
        terms.append(f"all:{value}" if simple else f'all:"{value}"')
    return " AND ".join(terms)


def _text(element: ET.Element, path: str) -> str | None:
    found = element.find(path, _NS)
    return collapse_whitespace(found.text) if found is not None and found.text else None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_feed(payload: bytes) -> ParsedPage:
    """Parse one page of the arXiv Atom feed into normalised candidates."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ArxivFeedError("arXiv sent a response that could not be read") from exc
    total_text = _text(root, "opensearch:totalResults")
    entries = root.findall("atom:entry", _NS)
    for entry in entries:
        entry_id = _text(entry, "atom:id") or ""
        if "/api/errors" in entry_id or _text(entry, "atom:title") == "Error":
            raise ArxivFeedError(f"arXiv reported an error: {_text(entry, 'atom:summary') or 'no details given'}")
    if total_text is None:
        raise ArxivFeedError("arXiv sent a response without result counts")

    candidates = []
    for entry in entries:
        parsed = parse_arxiv_id(_text(entry, "atom:id"))
        if parsed is None:
            continue
        arxiv_id, version = parsed
        primary = entry.find("arxiv:primary_category", _NS)
        candidates.append(
            PaperCandidate(
                source_name="arxiv",
                title=_text(entry, "atom:title") or "Untitled",
                authors=tuple(
                    name for a in entry.findall("atom:author", _NS) if (name := _text(a, "atom:name"))
                ),
                abstract=_text(entry, "atom:summary") or "",
                published_at=_parse_time(_text(entry, "atom:published")),
                updated_at=_parse_time(_text(entry, "atom:updated")),
                source_url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                arxiv_id=arxiv_id,
                arxiv_version=version,
                doi=normalize_doi(_text(entry, "arxiv:doi")),
                source_categories=tuple(
                    term for c in entry.findall("atom:category", _NS) if (term := c.get("term"))
                ),
                source_primary_category=primary.get("term") if primary is not None else None,
                author_comment=_text(entry, "arxiv:comment"),
                journal_ref=_text(entry, "arxiv:journal_ref"),
            )
        )
    return ParsedPage(total_results=int(total_text), entries=candidates)


def _date_clause(since: datetime, until: datetime) -> str:
    fmt = "%Y%m%d%H%M"
    start = since.astimezone(timezone.utc).strftime(fmt)
    end = until.astimezone(timezone.utc).strftime(fmt)
    return f"submittedDate:[{start} TO {end}]"


def _quote(term: str) -> str:
    return f'"{term}"' if any(ch in term for ch in " -") else term


class ArxivSource:
    name = "arxiv"

    def __init__(
        self,
        *,
        cross_list_categories: tuple[str, ...] = ("cs.CV", "cs.LG", "cs.AI", "eess.SY"),
        cross_list_terms: tuple[str, ...] = ("robot", "robotic"),
        session: requests.Session | None = None,
        page_size: int = 250,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 3.0,
        contact_email: str | None = None,
        rate_limiter: RateLimiter = SHARED_RATE_LIMITER,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cross_list_categories = cross_list_categories
        self.cross_list_terms = cross_list_terms
        self.session = session or requests.Session()
        self.page_size = page_size
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.rate_limiter = rate_limiter
        self._sleep = sleep
        agent = f"robotics-paper-radar/{__version__}"
        self.user_agent = f"{agent} (mailto:{contact_email})" if contact_email else agent

    def build_queries(self, since: datetime, until: datetime, max_results: int) -> list[ArxivQuery]:
        dates = _date_clause(since, until)
        if not self.cross_list_categories or not self.cross_list_terms:
            return [ArxivQuery("Robotics (cs.RO)", f"cat:cs.RO AND {dates}", max_results)]
        cross_cap = max(100, max_results // 5)
        categories = " OR ".join(f"cat:{c}" for c in self.cross_list_categories)
        terms = " OR ".join(f"abs:{_quote(t)}" for t in self.cross_list_terms)
        return [
            ArxivQuery("Robotics (cs.RO)", f"cat:cs.RO AND {dates}", max(1, max_results - cross_cap)),
            ArxivQuery(
                "Related categories",
                f"({categories}) AND ({terms}) AND {dates} ANDNOT cat:cs.RO",
                cross_cap,
            ),
        ]

    def fetch_recent(
        self,
        *,
        since: datetime,
        until: datetime,
        max_results: int,
        progress: ProgressCallback | None = None,
    ) -> FetchResult:
        result = FetchResult()
        for query in self.build_queries(since, until, max_results):
            fetched: list[PaperCandidate] = []
            try:
                self._run_query(query, fetched, progress)
            except SourceError as exc:
                kept = f" The {len(fetched)} papers fetched before that were kept." if fetched else ""
                result.errors.append(f"{query.label}: {exc}.{kept}")
            result.candidates.extend(fetched)
        return result

    def _run_query(self, query: ArxivQuery, out: list[PaperCandidate], progress: ProgressCallback | None) -> None:
        start = 0
        while len(out) < query.cap:
            size = min(self.page_size, query.cap - len(out))
            page = self.fetch_page(query.search_query, start, size, sort_by=query.sort_by)
            out.extend(page.entries)
            if progress:
                progress(f"{query.label}: {len(out):,} of {min(page.total_results, query.cap):,} papers")
            start += len(page.entries)
            if not page.entries or start >= page.total_results:
                return

    def search(
        self,
        text: str,
        *,
        since: datetime,
        until: datetime,
        max_results: int = 200,
        progress: ProgressCallback | None = None,
    ) -> FetchResult:
        """Papers in cs.RO and the neighbouring categories that match keywords, best matches first."""
        terms = keyword_clause(text)
        if not terms:
            return FetchResult()
        categories = " OR ".join(f"cat:{c}" for c in ("cs.RO", *self.cross_list_categories))
        query = ArxivQuery(
            "arXiv search", f"({terms}) AND ({categories}) AND {_date_clause(since, until)}", max_results, "relevance"
        )
        result = FetchResult()
        try:
            self._run_query(query, result.candidates, progress)
        except SourceError as exc:
            result.errors.append(f"{exc}.")
        return result

    def fetch_by_ids(self, arxiv_ids: list[str]) -> list[PaperCandidate]:
        """Metadata for specific papers, in batches of 100 ids."""
        candidates: list[PaperCandidate] = []
        for i in range(0, len(arxiv_ids), 100):
            batch = arxiv_ids[i : i + 100]
            candidates += self.fetch_page("", 0, len(batch), id_list=",".join(batch)).entries
        return candidates

    def fetch_page(
        self, search_query: str, start: int, size: int, *, id_list: str | None = None, sort_by: str = "submittedDate"
    ) -> ParsedPage:
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": size,
            "sortBy": sort_by,
            "sortOrder": "descending",
        }
        if id_list:
            params = {"id_list": id_list, "max_results": size}
        last_problem = "arXiv could not be reached"
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(self.retry_delay * 2 ** (attempt - 1))
            self.rate_limiter.wait()
            try:
                response = self.session.get(
                    API_URL, params=params, timeout=self.timeout, headers={"User-Agent": self.user_agent}
                )
            except requests.Timeout:
                last_problem = f"arXiv did not respond within {self.timeout:.0f} seconds"
                continue
            except requests.ConnectionError:
                last_problem = "arXiv could not be reached; check your internet connection"
                continue
            if response.status_code in RETRYABLE_STATUS:
                last_problem = f"arXiv is busy (HTTP {response.status_code})"
                continue
            if response.status_code != 200:
                raise SourceError(f"arXiv rejected the request (HTTP {response.status_code})")
            try:
                page = parse_feed(response.content)
            except ArxivFeedError as exc:
                last_problem = str(exc)
                continue
            if not page.entries and start < page.total_results:
                last_problem = "arXiv returned an empty page"  # a known transient quirk; retry
                continue
            return page
        raise SourceError(f"{last_problem} (tried {self.max_retries + 1} times)")
