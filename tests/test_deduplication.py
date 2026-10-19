from datetime import timedelta

from paper_radar.services.deduplication import ExistingRecord, find_duplicate
from paper_radar.services.normalization import normalize_title

from .conftest import NOW, make_candidate


class MemoryLookup:
    def __init__(self, records: list[tuple[ExistingRecord, str]]):
        self.records = records  # (record, title)

    def by_arxiv_id(self, arxiv_id):
        return next((r for r, _ in self.records if r.arxiv_id == arxiv_id), None)

    def by_doi(self, doi):
        return next((r for r, _ in self.records if r.doi == doi), None)

    def by_title(self, title_norm):
        return [r for r, t in self.records if normalize_title(t) == title_norm]


def record(id=1, arxiv_id="2610.00001", doi=None, authors=("Ada Lovelace",), days_ago=2):
    return ExistingRecord(id=id, arxiv_id=arxiv_id, doi=doi, authors=authors, published_at=NOW - timedelta(days=days_ago))


def test_matches_on_arxiv_id_first() -> None:
    lookup = MemoryLookup([(record(id=7), "Something else entirely")])
    decision = find_duplicate(make_candidate(), lookup)
    assert (decision.existing_id, decision.matched_on) == (7, "arxiv_id")


def test_matches_on_doi_when_arxiv_id_differs_or_is_missing() -> None:
    lookup = MemoryLookup([(record(id=3, arxiv_id=None, doi="10.1/x"), "Other title")])
    decision = find_duplicate(make_candidate(arxiv_id=None, doi="https://doi.org/10.1/X"), lookup)
    assert (decision.existing_id, decision.matched_on) == (3, "doi")


def test_matches_on_title_with_compatible_authors_and_dates() -> None:
    candidate = make_candidate(arxiv_id=None, title="Visual-Inertial Odometry for Agile Quadrotors!")
    lookup = MemoryLookup([(record(id=4, arxiv_id=None, authors=("A. Lovelace",)), "visual inertial odometry for agile quadrotors")])
    decision = find_duplicate(candidate, lookup)
    assert (decision.existing_id, decision.matched_on) == (4, "title")


def test_same_title_different_authors_is_kept_separate_with_a_note() -> None:
    candidate = make_candidate(arxiv_id=None)
    lookup = MemoryLookup([(record(id=5, arxiv_id=None, authors=("Someone Else",)), candidate.title)])
    decision = find_duplicate(candidate, lookup)
    assert decision.existing_id is None
    assert "#5" in decision.note


def test_same_title_but_conflicting_arxiv_ids_is_not_merged() -> None:
    candidate = make_candidate(arxiv_id="2610.99999")
    lookup = MemoryLookup([(record(id=6, arxiv_id="2610.00001"), candidate.title)])
    decision = find_duplicate(candidate, lookup)
    assert decision.existing_id is None and decision.note


def test_same_title_far_apart_in_time_is_not_merged() -> None:
    candidate = make_candidate(arxiv_id=None)
    lookup = MemoryLookup([(record(id=8, arxiv_id=None, days_ago=900), candidate.title)])
    assert find_duplicate(candidate, lookup).existing_id is None


def test_new_paper() -> None:
    decision = find_duplicate(make_candidate(), MemoryLookup([]))
    assert decision.existing_id is None and decision.note is None
