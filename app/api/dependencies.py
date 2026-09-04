from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from fastapi import Request

from app.workflow.runtime import UnavailableWorkflowRuntime, WorkflowRuntime


class Neo4jClient(Protocol):
    async def close(self) -> None: ...

    def verify_connectivity(self) -> Awaitable[bool | None]: ...


class HttpClient(Protocol):
    async def aclose(self) -> None: ...


@dataclass
class AppDependencies:
    """Process-scoped dependencies with a test-friendly workflow seam."""

    runtime: WorkflowRuntime = field(default_factory=UnavailableWorkflowRuntime)
    neo4j_client: Neo4jClient | None = None
    http_client: HttpClient | None = None
    llm_configured: bool = False

    async def startup(self) -> None:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient()

    async def shutdown(self) -> None:
        if self.neo4j_client is not None:
            await self.neo4j_client.close()
        if self.http_client is not None:
            await self.http_client.aclose()

    async def neo4j_status(self) -> str:
        if self.neo4j_client is None:
            return "unavailable"
        try:
            connected = await self.neo4j_client.verify_connectivity()
        except Exception:
            return "unavailable"
        return "ok" if connected is not False else "unavailable"


def get_dependencies(request: Request) -> AppDependencies:
    return request.app.state.dependencies  # type: ignore[no-any-return]
