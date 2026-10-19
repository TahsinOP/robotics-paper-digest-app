"""Visual system: tokens, global CSS and small HTML builders.

The palette, serif and motion follow Tahsin's website (TahsinOP.github.io): plum #8a1c7c on
white in light mode, orchid #e58ad9 on near-black in dark mode, Source Serif 4 for titles and
reading text, a single "rise" entrance with the site's easing. The interface is set in
IBM Plex Sans. Shortlisted papers get a plum highlighter mark across their title.

Every string that comes from a paper is escaped before it reaches HTML.
"""

from __future__ import annotations

import html
import re
from datetime import datetime

import streamlit as st

from paper_radar.models import Paper
from paper_radar.taxonomy import Taxonomy
from paper_radar.text import tex_to_text

# Mirrors the custom properties in TahsinOP.github.io/assets/css/main.css.
TOKENS = {
    "light": {
        "paper": "#ffffff",
        "paper-2": "#f5f6f7",
        "ink": "#16181d",
        "ink-2": "#3a3f47",
        "graphite": "#5b616b",
        "rule": "#e3e5e8",
        "rule-2": "#cfd3d8",
        "accent": "#8a1c7c",
        "accent-2": "#6f1463",
        "accent-bg": "rgba(138, 28, 124, 0.07)",
        "mark": "rgba(138, 28, 124, 0.13)",
    },
    "dark": {
        "paper": "#17181a",
        "paper-2": "#202226",
        "ink": "#e8e9eb",
        "ink-2": "#cdd0d5",
        "graphite": "#a3a8b0",
        "rule": "#2c2e33",
        "rule-2": "#3a3d44",
        "accent": "#e58ad9",
        "accent-2": "#f0abe7",
        "accent-bg": "rgba(229, 138, 217, 0.10)",
        "mark": "rgba(229, 138, 217, 0.20)",
    },
}
# Topic chips tint their text, fill and border from one hue, as the website's .topic chips do.
TOPIC_MIX = {
    "light": {"text": "68%, #000", "fill": "10%", "border": "24%"},
    "dark": {"text": "45%, #fff", "fill": "16%", "border": "34%"},
}

# One hue per interest group.
GROUP_COLORS = {
    "slam": "#2F6FB0",
    "manipulation": "#C4571B",
    "learning": "#6D4AC7",
    "world_models": "#0F8A7E",
    "planning": "#5E8A1E",
    "locomotion": "#C0364A",
    "soft": "#B7860B",
    "other": "#64748B",
}

