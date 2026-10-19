from datetime import timedelta

import pytest

from paper_radar.text import compile_phrase, match_text, match_view, relative_time, tex_to_text

from .conftest import NOW


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"Steering $\pi_0$ policies", "Steering π₀ policies"),
        (r"\textit{SE}(3) estimation", "SE(3) estimation"),
        (r"error below $10^{-3}$", "error below 10⁻³"),
        (r"a 50$\times$ speed-up", "a 50× speed-up"),
        (r"Schr\"odinger", "Schrödinger"),
        (r"costs \$5 and 90$^\circ$", "costs $5 and 90°"),
        (r"pages 1--5 --- roughly", "pages 1–5 — roughly"),
        (r"\unknowncommand stays", r"\unknowncommand stays"),
        ("  extra   whitespace\n here ", "extra whitespace here"),
    ],
)
def test_tex_to_text(raw: str, expected: str) -> None:
    assert tex_to_text(raw) == expected


def test_match_text_normalises_spelling_and_hyphens() -> None:
    assert match_text("Optimisation of behaviour") == "Optimization of behavior"
    assert match_text("visual–inertial") == "visual-inertial"
    assert match_text(r"$\pi_0$") == "π0"


@pytest.mark.parametrize(
    ("phrase", "text", "hit"),
    [
        ("sim-to-real", "a sim to real transfer", True),
        ("sim-to-real", "sim2real", False),
        ("diffusion policy", "two diffusion policies", True),
        ("VIO", "a VIO system", True),
        ("VIO", "a vio system", False),
        ("VIO", "violations", False),
        ("ACT", "we act quickly", False),
        ("manipulat*", "manipulators", True),
        ("optimisation-based control", "optimization based control", True),
        ("π0", r"policies such as $\pi_0$", True),
        ("UAV", "fleets of UAVs", True),
        (r"re:\bintroduc\w*\s+(?:\w+[\s-]+){0,4}dataset", "We introduce a large real-world dataset", True),
        ("trajectory", "many trajectories", True),
        ("SLAM", "SLAMs", True),
    ],
)
def test_compile_phrase(phrase: str, text: str, hit: bool) -> None:
    view = match_view(text, "")
    assert compile_phrase(phrase).in_title(view) is hit


def test_phrase_counts_abstract_hits_up_to_cap() -> None:
    view = match_view("", "SLAM here, SLAM there, SLAM everywhere, SLAM again, SLAM")
    assert compile_phrase("SLAM").in_abstract(view, cap=4) == 4


def test_relative_time() -> None:
    assert relative_time(NOW - timedelta(seconds=20), NOW) == "just now"
    assert relative_time(NOW - timedelta(minutes=5), NOW) == "5 minutes ago"
    assert relative_time(NOW - timedelta(hours=1), NOW) == "1 hour ago"
    assert relative_time(NOW - timedelta(days=3), NOW) == "3 days ago"
