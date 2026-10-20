"""Settings: interests, focus keywords, refresh window and the scoring formula."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import streamlit as st

from paper_radar import cli
from paper_radar.llm import credentials
from paper_radar.llm.anthropic_provider import AnthropicProvider
from paper_radar.llm.base import EFFORT_LEVELS, MODELS, LLMError
from paper_radar.llm.claude_code_provider import ClaudeCodeProvider
from paper_radar.llm.factory import ENGINES, engine_status
from paper_radar.sources.semantic_scholar import KEY_VAR as S2_KEY_VAR
from paper_radar.preferences import (
    ARXIV_CROSS_LIST_OPTIONS,
    FULLTEXT_CHARS_RANGE,
    Preferences,
    ScoreWeights,
    clean_keywords,
    reset_preferences,
    save_preferences,
)
from paper_radar.repositories import digests as digest_repo
from paper_radar.repositories import papers
from paper_radar.repositories.papers import PaperQuery
from paper_radar.services import library
from paper_radar.ui.context import AppContext, get_context, now


def _reset() -> None:
    ctx = get_context()
    with ctx.connect() as conn:
        reset_preferences(conn)
        library.rescore_all(conn, ctx.preferences(conn), ctx.taxonomy, now())
    st.session_state["settings_nonce"] = st.session_state.get("settings_nonce", 0) + 1
    st.session_state["settings_toast"] = "Restored the defaults from config/profile.yaml and re-ranked your library."


def page() -> None:
    ctx = get_context()
    tax = ctx.taxonomy
    with ctx.connect() as conn:
        prefs = ctx.preferences(conn)
        total = papers.count_papers(conn, PaperQuery())
    n = st.session_state.get("settings_nonce", 0)

    if message := st.session_state.pop("settings_toast", None):
        st.toast(message, icon=":material/check_circle:")

    st.title("Settings")
    st.html('<p class="pr-lede">Tune what counts as relevant. Saving re-ranks your whole library straight away.</p>')

    with st.form(f"settings-{n}", border=False):
        st.subheader("Interests")
        st.caption("How much each area matters to you. Only the relative sizes count: a topic at 20 ranks twice as high as one at 10.")
        weights = {}
        columns = st.columns(2, gap="large")
        for index, group in enumerate(tax.groups):
            with columns[index % 2]:
                weights[group.id] = st.slider(
                    group.name, 0, 40, int(prefs.interest_weights.get(group.id, group.default_weight)), key=f"w-{group.id}-{n}"
                )
        included = st.pills(
            "Topics shown in the feed",
            list(tax.category_ids),
            selection_mode="multi",
            default=list(prefs.included_categories),
            format_func=lambda c: tax.category(c).short,
            key=f"included-{n}",
            help="Papers in other topics are still collected and stay searchable in the Library.",
        )
        keywords = st.text_area(
            "Focus keywords, one per line",
            "\n".join(prefs.focus_keywords),
            height=260,
            key=f"keywords-{n}",
            help=(
                "A keyword in a title earns full credit, in an abstract half. Hyphens and plurals are flexible, "
                "and all-caps entries such as VIO match only in capitals."
            ),
        )

        st.subheader("Refreshing")
        lookback = st.slider("Look back this many days", 1, 60, prefs.lookback_days, key=f"lookback-{n}")
        max_results = st.number_input(
            "Most papers to fetch per refresh", 50, 5000, prefs.max_results, step=100, key=f"max-{n}",
            help="cs.RO alone sees roughly 70–80 new papers a day.",
        )
        cross = st.multiselect(
            "Also search these arXiv categories for robotics papers",
            list(ARXIV_CROSS_LIST_OPTIONS),
            default=list(prefs.cross_list_categories),
            key=f"cross-{n}",
        )

        st.subheader("Reading")
        target = st.number_input("Papers to read each week", 1, 8, prefs.weekly_target, key=f"target-{n}")

        with st.expander("Scoring formula"):
            st.caption(
                "Maximum points for each factor. They're scaled to add up to 100, so only the proportions matter."
            )
            sw = prefs.score_weights
            cols = st.columns(5)
            interest = cols[0].number_input("Topic match", 0.0, 100.0, float(sw.interest), step=5.0, key=f"si-{n}")
            kw = cols[1].number_input("Keywords", 0.0, 100.0, float(sw.keywords), step=5.0, key=f"sk-{n}")
            recency = cols[2].number_input("Recency", 0.0, 100.0, float(sw.recency), step=5.0, key=f"sr-{n}")
            resources = cols[3].number_input("Code/project", 0.0, 100.0, float(sw.resources), step=5.0, key=f"sc-{n}")
            venue = cols[4].number_input("Venue", 0.0, 100.0, float(sw.venue), step=5.0, key=f"sv-{n}")
            half_life = st.slider(
                "Recency half-life in days", 1, 60, int(prefs.recency_half_life_days), key=f"hl-{n}",
                help="A paper this many days old gets half the recency points of one submitted today.",
            )

        submitted = st.form_submit_button("Save and re-rank", type="primary")

    if submitted:
        if not included:
            st.error("Choose at least one topic to show in the feed.")
            return
        updated = Preferences(
            lookback_days=lookback,
            max_results=int(max_results),
            cross_list_categories=tuple(cross),
            interest_weights=weights,
            included_categories=tuple(included),
            focus_keywords=clean_keywords(keywords.splitlines()),
            recency_half_life_days=float(half_life),
            score_weights=ScoreWeights(interest, kw, recency, resources, venue),
            weekly_target=int(target),
        )
        with ctx.connect() as conn:
            saved = save_preferences(conn, updated, tax, now())
            library.rescore_all(conn, saved, tax, now())
        st.session_state["settings_toast"] = f"Saved. Re-ranked {total:,} papers."
        st.rerun()

    st.space("small")
    st.button("Restore defaults", type="tertiary", icon=":material/restart_alt:", on_click=_reset,
              help="Go back to the values in profile.yaml")

    st.space("medium")
    _ai_section(ctx, prefs, n)

    st.space("medium")
    st.caption(f"Your library lives in {ctx.config.db_path}. Back it up by copying that file.")


def _save_digest_setting(field: str, key: str) -> None:
    ctx = get_context()
    with ctx.connect() as conn:
        current = ctx.preferences(conn)
        save_preferences(conn, replace(current, **{field: st.session_state[key]}), ctx.taxonomy, now())
    st.toast("Saved.", icon=":material/check_circle:")


def _save_secret(var: str, input_key: str, success: str) -> None:
    try:
        credentials.save_key(st.session_state.get(input_key, ""), var)
    except ValueError as exc:
        st.session_state["key_message"] = ("error", str(exc))
    else:
        st.session_state["key_message"] = ("success", success)
    st.session_state[input_key] = ""


def _remove_secret(var: str, message: str) -> None:
    credentials.remove_key(var)
    st.session_state["key_message"] = ("info", message)


def _connect_mcp() -> None:
    ok, message = cli.register_mcp()
    st.session_state["key_message"] = ("success" if ok else "error", message)
    st.session_state.pop("mcp_registered", None)


def _secret_form(form_key: str, label: str, var: str, *, replace_label: bool, help_text: str, success: str) -> None:
    with st.form(form_key, border=False, enter_to_submit=True):
        st.text_input(label, key=f"{form_key}-input", type="password", help=help_text)
        st.form_submit_button(
            "Replace key" if replace_label else "Save key",
            on_click=_save_secret,
            args=(var, f"{form_key}-input", success),
        )


def _ai_section(ctx: AppContext, prefs: Preferences, n: int) -> None:
    st.subheader("AI digests")
    st.caption(
        "Claude writes two-minute screening digests on request, from a paper's abstract or its full text, "
        "and every digest says which it used."
    )
    if kind_message := st.session_state.pop("key_message", None):
        kind, message = kind_message
        getattr(st, kind)(message)

    st.session_state.setdefault(f"set-digest-engine-{n}", prefs.digest_engine)
    st.segmented_control(
        "Engine",
        list(ENGINES),
        key=f"set-digest-engine-{n}",
        format_func=ENGINES.get,
        required=True,
        on_change=_save_digest_setting,
        args=("digest_engine", f"set-digest-engine-{n}"),
        help="Claude Code runs on your Claude subscription's usage limits. An API key is billed per use by Anthropic.",
    )
    status = engine_status(prefs.digest_engine)
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.badge("Ready" if status.ready else "Off", icon=":material/check_circle:" if status.ready else ":material/block:",
                 color="green" if status.ready else "gray")
        st.caption(status.description)
        st.space("stretch")
        if status.ready and st.button(
            "Test",
            type="tertiary",
            icon=":material/verified:",
            help="Claude Code: one tiny request on your plan. API key: a free token count.",
        ):
            try:
                if prefs.digest_engine == "api":
                    AnthropicProvider(prefs.digest_model).check()
                    st.success(f"The key works with {MODELS[prefs.digest_model].label}.")
                else:
                    served = ClaudeCodeProvider(prefs.digest_model).check()
                    st.success(f"Claude Code answered using {MODELS.get(served).label if served in MODELS else served}.")
            except LLMError as exc:
                st.error(str(exc))

    if prefs.digest_engine == "api":
        key_saved = credentials.is_saved(credentials.KEY_VAR)
        if not status.ready or key_saved:
            _secret_form(
                "api-key", "Claude API key", credentials.KEY_VAR, replace_label=key_saved,
                help_text="Create one at console.anthropic.com. It's stored in your user config folder, readable only "
                "by you, and never in the paper database.",
                success="Key saved. Digests are on.",
            )
        if key_saved:
            st.button("Remove saved key", type="tertiary", icon=":material/delete:", on_click=_remove_secret,
                      args=(credentials.KEY_VAR, "Saved key removed."))
    else:
        st.caption(
            "Claude Code runs with no tools and sees only the paper text the app passes in. "
            "Each digest counts toward your plan's usage limits, like any Claude Code request."
        )

    st.selectbox(
        "Model",
        list(MODELS),
        index=list(MODELS).index(prefs.digest_model),
        format_func=lambda m: (
            f"{MODELS[m].label}. {MODELS[m].note}" if prefs.digest_engine != "api"
            else f"{MODELS[m].label}: ${MODELS[m].input_per_mtok:g} / ${MODELS[m].output_per_mtok:g} per million tokens. {MODELS[m].note}"
        ),
        key=f"set-digest-model-{n}",
        on_change=_save_digest_setting,
        args=("digest_model", f"set-digest-model-{n}"),
    )
    st.session_state.setdefault(f"set-digest-effort-{n}", prefs.digest_effort)
    st.segmented_control(
        "Effort",
        list(EFFORT_LEVELS),
        key=f"set-digest-effort-{n}",
        required=True,
        on_change=_save_digest_setting,
        args=("digest_effort", f"set-digest-effort-{n}"),
        help="How hard the model thinks. Medium is Anthropic's recommended starting point; higher levels take longer "
        "and use more of your plan or budget.",
    )
    st.number_input(
        "Read at most this many characters of a full paper",
        FULLTEXT_CHARS_RANGE[0],
        FULLTEXT_CHARS_RANGE[1],
        prefs.fulltext_max_chars,
        step=10_000,
        key=f"set-fulltext-chars-{n}",
        on_change=_save_digest_setting,
        args=("fulltext_max_chars", f"set-fulltext-chars-{n}"),
        help="About four characters per token. Longer papers are cut at a page boundary and the digest says so.",
    )
    month_start = datetime.now().astimezone().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with ctx.connect() as conn:
        usage = digest_repo.usage_since(conn, month_start)
    if usage.calls:
        failed = f", {usage.failed} failed" if usage.failed else ""
        spend = f", about ${usage.cost_usd:.2f} on the API (estimated)" if usage.cost_usd else ""
        st.caption(f"This month: {usage.digests} digests from {usage.calls} Claude calls{failed}{spend}.")

    st.space("small")
    st.markdown("**Your Claude Code sessions**")
    st.caption(
        "Connect Paper Radar to Claude Code (MCP) and you can ask any Claude Code session to work with your "
        'library, using your skills: "digest this week\'s papers", "deep-read paper 12 with deepread", '
        '"most cited SLAM papers of 2023". Digests saved there appear here, checked the same way.'
    )
    if "mcp_registered" not in st.session_state:
        st.session_state["mcp_registered"] = cli.mcp_registered()
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        connected = st.session_state["mcp_registered"]
        st.badge("Connected" if connected else "Not connected", icon=":material/hub:", color="green" if connected else "gray")
        st.button("Reconnect" if connected else "Connect to Claude Code", icon=":material/link:", type="tertiary",
                  on_click=_connect_mcp, disabled=engine_status("claude_code").ready is False)

    st.space("medium")
    st.subheader("Citations")
    st.caption(
        "The Explore page ranks a year's papers by citations from Semantic Scholar, which merges arXiv and published "
        "versions. It works without a key but its shared limit is often busy; a free key from "
        "semanticscholar.org/product/api makes it reliable."
    )
    s2_saved = credentials.is_saved(S2_KEY_VAR)
    _secret_form(
        "s2-key", "Semantic Scholar API key (optional)", S2_KEY_VAR, replace_label=s2_saved,
        help_text="Stored in your user config folder next to the Claude key, readable only by you.",
        success="Semantic Scholar key saved.",
    )
    if s2_saved:
        st.button("Remove Semantic Scholar key", type="tertiary", icon=":material/delete:", on_click=_remove_secret,
                  args=(S2_KEY_VAR, "Semantic Scholar key removed."))
