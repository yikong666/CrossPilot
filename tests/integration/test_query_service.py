import os
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.core.errors import GraphQueryError
from app.graph.client import Neo4jClient
from app.graph.cypher_generator import GeneratedCypher
from app.graph.cypher_validator import CypherValidator
from app.graph.entity_resolver import EntityResolver
from app.graph.query_service import GraphQueryService
from app.graph.schema_registry import SchemaRegistry


class FakeEmbedding:
    def embed_query(self, text: str) -> list[float]:
        assert text
        return [1.0] + [0.0] * 1023

    def embed_entities(self, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                **record,
                "embedding_text": "Example | Example title | Brand | Alias",
                "embedding": [1.0] + [0.0] * 1023,
            }
            for record in records
        ]


class FakeGraphClient:
    def __init__(self) -> None:
        self.explained: list[tuple[str, dict[str, Any]]] = []
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.written: list[tuple[str, dict[str, Any]]] = []

    async def explain(self, cypher: str, params: Mapping[str, Any]) -> None:
        self.explained.append((cypher, dict(params)))

    async def execute_read(
        self, cypher: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        self.executed.append((cypher, dict(params)))
        if "db.index.vector.queryNodes" in cypher:
            return [
                {
                    "entity_id": "product-1",
                    "label": "Product",
                    "display_name": "Example",
                    "score": 0.81,
                }
            ]
        return [
            {
                "product_id": f"p-{index}",
                "name": f"Product {index}",
                "__node_ids": [f"node-{index}"],
                "__relationship_ids": [f"rel-{index}"],
            }
            for index in range(60)
        ]

    async def execute_write(
        self, cypher: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        self.written.append((cypher, dict(params)))
        return [{"updated": len(params["rows"])}]


class SequenceGenerator:
    def __init__(self, outputs: list[GeneratedCypher]) -> None:
        self.outputs = iter(outputs)
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        question: str,
        entity_matches: Sequence[Mapping[str, Any]],
        purpose: str,
        feedback: str | None = None,
    ) -> GeneratedCypher:
        self.calls.append(
            {
                "question": question,
                "entity_matches": list(entity_matches),
                "purpose": purpose,
                "feedback": feedback,
            }
        )
        return next(self.outputs)


def make_service(
    graph: FakeGraphClient,
    generator: SequenceGenerator,
) -> GraphQueryService:
    schema = SchemaRegistry()
    resolver = EntityResolver(graph, FakeEmbedding(), schema=schema, score_threshold=0.75)
    return GraphQueryService(
        client=graph,
        entity_resolver=resolver,
        generator=generator,
        validator=CypherValidator(schema),
        schema=schema,
    )


@pytest.mark.asyncio
async def test_entity_resolver_marks_low_confidence_and_indexes_product_text() -> None:
    graph = FakeGraphClient()
    schema = SchemaRegistry()
    resolver = EntityResolver(graph, FakeEmbedding(), schema=schema, score_threshold=0.85)

    matches = await resolver.resolve("Example", ["Product"], top_k=5)
    updated = await resolver.index_products(
        [
            {
                "product_id": "product-1",
                "name": "Example",
                "title_en": "Example title",
                "brand": "Brand",
                "aliases": ["Alias"],
            }
        ]
    )

    assert matches[0].entity_id == "product-1"
    assert matches[0].needs_confirmation is True
    assert graph.executed[0][1]["index_name"] == "product_name_embeddings"
    assert len(graph.executed[0][1]["embedding"]) == 1024
    assert updated == 1
    indexed_row = graph.written[0][1]["rows"][0]
    assert len(indexed_row["embedding"]) == 1024


@pytest.mark.asyncio
async def test_invalid_cypher_is_never_explained_or_executed_then_repairs_to_evidence() -> None:
    graph = FakeGraphClient()
    generator = SequenceGenerator(
        [
            GeneratedCypher(
                cypher="MATCH (c:Customer) RETURN c LIMIT 1",
                params={},
                purpose="market",
            ),
            GeneratedCypher(
                cypher=(
                    "MATCH (p:Product) WHERE p.product_id = $product_id "
                    "RETURN p.product_id AS product_id, p.name AS name LIMIT 50"
                ),
                params={"product_id": "product-1"},
                purpose="market",
            ),
        ]
    )
    service = make_service(graph, generator)

    evidence = await service.query("分析市场", entity_hint="Example", purpose="market")

    assert len(generator.calls) == 2
    assert "Unknown label: Customer" in generator.calls[1]["feedback"]
    assert len(graph.explained) == 1
    assert len([call for call in graph.executed if "vector.queryNodes" not in call[0]]) == 1
    assert len(evidence.rows) == 50
    assert evidence.rows[0] == {"product_id": "p-0", "name": "Product 0"}
    assert evidence.graph_node_ids == [f"node-{index}" for index in range(50)]
    assert evidence.graph_edge_ids == [f"rel-{index}" for index in range(50)]


@pytest.mark.asyncio
async def test_query_generation_stops_after_exactly_three_invalid_attempts() -> None:
    graph = FakeGraphClient()
    invalid = GeneratedCypher(
        cypher="MATCH (c:Customer) RETURN c LIMIT 1", params={}, purpose="market"
    )
    generator = SequenceGenerator([invalid, invalid, invalid, invalid])
    service = make_service(graph, generator)

    with pytest.raises(GraphQueryError, match="after 3 attempts"):
        await service.query("分析市场", entity_hint=None, purpose="market")

    assert len(generator.calls) == 3
    assert graph.explained == []
    assert graph.executed == []


@pytest.mark.asyncio
async def test_real_neo4j_repairs_invalid_generation_when_test_database_is_configured() -> None:
    uri = os.getenv("CROSSPILOT_TEST_NEO4J_URI")
    user = os.getenv("CROSSPILOT_TEST_NEO4J_USER")
    password = os.getenv("CROSSPILOT_TEST_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("real Neo4j test credentials are not configured")

    client = Neo4jClient(str(uri), str(user), str(password))
    schema = SchemaRegistry()
    generator = SequenceGenerator(
        [
            GeneratedCypher(cypher="DELETE p", params={}, purpose="market"),
            GeneratedCypher(
                cypher=(
                    "MATCH (p:Product) RETURN p.product_id AS product_id, "
                    "elementId(p) AS __node_ids LIMIT 1"
                ),
                params={},
                purpose="market",
            ),
        ]
    )
    service = GraphQueryService(
        client=client,
        entity_resolver=None,
        generator=generator,
        validator=CypherValidator(schema),
        schema=schema,
    )
    try:
        assert await client.health_check() is True
        evidence = await service.query("分析市场", entity_hint=None, purpose="market")
        assert evidence.source_type == "neo4j"
        assert len(generator.calls) == 2
    finally:
        await client.close()
