"""User preferences: defaults come from config/profile.yaml, edits are stored in SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

import yaml

from paper_radar.llm.base import DEFAULT_MODEL, EFFORT_LEVELS, MODELS
from paper_radar.repositories import app_state
from paper_radar.taxonomy import Taxonomy

LOOKBACK_RANGE = (1, 60)
MAX_RESULTS_RANGE = (50, 5000)
HALF_LIFE_RANGE = (1.0, 60.0)
WEEKLY_TARGET_RANGE = (1, 8)
FULLTEXT_CHARS_RANGE = (20_000, 400_000)
ARXIV_CROSS_LIST_OPTIONS = ("cs.CV", "cs.LG", "cs.AI", "eess.SY", "cs.SY", "cs.MA", "cs.HC", "math.OC")


@dataclass(frozen=True)
class ScoreWeights:
    """Maximum points per ranking factor. Normalised to sum to 100 when scoring."""

    interest: float = 40
    keywords: float = 25
    recency: float = 20
    resources: float = 10
    venue: float = 5

    def normalised(self) -> ScoreWeights:
        total = self.interest + self.keywords + self.recency + self.resources + self.venue
        if total <= 0:
            return ScoreWeights()
        factor = 100.0 / total
        return ScoreWeights(*(round(v * factor, 4) for v in asdict(self).values()))


@dataclass(frozen=True)
class Preferences:
    lookback_days: int = 14
    max_results: int = 1500
    cross_list_categories: tuple[str, ...] = ("cs.CV", "cs.LG", "cs.AI", "eess.SY")
    interest_weights: dict[str, float] = field(default_factory=dict)  # interest group id -> weight
    included_categories: tuple[str, ...] = ()  # category ids shown in the feed
    focus_keywords: tuple[str, ...] = ()
    recency_half_life_days: float = 10.0
    score_weights: ScoreWeights = ScoreWeights()
    weekly_target: int = 4
    digest_engine: str = "claude_code"  # "claude_code" (your Claude plan) or "api" (API key)
    digest_model: str = DEFAULT_MODEL
    digest_effort: str = "medium"
    fulltext_max_chars: int = 120_000

    def to_dict(self) -> dict:
        data = asdict(self)
        data["cross_list_categories"] = list(self.cross_list_categories)
        data["included_categories"] = list(self.included_categories)
        data["focus_keywords"] = list(self.focus_keywords)
        return data


def _score_weights(base: ScoreWeights, overrides: dict | None) -> ScoreWeights:
    known = asdict(base)
    return ScoreWeights(**{**known, **{k: v for k, v in (overrides or {}).items() if k in known}})


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def clean_keywords(keywords: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Strip, drop blanks and case-insensitive duplicates, keep order."""
    seen: set[str] = set()
    cleaned = []
    for keyword in keywords:
        keyword = " ".join(str(keyword).split())
        if keyword and keyword.lower() not in seen:
            seen.add(keyword.lower())
            cleaned.append(keyword)
    return tuple(cleaned)


def validate(prefs: Preferences, taxonomy: Taxonomy) -> Preferences:
    """Clamp ranges and drop unknown ids so stored preferences can never break scoring."""
    weights = {g.id: _clamp(float(prefs.interest_weights.get(g.id, g.default_weight)), (0, 100)) for g in taxonomy.groups}
    included = tuple(c for c in prefs.included_categories if c in taxonomy.category_ids) or taxonomy.category_ids
    cross = tuple(c for c in prefs.cross_list_categories if c in ARXIV_CROSS_LIST_OPTIONS)
    sw = prefs.score_weights
    return replace(
        prefs,
        lookback_days=int(_clamp(int(prefs.lookback_days), LOOKBACK_RANGE)),
        max_results=int(_clamp(int(prefs.max_results), MAX_RESULTS_RANGE)),
        cross_list_categories=cross,
        interest_weights=weights,
        included_categories=included,
        focus_keywords=clean_keywords(prefs.focus_keywords),
        recency_half_life_days=float(_clamp(float(prefs.recency_half_life_days), HALF_LIFE_RANGE)),
        score_weights=ScoreWeights(*(max(0.0, float(v)) for v in asdict(sw).values())),
        weekly_target=int(_clamp(int(prefs.weekly_target), WEEKLY_TARGET_RANGE)),
        digest_engine=prefs.digest_engine if prefs.digest_engine in ("claude_code", "api") else "claude_code",
        digest_model=prefs.digest_model if prefs.digest_model in MODELS else DEFAULT_MODEL,
        digest_effort=prefs.digest_effort if prefs.digest_effort in EFFORT_LEVELS else "medium",
        fulltext_max_chars=int(_clamp(int(prefs.fulltext_max_chars), FULLTEXT_CHARS_RANGE)),
    )


