"""Refresh: fetch -> keep robotics papers -> deduplicate -> classify -> store -> rescore.

Fetching finishes before anything is written, and writing happens in one transaction, so a
failed or interrupted refresh never damages papers that are already stored.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from paper_radar.config import AppConfig
from paper_radar.preferences import Preferences
from paper_radar.repositories import app_state, papers
from paper_radar.services.classification import classify, is_robotics_paper
from paper_radar.services.deduplication import find_duplicate
from paper_radar.services.library import rescore_all
from paper_radar.services.normalization import content_hash
from paper_radar.services.signals import detect_venue, extract_links
from paper_radar.sources.arxiv_source import ArxivSource
from paper_radar.sources.base import PaperSource, ProgressCallback
from paper_radar.taxonomy import Taxonomy
from paper_radar.text import utc_now


@dataclass(frozen=True)
class RefreshReport:
    started_at: datetime
    finished_at: datetime
    fetched: int
    inserted: int
    updated: int
    unchanged: int
    off_topic: int
    errors: tuple[str, ...]

    @property
    def status(self) -> str:
        if self.errors and self.fetched == 0:
            return "failed"
        return "partial" if self.errors else "ok"

    def summary(self) -> str:
        if self.status == "failed":
            return "No papers could be fetched."
        if self.inserted == 0 and self.updated == 0:
            return "Already up to date. No new papers since the last refresh."
        parts = [f"{self.inserted:,} new paper{'s' if self.inserted != 1 else ''}"]
        if self.updated:
            parts.append(f"{self.updated:,} revised")
        return "Added " + " and ".join(parts) + "."


def make_source(prefs: Preferences, taxonomy: Taxonomy, config: AppConfig) -> PaperSource:
    return ArxivSource(
        cross_list_categories=prefs.cross_list_categories,
        cross_list_terms=taxonomy.cross_list_terms,
        timeout=config.http_timeout,
        contact_email=config.contact_email,
    )


def refresh_papers(
    conn: sqlite3.Connection,
    source: PaperSource,
    prefs: Preferences,
    taxonomy: Taxonomy,
    *,
    now: datetime | None = None,
    progress: ProgressCallback | None = None,
) -> RefreshReport:
    started = now or utc_now()
    result = source.fetch_recent(
        since=started - timedelta(days=prefs.lookback_days),
        until=started,
        max_results=prefs.max_results,
        progress=progress,
    )
    if progress:
        progress(f"Sorting and ranking {len(result.candidates):,} papers")

    inserted = updated = unchanged = off_topic = 0
    lookup = papers.SqliteRecordLookup(conn)
    for candidate in result.candidates:
        if not is_robotics_paper(candidate, taxonomy):
            off_topic += 1
            continue
        decision = find_duplicate(candidate, lookup)
        links = extract_links(candidate.abstract, candidate.author_comment)
        venue = detect_venue(candidate.author_comment, candidate.journal_ref, taxonomy)
        if decision.existing_id is None:
            classification = classify(candidate.title, candidate.abstract, candidate.source_primary_category, taxonomy)
            papers.insert_paper(
                conn, candidate, links, venue, classification, taxonomy.version, started, dedup_note=decision.note
            )
            inserted += 1
            continue
        previous_hash = papers.stored_content_hash(conn, decision.existing_id)
        papers.update_metadata(conn, decision.existing_id, candidate, links, venue, started)
        if previous_hash != content_hash(candidate):
            classification = classify(candidate.title, candidate.abstract, candidate.source_primary_category, taxonomy)
            papers.update_classification(conn, decision.existing_id, classification, taxonomy.version)
            updated += 1
        else:
            unchanged += 1

    rescore_all(conn, prefs, taxonomy, started)
    report = RefreshReport(
        started_at=started,
        finished_at=utc_now(),
        fetched=len(result.candidates),
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        off_topic=off_topic,
        errors=tuple(result.errors),
    )
    app_state.record_refresh(
        conn,
        app_state.RefreshRun(
            source_name=source.name,
            started_at=report.started_at,
            finished_at=report.finished_at,
            status=report.status,
            fetched=report.fetched,
            inserted=report.inserted,
            updated=report.updated,
            skipped=report.off_topic,
            errors=report.errors,
        ),
    )
    conn.commit()
    return report
