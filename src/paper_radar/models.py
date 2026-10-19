"""Domain models shared across sources, services, repositories and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Status(StrEnum):
    NEW = "new"
    SHORTLISTED = "shortlisted"
    READING = "reading"
    READ = "read"
    SKIPPED = "skipped"


class DigestSourceLevel(StrEnum):
    NOT_GENERATED = "not_generated"
    ABSTRACT_ONLY = "abstract_only"
    PARTIAL_FULL_TEXT = "partial_full_text"
    FULL_TEXT = "full_text"


@dataclass(frozen=True)
class PaperCandidate:
    """A normalised record from any source, before deduplication and storage."""

    source_name: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    published_at: datetime | None
    updated_at: datetime | None
    source_url: str | None
    pdf_url: str | None = None
    arxiv_id: str | None = None
    arxiv_version: int | None = None
    doi: str | None = None
    source_categories: tuple[str, ...] = ()
    source_primary_category: str | None = None
    author_comment: str | None = None
    journal_ref: str | None = None


@dataclass(frozen=True)
class ResourceLinks:
    """Links the authors themselves listed in the arXiv abstract or comment."""

    code_url: str | None = None
    project_url: str | None = None


@dataclass(frozen=True)
class VenueSignal:
    name: str  # e.g. "CoRL 2026"
    tier: str  # "top" | "known" | "workshop"
    evidence: str  # "accepted" | "published" | "stated" (venue and year named, no acceptance wording)


@dataclass(frozen=True)
class Classification:
    primary_category: str
    secondary_tags: tuple[str, ...]
    confidence: float
    needs_review: bool
    category_scores: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreComponent:
    points: float
    max_points: float
    detail: str


@dataclass(frozen=True)
class ScoreResult:
    total: float
    explanation: str
    components: dict[str, ScoreComponent]
    matched_keywords: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "matched_keywords": list(self.matched_keywords),
            "components": {
                name: {"points": c.points, "max": c.max_points, "detail": c.detail}
                for name, c in self.components.items()
            },
        }


@dataclass(frozen=True)
class Paper:
    """A stored paper as the UI and services see it."""

    id: int
    title: str
    authors: tuple[str, ...]
    abstract: str
    source_name: str
    source_url: str | None
    pdf_url: str | None
    arxiv_id: str | None
    arxiv_version: int | None
    doi: str | None
    published_at: datetime | None
    updated_at: datetime | None
    source_categories: tuple[str, ...]
    source_primary_category: str | None
    author_comment: str | None
    journal_ref: str | None
    code_url: str | None
    project_url: str | None
    venue_name: str | None
    venue_tier: str | None
    venue_evidence: str | None
    primary_category: str | None
    secondary_tags: tuple[str, ...]
    category_scores: dict[str, float]
    category_confidence: float | None
    needs_review: bool
    relevance_score: float | None
    relevance_explanation: str | None
    score_breakdown: dict | None
    status: Status
    status_changed_at: datetime | None
    digest_source_level: DigestSourceLevel
    digest: dict | None  # the best current AI digest (see repositories/digests.py)
    digest_model: str | None
    digest_created_at: datetime | None
    digest_content_hash: str | None
    content_hash: str
    personal_notes: str
    notes_updated_at: datetime | None
    dedup_note: str | None
    first_seen_at: datetime
    last_seen_at: datetime

    @property
    def digest_is_stale(self) -> bool:
        """The digest was written for an earlier version of the paper."""
        return self.digest is not None and self.digest_content_hash != self.content_hash
