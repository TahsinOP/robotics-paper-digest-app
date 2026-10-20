import pytest

from paper_radar.sources.arxiv_source import RateLimiter
from paper_radar.sources.base import SourceError
from paper_radar.sources.semantic_scholar import ROBOTICS_QUERY, SemanticScholar, build_query, clean_title


class Response:
    def __init__(self, status, payload=None):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((params, headers))
        return self.responses.pop(0)


PAYLOAD = {
    "data": [
        {"title": "OpenVLA", "citationCount": 4032, "influentialCitationCount": 573, "externalIds": {"ArXiv": "2406.09246"},
         "venue": "CoRL", "year": 2024, "authors": [{"name": "Moo Jin Kim"}]},
        {"title": "No arXiv version", "citationCount": 9000, "externalIds": {"DOI": "10.1/x"}},
        {"title": "Octo", "citationCount": 2101, "influentialCitationCount": 143, "externalIds": {"ArXiv": "2405.12213"}},
    ]
}


def client(session, **kwargs):
    return SemanticScholar(session=session, rate_limiter=RateLimiter(0, clock=lambda: 0.0, sleep=lambda s: None),
                           sleep=lambda s: None, **kwargs)


def test_query_building() -> None:
    assert build_query("") == ROBOTICS_QUERY
    assert build_query("SLAM") == f"SLAM + {ROBOTICS_QUERY}"
    assert build_query("diffusion policy") == f'"diffusion policy" + {ROBOTICS_QUERY}'


def test_most_cited_keeps_arxiv_papers_and_retries_rate_limits() -> None:
    session = Session(Response(429), Response(200, PAYLOAD))
    results = client(session, api_key="").most_cited(2024, limit=5)
    assert [r.arxiv_id for r in results] == ["2406.09246", "2405.12213"]
    assert results[0].citation_count == 4032 and results[0].influential_count == 573
    params, headers = session.calls[-1]
    assert params["year"] == "2024" and params["sort"] == "citationCount:desc"
    assert "x-api-key" not in headers


def test_api_key_header_and_errors() -> None:
    session = Session(Response(200, PAYLOAD))
    client(session, api_key="k").most_cited(2024)
    assert session.calls[0][1]["x-api-key"] == "k"
    with pytest.raises(SourceError, match="rejected the API key"):
        client(Session(Response(403)), api_key="bad").most_cited(2024)
    with pytest.raises(SourceError, match="rate limit"):
        client(Session(*[Response(429)] * 5), api_key="").most_cited(2024)


def test_clean_title() -> None:
    assert clean_title("π_0.5: a  Vision-Language-Action Model") == "π0.5: a Vision-Language-Action Model"
    assert clean_title("snake_case_names stay") == "snake_case_names stay"
