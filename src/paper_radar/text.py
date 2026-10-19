"""Text helpers shared by normalisation, classification and the UI.

arXiv titles and abstracts contain light LaTeX ($\\pi_0$, \\textit{...}); `tex_to_text`
turns the common cases into readable Unicode and leaves anything unknown untouched.
`match_text` and `compile_phrase` give classification and keyword matching one consistent
view of the text (Unicode-normalised, unified hyphens, US spelling).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ", "kappa": "κ", "lambda": "λ",
    "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ",
    "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
    "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_SYMBOLS = {
    "times": "×", "cdot": "·", "sim": "~", "approx": "≈", "leq": "≤", "le": "≤", "geq": "≥",
    "ge": "≥", "neq": "≠", "infty": "∞", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "pm": "±", "circ": "°", "degree": "°", "ell": "ℓ", "partial": "∂",
    "nabla": "∇", "in": "∈", "ldots": "…", "dots": "…", "quad": " ", "qquad": " ",
}
_FORMAT_COMMANDS = (
    "textbf|textit|textrm|textsf|texttt|textsc|emph|mathrm|mathbf|mathit|mathcal|mathbb|"
    "mathsf|mathtt|boldsymbol|operatorname|text|bm|hat|bar|tilde|vec|underline|overline"
)
_ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303"}
_SUB = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")
_SUP = str.maketrans("0123456789+-n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ")
_HYPHENS = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")


def collapse_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def tex_to_text(value: str | None) -> str:
    """Render the light LaTeX used in arXiv metadata as plain Unicode text."""
    if not value:
        return ""
    s = value.replace("\\$", "\x00")
    s = re.sub(r"\\([%&_#{}])", r"\1", s)
    s = re.sub(
        r"\\([\"'`^~])\{?([A-Za-z])\}?",
        lambda m: unicodedata.normalize("NFC", m.group(2) + _ACCENTS[m.group(1)]),
        s,
    )
    for _ in range(3):  # nested formatting such as \mathbf{\hat{x}}
        s = re.sub(rf"\\(?:{_FORMAT_COMMANDS})\s*\{{([^{{}}]*)\}}", r"\1", s)

    def command(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _GREEK:
            return _GREEK[name]
        if name in _SYMBOLS:
            return _SYMBOLS[name]
        return match.group(0)

    s = re.sub(r"\\([A-Za-z]+)\s?", command, s)
    s = re.sub(r"\^\{?°\}?", "°", s)
    s = re.sub(r"_\{([0-9+\-]+)\}|_([0-9])(?![0-9])", lambda m: (m.group(1) or m.group(2)).translate(_SUB), s)
    s = re.sub(r"\^\{([0-9+\-n]+)\}|\^([0-9n])(?![0-9])", lambda m: (m.group(1) or m.group(2)).translate(_SUP), s)
    s = s.replace("$", "").replace("\x00", "$")
    s = re.sub(r"(?<!\\)\{([^{}]*)\}", r"\1", s)
    s = s.replace("---", "—").replace("--", "–")
    return collapse_whitespace(s)


# UK -> US spelling, applied identically to papers and to phrases so either spelling matches.
_UK_SUFFIX = re.compile(r"\b([A-Za-z]*[a-z])is(ation|ations|e|es|ed|ing|er|ers)\b")
_UK_WORDS = {
    "behaviour": "behavior", "behaviours": "behaviors", "behavioural": "behavioral",
    "colour": "color", "colours": "colors", "modelling": "modeling", "modelled": "modeled",
    "labelling": "labeling", "labelled": "labeled", "travelling": "traveling",
    "centre": "center", "centres": "centers", "metre": "meter", "metres": "meters",
    "manoeuvre": "maneuver", "manoeuvres": "maneuvers", "analyse": "analyze",
    "analysed": "analyzed", "grey": "gray", "aluminium": "aluminum", "tyre": "tire",
}
_UK_WORD_RE = re.compile(r"\b(" + "|".join(_UK_WORDS) + r")\b", re.IGNORECASE)


def _us_spelling(s: str) -> str:
    s = _UK_SUFFIX.sub(lambda m: f"{m.group(1)}iz{m.group(2)}", s)

    def word(m: re.Match[str]) -> str:
        replacement = _UK_WORDS[m.group(0).lower()]
        return replacement.capitalize() if m.group(0)[0].isupper() else replacement

    return _UK_WORD_RE.sub(word, s)


def match_text(value: str | None) -> str:
    """Canonical text for matching: Unicode-folded, unified hyphens, US spelling, case kept."""
    s = unicodedata.normalize("NFKC", tex_to_text(value)).translate(_HYPHENS)
    return _us_spelling(s)


def is_acronym(phrase: str) -> bool:
    """Phrases with letters but no lowercase letters (SLAM, VIO, SE(3)) match case-sensitively."""
    return any(c.isalpha() for c in phrase) and phrase == phrase.upper()


@dataclass(frozen=True)
class MatchView:
    """A title and abstract prepared once for matching many phrases."""

    title: str
    abstract: str
    title_lower: str
    abstract_lower: str


def match_view(title: str | None, abstract: str | None) -> MatchView:
    t, a = match_text(title), match_text(abstract)
    return MatchView(t, a, t.lower(), a.lower())


@dataclass(frozen=True)
class Phrase:
    """A compiled phrase. `literal` is a substring every match must contain; checking it first
    skips the regex for the large majority of phrases that cannot match a given paper."""

    text: str
    pattern: re.Pattern[str]
    literal: str | None
    case_sensitive: bool

    def _may_match(self, text: str, lowered: str) -> bool:
        return self.literal is None or self.literal in (text if self.case_sensitive else lowered)

    def search(self, text: str, lowered: str | None = None) -> bool:
        lowered = text.lower() if lowered is None else lowered
        return self._may_match(text, lowered) and self.pattern.search(text) is not None

    def count(self, text: str, lowered: str | None = None, cap: int = 4) -> int:
        lowered = text.lower() if lowered is None else lowered
        if not self._may_match(text, lowered):
            return 0
        return sum(1 for _ in zip(range(cap), self.pattern.finditer(text)))

    def in_title(self, view: MatchView) -> bool:
        return self.search(view.title, view.title_lower)

    def in_abstract(self, view: MatchView, cap: int = 4) -> int:
        return self.count(view.abstract, view.abstract_lower, cap)


def compile_phrase(phrase: str, *, case_sensitive: bool | None = None) -> Phrase:
    """Compile a taxonomy/keyword phrase. See config/taxonomy.yaml for the rules."""
    phrase = phrase.strip()
    if phrase.startswith("re:"):
        return Phrase(phrase, re.compile(phrase[3:], re.IGNORECASE), None, False)
    normalised = match_text(phrase)
    if case_sensitive is None:
        case_sensitive = is_acronym(normalised)
    tokens = [t for t in re.split(r"[\s\-]+", normalised) if t]
    if not tokens:
        raise ValueError("empty phrase")
    parts = [re.escape(t[:-1]) + r"\w*" if t.endswith("*") and len(t) > 1 else re.escape(t) for t in tokens]
    last = tokens[-1]
    if last.endswith("*"):
        plural = ""
    elif len(last) > 2 and last[-1] in "yY" and last[-2].lower() not in "aeiou":
        parts[-1] = re.escape(last[:-1]) + ("(?:Y|IES)" if case_sensitive else "(?:y|ies)")  # policy -> policies
        plural = ""
    else:
        plural = r"(?:s|es)?"
    pattern = r"(?<!\w)" + r"[\s\-]*".join(parts) + plural + r"(?!\w)"
    literal = tokens[0].rstrip("*") or None
    if literal and len(tokens) == 1 and not plural and not last.endswith("*"):
        literal = literal[:-1]  # "trajectory" must also find "trajectories"
    if literal and not case_sensitive:
        literal = literal.lower()
    return Phrase(phrase, re.compile(pattern, 0 if case_sensitive else re.IGNORECASE), literal, case_sensitive)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def relative_time(moment: datetime, now: datetime) -> str:
    """'just now', '5 minutes ago', '3 hours ago', '2 days ago'."""
    seconds = max(0, int((now - moment).total_seconds()))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"
