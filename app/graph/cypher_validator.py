from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.errors import CypherValidationError
from app.graph.schema_registry import SchemaRegistry

_WRITE_WORDS = {
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "LOAD",
    "FOREACH",
    "GRANT",
    "DENY",
    "REVOKE",
    "START",
    "STOP",
    "TERMINATE",
}
_FILTER_WORDS = {
    "AND",
    "OR",
    "NOT",
    "IN",
    "IS",
    "NULL",
    "CONTAINS",
    "STARTS",
    "ENDS",
    "WITH",
}
_FILTER_FUNCTIONS = {"COALESCE", "SIZE", "TOLOWER", "TOUPPER"}
_RETURN_FUNCTIONS = {
    "AVG",
    "MIN",
    "MAX",
    "SUM",
    "COUNT",
    "ROUND",
    "COALESCE",
    "ELEMENTID",
    "LABELS",
    "TYPE",
    "COLLECT",
}
_RESERVED_NODE_IDS = "__node_ids"
_RESERVED_RELATIONSHIP_IDS = "__relationship_ids"


@dataclass(frozen=True)
class ValidatedCypher:
    return_fields: frozenset[str]


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    offset: int

    @property
    def upper(self) -> str:
        return self.value.upper()


class CypherValidator:
    """Fail-closed validator for the deliberately small CrossPilot query subset."""

    def __init__(self, schema: SchemaRegistry, *, max_rows: int = 50) -> None:
        self.schema = schema
        self.max_rows = max_rows

    def validate(self, cypher: str, params: Mapping[str, Any]) -> ValidatedCypher:
        tokens = _tokenize(cypher)
        parser = _RestrictedQueryParser(tokens, params, self.schema, self.max_rows)
        return parser.parse()