_CSS = """
:root { %(vars)s --pr-serif: 'Source Serif 4', Georgia, serif; --pr-ease: cubic-bezier(.2, .7, .2, 1); }

[data-testid="stMainBlockContainer"] { max-width: 50rem; padding-top: 3rem; padding-bottom: 6rem; }
h1 { letter-spacing: -0.012em; }
::selection { background: var(--pr-mark); }

/* Navigation: the active page gets the accent and an underline, as on the website */
[data-testid="stTopNavLink"] { background: transparent !important; transition: color .2s ease; }
[data-testid="stTopNavLink"] span { transition: color .2s ease; }
[data-testid="stTopNavLink"]:hover span { color: var(--pr-ink) !important; }
[data-testid="stTopNavLink"][aria-current="page"] { position: relative; }
[data-testid="stTopNavLink"][aria-current="page"] span { color: var(--pr-accent) !important; }
[data-testid="stTopNavLink"][aria-current="page"]::after {
  content: ""; position: absolute; left: 0.5rem; right: 0.5rem; bottom: -2px; height: 2px; border-radius: 2px;
  background: var(--pr-accent);
}

/* Solid accent buttons use the page colour for text, as the website's .btn--solid does
   (white on plum in light mode, near-black on orchid in dark mode). */
[data-testid="stBaseButton-primary"], [data-testid="stBaseLinkButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stBaseButton-primary"] *, [data-testid="stBaseLinkButton-primary"] *,
[data-testid="stBaseButton-primaryFormSubmit"] * { color: var(--pr-paper) !important; }

/* Page header */
.pr-lede { color: var(--pr-graphite); font-size: 1rem; line-height: 1.55; margin: -0.4rem 0 0.2rem; max-width: 40rem; }
.pr-lede strong { color: var(--pr-ink); font-weight: 600; }

/* Ranked list entries */
[class*="st-key-entry-"] { border-bottom: 1px solid var(--pr-rule); padding: 1.15rem 0 1.25rem; gap: 0.5rem; }
[class*="st-key-entry-"] [data-testid="stVerticalBlock"] { gap: 0.45rem; }
.pr-gutter { text-align: right; padding-top: 0.15rem; }
.pr-score { font-weight: 600; font-size: 1.65rem; line-height: 1; font-variant-numeric: tabular-nums; color: var(--pr-ink); }
.pr-score.pr-high { color: var(--pr-accent); }
.pr-date { font-size: 0.76rem; color: var(--pr-graphite); margin-top: 0.4rem; white-space: nowrap; }

/* Paper titles are buttons that open the detail view */
[class*="st-key-ptitle-"] button { justify-content: flex-start; text-align: left; padding: 0; min-height: 0; border: none; background: none; }
[class*="st-key-ptitle-"] button [data-testid="stMarkdownContainer"] { white-space: normal; overflow: visible; text-overflow: clip; text-align: left; }
[class*="st-key-ptitle-"] button p {
  font-family: var(--pr-serif); font-size: 1.3rem; line-height: 1.3; font-weight: 600; letter-spacing: -0.01em;
  color: var(--pr-ink); margin: 0; white-space: normal; text-align: left; transition: color .2s ease;
}
[class*="st-key-ptitle-"] button:hover p { color: var(--pr-accent); }
[class*="st-key-ptitle-mark-"] button p {
  display: inline; padding: 0 0.12em;
  background-image: linear-gradient(to bottom, transparent 12%%, var(--pr-mark) 12%%, var(--pr-mark) 92%%, transparent 92%%);
  background-repeat: no-repeat; background-size: 100%% 100%%;
  -webkit-box-decoration-break: clone; box-decoration-break: clone;
  animation: pr-swipe .55s var(--pr-ease) both;
}
[class*="st-key-ptitle-done-"] button p { color: var(--pr-graphite); }

.pr-authors { font-size: 0.88rem; color: var(--pr-graphite); line-height: 1.45; }
.pr-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 0.1rem; }
.pr-chip { font-size: 0.75rem; line-height: 1.55; padding: 1px 9px; white-space: nowrap; border: 1px solid transparent; }
.pr-topic {
  border-radius: 100px; padding: 1px 10px; font-weight: 500;
  color: color-mix(in srgb, var(--c) %(mix_text)s);
  background: color-mix(in srgb, var(--c) %(mix_fill)s, var(--pr-paper));
  border-color: color-mix(in srgb, var(--c) %(mix_border)s, var(--pr-paper));
}
.pr-topic.pr-uncertain { border-style: dashed; }
.pr-tag { border-radius: 6px; border-color: var(--pr-rule-2); color: var(--pr-ink-2); }
.pr-venue { border-radius: 6px; background: var(--pr-accent-bg); color: var(--pr-accent); font-weight: 600; }
.pr-state { border-radius: 100px; color: var(--pr-accent); border-color: color-mix(in srgb, var(--pr-accent) 40%%, var(--pr-paper)); }
.pr-why { font-size: 0.87rem; line-height: 1.5; color: var(--pr-graphite); margin: 0.15rem 0 0; max-width: 40rem; }

/* Quiet text actions under each entry; the shortlisted state takes the accent colour */
[class*="st-key-act-"] button, [class*="st-key-act-"] a { min-height: 1.9rem; }
[class*="st-key-act-"] button p, [class*="st-key-act-"] a p { font-size: 0.86rem; }
[class*="st-key-act-"] [data-testid="stBaseButton-tertiary"],
[class*="st-key-act-"] [data-testid="stBaseLinkButton-tertiary"] { color: var(--pr-graphite); padding: 0.1rem 0.15rem; transition: color .2s ease; }
[class*="st-key-act-"] [data-testid="stBaseButton-tertiary"]:hover,
[class*="st-key-act-"] [data-testid="stBaseLinkButton-tertiary"]:hover { color: var(--pr-accent); }
[class*="st-key-act-sl-on-"] [data-testid="stBaseButton-tertiary"] { color: var(--pr-accent); }
[class*="st-key-act-"] { column-gap: 1.1rem; }

/* AI digests: always marked as AI, set apart from the authors' own text */
.pr-takeaway { font-family: var(--pr-serif); font-size: 0.98rem; line-height: 1.5; color: var(--pr-ink-2); margin: 0.2rem 0 0; max-width: 42rem; }
.pr-ai {
  display: inline-block; margin-right: 0.45rem; padding: 0 0.4rem; border-radius: 4px; vertical-align: 0.1em;
  font: 600 0.66rem/1.55 var(--pr-sans, inherit); letter-spacing: 0.03em; background: var(--pr-accent-bg); color: var(--pr-accent);
}
.pr-digest { max-width: 44rem; }
.pr-digest section { margin: 0.9rem 0 0; }
.pr-digest h4 { font-family: var(--pr-serif); font-size: 1.02rem; font-weight: 600; margin: 0 0 0.3rem; color: var(--pr-ink); }
.pr-digest p, .pr-digest li { font-size: 0.95rem; line-height: 1.6; color: var(--pr-ink); }
.pr-digest p { margin: 0; }
.pr-digest ol, .pr-digest ul { margin: 0; padding-left: 1.25rem; }
.pr-digest li { margin: 0.15rem 0; }
.pr-dg-takeaway { font-family: var(--pr-serif); font-size: 1.22rem !important; line-height: 1.4 !important; font-weight: 500; margin: 0.6rem 0 0 !important; }
.pr-kind { display: inline-block; margin-right: 0.4rem; padding: 0 0.45rem; border-radius: 100px; font-size: 0.72rem; line-height: 1.6; border: 1px solid var(--pr-rule-2); color: var(--pr-ink-2); }
.pr-kind-interpretation { border-style: dashed; color: var(--pr-graphite); }
.pr-where { font-size: 0.8rem; color: var(--pr-graphite); }
.pr-check { list-style: none; padding-left: 0.1rem !important; }
.pr-check li::before { content: "☐"; margin-right: 0.5rem; color: var(--pr-accent); }

/* Paper detail */
.pr-detail-title { font-family: var(--pr-serif); font-size: 1.75rem; line-height: 1.22; font-weight: 600; letter-spacing: -0.012em; margin: 0 0 0.6rem; color: var(--pr-ink); }
.pr-detail-meta { font-size: 0.9rem; color: var(--pr-graphite); line-height: 1.5; }
.pr-abstract { font-family: var(--pr-serif); font-size: 1.08rem; line-height: 1.65; color: var(--pr-ink); max-width: 42rem; }
.pr-section { position: relative; font-family: var(--pr-serif); font-size: 1.12rem; font-weight: 600; margin: 0.8rem 0 0.2rem; padding-bottom: 0.3rem; color: var(--pr-ink); }
.pr-section::after { content: ""; position: absolute; left: 0; bottom: 0; width: 32px; height: 2px; background: var(--pr-accent); }
.pr-note { font-size: 0.85rem; color: var(--pr-graphite); line-height: 1.5; }
.pr-breakdown { width: 100%%; border-collapse: collapse; font-size: 0.88rem; }
.pr-breakdown td { padding: 0.4rem 0; border-bottom: 1px solid var(--pr-rule); vertical-align: top; }
.pr-breakdown td:first-child { width: 9rem; color: var(--pr-ink); font-weight: 500; }
.pr-breakdown td:nth-child(2) { color: var(--pr-graphite); padding-right: 1rem; }
.pr-breakdown td:last-child { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }

/* This week */
.pr-qnum { font-family: var(--pr-serif); font-size: 1.9rem; line-height: 1; color: var(--pr-graphite); text-align: right; padding-top: 0.1rem; }
.pr-slot { border: 1.5px dashed var(--pr-rule-2); border-radius: 10px; padding: 0.95rem 1.1rem; color: var(--pr-graphite); font-size: 0.92rem; }
[class*="st-key-qrow-"] { border-bottom: 1px solid var(--pr-rule); padding: 0.9rem 0 1rem; }
[class*="st-key-qrow-"] [data-testid="stVerticalBlock"] { gap: 0.4rem; }
[class*="st-key-cand-"] { border-bottom: 1px solid var(--pr-rule); padding: 0.7rem 0 0.8rem; }

/* Motion: one entrance with the website's easing, plus feedback for actions */
@keyframes pr-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes pr-swipe { from { background-size: 0%% 100%%; } to { background-size: 100%% 100%%; } }
[data-testid="stMainBlockContainer"] h1, .pr-lede { animation: pr-rise .6s var(--pr-ease) both; }
.pr-lede { animation-delay: .06s; }
[class*="st-key-entry-"], [class*="st-key-qrow-"], [class*="st-key-cand-"] { animation: pr-rise .5s var(--pr-ease) both; animation-delay: .1s; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}

@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-top: 4.5rem; }
  .pr-gutter { text-align: left; display: flex; gap: 0.75rem; align-items: baseline; }
  .pr-date { margin-top: 0; }
  .pr-qnum { text-align: left; }
}
"""


