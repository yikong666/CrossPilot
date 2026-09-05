"""Compliance-preparation specialist that reports graph-backed material gaps."""

from __future__ import annotations

import re
from typing import Any, Protocol

from app.contracts import AgentName, AgentResult, AgentTask, EvidenceRecord, ProductInput


class ComplianceQueryService(Protocol):
    async def query(
        self, question: str, entity_hint: str | None, purpose: str
    ) -> EvidenceRecord: ...


_DISCLAIMER = "非法律意见，不替代厂家认证。"
_DOCUMENT_KEYS = ("document", "required_document", "document_name", "material")
_RISK_KEYS = ("risk_alert", "risk_warning", "risk", "risk_rule")


class ComplianceAgent:
    """Compare normalized user documents with controlled graph requirements."""

    def __init__(self, query_service: ComplianceQueryService) -> None:
        self._query_service = query_service

    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        question = _query_question(task, product)
        try:
            evidence = await self._query_service.query(
                question=question,
                entity_hint=product.category,
                purpose="compliance",
            )
        except Exception:
            return AgentResult(
                agent=AgentName.COMPLIANCE,
                status="failed",
                summary="Compliance preparation could not query approved graph rules.",
                errors=["compliance_query_failed"],
            )

        if not evidence.rows:
            return AgentResult(
                agent=AgentName.COMPLIANCE,
                status="failed",
                summary="No graph-backed compliance requirements are available.",
                evidence=[evidence],
                errors=["No compliance rules or material requirements were found."],
            )

        data: dict[str, Any] = _group_requirements(evidence.rows, product.provided_documents)
        if not any(data.values()):
            return AgentResult(
                agent=AgentName.COMPLIANCE,
                status="failed",
                summary="Graph rows contain no usable compliance materials or risk rules.",
                evidence=[evidence],
                errors=["compliance_data_incomplete"],
            )
        data["disclaimer"] = _DISCLAIMER
        return AgentResult(
            agent=AgentName.COMPLIANCE,
            status="success",
            summary=f"Compliance preparation completed from graph-backed rules. {_DISCLAIMER}",
            data=data,
            evidence=[evidence],
        )


def _query_question(task: AgentTask, product: ProductInput) -> str:
    material = product.material or "unknown"
    intended_age = product.intended_age or "unknown"
    return (
        f"{task.objective} Category: {product.category}; material: {material}; "
        f"has_battery: {product.has_battery}; intended_age: {intended_age}."
    )


def _group_requirements(
    rows: list[dict[str, Any]], provided_documents: list[str]
) -> dict[str, list[str]]:
    provided = {_normalize(document) for document in provided_documents}
    groups: dict[str, list[str]] = {
        "available": [],
        "missing": [],
        "needs_confirmation": [],
        "not_applicable": [],
        "risk_alerts": [],
    }
    for row in rows:
        _append_risks(groups["risk_alerts"], row)
        document = _document_name(row)
        if document is None:
            continue
        applicability = str(row.get("applicability", "required")).casefold().strip()
        if row.get("not_applicable") is True or applicability in {
            "not_applicable",
            "not applicable",
        }:
            groups["not_applicable"].append(document)
        elif applicability in {"needs_confirmation", "needs confirmation", "conditional"}:
            groups["needs_confirmation"].append(document)
        elif _normalize(document) in provided:
            groups["available"].append(document)
        else:
            groups["missing"].append(document)
    return groups


def _document_name(row: dict[str, Any]) -> str | None:
    for key in _DOCUMENT_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _append_risks(target: list[str], row: dict[str, Any]) -> None:
    for key in _RISK_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in target:
            target.append(value.strip())


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]", "", value, flags=re.UNICODE).casefold()
