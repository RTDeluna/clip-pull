from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from downloader import check_aria2c_available
from settings_store import SettingsStore


class SettingsUpdateRequest(BaseModel):
    max_concurrent_downloads: Optional[int] = Field(None, ge=1, le=10)
    concurrent_fragment_downloads: Optional[int] = Field(None, ge=1, le=32)
    aria2c_enabled: Optional[bool] = None
    skip_duplicates: Optional[bool] = None
    default_output_folder: Optional[str] = None


def build_settings_router(settings_store: SettingsStore) -> APIRouter:
    router = APIRouter()

    @router.get("/settings")
    def get_settings() -> dict:
        settings = settings_store.get()
        settings["aria2c_detected"] = check_aria2c_available()
        return settings

    @router.patch("/settings")
    def patch_settings(request: SettingsUpdateRequest) -> dict:
        updated = settings_store.update(**request.model_dump())
        updated["aria2c_detected"] = check_aria2c_available()
        return updated

    return router
