"""This week: the three or four papers you've chosen, your shortlist, and how last week went."""

from __future__ import annotations

import streamlit as st

from paper_radar.models import Paper, Status
from paper_radar.repositories.reading_queue import QueueEntry
from paper_radar.services import library, reading_queue
from paper_radar.ui import digest_view, theme
from paper_radar.ui.components import title_button
from paper_radar.ui.context import AppContext, get_context, now, this_week

QUEUE_STATUS_LABELS = {Status.SHORTLISTED.value: "To read", Status.READING.value: "Reading", Status.READ.value: "Read"}
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}


def _status_changed(paper_id: int, key: str) -> None:
    with get_context().connect() as conn:
        library.change_status(conn, paper_id, Status(st.session_state[key]), now(), this_week())


def _move(paper_id: int, offset: int) -> None:
    with get_context().connect() as conn:
        reading_queue.move(conn, paper_id, this_week(), offset)


def _remove(paper_id: int) -> None:
    with get_context().connect() as conn:
        reading_queue.remove_from_week(conn, paper_id, this_week())


def _add(paper_id: int) -> None:
    with get_context().connect() as conn:
        reading_queue.add_to_week(conn, paper_id, this_week(), now())


def _carry_over() -> None:
    week = this_week()
    with get_context().connect() as conn:
        added = reading_queue.carry_over(conn, reading_queue.previous_week(week), week, now())
    st.session_state["queue_toast"] = f"Carried over {added} paper{'s' if added != 1 else ''} from last week."


def _queue_row(ctx: AppContext, entry: QueueEntry, count: int) -> None:
    paper = entry.paper
    with st.container(key=f"qrow-{paper.id}"):
        number, body = st.columns([0.08, 0.92], gap="small")
        number.html(f'<div class="pr-qnum">{entry.position}</div>')
        with body:
            title_button(paper, key_prefix="ptitle-q")
            extra = (f"Carried over from {entry.carried_from:%-d %b}",) if entry.carried_from else ()
            st.html(
                f'<div class="pr-authors">{theme.esc(theme.author_line(paper.authors))}</div>'
                + theme.chips_html(paper, ctx.taxonomy, extra=extra)
                + digest_view.takeaway_html(paper)
            )
            key = f"qstatus-{paper.id}"
            st.session_state[key] = paper.status.value if paper.status.value in QUEUE_STATUS_LABELS else Status.SHORTLISTED.value
            with st.container(horizontal=True, vertical_alignment="center", key=f"act-q-{paper.id}", gap="small"):
                st.segmented_control(
                    "Reading status", list(QUEUE_STATUS_LABELS), key=key, required=True,
                    format_func=QUEUE_STATUS_LABELS.get, label_visibility="collapsed",
                    on_change=_status_changed, args=(paper.id, key),
                )
                st.space("stretch")
                st.button(":material/arrow_upward:", key=f"act-up-{paper.id}", type="tertiary", help="Read earlier",
                          on_click=_move, args=(paper.id, -1), disabled=entry.position == 1)
                st.button(":material/arrow_downward:", key=f"act-down-{paper.id}", type="tertiary", help="Read later",
                          on_click=_move, args=(paper.id, 1), disabled=entry.position == count)
                st.button("Remove", key=f"act-rm-{paper.id}", type="tertiary", icon=":material/close:",
                          help="Take it off this week's list; it stays on your shortlist", on_click=_remove, args=(paper.id,))


def _candidate_row(ctx: AppContext, paper: Paper) -> None:
    with st.container(key=f"cand-{paper.id}"):
        gutter, body, action = st.columns([0.11, 0.65, 0.24], gap="small", vertical_alignment="top")
        gutter.html(theme.gutter_html(paper))
        with body:
            title_button(paper, key_prefix="ptitle-c")
            st.html(theme.chips_html(paper, ctx.taxonomy, max_tags=2))
        with action:
            st.button("Add to this week", key=f"act-add-{paper.id}", icon=":material/playlist_add:",
                      on_click=_add, args=(paper.id,), width="stretch")


def _lede(entries: list[QueueEntry], target: int) -> str:
    chosen = len(entries)
    read = sum(e.paper.status == Status.READ for e in entries)
    if chosen == 0:
        return f"Pick {NUMBER_WORDS.get(target, target)} papers to read this week. Start from your shortlist below."
    text = f"<strong>{chosen} of {target}</strong> chosen, {read} read so far."
    if chosen > target:
        text += f" That's more than your target of {target}; consider leaving one for next week."
    return text


def page() -> None:
    ctx = get_context()
    week = this_week()
    previous = reading_queue.previous_week(week)
    with ctx.connect() as conn:
        target = ctx.preferences(conn).weekly_target
        entries = reading_queue.entries(conn, week)
        candidates = reading_queue.candidates(conn, week)
        last_week = reading_queue.review_week(conn, previous)
        already_here = {e.paper.id for e in entries}

    if message := st.session_state.pop("queue_toast", None):
        st.toast(message, icon=":material/check_circle:")

    st.title(f"Week of {week:%-d %B}")
    st.html(f'<p class="pr-lede">{_lede(entries, target)}</p>')
    mix = reading_queue.topic_mix(entries, ctx.taxonomy)
    unread_in_full = [
        e.paper.id for e in entries
        if not (e.paper.digest and not e.paper.digest_is_stale and e.paper.digest_source_level.value != "abstract_only")
    ]
    if message := st.session_state.pop("digest_batch_done", None):
        st.toast(f"Digests: {message}.", icon=":material/check_circle:")
    with st.container(horizontal=True, vertical_alignment="center", key="act-week-batch"):
        if mix:
            st.caption("Topics: " + ", ".join(f"{name} ({count})" for name, count in mix))
        st.space("stretch")
        prepare = False
        if unread_in_full and digest_view.keys_ready():
            with ctx.connect() as conn:
                prefs = ctx.preferences(conn)
            prepare = st.button(
                f"Prepare full-text digests ({len(unread_in_full)})",
                icon=":material/auto_awesome:",
                type="tertiary",
                help=f"Reads each paper's PDF from arXiv and writes a digest, {digest_view.cost_hint(ctx, prefs, 'full_text')} each.",
            )
    if prepare:
        digest_view.run_batch(unread_in_full, "full_text")
        st.rerun()

    for entry in entries:
        _queue_row(ctx, entry, len(entries))
    for slot in range(len(entries) + 1, target + 1):
        number, body = st.columns([0.08, 0.92], gap="small")
        number.html(f'<div class="pr-qnum">{slot}</div>')
        body.html('<div class="pr-slot">Open slot. Add a paper from your shortlist below, or from a paper\'s details.</div>')

    st.space("medium")
    st.subheader("Shortlist")
    if candidates:
        st.caption("Shortlisted papers that aren't on this week's list, best match first.")
        for paper in candidates:
            _candidate_row(ctx, paper)
    else:
        st.caption("Nothing waiting. Shortlist papers in the feed and they'll appear here.")

    if last_week.total:
        st.space("medium")
        st.subheader("Last week")
        summary = (
            f"Week of {previous:%-d %B}: {last_week.read} of {last_week.total} read, "
            f"notes on {last_week.with_notes}."
        )
        pending = [pid for pid in last_week.unfinished if pid not in already_here]
        if pending:
            summary += f" {len(pending)} unfinished."
        st.html(f'<p class="pr-lede">{summary}</p>')
        if pending:
            st.button(
                f"Carry over {len(pending)} unfinished paper{'s' if len(pending) != 1 else ''}",
                icon=":material/redo:",
                on_click=_carry_over,
            )
