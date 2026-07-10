from pathlib import Path
from typing import Union

from db import get_connection

DEFAULT_SETTINGS = {
    "max_concurrent_downloads": 3,
    "concurrent_fragment_downloads": 8,
    "aria2c_enabled": True,
    "skip_duplicates": False,
    "default_output_folder": None,
}


class SettingsStore:
    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self._conn = get_connection(db_path)
        self._conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        self._conn.commit()

    def get(self) -> dict:
        row = self._conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return {
            "max_concurrent_downloads": row["max_concurrent_downloads"],
            "concurrent_fragment_downloads": row["concurrent_fragment_downloads"],
            "aria2c_enabled": bool(row["aria2c_enabled"]),
            "skip_duplicates": bool(row["skip_duplicates"]),
            "default_output_folder": row["default_output_folder"],
        }

    def update(self, **changes) -> dict:
        # None means "not provided" (partial update semantics), not "clear this field."
        fields_to_update = {
            key: value
            for key, value in changes.items()
            if key in DEFAULT_SETTINGS and value is not None
        }
        if not fields_to_update:
            return self.get()
        set_clause = ", ".join(f"{key} = ?" for key in fields_to_update)
        values = [
            int(value) if isinstance(value, bool) else value
            for value in fields_to_update.values()
        ]
        self._conn.execute(f"UPDATE settings SET {set_clause} WHERE id = 1", values)
        self._conn.commit()
        return self.get()
