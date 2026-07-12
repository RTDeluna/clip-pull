import asyncio
import sqlite3

from fastapi import APIRouter, HTTPException

from background_tasks import track_task
from history_store import HistoryStore
from transcription import TranscriptionOrchestrator

DB_BUSY_MESSAGE = "The app's local database is busy — try again in a moment."


def build_transcription_router(
    history_store: HistoryStore, transcription_orchestrator: TranscriptionOrchestrator
) -> APIRouter:
    router = APIRouter()

    @router.post("/history/{entry_id}/transcribe", status_code=202)
    async def start_transcription(entry_id: int) -> dict:
        try:
            entry = history_store.get(entry_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if entry is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        if entry["status"] != "done" or not entry["output_path"]:
            raise HTTPException(
                status_code=400,
                detail="This download hasn't finished successfully, so there's nothing to transcribe.",
            )
        if not transcription_orchestrator.request_transcription(entry_id):
            raise HTTPException(status_code=400, detail="This entry is already being transcribed.")

        updated = transcription_orchestrator.mark_transcription_running(entry_id)
        track_task(asyncio.create_task(transcription_orchestrator.transcribe_entry(entry_id)))
        return {"entry": updated}

    @router.delete("/history/{entry_id}/transcript")
    def clear_transcript(entry_id: int) -> dict:
        try:
            updated = history_store.update_transcript(entry_id, status="none")
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if updated is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        return {"entry": updated}

    @router.post("/history/{entry_id}/summarize", status_code=202)
    async def start_summarization(entry_id: int) -> dict:
        try:
            entry = history_store.get(entry_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if entry is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        if entry["transcript_status"] != "done" or not entry["transcript"]:
            raise HTTPException(
                status_code=400,
                detail="This video hasn't been transcribed yet — transcribe it first.",
            )
        if not transcription_orchestrator.request_summarization(entry_id):
            raise HTTPException(status_code=400, detail="This entry is already being summarized.")

        updated = transcription_orchestrator.mark_summarization_running(entry_id)
        track_task(asyncio.create_task(transcription_orchestrator.summarize_entry(entry_id)))
        return {"entry": updated}

    @router.delete("/history/{entry_id}/summary")
    def clear_summary(entry_id: int) -> dict:
        try:
            updated = history_store.update_summary(entry_id, status="none")
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if updated is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        return {"entry": updated}

    return router
