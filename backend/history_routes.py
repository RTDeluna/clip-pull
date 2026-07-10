from typing import Optional

from fastapi import APIRouter

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

    return router
