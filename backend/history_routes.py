from typing import Optional

from fastapi import APIRouter, HTTPException

from history_store import HistoryStore


def build_history_router(history_store: HistoryStore) -> APIRouter:
    router = APIRouter()

    @router.get("/history")
    def get_history(
        q: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        entries = history_store.search(query=q, status=status, limit=limit, offset=offset)
        return {"entries": entries}

    @router.delete("/history/{entry_id}")
    def delete_history_entry(entry_id: int) -> dict:
        if not history_store.delete(entry_id):
            raise HTTPException(status_code=404, detail="History entry not found")
        return {"deleted": entry_id}

    @router.delete("/history")
    def clear_history(q: Optional[str] = None, status: Optional[str] = None) -> dict:
        return {"deleted": history_store.clear(query=q, status=status)}

    return router
