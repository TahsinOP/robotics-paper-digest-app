"""Render every page headlessly with Streamlit's AppTest against a seeded temporary database."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from paper_radar import db
from paper_radar.models import Status
from paper_radar.repositories import papers
from paper_radar.repositories.papers import PaperQuery
from paper_radar.services.ingestion import refresh_papers
from paper_radar.sources.arxiv_source import parse_feed
from paper_radar.ui.context import get_context

from .conftest import FIXTURES, NOW, FakeSource, make_candidate

PAGE_SCRIPT = """
from paper_radar.ui import theme, {module}
theme.apply()
{module}.page()
"""


@pytest.fixture
def seeded_db(tmp_path, monkeypatch, prefs, taxonomy):
    path = tmp_path / "ui.db"
    monkeypatch.setenv("PAPER_RADAR_DB_PATH", str(path))
    db.initialise(path)
    with db.session(path) as conn:
        feed = parse_feed((FIXTURES / "arxiv_page.xml").read_bytes()).entries
        refresh_papers(conn, FakeSource(feed), prefs, taxonomy, now=NOW)
    get_context.clear()
    yield path
    get_context.clear()


def run_page(module: str) -> AppTest:
    app = AppTest.from_string(PAGE_SCRIPT.format(module=module), default_timeout=30)
    app.session_state["feed_window"] = "all"  # fixture papers have fixed dates; don't depend on today's date
    app.run()
    assert not app.exception, [e.message for e in app.exception]
    return app


@pytest.mark.parametrize("module", ["feed", "queue", "library", "settings"])
def test_pages_render(seeded_db, module) -> None:
    run_page(module)


def test_feed_lists_papers_and_shortlisting_persists(seeded_db) -> None:
    app = run_page("feed")
    titles = [b.label for b in app.button if b.key and b.key.startswith("ptitle-")]
    assert any("LiDAR" in t for t in titles)
    with db.session(seeded_db) as conn:
        paper = papers.list_papers(conn, PaperQuery())[0]
    app.button(key=f"act-sl-{paper.id}").click().run()
    assert not app.exception
    with db.session(seeded_db) as conn:
        assert papers.get_paper(conn, paper.id).status == Status.SHORTLISTED


def test_empty_feed_explains_what_to_do(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPER_RADAR_DB_PATH", str(tmp_path / "empty.db"))
    get_context.clear()
    app = run_page("feed")
    assert any("Your feed is empty" in h.proto.body for h in app.get("html"))
    get_context.clear()


def test_show_more_extends_the_feed(tmp_path, monkeypatch, prefs, taxonomy) -> None:
    path = tmp_path / "many.db"
    monkeypatch.setenv("PAPER_RADAR_DB_PATH", str(path))
    db.initialise(path)
    candidates = [
        make_candidate(arxiv_id=f"2610.{i:05d}", title=f"Visual SLAM for Robot Number {i}") for i in range(30)
    ]
    with db.session(path) as conn:
        refresh_papers(conn, FakeSource(candidates), prefs, taxonomy, now=NOW)
    get_context.clear()
    app = run_page("feed")
    count = lambda: len([b for b in app.button if b.key and b.key.startswith("ptitle-")])  # noqa: E731
    assert count() == 25
    next(b for b in app.button if b.label.startswith("Show 5 more")).click().run()
    assert not app.exception
    assert count() == 30
    get_context.clear()


def test_feed_shows_ai_takeaways_marked_as_ai(seeded_db, prefs, taxonomy) -> None:
    from paper_radar.llm.base import LLMResponse
    from paper_radar.services.digest import generate_digest

    from .test_digest import digest_data

    class Scripted:
        name, model = "scripted", "claude-opus-5-5"

        def generate_json(self, request):
            return LLMResponse(data=digest_data(), model=self.model, input_tokens=10, output_tokens=10)

    with db.session(seeded_db) as conn:
        paper = next(p for p in papers.list_papers(conn, PaperQuery()) if p.arxiv_id == "2610.11111")
        generate_digest(conn, paper.id, provider=Scripted(), prefs=prefs, taxonomy=taxonomy, mode="abstract", now=NOW)
    get_context.clear()
    app = run_page("feed")
    bodies = " ".join(h.proto.body for h in app.get("html"))
    assert 'class="pr-ai"' in bodies and "Adding loop closure" in bodies


@pytest.mark.parametrize("with_key", [False, True])
def test_settings_ai_section_in_both_key_states(seeded_db, monkeypatch, with_key) -> None:
    if with_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    app = run_page("settings")
    text = " ".join(str(c.value) for c in app.caption)
    assert ("environment variable" in text) if with_key else ("No Claude API key yet" in text)
    assert "sk-ant-test" not in text
