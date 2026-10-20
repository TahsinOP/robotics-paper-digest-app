"""The paper detail view, shown as a large dialog over any page."""

from __future__ import annotations

import streamlit as st

from paper_radar.models import Paper, Status
from paper_radar.repositories import digests as digest_repo
from paper_radar.repositories import papers
from paper_radar.repositories import reading_queue as queue_repo
from paper_radar.services import library, reading_queue
from paper_radar.text import relative_time, tex_to_text
from paper_radar.ui import digest_view, theme
from paper_radar.ui.context import get_context, now, this_week

STATUS_LABELS = {
    Status.NEW.value: "New",
    Status.SHORTLISTED.value: "Shortlisted",
    Status.READING.value: "Reading",
    Status.READ.value: "Read",
    Status.SKIPPED.value: "Skipped",
}
COMPONENT_LABELS = {
    "interest": "Topic match",
    "keywords": "Your keywords",
    "recency": "Recency",
    "resources": "Code or project",
    "venue": "Venue",
}


def _on_dismiss() -> None:
    # Tables that opened the dialog through row selection get a fresh key, clearing the selection.
    st.session_state["dialog_nonce"] = st.session_state.get("dialog_nonce", 0) + 1


def open_paper(paper_id: int) -> None:
    """Show the paper in a large dialog headed by its arXiv identifier."""
    with get_context().connect() as conn:
        paper = papers.get_paper(conn, paper_id)
    heading = f"arXiv {paper.arxiv_id}" if paper and paper.arxiv_id else "Paper"
    st.dialog(heading, width="medium", on_dismiss=_on_dismiss)(render_detail)(paper_id)


def _status_changed(paper_id: int, key: str) -> None:
    with get_context().connect() as conn:
        library.change_status(conn, paper_id, Status(st.session_state[key]), now(), this_week())


def _add_to_week(paper_id: int) -> None:
    with get_context().connect() as conn:
        reading_queue.add_to_week(conn, paper_id, this_week(), now())
    st.toast("Added to this week's reading list.")


def _remove_from_week(paper_id: int) -> None:
    with get_context().connect() as conn:
        reading_queue.remove_from_week(conn, paper_id, this_week())


def _save_notes(paper_id: int, key: str) -> None:
    with get_context().connect() as conn:
        library.save_notes(conn, paper_id, st.session_state.get(key, ""), now())


def _meta_line(paper: Paper) -> str:
    parts = [f"Submitted {theme.long_date(paper.published_at)}"]
    if paper.updated_at and paper.published_at and paper.updated_at.date() != paper.published_at.date():
        parts.append(f"revised {theme.long_date(paper.updated_at)}")
    ident = f"arXiv {paper.arxiv_id}" + (f"v{paper.arxiv_version}" if paper.arxiv_version else "")
    categories = ", ".join(paper.source_categories)
    return f"{', '.join(parts)}. {ident} in {categories}." if categories else f"{', '.join(parts)}. {ident}."


def _breakdown_html(paper: Paper) -> str:
    if not paper.score_breakdown:
        return '<p class="pr-note">Not scored yet. Scores appear after the next refresh.</p>'
    rows = []
    for name, label in COMPONENT_LABELS.items():
        component = paper.score_breakdown["components"].get(name)
        if not component:
            continue
        rows.append(
            f"<tr><td>{label}</td><td>{theme.esc(component['detail'])}</td>"
            f"<td>{component['points']:.1f} / {component['max']:.0f}</td></tr>"
        )
    total = paper.score_breakdown["total"]
    rows.append(f"<tr><td>Total</td><td></td><td><strong>{total:.0f}</strong> / 100</td></tr>")
    return f'<table class="pr-breakdown">{"".join(rows)}</table>'


