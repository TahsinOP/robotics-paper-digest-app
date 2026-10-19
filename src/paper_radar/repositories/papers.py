"""SQLite access for papers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from paper_radar.models import (
    Classification,
    DigestSourceLevel,
    Paper,
    PaperCandidate,
    ResourceLinks,
    ScoreResult,
    Status,
    VenueSignal,
)
from paper_radar.services.deduplication import ExistingRecord
from paper_radar.services.normalization import content_hash, normalize_doi, normalize_title
from paper_radar.text import from_iso, to_iso


@dataclass(frozen=True)
class PaperQuery:
    search: str = ""
    statuses: tuple[Status, ...] = ()  # empty = any status
    categories: tuple[str, ...] = ()  # empty = any category
    tags: tuple[str, ...] = ()  # paper must carry at least one
    published_since: datetime | None = None
    with_links_only: bool = False
    venue_only: bool = False
    search_notes: bool = False
    sort: str = "relevance"  # "relevance" | "newest"
    limit: int | None = None
    offset: int = 0
    exclude_ids: tuple[int, ...] = field(default_factory=tuple)


def row_to_paper(row: sqlite3.Row) -> Paper:
    return Paper(
        id=row["id"],
        title=row["title"],
        authors=tuple(json.loads(row["authors_json"])),
        abstract=row["abstract"],
        source_name=row["source_name"],
        source_url=row["source_url"],
        pdf_url=row["pdf_url"],
        arxiv_id=row["arxiv_id"],
        arxiv_version=row["arxiv_version"],
        doi=row["doi"],
        published_at=from_iso(row["published_at"]),
        updated_at=from_iso(row["updated_at"]),
        source_categories=tuple(json.loads(row["source_categories_json"])),
        source_primary_category=row["source_primary_category"],
        author_comment=row["author_comment"],
        journal_ref=row["journal_ref"],
        code_url=row["code_url"],
        project_url=row["project_url"],
        venue_name=row["venue_name"],
        venue_tier=row["venue_tier"],
        venue_evidence=row["venue_evidence"],
        primary_category=row["primary_category"],
        secondary_tags=tuple(json.loads(row["secondary_tags_json"])),
        category_scores=json.loads(row["category_scores_json"]),
        category_confidence=row["category_confidence"],
        needs_review=bool(row["needs_review"]),
        relevance_score=row["relevance_score"],
        relevance_explanation=row["relevance_explanation"],
        score_breakdown=json.loads(row["score_breakdown_json"]) if row["score_breakdown_json"] else None,
        status=Status(row["status"]),
        status_changed_at=from_iso(row["status_changed_at"]),
        digest_source_level=DigestSourceLevel(row["digest_source_level"]),
        digest=json.loads(row["digest_json"]) if row["digest_json"] else None,
        digest_model=row["digest_model"],
        digest_created_at=from_iso(row["digest_created_at"]),
        digest_content_hash=row["digest_content_hash"],
        content_hash=row["content_hash"],
        personal_notes=row["personal_notes"],
        notes_updated_at=from_iso(row["notes_updated_at"]),
        dedup_note=row["dedup_note"],
        first_seen_at=from_iso(row["first_seen_at"]),
        last_seen_at=from_iso(row["last_seen_at"]),
    )


class SqliteRecordLookup:
    """Duplicate lookups against stored papers (see services.deduplication)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _record(row: sqlite3.Row) -> ExistingRecord:
        return ExistingRecord(
            id=row["id"],
            arxiv_id=row["arxiv_id"],
            doi=row["doi"],
            authors=tuple(json.loads(row["authors_json"])),
            published_at=from_iso(row["published_at"]),
        )

    def by_arxiv_id(self, arxiv_id: str) -> ExistingRecord | None:
        row = self.conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
        return self._record(row) if row else None

    def by_doi(self, doi: str) -> ExistingRecord | None:
        row = self.conn.execute("SELECT * FROM papers WHERE doi = ? ORDER BY id LIMIT 1", (doi,)).fetchone()
        return self._record(row) if row else None

    def by_title(self, title_norm: str) -> list[ExistingRecord]:
        rows = self.conn.execute("SELECT * FROM papers WHERE title_norm = ? ORDER BY id", (title_norm,)).fetchall()
        return [self._record(r) for r in rows]


