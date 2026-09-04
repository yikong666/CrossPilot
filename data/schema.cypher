// CrossPilot's synthetic Amazon-US product graph schema.
CREATE CONSTRAINT category_id_unique IF NOT EXISTS FOR (n:Category) REQUIRE n.category_id IS UNIQUE;
CREATE CONSTRAINT brand_id_unique IF NOT EXISTS FOR (n:Brand) REQUIRE n.brand_id IS UNIQUE;
CREATE CONSTRAINT marketplace_id_unique IF NOT EXISTS FOR (n:Marketplace) REQUIRE n.marketplace_id IS UNIQUE;
CREATE CONSTRAINT product_id_unique IF NOT EXISTS FOR (n:Product) REQUIRE n.product_id IS UNIQUE;
CREATE CONSTRAINT feature_id_unique IF NOT EXISTS FOR (n:Feature) REQUIRE n.feature_id IS UNIQUE;
CREATE CONSTRAINT metric_id_unique IF NOT EXISTS FOR (n:MarketMetric) REQUIRE n.metric_id IS UNIQUE;
CREATE CONSTRAINT fee_rule_id_unique IF NOT EXISTS FOR (n:FeeRule) REQUIRE n.fee_rule_id IS UNIQUE;
CREATE CONSTRAINT risk_id_unique IF NOT EXISTS FOR (n:RiskAttribute) REQUIRE n.risk_id IS UNIQUE;
CREATE CONSTRAINT rule_id_unique IF NOT EXISTS FOR (n:ComplianceRule) REQUIRE n.rule_id IS UNIQUE;
CREATE CONSTRAINT document_id_unique IF NOT EXISTS FOR (n:Document) REQUIRE n.document_id IS UNIQUE;

CREATE INDEX product_category_idx IF NOT EXISTS FOR (n:Product) ON (n.category_id);
CREATE INDEX product_brand_idx IF NOT EXISTS FOR (n:Product) ON (n.brand_id);
CREATE INDEX product_price_idx IF NOT EXISTS FOR (n:Product) ON (n.price);
CREATE INDEX compliance_rule_name_idx IF NOT EXISTS FOR (n:ComplianceRule) ON (n.name);

CREATE VECTOR INDEX product_name_embeddings IF NOT EXISTS
FOR (n:Product) ON (n.name_embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}};
