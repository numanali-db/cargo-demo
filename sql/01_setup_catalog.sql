-- Cargo Yield Agent Demo - Catalog Setup
-- Template: rendered by scripts/render.sh into build/sql/01_setup_catalog.sql

CREATE CATALOG IF NOT EXISTS ${CATALOG} COMMENT 'Virgin Atlantic Cargo Yield Agent Demo';

CREATE SCHEMA IF NOT EXISTS ${CATALOG}.cargo_bronze COMMENT 'Cargo demo - raw data feeds';
CREATE SCHEMA IF NOT EXISTS ${CATALOG}.cargo_silver COMMENT 'Cleaned and enriched cargo data';
CREATE SCHEMA IF NOT EXISTS ${CATALOG}.cargo_gold COMMENT 'Business-ready cargo analytics';
CREATE SCHEMA IF NOT EXISTS ${CATALOG}.cargo_ai COMMENT 'AI artifacts: models, vector indexes, agents';
CREATE SCHEMA IF NOT EXISTS ${CATALOG}.cargo_ops COMMENT 'Operational tables synced from Lakebase';

SELECT 'Catalog ${CATALOG} created with schemas: cargo_bronze, cargo_silver, cargo_gold, cargo_ai, cargo_ops' AS status;