class _RestrictedQueryParser:
    def __init__(
        self,
        tokens: Sequence[_Token],
        params: Mapping[str, Any],
        schema: SchemaRegistry,
        max_rows: int,
    ) -> None:
        self.tokens = list(tokens)
        self.params = params
        self.schema = schema
        self.max_rows = max_rows
        self.node_aliases: dict[str, str] = {}
        self.relationship_aliases: set[str] = set()

    def parse(self) -> ValidatedCypher:
        if not self.tokens:
            raise CypherValidationError("Cypher must not be empty")
        self._validate_single_statement()
        self._validate_read_only_words()
        self._validate_dynamic_properties()

        return_indexes = self._word_indexes("RETURN")
        limit_indexes = self._word_indexes("LIMIT")
        if len(return_indexes) != 1:
            raise CypherValidationError("Exactly one RETURN clause is required")
        if not limit_indexes:
            raise CypherValidationError("LIMIT is required")
        if len(limit_indexes) != 1:
            raise CypherValidationError("Exactly one LIMIT clause is required")
        return_index = return_indexes[0]
        limit_index = limit_indexes[0]
        if return_index >= limit_index:
            raise CypherValidationError("RETURN must precede LIMIT")

        pattern_start = self._match_start()
        where_indexes = [index for index in self._word_indexes("WHERE") if index < return_index]
        if len(where_indexes) > 1:
            raise CypherValidationError("Only one WHERE clause is supported")
        pattern_end = where_indexes[0] if where_indexes else return_index
        self._parse_pattern(pattern_start, pattern_end)
        self._validate_property_accesses()
        if where_indexes:
            self._validate_filter(where_indexes[0] + 1, return_index)

        order_index = self._find_order_by(return_index + 1, limit_index)
        projection_end = order_index if order_index is not None else limit_index
        return_fields = self._parse_return(return_index + 1, projection_end)
        if order_index is not None:
            self._validate_order_by(order_index, limit_index, return_fields)
        self._validate_limit(limit_index)
        self._validate_parameters()
        return ValidatedCypher(return_fields=frozenset(return_fields))

    def _validate_single_statement(self) -> None:
        semicolons = [index for index, token in enumerate(self.tokens) if token.value == ";"]
        if not semicolons:
            return
        if len(semicolons) != 1 or semicolons[0] != len(self.tokens) - 1:
            raise CypherValidationError("Cypher must contain a single statement")

    def _validate_read_only_words(self) -> None:
        for token in self.tokens:
            if token.kind != "IDENT":
                continue
            if token.upper in _WRITE_WORDS:
                raise CypherValidationError("Cypher must be read-only")
            if token.upper == "CALL":
                raise CypherValidationError("CALL is not authorized")

    def _validate_dynamic_properties(self) -> None:
        for index in range(len(self.tokens) - 1):
            token = self.tokens[index]
            if token.kind == "IDENT" and self.tokens[index + 1].value == "[":
                raise CypherValidationError("Dynamic property access is not allowed")

    def _match_start(self) -> int:
        if self._is_word(0, "MATCH"):
            return 1
        if self._is_word(0, "OPTIONAL") and self._is_word(1, "MATCH"):
            return 2
        raise CypherValidationError("Query must start with MATCH or OPTIONAL MATCH")

    def _parse_pattern(self, start: int, end: int) -> None:
        if start >= end:
            raise CypherValidationError("MATCH pattern is required")
        index = self._parse_node(start, end)
        while index < end:
            if self.tokens[index].value == ",":
                index = self._parse_node(index + 1, end)
                continue
            if self.tokens[index].value == "<":
                index += 1
            if index >= end or self.tokens[index].value != "-":
                raise CypherValidationError("Unsupported MATCH pattern")
            index += 1
            if index >= end or self.tokens[index].value != "[":
                raise CypherValidationError("typed relationship pattern is required")
            index = self._parse_relationship(index, end)
            if index >= end or self.tokens[index].value != "-":
                raise CypherValidationError("Relationship direction is malformed")
            index += 1
            if index < end and self.tokens[index].value == ">":
                index += 1
            index = self._parse_node(index, end)

    def _parse_node(self, index: int, end: int) -> int:
        if index >= end or self.tokens[index].value != "(":
            raise CypherValidationError("Node pattern is required")
        index += 1
        alias: str | None = None
        if index < end and self.tokens[index].kind == "IDENT":
            alias = self.tokens[index].value
            index += 1
        if index >= end or self.tokens[index].value != ":":
            raise CypherValidationError("node label is required")
        index += 1
        label = self._require_identifier(index, "Node label")
        if label not in self.schema.labels:
            raise CypherValidationError(f"Unknown label: {label}")
        index += 1
        if alias:
            existing = self.node_aliases.get(alias)
            if existing is not None and existing != label:
                raise CypherValidationError(f"Alias {alias} has conflicting labels")
            self.node_aliases[alias] = label
        if index < end and self.tokens[index].value == "{":
            index = self._parse_node_map(index, end, label)
        if index >= end or self.tokens[index].value != ")":
            raise CypherValidationError("Unsupported node pattern")
        return index + 1

    def _parse_node_map(self, index: int, end: int, label: str) -> int:
        index += 1
        while index < end and self.tokens[index].value != "}":
            prop = self._require_identifier(index, "Node property")
            if prop not in self.schema.properties_for(label):
                raise CypherValidationError(f"Unknown property for {label}: {prop}")
            index += 1
            if index >= end or self.tokens[index].value != ":":
                raise CypherValidationError("Node property map is malformed")
            index += 1
            if index >= end or self.tokens[index].kind != "PARAM":
                raise CypherValidationError("Node property values must use parameters")
            index += 1
            if index < end and self.tokens[index].value == ",":
                index += 1
            elif index < end and self.tokens[index].value != "}":
                raise CypherValidationError("Node property map is malformed")
        if index >= end or self.tokens[index].value != "}":
            raise CypherValidationError("Node property map is malformed")
        return index + 1

    def _parse_relationship(self, index: int, end: int) -> int:
        index += 1
        alias: str | None = None
        if index < end and self.tokens[index].kind == "IDENT":
            alias = self.tokens[index].value
            index += 1
        if index >= end or self.tokens[index].value != ":":
            raise CypherValidationError("relationship type is required")
        while True:
            index += 1
            relation_type = self._require_identifier(index, "Relationship type")
            if relation_type not in self.schema.relationships:
                raise CypherValidationError(f"Unknown relationship: {relation_type}")
            index += 1
            if index >= end or self.tokens[index].value != "|":
                break
            index += 1
            if index < end and self.tokens[index].value == ":":
                continue
            index -= 1
        if index >= end or self.tokens[index].value != "]":
            raise CypherValidationError("Unsupported relationship pattern")
        if alias:
            self.relationship_aliases.add(alias)
        return index + 1

    def _validate_property_accesses(self) -> None:
        for index in range(len(self.tokens) - 2):
            alias_token, dot, prop_token = self.tokens[index : index + 3]
            if alias_token.kind != "IDENT" or dot.value != ".":
                continue
            if prop_token.kind != "IDENT":
                raise CypherValidationError("Property identifier must be explicit")
            alias = alias_token.value
            prop = prop_token.value
            label = self.node_aliases.get(alias)
            if label is not None:
                if prop not in self.schema.properties_for(label):
                    raise CypherValidationError(f"Unknown property for {label}: {prop}")
                continue
            if alias in self.relationship_aliases:
                raise CypherValidationError("Relationship properties are not allowed")
            raise CypherValidationError(f"Unknown graph alias: {alias}")

    def _validate_filter(self, start: int, end: int) -> None:
        index = start
        while index < end:
            token = self.tokens[index]
            if token.kind == "NUMBER":
                raise CypherValidationError("WHERE values must use parameters")
            if token.kind == "IDENT" and token.upper in {"TRUE", "FALSE"}:
                raise CypherValidationError("WHERE values must use parameters")
            if token.kind == "PARAM" or token.value in {
                "(", ")", "[", "]", ",", "=", "<", ">", "!", "+", "-", "*", "/",
            }:
                index += 1
                continue
            if token.kind != "IDENT":
                raise CypherValidationError("Unsupported WHERE expression")
            if index + 2 < end and self.tokens[index + 1].value == ".":
                index += 3
                continue
            if index + 2 < end and self.tokens[index + 1].value == ":":
                alias = token.value
                label = self._require_identifier(index + 2, "Label predicate")
                if alias not in self.node_aliases:
                    raise CypherValidationError(f"Unknown graph alias: {alias}")
                if label not in self.schema.labels:
                    raise CypherValidationError(f"Unknown label: {label}")
                index += 3
                continue
            if token.upper in _FILTER_WORDS:
                index += 1
                continue
            if token.upper in _FILTER_FUNCTIONS and self._value(index + 1) == "(":
                index += 1
                continue
            raise CypherValidationError(f"Unsupported WHERE identifier: {token.value}")

    def _parse_return(self, start: int, end: int) -> set[str]:
        items = self._split_top_level(start, end, ",")
        if not items:
            raise CypherValidationError("RETURN projection is required")
        fields: set[str] = set()
        for item_start, item_end in items:
            field = self._parse_return_item(item_start, item_end)
            if field in fields:
                raise CypherValidationError(f"Duplicate RETURN field: {field}")
            fields.add(field)
        return fields

    def _parse_return_item(self, start: int, end: int) -> str:
        if start >= end:
            raise CypherValidationError("Empty RETURN projection")
        if self._is_word(start, "DISTINCT"):
            start += 1
        as_indexes = self._top_level_word_indexes(start, end, "AS")
        if len(as_indexes) > 1:
            raise CypherValidationError("RETURN alias is malformed")
        alias: str | None = None
        expression_end = end
        if as_indexes:
            as_index = as_indexes[0]
            alias = self._require_identifier(as_index + 1, "RETURN alias")
            if as_index + 2 != end:
                raise CypherValidationError("RETURN alias is malformed")
            expression_end = as_index
        kind = self._expression_kind(start, expression_end)
        if kind == "graph":
            raise CypherValidationError("Returning graph variables is not allowed")
        if alias is None:
            if expression_end - start != 3 or self.tokens[start + 1].value != ".":
                raise CypherValidationError("Computed RETURN expressions require an explicit alias")
            alias = f"{self.tokens[start].value}.{self.tokens[start + 2].value}"
        if alias.startswith("_"):
            self._validate_reserved_projection(alias, start, expression_end)
            return alias
        return alias

    def _expression_kind(self, start: int, end: int) -> str:
        if start >= end:
            raise CypherValidationError("RETURN expression is empty")
        if end - start == 1:
            token = self.tokens[start]
            if token.value == "*":
                return "graph"
            if token.kind in {"NUMBER", "PARAM"}:
                return "scalar"
            if token.kind == "IDENT" and (
                token.value in self.node_aliases or token.value in self.relationship_aliases
            ):
                return "graph"
            raise CypherValidationError("Unsupported RETURN expression")
        if end - start == 3 and self.tokens[start + 1].value == ".":
            self._property_kind(start)
            return "scalar"
        if (
            self.tokens[start].kind == "IDENT"
            and self.tokens[start].upper in _RETURN_FUNCTIONS
            and self._value(start + 1) == "("
            and self._matching_close(start + 1, end, "(", ")") == end - 1
        ):
            function = self.tokens[start].upper
            arguments = self._split_top_level(start + 2, end - 1, ",")
            if not arguments:
                raise CypherValidationError("RETURN function requires arguments")
            if function == "COUNT" and len(arguments) == 1:
                argument_start, argument_end = arguments[0]
                if (
                    argument_end - argument_start == 2
                    and self._is_word(argument_start, "DISTINCT")
                    and self.tokens[argument_start + 1].kind == "IDENT"
                    and (
                        self.tokens[argument_start + 1].value in self.node_aliases
                        or self.tokens[argument_start + 1].value in self.relationship_aliases
                    )
                ):
                    kinds = ["graph"]
                else:
                    kinds = [
                        self._expression_kind(left, right) for left, right in arguments
                    ]
            else:
                kinds = [self._expression_kind(left, right) for left, right in arguments]
            if function in {"ELEMENTID", "LABELS", "TYPE"}:
                if len(kinds) != 1 or kinds[0] != "graph":
                    raise CypherValidationError(f"{function} requires a graph variable")
            elif function not in {"COUNT"} and any(kind == "graph" for kind in kinds):
                raise CypherValidationError(f"{function} cannot return a graph variable")
            return "scalar"
        raise CypherValidationError("Unsupported RETURN expression")

    def _property_kind(self, start: int) -> None:
        alias = self.tokens[start].value
        prop = self.tokens[start + 2].value
        label = self.node_aliases.get(alias)
        if label is None or prop not in self.schema.properties_for(label):
            raise CypherValidationError(f"Unknown property access: {alias}.{prop}")

    def _validate_reserved_projection(self, alias: str, start: int, end: int) -> None:
        expected_relationship = alias == _RESERVED_RELATIONSHIP_IDS
        if alias not in {_RESERVED_NODE_IDS, _RESERVED_RELATIONSHIP_IDS}:
            raise CypherValidationError("Private RETURN aliases are not allowed")
        graph_alias = self._id_projection_alias(start, end)
        if graph_alias is None:
            raise CypherValidationError("Reserved graph ID fields require elementId projection")
        if expected_relationship != (graph_alias in self.relationship_aliases):
            raise CypherValidationError("Reserved graph ID field has the wrong entity type")

    def _id_projection_alias(self, start: int, end: int) -> str | None:
        tokens = self.tokens
        if end - start == 4 and tokens[start].upper == "ELEMENTID":
            if tokens[start + 1].value == "(" and tokens[start + 3].value == ")":
                return tokens[start + 2].value
        if end - start == 7 and tokens[start].upper == "COLLECT":
            values = [token.value.upper() for token in tokens[start:end]]
            if values[1] == "(" and values[2] == "ELEMENTID" and values[3] == "(":
                if values[5] == ")" and values[6] == ")":
                    return tokens[start + 4].value
        return None

    def _find_order_by(self, start: int, end: int) -> int | None:
        indexes = self._top_level_word_indexes(start, end, "ORDER")
        if not indexes:
            return None
        if len(indexes) != 1 or not self._is_word(indexes[0] + 1, "BY"):
            raise CypherValidationError("ORDER BY is malformed")
        return indexes[0]

    def _validate_order_by(self, order_index: int, end: int, fields: set[str]) -> None:
        items = self._split_top_level(order_index + 2, end, ",")
        for start, item_end in items:
            if item_end > start and self.tokens[item_end - 1].upper in {"ASC", "DESC"}:
                item_end -= 1
            if item_end - start == 1 and self.tokens[start].value in fields:
                continue
            if item_end - start == 3 and self.tokens[start + 1].value == ".":
                self._property_kind(start)
                continue
            raise CypherValidationError("ORDER BY must use an allowed projected field")

    def _validate_limit(self, index: int) -> None:
        if index + 1 >= len(self.tokens):
            raise CypherValidationError("LIMIT value is required")
        token = self.tokens[index + 1]
        if token.kind == "PARAM":
            value = self.params.get(token.value)
        elif token.kind == "NUMBER" and token.value.isdigit():
            value = int(token.value)
        else:
            raise CypherValidationError("LIMIT must be a bounded integer")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= self.max_rows:
            raise CypherValidationError(f"LIMIT must be between 1 and {self.max_rows}")
        trailing = self.tokens[index + 2 :]
        if trailing and not (len(trailing) == 1 and trailing[0].value == ";"):
            raise CypherValidationError("Unsupported clause after LIMIT")

    def _validate_parameters(self) -> None:
        referenced = {token.value for token in self.tokens if token.kind == "PARAM"}
        provided = set(self.params)
        missing = sorted(referenced - provided)
        if missing:
            raise CypherValidationError(f"Missing parameters: {', '.join(missing)}")
        unexpected = sorted(provided - referenced)
        if unexpected:
            raise CypherValidationError(f"Unexpected parameters: {', '.join(unexpected)}")

    def _split_top_level(self, start: int, end: int, separator: str) -> list[tuple[int, int]]:
        if start >= end:
            return []
        result: list[tuple[int, int]] = []
        item_start = start
        round_depth = 0
        square_depth = 0
        for index in range(start, end):
            value = self.tokens[index].value
            if value == "(":
                round_depth += 1
            elif value == ")":
                round_depth -= 1
            elif value == "[":
                square_depth += 1
            elif value == "]":
                square_depth -= 1
            elif value == separator and round_depth == 0 and square_depth == 0:
                result.append((item_start, index))
                item_start = index + 1
            if round_depth < 0 or square_depth < 0:
                raise CypherValidationError("Unbalanced expression delimiters")
        if round_depth != 0 or square_depth != 0:
            raise CypherValidationError("Unbalanced expression delimiters")
        result.append((item_start, end))
        return result

    def _top_level_word_indexes(self, start: int, end: int, word: str) -> list[int]:
        indexes: list[int] = []
        depth = 0
        for index in range(start, end):
            value = self.tokens[index].value
            if value in {"(", "["}:
                depth += 1
            elif value in {")", "]"}:
                depth -= 1
            elif depth == 0 and self._is_word(index, word):
                indexes.append(index)
        return indexes

    def _word_indexes(self, word: str) -> list[int]:
        return [index for index in range(len(self.tokens)) if self._is_word(index, word)]

    def _matching_close(
        self, open_index: int, end: int, opening: str, closing: str
    ) -> int | None:
        depth = 0
        for index in range(open_index, end):
            if self.tokens[index].value == opening:
                depth += 1
            elif self.tokens[index].value == closing:
                depth -= 1
                if depth == 0:
                    return index
        return None

    def _require_identifier(self, index: int, description: str) -> str:
        if index >= len(self.tokens) or self.tokens[index].kind != "IDENT":
            raise CypherValidationError(f"{description} must be an explicit identifier")
        return self.tokens[index].value

    def _is_word(self, index: int, word: str) -> bool:
        return (
            0 <= index < len(self.tokens)
            and self.tokens[index].kind == "IDENT"
            and self.tokens[index].upper == word
        )

    def _value(self, index: int) -> str | None:
        return self.tokens[index].value if 0 <= index < len(self.tokens) else None


