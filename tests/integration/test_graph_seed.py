import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def test_generate_seed_data_creates_deterministic_complete_synthetic_dataset(
    tmp_path: Path,
) -> None:
    """Removing deterministic generation or synthetic markers must break this test."""
    from scripts.generate_seed_data import generate_seed_data

    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    first_manifest = generate_seed_data(first_output)
    second_manifest = generate_seed_data(second_output)

    expected_csvs = {
        "categories.csv",
        "brands.csv",
        "products.csv",
        "features.csv",
        "product_features.csv",
        "market_metrics.csv",
        "fee_rules.csv",
        "risk_attributes.csv",
        "product_risks.csv",
        "compliance_rules.csv",
        "documents.csv",
        "rule_documents.csv",
        "rule_categories.csv",
        "product_competitors.csv",
    }
    assert {path.name for path in first_output.glob("*.csv")} == expected_csvs
    assert first_manifest == second_manifest
    assert first_manifest["synthetic_data"] is True
    assert set(first_manifest["relationship_expectations"]) == {
        "BELONGS_TO",
        "MADE_BY",
        "SOLD_ON",
        "HAS_FEATURE",
        "COMPETES_WITH",
        "HAS_MARKET_METRIC",
        "USES_FEE_RULE",
        "HAS_RISK_ATTRIBUTE",
        "TRIGGERS",
        "REQUIRES_DOCUMENT",
        "APPLIES_TO",
    }

    products = read_csv(first_output / "products.csv")
    assert 90 <= len(products) <= 120
    assert {product["is_synthetic"] for product in products} == {"true"}
    assert {product["category_id"] for product in products} == {
        "category_electronics_accessories",
        "category_children_toys",
        "category_home_goods",
    }
    assert all(product["price"] and product["rating"] and product["bsr"] for product in products)
    trigger_pairs = {
        (row["risk_id"], row["rule_id"]) for row in read_csv(first_output / "product_risks.csv")
    }
    assert first_manifest["relationship_expectations"]["TRIGGERS"] == len(trigger_pairs)

    assert (first_output / "products.csv").read_bytes() == (
        second_output / "products.csv"
    ).read_bytes()
    assert (
        json.loads((first_output / "seed_manifest.json").read_text(encoding="utf-8"))
        == first_manifest
    )


def test_generate_seed_data_links_each_product_to_required_graph_context(tmp_path: Path) -> None:
    """Dropping graph relationships, risk samples, or competitor coverage must break this test."""
    from scripts.generate_seed_data import generate_seed_data

    output_dir = tmp_path / "data"
    generate_seed_data(output_dir)

    products = read_csv(output_dir / "products.csv")
    features = read_csv(output_dir / "product_features.csv")
    competitors = read_csv(output_dir / "product_competitors.csv")
    risks = read_csv(output_dir / "risk_attributes.csv")
    product_risks = read_csv(output_dir / "product_risks.csv")

    product_ids = {product["product_id"] for product in products}
    features_by_product = {product_id: 0 for product_id in product_ids}
    for row in features:
        features_by_product[row["product_id"]] += 1
    assert all(2 <= count <= 5 for count in features_by_product.values())

    competitors_by_product = {product_id: 0 for product_id in product_ids}
    for row in competitors:
        competitors_by_product[row["product_id"]] += 1
    assert max(competitors_by_product.values()) >= 5

    risk_names = {risk["name"] for risk in risks}
    assert {"battery", "electrical", "wireless"} <= risk_names
    assert {"age", "material", "small_parts"} <= risk_names
    assert {"material", "load_bearing", "food_contact"} <= risk_names
    assert {row["product_id"] for row in product_risks} <= product_ids


def test_schema_declares_constraints_indexes_and_vector_index() -> None:
    """Removing required graph safeguards or embedding index must break this test."""
    schema = Path("data/schema.cypher").read_text(encoding="utf-8")

    assert "REQUIRE n.category_id IS UNIQUE" in schema
    assert "REQUIRE n.brand_id IS UNIQUE" in schema
    assert "REQUIRE n.product_id IS UNIQUE" in schema
    assert "REQUIRE n.rule_id IS UNIQUE" in schema
    assert "product_name_embeddings" in schema
    assert "vector.dimensions`: 1024" in schema
    assert "vector.similarity_function`: 'cosine'" in schema


