from __future__ import annotations

from typing import Any

from langgraph.types import Send

from app.contracts.agents import AgentTask, ValidationDecision


def dispatch_tasks(state: dict[str, Any]) -> list[Send] | str:
    tasks: list[AgentTask] = state.get("tasks", [])
    if not tasks:
        return "fail"
    return [
        Send(
            "run_agent",
            {
                "task": task,
                "request": state["request"],
                "trace_id": state["trace_id"],
                "thread_id": state["thread_id"],
            },
        )
        for task in tasks
    ]


def route_after_validation(state: dict[str, Any]) -> str:
    validation: ValidationDecision = state["validation"]
    if validation.action == "need_input":
        return "request_input"
    if validation.action == "replan":
        return "supervisor"
    if validation.action == "pass":
        return "answer"
    return "fail"


def needs_strategy(
    tasks: list[AgentTask],
    question: str,
    market_entry_requested: bool,
) -> bool:
    selected = {task.agent for task in tasks}
    explicitly_asks_entry = "进入" in question or "enter" in question.casefold()
    return len(selected) >= 2 or market_entry_requested or explicitly_asks_entry
