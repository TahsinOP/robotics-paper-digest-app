"""SQLite connection handling and schema migrations.

Migrations are an ordered list; `PRAGMA user_version` records how many have run. Before
migrating a database that already holds data, a copy is written to data/backups/.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE papers (
        id INTEGER PRIMARY KEY,
        arxiv_id TEXT UNIQUE,
        arxiv_version INTEGER,
        doi TEXT,
        title TEXT NOT NULL,
        title_norm TEXT NOT NULL,
        authors_json TEXT NOT NULL DEFAULT '[]',
        abstract TEXT NOT NULL DEFAULT '',
        published_at TEXT,
        updated_at TEXT,
        source_name TEXT NOT NULL,
        source_url TEXT,
        pdf_url TEXT,
        source_categories_json TEXT NOT NULL DEFAULT '[]',
        source_primary_category TEXT,
        author_comment TEXT,
        journal_ref TEXT,
        code_url TEXT,
        project_url TEXT,
        venue_name TEXT,
        venue_tier TEXT,
        venue_evidence TEXT,
        primary_category TEXT,
        secondary_tags_json TEXT NOT NULL DEFAULT '[]',
        category_scores_json TEXT NOT NULL DEFAULT '{}',
        category_confidence REAL,
        needs_review INTEGER NOT NULL DEFAULT 0,
        classified_with TEXT,
        relevance_score REAL,
        relevance_explanation TEXT,
        score_breakdown_json TEXT,
        scored_at TEXT,
        status TEXT NOT NULL DEFAULT 'new'
            CHECK (status IN ('new', 'shortlisted', 'reading', 'read', 'skipped')),
        status_changed_at TEXT,
        digest_json TEXT,
        digest_source_level TEXT NOT NULL DEFAULT 'not_generated'
            CHECK (digest_source_level IN ('not_generated', 'abstract_only', 'partial_full_text', 'full_text')),
        digest_model TEXT,
        digest_created_at TEXT,
        personal_notes TEXT NOT NULL DEFAULT '',
        notes_updated_at TEXT,
        dedup_note TEXT,
        content_hash TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    );
    CREATE INDEX idx_papers_doi ON papers(doi);
    CREATE INDEX idx_papers_title_norm ON papers(title_norm);
    CREATE INDEX idx_papers_status ON papers(status);
    CREATE INDEX idx_papers_published ON papers(published_at);
    CREATE INDEX idx_papers_score ON papers(relevance_score);

    CREATE TABLE queue_items (
        week_start TEXT NOT NULL,
        paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        added_at TEXT NOT NULL,
        carried_from TEXT,
        PRIMARY KEY (week_start, paper_id)
    );

    CREATE TABLE app_settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE refresh_runs (
        id INTEGER PRIMARY KEY,
        source_name TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'failed')),
        fetched INTEGER NOT NULL DEFAULT 0,
        inserted INTEGER NOT NULL DEFAULT 0,
        updated INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        errors_json TEXT NOT NULL DEFAULT '[]'
    );
    """,
    # 2: Phase 2 digests. Digests are cached per (paper, source level, paper version, prompt
    # version, model); papers.digest_* mirrors the best current digest for quick listing.
    """
    CREATE TABLE digests (
        id INTEGER PRIMARY KEY,
        paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        source_level TEXT NOT NULL CHECK (source_level IN ('abstract_only', 'partial_full_text', 'full_text')),
        content_hash TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        model TEXT NOT NULL,
        served_model TEXT NOT NULL,
        digest_json TEXT NOT NULL,
        source_note TEXT NOT NULL,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cost_usd REAL,
        created_at TEXT NOT NULL,
        UNIQUE (paper_id, source_level, content_hash, prompt_version, model)
    );
    CREATE INDEX idx_digests_paper ON digests(paper_id);

    CREATE TABLE paper_texts (
        paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        content_hash TEXT NOT NULL,
        source_url TEXT NOT NULL,
        text TEXT NOT NULL,
        page_count INTEGER NOT NULL,
        pages_included INTEGER NOT NULL,
        references_removed INTEGER NOT NULL,
        truncated INTEGER NOT NULL,
        extracted_at TEXT NOT NULL,
        PRIMARY KEY (paper_id, content_hash)
    );

    CREATE TABLE llm_calls (
        id INTEGER PRIMARY KEY,
        paper_id INTEGER REFERENCES papers(id) ON DELETE SET NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        purpose TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
        error_kind TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cost_usd REAL,
        duration_ms INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    ALTER TABLE papers ADD COLUMN digest_content_hash TEXT;
    ALTER TABLE papers ADD COLUMN digest_prompt_version TEXT;
    """,
    # 3: past years and citations. timeless_score is the relevance score without the recency
    # factor, for ranking old papers; citation counts come from Semantic Scholar.
    """
    ALTER TABLE papers ADD COLUMN timeless_score REAL;
    ALTER TABLE papers ADD COLUMN citation_count INTEGER;
    ALTER TABLE papers ADD COLUMN influential_citation_count INTEGER;
    ALTER TABLE papers ADD COLUMN citations_checked_at TEXT;
    CREATE INDEX idx_papers_timeless ON papers(timeless_score);
    ALTER TABLE digests ADD COLUMN engine TEXT NOT NULL DEFAULT 'api';
    """,
)


def connect(db_path: Path | str) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, db_path: Path | None = None) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    current = schema_version(conn)
    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"The database schema (v{current}) is newer than this app (v{len(MIGRATIONS)}). Update the app."
        )
    if current == len(MIGRATIONS):
        return current
    if current > 0 and db_path is not None:
        backup(conn, db_path, current)
    for version in range(current, len(MIGRATIONS)):
        conn.executescript(f"BEGIN; {MIGRATIONS[version]} PRAGMA user_version = {version + 1}; COMMIT;")
    return schema_version(conn)


def backup(conn: sqlite3.Connection, db_path: Path, version: int) -> Path:
    target_dir = Path(db_path).parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{Path(db_path).stem}-v{version}-{stamp}.db"
    with sqlite3.connect(target) as dest:
        conn.backup(dest)
    return target


def initialise(db_path: Path) -> None:
    """Create the database file if needed, enable WAL and run migrations."""
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn, db_path)
    finally:
        conn.close()


@contextmanager
def session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """A connection that commits on success, rolls back on error and always closes."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