def render_detail(paper_id: int) -> None:
    ctx = get_context()
    week = this_week()
    with ctx.connect() as conn:
        paper = papers.get_paper(conn, paper_id)
        in_week = queue_repo.contains(conn, week, paper_id)
        week_count = len(queue_repo.week_paper_ids(conn, week))
        target = ctx.preferences(conn).weekly_target
    if paper is None:
        st.warning("This paper is no longer in your library.")
        return

    st.html(
        f'<h2 class="pr-detail-title">{theme.esc(theme.display_title(paper))}</h2>'
        f'<div class="pr-detail-meta">{theme.esc(", ".join(paper.authors) or "Authors not listed")}</div>'
        f'<div class="pr-detail-meta">{theme.esc(_meta_line(paper))}</div>'
    )
    st.html(theme.chips_html(paper, ctx.taxonomy, max_tags=6))

    with st.container(horizontal=True, key=f"act-links-{paper.id}", gap="small"):
        if paper.source_url:
            st.link_button("Open on arXiv", paper.source_url, icon=":material/open_in_new:", type="primary")
        if paper.pdf_url:
            st.link_button("PDF", paper.pdf_url, icon=":material/picture_as_pdf:")
        if paper.code_url:
            st.link_button("Code", paper.code_url, icon=":material/code:", help="Listed by the authors on arXiv")
        if paper.project_url:
            st.link_button("Project page", paper.project_url, icon=":material/language:", help="Listed by the authors on arXiv")

    status_key = f"detail-status-{paper.id}"
    st.session_state[status_key] = paper.status.value
    with st.container(horizontal=True, vertical_alignment="bottom", key=f"act-status-{paper.id}", gap="medium"):
        st.segmented_control(
            "Status",
            options=list(STATUS_LABELS),
            format_func=STATUS_LABELS.get,
            key=status_key,
            required=True,
            on_change=_status_changed,
            args=(paper.id, status_key),
        )
        if in_week:
            st.button(
                "On this week's list",
                icon=":material/event_available:",
                key=f"act-week-{paper.id}",
                on_click=_remove_from_week,
                args=(paper.id,),
                help="Click to take it off this week's list",
            )
        else:
            st.button(
                f"Add to this week ({week_count} of {target})",
                icon=":material/playlist_add:",
                key=f"act-week-{paper.id}",
                on_click=_add_to_week,
                args=(paper.id,),
            )

    digest_tab, abstract_tab, score_tab, notes_tab = st.tabs(
        ["AI digest", "Abstract", "Why it ranks here", "Notes"],
        default="AI digest" if paper.digest else "Abstract",
        key=f"detail-tabs-{paper.id}",
    )
    with digest_tab:
        _digest_tab(ctx, paper)
    with abstract_tab:
        st.html(
            '<p class="pr-note">The authors\' own abstract and comment, as listed on arXiv.</p>'
            f'<div class="pr-abstract">{theme.esc(tex_to_text(paper.abstract)) or "No abstract available."}</div>'
        )
        if paper.author_comment:
            st.html(f'<p class="pr-note">Author comment: {theme.esc(paper.author_comment)}</p>')
        if paper.journal_ref:
            st.html(f'<p class="pr-note">Journal reference: {theme.esc(paper.journal_ref)}</p>')
    with score_tab:
        st.html(_breakdown_html(paper))
        if paper.needs_review:
            st.html('<p class="pr-note">The topic is a best guess: the text matched several topics about equally.</p>')
        if paper.dedup_note:
            st.html(f'<p class="pr-note">{theme.esc(paper.dedup_note)}</p>')
    with notes_tab:
        _notes(paper)


def _digest_tab(ctx, paper: Paper) -> None:
    with ctx.connect() as conn:
        prefs = ctx.preferences(conn)
        record = digest_repo.best_for_paper(conn, paper.id)
    ready = digest_view.engine_ready(prefs)
    if paper.digest:
        digest_view.render_digest(paper, record)
    elif ready:
        st.html(
            '<p class="pr-note">A digest is a two-minute screening summary written by Claude: problem, idea, '
            "method, evidence, limitations and what to check when you read. It always says whether it was "
            "written from the abstract or the full paper.</p>"
        )
    else:
        st.info(digest_view.not_ready_message(prefs), title="AI digests are off", icon=":material/key:")
        return

    # None: no usable digest yet (or only one for an older version of the paper).
    level = paper.digest_source_level.value if paper.digest and not paper.digest_is_stale else None
    run: tuple[str, bool] | None = None  # (mode, force)
    with st.container(horizontal=True, gap="small", key=f"act-digest-{paper.id}"):
        if level in (None, "abstract_only") and st.button(
            "Read the full paper",
            icon=":material/article:",
            type="primary",
            key=f"dg-full-{paper.id}",
            help=f"Downloads the PDF from arXiv and summarizes all of it; {digest_view.cost_hint(ctx, prefs, 'full_text')}.",
            disabled=not paper.pdf_url,
        ):
            run = ("full_text", False)
        if level is None and st.button(
            "Summarize the abstract",
            icon=":material/short_text:",
            key=f"dg-abs-{paper.id}",
            help=f"A quick screening digest from the abstract only; {digest_view.cost_hint(ctx, prefs, 'abstract')}.",
        ):
            run = ("abstract", False)
        if level is not None and st.button(
            "Regenerate",
            icon=":material/refresh:",
            key=f"dg-redo-{paper.id}",
            help="Write this digest again from the same source, for example after changing the model in Settings.",
        ):
            run = ("abstract" if level == "abstract_only" else "full_text", True)
    if run and digest_view.run_one(paper.id, run[0], force=run[1]):
        st.rerun(scope="fragment")


def _notes(paper: Paper) -> None:
    notes_key = f"notes-{paper.id}"
    if notes_key not in st.session_state:
        st.session_state[notes_key] = paper.personal_notes
    st.text_area(
        "Your notes",
        key=notes_key,
        height=150,
        label_visibility="collapsed",
        placeholder="The key idea, the figure that matters, what you'd try on your own robots…",
        on_change=_save_notes,  # autosaves when the box loses focus
        args=(paper.id, notes_key),
    )
    with st.container(horizontal=True, vertical_alignment="center", key=f"act-notes-{paper.id}"):
        st.button("Save notes", key=f"act-save-{paper.id}", on_click=_save_notes, args=(paper.id, notes_key))
        if paper.notes_updated_at:
            st.caption(f"Saved {relative_time(paper.notes_updated_at, now())}")
        else:
            st.caption("Notes also save on their own when you click outside the box.")
