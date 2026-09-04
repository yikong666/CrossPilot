from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction

from app.core.config import get_settings


class Neo4jClient:
    """Application-scoped async Neo4j driver with governed read helpers."""

    _shared: ClassVar[Neo4jClient | None] = None

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        database: str | None = None,
        driver: AsyncDriver | None = None,
    ) -> None:
        self.database = database
        self._driver = driver or AsyncGraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def shared(cls) -> Neo4jClient:
        if cls._shared is None:
            settings = get_settings()
            cls._shared = cls(
                settings.neo4j_uri,
                settings.neo4j_user,
                settings.neo4j_password.get_secret_value(),
            )
        return cls._shared

    async def health_check(self) -> bool:
        await self._driver.verify_connectivity()
        return True

    async def close(self) -> None:
        await self._driver.close()
        if type(self)._shared is self:
            type(self)._shared = None

    async def explain(self, cypher: str, params: Mapping[str, Any]) -> None:
        async with self._driver.session(database=self.database) as session:
            await session.execute_read(self._explain_transaction, cypher, dict(params))

    async def execute_read(
        self, cypher: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        async with self._driver.session(database=self.database) as session:
            return await session.execute_read(self._read_transaction, cypher, dict(params))

    async def execute_write(
        self, cypher: str, params: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        async with self._driver.session(database=self.database) as session:
            return await session.execute_write(self._read_transaction, cypher, dict(params))

    @staticmethod
    async def _read_transaction(
        transaction: AsyncManagedTransaction,
        /,
        cypher: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        result = await transaction.run(cypher, parameters=params)
        return await result.data()

    @staticmethod
    async def _explain_transaction(
        transaction: AsyncManagedTransaction,
        /,
        cypher: str,
        params: dict[str, Any],
    ) -> None:
        result = await transaction.run(f"EXPLAIN {cypher}", parameters=params)
        await result.consume()
