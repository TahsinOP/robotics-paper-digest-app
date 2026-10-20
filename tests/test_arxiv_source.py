from datetime import datetime, timezone

import pytest
import requests

from paper_radar.sources.arxiv_source import ArxivFeedError, ArxivSource, RateLimiter, parse_feed
from paper_radar.sources.base import SourceError

from .conftest import FIXTURES, NOW

PAGE = (FIXTURES / "arxiv_page.xml").read_bytes()
ERROR = (FIXTURES / "arxiv_error.xml").read_bytes()


class FakeResponse:
    def __init__(self, status_code=200, content=PAGE):
        self.status_code = status_code
        self.content = content


class FakeSession:
    """Returns queued responses (or raises queued exceptions) and records request params."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.requests.append(params)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def make_source(responses, **kwargs):
    clock = FakeClock()
    source = ArxivSource(
        session=FakeSession(responses),
        rate_limiter=RateLimiter(3.0, clock=clock, sleep=clock.sleep),
        sleep=clock.sleep,
        **kwargs,
    )
    return source, clock


def test_parse_feed_normalises_entries() -> None:
    page = parse_feed(PAGE)
    assert page.total_results == 3
    first, second, third = page.entries
    assert first.arxiv_id == "2610.11111" and first.arxiv_version == 2
    assert first.title == "Tightly Coupled LiDAR-Inertial Odometry with Loop Closure for UAVs"
    assert first.authors == ("Ada Lovelace", "Alan Turing")
    assert first.published_at == datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)
    assert first.source_url == "https://arxiv.org/abs/2610.11111"
    assert first.pdf_url == "https://arxiv.org/pdf/2610.11111"
    assert first.author_comment.startswith("Accepted to IROS 2026")
    assert second.doi == "10.1234/abc.5678"
    assert second.source_categories == ("cs.RO", "cs.LG")
    assert third.source_primary_category == "cs.CV"


def test_parse_feed_raises_on_error_feed_and_garbage() -> None:
    with pytest.raises(ArxivFeedError, match="incorrect id format"):
        parse_feed(ERROR)
    with pytest.raises(ArxivFeedError):
        parse_feed(b"<html>not a feed")


def test_requests_are_spaced_three_seconds_apart() -> None:
    source, clock = make_source([FakeResponse(), FakeResponse()])
    source.fetch_page("cat:cs.RO", 0, 10)
    source.fetch_page("cat:cs.RO", 10, 10)
    assert clock.sleeps == [3.0]


def test_retries_transient_failures_then_succeeds() -> None:
    source, _ = make_source([FakeResponse(503), requests.Timeout(), FakeResponse(content=ERROR), FakeResponse()])
    page = source.fetch_page("cat:cs.RO", 0, 10)
    assert len(page.entries) == 3
    assert len(source.session.requests) == 4


def test_gives_up_with_a_readable_message() -> None:
    source, _ = make_source([requests.Timeout()] * 4, timeout=30)
    with pytest.raises(SourceError, match="did not respond within 30 seconds"):
        source.fetch_page("cat:cs.RO", 0, 10)


def test_client_errors_are_not_retried() -> None:
    source, _ = make_source([FakeResponse(400)])
    with pytest.raises(SourceError, match="HTTP 400"):
        source.fetch_page("cat:cs.RO", 0, 10)
    assert len(source.session.requests) == 1


def test_queries_cover_cs_ro_and_neighbouring_categories() -> None:
    source, _ = make_source([], cross_list_categories=("cs.CV", "cs.LG"), cross_list_terms=("robot", "vision-language-action"))
    since = datetime(2026, 10, 5, tzinfo=timezone.utc)
    primary, cross = source.build_queries(since, NOW, 1500)
    assert primary.search_query == "cat:cs.RO AND submittedDate:[202610050000 TO 202610191200]"
    assert '(cat:cs.CV OR cat:cs.LG) AND (abs:robot OR abs:"vision-language-action")' in cross.search_query
    assert cross.search_query.endswith("ANDNOT cat:cs.RO")
    assert primary.cap + cross.cap == 1500


def test_fetch_recent_keeps_results_when_one_query_fails() -> None:
    source, _ = make_source([FakeResponse(), FakeResponse(400)])
    result = source.fetch_recent(since=NOW, until=NOW, max_results=1500)
    assert len(result.candidates) == 3
    assert len(result.errors) == 1 and result.errors[0].startswith("Related categories")


def feed_xml(ids: list[str], total: int) -> bytes:
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/{i}v1</id><title>Robot paper {i}</title>"
        f"<summary>A robot.</summary><published>2026-10-16T08:00:00Z</published>"
        f'<category term="cs.RO"/></entry>'
        for i in ids
    )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        f"<opensearch:totalResults>{total}</opensearch:totalResults>{entries}</feed>"
    ).encode()


def test_pagination_walks_pages_until_total() -> None:
    pages = [FakeResponse(content=feed_xml(["2610.00001", "2610.00002"], 3)), FakeResponse(content=feed_xml(["2610.00003"], 3))]
    source, _ = make_source(pages, page_size=2, cross_list_categories=())
    result = source.fetch_recent(since=NOW, until=NOW, max_results=100)
    assert [c.arxiv_id for c in result.candidates] == ["2610.00001", "2610.00002", "2610.00003"]
    assert [r["start"] for r in source.session.requests] == [0, 2]


def test_empty_page_before_total_is_retried() -> None:
    pages = [FakeResponse(content=feed_xml([], 2)), FakeResponse(content=feed_xml(["2610.00001", "2610.00002"], 2))]
    source, _ = make_source(pages)
    assert len(source.fetch_page("cat:cs.RO", 0, 10).entries) == 2


def test_keyword_search_uses_relevance_within_robotics_categories() -> None:
    source, _ = make_source([FakeResponse()], cross_list_categories=("cs.CV",))
    result = source.search('loop closure "pose graph"', since=datetime(2015, 1, 1, tzinfo=timezone.utc),
                           until=datetime(2016, 1, 1, tzinfo=timezone.utc), max_results=50)
    params = source.session.requests[0]
    assert params["sortBy"] == "relevance"
    assert params["search_query"] == (
        '(all:loop AND all:closure AND all:"pose graph") AND (cat:cs.RO OR cat:cs.CV) '
        "AND submittedDate:[201501010000 TO 201601010000]"
    )
    assert len(result.candidates) == 3
    assert source.search("   ", since=NOW, until=NOW).candidates == []
