"""AI digest display and generation controls, shared by the detail dialog, feed and week pages."""

from __future__ import annotations

import streamlit as st

from paper_radar.llm.base import MODELS, LLMProvider, estimate_cost
from paper_radar.llm.factory import engine_status, make_provider
from paper_radar.models import Paper
from paper_radar.preferences import Preferences
from paper_radar.repositories import digests as digest_repo
from paper_radar.services.digest import DigestError, generate_digest, generate_many
from paper_radar.text import relative_time
from paper_radar.ui import theme
from paper_radar.ui.context import AppContext, get_context, now

LEVEL_LABELS = {
    "abstract_only": ("Abstract only", "orange", ":material/short_text:"),
    "partial_full_text": ("Part of the full text", "blue", ":material/description:"),
    "full_text": ("Full text", "green", ":material/article:"),
}
KIND_LABELS = {"paper_stated": "Stated by the authors", "interpretation": "Interpretation"}
ENGINE_PHRASES = {
    "claude_code": "via Claude Code on your plan",
    "claude_code_session": "in one of your Claude Code sessions",
    "anthropic": "via the Anthropic API",
}


def provider(prefs: Preferences) -> LLMProvider:
    return make_provider(prefs.digest_engine, prefs.digest_model)


def engine_ready(prefs: Preferences) -> bool:
    return engine_status(prefs.digest_engine).ready


def not_ready_message(prefs: Preferences) -> str:
    if prefs.digest_engine == "api":
        return "Add a Claude API key in Settings → AI digests, or switch the engine to Claude Code."
    return "Claude Code isn't installed. Install it, or switch Settings → AI digests to an API key."


