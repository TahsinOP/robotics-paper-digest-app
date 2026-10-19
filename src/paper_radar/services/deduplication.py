"""Duplicate detection (spec §10.1): arXiv id, then DOI, then title with compatible metadata.

Title matches are merged only when the author lists overlap, the dates are close and the
records do not carry conflicting identifiers. Anything else becomes a separate paper with a
note, so two different papers are never silently merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from paper_radar.models import PaperCandidate
from paper_radar.services.normalization import author_key, normalize_doi, normalize_title

MAX_DATE_GAP_DAYS = 400


@dataclass(frozen=True)
class ExistingRecord:
    id: int
    arxiv_id: str | None
    doi: str | None
    authors: tuple[str, ...]
    published_at: datetime | None


class RecordLookup(Protocol):
    def by_arxiv_id(self, arxiv_id: str) -> ExistingRecord | None: ...
    def by_doi(self, doi: str) -> ExistingRecord | None: ...
    def by_title(self, title_norm: str) -> list[ExistingRecord]: ...


@dataclass(frozen=True)
class DedupDecision:
    existing_id: int | None
    matched_on: str  # "arxiv_id" | "doi" | "title" | "none"
    note: str | None = None


def authors_compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    keys_a = {author_key(n) for n in a} - {""}
    keys_b = {author_key(n) for n in b} - {""}
    return not keys_a or not keys_b or bool(keys_a & keys_b)


def dates_compatible(a: datetime | None, b: datetime | None) -> bool:
    return a is None or b is None or abs((a - b).days) <= MAX_DATE_GAP_DAYS


def _conflicting_ids(candidate: PaperCandidate, record: ExistingRecord) -> bool:
    if candidate.arxiv_id and record.arxiv_id and candidate.arxiv_id != record.arxiv_id:
        return True
    doi = normalize_doi(candidate.doi)
    return bool(doi and record.doi and doi != record.doi)


def find_duplicate(candidate: PaperCandidate, lookup: RecordLookup) -> DedupDecision:
    if candidate.arxiv_id:
        record = lookup.by_arxiv_id(candidate.arxiv_id)
        if record:
            return DedupDecision(record.id, "arxiv_id")
    doi = normalize_doi(candidate.doi)
    if doi:
        record = lookup.by_doi(doi)
        if record and not _conflicting_ids(candidate, record):
            return DedupDecision(record.id, "doi")
    suspects = []
    for record in lookup.by_title(normalize_title(candidate.title)):
        if (
            not _conflicting_ids(candidate, record)
            and authors_compatible(candidate.authors, record.authors)
            and dates_compatible(candidate.published_at, record.published_at)
        ):
            return DedupDecision(record.id, "title")
        suspects.append(record.id)
    if suspects:
        return DedupDecision(
            None,
            "none",
            note=f"Possible duplicate of paper #{suspects[0]}: same title, but different identifiers, authors or dates.",
        )
    return DedupDecision(None, "none")
