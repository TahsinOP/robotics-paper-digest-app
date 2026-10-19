"""The contract every discovery source follows: return normalised candidates plus readable errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from paper_radar.models import PaperCandidate

ProgressCallback = Callable[[str], None]


class SourceError(Exception):
    """A source failed in a way the user should hear about. The message is shown as-is."""


@dataclass
class FetchResult:
    candidates: list[PaperCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class PaperSource(Protocol):
    name: str

    def fetch_recent(
        self,
        *,
        since: datetime,
        until: datetime,
        max_results: int,
        progress: ProgressCallback | None = None,
    ) -> FetchResult: ...
