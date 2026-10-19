from dataclasses import replace

from paper_radar.config import DEFAULTS_DIR
from paper_radar.preferences import (
    from_dict,
    load_preferences,
    reset_preferences,
    save_preferences,
    validate,
)

from .conftest import NOW

PROFILE = DEFAULTS_DIR / "profile.yaml"


def test_defaults_come_from_profile_and_taxonomy(prefs, taxonomy) -> None:
    assert prefs.lookback_days == 14
    assert prefs.interest_weights["slam"] == 20 and prefs.interest_weights["locomotion"] == 5
    assert prefs.included_categories == taxonomy.category_ids
    assert "cooperative transport" in prefs.focus_keywords
    assert prefs.weekly_target == 4


def test_validate_clamps_and_drops_unknown_values(prefs, taxonomy) -> None:
    messy = replace(
        prefs,
        lookback_days=999,
        included_categories=("bogus",),
        cross_list_categories=("cs.CV", "q-bio.NC"),
        focus_keywords=(" VIO ", "vio", "", "loop closure"),
        weekly_target=0,
    )
    clean = validate(messy, taxonomy)
    assert clean.lookback_days == 60
    assert clean.included_categories == taxonomy.category_ids  # never an empty feed
    assert clean.cross_list_categories == ("cs.CV",)
    assert clean.focus_keywords == ("VIO", "loop closure")
    assert clean.weekly_target == 1


def test_from_dict_ignores_unknown_keys(prefs, taxonomy) -> None:
    restored = from_dict({"lookback_days": 7, "surprise": True, "score_weights": {"venue": 0, "extra": 3}}, prefs, taxonomy)
    assert restored.lookback_days == 7
    assert restored.score_weights.venue == 0


def test_save_load_and_reset(conn, prefs, taxonomy) -> None:
    save_preferences(conn, replace(prefs, lookback_days=21, focus_keywords=("world model",)), taxonomy, NOW)
    loaded = load_preferences(conn, taxonomy, PROFILE)
    assert loaded.lookback_days == 21 and loaded.focus_keywords == ("world model",)
    reset_preferences(conn)
    assert load_preferences(conn, taxonomy, PROFILE) == prefs
