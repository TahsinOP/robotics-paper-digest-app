"""Transparent, deterministic relevance score (spec §6).

score = interest match + focus keywords + recency + author-listed resources + stated venue,
each factor scaled to its configured maximum (weights normalised to 100). Missing metadata is
handled neutrally: an unknown date gets half the recency credit; a missing code link simply
earns no bonus and is never described as a quality problem.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

from paper_radar.models import ScoreComponent, ScoreResult
from paper_radar.preferences import Preferences
from paper_radar.taxonomy import Taxonomy
from paper_radar.text import MatchView, Phrase, compile_phrase, match_view

SECONDARY_TOPIC_FACTOR = 0.7
SECONDARY_MIN_EVIDENCE = 2.0
KEYWORD_SATURATION = 1.5
TITLE_KEYWORD_CREDIT = 1.0
ABSTRACT_KEYWORD_CREDIT = 0.5
UNKNOWN_DATE_RECENCY = 0.5
RESOURCE_CREDIT = {"code": 1.0, "project": 0.6}
VENUE_CREDIT = {"top": 1.0, "known": 0.6, "workshop": 0.3}


@dataclass(frozen=True)
class RankingInput:
    title: str
    abstract: str
    primary_category: str | None
    category_scores: dict[str, float]
    published_at: datetime | None
    code_url: str | None
    project_url: str | None
    venue_name: str | None
    venue_tier: str | None
    venue_evidence: str | None = None


@dataclass(frozen=True)
class CompiledKeywords:
    items: tuple[tuple[str, Phrase], ...]


def compile_keywords(keywords: tuple[str, ...] | list[str]) -> CompiledKeywords:
    compiled = []
    for keyword in keywords:
        try:
            compiled.append((keyword, compile_phrase(keyword)))
        except (re.error, ValueError):
            continue  # an unusable keyword is ignored rather than breaking scoring
    return CompiledKeywords(tuple(compiled))


def _interest(inp: RankingInput, prefs: Preferences, taxonomy: Taxonomy) -> tuple[float, str, str]:
    """Returns (fraction, detail for the breakdown, phrase for the explanation)."""
    weights = prefs.interest_weights
    top_weight = max(weights.values(), default=0) or 1.0
    primary = taxonomy.category(inp.primary_category)
    primary_weight = weights.get(primary.group, 0.0)
    fraction = primary_weight / top_weight
    detail = f"{primary.short}: weight {primary_weight:g} of your top {top_weight:g}"
    phrase = f"Matches your {primary.short} interest (weight {primary_weight:g})"

    primary_evidence = inp.category_scores.get(primary.id, 0.0)
    for category_id, evidence in sorted(inp.category_scores.items(), key=lambda kv: -kv[1]):
        if category_id == primary.id or evidence < max(SECONDARY_MIN_EVIDENCE, 0.5 * primary_evidence):
            continue
        category = taxonomy.category(category_id)
        weight = weights.get(category.group, 0.0)
        if SECONDARY_TOPIC_FACTOR * weight / top_weight > fraction:
            fraction = SECONDARY_TOPIC_FACTOR * weight / top_weight
            detail = (
                f"Also covers {category.short} (weight {weight:g}), counted at "
                f"{SECONDARY_TOPIC_FACTOR:.0%}; main topic {primary.short}"
            )
            phrase = f"Also covers your {category.short} interest (weight {weight:g})"
    return fraction, detail, phrase


def _keywords(view: MatchView, keywords: CompiledKeywords) -> tuple[float, tuple[str, ...]]:
    in_title, in_abstract = [], []
    for keyword, matcher in keywords.items:
        if matcher.in_title(view):
            in_title.append(keyword)
        elif matcher.in_abstract(view, cap=1):
            in_abstract.append(keyword)
    strength = TITLE_KEYWORD_CREDIT * len(in_title) + ABSTRACT_KEYWORD_CREDIT * len(in_abstract)
    return 1.0 - math.exp(-strength / KEYWORD_SATURATION), tuple(in_title + in_abstract)


def _recency(published_at: datetime | None, now: datetime, half_life_days: float) -> tuple[float, str]:
    if published_at is None:
        return UNKNOWN_DATE_RECENCY, "Submission date unknown, so half credit"
    age_days = max(0.0, (now - published_at).total_seconds() / 86400)
    label = "today" if age_days < 1 else f"{int(age_days)} day{'s' if int(age_days) != 1 else ''} ago"
    return 0.5 ** (age_days / half_life_days), f"Submitted {label} (half-life {half_life_days:g} days)"


def score_paper(
    inp: RankingInput,
    prefs: Preferences,
    taxonomy: Taxonomy,
    keywords: CompiledKeywords,
    now: datetime,
) -> ScoreResult:
    weights = prefs.score_weights.normalised()
    view = match_view(inp.title, inp.abstract)

    interest, interest_detail, interest_phrase = _interest(inp, prefs, taxonomy)
    keyword_fraction, matched = _keywords(view, keywords)
    recency, recency_detail = _recency(inp.published_at, now, prefs.recency_half_life_days)

    if inp.code_url:
        resources, resource_detail, resource_phrase = RESOURCE_CREDIT["code"], "Code link listed by the authors", "Code listed"
    elif inp.project_url:
        resources, resource_detail, resource_phrase = RESOURCE_CREDIT["project"], "Project page listed by the authors", "Project page listed"
    else:
        resources, resource_detail, resource_phrase = 0.0, "No link listed (not a judgement of quality)", ""

    venue = VENUE_CREDIT.get(inp.venue_tier or "", 0.0)
    if inp.venue_name and venue:
        how = {"accepted": "acceptance stated", "published": "publication stated"}.get(inp.venue_evidence or "", "venue named")
        venue_detail = f"{inp.venue_name}: {inp.venue_tier} venue, {how} in the authors' arXiv comment"
        if inp.venue_tier == "workshop":
            venue_phrase = f"{inp.venue_name} paper"
        elif inp.venue_evidence == "stated":
            venue_phrase = f"Lists {inp.venue_name}"
        else:
            venue_phrase = f"{'Published in' if inp.venue_evidence == 'published' else 'Accepted at'} {inp.venue_name}"
    else:
        venue_detail, venue_phrase = "No accepted venue stated", ""

    if matched:
        keyword_detail = ", ".join(matched)
        shown = ", ".join(matched[:3]) + (f" and {len(matched) - 3} more" if len(matched) > 3 else "")
        keyword_phrase = f"Your keywords: {shown}"
    else:
        keyword_detail, keyword_phrase = "None of your focus keywords appear", ""

    components = {
        "interest": ScoreComponent(round(weights.interest * interest, 1), weights.interest, interest_detail),
        "keywords": ScoreComponent(round(weights.keywords * keyword_fraction, 1), weights.keywords, keyword_detail),
        "recency": ScoreComponent(round(weights.recency * recency, 1), weights.recency, recency_detail),
        "resources": ScoreComponent(round(weights.resources * resources, 1), weights.resources, resource_detail),
        "venue": ScoreComponent(round(weights.venue * venue, 1), weights.venue, venue_detail),
    }
    total = round(min(100.0, max(0.0, sum(c.points for c in components.values()))), 1)
    phrases = [p for p in (interest_phrase, keyword_phrase, resource_phrase, venue_phrase) if p]
    return ScoreResult(
        total=total,
        explanation=". ".join(phrases) + ".",
        components=components,
        matched_keywords=matched,
    )
