"""The weekly reading list: choose three or four papers, read them, carry over what's left."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from paper_radar.models import Paper, Status
from paper_radar.repositories import papers
from paper_radar.repositories import reading_queue as queue_repo
from paper_radar.repositories.reading_queue import QueueEntry
from paper_radar.taxonomy import Taxonomy

FINISHED = (Status.READ, Status.SKIPPED)


def week_start(day: date) -> date:
    """The Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def previous_week(week: date) -> date:
    return week - timedelta(days=7)


def add_to_week(conn: sqlite3.Connection, paper_id: int, week: date, now: datetime) -> bool:
    """Schedule a paper for the week. A new or skipped paper becomes shortlisted."""
    added = queue_repo.add(conn, week, paper_id, now)
    paper = papers.get_paper(conn, paper_id)
    if paper and paper.status in (Status.NEW, Status.SKIPPED):
        papers.set_status(conn, paper_id, Status.SHORTLISTED, now)
    return added


def remove_from_week(conn: sqlite3.Connection, paper_id: int, week: date) -> bool:
    return queue_repo.remove(conn, week, paper_id)


def move(conn: sqlite3.Connection, paper_id: int, week: date, offset: int) -> bool:
    return queue_repo.move(conn, week, paper_id, offset)


def entries(conn: sqlite3.Connection, week: date) -> list[QueueEntry]:
    return queue_repo.list_week(conn, week)


def candidates(conn: sqlite3.Connection, week: date, limit: int = 30) -> list[Paper]:
    """Shortlisted papers that are not on this week's list yet, best first."""
    return papers.list_papers(
        conn,
        papers.PaperQuery(
            statuses=(Status.SHORTLISTED,),
            exclude_ids=tuple(queue_repo.week_paper_ids(conn, week)),
            limit=limit,
        ),
    )


@dataclass(frozen=True)
class WeekReview:
    week: date
    total: int
    read: int
    with_notes: int
    unfinished: tuple[int, ...]  # paper ids neither read nor skipped


def review_week(conn: sqlite3.Connection, week: date) -> WeekReview:
    week_entries = queue_repo.list_week(conn, week)
    return WeekReview(
        week=week,
        total=len(week_entries),
        read=sum(e.paper.status == Status.READ for e in week_entries),
        with_notes=sum(bool(e.paper.personal_notes.strip()) for e in week_entries),
        unfinished=tuple(e.paper.id for e in week_entries if e.paper.status not in FINISHED),
    )


def carry_over(conn: sqlite3.Connection, from_week: date, to_week: date, now: datetime) -> int:
    """Copy unfinished papers from one week onto another. Returns how many were added."""
    added = 0
    for paper_id in review_week(conn, from_week).unfinished:
        if queue_repo.add(conn, to_week, paper_id, now, carried_from=from_week):
            added += 1
    return added


def topic_mix(week_entries: list[QueueEntry], taxonomy: Taxonomy) -> list[tuple[str, int]]:
    counts = Counter(taxonomy.category(e.paper.primary_category).short for e in week_entries)
    return counts.most_common()
