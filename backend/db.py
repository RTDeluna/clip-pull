import sqlite3
from pathlib import Path
from typing import Union

MIGRATIONS = [
    (1, """
        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_id TEXT,
          batch_id TEXT,
          url TEXT NOT NULL,
          title TEXT,
          output_path TEXT,
          total_size TEXT,
          status TEXT NOT NULL CHECK (status IN ('done', 'error')),
          error_reason TEXT,
          retry_count INTEGER NOT NULL DEFAULT 0,
          started_at TEXT,
          finished_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);
        CREATE INDEX IF NOT EXISTS idx_history_status_url ON history(status, url);
    """),
    (2, """
        CREATE TABLE IF NOT EXISTS settings (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          max_concurrent_downloads INTEGER NOT NULL DEFAULT 3,
          concurrent_fragment_downloads INTEGER NOT NULL DEFAULT 8,
          aria2c_enabled INTEGER NOT NULL DEFAULT 1,
          skip_duplicates INTEGER NOT NULL DEFAULT 0,
          default_output_folder TEXT
        );
    """),
]


def get_connection(db_path: Union[str, Path]) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    run_migrations(conn)
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in MIGRATIONS:
        if version > current_version:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
