import pytest

from paper_radar.services.signals import detect_venue, extract_links


def test_extracts_code_and_project_links() -> None:
    links = extract_links(
        "Code: https://github.com/example/lio-uav. See also https://arxiv.org/abs/2401.00001.",
        "Project page: https://example.github.io/lio/",
    )
    assert links.code_url == "https://github.com/example/lio-uav"
    assert links.project_url == "https://example.github.io/lio/"


def test_bare_github_link_and_profile_pages() -> None:
    assert extract_links("Code at github.com/org/repo").code_url == "https://github.com/org/repo"
    # a profile page is not a code repository
    assert extract_links("https://github.com/someone").code_url is None


def test_no_links() -> None:
    links = extract_links("No links here.", None)
    assert links.code_url is None and links.project_url is None


@pytest.mark.parametrize(
    ("comment", "name", "tier", "evidence"),
    [
        ("Accepted at CoRL 2026. 24 pages", "CoRL 2026", "top", "accepted"),
        ("Accepted to the 10th Conference on Robot Learning (CoRL 2026), Austin", "CoRL 2026", "top", "accepted"),
        ("Published in IEEE Robotics and Automation Letters (RA-L)", "RA-L", "top", "published"),
        ("CoRL 2026", "CoRL 2026", "top", "stated"),
        ("Accepted by WACV 2027", "WACV 2027", "known", "accepted"),
        ("Accepted to the IROS 2026 SeMaNa Workshop.", "IROS 2026 workshop", "workshop", "accepted"),
        ("5 pages, IROS 2026 \"Data in Field Robotics: From State Estimation to Navigation\" workshop", "IROS 2026 workshop", "workshop", "stated"),
        ("Research statement for RSS Pioneers 2026", "RSS 2026 workshop", "workshop", "stated"),
    ],
)
def test_detects_stated_venues(taxonomy, comment, name, tier, evidence) -> None:
    venue = detect_venue(comment, None, taxonomy)
    assert venue is not None
    assert (venue.name, venue.tier, venue.evidence) == (name, tier, evidence)


@pytest.mark.parametrize(
    "comment",
    [
        "Submitted to ICRA 2027",
        "Submiited to ICRA 2027",
        "8 pages, under review at RA-L",
        "Extended version of our ICRA 2025 paper",
        "ICRA",  # no year and no acceptance wording
        "8 pages, 5 figures",
    ],
)
def test_does_not_count_unaccepted_or_ambiguous_mentions(taxonomy, comment) -> None:
    assert detect_venue(comment, None, taxonomy) is None


def test_journal_ref_counts_only_known_venues(taxonomy) -> None:
    ral = detect_venue(None, "IEEE Robotics and Automation Letters, vol. 11, 2026", taxonomy)
    assert ral is not None and ral.name == "RA-L 2026" and ral.evidence == "published"
    assert detect_venue(None, "Robots to the refuse, Jun. 10, 2016", taxonomy) is None
