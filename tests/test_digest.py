from dataclasses import replace

import pytest

from paper_radar import db
from paper_radar.llm.base import LLMError, LLMResponse
from paper_radar.models import ResourceLinks
from paper_radar.repositories import digests as digest_repo
from paper_radar.repositories import papers
from paper_radar.services.digest import (
    Digest,
    DigestError,
    SourceText,
    build_prompt,
    check_digest,
    generate_digest,
    generate_many,
    output_schema,
)

from .conftest import NOW, make_candidate, store
from .pdfgen import make_pdf

ABSTRACT = (
    "We present a LiDAR-inertial odometry system for UAVs with loop closure. "
    "On public datasets it reduces drift by 31% compared with FAST-LIO2."
)


def digest_data(**overrides):
    data = {
        "one_line_takeaway": "Adding loop closure to LiDAR-inertial odometry cuts UAV drift by 31% against FAST-LIO2.",
        "problem": "LiDAR-inertial odometry drifts over long UAV flights.",
        "core_idea": "Close loops and optimise a pose graph on top of a tightly coupled filter.",
        "method_steps": ["Run LiDAR-inertial odometry.", "Detect loop closures.", "Optimise the pose graph."],
        "experimental_evidence": [
            {
                "setting": "Public datasets",
                "dataset_or_robot": "Not reported in the inspected text",
                "baseline": "FAST-LIO2",
                "metric_or_result": "Drift reduced by 31%",
                "evidence_location": "Abstract",
            }
        ],
        "contributions": ["A loop-closing LiDAR-inertial odometry system for UAVs."],
        "limitations": [{"text": "Only public datasets are mentioned.", "kind": "interpretation", "evidence_location": "Abstract"}],
        "primary_category": "SLAM, Perception & State Estimation",
        "secondary_tags": ["LiDAR-Inertial Odometry", "Loop Closure"],
        "relevance_explanation": "Directly relevant to LiDAR SLAM on UAVs.",
        "manual_reading_checklist": ["Find the drift table and its baselines."],
        "unknowns": ["Which datasets were used."],
        "confidence": 0.7,
    }
    data.update(overrides)
    return data


