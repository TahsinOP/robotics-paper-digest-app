"""Load and validate the robotics taxonomy (config/taxonomy.yaml) and venue list."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

from paper_radar.text import Phrase, compile_phrase

# Bump when matching logic changes in a way that should trigger reclassification.
MATCHING_RULES_VERSION = "1"
FALLBACK_CATEGORY = "other"
STRONG_TERM_WEIGHT = 1.0
WEAK_TERM_WEIGHT = 0.35


class TaxonomyError(ValueError):
    """The taxonomy file is malformed or internally inconsistent."""


@dataclass(frozen=True)
class Term:
    matcher: Phrase
    weight: float


@dataclass(frozen=True)
class Tag:
    name: str
    category_id: str
    matchers: tuple[Phrase, ...]


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    short: str
    group: str
    arxiv_hints: tuple[str, ...]
    terms: tuple[Term, ...]
    tags: tuple[Tag, ...]


@dataclass(frozen=True)
class InterestGroup:
    id: str
    name: str
    default_weight: float


@dataclass(frozen=True)
class Venue:
    name: str
    tier: str  # "top" | "known"
    matchers: tuple[Phrase, ...]


@dataclass(frozen=True)
class Taxonomy:
    version: str
    groups: tuple[InterestGroup, ...]
    categories: tuple[Category, ...]
    gate_anchors: tuple[Phrase, ...]
    gate_min_evidence: float
    cross_list_terms: tuple[str, ...]
    venues: tuple[Venue, ...]

    @cached_property
    def _categories_by_id(self) -> dict[str, Category]:
        return {c.id: c for c in self.categories}

    def category(self, category_id: str | None) -> Category:
        """The category with this id, or the fallback category for unknown ids."""
        by_id = self._categories_by_id
        return by_id.get(category_id or "", by_id[FALLBACK_CATEGORY])

    def group(self, group_id: str) -> InterestGroup:
        return next(g for g in self.groups if g.id == group_id)

    @property
    def category_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.categories)

    @property
    def tag_names(self) -> tuple[str, ...]:
        return tuple(tag.name for c in self.categories for tag in c.tags)


def _phrases(value: object, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(p, str) and p.strip() for p in value):
        raise TaxonomyError(f"{where} must be a list of non-empty strings")
    return value


def _compile(phrase: str, where: str, **kwargs: bool) -> Phrase:
    try:
        return compile_phrase(phrase, **kwargs)
    except (re.error, ValueError) as exc:
        raise TaxonomyError(f"{where}: cannot use phrase {phrase!r} ({exc})") from exc


def load_taxonomy(taxonomy_path: Path, venues_path: Path) -> Taxonomy:
    raw_taxonomy = taxonomy_path.read_bytes()
    raw_venues = venues_path.read_bytes()
    data = yaml.safe_load(raw_taxonomy) or {}
    venue_data = yaml.safe_load(raw_venues) or {}

    groups = tuple(
        InterestGroup(id=g["id"], name=g["name"], default_weight=float(g.get("default_weight", 0)))
        for g in data.get("interest_groups", [])
    )
    group_ids = {g.id for g in groups}
    if len(group_ids) != len(groups) or not groups:
        raise TaxonomyError("interest_groups must be a non-empty list with unique ids")

    categories: list[Category] = []
    seen_tags: set[str] = set()
    for raw in data.get("categories", []):
        cid = raw.get("id")
        where = f"category {cid!r}"
        if not cid or any(c.id == cid for c in categories):
            raise TaxonomyError(f"{where}: missing or duplicate id")
        if raw.get("group") not in group_ids:
            raise TaxonomyError(f"{where}: unknown interest group {raw.get('group')!r}")
        raw_terms = raw.get("terms") or {}
        terms = tuple(
            Term(_compile(p, where), weight)
            for key, weight in (("strong", STRONG_TERM_WEIGHT), ("weak", WEAK_TERM_WEIGHT))
            for p in _phrases(raw_terms.get(key), f"{where} terms.{key}")
        )
        tags = []
        for tag_name, phrases in (raw.get("tags") or {}).items():
            if tag_name in seen_tags:
                raise TaxonomyError(f"{where}: tag {tag_name!r} is defined twice")
            seen_tags.add(tag_name)
            matchers = tuple(_compile(p, f"{where} tag {tag_name!r}") for p in _phrases(phrases, f"tag {tag_name!r}"))
            if not matchers:
                raise TaxonomyError(f"{where}: tag {tag_name!r} has no phrases")
            tags.append(Tag(tag_name, cid, matchers))
        tag_phrases = {m.text.lower() for tag in tags for m in tag.matchers}
        duplicated = [term.matcher.text for term in terms if term.matcher.text.lower() in tag_phrases]
        if duplicated:
            raise TaxonomyError(f"{where}: {duplicated} appear both as terms and in tags; keep them in tags only")
        categories.append(
            Category(
                id=cid,
                name=raw["name"],
                short=raw.get("short", raw["name"]),
                group=raw["group"],
                arxiv_hints=tuple(raw.get("arxiv_hints") or ()),
                terms=terms,
                tags=tuple(tags),
            )
        )
    if not any(c.id == FALLBACK_CATEGORY for c in categories):
        raise TaxonomyError(f"the taxonomy needs a fallback category with id {FALLBACK_CATEGORY!r}")

    gate = data.get("robotics_gate") or {}
    anchors = tuple(_compile(p, "robotics_gate") for p in _phrases(gate.get("anchors"), "robotics_gate.anchors"))

    venues = []
    for tier in ("top", "known"):
        for name, aliases in (venue_data.get(tier) or {}).items():
            matchers = tuple(
                _compile(a, f"venue {name!r}", case_sensitive=True) for a in _phrases(aliases, f"venue {name!r}")
            )
            venues.append(Venue(name=str(name), tier=tier, matchers=matchers))

    digest = hashlib.sha256(raw_taxonomy + b"\0" + raw_venues + MATCHING_RULES_VERSION.encode()).hexdigest()
    return Taxonomy(
        version=digest[:12],
        groups=groups,
        categories=tuple(categories),
        gate_anchors=anchors,
        gate_min_evidence=float(gate.get("min_evidence", 2)),
        cross_list_terms=tuple(data.get("arxiv_cross_list_terms") or ()),
        venues=tuple(venues),
    )