def _metadata_values(c: PaperCandidate, links: ResourceLinks, venue: VenueSignal | None) -> dict:
    return {
        "arxiv_id": c.arxiv_id,
        "arxiv_version": c.arxiv_version,
        "doi": normalize_doi(c.doi),
        "title": c.title,
        "title_norm": normalize_title(c.title),
        "authors_json": json.dumps(list(c.authors), ensure_ascii=False),
        "abstract": c.abstract,
        "published_at": to_iso(c.published_at),
        "updated_at": to_iso(c.updated_at),
        "source_name": c.source_name,
        "source_url": c.source_url,
        "pdf_url": c.pdf_url,
        "source_categories_json": json.dumps(list(c.source_categories)),
        "source_primary_category": c.source_primary_category,
        "author_comment": c.author_comment,
        "journal_ref": c.journal_ref,
        "code_url": links.code_url,
        "project_url": links.project_url,
        "venue_name": venue.name if venue else None,
        "venue_tier": venue.tier if venue else None,
        "venue_evidence": venue.evidence if venue else None,
        "content_hash": content_hash(c),
    }


def _classification_values(cls: Classification, taxonomy_version: str) -> dict:
    return {
        "primary_category": cls.primary_category,
        "secondary_tags_json": json.dumps(list(cls.secondary_tags)),
        "category_scores_json": json.dumps(cls.category_scores, sort_keys=True),
        "category_confidence": cls.confidence,
        "needs_review": int(cls.needs_review),
        "classified_with": taxonomy_version,
    }


def insert_paper(
    conn: sqlite3.Connection,
    candidate: PaperCandidate,
    links: ResourceLinks,
    venue: VenueSignal | None,
    classification: Classification,
    taxonomy_version: str,
    now: datetime,
    dedup_note: str | None = None,
) -> int:
    values = {
        **_metadata_values(candidate, links, venue),
        **_classification_values(classification, taxonomy_version),
        "dedup_note": dedup_note,
        "first_seen_at": to_iso(now),
        "last_seen_at": to_iso(now),
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f":{k}" for k in values)
    cursor = conn.execute(f"INSERT INTO papers ({columns}) VALUES ({placeholders})", values)
    return int(cursor.lastrowid)


def stored_content_hash(conn: sqlite3.Connection, paper_id: int) -> str | None:
    row = conn.execute("SELECT content_hash FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return row["content_hash"] if row else None


def update_metadata(
    conn: sqlite3.Connection,
    paper_id: int,
    candidate: PaperCandidate,
    links: ResourceLinks,
    venue: VenueSignal | None,
    now: datetime,
) -> None:
    """Refresh source metadata; status, notes, queue membership and history are untouched."""
    values = _metadata_values(candidate, links, venue)
    if values["arxiv_id"] is None:
        values.pop("arxiv_id")  # keep an identifier learned from another source
    values["last_seen_at"] = to_iso(now)
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    conn.execute(f"UPDATE papers SET {assignments} WHERE id = :id", {**values, "id": paper_id})


def touch_seen(conn: sqlite3.Connection, paper_id: int, now: datetime) -> None:
    conn.execute("UPDATE papers SET last_seen_at = ? WHERE id = ?", (to_iso(now), paper_id))


def update_classification(
    conn: sqlite3.Connection, paper_id: int, classification: Classification, taxonomy_version: str
) -> None:
    values = _classification_values(classification, taxonomy_version)
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    conn.execute(f"UPDATE papers SET {assignments} WHERE id = :id", {**values, "id": paper_id})


def ids_needing_classification(conn: sqlite3.Connection, taxonomy_version: str) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM papers WHERE classified_with IS NULL OR classified_with != ?", (taxonomy_version,)
    ).fetchall()
    return [r["id"] for r in rows]


def iter_papers(conn: sqlite3.Connection, ids: Iterable[int] | None = None) -> Iterator[Paper]:
    if ids is None:
        rows = conn.execute("SELECT * FROM papers ORDER BY id")
    else:
        id_list = list(ids)
        if not id_list:
            return
        rows = conn.execute(
            f"SELECT * FROM papers WHERE id IN ({', '.join('?' * len(id_list))}) ORDER BY id", id_list
        )
    for row in rows:
        yield row_to_paper(row)


