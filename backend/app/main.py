"""FastAPI application entrypoint."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.campaigns import router as campaigns_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.webhooks import router as webhooks_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    active_settings = settings or get_settings()
    logger = configure_logging(active_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Starting %s v%s (env=%s, db=%s)",
            active_settings.app_name,
            active_settings.app_version,
            active_settings.environment,
            active_settings.masked_database_uri,
        )
        yield
        logger.info("Shutting down %s", active_settings.app_name)

    application = FastAPI(
        title=active_settings.app_name,
        version=active_settings.app_version,
        description=(
            "Backend foundation for the Plural WhatsApp Upsell & Cross-Sell Automation service. "
            "Stage 2 provides core configuration, SQLAlchemy 2.x models, Alembic migrations, "
            "Pydantic v2 schemas, repositories, and liveness/readiness probes."
        ),
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    application.state.settings = active_settings

    if active_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=active_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(health_router)
    application.include_router(webhooks_router)
    application.include_router(campaigns_router)
    application.include_router(dashboard_router)

    static_base = Path(__file__).resolve().parent / "static"
    simulator_dir = static_base / "simulator"
    application.mount("/static", StaticFiles(directory=static_base), name="static-assets")
    application.mount("/simulator/static", StaticFiles(directory=simulator_dir), name="simulator-static")

    @application.get("/simulator", include_in_schema=False)
    def simulator_page() -> FileResponse:
        return FileResponse(simulator_dir / "index.html")

    @application.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/simulator")
    return application


app = create_app()
