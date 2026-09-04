import pytest

from app.core.errors import CypherValidationError
from app.graph.cypher_validator import CypherValidator
from app.graph.schema_registry import SchemaRegistry


@pytest.fixture
def validator() -> CypherValidator:
    return CypherValidator(SchemaRegistry())


def test_accepts_parameterized_read_query_with_bounded_limit(
    validator: CypherValidator,
) -> None:
    cypher = (
        "MATCH (p:Product)-[:MADE_BY]->(b:Brand) "
        "WHERE p.name = $name RETURN p.product_id AS product_id, b.name AS brand LIMIT 10"
    )

    validator.validate(cypher, {"name": "示例商品"})


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (p:Product) CREATE (:Product {name: $name}) RETURN p LIMIT 1",
        "MATCH (p:Product) MERGE (:Brand {name: $name}) RETURN p LIMIT 1",
        "MATCH (p:Product) DETACH DELETE p RETURN p LIMIT 1",
        "MATCH (p:Product) SET p.name = $name RETURN p LIMIT 1",
        "MATCH (p:Product) REMOVE p.name RETURN p LIMIT 1",
        "MATCH (p:Product) LOAD CSV FROM $url AS row RETURN row LIMIT 1",
        "MATCH (p:Product) FOREACH (x IN [1] | SET p.rating = x) RETURN p LIMIT 1",
    ],
)
def test_rejects_write_clauses(validator: CypherValidator, cypher: str) -> None:
    with pytest.raises(CypherValidationError, match="read-only"):
        validator.validate(cypher, {"name": "x", "url": "https://invalid.example"})


def test_rejects_multiple_statements(validator: CypherValidator) -> None:
    with pytest.raises(CypherValidationError, match="single statement"):
        validator.validate(
            "MATCH (p:Product) RETURN p LIMIT 1; MATCH (b:Brand) RETURN b LIMIT 1",
            {},
        )


def test_rejects_unknown_label(validator: CypherValidator) -> None:
    with pytest.raises(CypherValidationError, match="Unknown label: Customer"):
        validator.validate("MATCH (c:Customer) RETURN c LIMIT 1", {})


def test_rejects_unknown_label_predicate(validator: CypherValidator) -> None:
    with pytest.raises(CypherValidationError, match="Unknown label: Customer"):
        validator.validate("MATCH (p) WHERE p:Customer RETURN p LIMIT 1", {})


def test_rejects_unknown_relationship(validator: CypherValidator) -> None:
    with pytest.raises(CypherValidationError, match="Unknown relationship: PURCHASED"):
        validator.validate(
            "MATCH (p:Product)-[:PURCHASED]->(b:Brand) RETURN p LIMIT 1", {}
        )


def test_rejects_unknown_property_for_label(validator: CypherValidator) -> None:
    with pytest.raises(CypherValidationError, match="Unknown property for Product: secret"):
        validator.validate("MATCH (p:Product) RETURN p.secret LIMIT 1", {})


def test_rejects_unknown_property_in_node_map(validator: CypherValidator) -> None:
    with pytest.raises(CypherValidationError, match="Unknown property for Product: secret"):
        validator.validate(
            "MATCH (p:Product {secret: $secret}) RETURN p LIMIT 1", {"secret": "x"}
        )


def test_rejects_unauthorized_call(validator: CypherValidator) -> None:
    with pytest.raises(CypherValidationError, match="CALL is not authorized"):
        validator.validate("CALL dbms.components() YIELD name RETURN name LIMIT 1", {})


@pytest.mark.parametrize(
    ("cypher", "params", "message"),
    [
        ("MATCH (p:Product) RETURN p", {}, "LIMIT is required"),
        ("MATCH (p:Product) RETURN p LIMIT 51", {}, "LIMIT must be between 1 and 50"),
        (
            "MATCH (p:Product) WHERE p.name = $name RETURN p LIMIT 1",
            {},
            "Missing parameters: name",
        ),
        (
            "MATCH (p:Product) RETURN p LIMIT 1",
            {"unused": "x"},
            "Unexpected parameters: unused",
        ),
    ],
)
def test_rejects_unbounded_or_incorrect_parameters(
    validator: CypherValidator,
    cypher: str,
    params: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(CypherValidationError, match=message):
        validator.validate(cypher, params)


def test_ignores_keywords_inside_parameter_safe_string_literals(
    validator: CypherValidator,
) -> None:
    validator.validate(
        "MATCH (p:Product) WHERE p.name = $name RETURN p.name AS name LIMIT 1",
        {"name": "DELETE; MATCH"},
    )
