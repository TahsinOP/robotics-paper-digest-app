import pytest
from mcp.server.mcpserver.exceptions import ToolError

from paper_radar import db, mcp_server
from paper_radar.repositories import papers
from paper_radar.services import reading_queue

from .conftest import NOW, make_candidate, store
from .pdfgen import make_pdf
from .test_digest import ABSTRACT, digest_data


@pytest.fixture
def library(tmp_path, monkeypatch, taxonomy):
    path = tmp_path / "mcp.db"
    monkeypatch.setenv("PAPER_RADAR_DB_PATH", str(path))
    mcp_server._setup.cache_clear()
    db.initialise(path)
    with db.session(path) as conn:
        paper_id = store(conn, taxonomy, candidate=make_candidate(abstract=ABSTRACT, title="Loop Closure for LiDAR-Inertial Odometry"))
    yield path, paper_id
    mcp_server._setup.cache_clear()


def test_find_and_get_papers(library) -> None:
    _, paper_id = library
    found = mcp_server.find_papers(query="loop closure")
    assert found[0]["paper"] == str(paper_id) and found[0]["topic"] == "SLAM, Perception & State Estimation"
    detail = mcp_server.get_paper("2610.00001")  # arXiv ids work too
    assert detail["abstract"].startswith("We present") and detail["saved_digest"] is None
    with pytest.raises(ToolError, match="No paper"):
        mcp_server.get_paper("9999")


def test_digest_rules_and_checked_saving(library) -> None:
    path, paper_id = library
    rules = mcp_server.digest_instructions()
    assert "Honesty rules" in rules["rules"] and "properties" in rules["schema"]

    bad = mcp_server.save_digest(str(paper_id), digest_data(one_line_takeaway="Cuts drift by 45%."), "abstract_only")
    assert bad["saved"] is False and any("45" in p for p in bad["problems"])
    good = mcp_server.save_digest(str(paper_id), digest_data(), "abstract_only", model="claude-opus-5-5")
    assert good == {"saved": True, "paper": str(paper_id), "source_level": "abstract_only", "verification_notes": []}
    with db.session(path) as conn:
        assert papers.get_paper(conn, paper_id).digest["one_line_takeaway"].startswith("Adding loop closure")
    forced = mcp_server.save_digest(str(paper_id), digest_data(one_line_takeaway="Cuts drift by 45%."), "abstract_only",
                                    accept_with_notes=True)
    assert forced["saved"] and forced["verification_notes"]


def test_full_text_must_be_read_before_a_full_text_digest(library, monkeypatch) -> None:
    _, paper_id = library
    with pytest.raises(ToolError, match="get_full_text first"):
        mcp_server.save_digest(str(paper_id), digest_data(), "full_text")
    body = ABSTRACT + " " + "It runs onboard at 10 Hz. " * 80
    monkeypatch.setattr(mcp_server, "download_pdf", lambda url: make_pdf([body[:1200], body[1200:2400]]))
    text = mcp_server.get_full_text(str(paper_id))
    assert text["source_level"] == "full_text" and text["text"].startswith("[Page 1]")
    assert mcp_server.get_full_text(str(paper_id), first_page=2)["text"].startswith("[Page 2]")
    assert mcp_server.save_digest(str(paper_id), digest_data(), "full_text")["saved"]


def test_week_notes_and_status(library) -> None:
    path, paper_id = library
    with db.session(path) as conn:
        reading_queue.add_to_week(conn, paper_id, reading_queue.week_start(NOW.astimezone().date()), NOW)
    mcp_server.add_note(str(paper_id), "Key idea: close loops on a pose graph.")
    mcp_server.add_note(str(paper_id), "Try it on the UAV logs.")
    assert mcp_server.set_status(str(paper_id), "reading")["status"] == "reading"
    with db.session(path) as conn:
        paper = papers.get_paper(conn, paper_id)
    assert paper.status.value == "reading"
    assert "Key idea" in paper.personal_notes and "UAV logs" in paper.personal_notes and paper.personal_notes.count("[Claude Code") == 2


def test_bad_status_is_explained(library) -> None:
    _, paper_id = library
    with pytest.raises(ToolError, match="status must be one of"):
        mcp_server.set_status(str(paper_id), "finished")
