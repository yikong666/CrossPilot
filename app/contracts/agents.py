from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.contracts.graph import EvidenceRecord


class AgentName(StrEnum):
    MARKET = "market"
    COMPETITOR = "competitor"
    PRICING = "pricing"
    COMPLIANCE = "compliance"


class AgentTask(BaseModel):
    task_id: str
    agent: AgentName
    objective: str
    required_fields: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    agent: AgentName
    status: Literal["success", "need_input", "failed"]
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ValidationDecision(BaseModel):
    action: Literal["pass", "need_input", "replan", "fail"]
    missing_fields: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