def default_preferences(taxonomy: Taxonomy, profile_path: Path) -> Preferences:
    profile = yaml.safe_load(profile_path.read_text()) or {}
    refresh = profile.get("refresh") or {}
    scoring = profile.get("scoring") or {}
    digests = profile.get("digests") or {}
    defaults = Preferences()
    prefs = Preferences(
        lookback_days=refresh.get("lookback_days", defaults.lookback_days),
        max_results=refresh.get("max_results", defaults.max_results),
        cross_list_categories=tuple(refresh.get("cross_list_categories", defaults.cross_list_categories)),
        interest_weights={g.id: g.default_weight for g in taxonomy.groups},
        included_categories=taxonomy.category_ids,
        focus_keywords=tuple(profile.get("focus_keywords") or ()),
        recency_half_life_days=scoring.get("recency_half_life_days", defaults.recency_half_life_days),
        score_weights=_score_weights(defaults.score_weights, scoring.get("weights")),
        weekly_target=(profile.get("reading") or {}).get("weekly_target", defaults.weekly_target),
        digest_engine=digests.get("engine", defaults.digest_engine),
        digest_model=digests.get("model", defaults.digest_model),
        digest_effort=digests.get("effort", defaults.digest_effort),
        fulltext_max_chars=digests.get("fulltext_max_chars", defaults.fulltext_max_chars),
    )
    return validate(prefs, taxonomy)


def from_dict(data: dict, defaults: Preferences, taxonomy: Taxonomy) -> Preferences:
    """Merge stored values over defaults; unknown or missing keys fall back safely."""
    merged = {**defaults.to_dict(), **{k: v for k, v in data.items() if k in defaults.to_dict()}}
    prefs = Preferences(
        lookback_days=merged["lookback_days"],
        max_results=merged["max_results"],
        cross_list_categories=tuple(merged["cross_list_categories"]),
        interest_weights=dict(merged["interest_weights"]),
        included_categories=tuple(merged["included_categories"]),
        focus_keywords=tuple(merged["focus_keywords"]),
        recency_half_life_days=merged["recency_half_life_days"],
        score_weights=_score_weights(defaults.score_weights, merged["score_weights"]),
        weekly_target=merged["weekly_target"],
        digest_engine=merged["digest_engine"],
        digest_model=merged["digest_model"],
        digest_effort=merged["digest_effort"],
        fulltext_max_chars=merged["fulltext_max_chars"],
    )
    return validate(prefs, taxonomy)


PREFERENCES_KEY = "preferences"


def load_preferences(conn: sqlite3.Connection, taxonomy: Taxonomy, profile_path: Path) -> Preferences:
    defaults = default_preferences(taxonomy, profile_path)
    stored = app_state.get_json(conn, PREFERENCES_KEY)
    return from_dict(stored, defaults, taxonomy) if isinstance(stored, dict) else defaults


def save_preferences(conn: sqlite3.Connection, prefs: Preferences, taxonomy: Taxonomy, now: datetime) -> Preferences:
    validated = validate(prefs, taxonomy)
    app_state.set_json(conn, PREFERENCES_KEY, validated.to_dict(), now)
    return validated


def reset_preferences(conn: sqlite3.Connection) -> None:
    app_state.delete(conn, PREFERENCES_KEY)


def fingerprint(prefs: Preferences) -> str:
    """Changes whenever anything that affects scoring changes."""
    return hashlib.sha256(json.dumps(prefs.to_dict(), sort_keys=True).encode()).hexdigest()[:12]
