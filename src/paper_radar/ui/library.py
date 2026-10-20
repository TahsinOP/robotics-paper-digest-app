"""Library: every paper collected so far, searchable including your notes."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from paper_radar.models import Status
from paper_radar.repositories import papers
from paper_radar.repositories.papers import PaperQuery
from paper_radar.ui import theme
from paper_radar.ui.context import get_context
from paper_radar.ui.paper_detail import STATUS_LABELS, open_paper

ROW_LIMIT = 500


def page() -> None:
    ctx = get_context()
    with ctx.connect() as conn:
        stats = papers.library_stats(conn)
        years = papers.years_in_library(conn)

    st.html('<style>[data-testid="stMainBlockContainer"] { max-width: 72rem; }</style>')  # tables need room
    st.title("Library")
    read = stats.by_status.get(Status.READ.value, 0)
    st.html(
        f'<p class="pr-lede"><strong>{stats.total:,}</strong> papers collected. {read:,} read, '
        f"{stats.with_notes:,} with notes. Select a row to open the paper.</p>"
    )

    search_col, year_col, status_col, topic_col = st.columns([0.37, 0.17, 0.23, 0.23])
    search = search_col.text_input(
        "Search", key="lib_search", placeholder="Title, abstract, author or your notes", icon=":material/search:"
    )
    year = year_col.selectbox(
        "Year", [0, *years], key="lib_year", format_func=lambda y: "Any year" if y == 0 else str(y),
        help="Years that have papers in your library. Explore can bring in more.",
    )
    statuses = status_col.multiselect(
        "Status", list(STATUS_LABELS), key="lib_status", format_func=STATUS_LABELS.get, placeholder="Any status"
    )
    topics = topic_col.multiselect(
        "Topic", list(ctx.taxonomy.category_ids), key="lib_topics",
        format_func=lambda c: ctx.taxonomy.category(c).short, placeholder="Any topic",
    )
    query = PaperQuery(
        search=search.strip(),
        statuses=tuple(Status(s) for s in statuses),
        categories=tuple(topics),
        search_notes=True,
        sort="newest",
        limit=ROW_LIMIT,
        published_since=datetime(year, 1, 1, tzinfo=timezone.utc) if year else None,
        published_until=datetime(year + 1, 1, 1, tzinfo=timezone.utc) if year else None,
    )
    with ctx.connect() as conn:
        matching = papers.count_papers(conn, query)
        rows = papers.list_papers(conn, query)

    if not rows:
        st.caption("No papers match. Try a shorter search." if stats.total else "Your library is empty. Refresh the feed first.")
        return
    if matching > len(rows):
        st.caption(f"Showing the newest {len(rows):,} of {matching:,}. Narrow the search to see the rest.")

    data = [
        {
            "Title": theme.display_title(p),
            "Topic": ctx.taxonomy.category(p.primary_category).short,
            "Status": STATUS_LABELS[p.status.value],
            "Submitted": p.published_at.date() if p.published_at else None,
            "Score": p.relevance_score,
            "Citations": p.citation_count,
            "Digest": {"abstract_only": "Abstract", "partial_full_text": "Part of full text", "full_text": "Full text"}.get(
                p.digest_source_level.value, ""
            ),
            "Notes": bool(p.personal_notes.strip()),
        }
        for p in rows
    ]
    event = st.dataframe(
        data,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"lib_table_{st.session_state.get('dialog_nonce', 0)}",
        height=min(36 * len(data) + 40, 640),
        column_config={
            "Title": st.column_config.TextColumn(width=520),
            "Topic": st.column_config.TextColumn(width="medium"),
            "Status": st.column_config.TextColumn(width="small"),
            "Submitted": st.column_config.DateColumn(format="D MMM YYYY", width="medium"),
            "Score": st.column_config.NumberColumn(format="%d", width="small"),
            "Citations": st.column_config.NumberColumn(format="%d", width="small", help="From Semantic Scholar, when known"),
            "Digest": st.column_config.TextColumn("AI digest", width="small"),
            "Notes": st.column_config.CheckboxColumn(width="small"),
        },
        alt="Papers in your library",
    )
    selected = event.selection.rows if event and event.selection else []
    if selected:
        open_paper(rows[selected[0]].id)