def cost_hint(ctx: AppContext, prefs: Preferences, mode: str) -> str:
    """'about $0.06' for API digests, from past digests of this kind or typical token counts."""
    if prefs.digest_engine != "api":
        return "uses your Claude plan"
    level = "abstract_only" if mode == "abstract" else "full_text"
    with ctx.connect() as conn:
        average = digest_repo.average_cost(conn, level, prefs.digest_model)
    if average is None:
        tokens_in = 2_500 if mode == "abstract" else 2_500 + min(prefs.fulltext_max_chars // 4, 30_000)
        average = estimate_cost(prefs.digest_model, tokens_in, 3_000 if mode == "abstract" else 4_500)
    if average is None:
        return "cost unknown"
    return "less than $0.01" if average < 0.01 else f"about ${average:.2f}"


def takeaway_html(paper: Paper) -> str:
    if not paper.digest:
        return ""
    stale = " (earlier version)" if paper.digest_is_stale else ""
    level = LEVEL_LABELS.get(paper.digest_source_level.value, ("", "", ""))[0].lower()
    return (
        f'<p class="pr-takeaway"><span class="pr-ai" title="AI digest from the {theme.esc(level)}{stale}">AI</span>'
        f"{theme.esc(paper.digest['one_line_takeaway'])}</p>"
    )


def _list(items: list[str], css: str = "") -> str:
    return f'<ul class="{css}">' + "".join(f"<li>{theme.esc(i)}</li>" for i in items) + "</ul>" if items else ""


def _section(title: str, body: str) -> str:
    return f'<section><h4>{theme.esc(title)}</h4>{body}</section>' if body else ""


def render_digest(paper: Paper, record: digest_repo.DigestRecord | None) -> None:
    digest = paper.digest
    if not digest:
        return
    label, color, icon = LEVEL_LABELS[paper.digest_source_level.value]
    model = MODELS.get(paper.digest_model or "")
    model_name = model.label if model else "Claude"
    via = f" {ENGINE_PHRASES.get(record.engine, '')}" if record else ""
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.badge(label, icon=icon, color=color)
        when = f" {relative_time(paper.digest_created_at, now())}" if paper.digest_created_at else ""
        source = f", from the {record.source_note[0].lower()}{record.source_note[1:]}" if record else ""
        st.caption(f"Written{when} by {model_name}{via}{source}. AI-generated: check claims against the paper.")
    if paper.digest_is_stale:
        st.warning("arXiv has a newer version of this paper than the one this digest describes.", title="Digest is out of date")
    notes = digest.get("verification_notes") or []
    if notes:
        st.warning("\n".join(f"- {n}" for n in notes), title="Some automatic checks didn't pass")

    st.html(
        '<div class="pr-digest">'
        f'<p class="pr-dg-takeaway">{theme.esc(digest["one_line_takeaway"])}</p>'
        + _section("Problem", f"<p>{theme.esc(digest['problem'])}</p>")
        + _section("Core idea", f"<p>{theme.esc(digest['core_idea'])}</p>")
        + _section("Method", "<ol>" + "".join(f"<li>{theme.esc(s)}</li>" for s in digest["method_steps"]) + "</ol>" if digest["method_steps"] else "")
        + "</div>"
    )
    evidence = digest["experimental_evidence"]
    if evidence:
        st.html('<div class="pr-digest"><section><h4>Evidence</h4></section></div>')
        st.table(
            [
                {
                    "Setting": e["setting"],
                    "Robot or dataset": e["dataset_or_robot"],
                    "Baseline": e["baseline"],
                    "Result": e["metric_or_result"],
                    "Where": e["evidence_location"],
                }
                for e in evidence
            ],
            hide_index=True,
            border="horizontal",
            alt="Evidence reported in the inspected text",
        )
    limitations = "".join(
        f'<li><span class="pr-kind pr-kind-{theme.esc(lim["kind"])}">{KIND_LABELS.get(lim["kind"], lim["kind"])}</span>'
        f'{theme.esc(lim["text"])} <span class="pr-where">{theme.esc(lim["evidence_location"])}</span></li>'
        for lim in digest["limitations"]
    )
    st.html(
        '<div class="pr-digest">'
        + _section("What's new", _list(digest["contributions"]))
        + _section("Limitations and assumptions", f"<ul>{limitations}</ul>" if limitations else "")
        + _section("Why it may matter to you", f"<p>{theme.esc(digest['relevance_explanation'])}</p>")
        + _section("Reading checklist", _list(digest["manual_reading_checklist"], "pr-check"))
        + _section("Not reported in the inspected text", _list(digest["unknowns"]))
        + f'<p class="pr-note">The model rates its own faithfulness to the inspected text at {digest["confidence"]:.0%}.</p>'
        + "</div>"
    )


def run_one(paper_id: int, mode: str, *, force: bool = False) -> bool:
    """Generate a digest with live progress. Returns True on success."""
    ctx = get_context()
    with ctx.connect() as conn:
        prefs = ctx.preferences(conn)
    what = "the full paper" if mode == "full_text" else "the abstract"
    with st.status(f"Summarizing {what}…", expanded=False) as status:
        try:
            with ctx.connect() as conn:
                outcome = generate_digest(
                    conn, paper_id, provider=provider(prefs), prefs=prefs, taxonomy=ctx.taxonomy, mode=mode,
                    now=now(), force=force, progress=lambda message: status.update(label=message),
                )
        except DigestError as exc:
            status.update(label="The digest couldn't be written", state="error", expanded=True)
            st.write(str(exc))
            return False
        cost = outcome.record.cost_usd
        if outcome.cached:
            done = "Loaded the saved digest"
        elif cost:
            done = f"Digest written (cost about ${cost:.3f})"
        else:
            done = "Digest written on your Claude plan"
        status.update(label=done, state="complete")
    return True


def run_batch(paper_ids: list[int], mode: str) -> None:
    ctx = get_context()
    with ctx.connect() as conn:
        prefs = ctx.preferences(conn)
    with st.status(f"Summarizing {len(paper_ids)} papers…", expanded=True) as status:
        report = generate_many(
            ctx.connect, paper_ids, provider=provider(prefs), prefs=prefs, taxonomy=ctx.taxonomy, mode=mode, now=now,
            progress=lambda i, n, title: status.update(label=f"Paper {i} of {n}: {title}…"),
        )
        summary = f"{report.generated} written, {report.cached} already saved"
        if report.cost_usd:
            summary += f", about ${report.cost_usd:.2f}"
        if report.failed:
            status.update(label=f"{summary}. {len(report.failed)} failed.", state="error")
            for _, message in report.failed[:3]:
                st.write(message)
        else:
            status.update(label=summary + ".", state="complete")
    st.session_state["digest_batch_done"] = summary