def apply() -> None:
    """Inject the stylesheet with the colour tokens for the active light/dark theme."""
    theme = getattr(st.context, "theme", None)
    mode = "dark" if getattr(theme, "type", None) == "dark" else "light"
    variables = " ".join(f"--pr-{name}: {value};" for name, value in TOKENS[mode].items())
    mix = TOPIC_MIX[mode]
    css = _CSS % {"vars": variables, "mix_text": mix["text"], "mix_fill": mix["fill"], "mix_border": mix["border"]}
    st.html(f"<style>{css}</style>")


def esc(value: str | None) -> str:
    return html.escape(value or "", quote=True)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~<>$:])")


def md_escape(value: str) -> str:
    """Escape Markdown so untrusted text renders literally inside widget labels."""
    return _MD_SPECIAL.sub(r"\\\1", value)


def display_title(paper: Paper) -> str:
    return tex_to_text(paper.title)


def short_date(moment: datetime | None) -> str:
    if moment is None:
        return "Date unknown"
    local = moment.astimezone()
    fmt = "%-d %b" if local.year == datetime.now().astimezone().year else "%-d %b %Y"
    return local.strftime(fmt)


def long_date(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%-d %B %Y") if moment else "unknown date"


def author_line(authors: tuple[str, ...], limit: int = 4) -> str:
    if not authors:
        return "Authors not listed"
    if len(authors) <= limit:
        return ", ".join(authors)
    return f"{', '.join(authors[: limit - 1])} and {len(authors) - limit + 1} others"


def topic_chip(paper: Paper, taxonomy: Taxonomy) -> str:
    category = taxonomy.category(paper.primary_category)
    color = GROUP_COLORS.get(category.group, GROUP_COLORS["other"])
    classes = "pr-chip pr-topic" + (" pr-uncertain" if paper.needs_review else "")
    hint = "Topic assignment is uncertain" if paper.needs_review else category.name
    return f'<span class="{classes}" style="--c:{color}" title="{esc(hint)}">{esc(category.short)}</span>'


def chips_html(paper: Paper, taxonomy: Taxonomy, *, max_tags: int = 3, extra: tuple[str, ...] = ()) -> str:
    parts = [topic_chip(paper, taxonomy)]
    parts += [f'<span class="pr-chip pr-tag">{esc(tag)}</span>' for tag in paper.secondary_tags[:max_tags]]
    if paper.venue_name:
        parts.append(f'<span class="pr-chip pr-venue" title="Stated in the authors\' arXiv comment">{esc(paper.venue_name)}</span>')
    parts += [f'<span class="pr-chip pr-state">{esc(label)}</span>' for label in extra]
    return f'<div class="pr-chips">{"".join(parts)}</div>'


def gutter_html(paper: Paper) -> str:
    score = paper.relevance_score
    shown = "–" if score is None else f"{score:.0f}"
    high = " pr-high" if score is not None and score >= 70 else ""
    return (
        '<div class="pr-gutter">'
        f'<div class="pr-score{high}" title="Relevance to your interests, out of 100">{shown}</div>'
        f'<div class="pr-date">{esc(short_date(paper.published_at))}</div>'
        "</div>"
    )
