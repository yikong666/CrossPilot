"""Idempotently load CrossPilot's synthetic CSV dataset into Neo4j."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from neo4j import GraphDatabase  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from scripts.generate_seed_data import generate_seed_data  # noqa: E402

DATA_DIR = ROOT_DIR / "data"
CSV_DIR = DATA_DIR / "csv"
MANIFEST_PATH = DATA_DIR / "seed_manifest.json"
BATCH_SIZE = 100

NODE_SPECS = {
    "categories.csv": ("Category", "category_id"),
    "brands.csv": ("Brand", "brand_id"),
    "products.csv": ("Product", "product_id"),
    "features.csv": ("Feature", "feature_id"),
    "market_metrics.csv": ("MarketMetric", "metric_id"),
    "fee_rules.csv": ("FeeRule", "fee_rule_id"),
    "risk_attributes.csv": ("RiskAttribute", "risk_id"),
    "compliance_rules.csv": ("ComplianceRule", "rule_id"),
    "documents.csv": ("Document", "document_id"),
}

RELATIONSHIP_SPECS = [
    ("product_features.csv", ("Product", "product_id", "HAS_FEATURE", "Feature", "feature_id")),
    (
        "product_risks.csv",
        ("Product", "product_id", "HAS_RISK_ATTRIBUTE", "RiskAttribute", "risk_id"),
    ),
    (
        "product_risks.csv",
        ("RiskAttribute", "risk_id", "TRIGGERS", "ComplianceRule", "rule_id"),
    ),
    (
        "rule_documents.csv",
        ("ComplianceRule", "rule_id", "REQUIRES_DOCUMENT", "Document", "document_id"),
    ),
    ("rule_categories.csv", ("ComplianceRule", "rule_id", "APPLIES_TO", "Category", "category_id")),
    (
        "product_competitors.csv",
        ("Product", "product_id", "COMPETES_WITH", "Product", "competitor_product_id"),
    ),
]


def load_csv_rows(path: Path) -> list[dict[str, Any]]:
    """Load a CSV and coerce graph scalar properties from their textual form."""
    integer_fields = {"review_count", "bsr", "monthly_search_volume", "estimated_monthly_sales"}
    float_fields = {"price", "rating", "referral_rate", "fulfillment_fee"}
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as file:
        for raw_row in csv.DictReader(file):
            row: dict[str, Any] = {}
            for key, value in raw_row.items():
                if key in integer_fields:
                    row[key] = int(value)
                elif key in float_fields:
                    row[key] = float(value)
                elif key == "is_synthetic":
                    row[key] = value.lower() == "true"
                else:
                    row[key] = value
            rows.append(row)
    return rows


def _batches(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [rows[index : index + BATCH_SIZE] for index in range(0, len(rows), BATCH_SIZE)]


def _execute_schema(driver: Any) -> None:
    statements = (DATA_DIR / "schema.cypher").read_text(encoding="utf-8").split(";")
    with driver.session() as session:
        for statement in statements:
            if statement.strip():
                session.run(statement).consume()


def _merge_nodes(driver: Any, label: str, identity: str, rows: list[dict[str, Any]]) -> None:
    query = (
        f"UNWIND $rows AS row MERGE (node:{label} {{{identity}: row.{identity}}}) SET node += row"
    )
    with driver.session() as session:
        for batch in _batches(rows):
            session.run(query, rows=batch).consume()


def _merge_relationships(
    driver: Any,
    source_label: str,
    source_key: str,
    relationship: str,
    target_label: str,
    target_key: str,
    rows: list[dict[str, Any]],
) -> None:
    query = (
        f"UNWIND $rows AS row MATCH (source:{source_label} {{{source_key}: row.{source_key}}}) "
        f"MATCH (target:{target_label} {{{target_key}: row.{target_key}}}) "
        f"MERGE (source)-[:{relationship}]->(target)"
    )
    with driver.session() as session:
        for batch in _batches(rows):
            session.run(query, rows=batch).consume()


def neo4j_is_available() -> bool:
    """Return whether configured Neo4j is reachable, without substituting a mock."""
    try:
        settings = get_settings()
        with GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        ) as driver:
            driver.verify_connectivity()
    except Exception:
        return False
    return True


def seed_graph() -> dict[str, int]:
    """Apply the schema then MERGE all dataset nodes and relationships in batches."""
    if not MANIFEST_PATH.exists():
        generate_seed_data(CSV_DIR, MANIFEST_PATH)
    settings = get_settings()
    with GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    ) as driver:
        driver.verify_connectivity()
        _execute_schema(driver)
        _merge_nodes(
            driver,
            "Marketplace",
            "marketplace_id",
            [{"marketplace_id": "marketplace_amazon_us", "name": "Amazon US", "country": "US"}],
        )
        for filename, (label, identity) in NODE_SPECS.items():
            _merge_nodes(driver, label, identity, load_csv_rows(CSV_DIR / filename))
        _merge_relationships(
            driver,
            "Product",
            "product_id",
            "BELONGS_TO",
            "Category",
            "category_id",
            load_csv_rows(CSV_DIR / "products.csv"),
        )
        _merge_relationships(
            driver,
            "Product",
            "product_id",
            "MADE_BY",
            "Brand",
            "brand_id",
            load_csv_rows(CSV_DIR / "products.csv"),
        )
        _merge_relationships(
            driver,
            "Product",
            "product_id",
            "SOLD_ON",
            "Marketplace",
            "marketplace_id",
            load_csv_rows(CSV_DIR / "products.csv"),
        )
        _merge_relationships(
            driver,
            "Product",
            "product_id",
            "HAS_MARKET_METRIC",
            "MarketMetric",
            "metric_id",
            load_csv_rows(CSV_DIR / "market_metrics.csv"),
        )
        _merge_relationships(
            driver,
            "Category",
            "category_id",
            "USES_FEE_RULE",
            "FeeRule",
            "fee_rule_id",
            load_csv_rows(CSV_DIR / "fee_rules.csv"),
        )
        for filename, spec in RELATIONSHIP_SPECS:
            _merge_relationships(driver, *spec, load_csv_rows(CSV_DIR / filename))
    return {
        label: len(load_csv_rows(CSV_DIR / filename)) for filename, (label, _) in NODE_SPECS.items()
    }


if __name__ == "__main__":
    print(seed_graph())
