from datetime import datetime, timezone

from paper_radar import db
from paper_radar.repositories import app_state, papers
from paper_radar.repositories.papers import PaperQuery
from paper_radar.services import explore
from paper_radar.sources.base import FetchResult
from paper_radar.sources.semantic_scholar import CitedPaper

from .conftest import NOW, make_candidate


def test_period_bounds_and_labels() -> None:
    assert explore.period_bounds(2023) == (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert explore.period_bounds(2023, 12)[1] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert explore.period_bounds(2023, 2) == (datetime(2023, 2, 1, tzinfo=timezone.utc), datetime(2023, 3, 1, tzinfo=timezone.utc))
    assert explore.period_label(2023, 2) == "February 2023" and explore.period_label(2023) == "2023"


class MonthSource:
    """Returns one paper per requested month and records the windows it was asked for."""

    name = "arxiv"

    def __init__(self):
        self.windows = []

    def fetch_recent(self, *, since, until, max_results, progress=None):
        self.windows.append((since, until))
        n = len(self.windows)
        return FetchResult(candidates=[make_candidate(
            arxiv_id=f"2301.{n:05d}", title=f"Visual SLAM paper {n}", published_at=since.replace(day=10), updated_at=since,
        )])


def test_fetching_a_year_goes_month_by_month_and_keeps_the_feed_refresh_time(db_path, prefs, taxonomy) -> None:
    source = MonthSource()
    report = explore.fetch_period(lambda: db.session(db_path), source, prefs, taxonomy, 2023, now=lambda: NOW)
    assert report.months == 12 and report.inserted == 12 and len(source.windows) == 12
    with db.session(db_path) as conn:
        ranked = papers.list_papers(conn, PaperQuery(published_since=datetime(2023, 1, 1, tzinfo=timezone.utc), sort="timeless"))
        assert len(ranked) == 12 and all(p.timeless_score is not None for p in ranked)
        assert app_state.last_refresh(conn, "arxiv") is None  # the feed's "refreshed" time is untouched
        assert app_state.last_refresh(conn, explore.ARCHIVE_RUN) is not None


def test_months_in_the_future_are_skipped(db_path, prefs, taxonomy) -> None:
    source = MonthSource()
    report = explore.fetch_period(lambda: db.session(db_path), source, prefs, taxonomy, 2026, now=lambda: NOW)
    assert report.months == 10  # NOW is 19 October 2026


class IdSource:
    def __init__(self):
        self.requested = []

    def fetch_by_ids(self, ids):
        self.requested += ids
        # A cs.CV-only paper with little robotics wording: the topic filter would normally drop it.
        return [make_candidate(arxiv_id=i, title="Scaling image models", abstract="A vision model.", source_categories=("cs.CV",),
                               source_primary_category="cs.CV") for i in ids]


def test_adding_cited_papers_skips_the_robotics_filter_and_records_citations(conn, prefs, taxonomy) -> None:
    cited = [CitedPaper("Scaling image models", "2406.00001", 900, 90, "CVPR", 2024, ("A. Author",))]
    source = IdSource()
    ids = explore.add_cited_papers(conn, cited, source, prefs, taxonomy, NOW)
    assert source.requested == ["2406.00001"] and len(ids) == 1
    paper = papers.get_paper(conn, ids[0])
    assert (paper.citation_count, paper.influential_citation_count) == (900, 90)
    assert paper.relevance_score is not None
    explore.add_cited_papers(conn, cited, source, prefs, taxonomy, NOW)
    assert source.requested == ["2406.00001"]  # already in the library: not fetched again


class SearchSource:
    def __init__(self):
        self.calls = []

    def search(self, text, *, since, until, max_results):
        self.calls.append((text, since, until))
        return FetchResult(candidates=[make_candidate(
            arxiv_id="1503.00001", title="Monocular SLAM for small drones", abstract="A camera-only method.",
            published_at=since.replace(day=5), updated_at=since, source_categories=("cs.CV",), source_primary_category="cs.CV",
        )])


def test_searching_arxiv_for_a_period_adds_the_matches(conn, prefs, taxonomy) -> None:
    source = SearchSource()
    report = explore.search_arxiv(conn, source, prefs, taxonomy, "monocular slam", 2015, 3, now=NOW)
    assert (report.found, report.inserted) == (1, 1)
    text, since, until = source.calls[0]
    assert text == "monocular slam" and (since.month, until.month) == (3, 4)
    stored = papers.list_papers(conn, PaperQuery(search="drones"))
    assert stored and stored[0].relevance_score is not None
    assert papers.years_in_library(conn) == [2015]
