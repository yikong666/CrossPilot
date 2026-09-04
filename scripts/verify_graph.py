"""Validate required CrossPilot synthetic graph integrity conditions in Neo4j."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from neo4j import GraphDatabase  # noqa: E402

from app.core.config import get_settings  # noqa: E402

EXPECTED_CATEGORY_IDS = {
    "consumer_electronics",
    "children_toys",
    "home_goods",
}


def evaluate_graph_checks(
    *,
    product_count: int,
    category_counts: dict[str, int],
    orphan_products: int,
    incomplete_products: int,
    rules_without_documents: int,
    rules_without_categories: int,
    categories_without_metrics: int,
    orphan_market_metrics: int,
) -> dict[str, Any]:
    """Return a user-readable result for the graph checks collected from Neo4j."""
    issues: list[str] = []
    if not 90 <= product_count <= 120:
        issues.append(f"Product count must be between 90 and 120; found {product_count}.")
    if set(category_counts) != EXPECTED_CATEGORY_IDS:
        issues.append(f"Expected three categories; found {len(category_counts)}.")
    for category_id, count in sorted(category_counts.items()):
        if not 30 <= count <= 40:
            issues.append(f"Category {category_id} must contain 30 to 40 Products; found {count}.")
    if orphan_products:
        issues.append(f"Found {orphan_products} orphan Product nodes.")
    if incomplete_products:
        issues.append(f"Found {incomplete_products} Products missing price, rating, or BSR.")
    if rules_without_documents:
        issues.append(
            f"Found {rules_without_documents} ComplianceRules without supporting Documents."
        )
    if rules_without_categories:
        issues.append(
            f"Found {rules_without_categories} ComplianceRules without target "
            "Category relationships."
        )
    if categories_without_metrics:
        issues.append(
            f"Found {categories_without_metrics} Categories without "
            "HAS_MARKET_METRIC relationships."
        )
    if orphan_market_metrics:
        issues.append(
            f"Found {orphan_market_metrics} MarketMetrics without Category "
            "HAS_MARKET_METRIC relationships."
        )
    return {"ok": not issues, "product_count": product_count, "issues": issues}


def _single_count(session: Any, query: str) -> int:
    record = session.run(query).single()
    if record is None:
        raise RuntimeError("Neo4j verification query unexpectedly returned no record.")
    return int(record["count"])


def verify_graph() -> dict[str, Any]:
    """Query a real configured Neo4j instance and evaluate all required graph checks."""
    settings = get_settings()
    with GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    ) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            product_count = _single_count(session, "MATCH (p:Product) RETURN count(p) AS count")
            category_counts = {
                record["category_id"]: int(record["count"])
                for record in session.run(
                    "MATCH (c:Category)<-[:BELONGS_TO]-(p:Product) "
                    "RETURN c.category_id AS category_id, count(p) AS count"
                )
            }
            orphan_products = _single_count(
                session,
                "MATCH (p:Product) WHERE NOT (p)-[:BELONGS_TO]->(:Category) "
                "OR NOT (p)-[:MADE_BY]->(:Brand) OR NOT (p)-[:SOLD_ON]->(:Marketplace) "
                "OR NOT (p)-[:HAS_FEATURE]->(:Feature) "
                "RETURN count(p) AS count",
            )
            incomplete_products = _single_count(
                session,
                "MATCH (p:Product) WHERE p.price IS NULL OR p.rating IS NULL OR p.bsr IS NULL "
                "RETURN count(p) AS count",
            )
            rules_without_documents = _single_count(
                session,
                "MATCH (r:ComplianceRule) WHERE NOT (r)-[:REQUIRES_DOCUMENT]->(:Document) "
                "RETURN count(r) AS count",
            )
            rules_without_categories = _single_count(
                session,
                "MATCH (r:ComplianceRule) WHERE NOT (r)-[:APPLIES_TO]->(:Category) "
                "RETURN count(r) AS count",
            )
            categories_without_metrics = _single_count(
                session,
                "MATCH (c:Category) WHERE NOT (c)-[:HAS_MARKET_METRIC]->(:MarketMetric) "
                "RETURN count(c) AS count",
            )
            orphan_market_metrics = _single_count(
                session,
                "MATCH (m:MarketMetric) WHERE NOT (:Category)-[:HAS_MARKET_METRIC]->(m) "
                "RETURN count(m) AS count",
            )
    return evaluate_graph_checks(
        product_count=product_count,
        category_counts=category_counts,
        orphan_products=orphan_products,
        incomplete_products=incomplete_products,
        rules_without_documents=rules_without_documents,
        rules_without_categories=rules_without_categories,
        categories_without_metrics=categories_without_metrics,
        orphan_market_metrics=orphan_market_metrics,
    )


if __name__ == "__main__":
    result = verify_graph()
    print(result)
    if not result["ok"]:
        raise SystemExit(1)
