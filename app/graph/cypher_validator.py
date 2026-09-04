from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.core.errors import CypherValidationError
from app.graph.schema_registry import SchemaRegistry

_WRITE_PATTERN = re.compile(
    r"\b(?:CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH|"
    r"GRANT|DENY|REVOKE|START|STOP|TERMINATE)\b",
    re.IGNORECASE,
)
_PARAMETER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+(\$[A-Za-z_][A-Za-z0-9_]*|\d+)\b", re.IGNORECASE)
_NODE_PATTERN = re.compile(r"\(([^()]*)\)")
_RELATIONSHIP_PATTERN = re.compile(r"\[([^\[\]]*)\]")
_ALIAS_LABEL_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)?\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_TYPE_PATTERN = re.compile(r":\s*([A-Za-z_][A-Za-z0-9_]*)")
_PROPERTY_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_LABEL_PREDICATE_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_MAP_KEY_PATTERN = re.compile(r"(?:\{|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")


class CypherValidator:
    """Statically reject generated Cypher outside the read-only allowlist."""

    def __init__(
        self,
        schema: SchemaRegistry,
        *,
        allowed_procedures: frozenset[str] = frozenset(),
        max_rows: int = 50,
    ) -> None:
        self.schema = schema
        self.allowed_procedures = allowed_procedures
        self.max_rows = max_rows

    def validate(self, cypher: str, params: Mapping[str, Any]) -> None:
        if not cypher.strip():
            raise CypherValidationError("Cypher must not be empty")
        statement = _strip_comments_and_literals(cypher).strip()
        if ";" in statement.rstrip(";"):
            raise CypherValidationError("Cypher must contain a single statement")
        if _WRITE_PATTERN.search(statement):
            raise CypherValidationError("Cypher must be read-only")
        self._validate_calls(statement)
        aliases = self._validate_schema(statement)
        self._validate_properties(statement, aliases)
        self._validate_limit(statement, params)
        self._validate_parameters(statement, params)

    def _validate_calls(self, statement: str) -> None:
        allowed = {item.lower() for item in self.allowed_procedures}
        calls = re.findall(
            r"\bCALL\s+([A-Za-z_][A-Za-z0-9_.]*)", statement, flags=re.IGNORECASE
        )
        for procedure in calls:
            if procedure.lower() not in allowed:
                raise CypherValidationError(f"CALL is not authorized: {procedure}")

    def _validate_schema(self, statement: str) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for node_body in _NODE_PATTERN.findall(statement):
            match = _ALIAS_LABEL_PATTERN.search(node_body)
            if match is None:
                continue
            alias, label = match.groups()
            if label not in self.schema.labels:
                raise CypherValidationError(f"Unknown label: {label}")
            if alias:
                aliases[alias] = label
            for prop in _MAP_KEY_PATTERN.findall(node_body):
                if prop not in self.schema.properties_for(label):
                    raise CypherValidationError(f"Unknown property for {label}: {prop}")
        for label in _LABEL_PREDICATE_PATTERN.findall(statement):
            if label not in self.schema.labels:
                raise CypherValidationError(f"Unknown label: {label}")
        for relationship_body in _RELATIONSHIP_PATTERN.findall(statement):
            for relation_type in _TYPE_PATTERN.findall(relationship_body):
                if relation_type not in self.schema.relationships:
                    raise CypherValidationError(f"Unknown relationship: {relation_type}")
        return aliases

    def _validate_properties(self, statement: str, aliases: Mapping[str, str]) -> None:
        all_properties = frozenset().union(*self.schema.label_properties.values())
        for alias, prop in _PROPERTY_PATTERN.findall(statement):
            label = aliases.get(alias)
            if label is not None and prop not in self.schema.properties_for(label):
                raise CypherValidationError(f"Unknown property for {label}: {prop}")
            if label is None and prop not in all_properties:
                raise CypherValidationError(f"Unknown property: {prop}")

    def _validate_limit(self, statement: str, params: Mapping[str, Any]) -> None:
        matches = _LIMIT_PATTERN.findall(statement)
        if not matches:
            raise CypherValidationError("LIMIT is required")
        if len(matches) != 1:
            raise CypherValidationError("Exactly one LIMIT clause is required")
        raw_limit = matches[0]
        value = params.get(raw_limit[1:]) if raw_limit.startswith("$") else int(raw_limit)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= self.max_rows:
            raise CypherValidationError(f"LIMIT must be between 1 and {self.max_rows}")

    @staticmethod
    def _validate_parameters(statement: str, params: Mapping[str, Any]) -> None:
        referenced = set(_PARAMETER_PATTERN.findall(statement))
        provided = set(params)
        missing = sorted(referenced - provided)
        if missing:
            raise CypherValidationError(f"Missing parameters: {', '.join(missing)}")
        unexpected = sorted(provided - referenced)
        if unexpected:
            raise CypherValidationError(f"Unexpected parameters: {', '.join(unexpected)}")


def _strip_comments_and_literals(cypher: str) -> str:
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(cypher):
        char = cypher[index]
        following = cypher[index + 1] if index + 1 < len(cypher) else ""
        if quote is not None:
            if char == "\\" and following:
                result.extend((" ", " "))
                index += 2
                continue
            if char == quote:
                quote = None
            result.append(" ")
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            result.append(" ")
            index += 1
            continue
        if char == "/" and following == "/":
            newline = cypher.find("\n", index + 2)
            if newline == -1:
                result.extend(" " * (len(cypher) - index))
                break
            result.extend(" " * (newline - index))
            index = newline
            continue
        if char == "/" and following == "*":
            end = cypher.find("*/", index + 2)
            if end == -1:
                result.extend(" " * (len(cypher) - index))
                break
            result.extend(" " * (end + 2 - index))
            index = end + 2
            continue
        result.append(char)
        index += 1
    return "".join(result)
