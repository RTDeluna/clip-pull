import threading
from pathlib import Path
from typing import Optional, Union

from db import get_connection


class LicenseStore:
    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self._conn = get_connection(db_path)
        # Seed the single default row up front, exactly like SettingsStore does
        # for `settings` -- the row then always exists (status 'none') and the
        # methods below are plain UPDATEs rather than upserts.
        self._conn.execute("INSERT OR IGNORE INTO license (id) VALUES (1)")
        self._conn.commit()
        # Same reasoning as SettingsStore._lock: this one connection is read
        # from license_routes' sync handlers (FastAPI threadpool) and from the
        # startup revalidation task (asyncio event-loop thread) -- serialize
        # explicitly rather than relying on undocumented sqlite3 connection
        # thread-safety under concurrent access.
        self._lock = threading.Lock()

    def _row(self):
        return self._conn.execute("SELECT * FROM license WHERE id = 1").fetchone()

    def get(self) -> dict:
        """Safe, frontend-facing view of the license state. Deliberately does
        NOT return the raw license_key -- only its last 4 chars -- so a caller
        can forward this straight to the UI without leaking the key."""
        with self._lock:
            row = self._row()
        if row is None:
            return {
                "status": "none",
                "license_key_last4": None,
                "purchase_email": None,
                "activated_at": None,
                "last_validated_at": None,
            }
        key = row["license_key"]
        return {
            "status": row["status"],
            "license_key_last4": key[-4:] if key else None,
            "purchase_email": row["purchase_email"],
            "activated_at": row["activated_at"],
            "last_validated_at": row["last_validated_at"],
        }

    def get_license_key(self) -> Optional[str]:
        """The raw stored key -- for internal revalidation only. Never expose
        this via a route; routes use get(), which masks the key."""
        with self._lock:
            row = self._row()
        return row["license_key"] if row is not None else None

    def set_active(self, *, license_key: str, purchase_email: Optional[str]) -> dict:
        with self._lock:
            self._conn.execute(
                """
                UPDATE license
                SET license_key = ?, status = 'active', purchase_email = ?,
                    activated_at = datetime('now'), last_validated_at = datetime('now')
                WHERE id = 1
                """,
                (license_key, purchase_email),
            )
            self._conn.commit()
        return self.get()

    def set_invalid(self) -> dict:
        # Keep the stored key/email on file -- the key is still what the user
        # entered, it just no longer verifies (e.g. a later re-check found it
        # refunded). Only the status and the last-checked timestamp change.
        with self._lock:
            self._conn.execute(
                "UPDATE license SET status = 'invalid', last_validated_at = datetime('now') WHERE id = 1"
            )
            self._conn.commit()
        return self.get()

    def touch_validated(self) -> dict:
        # A successful re-check that confirms the license is still active:
        # bump the timestamp, change nothing else.
        with self._lock:
            self._conn.execute(
                "UPDATE license SET last_validated_at = datetime('now') WHERE id = 1"
            )
            self._conn.commit()
        return self.get()

    def clear(self) -> dict:
        # Deactivation: wipe the row back to its pristine 'none' state.
        with self._lock:
            self._conn.execute(
                """
                UPDATE license
                SET license_key = NULL, status = 'none', purchase_email = NULL,
                    activated_at = NULL, last_validated_at = NULL
                WHERE id = 1
                """
            )
            self._conn.commit()
        return self.get()

    def is_pro(self) -> bool:
        """The cheap, no-network Pro gate every future gated route calls.
        Reflects the cached status from the last activation/revalidation --
        it does not re-check with Gumroad on every call by design."""
        return self.get()["status"] == "active"
