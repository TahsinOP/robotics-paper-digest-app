"""Full text for deeper digests: download the open-access arXiv PDF and extract its text.

Only arXiv PDFs are fetched, one at a time on the user's request, through the same polite
rate limiter as the metadata API. The extracted text keeps page markers ("[Page 3]") so digests
can point to where evidence appears; the bibliography is dropped; long papers are cut at a
page boundary and labelled partial. PDFs themselves are never stored, only the extracted text.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from paper_radar import __version__
from paper_radar.sources.arxiv_source import SHARED_RATE_LIMITER, RateLimiter

ALLOWED_HOSTS = ("arxiv.org", "export.arxiv.org")
MAX_PDF_BYTES = 60 * 1024 * 1024
MIN_USEFUL_CHARS = 1500
DEFAULT_MAX_CHARS = 120_000

_REFERENCES = re.compile(r"(?m)^\s*(?:[IVX]+\.?\s+|\d+\.?\s+)?(REFERENCES|References|Bibliography|BIBLIOGRAPHY)\s*$")
_APPENDIX = re.compile(
    r"(?m)^\s*(?:[A-Z]\.?\s+)?(APPENDIX|Appendix|APPENDICES|Appendices|Supplementary Material|SUPPLEMENTARY MATERIAL)\b"
)


class FullTextError(Exception):
    """The full text could not be obtained. The message is meant for the user."""


@dataclass(frozen=True)
class PaperText:
    text: str
    page_count: int
    pages_included: int
    references_removed: bool
    truncated: bool

    def describe(self) -> tuple[str, str]:
        return describe_text(self.page_count, self.pages_included, self.references_removed, self.truncated)


def describe_text(page_count: int, pages_included: int, references_removed: bool, truncated: bool) -> tuple[str, str]:
    """(source level, human description) for extracted full text."""
    if truncated:
        return "partial_full_text", f"First {pages_included} of {page_count} pages of the arXiv PDF (length limit reached)"
    extra = "; bibliography omitted" if references_removed else ""
    return "full_text", f"Full text of the arXiv PDF ({page_count} pages{extra})"


def download_pdf(
    url: str,
    *,
    session: requests.Session | None = None,
    rate_limiter: RateLimiter = SHARED_RATE_LIMITER,
    timeout: float = 120.0,
    max_bytes: int = MAX_PDF_BYTES,
) -> bytes:
    host = urlparse(url).netloc.lower()
    if urlparse(url).scheme != "https" or host not in ALLOWED_HOSTS:
        raise FullTextError("Only open-access arXiv PDFs can be read automatically.")
    session = session or requests.Session()
    rate_limiter.wait()
    try:
        with session.get(
            url, stream=True, timeout=timeout, headers={"User-Agent": f"robotics-paper-radar/{__version__}"}
        ) as response:
            if response.status_code == 404:
                raise FullTextError("arXiv has no PDF for this paper.")
            if response.status_code != 200:
                raise FullTextError(f"arXiv didn't send the PDF (HTTP {response.status_code}). Try again later.")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise FullTextError(f"The PDF is larger than {max_bytes // (1024 * 1024)} MB, so it wasn't downloaded.")
                chunks.append(chunk)
    except requests.Timeout as exc:
        raise FullTextError("Downloading the PDF took too long. Try again later.") from exc
    except requests.ConnectionError as exc:
        raise FullTextError("Couldn't reach arXiv to download the PDF. Check your internet connection.") from exc
    payload = b"".join(chunks)
    if not payload.startswith(b"%PDF"):
        raise FullTextError("arXiv sent something that isn't a PDF.")
    return payload


def extract_pages(pdf_bytes: bytes) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise FullTextError("The PDF couldn't be read.") from exc


def _clean_page(text: str) -> str:
    text = text.replace("­", "")  # soft hyphens
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)  # re-join words split across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def prepare_text(pages: list[str], max_chars: int = DEFAULT_MAX_CHARS) -> PaperText:
    cleaned = [_clean_page(p) for p in pages]
    references_removed = False
    in_references = False
    kept: list[str] = []
    for page in cleaned:
        if in_references:
            appendix = _APPENDIX.search(page)
            if not appendix:
                kept.append("")
                continue
            page, in_references = page[appendix.start():], False
        refs = _REFERENCES.search(page)
        if refs:
            after = page[refs.end():]
            appendix = _APPENDIX.search(after)
            page = page[: refs.start()] + ("\n" + after[appendix.start():] if appendix else "")
            in_references = appendix is None
            references_removed = True
        kept.append(page)

    parts, total, included = [], 0, 0
    for number, page in enumerate(kept, start=1):
        if not page:
            continue
        block = f"[Page {number}]\n{page}\n"
        if total + len(block) > max_chars:
            if not parts:  # a single enormous page: keep what fits
                parts.append(block[:max_chars])
                included = number
            break
        parts.append(block)
        total += len(block)
        included = number
    text = "\n".join(parts).strip()
    if len(text) < MIN_USEFUL_CHARS:
        raise FullTextError(
            "The PDF has almost no extractable text (it may be scanned images). Use the abstract digest instead."
        )
    last_content_page = max((i for i, p in enumerate(kept, start=1) if p), default=0)
    return PaperText(
        text=text,
        page_count=len(pages),
        pages_included=included,
        references_removed=references_removed,
        truncated=included < last_content_page,
    )


PdfFetcher = Callable[[str], bytes]
