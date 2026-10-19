from pathlib import Path

import pytest

from paper_radar.config import DEFAULTS_DIR
from paper_radar.models import Classification
from paper_radar.services.classification import classify, is_robotics_paper, validate_classification
from paper_radar.taxonomy import TaxonomyError, load_taxonomy

from .conftest import make_candidate


def test_taxonomy_is_internally_consistent(taxonomy) -> None:
    group_ids = {g.id for g in taxonomy.groups}
    assert len(taxonomy.categories) == 12
    assert all(c.group in group_ids for c in taxonomy.categories)
    assert len(set(taxonomy.tag_names)) == len(taxonomy.tag_names)
    assert taxonomy.category("does-not-exist").id == "other"


def test_taxonomy_rejects_terms_duplicated_in_tags(tmp_path: Path) -> None:
    bad = (DEFAULTS_DIR / "taxonomy.yaml").read_text().replace(
        "strong: [SLAM, odometry,", "strong: [SLAM, visual SLAM, odometry,", 1
    )
    path = tmp_path / "taxonomy.yaml"
    path.write_text(bad)
    with pytest.raises(TaxonomyError, match="both as terms and in tags"):
        load_taxonomy(path, DEFAULTS_DIR / "venues.yaml")


@pytest.mark.parametrize(
    ("title", "abstract", "category", "expected_tags"),
    [
        (
            "Tightly Coupled LiDAR-Inertial Odometry with Loop Closure",
            "A LiDAR-inertial odometry system with loop closure and pose graph optimisation for UAV mapping.",
            "slam_perception",
            {"LiDAR-Inertial Odometry", "Loop Closure"},
        ),
        (
            "Action Chunking for Bimanual Cloth Folding",
            "We fold garments with a bimanual robot using imitation learning and action chunking.",
            "manipulation",
            {"Bimanual", "Deformable Objects"},
        ),
        (
            "Scaling Vision-Language-Action Models across Embodiments",
            "We train a VLA on cross-embodiment data and evaluate this generalist policy on six robots.",
            "vla_foundation",
            {"VLA", "Cross-Embodiment"},
        ),
        (
            "Cooperative Transport of a Payload with Two Mobile Manipulators",
            "Two mobile manipulators perform cooperative transport of a rigid payload with decentralized control.",
            "mobile_multi",
            {"Cooperative Transport", "Mobile Manipulator"},
        ),
        (
            "Learning Agile Parkour for Quadrupeds",
            "A quadruped robot learns agile locomotion over rough terrain with reinforcement learning.",
            "locomotion",
            {"Quadrupeds", "Terrain & Agility"},
        ),
    ],
)
def test_classifies_representative_papers(taxonomy, title, abstract, category, expected_tags) -> None:
    result = classify(title, abstract, "cs.RO", taxonomy)
    assert result.primary_category == category
    assert expected_tags <= set(result.secondary_tags)
    assert validate_classification(result, taxonomy) == []


def test_weak_evidence_falls_back_to_other_and_asks_for_review(taxonomy) -> None:
    result = classify("A Note on Things", "We discuss several general ideas.", "cs.RO", taxonomy)
    assert result.primary_category == "other"
    assert result.needs_review
    assert 0.0 <= result.confidence <= 1.0


def test_validate_classification_reports_unknown_values(taxonomy) -> None:
    bogus = Classification("made_up", ("Not A Tag",), 1.5, False)
    problems = validate_classification(bogus, taxonomy)
    assert len(problems) == 3


def test_robotics_gate(taxonomy) -> None:
    assert is_robotics_paper(make_candidate(source_categories=("cs.RO",)), taxonomy)
    passing_mention = make_candidate(
        title="Efficient Segmentation Transformers",
        abstract="A segmentation model. It may also help robotics.",
        source_categories=("cs.CV",),
    )
    assert not is_robotics_paper(passing_mention, taxonomy)
    in_title = make_candidate(
        title="Segmentation for Robot Grasping", abstract="Masks for picking.", source_categories=("cs.CV",)
    )
    assert is_robotics_paper(in_title, taxonomy)
