-- Template: rendered by scripts/render.sh
-- (Schemas are also created in 01_setup_catalog.sql; this is a no-op safety net.)

CREATE SCHEMA IF NOT EXISTS ${CATALOG}.cargo_bronze COMMENT 'Cargo demo - raw data feeds';
