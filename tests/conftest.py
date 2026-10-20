from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from paper_radar import db
from paper_radar.config import DEFAULTS_DIR
from paper_radar.models import Classification, PaperCandidate, ResourceLinks
from paper_radar.preferences import Preferences, default_preferences
from paper_radar.repositories import papers
from paper_radar.services.classification import classify
from paper_radar.sources.base import FetchResult
from paper_radar.taxonomy import Taxonomy, load_taxonomy

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real library, config directory or API credentials from a test."""
    base = tmp_path_factory.mktemp("user")
    monkeypatch.setenv("PAPER_RADAR_DATA_DIR", str(base / "data"))
    monkeypatch.setenv("PAPER_RADAR_CONFIG_DIR", str(base / "config"))
    for var in ("PAPER_RADAR_DB_PATH", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "SEMANTIC_SCHOLAR_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Tests never run the real Claude Code; individual tests point this at a fake script.
    monkeypatch.setenv("PAPER_RADAR_CLAUDE_BIN", str(base / "no-claude-here"))


@pytest.fixture(scope="session")
def taxonomy() -> Taxonomy:
    return load_taxonomy(DEFAULTS_DIR / "taxonomy.yaml", DEFAULTS_DIR / "venues.yaml")


@pytest.fixture
def prefs(taxonomy: Taxonomy) -> Preferences:
    return default_preferences(taxonomy, DEFAULTS_DIR / "profile.yaml")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "radar.db"
    db.initialise(path)
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(db_path)
    yield connection
    connection.close()


def make_candidate(**overrides) -> PaperCandidate:
    base = PaperCandidate(
        source_name="arxiv",
        title="Visual-Inertial Odometry for Agile Quadrotors",
        authors=("Ada Lovelace", "Alan Turing"),
        abstract="We present a visual-inertial odometry (VIO) pipeline with loop closure for UAVs.",
        published_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=2),
        source_url="https://arxiv.org/abs/2610.00001",
        pdf_url="https://arxiv.org/pdf/2610.00001",
        arxiv_id="2610.00001",
        arxiv_version=1,
        source_categories=("cs.RO",),
        source_primary_category="cs.RO",
    )
    return replace(base, **overrides)


def store(conn: sqlite3.Connection, taxonomy: Taxonomy, candidate: PaperCandidate | None = None, **overrides) -> int:
    candidate = candidate or make_candidate(**overrides)
    classification: Classification = classify(
        candidate.title, candidate.abstract, candidate.source_primary_category, taxonomy
    )
    paper_id = papers.insert_paper(conn, candidate, ResourceLinks(), None, classification, taxonomy.version, NOW)
    conn.commit()
    return paper_id


class FakeSource:
    """A PaperSource returning canned results, for ingestion tests."""

    name = "arxiv"

    def __init__(self, candidates: list[PaperCandidate], errors: list[str] | None = None):
        self.candidates = candidates
        self.errors = errors or []
        self.calls = 0

    def fetch_recent(self, *, since, until, max_results, progress=None) -> FetchResult:
        self.calls += 1
        return FetchResult(candidates=list(self.candidates), errors=list(self.errors))