def _tokenize(cypher: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    while index < len(cypher):
        char = cypher[index]
        following = cypher[index + 1] if index + 1 < len(cypher) else ""
        if char.isspace():
            index += 1
            continue
        if char == "/" and following == "/":
            newline = cypher.find("\n", index + 2)
            index = len(cypher) if newline == -1 else newline + 1
            continue
        if char == "/" and following == "*":
            end = cypher.find("*/", index + 2)
            if end == -1:
                raise CypherValidationError("Unterminated comment")
            index = end + 2
            continue
        if char in {"'", '"'}:
            raise CypherValidationError(
                "String literals are not allowed; use parameters for business values"
            )
        if char == "`":
            raise CypherValidationError("Backtick identifiers are not allowed")
        if char == "$":
            start = index
            index += 1
            name_start = index
            while index < len(cypher) and (cypher[index].isalnum() or cypher[index] == "_"):
                index += 1
            invalid_start = (
                index == name_start
                or not cypher[name_start].isalpha()
                and cypher[name_start] != "_"
            )
            if invalid_start:
                raise CypherValidationError("Parameter name is malformed")
            tokens.append(_Token("PARAM", cypher[name_start:index], start))
            continue
        if char.isalpha() or char == "_":
            start = index
            index += 1
            while index < len(cypher) and (cypher[index].isalnum() or cypher[index] == "_"):
                index += 1
            tokens.append(_Token("IDENT", cypher[start:index], start))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < len(cypher) and (cypher[index].isdigit() or cypher[index] == "."):
                index += 1
            tokens.append(_Token("NUMBER", cypher[start:index], start))
            continue
        if char in "()[]{}:,.|;+-*/<>=!":
            tokens.append(_Token("SYMBOL", char, index))
            index += 1
            continue
        raise CypherValidationError(f"Unsupported Cypher token at offset {index}")
    return tokens
