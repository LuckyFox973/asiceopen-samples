"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import auth, health, messages, sync
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.startup import verify_configuration

log = get_logger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    problems = verify_configuration(settings)
    for problem in problems:
        log.warning("startup.configuration", issue=problem)
    log.info("startup", environment=settings.app_env, api_prefix=API_PREFIX)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Email AI Assistant",
        description=(
            "Personal assistant over Google Workspace mail. "
            "Phase 1: durable Gmail ingest, search and audit."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )
    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(sync.router, prefix=API_PREFIX)
    app.include_router(messages.router, prefix=API_PREFIX)
    return app


app = create_app()
