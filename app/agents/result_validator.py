from __future__ import annotations

import json
from collections.abc import Mapping

from app.agents.supervisor import StructuredLLM
from app.contracts.agents import AgentResult, AgentTask, ValidationDecision
from app.contracts.api import AnalysisRequest


class ResultValidator:
    """Apply non-negotiable coverage checks before optional semantic review."""

    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    async def validate(
        self,
        request: AnalysisRequest,
        tasks: list[AgentTask],
        results: Mapping[str, AgentResult],
    ) -> ValidationDecision:
        deterministic = self._deterministic_decision(tasks, results)
        if deterministic.action != "pass":
            return deterministic
        return await self._llm.generate_json(
            ValidationDecision,
            [
                {
                    "role": "system",
                    "content": (
                        "Check semantic completeness of the supplied CrossPilot results. "
                        "Return pass, need_input, replan, or fail. Never treat missing data "
                        "or evidence as success. Identify omitted requested dimensions and "
                        "conflicting specialist conclusions.\nJSON Schema: "
                        f"{json.dumps(ValidationDecision.model_json_schema(), ensure_ascii=False)}"
                    ),
                },
                {
                    "role": "user",
                    "content": self._review_payload(request, tasks, results),
                },
            ],
        )

    @staticmethod
    def _deterministic_decision(
        tasks: list[AgentTask],
        results: Mapping[str, AgentResult],
    ) -> ValidationDecision:
        if not tasks:
            return ValidationDecision(
                action="fail",
                gaps=["no relevant specialist task was selected"],
            )

        missing_fields: list[str] = []
        gaps: list[str] = []
        for task in tasks:
            result = results.get(task.agent.value)
            if result is None:
                gaps.append(f"{task.agent.value}: result missing")
                continue
            if result.status == "need_input":
                if result.missing_fields:
                    missing_fields.extend(result.missing_fields)
                else:
                    gaps.append(f"{task.agent.value}: need_input missing field list")
                continue
            if result.status == "failed":
                gaps.append(f"{task.agent.value}: specialist failed")
                continue
            missing_required = [
                field for field in task.required_fields if result.data.get(field) is None
            ]
            if missing_required:
                gaps.append(
                    f"{task.agent.value}: missing fields {', '.join(missing_required)}"
                )
            if not result.evidence:
                gaps.append(f"{task.agent.value}: missing evidence")
            elif not any(evidence.rows for evidence in result.evidence):
                gaps.append(f"{task.agent.value}: evidence rows empty")

        if missing_fields:
            unique_fields = list(dict.fromkeys(missing_fields))
            return ValidationDecision(
                action="need_input",
                missing_fields=unique_fields,
                follow_up_question=f"请补充以下信息：{', '.join(unique_fields)}",
            )
        if gaps:
            if any("need_input missing field list" in gap for gap in gaps):
                return ValidationDecision(action="fail", gaps=gaps)
            return ValidationDecision(action="replan", gaps=gaps)
        return ValidationDecision(action="pass")

    @staticmethod
    def _review_payload(
        request: AnalysisRequest,
        tasks: list[AgentTask],
        results: Mapping[str, AgentResult],
    ) -> str:
        task_json = [task.model_dump(mode="json") for task in tasks]
        result_json = {
            name: result.model_dump(mode="json") for name, result in results.items()
        }
        return (
            f"Original request: {request.model_dump(mode='json')}\n"
            f"Full tasks: {task_json}\nResults: {result_json}"
        )
