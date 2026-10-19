import sqlite3
from datetime import timedelta

import pytest

from paper_radar import db
from paper_radar.models import ResourceLinks, Status, VenueSignal
from paper_radar.repositories import app_state, papers
from paper_radar.repositories import reading_queue as queue_repo
from paper_radar.repositories.papers import PaperQuery

from .conftest import NOW, make_candidate, store


def test_fresh_database_is_at_latest_schema(db_path) -> None:
    with db.session(db_path) as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS)
        assert db.migrate(conn, db_path) == len(db.MIGRATIONS)  # idempotent


def test_migrating_existing_data_makes_a_backup(db_path, monkeypatch) -> None:
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + ("CREATE TABLE extra (x INTEGER);",))
    with db.session(db_path) as conn:
        assert db.migrate(conn, db_path) == len(db.MIGRATIONS)
    backups = list((db_path.parent / "backups").glob("*.db"))
    assert len(backups) == 1


def test_refuses_a_newer_schema(db_path) -> None:
    with db.session(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
        with pytest.raises(RuntimeError, match="newer than this app"):
            db.migrate(conn)


def test_insert_and_read_back(conn, taxonomy) -> None:
    paper_id = store(conn, taxonomy)
    paper = papers.get_paper(conn, paper_id)
    assert paper.title == "Visual-Inertial Odometry for Agile Quadrotors"
    assert paper.authors == ("Ada Lovelace", "Alan Turing")
    assert paper.status == Status.NEW
    assert paper.primary_category == "slam_perception"
    assert paper.digest_source_level.value == "not_generated"


def test_status_and_notes_survive_metadata_updates_and_restarts(db_path, taxonomy) -> None:
    with db.session(db_path) as conn:
        paper_id = store(conn, taxonomy)
        papers.set_status(conn, paper_id, Status.READING, NOW)
        papers.set_notes(conn, paper_id, "Check Fig. 3", NOW)
        revised = make_candidate(abstract="Revised abstract.", arxiv_version=2)
        papers.update_metadata(conn, paper_id, revised, ResourceLinks(code_url="https://github.com/a/b"), None, NOW)
    with db.session(db_path) as conn:  # a new connection, as after an app restart
        paper = papers.get_paper(conn, paper_id)
    assert paper.status == Status.READING
    assert paper.personal_notes == "Check Fig. 3"
    assert paper.abstract == "Revised abstract." and paper.arxiv_version == 2
    assert paper.code_url == "https://github.com/a/b"


def test_list_papers_filters_and_sorts(conn, taxonomy) -> None:
    slam = store(conn, taxonomy)
    manip = store(
        conn, taxonomy, arxiv_id="2610.00002", title="Bimanual Cloth Folding with Action Chunking",
        abstract="A bimanual robot folds garments.", published_at=NOW - timedelta(days=10),
    )
    papers.save_scores(conn, [(slam, _score(80)), (manip, _score(60))], NOW)
    papers.update_signals(conn, manip, ResourceLinks(code_url="https://github.com/x/y"), VenueSignal("CoRL 2026", "top", "accepted"))
    papers.set_status(conn, manip, Status.SHORTLISTED, NOW)

    ids = lambda q: [p.id for p in papers.list_papers(conn, q)]  # noqa: E731
    assert ids(PaperQuery()) == [slam, manip]
    assert ids(PaperQuery(sort="newest")) == [slam, manip]
    assert ids(PaperQuery(statuses=(Status.SHORTLISTED,))) == [manip]
    assert ids(PaperQuery(categories=("manipulation",))) == [manip]
    assert ids(PaperQuery(tags=("Bimanual",))) == [manip]
    assert ids(PaperQuery(search="cloth folding")) == [manip]
    assert ids(PaperQuery(search="lovelace")) == [slam, manip]
    assert ids(PaperQuery(with_links_only=True)) == [manip]
    assert ids(PaperQuery(venue_only=True)) == [manip]
    assert ids(PaperQuery(published_since=NOW - timedelta(days=5))) == [slam]
    assert ids(PaperQuery(limit=1, offset=1)) == [manip]
    assert papers.count_papers(conn, PaperQuery(statuses=(Status.NEW,))) == 1
    assert "Bimanual" in papers.tags_in_use(conn)


def test_search_notes_only_when_asked(conn, taxonomy) -> None:
    paper_id = store(conn, taxonomy)
    papers.set_notes(conn, paper_id, "reminds me of MSCKF", NOW)
    assert papers.count_papers(conn, PaperQuery(search="msckf")) == 0
    assert papers.count_papers(conn, PaperQuery(search="msckf", search_notes=True)) == 1


def test_search_escapes_like_wildcards(conn, taxonomy) -> None:
    store(conn, taxonomy)
    assert papers.count_papers(conn, PaperQuery(search="100%")) == 0
    assert papers.count_papers(conn, PaperQuery(search="_")) == 0


def test_queue_add_move_remove(conn, taxonomy) -> None:
    week = NOW.date()
    a, b, c = (store(conn, taxonomy, arxiv_id=f"2610.0000{i}", title=f"Paper {i}") for i in (1, 2, 3))
    for pid in (a, b, c):
        assert queue_repo.add(conn, week, pid, NOW)
    assert not queue_repo.add(conn, week, a, NOW)
    assert queue_repo.week_paper_ids(conn, week) == [a, b, c]
    assert queue_repo.move(conn, week, c, -1)
    assert queue_repo.week_paper_ids(conn, week) == [a, c, b]
    assert not queue_repo.move(conn, week, a, -1)
    assert queue_repo.remove(conn, week, a)
    entries = queue_repo.list_week(conn, week)
    assert [(e.position, e.paper.id) for e in entries] == [(1, c), (2, b)]


def test_settings_and_refresh_history(conn) -> None:
    app_state.set_json(conn, "k", {"a": 1}, NOW)
    app_state.set_json(conn, "k", {"a": 2}, NOW)
    assert app_state.get_json(conn, "k") == {"a": 2}
    assert app_state.last_refresh(conn) is None
    run = app_state.RefreshRun("arxiv", NOW, NOW, "partial", 10, 8, 1, 1, ("timeout",))
    app_state.record_refresh(conn, run)
    assert app_state.last_refresh(conn) == run


def test_status_values_are_constrained(conn, taxonomy) -> None:
    paper_id = store(conn, taxonomy)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE papers SET status = 'archived' WHERE id = ?", (paper_id,))


def _score(total):
    from paper_radar.models import ScoreResult

    return ScoreResult(total=total, explanation="test", components={}, matched_keywords=())
