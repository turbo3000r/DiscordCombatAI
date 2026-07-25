"""SQLite connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_configs (
    guild_id TEXT PRIMARY KEY,
    document_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS suggestions (
    guild_id TEXT NOT NULL,
    suggestion_id TEXT NOT NULL,
    document_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, suggestion_id)
);

CREATE TABLE IF NOT EXISTS status_document (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    document_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS metrics (
    node_id TEXT NOT NULL,
    row_key TEXT NOT NULL,
    document_json TEXT NOT NULL,
    PRIMARY KEY (node_id, row_key)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


__all__ = ["SCHEMA", "connect"]
