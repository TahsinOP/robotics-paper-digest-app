"""The ranked-list entry used by the feed. Each entry is a fragment, so shortlisting or skipping
re-renders only that entry instead of the whole page."""

from __future__ import annotations

import streamlit as st

from paper_radar.models import Paper, Status
from paper_radar.repositories import papers
from paper_radar.repositories import reading_queue as queue_repo
from paper_radar.services import library
from paper_radar.ui import digest_view, theme
from paper_radar.ui.context import get_context, now, this_week
from paper_radar.ui.paper_detail import open_paper


def _set_status(paper_id: int, status: Status) -> None:
    with get_context().connect() as conn:
        library.change_status(conn, paper_id, status, now(), this_week())


def _skip(paper_id: int, previous: str) -> None:
    st.session_state[f"undo-{paper_id}"] = previous
    _set_status(paper_id, Status.SKIPPED)


def _undo_skip(paper_id: int) -> None:
    previous = st.session_state.pop(f"undo-{paper_id}", Status.NEW.value)
    _set_status(paper_id, Status(previous))


def title_button(paper: Paper, key_prefix: str = "ptitle") -> None:
    """The paper title as a button that opens the detail dialog. Shortlisted titles get the
    highlighter mark; read and skipped titles are dimmed."""
    if paper.status == Status.SHORTLISTED:
        state = "mark"
    elif paper.status in (Status.READ, Status.SKIPPED):
        state = "done"
    else:
        state = "plain"
    if st.button(theme.md_escape(theme.display_title(paper)), key=f"{key_prefix}-{state}-{paper.id}", type="tertiary"):
        open_paper(paper.id)


def _actions(paper: Paper) -> None:
    pid = paper.id
    with st.container(horizontal=True, key=f"act-{pid}", gap="small", vertical_alignment="center"):
        if paper.status == Status.SHORTLISTED:
            st.button("Shortlisted", icon=":material/bookmark_added:", key=f"act-sl-on-{pid}", type="tertiary",
                      on_click=_set_status, args=(pid, Status.NEW), help="Remove from your shortlist")
        elif paper.status in (Status.NEW, Status.SKIPPED):
            st.button("Shortlist", icon=":material/bookmark_add:", key=f"act-sl-{pid}", type="tertiary",
                      on_click=_set_status, args=(pid, Status.SHORTLISTED))
        if paper.status == Status.READ:
            st.button("Mark unread", icon=":material/undo:", key=f"act-read-{pid}", type="tertiary",
                      on_click=_set_status, args=(pid, Status.NEW))
        else:
            st.button("Mark read", icon=":material/check:", key=f"act-read-{pid}", type="tertiary",
                      on_click=_set_status, args=(pid, Status.READ))
        if paper.status != Status.SKIPPED:
            st.button("Skip", icon=":material/do_not_disturb_on:", key=f"act-skip-{pid}", type="tertiary",
                      on_click=_skip, args=(pid, paper.status.value), help="Hide this paper from the feed")
        st.space("stretch")
        if paper.source_url:
            st.link_button("arXiv", paper.source_url, type="tertiary", icon=":material/open_in_new:")
        if paper.pdf_url:
            st.link_button("PDF", paper.pdf_url, type="tertiary", icon=":material/picture_as_pdf:")


@st.fragment
def paper_entry(paper_id: int) -> None:
    ctx = get_context()
    with ctx.connect() as conn:
        paper = papers.get_paper(conn, paper_id)
        queued = queue_repo.contains(conn, this_week(), paper_id)
    if paper is None:
        return

    if paper.status == Status.SKIPPED and f"undo-{paper_id}" in st.session_state:
        with st.container(key=f"entry-skipped-{paper_id}", horizontal=True, vertical_alignment="center"):
            st.html(f'<span class="pr-note">Skipped “{theme.esc(theme.display_title(paper)[:90])}”</span>')
            st.button("Undo", key=f"act-undo-{paper_id}", type="tertiary", on_click=_undo_skip, args=(paper_id,))
        return

    states = []
    if paper.status == Status.READING:
        states.append("Reading")
    if queued:
        states.append("This week")
    with st.container(key=f"entry-{paper_id}"):
        gutter, body = st.columns([0.11, 0.89], gap="small")
        gutter.html(theme.gutter_html(paper))
        with body:
            title_button(paper)
            st.html(
                f'<div class="pr-authors">{theme.esc(theme.author_line(paper.authors))}</div>'
                + theme.chips_html(paper, ctx.taxonomy, extra=tuple(states))
                + digest_view.takeaway_html(paper)
                + (f'<p class="pr-why">{theme.esc(paper.relevance_explanation)}</p>' if paper.relevance_explanation else "")
            )
            _actions(paper)
