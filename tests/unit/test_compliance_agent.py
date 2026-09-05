from __future__ import annotations

import pytest

from app.agents.compliance import ComplianceAgent
from app.contracts import AgentName, AgentTask, EvidenceRecord, ProductInput


def _task() -> AgentTask:
    return AgentTask(
        task_id="compliance-1",
        agent=AgentName.COMPLIANCE,
        objective="Prepare compliance materials for Amazon US launch.",
    )


def _product(**overrides: object) -> ProductInput:
    values: dict[str, object] = {
        "name": "Toddler night light",
        "category": "children_toys",
        "purchase_cost_cny": "20.00",
        "has_battery": True,
        "intended_age": "3+",
        "provided_documents": ["CPC Certificate", "Battery Test Report"],
    }
    values.update(overrides)
    return ProductInput.model_validate(values)


class QueryService:
    def __init__(
        self, evidence: EvidenceRecord | None = None, error: Exception | None = None
    ) -> None:
        self.evidence = evidence
        self.error = error

    async def query(self, question: str, entity_hint: str | None, purpose: str) -> EvidenceRecord:
        if self.error is not None:
            raise self.error
        assert "children_toys" in question
        assert entity_hint == "children_toys"
        assert purpose == "compliance"
        assert self.evidence is not None
        return self.evidence


def _evidence(rows: list[dict[str, object]]) -> EvidenceRecord:
    return EvidenceRecord(
        source_type="neo4j",
        summary="compliance graph query returned rules",
        rows=rows,
    )


@pytest.mark.anyio
async def test_compliance_agent_groups_documents_and_adds_safety_disclaimer() -> None:
    """Misclassifying normalized documents or dropping the disclaimer must fail this test."""
    evidence = _evidence(
        [
            {
                "document": "CPC certificate",
                "applicability": "required",
                "risk_alert": "Toy age grading must match labeling.",
            },
            {"document": "battery-test_report", "applicability": "required"},
            {"document": "Tracking label", "applicability": "needs_confirmation"},
            {"document": "FCC filing", "applicability": "not_applicable"},
        ]
    )

    result = await ComplianceAgent(QueryService(evidence)).run(_task(), _product())

    assert result.status == "success"
    assert result.data["available"] == ["CPC certificate", "battery-test_report"]
    assert result.data["missing"] == []
    assert result.data["needs_confirmation"] == ["Tracking label"]
    assert result.data["not_applicable"] == ["FCC filing"]
    assert result.data["risk_alerts"] == ["Toy age grading must match labeling."]
    assert result.data["disclaimer"] == "非法律意见，不替代厂家认证。"
    assert result.evidence == [evidence]


@pytest.mark.anyio
async def test_compliance_agent_marks_required_unprovided_document_as_missing() -> None:
    """Treating an absent required material as available must fail this test."""
    evidence = _evidence(
        [{"required_document": "ASTM F963 test report", "applicability": "required"}]
    )

    result = await ComplianceAgent(QueryService(evidence)).run(_task(), _product())

    assert result.status == "success"
    assert result.data["available"] == []
    assert result.data["missing"] == ["ASTM F963 test report"]


@pytest.mark.anyio
async def test_compliance_agent_fails_for_empty_graph_data() -> None:
    """Inventing compliance rules for an empty query must fail this test."""
    result = await ComplianceAgent(QueryService(_evidence([]))).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["No compliance rules or material requirements were found."]


@pytest.mark.anyio
async def test_compliance_agent_fails_for_graph_dependency_error() -> None:
    """Leaking graph implementation details to the user must fail this test."""
    result = await ComplianceAgent(QueryService(error=RuntimeError("query failed"))).run(
        _task(), _product()
    )

    assert result.status == "failed"
    assert result.errors == ["compliance_query_failed"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "rows",
    [
        [{"unexpected": "value"}],
        [{}],
        [{"document": "", "risk_alert": "   "}],
        [{"required_document": " \t", "risk": None}],
        [{"material": 123, "risk_rule": ["Unparsed rule"]}],
        [{"applicability": "not_applicable"}],
    ],
)
async def test_compliance_agent_rejects_rows_without_usable_rules(
    rows: list[dict[str, object]],
) -> None:
    """Nonempty query rows cannot establish success without parsed materials or risks."""
    evidence = _evidence(rows)

    result = await ComplianceAgent(QueryService(evidence)).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["compliance_data_incomplete"]
    assert result.evidence == [evidence]
    assert result.data == {}


@pytest.mark.anyio
@pytest.mark.parametrize("risk_key", ["risk_alert", "risk_warning", "risk", "risk_rule"])
async def test_compliance_agent_accepts_explicit_risk_without_documents(risk_key: str) -> None:
    """A supported nonblank risk rule remains useful when it has no material requirement."""
    evidence = _evidence([{risk_key: "  Verify transport labeling with manufacturer.  "}])

    result = await ComplianceAgent(QueryService(evidence)).run(_task(), _product())

    assert result.status == "success"
    assert result.data["risk_alerts"] == ["Verify transport labeling with manufacturer."]
    for group in ("available", "missing", "needs_confirmation", "not_applicable"):
        assert result.data[group] == []
    assert result.evidence == [evidence]


@pytest.mark.anyio
async def test_compliance_agent_accepts_explicit_not_applicable_material() -> None:
    """A graph-backed nonapplicability result must not be mistaken for empty evidence."""
    evidence = _evidence([{"document_name": "FCC filing", "not_applicable": True}])

    result = await ComplianceAgent(QueryService(evidence)).run(_task(), _product())

    assert result.status == "success"
    assert result.data["not_applicable"] == ["FCC filing"]
    assert result.data["missing"] == []
    assert result.evidence == [evidence]


@pytest.mark.anyio
async def test_compliance_agent_preserves_valid_material_among_unusable_rows() -> None:
    """Unrecognized rows cannot erase usable evidence or create invented requirements."""
    evidence = _evidence(
        [
            {"unexpected": "value"},
            {"document": " ", "risk": ""},
            {"material": "Tracking label", "applicability": "needs_confirmation"},
        ]
    )

    result = await ComplianceAgent(QueryService(evidence)).run(_task(), _product())

    assert result.status == "success"
    assert result.data["needs_confirmation"] == ["Tracking label"]
    for group in ("available", "missing", "not_applicable", "risk_alerts"):
        assert result.data[group] == []
    assert result.evidence == [evidence]
