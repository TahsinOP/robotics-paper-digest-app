"""SQLite access for weekly reading queues. A week is identified by its Monday (ISO date)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from paper_radar.models import Paper
from paper_radar.repositories.papers import row_to_paper
from paper_radar.text import to_iso


@dataclass(frozen=True)
class QueueEntry:
    position: int
    paper: Paper
    carried_from: date | None


def list_week(conn: sqlite3.Connection, week_start: date) -> list[QueueEntry]:
    rows = conn.execute(
        "SELECT p.*, q.position AS q_position, q.carried_from AS q_carried_from"
        " FROM queue_items q JOIN papers p ON p.id = q.paper_id"
        " WHERE q.week_start = ? ORDER BY q.position",
        (week_start.isoformat(),),
    ).fetchall()
    return [
        QueueEntry(
            position=r["q_position"],
            paper=row_to_paper(r),
            carried_from=date.fromisoformat(r["q_carried_from"]) if r["q_carried_from"] else None,
        )
        for r in rows
    ]


def week_paper_ids(conn: sqlite3.Connection, week_start: date) -> list[int]:
    rows = conn.execute(
        "SELECT paper_id FROM queue_items WHERE week_start = ? ORDER BY position", (week_start.isoformat(),)
    ).fetchall()
    return [r["paper_id"] for r in rows]


def contains(conn: sqlite3.Connection, week_start: date, paper_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM queue_items WHERE week_start = ? AND paper_id = ?", (week_start.isoformat(), paper_id)
    ).fetchone()
    return row is not None


def add(
    conn: sqlite3.Connection, week_start: date, paper_id: int, now: datetime, carried_from: date | None = None
) -> bool:
    """Append to the end of the week's queue. Returns False if it was already there."""
    if contains(conn, week_start, paper_id):
        return False
    next_position = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM queue_items WHERE week_start = ?", (week_start.isoformat(),)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO queue_items (week_start, paper_id, position, added_at, carried_from) VALUES (?, ?, ?, ?, ?)",
        (week_start.isoformat(), paper_id, next_position, to_iso(now), carried_from.isoformat() if carried_from else None),
    )
    return True


def _renumber(conn: sqlite3.Connection, week_start: date, ordered_ids: list[int]) -> None:
    conn.executemany(
        "UPDATE queue_items SET position = ? WHERE week_start = ? AND paper_id = ?",
        [(i + 1, week_start.isoformat(), pid) for i, pid in enumerate(ordered_ids)],
    )


def remove(conn: sqlite3.Connection, week_start: date, paper_id: int) -> bool:
    cursor = conn.execute(
        "DELETE FROM queue_items WHERE week_start = ? AND paper_id = ?", (week_start.isoformat(), paper_id)
    )
    _renumber(conn, week_start, week_paper_ids(conn, week_start))
    return cursor.rowcount > 0


def move(conn: sqlite3.Connection, week_start: date, paper_id: int, offset: int) -> bool:
    """Move a paper up (negative offset) or down the week's order. Returns True if it moved."""
    ids = week_paper_ids(conn, week_start)
    if paper_id not in ids:
        return False
    old = ids.index(paper_id)
    new = max(0, min(len(ids) - 1, old + offset))
    if new == old:
        return False
    ids.insert(new, ids.pop(old))
    _renumber(conn, week_start, ids)
    return True


def weeks_containing(conn: sqlite3.Connection, paper_id: int) -> list[date]:
    rows = conn.execute(
        "SELECT week_start FROM queue_items WHERE paper_id = ? ORDER BY week_start", (paper_id,)
    ).fetchall()
    return [date.fromisoformat(r["week_start"]) for r in rows]
