from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.result_validator import ResultValidator
from app.agents.strategy import StrategyAgent, StrategyRecommendation
from app.agents.supervisor import Supervisor, SupervisorPlan
from app.contracts.agents import AgentName, AgentResult, AgentTask, ValidationDecision
from app.contracts.api import AnalysisRequest, ProductInput
from app.contracts.events import SSEEvent
from app.workflow.routing import dispatch_tasks, needs_strategy, route_after_validation
from app.workflow.state import WorkflowState

logger = logging.getLogger(__name__)


class SpecialistAgent(Protocol):
    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult: ...


class SupervisorService(Protocol):
    async def plan(
        self,
        request: AnalysisRequest,
        *,
        gaps: list[str] | None = None,
    ) -> SupervisorPlan: ...


class ValidatorService(Protocol):
    async def validate(
        self,
        request: AnalysisRequest,
        tasks: list[AgentTask],
        results: Mapping[str, AgentResult],
    ) -> ValidationDecision: ...


class StrategyService(Protocol):
    async def run(
        self,
        question: str,
        results: Mapping[str, AgentResult],
    ) -> StrategyRecommendation: ...


@dataclass(frozen=True)
class WorkflowDependencies:
    supervisor: SupervisorService | Supervisor
    validator: ValidatorService | ResultValidator
    strategy: StrategyService | StrategyAgent
    agents: Mapping[AgentName, SpecialistAgent]


class WorkflowGraphState(WorkflowState, total=False):
    task: AgentTask
    market_entry_requested: bool
    dispatch_tasks: list[AgentTask]
    pending_input_tasks: list[AgentTask]
    round_id: int
    round_size: int
    dispatch_sequence: int


@dataclass
class _BarrierEntry:
    barrier: asyncio.Barrier
    remaining: int


