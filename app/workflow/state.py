from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from app.contracts.agents import AgentName, AgentResult, AgentTask, ValidationDecision
from app.contracts.api import AnalysisRequest

AgentResultKey = str | AgentName
AgentResultMap = dict[str, AgentResult]


def merge_agent_results(
    left: Mapping[AgentResultKey, AgentResult] | None,
    right: Mapping[AgentResultKey, AgentResult] | None,
) -> AgentResultMap:
    merged: AgentResultMap = {}
    for results in (left, right):
        if not results:
            continue
        for agent, result in results.items():
            key = agent.value if isinstance(agent, AgentName) else agent
            merged[key] = result
    return merged


class WorkflowState(TypedDict, total=False):
    trace_id: str
    thread_id: str
    request: AnalysisRequest
    tasks: list[AgentTask]
    agent_results: Annotated[AgentResultMap, merge_agent_results]
    validation: ValidationDecision | None
    replan_count: int
    final_answer: str | None
    graph_nodes: list[dict[str, Any]]
    graph_edges: list[dict[str, Any]]
    errors: list[str]
