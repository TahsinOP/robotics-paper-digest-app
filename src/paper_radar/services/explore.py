"""Looking back: fetch a past month or year from arXiv, and add a year's most-cited papers."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone

from paper_radar.preferences import Preferences
from paper_radar.repositories import papers
from paper_radar.services import ingestion, library
from paper_radar.sources.arxiv_source import ArxivSource
from paper_radar.sources.semantic_scholar import CitedPaper
from paper_radar.taxonomy import Taxonomy
from paper_radar.text import utc_now

ARCHIVE_RUN = "arxiv-archive"
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")


def period_bounds(year: int, month: int | None = None) -> tuple[datetime, datetime]:
    """[start, end) of a calendar year or month, in UTC."""
    if month is None:
        return datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
    return datetime(year, month, 1, tzinfo=timezone.utc), end


def period_label(year: int, month: int | None = None) -> str:
    return f"{MONTH_NAMES[month - 1]} {year}" if month else str(year)


@dataclass(frozen=True)
class PeriodReport:
    months: int
    fetched: int
    inserted: int
    errors: tuple[str, ...]


def fetch_period(
    connect: Callable[[], AbstractContextManager[sqlite3.Connection]],
    source: ArxivSource,
    prefs: Preferences,
    taxonomy: Taxonomy,
    year: int,
    month: int | None = None,
    *,
    max_per_month: int = 8000,
    progress: Callable[[str], None] | None = None,
    now: Callable[[], datetime] = utc_now,
) -> PeriodReport:
    """Fetch a past month or a whole year, one month per transaction, so stopping halfway keeps the done months."""
    months = [month] if month else list(range(1, 13))
    current = now()
    fetched = inserted = done = 0
    errors: list[str] = []
    for m in months:
        since, until = period_bounds(year, m)
        if since >= current:
            break
        label = period_label(year, m)
        with connect() as conn:
            report = ingestion.refresh_papers(
                conn, source, prefs, taxonomy, now=current, window=(since, min(until, current)),
                max_results=max_per_month, run_label=ARCHIVE_RUN,
                progress=(lambda msg, label=label: progress(f"{label}: {msg}")) if progress else None,
            )
        done += 1
        fetched += report.fetched
        inserted += report.inserted
        errors += [f"{label}: {e}" for e in report.errors]
    return PeriodReport(months=done, fetched=fetched, inserted=inserted, errors=tuple(errors))


def add_cited_papers(
    conn: sqlite3.Connection,
    cited: list[CitedPaper],
    source: ArxivSource,
    prefs: Preferences,
    taxonomy: Taxonomy,
    now: datetime,
) -> list[int]:
    """Bring most-cited papers into the library (fetching their arXiv metadata) and record their citations.
    You chose these papers, so the robotics filter for neighbouring categories is skipped."""
    known = papers.ids_by_arxiv(conn, [c.arxiv_id for c in cited])
    missing = [c.arxiv_id for c in cited if c.arxiv_id not in known]
    if missing:
        stored = ingestion.store_candidates(conn, source.fetch_by_ids(missing), taxonomy, now, check_robotics=False)
        known = papers.ids_by_arxiv(conn, [c.arxiv_id for c in cited])
        library.rescore_ids(conn, list(stored.ids), prefs, taxonomy, now)
    record_citations(conn, cited, now)
    return [known[c.arxiv_id] for c in cited if c.arxiv_id in known]


def record_citations(conn: sqlite3.Connection, cited: list[CitedPaper], now: datetime) -> int:
    """Store citation counts for the papers that are already in the library."""
    known = papers.ids_by_arxiv(conn, [c.arxiv_id for c in cited])
    for c in cited:
        if c.arxiv_id in known:
            papers.set_citations(conn, known[c.arxiv_id], c.citation_count, c.influential_count, now)
    return len(known)


@dataclass(frozen=True)
class SearchReport:
    found: int
    inserted: int
    errors: tuple[str, ...]


def search_arxiv(
    conn: sqlite3.Connection,
    source: ArxivSource,
    prefs: Preferences,
    taxonomy: Taxonomy,
    text: str,
    year: int,
    month: int | None = None,
    *,
    now: datetime,
    max_results: int = 200,
) -> SearchReport:
    """Search arXiv for keywords within a past period and add the matches to the library.
    You asked for these papers, so the robotics filter for neighbouring categories is skipped."""
    since, until = period_bounds(year, month)
    result = source.search(text, since=since, until=min(until, now), max_results=max_results)
    stored = ingestion.store_candidates(conn, result.candidates, taxonomy, now, check_robotics=False)
    library.rescore_ids(conn, list(stored.ids), prefs, taxonomy, now)
    return SearchReport(found=len(result.candidates), inserted=stored.inserted, errors=tuple(result.errors))
