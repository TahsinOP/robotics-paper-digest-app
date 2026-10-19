import pytest
import requests

from paper_radar.services.fulltext import FullTextError, download_pdf, extract_pages, prepare_text
from paper_radar.sources.arxiv_source import RateLimiter

from .pdfgen import make_pdf

BODY = "We present a LiDAR-inertial odometry method for UAVs. " * 20


class FakeStreamResponse:
    def __init__(self, status_code=200, payload=b"%PDF-1.4 fake"):
        self.status_code = status_code
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        for i in range(0, len(self.payload), chunk_size):
            yield self.payload[i : i + chunk_size]


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.urls = response, error, []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


def no_wait():
    return RateLimiter(0, clock=lambda: 0.0, sleep=lambda s: None)


def test_extracts_text_from_a_real_pdf() -> None:
    pages = extract_pages(make_pdf(["I. INTRODUCTION\nLoop closure matters.", "II. METHOD\nWe fuse IMU data."]))
    assert len(pages) == 2
    assert "Loop closure matters." in pages[0] and "We fuse IMU data." in pages[1]


def test_unreadable_pdf_is_reported() -> None:
    with pytest.raises(FullTextError, match="couldn't be read"):
        extract_pages(b"%PDF-1.4 this is not really a pdf")


def test_prepare_marks_pages_rejoins_hyphens_and_drops_references() -> None:
    pages = [
        "Title\n" + BODY + "\nA ro-\nbust estimator.",
        BODY + "\nREFERENCES\n[1] Someone, Some Paper, 2020.",
        "[2] Another reference, 2021.",
        "APPENDIX\nA. Extra experiments on 3 robots.",
    ]
    prepared = prepare_text(pages)
    assert prepared.text.startswith("[Page 1]")
    assert "A robust estimator." in prepared.text
    assert "Some Paper" not in prepared.text and "Another reference" not in prepared.text
    assert "Extra experiments on 3 robots." in prepared.text
    assert prepared.references_removed and not prepared.truncated
    assert prepared.describe() == ("full_text", "Full text of the arXiv PDF (4 pages; bibliography omitted)")


def test_long_papers_are_cut_at_a_page_boundary_and_labelled_partial() -> None:
    pages = [f"Page {i} text. " + BODY * 3 for i in range(1, 11)]
    prepared = prepare_text(pages, max_chars=9000)
    assert prepared.truncated
    assert 0 < prepared.pages_included < 10
    level, note = prepared.describe()
    assert level == "partial_full_text" and note.startswith(f"First {prepared.pages_included} of 10 pages")


def test_scanned_pdfs_with_no_text_are_rejected() -> None:
    with pytest.raises(FullTextError, match="scanned"):
        prepare_text(["", "  ", "Figure 1"])


def test_download_checks_host_status_size_and_content() -> None:
    pdf = make_pdf([BODY])
    ok = FakeSession(FakeStreamResponse(payload=pdf))
    assert download_pdf("https://arxiv.org/pdf/2610.00001", session=ok, rate_limiter=no_wait()) == pdf

    with pytest.raises(FullTextError, match="Only open-access arXiv"):
        download_pdf("https://example.com/paper.pdf", session=ok, rate_limiter=no_wait())
    with pytest.raises(FullTextError, match="no PDF"):
        download_pdf("https://arxiv.org/pdf/x", session=FakeSession(FakeStreamResponse(404)), rate_limiter=no_wait())
    with pytest.raises(FullTextError, match="larger than"):
        download_pdf("https://arxiv.org/pdf/x", session=FakeSession(FakeStreamResponse(payload=b"%PDF" + b"0" * 5000)),
                     rate_limiter=no_wait(), max_bytes=1000)
    with pytest.raises(FullTextError, match="isn't a PDF"):
        download_pdf("https://arxiv.org/pdf/x", session=FakeSession(FakeStreamResponse(payload=b"<html>")), rate_limiter=no_wait())
    with pytest.raises(FullTextError, match="took too long"):
        download_pdf("https://arxiv.org/pdf/x", session=FakeSession(error=requests.Timeout()), rate_limiter=no_wait())
