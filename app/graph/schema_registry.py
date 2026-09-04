from __future__ import annotations

import hashlib
from collections.abc import Mapping
from types import MappingProxyType


class SchemaRegistry:
    """Allowlisted graph schema exposed to generation and validation."""

    _LABEL_PROPERTIES: Mapping[str, frozenset[str]] = MappingProxyType(
        {
            "Product": frozenset(
                {
                    "product_id", "name", "title_en", "price", "rating",
                    "review_count", "bsr", "aliases", "name_embedding",
                }
            ),
            "Category": frozenset(
                {"category_id", "name", "category_group", "aliases"}
            ),
            "Brand": frozenset({"brand_id", "name", "aliases"}),
            "Marketplace": frozenset(
                {"marketplace_id", "country", "platform", "currency"}
            ),
            "Feature": frozenset({"feature_id", "name", "value"}),
            "MarketMetric": frozenset(
                {"metric_id", "demand_level", "period", "sample_size"}
            ),
            "FeeRule": frozenset(
                {"fee_id", "commission_rate", "fba_fee", "effective_date"}
            ),
            "RiskAttribute": frozenset({"risk_id", "name", "risk_level"}),
            "ComplianceRule": frozenset(
                {"rule_id", "name", "scope", "description"}
            ),
            "Document": frozenset(
                {"document_id", "name", "issuer_type", "validity_note"}
            ),
        }
    )
    _RELATIONSHIPS = frozenset(
        {
            "BELONGS_TO", "MADE_BY", "SOLD_ON", "HAS_FEATURE", "COMPETES_WITH",
            "HAS_MARKET_METRIC", "USES_FEE_RULE", "HAS_RISK_ATTRIBUTE", "TRIGGERS",
            "REQUIRES_DOCUMENT", "APPLIES_TO",
        }
    )

    @property
    def labels(self) -> frozenset[str]:
        return frozenset(self._LABEL_PROPERTIES)

    @property
    def relationships(self) -> frozenset[str]:
        return self._RELATIONSHIPS

    @property
    def label_properties(self) -> Mapping[str, frozenset[str]]:
        return self._LABEL_PROPERTIES

    def properties_for(self, label: str) -> frozenset[str]:
        return self._LABEL_PROPERTIES.get(label, frozenset())

    def compact_text(self) -> str:
        nodes = [
            f"{label}({', '.join(sorted(properties))})"
            for label, properties in self._LABEL_PROPERTIES.items()
        ]
        relationships = ", ".join(sorted(self._RELATIONSHIPS))
        return "Nodes: " + "; ".join(nodes) + f"\nRelationships: {relationships}"

    @property
    def version_hash(self) -> str:
        return hashlib.sha256(self.compact_text().encode("utf-8")).hexdigest()
