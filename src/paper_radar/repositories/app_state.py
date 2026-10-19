"""Key-value app settings and the refresh history."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from paper_radar.text import from_iso, to_iso


def get_json(conn: sqlite3.Connection, key: str) -> object | None:
    row = conn.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value_json"]) if row else None


def set_json(conn: sqlite3.Connection, key: str, value: object, now: datetime) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), to_iso(now)),
    )


def delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))


@dataclass(frozen=True)
class RefreshRun:
    source_name: str
    started_at: datetime
    finished_at: datetime
    status: str  # "ok" | "partial" | "failed"
    fetched: int
    inserted: int
    updated: int
    skipped: int
    errors: tuple[str, ...]


def record_refresh(conn: sqlite3.Connection, run: RefreshRun) -> None:
    conn.execute(
        "INSERT INTO refresh_runs (source_name, started_at, finished_at, status, fetched, inserted, updated,"
        " skipped, errors_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run.source_name,
            to_iso(run.started_at),
            to_iso(run.finished_at),
            run.status,
            run.fetched,
            run.inserted,
            run.updated,
            run.skipped,
            json.dumps(list(run.errors), ensure_ascii=False),
        ),
    )


def last_refresh(conn: sqlite3.Connection, source_name: str | None = None) -> RefreshRun | None:
    sql = "SELECT * FROM refresh_runs"
    params: tuple = ()
    if source_name:
        sql += " WHERE source_name = ?"
        params = (source_name,)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    if row is None:
        return None
    return RefreshRun(
        source_name=row["source_name"],
        started_at=from_iso(row["started_at"]),
        finished_at=from_iso(row["finished_at"]),
        status=row["status"],
        fetched=row["fetched"],
        inserted=row["inserted"],
        updated=row["updated"],
        skipped=row["skipped"],
        errors=tuple(json.loads(row["errors_json"])),
    )
