from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import pytest

from app.services.fee_rule_repository import FeeRuleNotFoundError, FeeRuleRepository


class RecordingReadClient:
    """Narrow client double: keeps the repository's executed query boundary observable."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.cypher: str | None = None
        self.params: dict[str, Any] | None = None

    async def execute_read(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.cypher = cypher
        self.params = params
        return self.rows


@pytest.mark.asyncio
async def test_reads_one_matching_us_amazon_rule_with_parameterized_query() -> None:
    """Fails if the repository stops filtering for US Amazon or interpolates user inputs."""
    client = RecordingReadClient(
        [
            {
                "commission_rate": "0.15",
                "fba_fee_usd": "4.00",
                "fx_cny_per_usd": "7.00",
                "fee_id": "electronics-lightweight",
            }
        ]
    )

    rule = await FeeRuleRepository(client).get_rule("consumer_electronics", Decimal("0.42"))

    assert rule.commission_rate == Decimal("0.1500")
    assert rule.fba_fee_usd == Decimal("4.00")
    assert rule.fx_cny_per_usd == Decimal("7.00")
    assert client.params == {"category": "consumer_electronics", "weight_kg": Decimal("0.42")}
    assert client.cypher is not None
    assert "amazon_us" in client.cypher
    assert "$category" in client.cypher
    assert "$weight_kg" in client.cypher


@pytest.mark.asyncio
async def test_missing_fee_rule_lists_fields_instead_of_inventing_defaults() -> None:
    """Fails if an unmatched graph query returns a silently populated fee rule."""
    with pytest.raises(FeeRuleNotFoundError) as raised:
        await FeeRuleRepository(RecordingReadClient([])).get_rule("home_goods", Decimal("1.0"))

    assert raised.value.fields == ("commission_rate", "fba_fee_usd", "fx_cny_per_usd")


@pytest.mark.asyncio
async def test_rejects_non_unique_effective_rule() -> None:
    """Fails if ambiguous graph data arbitrarily selects one of multiple fee rules."""
    rows = [
        {"commission_rate": "0.15", "fba_fee_usd": "4", "fx_cny_per_usd": "7"},
        {"commission_rate": "0.16", "fba_fee_usd": "5", "fx_cny_per_usd": "7"},
    ]

    with pytest.raises(FeeRuleNotFoundError, match="Multiple"):
        await FeeRuleRepository(RecordingReadClient(rows)).get_rule("home_goods", Decimal("1.0"))


@pytest.mark.asyncio
async def test_real_neo4j_rule_lookup_when_explicitly_configured() -> None:
    """Exercise the configured Neo4j repository path; never use a fake substitute."""
    uri = os.environ.get("CROSSPILOT_TEST_NEO4J_URI")
    user = os.environ.get("CROSSPILOT_TEST_NEO4J_USER")
    password = os.environ.get("CROSSPILOT_TEST_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("real Neo4j credentials are not configured for this test")

    from neo4j import AsyncGraphDatabase

    class Neo4jReadClient:
        def __init__(self) -> None:
            self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

        async def execute_read(
            self, cypher: str, params: dict[str, Any]
        ) -> list[dict[str, Any]]:
            async with self.driver.session() as session:
                result = await session.run(cypher, params)
                return [record.data() async for record in result]

        async def close(self) -> None:
            await self.driver.close()

    client = Neo4jReadClient()
    try:
        rule = await FeeRuleRepository(client).get_rule("consumer_electronics", Decimal("0.42"))
    finally:
        await client.close()

    assert rule.commission_rate >= Decimal("0")
    assert rule.fba_fee_usd >= Decimal("0")
    assert rule.fx_cny_per_usd > Decimal("0")
