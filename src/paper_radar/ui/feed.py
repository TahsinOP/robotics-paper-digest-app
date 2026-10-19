"""Feed: recent robotics papers ranked for your interests, with filters and refresh."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import streamlit as st

from paper_radar.models import Status
from paper_radar.preferences import Preferences
from paper_radar.repositories import app_state, papers
from paper_radar.repositories.papers import PaperQuery
from paper_radar.services import ingestion, library
from paper_radar.text import relative_time
from paper_radar.ui import digest_view, theme
from paper_radar.ui.components import paper_entry
from paper_radar.ui.context import AppContext, get_context, now
from paper_radar.ui.paper_detail import STATUS_LABELS

PAGE_SIZE = 25
DEFAULT_STATUSES = [Status.NEW.value, Status.SHORTLISTED.value, Status.READING.value]
FILTER_DEFAULTS = {
    "feed_search": "",
    "feed_sort": "relevance",
    "feed_status": DEFAULT_STATUSES,
    "feed_topics": [],
    "feed_tags": [],
    "feed_links": False,
    "feed_venue": False,
}


def _reset_filters() -> None:
    for key, value in FILTER_DEFAULTS.items():
        st.session_state[key] = list(value) if isinstance(value, list) else value
    st.session_state.pop("feed_window", None)


def _window_options(prefs: Preferences) -> list[str]:
    days = sorted({3, 7, prefs.lookback_days})
    return [str(d) for d in days] + ["all"]


def _sidebar(ctx: AppContext, prefs: Preferences, tags: list[str]) -> PaperQuery:
    for key, value in FILTER_DEFAULTS.items():
        st.session_state.setdefault(key, list(value) if isinstance(value, list) else value)
    windows = _window_options(prefs)
    if st.session_state.get("feed_window") not in windows:
        st.session_state["feed_window"] = str(prefs.lookback_days)

    with st.sidebar:
        st.text_input("Search", key="feed_search", placeholder="Title, abstract or author", icon=":material/search:")
        st.segmented_control(
            "Sort by", ["relevance", "newest"], key="feed_sort", required=True,
            format_func={"relevance": "Relevance", "newest": "Newest"}.get,
        )
        st.pills(
            "Submitted in the last", windows, key="feed_window", required=True,
            format_func=lambda w: "Any time" if w == "all" else f"{w} days",
        )
        st.pills("Status", list(STATUS_LABELS), key="feed_status", selection_mode="multi", format_func=STATUS_LABELS.get)
        st.multiselect(
            "Topics", list(prefs.included_categories), key="feed_topics",
            format_func=lambda c: ctx.taxonomy.category(c).short, placeholder="All your topics",
        )
        st.multiselect("Tags", tags, key="feed_tags", placeholder="Any tag")
        st.toggle("Code or project page listed", key="feed_links")
        st.toggle("Accepted at a known venue", key="feed_venue")
        st.button("Reset filters", type="tertiary", icon=":material/restart_alt:", on_click=_reset_filters)

    window = st.session_state["feed_window"]
    return PaperQuery(
        search=st.session_state["feed_search"].strip(),
        sort=st.session_state["feed_sort"],
        statuses=tuple(Status(s) for s in st.session_state["feed_status"]),
        categories=tuple(st.session_state["feed_topics"]) or prefs.included_categories,
        tags=tuple(st.session_state["feed_tags"]),
        published_since=None if window == "all" else now() - timedelta(days=int(window)),
        with_links_only=st.session_state["feed_links"],
        venue_only=st.session_state["feed_venue"],
    )


def _refresh(ctx: AppContext, prefs: Preferences) -> None:
    with st.status("Contacting arXiv…", expanded=True) as status:
        st.write("arXiv asks for a three-second pause between requests, so a refresh takes about half a minute.")
        try:
            with ctx.connect() as conn:
                report = ingestion.refresh_papers(
                    conn,
                    ingestion.make_source(prefs, ctx.taxonomy, ctx.config),
                    prefs,
                    ctx.taxonomy,
                    progress=lambda message: status.update(label=message),
                )
        except Exception as exc:  # unexpected failure: stored papers are untouched (transaction rolled back)
            status.update(label="Refresh stopped", state="error")
            st.session_state["refresh_failure"] = f"{type(exc).__name__}: {exc}"
            return
        status.update(label=report.summary(), state="complete" if report.status == "ok" else "error")
    st.session_state["refresh_toast"] = report.summary()
    st.rerun()


def _header(ctx: AppContext, prefs: Preferences, in_window: int, last: app_state.RefreshRun | None) -> None:
    left, right = st.columns([0.72, 0.28], vertical_alignment="bottom")
    left.title("New in robotics")
    with right:
        clicked = st.button(
            "Refresh papers", icon=":material/refresh:", type="secondary" if last else "primary", width="stretch"
        )
    if last:
        lede = (
            f"<strong>{in_window:,}</strong> papers from the last {prefs.lookback_days} days, ranked for your "
            f"interests. Refreshed {relative_time(last.finished_at, now())}."
        )
    else:
        lede = "Nothing fetched yet."
    st.html(f'<p class="pr-lede">{lede}</p>')

    if message := st.session_state.pop("refresh_toast", None):
        st.toast(message, icon=":material/check_circle:")
    if failure := st.session_state.pop("refresh_failure", None):
        st.error(f"The refresh stopped unexpectedly, and nothing was changed. Details: {failure}")
    elif last and last.status != "ok":
        problems = " ".join(last.errors)
        verb = "failed" if last.status == "failed" else "was incomplete"
        st.warning(f"The last refresh {verb}. {problems} Papers you already have are unaffected; refresh again to retry.")
    if clicked:
        _refresh(ctx, prefs)


def page() -> None:
    ctx = get_context()
    with ctx.connect() as conn:
        prefs = ctx.preferences(conn)
        library.ensure_scores_current(conn, prefs, ctx.taxonomy, now())
        last = app_state.last_refresh(conn)
        in_window = papers.count_papers(
            conn,
            PaperQuery(published_since=now() - timedelta(days=prefs.lookback_days), categories=prefs.included_categories),
        )
        total = papers.count_papers(conn, PaperQuery())
        tags = papers.tags_in_use(conn)

    query = _sidebar(ctx, prefs, tags)
    _header(ctx, prefs, in_window, last)

    if total == 0:
        st.html(
            '<p class="pr-lede">Your feed is empty. <strong>Refresh papers</strong> pulls the last '
            f"{prefs.lookback_days} days of robotics papers from arXiv and ranks them using the interests "
            "on the Settings page.</p>"
        )
        return

    # Reset paging when the filters change (the query itself holds a moving timestamp, so compare inputs).
    signature = repr([st.session_state.get(key) for key in [*FILTER_DEFAULTS, "feed_window"]])
    if st.session_state.get("feed_signature") != signature:
        st.session_state["feed_signature"] = signature
        st.session_state["feed_limit"] = PAGE_SIZE
    limit = st.session_state.get("feed_limit", PAGE_SIZE)

    with ctx.connect() as conn:
        matching = papers.count_papers(conn, query)
        shown = papers.list_papers(conn, replace(query, limit=limit))

    if not shown:
        st.html('<p class="pr-lede">No papers match these filters. Widen the date range or reset the filters in the sidebar.</p>')
        return
    if message := st.session_state.pop("digest_batch_done", None):
        st.toast(f"Digests: {message}.", icon=":material/check_circle:")
    undigested = [p.id for p in shown if not p.digest or p.digest_is_stale][:10]
    with st.container(horizontal=True, vertical_alignment="center", key="act-feed-batch"):
        st.caption(f"Showing {len(shown):,} of {matching:,} matching papers")
        st.space("stretch")
        if undigested and digest_view.keys_ready():
            summarize = st.button(
                f"Summarize the first {len(undigested)} abstracts",
                icon=":material/auto_awesome:",
                type="tertiary",
                help=f"AI screening digests from the abstracts, {digest_view.cost_hint(ctx, prefs, 'abstract')} each.",
            )
        else:
            summarize = False
    if summarize:
        digest_view.run_batch(undigested, "abstract")
        st.rerun()
    for paper in shown:
        paper_entry(paper.id)
    if matching > len(shown):
        st.space("small")
        if st.button(f"Show {min(PAGE_SIZE, matching - len(shown))} more", icon=":material/expand_more:"):
            st.session_state["feed_limit"] = limit + PAGE_SIZE
            st.rerun()
