"""Explore past years: a year's most-cited papers, and everything from a past month or year."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import streamlit as st

from paper_radar.models import Status
from paper_radar.repositories import papers
from paper_radar.repositories.papers import PaperQuery
from paper_radar.services import explore
from paper_radar.services.ingestion import make_source
from paper_radar.sources.base import SourceError
from paper_radar.sources.semantic_scholar import CitedPaper, SemanticScholar
from paper_radar.ui import theme
from paper_radar.ui.components import paper_entry, title_button
from paper_radar.ui.context import AppContext, get_context, now

PAGE_SIZE = 25
CITED_LIMIT = 25
FIRST_YEAR = 1998  # arXiv's robotics category (cs.RO) starts in November 1998


@st.cache_data(ttl=6 * 3600, max_entries=40, show_spinner=False)
def _most_cited(year: int, topic: str) -> list[CitedPaper]:
    return SemanticScholar().most_cited(year, topic=topic, limit=CITED_LIMIT)


def _add(ctx: AppContext, cited: list[CitedPaper]) -> None:
    with ctx.connect() as conn:
        prefs = ctx.preferences(conn)
        ids = explore.add_cited_papers(conn, cited, make_source(prefs, ctx.taxonomy, ctx.config), prefs, ctx.taxonomy, now())
    st.session_state["explore_toast"] = f"Added {len(ids)} paper{'s' if len(ids) != 1 else ''} to your library."


def _cited_tab(ctx: AppContext, year: int) -> None:
    with st.container(horizontal=True, vertical_alignment="bottom", gap="small"):
        topic = st.text_input(
            "Topic (optional)", key="explore_topic", placeholder="e.g. SLAM, diffusion policy, humanoid",
            help="Narrows the robotics search. Leave empty for all robotics.",
        )
        find = st.button("Find the most cited", icon=":material/trending_up:", type="primary")
    if find:
        with st.spinner("Asking Semantic Scholar…"):
            try:
                st.session_state["explore_cited"] = (year, topic.strip(), _most_cited(year, topic.strip()))
            except SourceError as exc:
                st.error(str(exc), title="Citations unavailable right now")
                return
    stored = st.session_state.get("explore_cited")
    if not stored or stored[:2] != (year, topic.strip()):
        st.caption(
            f"Lists the robotics papers submitted to arXiv in {year} with the most citations. Counts come from "
            "Semantic Scholar, which merges arXiv and published versions."
        )
        return
    cited: list[CitedPaper] = stored[2]
    if not cited:
        st.caption("Nothing found. Try a broader topic.")
        return

    with ctx.connect() as conn:
        explore.record_citations(conn, cited, now())
        in_library = papers.ids_by_arxiv(conn, [c.arxiv_id for c in cited])
        shown = {pid: papers.get_paper(conn, pid) for pid in in_library.values()}
    missing = [c for c in cited if c.arxiv_id not in in_library]
    with st.container(horizontal=True, vertical_alignment="center", key="act-cited-head"):
        st.caption(
            f"Top {len(cited)} for {year}{f' on “{topic.strip()}”' if topic.strip() else ''}. Recent years have had "
            "less time to gather citations; influential citations come from papers that build on the work."
        )
        st.space("stretch")
        if missing and st.button(f"Add all {len(missing)} to library", icon=":material/library_add:", type="tertiary"):
            with st.spinner("Fetching from arXiv…"):
                _add(ctx, missing)
            st.rerun()

    for rank, c in enumerate(cited, start=1):
        with st.container(key=f"cited-{c.arxiv_id.replace('.', '-').replace('/', '-')}"):
            number, body, action = st.columns([0.07, 0.69, 0.24], gap="small")
            number.html(f'<div class="pr-qnum">{rank}</div>')
            with body:
                paper = shown.get(in_library.get(c.arxiv_id, -1))
                if paper:
                    title_button(paper, key_prefix="ptitle-x")
                else:
                    st.html(f'<div class="pr-cited-title">{theme.esc(c.title)}</div>')
                venue = f"; {c.venue}" if c.venue else ""
                st.html(
                    f'<div class="pr-authors">{theme.esc(theme.author_line(c.authors))}</div>'
                    f'<div class="pr-chips"><span class="pr-chip pr-cites">{c.citation_count:,} citations</span>'
                    f'<span class="pr-chip pr-tag">{c.influential_count:,} influential</span>'
                    f'<span class="pr-chip pr-tag">arXiv {theme.esc(c.arxiv_id)}{theme.esc(venue)}</span></div>'
                )
            with action:
                if paper:
                    st.badge("In your library", icon=":material/check:", color="green")
                elif st.button("Add to library", key=f"add-{c.arxiv_id}", icon=":material/library_add:", width="stretch"):
                    with st.spinner("Fetching from arXiv…"):
                        _add(ctx, [c])
                    st.rerun()


def _period_tab(ctx: AppContext, year: int, month: int | None) -> None:
    since, until = explore.period_bounds(year, month)
    label = explore.period_label(year, month)
    query = PaperQuery(
        published_since=since,
        published_until=until,
        statuses=(Status.NEW, Status.SHORTLISTED, Status.READING, Status.READ),
        sort="timeless",
    )
    with ctx.connect() as conn:
        prefs = ctx.preferences(conn)
        total = papers.count_papers(conn, query)

    in_future = since >= now()
    with st.container(horizontal=True, vertical_alignment="center", key="act-period-head"):
        if total:
            st.caption(
                f"{total:,} paper{'s' if total != 1 else ''} from {label} in your library, "
                "ranked for your interests without recency."
            )
        else:
            st.caption(f"No papers from {label} in your library yet.")
        st.space("stretch")
        fetch = not in_future and st.button(
            f"Fetch {label} from arXiv",
            icon=":material/download:",
            type="primary" if not total else "tertiary",
            help="arXiv asks for a three-second pause between requests: about a minute per month, "
            "several minutes for a whole year. Months finished before you stop are kept.",
        )
    if fetch:
        with st.status(f"Fetching {label} from arXiv…", expanded=True) as status:
            report = explore.fetch_period(
                ctx.connect, make_source(prefs, ctx.taxonomy, ctx.config, page_size=1000), prefs, ctx.taxonomy, year, month,
                progress=lambda message: status.update(label=message), now=now,
            )
            summary = f"{report.inserted:,} new papers from {report.fetched:,} fetched"
            status.update(label=summary + ("." if not report.errors else f"; {len(report.errors)} problems."),
                          state="complete" if not report.errors else "error")
            for problem in report.errors[:3]:
                st.write(problem)
        st.session_state["explore_toast"] = summary + "."
        st.rerun()

    text = st.text_input(
        f"Search {label}",
        key="explore_search",
        placeholder="Keywords, e.g. loop closure lidar or \"diffusion policy\"",
        icon=":material/search:",
        help="Searches the papers from this period in your library (title, abstract, authors, your notes). "
        "Use the arXiv button to search arXiv itself, without fetching the whole period.",
    ).strip()
    if text and not in_future:
        _arxiv_search_row(ctx, prefs, text, year, month, label)
    if text:
        query = replace(query, search=text, search_notes=True)
        with ctx.connect() as conn:
            total = papers.count_papers(conn, query)
        if not total:
            st.caption(f"No papers in your library from {label} match “{text}”.")
            return
        st.caption(f"{total:,} match{'es' if total != 1 else ''} in your library, best for your interests first.")
    elif not total:
        return

    signature = (year, month, text)
    if st.session_state.get("explore_signature") != signature:
        st.session_state["explore_signature"] = signature
        st.session_state["explore_limit"] = PAGE_SIZE
    limit = st.session_state["explore_limit"]
    with ctx.connect() as conn:
        shown = papers.list_papers(conn, replace(query, limit=limit))
    for paper in shown:
        paper_entry(paper.id, timeless=True)
    if total > len(shown) and st.button(f"Show {min(PAGE_SIZE, total - len(shown))} more", icon=":material/expand_more:"):
        st.session_state["explore_limit"] = limit + PAGE_SIZE
        st.rerun()


def _arxiv_search_row(ctx: AppContext, prefs, text: str, year: int, month: int | None, label: str) -> None:
    with st.container(horizontal=True, vertical_alignment="center", key="act-arxiv-search"):
        st.space("stretch")
        clicked = st.button(
            f"Search arXiv for “{text}” in {label}",
            icon=":material/travel_explore:",
            type="tertiary",
            help="Asks arXiv directly (cs.RO and your neighbouring categories) and adds the best 200 matches "
            "to your library. Takes a few seconds.",
        )
    if clicked:
        with st.spinner("Searching arXiv…"), ctx.connect() as conn:
            report = explore.search_arxiv(
                conn, make_source(prefs, ctx.taxonomy, ctx.config), prefs, ctx.taxonomy, text, year, month, now=now()
            )
        if report.errors and not report.found:
            st.error(" ".join(report.errors), title="arXiv search didn't work")
            return
        st.session_state["explore_toast"] = (
            f"arXiv found {report.found:,} match{'es' if report.found != 1 else ''}; "
            f"{report.inserted:,} new to your library."
        )
        st.rerun()


def page() -> None:
    ctx = get_context()
    if message := st.session_state.pop("explore_toast", None):
        st.toast(message, icon=":material/check_circle:")
    st.title("Explore past years")
    st.html(
        '<p class="pr-lede">Look back at what mattered: the most-cited robotics papers of a year, and everything '
        "arXiv has from a past month or year, ranked for your interests.</p>"
    )
    this_year = datetime.now().year
    with st.container(horizontal=True, gap="small"):
        year = st.selectbox("Year", list(range(this_year, FIRST_YEAR - 1, -1)), index=1, key="explore_year")
        month = st.selectbox(
            "Month", [0, *range(1, 13)], key="explore_month",
            format_func=lambda m: "Whole year" if m == 0 else explore.MONTH_NAMES[m - 1],
            help="Citations are counted per year; the month narrows the arXiv list.",
        )
    # Fixed labels: a label that changed with the month would reset the selected tab.
    cited_tab, period_tab = st.tabs(["Most cited", "Everything on arXiv"], key="explore_tabs")
    with cited_tab:
        _cited_tab(ctx, year)
    with period_tab:
        _period_tab(ctx, year, month or None)
