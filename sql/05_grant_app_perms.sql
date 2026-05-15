-- Template: rendered by scripts/render.sh
-- Run this AFTER the app is deployed (so its service principal exists).
-- Set APP_SP_ID in .env, then re-run scripts/render.sh.

GRANT USE CATALOG ON CATALOG ${CATALOG} TO `${APP_SP_ID}`;
GRANT USE SCHEMA ON SCHEMA ${CATALOG}.cargo_bronze TO `${APP_SP_ID}`;
GRANT USE SCHEMA ON SCHEMA ${CATALOG}.cargo_silver TO `${APP_SP_ID}`;
GRANT USE SCHEMA ON SCHEMA ${CATALOG}.cargo_gold TO `${APP_SP_ID}`;
GRANT USE SCHEMA ON SCHEMA ${CATALOG}.cargo_ai TO `${APP_SP_ID}`;
GRANT SELECT ON SCHEMA ${CATALOG}.cargo_bronze TO `${APP_SP_ID}`;
GRANT SELECT ON SCHEMA ${CATALOG}.cargo_silver TO `${APP_SP_ID}`;
GRANT SELECT ON SCHEMA ${CATALOG}.cargo_gold TO `${APP_SP_ID}`;
GRANT SELECT ON SCHEMA ${CATALOG}.cargo_ai TO `${APP_SP_ID}`;
