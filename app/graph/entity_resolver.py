from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.graph.schema_registry import SchemaRegistry
from app.services.embedding import EmbeddingService

_VECTOR_QUERY = """
CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
YIELD node, score
WITH node, score, head([item IN labels(node) WHERE item IN $labels]) AS label
WHERE label IS NOT NULL
RETURN coalesce(node.product_id, node.category_id, node.brand_id) AS entity_id,
       label,
       coalesce(node.name, node.title_en) AS display_name,
       score
ORDER BY score DESC
LIMIT $top_k
""".strip()

_PRODUCT_INDEX_QUERY = """
UNWIND $rows AS row
MATCH (product:Product {product_id: row.product_id})
SET product.name_embedding = row.embedding
RETURN count(product) AS updated
""".strip()


class EntityMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    label: str
    display_name: str
    score: float = Field(ge=-1, le=1)
    needs_confirmation: bool


class EntityGraphClient(Protocol):
    async def execute_read(
        self, cypher: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]: ...

    async def execute_write(
        self, cypher: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]: ...


class EntityResolver:
    def __init__(
        self,
        client: EntityGraphClient,
        embedding: EmbeddingService,
        *,
        schema: SchemaRegistry,
        score_threshold: float = 0.75,
        index_name: str = "product_name_embeddings",
    ) -> None:
        if not 0 <= score_threshold <= 1:
            raise ValueError("score_threshold must be between 0 and 1")
        self.client = client
        self.embedding = embedding
        self.schema = schema
        self.score_threshold = score_threshold
        self.index_name = index_name

    async def resolve(
        self, text: str, labels: Sequence[str], top_k: int = 5
    ) -> list[EntityMatch]:
        unknown = sorted(set(labels) - self.schema.labels)
        if unknown:
            raise ValueError(f"unknown entity labels: {', '.join(unknown)}")
        if not labels:
            raise ValueError("at least one entity label is required")
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        rows = await self.client.execute_read(
            _VECTOR_QUERY,
            {
                "index_name": self.index_name,
                "top_k": top_k,
                "embedding": self.embedding.embed_query(text),
                "labels": list(labels),
            },
        )
        return [
            EntityMatch(
                entity_id=str(row["entity_id"]),
                label=str(row["label"]),
                display_name=str(row["display_name"]),
                score=float(row["score"]),
                needs_confirmation=float(row["score"]) < self.score_threshold,
            )
            for row in rows[:top_k]
        ]

    async def index_products(self, records: Sequence[Mapping[str, Any]]) -> int:
        embedded = self.embedding.embed_entities(records)
        rows: list[dict[str, Any]] = []
        for record in embedded:
            product_id = str(record.get("product_id", "")).strip()
            if not product_id:
                raise ValueError("each product embedding requires product_id")
            rows.append({"product_id": product_id, "embedding": record["embedding"]})
        if not rows:
            return 0
        result = await self.client.execute_write(_PRODUCT_INDEX_QUERY, {"rows": rows})
        return int(result[0]["updated"]) if result else 0