def test_repository_keeps_csv_assets_under_data_csv() -> None:
    """Moving required seed CSVs outside data/csv would break the task's artifact contract."""
    csv_dir = Path("data/csv")

    assert (csv_dir / "products.csv").is_file()
    assert (csv_dir / "product_risks.csv").is_file()
    assert Path("data/seed_manifest.json").is_file()


def test_seed_loader_parses_numeric_and_boolean_product_properties(tmp_path: Path) -> None:
    """Removing CSV coercion would leave graph properties as strings and fail this test."""
    from scripts.seed_graph import load_csv_rows

    csv_path = tmp_path / "products.csv"
    csv_path.write_text(
        "product_id,price,rating,review_count,bsr,is_synthetic\n"
        "product_test,19.95,4.7,123,456,true\n",
        encoding="utf-8",
    )

    assert load_csv_rows(csv_path) == [
        {
            "product_id": "product_test",
            "price": 19.95,
            "rating": 4.7,
            "review_count": 123,
            "bsr": 456,
            "is_synthetic": True,
        }
    ]


def test_graph_verification_flags_each_required_data_integrity_failure() -> None:
    """Removing any verification branch must let an invalid graph appear healthy."""
    from scripts.verify_graph import evaluate_graph_checks

    result = evaluate_graph_checks(
        product_count=89,
        category_counts={"category_electronics_accessories": 32, "category_children_toys": 32},
        orphan_products=1,
        incomplete_products=2,
        rules_without_documents=1,
        rules_without_categories=1,
    )

    assert result["ok"] is False
    assert result["product_count"] == 89
    assert result["issues"] == [
        "Product count must be between 90 and 120; found 89.",
        "Expected three categories; found 2.",
        "Found 1 orphan Product nodes.",
        "Found 2 Products missing price, rating, or BSR.",
        "Found 1 ComplianceRules without supporting Documents.",
        "Found 1 ComplianceRules without target Category relationships.",
    ]


def test_seed_relationship_specs_preserve_prd_directionality() -> None:
    """Reversing a PRD relationship source or target would corrupt graph traversal semantics."""
    from scripts.seed_graph import RELATIONSHIP_SPECS

    endpoint_pairs = {spec[2]: (spec[0], spec[3]) for _, spec in RELATIONSHIP_SPECS}

    assert endpoint_pairs == {
        "HAS_FEATURE": ("Product", "Feature"),
        "HAS_RISK_ATTRIBUTE": ("Product", "RiskAttribute"),
        "TRIGGERS": ("RiskAttribute", "ComplianceRule"),
        "REQUIRES_DOCUMENT": ("ComplianceRule", "Document"),
        "APPLIES_TO": ("ComplianceRule", "Category"),
        "COMPETES_WITH": ("Product", "Product"),
    }


@pytest.mark.parametrize("script_name", ["seed_graph.py", "verify_graph.py"])
def test_graph_scripts_run_from_repository_root_without_module_path_errors(
    script_name: str,
) -> None:
    """Removing root path bootstrapping would make direct script execution lose the app package."""
    environment = {
        **os.environ,
        "LLM_BASE_URL": "http://localhost:9999",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
        "NEO4J_URI": "invalid-uri",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "test-password",
    }
    completed = subprocess.run(
        [sys.executable, f"scripts/{script_name}"],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert "No module named 'app'" not in completed.stderr


def test_seed_script_is_idempotent_against_a_real_neo4j_instance() -> None:
    """A non-MERGE seed implementation must create duplicates and fail this integration test."""
    pytest.importorskip("neo4j")
    from scripts.seed_graph import neo4j_is_available, seed_graph
    from scripts.verify_graph import verify_graph

    if not neo4j_is_available():
        pytest.skip("Local Neo4j is unavailable; no mock substitute is used.")

    first_counts = seed_graph()
    second_counts = seed_graph()
    verification = verify_graph()

    assert first_counts == second_counts
    assert verification["ok"] is True
