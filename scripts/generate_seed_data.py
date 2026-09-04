"""Generate the deterministic, explicitly synthetic CrossPilot graph dataset."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any

SEED = 20260904
PRODUCTS_PER_CATEGORY = 32
MARKETPLACE_ID = "marketplace_amazon_us"


def _write_csv(output_dir: Path, filename: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write an empty CSV: {filename}")
    with (output_dir / filename).open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _categories() -> list[dict[str, str]]:
    return [
        {
            "category_id": "consumer_electronics",
            "name": "Electronics Accessories",
            "category_group": "Consumer Electronics",
        },
        {
            "category_id": "children_toys",
            "name": "Children Toys",
            "category_group": "Children Products",
        },
        {
            "category_id": "home_goods",
            "name": "Home Goods",
            "category_group": "Home and Kitchen",
        },
    ]


def _catalog() -> dict[str, dict[str, Any]]:
    return {
        "consumer_electronics": {
            "prefix": "EA",
            "names": ["USB-C Cable", "GaN Charger", "Wireless Earbuds", "Travel Adapter"],
            "brands": ["brand_ea_nova", "brand_ea_pulse", "brand_ea_vector", "brand_ea_orbit"],
            "features": [
                ("feature_ea_cable", "USB-C cable"),
                ("feature_ea_charging", "fast charging"),
                ("feature_ea_wireless", "wireless"),
                ("feature_ea_battery", "rechargeable battery"),
                ("feature_ea_compact", "compact"),
            ],
            "risk_ids": ["risk_battery", "risk_electrical", "risk_wireless"],
            "price": (12, 89),
        },
        "children_toys": {
            "prefix": "CT",
            "names": ["Building Blocks", "STEM Robot Kit", "Puzzle Set", "Craft Kit"],
            "brands": ["brand_ct_spark", "brand_ct_wonder", "brand_ct_cedar", "brand_ct_playful"],
            "features": [
                ("feature_ct_educational", "educational"),
                ("feature_ct_age", "age guidance"),
                ("feature_ct_material", "non-toxic material"),
                ("feature_ct_small_parts", "small parts warning"),
                ("feature_ct_creative", "creative play"),
            ],
            "risk_ids": ["risk_age", "risk_toy_material", "risk_small_parts"],
            "price": (10, 65),
        },
        "home_goods": {
            "prefix": "HG",
            "names": ["Storage Organizer", "Kitchen Container", "Wall Shelf", "Laundry Basket"],
            "brands": ["brand_hg_harbor", "brand_hg_nest", "brand_hg_mason", "brand_hg_sage"],
            "features": [
                ("feature_hg_storage", "storage"),
                ("feature_hg_material", "durable material"),
                ("feature_hg_load", "load bearing"),
                ("feature_hg_food", "food contact"),
                ("feature_hg_easy_clean", "easy clean"),
            ],
            "risk_ids": ["risk_home_material", "risk_load_bearing", "risk_food_contact"],
            "price": (14, 110),
        },
    }


def _products_and_relationships(
    rng: random.Random, catalog: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    products: list[dict[str, str]] = []
    product_features: list[dict[str, str]] = []
    market_metrics: list[dict[str, str]] = []
    product_risks: list[dict[str, str]] = []
    for category_id, details in catalog.items():
        for number in range(1, PRODUCTS_PER_CATEGORY + 1):
            product_id = f"product_{details['prefix'].lower()}_{number:03d}"
            product_name = details["names"][(number - 1) % len(details["names"])]
            feature_count = 2 + (number % 4)
            selected_features = rng.sample(details["features"], feature_count)
            products.append(
                {
                    "product_id": product_id,
                    "name": f"{product_name} {number:02d}",
                    "title_en": f"{product_name} {number:02d}",
                    "category_id": category_id,
                    "brand_id": details["brands"][(number - 1) % len(details["brands"])],
                    "marketplace_id": MARKETPLACE_ID,
                    "price": f"{rng.uniform(*details['price']):.2f}",
                    "rating": f"{rng.uniform(3.5, 4.9):.1f}",
                    "review_count": str(rng.randint(25, 8500)),
                    "bsr": str(rng.randint(120, 180000)),
                    "is_synthetic": "true",
                }
            )
            market_metrics.append(
                {
                    "metric_id": f"metric_{details['prefix'].lower()}_{number:03d}",
                    "product_id": product_id,
                    "category_id": category_id,
                    "marketplace_id": MARKETPLACE_ID,
                    "monthly_search_volume": str(rng.randint(500, 25000)),
                    "estimated_monthly_sales": str(rng.randint(10, 1800)),
                    "demand_level": ("high", "medium", "low")[number % 3],
                    "period": "2026-09",
                    "sample_size": str(rng.randint(200, 3000)),
                    "is_synthetic": "true",
                }
            )
            for feature_id, _ in selected_features:
                product_features.append({"product_id": product_id, "feature_id": feature_id})
            for risk_id in details["risk_ids"][: 1 + (number % 3)]:
                product_risks.append({"product_id": product_id, "risk_id": risk_id})
    return products, product_features, market_metrics, product_risks


def _competitors(products: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[str]] = {}
    for product in products:
        grouped.setdefault(product["category_id"], []).append(product["product_id"])
    rows: list[dict[str, str]] = []
    for product_ids in grouped.values():
        for index, product_id in enumerate(product_ids):
            for offset in range(1, 6):
                rows.append(
                    {
                        "product_id": product_id,
                        "competitor_product_id": product_ids[(index + offset) % len(product_ids)],
                    }
                )
    return rows


def generate_seed_data(output_dir: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    """Write a reproducible 96-product synthetic dataset and return its manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    categories = _categories()
    catalog = _catalog()
    products, product_features, market_metrics, product_risks = _products_and_relationships(
        rng, catalog
    )
    brands = [
        {"brand_id": brand_id, "name": brand_id.removeprefix("brand_").replace("_", " ").title()}
        for details in catalog.values()
        for brand_id in details["brands"]
    ]
    features = [
        {"feature_id": feature_id, "name": name, "value": name}
        for details in catalog.values()
        for feature_id, name in details["features"]
    ]
    risk_attributes = [
        {
            "risk_id": "risk_battery",
            "name": "battery",
            "description": "Battery transport and labeling review",
            "risk_level": "high",
        },
        {
            "risk_id": "risk_electrical",
            "name": "electrical",
            "description": "Electrical safety review",
            "risk_level": "high",
        },
        {
            "risk_id": "risk_wireless",
            "name": "wireless",
            "description": "Wireless radio compliance review",
            "risk_level": "medium",
        },
        {
            "risk_id": "risk_age",
            "name": "age",
            "description": "Age grading review",
            "risk_level": "high",
        },
        {
            "risk_id": "risk_toy_material",
            "name": "material",
            "description": "Toy material safety review",
            "risk_level": "high",
        },
        {
            "risk_id": "risk_small_parts",
            "name": "small_parts",
            "description": "Small parts hazard review",
            "risk_level": "high",
        },
        {
            "risk_id": "risk_home_material",
            "name": "material",
            "description": "Home material review",
            "risk_level": "medium",
        },
        {
            "risk_id": "risk_load_bearing",
            "name": "load_bearing",
            "description": "Load bearing review",
            "risk_level": "medium",
        },
        {
            "risk_id": "risk_food_contact",
            "name": "food_contact",
            "description": "Food contact material review",
            "risk_level": "high",
        },
    ]
    fee_rules = [
        {
            "fee_rule_id": f"fee_{category['category_id']}",
            "category_id": category["category_id"],
            "fee_id": f"fee_{category['category_id']}",
            "marketplace": "amazon_us",
            "referral_rate": "0.15",
            "referral_fee_rate": "0.15",
            "commission_rate": "0.15",
            "fulfillment_fee": "4.95",
            "fba_fee": "4.95",
            "fba_fee_usd": "4.95",
            "fx_cny_per_usd": "7.20",
            "min_weight_kg": "0.00",
            "max_weight_kg": "2.00",
            "effective_date": "2026-01-01",
        }
        for category in categories
    ]
    compliance_rules = [
        {
            "rule_id": f"rule_{category['category_id']}_{index}",
            "name": f"{category['name']} preparation rule {index}",
            "scope": category["name"],
            "description": "Synthetic compliance preparation reference; not legal advice.",
            "summary": "Synthetic compliance preparation reference; not legal advice.",
        }
        for category in categories
        for index in range(1, 3)
    ]
    documents = [
        {
            "document_id": f"doc_{index}",
            "title": f"Synthetic preparation reference {index}",
            "name": f"Synthetic preparation reference {index}",
            "source": "synthetic",
            "issuer_type": "synthetic_reference",
            "validity_note": "Synthetic offline demonstration data; not an official document.",
        }
        for index in range(1, len(compliance_rules) + 1)
    ]
    rule_documents = [
        {"rule_id": rule["rule_id"], "document_id": documents[index]["document_id"]}
        for index, rule in enumerate(compliance_rules)
    ]
    rule_categories = [
        {"rule_id": rule["rule_id"], "category_id": categories[index // 2]["category_id"]}
        for index, rule in enumerate(compliance_rules)
    ]
    category_by_product = {product["product_id"]: product["category_id"] for product in products}
    rules_by_category = {
        category["category_id"]: [
            f"rule_{category['category_id']}_{index}" for index in range(1, 3)
        ]
        for category in categories
    }
    for index, product_risk in enumerate(product_risks):
        product_risk["rule_id"] = rules_by_category[
            category_by_product[product_risk["product_id"]]
        ][index % 2]
    trigger_count = len({(row["risk_id"], row["rule_id"]) for row in product_risks})
    datasets: dict[str, list[dict[str, str]]] = {
        "categories.csv": categories,
        "brands.csv": brands,
        "products.csv": products,
        "features.csv": features,
        "product_features.csv": product_features,
        "market_metrics.csv": market_metrics,
        "fee_rules.csv": fee_rules,
        "risk_attributes.csv": risk_attributes,
        "product_risks.csv": product_risks,
        "compliance_rules.csv": compliance_rules,
        "documents.csv": documents,
        "rule_documents.csv": rule_documents,
        "rule_categories.csv": rule_categories,
        "product_competitors.csv": _competitors(products),
    }
    for filename, rows in datasets.items():
        _write_csv(output_dir, filename, rows)
    manifest: dict[str, Any] = {
        "seed": SEED,
        "synthetic_data": True,
        "csv_row_counts": {filename: len(rows) for filename, rows in datasets.items()},
        "node_expectations": {
            "Category": len(categories),
            "Brand": len(brands),
            "Marketplace": 1,
            "Product": len(products),
            "Feature": len(features),
            "MarketMetric": len(market_metrics),
            "FeeRule": len(fee_rules),
            "RiskAttribute": len(risk_attributes),
            "ComplianceRule": len(compliance_rules),
            "Document": len(documents),
        },
        "relationship_expectations": {
            "BELONGS_TO": len(products),
            "MADE_BY": len(products),
            "SOLD_ON": len(products),
            "HAS_FEATURE": len(product_features),
            "HAS_MARKET_METRIC": len(market_metrics),
            "USES_FEE_RULE": len(fee_rules),
            "HAS_RISK_ATTRIBUTE": len(product_risks),
            "TRIGGERS": trigger_count,
            "REQUIRES_DOCUMENT": len(rule_documents),
            "APPLIES_TO": len(rule_categories),
            "COMPETES_WITH": len(datasets["product_competitors.csv"]),
        },
    }
    (manifest_path or output_dir / "seed_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    repository_data_dir = Path(__file__).resolve().parents[1] / "data"
    generate_seed_data(repository_data_dir / "csv", repository_data_dir / "seed_manifest.json")
