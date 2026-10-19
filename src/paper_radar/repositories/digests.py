"""SQLite access for AI digests, extracted full texts and the LLM call log."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from paper_radar.text import from_iso, to_iso

LEVEL_RANK = {"abstract_only": 0, "partial_full_text": 1, "full_text": 2}


@dataclass(frozen=True)
class DigestRecord:
    id: int
    paper_id: int
    source_level: str
    content_hash: str
    prompt_version: str
    model: str
    served_model: str
    digest: dict
    source_note: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    created_at: datetime


def _record(row: sqlite3.Row) -> DigestRecord:
    return DigestRecord(
        id=row["id"],
        paper_id=row["paper_id"],
        source_level=row["source_level"],
        content_hash=row["content_hash"],
        prompt_version=row["prompt_version"],
        model=row["model"],
        served_model=row["served_model"],
        digest=json.loads(row["digest_json"]),
        source_note=row["source_note"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        created_at=from_iso(row["created_at"]),
    )


def find(
    conn: sqlite3.Connection, paper_id: int, source_level: str, content_hash: str, prompt_version: str, model: str
) -> DigestRecord | None:
    row = conn.execute(
        "SELECT * FROM digests WHERE paper_id = ? AND source_level = ? AND content_hash = ?"
        " AND prompt_version = ? AND model = ?",
        (paper_id, source_level, content_hash, prompt_version, model),
    ).fetchone()
    return _record(row) if row else None


def save(
    conn: sqlite3.Connection,
    *,
    paper_id: int,
    source_level: str,
    content_hash: str,
    prompt_version: str,
    model: str,
    served_model: str,
    digest: dict,
    source_note: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    now: datetime,
) -> DigestRecord:
    conn.execute(
        "INSERT INTO digests (paper_id, source_level, content_hash, prompt_version, model, served_model,"
        " digest_json, source_note, input_tokens, output_tokens, cost_usd, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (paper_id, source_level, content_hash, prompt_version, model) DO UPDATE SET"
        " served_model = excluded.served_model, digest_json = excluded.digest_json,"
        " source_note = excluded.source_note, input_tokens = excluded.input_tokens,"
        " output_tokens = excluded.output_tokens, cost_usd = excluded.cost_usd, created_at = excluded.created_at",
        (
            paper_id, source_level, content_hash, prompt_version, model, served_model,
            json.dumps(digest, ensure_ascii=False), source_note, input_tokens, output_tokens, cost_usd, to_iso(now),
        ),
    )
    refresh_paper_digest(conn, paper_id)
    return find(conn, paper_id, source_level, content_hash, prompt_version, model)


def for_paper(conn: sqlite3.Connection, paper_id: int) -> list[DigestRecord]:
    rows = conn.execute("SELECT * FROM digests WHERE paper_id = ? ORDER BY created_at DESC", (paper_id,)).fetchall()
    return [_record(r) for r in rows]


def best_for_paper(conn: sqlite3.Connection, paper_id: int) -> DigestRecord | None:
    """Prefer digests of the current paper version, then the deepest source level, then the newest."""
    row = conn.execute("SELECT content_hash FROM papers WHERE id = ?", (paper_id,)).fetchone()
    current = row["content_hash"] if row else None
    records = for_paper(conn, paper_id)
    if not records:
        return None
    return max(records, key=lambda r: (r.content_hash == current, LEVEL_RANK[r.source_level], r.created_at))


def refresh_paper_digest(conn: sqlite3.Connection, paper_id: int) -> None:
    """Mirror the best digest onto the papers row, which the feed reads."""
    best = best_for_paper(conn, paper_id)
    conn.execute(
        "UPDATE papers SET digest_json = ?, digest_source_level = ?, digest_model = ?, digest_created_at = ?,"
        " digest_content_hash = ?, digest_prompt_version = ? WHERE id = ?",
        (
            json.dumps(best.digest, ensure_ascii=False) if best else None,
            best.source_level if best else "not_generated",
            best.served_model if best else None,
            to_iso(best.created_at) if best else None,
            best.content_hash if best else None,
            best.prompt_version if best else None,
            paper_id,
        ),
    )


@dataclass(frozen=True)
class StoredText:
    text: str
    page_count: int
    pages_included: int
    references_removed: bool
    truncated: bool


def get_text(conn: sqlite3.Connection, paper_id: int, content_hash: str) -> StoredText | None:
    row = conn.execute(
        "SELECT * FROM paper_texts WHERE paper_id = ? AND content_hash = ?", (paper_id, content_hash)
    ).fetchone()
    if row is None:
        return None
    return StoredText(
        text=row["text"],
        page_count=row["page_count"],
        pages_included=row["pages_included"],
        references_removed=bool(row["references_removed"]),
        truncated=bool(row["truncated"]),
    )


def save_text(
    conn: sqlite3.Connection, paper_id: int, content_hash: str, source_url: str, text: StoredText, now: datetime
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO paper_texts (paper_id, content_hash, source_url, text, page_count, pages_included,"
        " references_removed, truncated, extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            paper_id, content_hash, source_url, text.text, text.page_count, text.pages_included,
            int(text.references_removed), int(text.truncated), to_iso(now),
        ),
    )


def record_call(
    conn: sqlite3.Connection,
    *,
    paper_id: int | None,
    provider: str,
    model: str,
    purpose: str,
    status: str,
    error_kind: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    duration_ms: int,
    now: datetime,
) -> None:
    conn.execute(
        "INSERT INTO llm_calls (paper_id, provider, model, purpose, status, error_kind, input_tokens, output_tokens,"
        " cost_usd, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (paper_id, provider, model, purpose, status, error_kind, input_tokens, output_tokens, cost_usd, duration_ms, to_iso(now)),
    )


@dataclass(frozen=True)
class UsageSummary:
    calls: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    digests: int


def usage_since(conn: sqlite3.Connection, since: datetime) -> UsageSummary:
    row = conn.execute(
        "SELECT COUNT(*) AS calls, SUM(status = 'error') AS failed, COALESCE(SUM(input_tokens), 0) AS inp,"
        " COALESCE(SUM(output_tokens), 0) AS outp, COALESCE(SUM(cost_usd), 0) AS cost"
        " FROM llm_calls WHERE created_at >= ?",
        (to_iso(since),),
    ).fetchone()
    digests = conn.execute("SELECT COUNT(*) FROM digests WHERE created_at >= ?", (to_iso(since),)).fetchone()[0]
    return UsageSummary(
        calls=row["calls"], failed=row["failed"] or 0, input_tokens=row["inp"], output_tokens=row["outp"],
        cost_usd=row["cost"], digests=digests,
    )


def average_cost(conn: sqlite3.Connection, source_level: str, model: str) -> float | None:
    """Average cost of past digests of this kind, used for cost hints before generating."""
    row = conn.execute(
        "SELECT AVG(cost_usd) FROM digests WHERE source_level = ? AND model = ? AND cost_usd IS NOT NULL",
        (source_level, model),
    ).fetchone()
    return row[0]
