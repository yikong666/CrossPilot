from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.dependencies import AppDependencies
from app.api.routes_analysis import router as analysis_router
from app.api.routes_graph import router as graph_router


def create_app(dependencies: AppDependencies | None = None) -> FastAPI:
    app_dependencies = dependencies or AppDependencies()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await app.state.dependencies.startup()
        try:
            yield
        finally:
            await app.state.dependencies.shutdown()

    app = FastAPI(title="CrossPilot API", lifespan=lifespan)
    app.state.dependencies = app_dependencies

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "api": "ok",
            "neo4j": await app.state.dependencies.neo4j_status(),
            "llm_config": "configured" if app.state.dependencies.llm_configured else "missing",
        }

    app.include_router(analysis_router)
    app.include_router(graph_router)
    return app


app = create_app()
