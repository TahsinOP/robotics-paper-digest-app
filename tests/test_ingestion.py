from dataclasses import replace

from paper_radar.models import Status
from paper_radar.repositories import app_state, papers
from paper_radar.repositories.papers import PaperQuery
from paper_radar.services.ingestion import refresh_papers
from paper_radar.sources.arxiv_source import parse_feed

from .conftest import FIXTURES, NOW, FakeSource

FEED = parse_feed((FIXTURES / "arxiv_page.xml").read_bytes()).entries


def test_refresh_stores_classifies_and_scores(conn, prefs, taxonomy) -> None:
    report = refresh_papers(conn, FakeSource(FEED), prefs, taxonomy, now=NOW)
    assert (report.fetched, report.inserted, report.off_topic, report.status) == (3, 2, 1, "ok")

    stored = papers.list_papers(conn, PaperQuery())
    assert len(stored) == 2  # the satellite-imagery paper was filtered out as off-topic
    lio = next(p for p in stored if p.arxiv_id == "2610.11111")
    assert lio.primary_category == "slam_perception"
    assert lio.code_url == "https://github.com/example/lio-uav"
    assert lio.venue_name == "IROS 2026"
    assert 0 < lio.relevance_score <= 100 and lio.relevance_explanation
    cloth = next(p for p in stored if p.arxiv_id == "2610.22222")
    assert cloth.venue_name is None  # "Submitted to ICRA 2027" does not count
    assert app_state.last_refresh(conn).inserted == 2


def test_repeated_refresh_creates_no_duplicates(conn, prefs, taxonomy) -> None:
    source = FakeSource(FEED)
    refresh_papers(conn, source, prefs, taxonomy, now=NOW)
    again = refresh_papers(conn, source, prefs, taxonomy, now=NOW)
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 2)
    assert again.summary().startswith("Already up to date")
    assert papers.count_papers(conn, PaperQuery()) == 2


def test_revised_paper_updates_metadata_but_keeps_your_status_and_notes(conn, prefs, taxonomy) -> None:
    refresh_papers(conn, FakeSource(FEED), prefs, taxonomy, now=NOW)
    paper = next(p for p in papers.list_papers(conn, PaperQuery()) if p.arxiv_id == "2610.11111")
    papers.set_status(conn, paper.id, Status.READING, NOW)
    papers.set_notes(conn, paper.id, "Compare with FAST-LIO2", NOW)

    revised = [replace(FEED[0], arxiv_version=3, abstract=FEED[0].abstract + " Now with ablations.")] + FEED[1:]
    report = refresh_papers(conn, FakeSource(revised), prefs, taxonomy, now=NOW)
    assert report.updated == 1
    after = papers.get_paper(conn, paper.id)
    assert after.arxiv_version == 3 and after.abstract.endswith("Now with ablations.")
    assert after.status == Status.READING and after.personal_notes == "Compare with FAST-LIO2"


def test_partial_failure_is_reported_and_keeps_what_arrived(conn, prefs, taxonomy) -> None:
    report = refresh_papers(conn, FakeSource(FEED[:1], errors=["Related categories: arXiv is busy."]), prefs, taxonomy, now=NOW)
    assert report.status == "partial" and report.inserted == 1
    assert app_state.last_refresh(conn).errors == ("Related categories: arXiv is busy.",)


def test_failed_source_leaves_existing_papers_untouched(conn, prefs, taxonomy) -> None:
    refresh_papers(conn, FakeSource(FEED), prefs, taxonomy, now=NOW)
    report = refresh_papers(conn, FakeSource([], errors=["Robotics (cs.RO): arXiv could not be reached."]), prefs, taxonomy, now=NOW)
    assert report.status == "failed" and report.summary() == "No papers could be fetched."
    assert papers.count_papers(conn, PaperQuery()) == 2
    assert app_state.last_refresh(conn).status == "failed"
