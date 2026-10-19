"""Keyword-evidence classification into the robotics taxonomy (spec §4.3).

Each category collects evidence from its terms and tags: a hit in the title counts 2, hits in
the abstract count sqrt(n) (capped), weak terms are down-weighted. The arXiv primary category is
only a small tie-breaking hint. Low or ambiguous evidence is flagged for review rather than
guessed.
"""

from __future__ import annotations

import math

from paper_radar.models import Classification, PaperCandidate
from paper_radar.taxonomy import FALLBACK_CATEGORY, Taxonomy
from paper_radar.text import MatchView, Phrase, match_view

TITLE_WEIGHT = 2.0
ABSTRACT_COUNT_CAP = 4
ARXIV_HINT_BONUS = 0.75
MIN_PRIMARY_EVIDENCE = 2.0
FULL_CONFIDENCE_EVIDENCE = 6.0
REVIEW_BELOW_CONFIDENCE = 0.35
MAX_TAGS = 6


def hit_strength(matcher: Phrase, view: MatchView) -> float:
    strength = TITLE_WEIGHT if matcher.in_title(view) else 0.0
    count = matcher.in_abstract(view, ABSTRACT_COUNT_CAP)
    return strength + (math.sqrt(count) if count else 0.0)


def classify(title: str, abstract: str, source_primary_category: str | None, taxonomy: Taxonomy) -> Classification:
    view = match_view(title, abstract)
    scores: dict[str, float] = {}
    tag_hits: list[tuple[float, str]] = []
    for category in taxonomy.categories:
        score = sum(term.weight * hit_strength(term.matcher, view) for term in category.terms)
        for tag in category.tags:
            strength = max(hit_strength(m, view) for m in tag.matchers)
            if strength > 0:
                score += strength
                tag_hits.append((strength, tag.name))
        if score > 0 and source_primary_category in category.arxiv_hints:
            score += ARXIV_HINT_BONUS
        scores[category.id] = round(score, 3)

    ranked = sorted(scores.items(), key=lambda item: -item[1])
    best_id, best = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best < MIN_PRIMARY_EVIDENCE:
        primary, confidence, needs_review = FALLBACK_CATEGORY, round(0.3 * best / MIN_PRIMARY_EVIDENCE, 2), True
    else:
        margin = (best - runner_up) / best
        evidence = min(1.0, best / FULL_CONFIDENCE_EVIDENCE)
        confidence = round(0.5 * margin + 0.5 * evidence, 2)
        primary, needs_review = best_id, confidence < REVIEW_BELOW_CONFIDENCE

    tags = tuple(name for _, name in sorted(tag_hits, key=lambda hit: -hit[0]))[:MAX_TAGS]
    return Classification(
        primary_category=primary,
        secondary_tags=tags,
        confidence=confidence,
        needs_review=needs_review,
        category_scores={k: v for k, v in scores.items() if v > 0},
    )


def robotics_evidence(title: str, abstract: str, taxonomy: Taxonomy) -> float:
    """Title hit = 2, abstract = 1 per occurrence up to 2, summed over robotics anchor terms."""
    view = match_view(title, abstract)
    return sum(
        (TITLE_WEIGHT if anchor.in_title(view) else 0.0) + anchor.in_abstract(view, cap=2)
        for anchor in taxonomy.gate_anchors
    )


def is_robotics_paper(candidate: PaperCandidate, taxonomy: Taxonomy) -> bool:
    """Everything in cs.RO is kept; papers from neighbouring categories need robotics evidence."""
    if "cs.RO" in candidate.source_categories:
        return True
    return robotics_evidence(candidate.title, candidate.abstract, taxonomy) >= taxonomy.gate_min_evidence


def validate_classification(classification: Classification, taxonomy: Taxonomy) -> list[str]:
    """Problems that would make a classification unusable (spec §4.3: validate against the taxonomy)."""
    problems = []
    if classification.primary_category not in taxonomy.category_ids:
        problems.append(f"unknown primary category {classification.primary_category!r}")
    unknown = [t for t in classification.secondary_tags if t not in taxonomy.tag_names]
    if unknown:
        problems.append(f"unknown tags {unknown}")
    if not 0.0 <= classification.confidence <= 1.0:
        problems.append("confidence outside [0, 1]")
    return problems
