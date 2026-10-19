"""Signals taken only from what the authors wrote in the source metadata.

- Resource links: URLs the authors listed in the abstract or comment. They are recorded as
  author-listed; whether the link still resolves is not checked in this phase.
- Venue: counted only when acceptance or publication is stated explicitly. "Submitted to"
  and "under review" never count, and workshop papers are kept separate from the main venue.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from paper_radar.models import ResourceLinks, VenueSignal
from paper_radar.taxonomy import Taxonomy

_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
_BARE_CODE_URL = re.compile(r"(?<![\w/.])(?:www\.)?(?:github\.com|gitlab\.com)/[\w.\-]+/[\w.\-]+", re.IGNORECASE)
CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "huggingface.co")
_IGNORED_HOSTS = ("arxiv.org", "doi.org", "dx.doi.org", "creativecommons.org")

# "subm\w*" also catches typos such as "Submiited"; extensions of earlier papers are not this paper's venue.
_NEGATIVE = re.compile(
    r"\b(subm\w*|under review|in review|being reviewed|extension of|extended version of|extends our|based on our)\b",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"\b(accepted|accept|to appear|appears? (?:in|at)|published|camera[- ]ready|oral|spotlight|poster|"
    r"presented at|proceedings of|in proceedings|final version)\b",
    re.IGNORECASE,
)
# Workshops, doctoral consortia, research statements and the like are not the main venue.
_WORKSHOP = re.compile(r"\b(workshops?|pioneers|doctoral consortium|late[- ]breaking|research statement|extended abstract)\b", re.IGNORECASE)
_YEAR_AFTER = re.compile(r"^[^.;]{0,14}?(?:'(\d{2})|\b((?:19|20)\d{2}))\b")
_YEAR_BEFORE = re.compile(r"\b((?:19|20)\d{2})\b[\w\s/()-]{0,60}$")


def _clean_url(url: str) -> str:
    return url.rstrip(".,;:!?")


def extract_links(*texts: str | None) -> ResourceLinks:
    code_url = project_url = None
    for text in texts:
        if not text:
            continue
        urls = [_clean_url(u) for u in _URL.findall(text)]
        urls += ["https://" + _clean_url(u) for u in _BARE_CODE_URL.findall(text) if not any(u in x for x in urls)]
        for url in urls:
            host = urlparse(url).netloc.lower().removeprefix("www.")
            path_parts = [p for p in urlparse(url).path.split("/") if p]
            if not host or host.endswith(_IGNORED_HOSTS):
                continue
            if host in CODE_HOSTS:
                if code_url is None and len(path_parts) >= 2:
                    code_url = url
            elif project_url is None:
                project_url = url
    return ResourceLinks(code_url=code_url, project_url=project_url)


def _clause_before(text: str, start: int) -> str:
    """The text of the same clause leading up to a venue mention."""
    window = text[max(0, start - 90) : start]
    cut = max(window.rfind(";"), window.rfind(". "), window.rfind("\n"))
    return window[cut + 1 :] if cut >= 0 else window


def _clause_after(text: str, end: int) -> str:
    window = text[end : end + 150]
    cut = min([i for i in (window.find(";"), window.find(". ")) if i >= 0], default=len(window))
    return window[:cut]


def _year_near(text: str, start: int, end: int) -> str | None:
    after = _YEAR_AFTER.match(text[end : end + 20])
    if after:
        return f"20{after.group(1)}" if after.group(1) else after.group(2)
    before = _YEAR_BEFORE.search(text[max(0, start - 80) : start])
    return before.group(1) if before else None


def detect_venue(comment: str | None, journal_ref: str | None, taxonomy: Taxonomy) -> VenueSignal | None:
    """The venue the authors say this paper was accepted at or published in, if any.

    Counted: an explicit acceptance/publication phrase near a known venue, or a known venue
    named with its year and no negative wording ("CoRL 2026"). Never counted: "submitted to",
    "under review", or a venue of an earlier paper this one extends.
    """
    if comment:
        mentions = sorted(
            ((m.start(), m.end(), venue) for venue in taxonomy.venues for p in venue.matchers for m in p.pattern.finditer(comment)),
            key=lambda mention: mention[0],
        )
        for start, end, venue in mentions:
            before, after = _clause_before(comment, start), _clause_after(comment, end)
            if _NEGATIVE.search(before):
                continue
            year = _year_near(comment, start, end)
            explicit = _POSITIVE.search(f"{before} {after[:60]}")
            if not explicit and not year:
                continue
            name = f"{venue.name} {year}" if year else venue.name
            if _WORKSHOP.search(after) or _WORKSHOP.search(before):
                return VenueSignal(name=f"{name} workshop", tier="workshop", evidence="accepted" if explicit else "stated")
            if not explicit:
                evidence = "stated"
            else:
                evidence = "published" if re.search(r"published", explicit.group(0), re.IGNORECASE) else "accepted"
            return VenueSignal(name=name, tier=venue.tier, evidence=evidence)
    if journal_ref:  # free text that authors sometimes misuse, so only known venues count
        for venue in taxonomy.venues:
            match = next((m for p in venue.matchers if (m := p.pattern.search(journal_ref))), None)
            if match:
                year = re.search(r"\b(?:19|20)\d{2}\b", journal_ref)
                name = f"{venue.name} {year.group(0)}" if year else venue.name
                return VenueSignal(name=name, tier=venue.tier, evidence="published")
    return None
