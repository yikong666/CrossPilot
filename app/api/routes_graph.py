from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import AppDependencies, get_dependencies
from app.workflow.runtime import GraphData

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])
Dependencies = Annotated[AppDependencies, Depends(get_dependencies)]


@router.get("/{thread_id}/graph", response_model=None)
async def get_analysis_graph(
    thread_id: str,
    dependencies: Dependencies,
) -> GraphData:
    graph = await dependencies.runtime.get_graph(thread_id)
    if graph is None:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis trace found for thread_id '{thread_id}'.",
        )
    return graph
