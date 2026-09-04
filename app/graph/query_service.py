from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from app.contracts.graph import EvidenceRecord
from app.core.errors import GraphQueryError
from app.graph.cypher_generator import GeneratedCypher
from app.graph.cypher_validator import CypherValidator
from app.graph.entity_resolver import EntityMatch, EntityResolver
from app.graph.schema_registry import SchemaRegistry

logger = logging.getLogger(__name__)

_FAILURE_CODES = {
    "entity_resolution": "entity_resolution_failed",
    "generation": "generation_failed",
    "purpose_validation": "purpose_validation_failed",
    "static_validation": "static_validation_failed",
    "explain": "explain_failed",
    "execution": "query_failed",
}


class QueryGraphClient(Protocol):
    async def explain(self, cypher: str, params: Mapping[str, Any]) -> None: ...

    async def execute_read(
        self, cypher: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]: ...


class QueryGenerator(Protocol):
    async def generate(
        self,
        question: str,
        entity_matches: Sequence[Mapping[str, Any]],
        purpose: str,
        feedback: str | None = None,
    ) -> GeneratedCypher: ...


@dataclass(frozen=True)
class QueryAttemptFailure:
    attempt: int
    stage: str
    code: str


class GraphQueryExecutionError(GraphQueryError):
    def __init__(self, failures: Sequence[QueryAttemptFailure]) -> None:
        self.failures = tuple(failures)
        super().__init__("Graph query could not be completed safely")


class GraphQueryService:
    max_attempts = 3
    max_rows = 50
    max_graph_nodes = 40
    max_graph_relationships = 80

    def __init__(
        self,
        *,
        client: QueryGraphClient,
        entity_resolver: EntityResolver | None,
        generator: QueryGenerator,
        validator: CypherValidator,
        schema: SchemaRegistry,
    ) -> None:
        self.client = client
        self.entity_resolver = entity_resolver
        self.generator = generator
        self.validator = validator
        self.schema = schema

    async def query(
        self,
        question: str,
        entity_hint: str | None,
        purpose: str,
    ) -> EvidenceRecord:
        resolution_failure: QueryAttemptFailure | None = None
        try:
            entity_matches = await self._resolve(entity_hint)
        except Exception:
            resolution_failure = _safe_failure(0, "entity_resolution")
            entity_matches = []
        if resolution_failure is not None:
            raise GraphQueryExecutionError([resolution_failure])
        if entity_matches and entity_matches[0].needs_confirmation:
            raise GraphQueryError("Entity confirmation is required before graph query")
        feedback: str | None = None
        failures: list[QueryAttemptFailure] = []
        for attempt in range(1, self.max_attempts + 1):
            generation_failure: QueryAttemptFailure | None = None
            try:
                generated = await self.generator.generate(
                    question,
                    [match.model_dump() for match in entity_matches],
                    purpose,
                    feedback,
                )
            except Exception:
                generation_failure = _safe_failure(attempt, "generation")
            if generation_failure is not None:
                raise GraphQueryExecutionError([generation_failure])
            stage = "purpose_validation"
            try:
                if generated.purpose != purpose:
                    raise GraphQueryError(
                        f"Generated purpose {generated.purpose!r} does not match {purpose!r}"
                    )
                stage = "static_validation"
                validated = self.validator.validate(generated.cypher, generated.params)
                stage = "explain"
                await self.client.explain(generated.cypher, generated.params)
                stage = "execution"
                rows = await self.client.execute_read(generated.cypher, generated.params)
            except Exception:
                failure = _safe_failure(attempt, stage)
                failures.append(failure)
                feedback = (
                    f"{failure.code}. Generate a corrected query using only this schema: "
                    f"{self.schema.compact_text()}"
                )
                continue
            clean_rows, node_ids, relationship_ids, truncated = _sanitize_rows(
                rows,
                validated.return_fields,
                max_rows=self.max_rows,
                max_nodes=self.max_graph_nodes,
                max_relationships=self.max_graph_relationships,
            )
            summary = f"{purpose} graph query returned {len(clean_rows)} rows"
            if truncated:
                summary += " (truncated to evidence limits)"
            return EvidenceRecord(
                source_type="neo4j",
                summary=summary,
                cypher=generated.cypher,
                params=generated.params,
                rows=clean_rows,
                graph_node_ids=node_ids,
                graph_edge_ids=relationship_ids,
            )
        raise GraphQueryExecutionError(failures)

    async def _resolve(self, entity_hint: str | None) -> list[EntityMatch]:
        if not entity_hint or self.entity_resolver is None:
            return []
        return await self.entity_resolver.resolve(
            entity_hint,
            ["Product", "Category", "Brand"],
            top_k=5,
        )


def _safe_failure(attempt: int, stage: str) -> QueryAttemptFailure:
    code = _FAILURE_CODES[stage]
    logger.warning(
        "graph_query_failure attempt=%d stage=%s code=%s",
        attempt,
        stage,
        code,
    )
    return QueryAttemptFailure(attempt=attempt, stage=stage, code=code)


def _sanitize_rows(
    rows: Sequence[Mapping[str, Any]],
    allowed_fields: frozenset[str],
    *,
    max_rows: int,
    max_nodes: int,
    max_relationships: int,
) -> tuple[list[dict[str, Any]], list[str], list[str], bool]:
    clean_rows: list[dict[str, Any]] = []
    node_ids: list[str] = []
    relationship_ids: list[str] = []
    for row in rows[:max_rows]:
        _extend_ids(node_ids, row.get("__node_ids"))
        _extend_ids(relationship_ids, row.get("__relationship_ids"))
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if key.startswith("_") or key not in allowed_fields:
                continue
            _collect_graph_ids(value, node_ids, relationship_ids)
            sanitized = _sanitize_value(value)
            if sanitized is not _DROP:
                clean[key] = sanitized
        clean_rows.append(clean)
    unique_nodes = list(dict.fromkeys(node_ids))
    unique_relationships = list(dict.fromkeys(relationship_ids))
    truncated = (
        len(rows) > max_rows
        or len(unique_nodes) > max_nodes
        or len(unique_relationships) > max_relationships
    )
    return (
        clean_rows,
        unique_nodes[:max_nodes],
        unique_relationships[:max_relationships],
        truncated,
    )


def _extend_ids(target: list[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, (str, int)):
        target.append(str(value))
        return
    if isinstance(value, Sequence):
        target.extend(str(item) for item in value)


def _collect_graph_ids(value: Any, node_ids: list[str], relationship_ids: list[str]) -> None:
    element_id = getattr(value, "element_id", None)
    if element_id is not None:
        target = relationship_ids if hasattr(value, "start_node") else node_ids
        target.append(str(element_id))
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_graph_ids(nested, node_ids, relationship_ids)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _collect_graph_ids(nested, node_ids, relationship_ids)


_DROP = object()


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping) or hasattr(value, "items"):
        return _DROP
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sanitized = [_sanitize_value(item) for item in value]
        return _DROP if any(item is _DROP for item in sanitized) else sanitized
    return _DROP
