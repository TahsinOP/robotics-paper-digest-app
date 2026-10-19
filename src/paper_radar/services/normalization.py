"""Normalise identifiers, titles and author names so records from any source compare cleanly."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from paper_radar.models import PaperCandidate
from paper_radar.text import tex_to_text

_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(?:v(\d+))?$")
_ARXIV_OLD = re.compile(r"^([a-z][a-z\-]*(?:\.[a-z]{2})?/\d{7})(?:v(\d+))?$", re.IGNORECASE)
_DOI_PREFIXES = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def parse_arxiv_id(value: str | None) -> tuple[str, int | None] | None:
    """'http://arxiv.org/abs/2410.01234v2' -> ('2410.01234', 2). Returns None if not an arXiv id."""
    if not value:
        return None
    s = value.strip()
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\.pdf$", "", s, flags=re.IGNORECASE).strip("/")
    for pattern in (_ARXIV_NEW, _ARXIV_OLD):
        match = pattern.match(s)
        if match:
            version = int(match.group(2)) if match.group(2) else None
            return match.group(1).lower(), version
    return None


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = _DOI_PREFIXES.sub("", value.strip()).strip().lower()
    return doi if doi.startswith("10.") and "/" in doi else None


def normalize_title(title: str | None) -> str:
    """Lower-case, accent-free, punctuation-free title for duplicate detection."""
    text = unicodedata.normalize("NFKD", tex_to_text(title))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def author_key(name: str) -> str:
    """Family-name key used to judge whether two author lists are compatible."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
    if "," in folded:  # "Khan, Tahsin"
        folded = folded.split(",")[0]
    else:
        parts = folded.split()
        folded = parts[-1] if parts else ""
    return re.sub(r"[^a-z]", "", folded)


def content_hash(candidate: PaperCandidate) -> str:
    """Changes when the text a digest would be based on changes (title, abstract, version)."""
    payload = "\x1f".join([candidate.title, candidate.abstract, str(candidate.arxiv_version or "")])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
