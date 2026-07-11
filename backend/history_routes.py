import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException

from history_store import HistoryStore

DB_BUSY_MESSAGE = "The app's local database is busy — try again in a moment."


def build_history_router(history_store: HistoryStore) -> APIRouter:
    router = APIRouter()

    @router.get("/history")
    def get_history(
        q: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        try:
            entries = history_store.search(query=q, status=status, limit=limit, offset=offset)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        return {"entries": entries}

    @router.delete("/history/{entry_id}")
    def delete_history_entry(entry_id: int) -> dict:
        try:
            deleted = history_store.delete(entry_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if not deleted:
            raise HTTPException(status_code=404, detail="History entry not found")
        return {"deleted": entry_id}

    @router.delete("/history")
    def clear_history(q: Optional[str] = None, status: Optional[str] = None) -> dict:
        try:
            return {"deleted": history_store.clear(query=q, status=status)}
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)

    return router
