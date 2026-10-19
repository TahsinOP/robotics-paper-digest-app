from dataclasses import replace
from datetime import timedelta

import pytest

from paper_radar.preferences import ScoreWeights
from paper_radar.services.classification import classify
from paper_radar.services.ranking import RankingInput, compile_keywords, score_paper

from .conftest import NOW


def ranking_input(taxonomy, title="LiDAR-Inertial Odometry with Loop Closure", abstract="", days_old=1.0, **overrides):
    abstract = abstract or "A LiDAR-inertial odometry method with loop closure for UAV mapping."
    cls = classify(title, abstract, "cs.RO", taxonomy)
    base = RankingInput(
        title=title,
        abstract=abstract,
        primary_category=cls.primary_category,
        category_scores=cls.category_scores,
        published_at=None if days_old is None else NOW - timedelta(days=days_old),
        code_url=None,
        project_url=None,
        venue_name=None,
        venue_tier=None,
    )
    return replace(base, **overrides)


def score(inp, prefs, taxonomy):
    return score_paper(inp, prefs, taxonomy, compile_keywords(prefs.focus_keywords), NOW)


def test_score_is_bounded_and_deterministic(prefs, taxonomy) -> None:
    inp = ranking_input(taxonomy, code_url="https://github.com/a/b", venue_name="IROS 2026", venue_tier="top", venue_evidence="accepted")
    first, second = score(inp, prefs, taxonomy), score(inp, prefs, taxonomy)
    assert first == second
    assert 0 <= first.total <= 100
    assert first.total == pytest.approx(sum(c.points for c in first.components.values()), abs=0.1)


def test_normalised_weights_sum_to_100() -> None:
    weights = ScoreWeights(10, 10, 10, 10, 10).normalised()
    assert sum([weights.interest, weights.keywords, weights.recency, weights.resources, weights.venue]) == pytest.approx(100)
    assert ScoreWeights(0, 0, 0, 0, 0).normalised() == ScoreWeights()


def test_newer_papers_score_higher_and_unknown_dates_get_half(prefs, taxonomy) -> None:
    fresh = score(ranking_input(taxonomy, days_old=0.5), prefs, taxonomy)
    old = score(ranking_input(taxonomy, days_old=20), prefs, taxonomy)
    unknown = score(ranking_input(taxonomy, days_old=None), prefs, taxonomy)
    assert fresh.total > old.total
    weight = prefs.score_weights.normalised().recency
    assert unknown.components["recency"].points == pytest.approx(weight / 2, abs=0.1)


def test_title_keywords_earn_more_than_abstract_keywords(prefs, taxonomy) -> None:
    in_title = score(ranking_input(taxonomy, title="Loop Closure for UAVs", abstract="A mapping method."), prefs, taxonomy)
    in_abstract = score(ranking_input(taxonomy, title="A Mapping Method", abstract="We add loop closure for UAVs."), prefs, taxonomy)
    assert in_title.components["keywords"].points > in_abstract.components["keywords"].points
    assert "loop closure" in in_title.matched_keywords


def test_interest_weights_drive_topic_points(prefs, taxonomy) -> None:
    inp = ranking_input(taxonomy)
    baseline = score(inp, prefs, taxonomy)
    no_slam = replace(prefs, interest_weights={**prefs.interest_weights, "slam": 0})
    assert score(inp, no_slam, taxonomy).components["interest"].points < baseline.components["interest"].points


def test_missing_links_are_neutral_and_never_called_a_flaw(prefs, taxonomy) -> None:
    result = score(ranking_input(taxonomy), prefs, taxonomy)
    assert result.components["resources"].points == 0
    assert "not a judgement of quality" in result.components["resources"].detail
    assert "code" not in result.explanation.lower()


def test_resources_and_venue_add_points_and_explanations(prefs, taxonomy) -> None:
    plain = score(ranking_input(taxonomy), prefs, taxonomy)
    rich = score(
        ranking_input(taxonomy, code_url="https://github.com/a/b", venue_name="CoRL 2026", venue_tier="top", venue_evidence="accepted"),
        prefs,
        taxonomy,
    )
    assert rich.total > plain.total
    assert "Code listed" in rich.explanation and "Accepted at CoRL 2026" in rich.explanation


def test_explanation_names_topic_and_keywords(prefs, taxonomy) -> None:
    result = score(ranking_input(taxonomy), prefs, taxonomy)
    assert result.explanation.startswith("Matches your SLAM & perception interest")
    assert "Your keywords:" in result.explanation
