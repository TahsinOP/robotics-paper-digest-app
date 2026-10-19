"""Library-wide maintenance and per-paper actions: rescoring, reclassification, status, notes."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from paper_radar.models import Paper, Status
from paper_radar.preferences import Preferences, fingerprint
from paper_radar.repositories import app_state, papers
from paper_radar.repositories import reading_queue as queue_repo
from paper_radar.services.classification import classify
from paper_radar.services.ranking import RankingInput, compile_keywords, score_paper
from paper_radar.services.signals import detect_venue, extract_links
from paper_radar.taxonomy import Taxonomy

SCORES_STAMP_KEY = "scores_stamp"


def ranking_input(paper: Paper) -> RankingInput:
    return RankingInput(
        title=paper.title,
        abstract=paper.abstract,
        primary_category=paper.primary_category,
        category_scores=paper.category_scores,
        published_at=paper.published_at,
        code_url=paper.code_url,
        project_url=paper.project_url,
        venue_name=paper.venue_name,
        venue_tier=paper.venue_tier,
        venue_evidence=paper.venue_evidence,
    )


def rescore_all(conn: sqlite3.Connection, prefs: Preferences, taxonomy: Taxonomy, now: datetime) -> int:
    keywords = compile_keywords(prefs.focus_keywords)
    results = [
        (paper.id, score_paper(ranking_input(paper), prefs, taxonomy, keywords, now))
        for paper in papers.iter_papers(conn)
    ]
    count = papers.save_scores(conn, results, now)
    app_state.set_json(conn, SCORES_STAMP_KEY, _scores_stamp(prefs, taxonomy, now), now)
    return count


def _scores_stamp(prefs: Preferences, taxonomy: Taxonomy, now: datetime) -> str:
    return f"{now.astimezone().date().isoformat()}|{fingerprint(prefs)}|{taxonomy.version}"


def ensure_scores_current(conn: sqlite3.Connection, prefs: Preferences, taxonomy: Taxonomy, now: datetime) -> bool:
    """Rescore when the day, the preferences or the taxonomy changed since the last scoring."""
    if app_state.get_json(conn, SCORES_STAMP_KEY) == _scores_stamp(prefs, taxonomy, now):
        return False
    rescore_all(conn, prefs, taxonomy, now)
    return True


def reclassify_stale(conn: sqlite3.Connection, taxonomy: Taxonomy) -> int:
    """Re-run classification and venue detection for papers analysed with an older taxonomy."""
    stale = papers.ids_needing_classification(conn, taxonomy.version)
    for paper in papers.iter_papers(conn, stale):
        papers.update_classification(
            conn, paper.id, classify(paper.title, paper.abstract, paper.source_primary_category, taxonomy), taxonomy.version
        )
        papers.update_signals(
            conn,
            paper.id,
            extract_links(paper.abstract, paper.author_comment),
            detect_venue(paper.author_comment, paper.journal_ref, taxonomy),
        )
    return len(stale)


def change_status(conn: sqlite3.Connection, paper_id: int, status: Status, now: datetime, current_week: date) -> None:
    """Set a paper's status. Moving a paper back to new or skipping it also takes it off this
    week's reading list; reading and read papers stay on it."""
    papers.set_status(conn, paper_id, status, now)
    if status in (Status.NEW, Status.SKIPPED):
        queue_repo.remove(conn, current_week, paper_id)


def save_notes(conn: sqlite3.Connection, paper_id: int, notes: str, now: datetime) -> None:
    papers.set_notes(conn, paper_id, notes.strip(), now)