class _StartCoordinator:
    """Keep one graph round from completing before all dispatched nodes start."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], _BarrierEntry] = {}

    async def wait(self, thread_id: str, round_id: int, round_size: int) -> None:
        key = (thread_id, round_id)
        entry = self._entries.get(key)
        if entry is None:
            entry = _BarrierEntry(asyncio.Barrier(round_size), round_size)
            self._entries[key] = entry
        try:
            await entry.barrier.wait()
        except BaseException:
            await entry.barrier.abort()
            raise
        finally:
            entry.remaining -= 1
            if entry.remaining == 0:
                self._entries.pop(key, None)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _event(
    state: WorkflowGraphState,
    event_type: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> SSEEvent:
    return SSEEvent.model_validate(
        {
            "trace_id": state["trace_id"],
            "thread_id": state["thread_id"],
            "event_type": event_type,
            "message": message,
            "timestamp": _timestamp(),
            "payload": payload or {},
        }
    )


_ANSWER_FIELDS: dict[AgentName, tuple[str, ...]] = {
    AgentName.MARKET: (
        "demand_level",
        "price_band",
        "competition_level",
        "sample_size",
    ),
    AgentName.COMPETITOR: ("competitors",),
    AgentName.PRICING: (
        "fixed_cost",
        "variable_rate",
        "profit",
        "margin",
        "break_even_price",
        "target_price",
        "assumptions",
    ),
    AgentName.COMPLIANCE: (
        "available",
        "missing",
        "needs_confirmation",
        "not_applicable",
        "risk_alerts",
        "disclaimer",
    ),
}


def _format_specialist_answer(result: AgentResult) -> str:
    fields = _ANSWER_FIELDS[result.agent]
    details = [
        f"{field}={json.dumps(result.data[field], ensure_ascii=False, default=str)}"
        for field in fields
        if field in result.data
    ]
    evidence = [record.summary for record in result.evidence if record.summary]
    sections = [result.summary]
    if details:
        sections.append("结果：" + "；".join(details))
    if evidence:
        sections.append("依据：" + "；".join(evidence))
    return " ".join(sections)


def _merge_task_obligations(
    existing: list[AgentTask], additions: list[AgentTask]
) -> list[AgentTask]:
    """Preserve stable task identity while accumulating newly discovered duties."""

    merged = list(existing)
    positions = {task.agent: index for index, task in enumerate(merged)}
    for addition in additions:
        position = positions.get(addition.agent)
        if position is None:
            positions[addition.agent] = len(merged)
            merged.append(addition)
            continue
        current = merged[position]
        required_fields = list(
            dict.fromkeys([*current.required_fields, *addition.required_fields])
        )
        merged[position] = current.model_copy(
            update={"required_fields": required_fields}
        )
    return merged


def build_workflow(dependencies: WorkflowDependencies) -> Any:
    """Compile the bounded orchestration graph with an in-process checkpointer."""

    start_coordinator = _StartCoordinator()

    async def supervisor_node(state: WorkflowGraphState) -> dict[str, Any]:
        validation = state.get("validation")
        gaps = validation.gaps if validation and validation.action == "replan" else None
        pending_input_tasks = state.get("pending_input_tasks", [])
        if pending_input_tasks and validation is None:
            plan_tasks = pending_input_tasks
            plan_market_entry_requested = False
        else:
            plan = await dependencies.supervisor.plan(state["request"], gaps=gaps)
            plan_tasks = plan.tasks
            plan_market_entry_requested = plan.market_entry_requested
        round_id = state.get("replan_count", 0)
        dispatch_sequence = state.get("dispatch_sequence", 0) + 1
        dispatch_plan = [
            task.model_copy(
                update={"task_id": f"dispatch-{dispatch_sequence}-{task.task_id}"}
            )
            for task in plan_tasks
        ]
        cumulative_tasks = _merge_task_obligations(
            state.get("tasks", []), plan_tasks
        )
        market_entry_requested = (
            state.get("market_entry_requested", False)
            or plan_market_entry_requested
        )
        writer = get_stream_writer()
        writer(_event(state, "intent_identified", "已识别分析意图。"))
        writer(
            _event(
                state,
                "agents_selected",
                "已选择所需专业 Agent。",
                {"agents": [task.agent.value for task in dispatch_plan]},
            )
        )
        return {
            "tasks": cumulative_tasks,
            "dispatch_tasks": dispatch_plan,
            "market_entry_requested": market_entry_requested,
            "round_id": round_id,
            "dispatch_sequence": dispatch_sequence,
            "validation": None,
            "pending_input_tasks": [],
        }

    async def run_agent_node(state: WorkflowGraphState) -> dict[str, Any]:
        task = state["task"]
        writer = get_stream_writer()
        writer(
            _event(
                state,
                "agent_started",
                f"{task.agent.value} Agent 已开始。",
                {"agent": task.agent.value, "task_id": task.task_id},
            )
        )
        await start_coordinator.wait(
            state["thread_id"], state["round_id"], state["round_size"]
        )
        try:
            agent = dependencies.agents[task.agent]
            result = await agent.run(task, state["request"].product)
        except Exception as exc:
            logger.warning(
                "Specialist execution failed thread_id=%s node=run_agent agent=%s "
                "exception_type=%s",
                state["thread_id"],
                task.agent.value,
                type(exc).__name__,
            )
            result = AgentResult(
                agent=task.agent,
                status="failed",
                summary=f"{task.agent.value} analysis failed.",
                errors=["specialist_execution_failed"],
            )
        writer(
            _event(
                state,
                "agent_completed",
                f"{task.agent.value} Agent 已结束。",
                {
                    "agent": task.agent.value,
                    "task_id": task.task_id,
                    "status": result.status,
                },
            )
        )
        return {"agent_results": {task.agent.value: result}}

    async def validate_node(state: WorkflowGraphState) -> dict[str, Any]:
        results = state.get("agent_results", {})
        decision = await dependencies.validator.validate(
            state["request"], state["tasks"], results
        )
        replan_count = state.get("replan_count", 0)
        if decision.action == "replan":
            if replan_count >= 2:
                decision = ValidationDecision(
                    action="fail",
                    gaps=[*decision.gaps, "replan limit reached"],
                )
            else:
                replan_count += 1
        get_stream_writer()(
            _event(
                state,
                "validation_completed",
                "结果校验完成。",
                {"action": decision.action},
            )
        )
        pending_input_tasks = (
            [
                task
                for task in state["tasks"]
                if (result := results.get(task.agent.value)) is not None
                and result.status == "need_input"
            ]
            if decision.action == "need_input"
            else []
        )
        return {
            "validation": decision,
            "replan_count": replan_count,
            "pending_input_tasks": pending_input_tasks,
        }

    def request_input_node(state: WorkflowGraphState) -> dict[str, Any]:
        validation = cast(ValidationDecision, state["validation"])
        supplied = interrupt(
            {
                "missing_fields": validation.missing_fields,
                "follow_up_question": validation.follow_up_question,
            }
        )
        if not isinstance(supplied, dict):
            raise TypeError("resume fields must be a mapping")
        product_payload = state["request"].product.model_dump()
        product_payload.update(supplied)
        product = ProductInput.model_validate(product_payload)
        request = state["request"].model_copy(update={"product": product})
        return {"request": request, "validation": None}

    async def answer_node(state: WorkflowGraphState) -> dict[str, Any]:
        if needs_strategy(
            state["tasks"],
            state["request"].question,
            state.get("market_entry_requested", False),
        ):
            recommendation = await dependencies.strategy.run(
                state["request"].question,
                state.get("agent_results", {}),
            )
            answer = recommendation.to_answer()
        else:
            selected = state["tasks"][0].agent.value
            answer = _format_specialist_answer(state["agent_results"][selected])
        writer = get_stream_writer()
        writer(_event(state, "answer_chunk", answer))
        writer(_event(state, "workflow_completed", "工作流已完成。"))
        return {"final_answer": answer}

    def fail_node(state: WorkflowGraphState) -> dict[str, Any]:
        get_stream_writer()(_event(state, "workflow_failed", "工作流无法完成。"))
        return {"final_answer": None}

    graph = StateGraph(WorkflowGraphState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("run_agent", run_agent_node)
    graph.add_node("validate", validate_node)
    graph.add_node("request_input", request_input_node)
    graph.add_node("answer", answer_node)
    graph.add_node("fail", fail_node)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", dispatch_tasks)
    graph.add_edge("run_agent", "validate")
    graph.add_conditional_edges("validate", route_after_validation)
    graph.add_edge("request_input", "supervisor")
    graph.add_edge("answer", END)
    graph.add_edge("fail", END)
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.contracts.api", "AnalysisRequest"),
            ("app.contracts.agents", "AgentName"),
            ("app.contracts.agents", "AgentTask"),
            ("app.contracts.agents", "AgentResult"),
            ("app.contracts.agents", "ValidationDecision"),
        ]
    )
    return graph.compile(checkpointer=InMemorySaver(serde=serializer))
