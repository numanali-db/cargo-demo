-- Cargo Yield Agent Demo - Catalog Setup
CREATE CATALOG IF NOT EXISTS cargo_demo COMMENT 'Virgin Atlantic Cargo Yield Agent Demo';

CREATE SCHEMA IF NOT EXISTS cargo_demo.bronze COMMENT 'Raw cargo data feeds';
CREATE SCHEMA IF NOT EXISTS cargo_demo.silver COMMENT 'Cleaned and enriched cargo data';
CREATE SCHEMA IF NOT EXISTS cargo_demo.gold COMMENT 'Business-ready cargo analytics';
CREATE SCHEMA IF NOT EXISTS cargo_demo.ai COMMENT 'AI artifacts: models, vector indexes, agents';
CREATE SCHEMA IF NOT EXISTS cargo_demo.ops COMMENT 'Operational tables synced from Lakebase';

SELECT 'Catalog cargo_demo created with schemas: bronze, silver, gold, ai, ops' AS status;
