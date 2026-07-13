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
    (3, """
        ALTER TABLE history ADD COLUMN transcript TEXT;
        ALTER TABLE history ADD COLUMN transcript_status TEXT NOT NULL DEFAULT 'none';
        ALTER TABLE history ADD COLUMN transcript_error TEXT;
        ALTER TABLE history ADD COLUMN summary TEXT;
        ALTER TABLE history ADD COLUMN transcribed_at TEXT;
    """),
    (4, """
        ALTER TABLE settings ADD COLUMN openai_api_key TEXT;
        ALTER TABLE settings ADD COLUMN anthropic_api_key TEXT;
    """),
    (5, """
        ALTER TABLE settings RENAME COLUMN openai_api_key TO openrouter_api_key;
    """),
    (6, """
        ALTER TABLE settings RENAME COLUMN openrouter_api_key TO gemini_api_key;
    """),
    (7, """
        ALTER TABLE history ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'none';
        ALTER TABLE history ADD COLUMN summary_error TEXT;
        ALTER TABLE history ADD COLUMN summarized_at TEXT;
    """),
    (8, """
        ALTER TABLE settings ADD COLUMN openai_api_key TEXT;
        ALTER TABLE settings ADD COLUMN groq_api_key TEXT;
        ALTER TABLE settings ADD COLUMN openrouter_api_key TEXT;
        ALTER TABLE settings ADD COLUMN transcription_provider TEXT NOT NULL DEFAULT 'gemini';
        ALTER TABLE settings ADD COLUMN summarization_provider TEXT NOT NULL DEFAULT 'anthropic';
    """),
    (9, """
        CREATE TABLE IF NOT EXISTS license (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          license_key TEXT,
          status TEXT NOT NULL DEFAULT 'none',
          purchase_email TEXT,
          activated_at TEXT,
          last_validated_at TEXT
        );
    """),
    (10, """
        CREATE TABLE IF NOT EXISTS ai_usage (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          provider TEXT NOT NULL,
          model TEXT,
          operation TEXT NOT NULL,
          history_id INTEGER,
          input_tokens INTEGER,
          output_tokens INTEGER,
          total_tokens INTEGER,
          audio_seconds REAL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_usage_provider ON ai_usage(provider);
    """),
    (11, """
        ALTER TABLE settings ADD COLUMN auto_transcribe_on_download INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE settings ADD COLUMN auto_summarize_after_transcribe INTEGER NOT NULL DEFAULT 0;
    """),
]


def get_connection(db_path: Union[str, Path]) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
        # Wait up to 5s for a competing writer to release its lock before
        # giving up with SQLITE_BUSY, rather than raising OperationalError on
        # the first contended read/write under concurrent access.
        conn.execute("PRAGMA busy_timeout=5000")
    run_migrations(conn)
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in MIGRATIONS:
        if version > current_version:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
