from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceRecord(BaseModel):
    source_type: Literal["neo4j", "calculator", "user_input"]
    summary: str
    cypher: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    graph_node_ids: list[str] = Field(default_factory=list)
    graph_edge_ids: list[str] = Field(default_factory=list)
