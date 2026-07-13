import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gumroad_client import GumroadClientError, verify_license
from license_config import DEV_LICENSE_KEY
from license_store import LicenseStore

DB_BUSY_MESSAGE = "The app's local database is busy — try again in a moment."


class LicenseActivateRequest(BaseModel):
    license_key: str = Field(..., min_length=1)


def _entry(row: dict) -> dict:
    # Shared frontend-facing shape for GET /license and the activate/deactivate
    # responses. Derived from license_store.get(), which already masks the raw
    # key, so this never leaks it.
    return {
        "status": row["status"],
        "pro": row["status"] == "active",
        "purchase_email": row["purchase_email"],
        "activated_at": row["activated_at"],
        "last_validated_at": row["last_validated_at"],
    }


def build_license_router(license_store: LicenseStore) -> APIRouter:
    router = APIRouter()

    @router.get("/license")
    def get_license() -> dict:
        try:
            row = license_store.get()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        return _entry(row)

    @router.post("/license/activate")
    def activate_license(request: LicenseActivateRequest) -> dict:
        # Dev-only bypass so the activation flow can be tested before a real
        # Gumroad product exists -- inert unless CLIP_PULL_DEV_LICENSE_KEY is
        # explicitly set (see license_config.py), so this never fires in a
        # packaged build that doesn't set it.
        if DEV_LICENSE_KEY and request.license_key == DEV_LICENSE_KEY:
            try:
                row = license_store.set_active(
                    license_key=request.license_key, purchase_email="dev@local.test"
                )
            except sqlite3.OperationalError:
                raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
            return {"entry": _entry(row)}

        try:
            result = verify_license(request.license_key, increment_uses_count=True)
        except GumroadClientError:
            raise HTTPException(
                status_code=503,
                detail="Couldn't reach Gumroad to verify your license. Check your connection and try again.",
            )

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("message") or "That license key isn't valid.",
            )

        purchase = result.get("purchase") or {}
        if purchase.get("refunded") or purchase.get("chargebacked") or purchase.get("disputed"):
            raise HTTPException(
                status_code=400,
                detail="This license has been refunded or disputed and can't be activated.",
            )

        try:
            row = license_store.set_active(
                license_key=request.license_key,
                purchase_email=purchase.get("email"),
            )
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        return {"entry": _entry(row)}

    @router.post("/license/deactivate")
    def deactivate_license() -> dict:
        try:
            row = license_store.clear()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        return {"entry": _entry(row)}

    return router
