from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
def health(session: Session = SessionDep, settings: Settings = SettingsDep) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - the point is to report, not raise
        database = f"error: {exc}"
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        environment=settings.app_env,
        database=database,
        version=VERSION,
    )
