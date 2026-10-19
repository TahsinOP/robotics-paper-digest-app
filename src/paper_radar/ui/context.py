"""Process-wide app context (config, taxonomy, database) and small per-run helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime

import streamlit as st

from paper_radar import db
from paper_radar.config import AppConfig
from paper_radar.preferences import Preferences, load_preferences
from paper_radar.services import library
from paper_radar.services.reading_queue import week_start
from paper_radar.taxonomy import Taxonomy, load_taxonomy
from paper_radar.text import utc_now


@dataclass(frozen=True)
class AppContext:
    config: AppConfig
    taxonomy: Taxonomy

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with db.session(self.config.db_path) as conn:
            yield conn

    def preferences(self, conn: sqlite3.Connection) -> Preferences:
        return load_preferences(conn, self.taxonomy, self.config.profile_path)


@st.cache_resource(show_spinner=False)
def get_context() -> AppContext:
    """Load configuration, prepare the database and bring stored analysis up to date (once per process)."""
    config = AppConfig.from_env()
    taxonomy = load_taxonomy(config.taxonomy_path, config.venues_path)
    db.initialise(config.db_path)
    ctx = AppContext(config=config, taxonomy=taxonomy)
    with ctx.connect() as conn:
        library.reclassify_stale(conn, taxonomy)
    return ctx


def now() -> datetime:
    return utc_now()


def this_week() -> date:
    return week_start(datetime.now().astimezone().date())