class FakeProvider:
    name = "fake"

    def __init__(self, *responses, model="claude-opus-5-5"):
        self.model = model
        self.responses = list(responses)
        self.requests = []

    def generate_json(self, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(data=item, model=self.model, input_tokens=1000, output_tokens=500)


@pytest.fixture
def paper_id(conn, taxonomy):
    return store(conn, taxonomy, abstract=ABSTRACT, title="Loop Closure for LiDAR-Inertial Odometry on UAVs")


def run(conn, paper_id, provider, prefs, taxonomy, **kwargs):
    return generate_digest(conn, paper_id, provider=provider, prefs=prefs, taxonomy=taxonomy, now=NOW, **kwargs)


def test_schema_constrains_categories_and_tags(taxonomy) -> None:
    schema = output_schema(taxonomy)
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    assert "SLAM, Perception & State Estimation" in schema["properties"]["primary_category"]["enum"]
    assert "VIO" in schema["properties"]["secondary_tags"]["items"]["enum"]
    assert "source_level" not in schema["properties"]  # the app sets it, never the model


def test_abstract_digest_is_stored_cached_and_labelled(conn, paper_id, prefs, taxonomy) -> None:
    provider = FakeProvider(digest_data(), digest_data(confidence=0.9))
    outcome = run(conn, paper_id, provider, prefs, taxonomy, mode="abstract")
    assert not outcome.cached
    record = outcome.record
    assert record.source_level == "abstract_only" and record.digest["source_level"] == "abstract_only"
    assert record.source_note == "Title and abstract from arXiv"
    assert record.cost_usd == pytest.approx(1000 * 4e-6 + 500 * 20e-6)
    paper = papers.get_paper(conn, paper_id)
    assert paper.digest["one_line_takeaway"].startswith("Adding loop closure")
    assert paper.digest_source_level.value == "abstract_only" and not paper.digest_is_stale

    again = run(conn, paper_id, provider, prefs, taxonomy, mode="abstract")
    assert again.cached and len(provider.requests) == 1  # no second call
    forced = run(conn, paper_id, provider, prefs, taxonomy, mode="abstract", force=True)
    assert not forced.cached and forced.record.digest["confidence"] == 0.9
    assert digest_repo.usage_since(conn, NOW).calls == 2


def test_prompt_carries_honesty_rules_source_level_and_interests(conn, paper_id, prefs, taxonomy) -> None:
    paper = papers.get_paper(conn, paper_id)
    prompt = build_prompt(paper, SourceText("abstract_only", ABSTRACT, "Title and abstract from arXiv"), prefs, taxonomy)
    assert "You have not seen the full paper" in prompt
    assert "<paper>" in prompt and ABSTRACT in prompt
    assert "SLAM, perception & state estimation (20)" in prompt


def test_checks_catch_unseen_locations_and_invented_numbers(conn, paper_id, taxonomy) -> None:
    paper = papers.get_paper(conn, paper_id)
    source = SourceText("abstract_only", ABSTRACT, "abstract")
    clean = Digest.model_validate({**digest_data(), "source_level": "abstract_only"})
    assert check_digest(clean, source, paper, taxonomy) == []

    bad = digest_data(
        one_line_takeaway="Cuts drift by 45% versus FAST-LIO2.",
        experimental_evidence=[{**digest_data()["experimental_evidence"][0], "evidence_location": "Table II, p. 6"}],
    )
    problems = check_digest(Digest.model_validate({**bad, "source_level": "abstract_only"}), source, paper, taxonomy)
    assert any("cite parts of the paper that were not seen" in p for p in problems)
    assert any("45" in p and "do not appear" in p for p in problems)


def test_a_flawed_draft_gets_one_repair_round(conn, paper_id, prefs, taxonomy) -> None:
    provider = FakeProvider(digest_data(one_line_takeaway="Cuts drift by 45%."), digest_data())
    record = run(conn, paper_id, provider, prefs, taxonomy, mode="abstract").record
    assert len(provider.requests) == 2
    assert "failed automatic checks" in provider.requests[1].prompt and "45" in provider.requests[1].prompt
    assert record.digest["verification_notes"] == []
    assert record.cost_usd == pytest.approx(2 * (1000 * 4e-6 + 500 * 20e-6))


def test_problems_that_survive_repair_are_kept_as_notes(conn, paper_id, prefs, taxonomy) -> None:
    flawed = digest_data(one_line_takeaway="Cuts drift by 45%.")
    record = run(conn, paper_id, FakeProvider(flawed, flawed), prefs, taxonomy, mode="abstract").record
    assert any("45" in note for note in record.digest["verification_notes"])


def test_provider_errors_are_reported_and_logged(conn, paper_id, prefs, taxonomy) -> None:
    provider = FakeProvider(LLMError("Claude rejected the API key.", "auth"))
    with pytest.raises(DigestError) as error:
        run(conn, paper_id, provider, prefs, taxonomy, mode="abstract")
    assert error.value.kind == "auth"
    usage = digest_repo.usage_since(conn, NOW)
    assert (usage.calls, usage.failed) == (1, 1)
    assert papers.get_paper(conn, paper_id).digest is None


def test_malformed_output_is_rejected(conn, paper_id, prefs, taxonomy) -> None:
    broken = digest_data()
    del broken["problem"]
    with pytest.raises(DigestError, match="expected format"):
        run(conn, paper_id, FakeProvider(broken), prefs, taxonomy, mode="abstract")


def test_full_text_digest_reads_the_pdf_once_and_prefers_full_text(conn, paper_id, prefs, taxonomy) -> None:
    body = ABSTRACT + " " + "The method runs onboard at 10 Hz on a Jetson. " * 40
    pdf = make_pdf(["I. INTRODUCTION\n" + body[:900], "II. METHOD\n" + body[900:1800], "REFERENCES\n[1] A. Ref, 2020."])
    fetches = []

    def fetch(url):
        fetches.append(url)
        return pdf

    run(conn, paper_id, FakeProvider(digest_data()), prefs, taxonomy, mode="abstract")
    on_page_two = [{**digest_data()["experimental_evidence"][0], "evidence_location": "p. 2, Sec. II"}]
    provider = FakeProvider(digest_data(experimental_evidence=on_page_two), digest_data())
    record = run(conn, paper_id, provider, prefs, taxonomy, mode="full_text", fetch_pdf=fetch).record
    assert record.source_level == "full_text"
    assert record.source_note == "Full text of the arXiv PDF (3 pages; bibliography omitted)"
    assert "[Page 1]" in provider.requests[0].prompt and "A. Ref" not in provider.requests[0].prompt
    assert papers.get_paper(conn, paper_id).digest_source_level.value == "full_text"  # best digest wins

    run(conn, paper_id, provider, prefs, taxonomy, mode="full_text", fetch_pdf=fetch, force=True)
    assert len(fetches) == 1  # extracted text is reused


def test_full_text_failure_explains_itself(conn, paper_id, prefs, taxonomy) -> None:
    with pytest.raises(DigestError, match="scanned"):
        run(conn, paper_id, FakeProvider(), prefs, taxonomy, mode="full_text", fetch_pdf=lambda url: make_pdf(["", ""]))


def test_new_paper_version_makes_the_digest_stale(conn, paper_id, prefs, taxonomy) -> None:
    run(conn, paper_id, FakeProvider(digest_data()), prefs, taxonomy, mode="abstract")
    revised = make_candidate(abstract=ABSTRACT + " Now with real flights.", arxiv_version=2)
    papers.update_metadata(conn, paper_id, revised, ResourceLinks(), None, NOW)
    assert papers.get_paper(conn, paper_id).digest_is_stale


def test_batch_stops_when_the_key_is_missing(db_path, taxonomy, prefs) -> None:
    with db.session(db_path) as conn:
        ids = [store(conn, taxonomy, arxiv_id=f"2610.0000{i}", title=f"Paper {i}", abstract=ABSTRACT) for i in (1, 2, 3)]
    provider = FakeProvider(digest_data(), LLMError("No key", "not_configured"), digest_data())
    report = generate_many(
        lambda: db.session(db_path), ids, provider=provider, prefs=prefs, taxonomy=taxonomy, mode="abstract", now=lambda: NOW
    )
    assert report.generated == 1 and len(report.failed) == 1 and report.failed[0][0] == ids[1]
    assert len(provider.requests) == 2  # stopped instead of failing the same way again


def test_preferences_validate_model_and_effort(prefs, taxonomy) -> None:
    from paper_radar.preferences import validate

    clean = validate(replace(prefs, digest_model="gpt-whatever", digest_effort="ludicrous", fulltext_max_chars=5), taxonomy)
    assert (clean.digest_model, clean.digest_effort, clean.fulltext_max_chars) == ("claude-opus-5-5", "medium", 20_000)