def update_signals(conn: sqlite3.Connection, paper_id: int, links: ResourceLinks, venue: VenueSignal | None) -> None:
    conn.execute(
        "UPDATE papers SET code_url = ?, project_url = ?, venue_name = ?, venue_tier = ?, venue_evidence = ?"
        " WHERE id = ?",
        (
            links.code_url,
            links.project_url,
            venue.name if venue else None,
            venue.tier if venue else None,
            venue.evidence if venue else None,
            paper_id,
        ),
    )


def save_scores(conn: sqlite3.Connection, scores: Iterable[tuple[int, ScoreResult]], now: datetime) -> int:
    stamp = to_iso(now)
    rows = [
        (s.total, s.explanation, json.dumps(s.to_dict(), ensure_ascii=False), stamp, paper_id)
        for paper_id, s in scores
    ]
    conn.executemany(
        "UPDATE papers SET relevance_score = ?, relevance_explanation = ?, score_breakdown_json = ?,"
        " scored_at = ? WHERE id = ?",
        rows,
    )
    return len(rows)


def get_paper(conn: sqlite3.Connection, paper_id: int) -> Paper | None:
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return row_to_paper(row) if row else None


def set_status(conn: sqlite3.Connection, paper_id: int, status: Status, now: datetime) -> None:
    conn.execute(
        "UPDATE papers SET status = ?, status_changed_at = ? WHERE id = ?",
        (Status(status).value, to_iso(now), paper_id),
    )


def set_notes(conn: sqlite3.Connection, paper_id: int, notes: str, now: datetime) -> None:
    conn.execute(
        "UPDATE papers SET personal_notes = ?, notes_updated_at = ? WHERE id = ?",
        (notes, to_iso(now), paper_id),
    )


def _like(term: str) -> str:
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(q: PaperQuery) -> tuple[str, list]:
    clauses, params = [], []
    if q.statuses:
        clauses.append(f"status IN ({', '.join('?' * len(q.statuses))})")
        params += [Status(s).value for s in q.statuses]
    if q.categories:
        clauses.append(f"primary_category IN ({', '.join('?' * len(q.categories))})")
        params += list(q.categories)
    if q.tags:
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(papers.secondary_tags_json) "
            f"WHERE json_each.value IN ({', '.join('?' * len(q.tags))}))"
        )
        params += list(q.tags)
    if q.published_since:
        clauses.append("published_at >= ?")
        params.append(to_iso(q.published_since))
    if q.with_links_only:
        clauses.append("(code_url IS NOT NULL OR project_url IS NOT NULL)")
    if q.venue_only:
        clauses.append("venue_name IS NOT NULL AND venue_tier != 'workshop'")
    if q.exclude_ids:
        clauses.append(f"id NOT IN ({', '.join('?' * len(q.exclude_ids))})")
        params += list(q.exclude_ids)
    fields = ["title", "abstract", "authors_json"] + (["personal_notes"] if q.search_notes else [])
    for term in q.search.split():
        clauses.append("(" + " OR ".join(f"lower({f}) LIKE ? ESCAPE '\\'" for f in fields) + ")")
        params += [_like(term)] * len(fields)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_papers(conn: sqlite3.Connection, q: PaperQuery) -> list[Paper]:
    where, params = _where(q)
    order = (
        "published_at DESC, relevance_score DESC"
        if q.sort == "newest"
        else "relevance_score DESC, published_at DESC"
    )
    sql = f"SELECT * FROM papers{where} ORDER BY {order}, id DESC"
    if q.limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [q.limit, q.offset]
    return [row_to_paper(r) for r in conn.execute(sql, params)]


def count_papers(conn: sqlite3.Connection, q: PaperQuery) -> int:
    where, params = _where(q)
    return conn.execute(f"SELECT COUNT(*) FROM papers{where}", params).fetchone()[0]


def tags_in_use(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT json_each.value AS tag, COUNT(*) AS n FROM papers, json_each(papers.secondary_tags_json)"
        " GROUP BY tag ORDER BY n DESC, tag"
    ).fetchall()
    return [r["tag"] for r in rows]


@dataclass(frozen=True)
class LibraryStats:
    total: int
    with_notes: int
    by_status: dict[str, int]


def library_stats(conn: sqlite3.Connection) -> LibraryStats:
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    with_notes = conn.execute("SELECT COUNT(*) FROM papers WHERE trim(personal_notes) != ''").fetchone()[0]
    by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM papers GROUP BY status")}
    return LibraryStats(total=total, with_notes=with_notes, by_status=by_status)
