import sqlite3

from db import MIGRATIONS, get_connection, run_migrations


def test_get_connection_creates_history_and_settings_tables():
    conn = get_connection(":memory:")
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "history" in tables
    assert "settings" in tables


def test_get_connection_row_factory_allows_column_access_by_name():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO settings (id, max_concurrent_downloads) VALUES (1, 5)")
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    assert row["max_concurrent_downloads"] == 5


def test_get_connection_persists_to_real_file(tmp_path):
    db_path = tmp_path / "test.db"
    conn1 = get_connection(db_path)
    conn1.execute("INSERT INTO settings (id, max_concurrent_downloads) VALUES (1, 7)")
    conn1.commit()
    conn1.close()

    conn2 = get_connection(db_path)
    row = conn2.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    assert row["max_concurrent_downloads"] == 7


def test_run_migrations_idempotent_when_called_twice():
    conn = get_connection(":memory:")
    run_migrations(conn)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [row["name"] for row in tables]
    assert table_names.count("history") == 1
    assert table_names.count("settings") == 1


def _column_names(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_history_table_has_transcript_columns():
    conn = get_connection(":memory:")
    columns = _column_names(conn, "history")
    assert {"transcript", "transcript_status", "transcript_error", "summary", "transcribed_at"} <= columns


def test_history_transcript_status_defaults_to_none():
    conn = get_connection(":memory:")
    conn.execute(
        "INSERT INTO history (url, status, finished_at) VALUES (?, 'done', datetime('now'))",
        ("https://vimeo.com/1",),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM history").fetchone()
    assert row["transcript_status"] == "none"


def test_history_table_has_independent_summary_columns():
    conn = get_connection(":memory:")
    columns = _column_names(conn, "history")
    assert {"summary_status", "summary_error", "summarized_at"} <= columns


def test_history_summary_status_defaults_to_none_independently_of_transcript_status():
    conn = get_connection(":memory:")
    conn.execute(
        "INSERT INTO history (url, status, transcript_status, finished_at) "
        "VALUES (?, 'done', 'done', datetime('now'))",
        ("https://vimeo.com/1",),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM history").fetchone()
    assert row["transcript_status"] == "done"
    assert row["summary_status"] == "none"


def test_settings_table_has_api_key_columns():
    conn = get_connection(":memory:")
    columns = _column_names(conn, "settings")
    assert {
        "gemini_api_key", "anthropic_api_key", "openai_api_key",
        "groq_api_key", "openrouter_api_key",
    } <= columns


def test_settings_table_has_provider_selection_columns_with_defaults():
    conn = get_connection(":memory:")
    columns = _column_names(conn, "settings")
    assert {"transcription_provider", "summarization_provider"} <= columns
    conn.execute("INSERT INTO settings (id) VALUES (1)")
    conn.commit()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    assert row["transcription_provider"] == "gemini"
    assert row["summarization_provider"] == "anthropic"


def test_migration_v5_preserves_existing_key_value_under_new_column_name(tmp_path):
    # Simulates a real database that already ran migration v4 (e.g. from
    # local testing before the rename) -- the rename must not silently
    # drop whatever key the user had already entered.
    db_path = tmp_path / "pre_rename.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    for version, sql in MIGRATIONS[:4]:
        conn.executescript(sql)
    conn.execute("PRAGMA user_version = 4")
    conn.execute("INSERT INTO settings (id, openai_api_key) VALUES (1, 'sk-already-saved')")
    conn.commit()
    conn.close()

    migrated = get_connection(db_path)
    row = migrated.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    assert row["gemini_api_key"] == "sk-already-saved"


def test_migration_v6_preserves_existing_key_value_under_new_column_name(tmp_path):
    # Simulates a real database that already ran migration v5 (e.g. from
    # local testing of the OpenRouter swap before this second rename).
    db_path = tmp_path / "pre_second_rename.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    for version, sql in MIGRATIONS[:5]:
        conn.executescript(sql)
    conn.execute("PRAGMA user_version = 5")
    conn.execute("INSERT INTO settings (id, openrouter_api_key) VALUES (1, 'sk-or-already-saved')")
    conn.commit()
    conn.close()

    migrated = get_connection(db_path)
    row = migrated.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    assert row["gemini_api_key"] == "sk-or-already-saved"
