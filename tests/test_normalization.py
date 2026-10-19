import pytest

from paper_radar.services.normalization import author_key, content_hash, normalize_doi, normalize_title, parse_arxiv_id

from .conftest import make_candidate


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://arxiv.org/abs/2410.01234v2", ("2410.01234", 2)),
        ("https://arxiv.org/pdf/2410.01234v3.pdf", ("2410.01234", 3)),
        ("arXiv:2410.01234", ("2410.01234", None)),
        ("2410.1234", ("2410.1234", None)),
        ("http://arxiv.org/abs/cs/0101001v1", ("cs/0101001", 1)),
        ("math.OC/0601001", ("math.oc/0601001", None)),
        ("not-an-id", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_arxiv_id(raw, expected) -> None:
    assert parse_arxiv_id(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1108/IR-05-2025-0162", "10.1108/ir-05-2025-0162"),
        ("https://doi.org/10.1108/IR-05-2025-0162", "10.1108/ir-05-2025-0162"),
        ("doi: 10.1/X", "10.1/x"),
        ("http://dx.doi.org/10.5555/abc", "10.5555/abc"),
        ("arXiv:2410.01234", None),
        (None, None),
    ],
)
def test_normalize_doi(raw, expected) -> None:
    assert normalize_doi(raw) == expected


def test_normalize_title_ignores_case_punctuation_latex_and_accents() -> None:
    a = normalize_title(r"Steering $\pi_0$: Action-Chunking for Bimanual Folding!")
    b = normalize_title("steering π₀ — action chunking for bimanual folding")
    assert a == b
    assert normalize_title("Café SLAM") == "cafe slam"


def test_author_key() -> None:
    assert author_key("Tahsin Khan") == "khan"
    assert author_key("Khan, Tahsin") == "khan"
    assert author_key("José Núñez") == "nunez"


def test_content_hash_changes_with_abstract_or_version() -> None:
    base = make_candidate()
    assert content_hash(base) == content_hash(make_candidate())
    assert content_hash(base) != content_hash(make_candidate(abstract="Different abstract."))
    assert content_hash(base) != content_hash(make_candidate(arxiv_version=2))
