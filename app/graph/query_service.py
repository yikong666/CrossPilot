from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from app.contracts.graph import EvidenceRecord
from app.core.errors import GraphQueryError, StructuredOutputError
from app.graph.cypher_generator import GeneratedCypher
from app.graph.cypher_validator import CypherValidator
from app.graph.entity_resolver import EntityMatch, EntityResolver
from app.graph.schema_registry import SchemaRegistry


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


class GraphQueryService:
    max_attempts = 3
    max_rows = 50

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
        entity_matches = await self._resolve(entity_hint)
        feedback: str | None = None
        last_error: Exception | None = None
        for _attempt in range(1, self.max_attempts + 1):
            try:
                generated = await self.generator.generate(
                    question,
                    [match.model_dump() for match in entity_matches],
                    purpose,
                    feedback,
                )
            except StructuredOutputError as exc:
                raise GraphQueryError(str(exc)) from exc
            try:
                if generated.purpose != purpose:
                    raise GraphQueryError(
                        f"Generated purpose {generated.purpose!r} does not match {purpose!r}"
                    )
                self.validator.validate(generated.cypher, generated.params)
                await self.client.explain(generated.cypher, generated.params)
                rows = await self.client.execute_read(generated.cypher, generated.params)
            except Exception as exc:
                last_error = exc
                feedback = f"{type(exc).__name__}: {exc}. Schema: {self.schema.compact_text()}"
                continue
            clean_rows, node_ids, relationship_ids = _sanitize_rows(rows[: self.max_rows])
            return EvidenceRecord(
                source_type="neo4j",
                summary=f"{purpose} graph query returned {len(clean_rows)} rows",
                cypher=generated.cypher,
                params=generated.params,
                rows=clean_rows,
                graph_node_ids=node_ids,
                graph_edge_ids=relationship_ids,
            )
        detail = str(last_error) if last_error is not None else "unknown query failure"
        raise GraphQueryError(f"Graph query failed after {self.max_attempts} attempts: {detail}")

    async def _resolve(self, entity_hint: str | None) -> list[EntityMatch]:
        if not entity_hint or self.entity_resolver is None:
            return []
        return await self.entity_resolver.resolve(
            entity_hint,
            ["Product", "Category", "Brand"],
            top_k=5,
        )


def _sanitize_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    clean_rows: list[dict[str, Any]] = []
    node_ids: list[str] = []
    relationship_ids: list[str] = []
    for row in rows:
        _extend_ids(node_ids, row.get("__node_ids"))
        _extend_ids(relationship_ids, row.get("__relationship_ids"))
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if key.startswith("_"):
                continue
            _collect_graph_ids(value, node_ids, relationship_ids)
            clean[key] = _sanitize_value(value)
        clean_rows.append(clean)
    return clean_rows, list(dict.fromkeys(node_ids)), list(dict.fromkeys(relationship_ids))


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


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping) or hasattr(value, "items"):
        return {
            str(key): _sanitize_value(nested)
            for key, nested in dict(value).items()
            if not str(key).startswith("_")
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_value(item) for item in value]
    return str(value)
