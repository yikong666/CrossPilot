from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import AppDependencies, get_dependencies

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])
Dependencies = Annotated[AppDependencies, Depends(get_dependencies)]


class GraphResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


@router.get("/{thread_id}/graph", response_model=GraphResponse)
async def get_analysis_graph(
    thread_id: str,
    dependencies: Dependencies,
) -> GraphResponse:
    graph = await dependencies.runtime.get_graph(thread_id)
    if graph is None:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis trace found for thread_id '{thread_id}'.",
        )
    return GraphResponse.model_validate(graph)
