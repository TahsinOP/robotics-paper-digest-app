from datetime import date, timedelta

from paper_radar.models import Status
from paper_radar.repositories import papers
from paper_radar.repositories import reading_queue as queue_repo
from paper_radar.services import library, reading_queue

from .conftest import NOW, store

WEEK = date(2026, 10, 19)  # a Monday
LAST_WEEK = WEEK - timedelta(days=7)


def test_week_start_is_monday() -> None:
    assert reading_queue.week_start(date(2026, 10, 19)) == WEEK
    assert reading_queue.week_start(date(2026, 10, 25)) == WEEK  # Sunday
    assert reading_queue.previous_week(WEEK) == LAST_WEEK


def test_adding_a_new_paper_shortlists_it(conn, taxonomy) -> None:
    pid = store(conn, taxonomy)
    assert reading_queue.add_to_week(conn, pid, WEEK, NOW)
    assert papers.get_paper(conn, pid).status == Status.SHORTLISTED
    assert not reading_queue.add_to_week(conn, pid, WEEK, NOW)


def test_skipping_or_unshortlisting_removes_from_this_week_but_reading_does_not(conn, taxonomy) -> None:
    a, b, c = (store(conn, taxonomy, arxiv_id=f"2610.0000{i}", title=f"Paper {i}") for i in (1, 2, 3))
    for pid in (a, b, c):
        reading_queue.add_to_week(conn, pid, WEEK, NOW)
    library.change_status(conn, a, Status.SKIPPED, NOW, WEEK)
    library.change_status(conn, b, Status.NEW, NOW, WEEK)
    library.change_status(conn, c, Status.READING, NOW, WEEK)
    assert queue_repo.week_paper_ids(conn, WEEK) == [c]


def test_candidates_are_shortlisted_papers_not_yet_scheduled(conn, taxonomy) -> None:
    a, b = (store(conn, taxonomy, arxiv_id=f"2610.0000{i}", title=f"Paper {i}") for i in (1, 2))
    papers.set_status(conn, a, Status.SHORTLISTED, NOW)
    papers.set_status(conn, b, Status.SHORTLISTED, NOW)
    reading_queue.add_to_week(conn, a, WEEK, NOW)
    assert [p.id for p in reading_queue.candidates(conn, WEEK)] == [b]


def test_review_and_carry_over(conn, taxonomy) -> None:
    a, b, c = (store(conn, taxonomy, arxiv_id=f"2610.0000{i}", title=f"Paper {i}") for i in (1, 2, 3))
    for pid in (a, b, c):
        reading_queue.add_to_week(conn, pid, LAST_WEEK, NOW)
    papers.set_status(conn, a, Status.READ, NOW)
    papers.set_notes(conn, a, "Great ablations", NOW)
    papers.set_status(conn, b, Status.READING, NOW)

    review = reading_queue.review_week(conn, LAST_WEEK)
    assert (review.total, review.read, review.with_notes) == (3, 1, 1)
    assert set(review.unfinished) == {b, c}

    assert reading_queue.carry_over(conn, LAST_WEEK, WEEK, NOW) == 2
    assert reading_queue.carry_over(conn, LAST_WEEK, WEEK, NOW) == 0  # nothing twice
    entries = reading_queue.entries(conn, WEEK)
    assert {e.paper.id for e in entries} == {b, c}
    assert all(e.carried_from == LAST_WEEK for e in entries)


def test_topic_mix(conn, taxonomy) -> None:
    a = store(conn, taxonomy)
    b = store(conn, taxonomy, arxiv_id="2610.00002", title="Bimanual Cloth Folding", abstract="A bimanual robot folds cloth.")
    for pid in (a, b):
        reading_queue.add_to_week(conn, pid, WEEK, NOW)
    mix = dict(reading_queue.topic_mix(reading_queue.entries(conn, WEEK), taxonomy))
    assert mix == {"SLAM & perception": 1, "Manipulation": 1}
