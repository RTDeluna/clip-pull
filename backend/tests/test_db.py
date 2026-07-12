from db import get_connection, run_migrations


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


def test_settings_table_has_api_key_columns():
    conn = get_connection(":memory:")
    columns = _column_names(conn, "settings")
    assert {"openai_api_key", "anthropic_api_key"} <= columns
