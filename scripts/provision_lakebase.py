#!/usr/bin/env python3
"""
Provision the Lakebase Postgres backend for the Cargo Yield Agent demo.

Creates the operational (OLTP) tables in the `cargo` database, grants the app
service principal least-privilege access, and seeds rfq_inbox + commodities from
the existing UC bronze tables so the app shows the same rows it does today.

Idempotent: safe to re-run (CREATE TABLE IF NOT EXISTS; seed does TRUNCATE + INSERT).

Prereqs:
  - `databricks auth login --profile <PROFILE>` (fresh token)
  - psycopg2 available (already installed in the local py3.11)

Configure via environment (values match your .env — see .env.example):
  PROFILE           databricks CLI profile               (required)
  WAREHOUSE_ID      warehouse for seed queries           (required)
  APP_SP_ID         app service principal client id      (required; grant this pg role)
  UC_CATALOG        source UC catalog for seeding        (default: cargo_demo)
  LB_PROJECT        lakebase project id                  (default: cargo-yield)
  LB_BRANCH         lakebase branch                      (default: production)
  LB_ENDPOINT       lakebase endpoint                    (default: primary)
  LB_DATABASE       postgres database name               (default: cargo)

Example:
  PROFILE=my-profile WAREHOUSE_ID=<id> \\
    APP_SP_ID=$(databricks apps get my-app -p my-profile -o json | jq -r .service_principal_client_id) \\
    python scripts/provision_lakebase.py
"""
import os
import subprocess
import json
import sys
import psycopg2
from psycopg2.extras import execute_values


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"error: environment variable {name} is required (see the module docstring / .env.example)")
    return v


PROFILE     = _require("PROFILE")
WAREHOUSE_ID = _require("WAREHOUSE_ID")
APP_SP_ID   = _require("APP_SP_ID")
UC_CATALOG  = os.environ.get("UC_CATALOG", "cargo_demo")
LB_PROJECT  = os.environ.get("LB_PROJECT", "cargo-yield")
LB_BRANCH   = os.environ.get("LB_BRANCH", "production")
LB_ENDPOINT = os.environ.get("LB_ENDPOINT", "primary")
LB_DATABASE = os.environ.get("LB_DATABASE", "cargo")

BRANCH_PATH   = f"projects/{LB_PROJECT}/branches/{LB_BRANCH}"
ENDPOINT_PATH = f"{BRANCH_PATH}/endpoints/{LB_ENDPOINT}"

DDL = """
CREATE TABLE IF NOT EXISTS rfq_inbox (
    rfq_id                        TEXT PRIMARY KEY,
    received_at                   TIMESTAMPTZ,
    flight_id                     TEXT,
    flight_date                   DATE,
    origin_iata                   TEXT,
    destination_iata              TEXT,
    forwarder_name                TEXT,
    commodity_code                TEXT,
    requested_weight_kg           NUMERIC,
    requested_pieces              INTEGER,
    ready_date                    DATE,
    special_handling              BOOLEAN,
    temp_controlled               BOOLEAN,
    status                        TEXT DEFAULT 'pending_quote',
    customer_max_rate_gbp_per_kg  NUMERIC,
    notes                         TEXT
);

CREATE TABLE IF NOT EXISTS commodities (
    commodity_code   TEXT PRIMARY KEY,
    commodity_name   TEXT,
    handling_tier    TEXT,
    rate_multiplier  NUMERIC,
    temp_controlled  BOOLEAN
);

CREATE TABLE IF NOT EXISTS quotes (
    quote_id         TEXT PRIMARY KEY,
    rfq_id           TEXT,
    rate_gbp_per_kg  NUMERIC,
    weight_kg        NUMERIC,
    revenue_gbp      NUMERIC,
    submitted_by     TEXT,
    notes            TEXT,
    status           TEXT DEFAULT 'fired_to_forwarder',
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rfq_status ON rfq_inbox (status);
CREATE INDEX IF NOT EXISTS idx_quotes_rfq ON quotes (rfq_id);
"""


def _cli_json(args):
    out = subprocess.run(["databricks", *args, "-p", PROFILE, "-o", "json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"CLI failed: databricks {' '.join(args)}\n{out.stderr}")
    return json.loads(out.stdout)


def connect():
    host = _cli_json(["postgres", "list-endpoints", BRANCH_PATH])[0]["status"]["hosts"]["host"]
    token = _cli_json(["postgres", "generate-database-credential", ENDPOINT_PATH])["token"]
    email = _cli_json(["current-user", "me"])["userName"]
    return psycopg2.connect(host=host, port=5432, dbname=LB_DATABASE,
                            user=email, password=token, sslmode="require")


def seed_rows(table, statement):
    """Run a SQL statement on the warehouse via the SDK and return list[dict]."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=PROFILE)
    res = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="50s")
    cols = [c.name for c in res.manifest.schema.columns]
    data = res.result.data_array or []
    return cols, data


def main():
    conn = connect()
    conn.autocommit = True
    cur = conn.cursor()

    print("→ creating tables …")
    cur.execute(DDL)

    print(f"→ granting app SP {APP_SP_ID} …")
    cur.execute(f'GRANT USAGE ON SCHEMA public TO "{APP_SP_ID}";')
    cur.execute(f'GRANT SELECT ON rfq_inbox, commodities TO "{APP_SP_ID}";')
    cur.execute(f'GRANT SELECT, INSERT, UPDATE ON rfq_inbox TO "{APP_SP_ID}";')
    cur.execute(f'GRANT SELECT, INSERT, UPDATE ON quotes TO "{APP_SP_ID}";')

    print("→ seeding commodities from UC bronze …")
    cols, rows = seed_rows("commodities", f"""
        SELECT commodity_code, commodity_name, handling_tier,
               CAST(rate_multiplier AS DOUBLE) AS rate_multiplier, temp_controlled
        FROM {UC_CATALOG}.cargo_bronze.commodities
    """)
    cur.execute("TRUNCATE commodities;")
    execute_values(cur,
        "INSERT INTO commodities (commodity_code, commodity_name, handling_tier, rate_multiplier, temp_controlled) VALUES %s",
        [[r[0], r[1], r[2], r[3], (str(r[4]).lower() == "true")] for r in rows])
    print(f"   commodities: {len(rows)} rows")

    print("→ seeding rfq_inbox from UC bronze …")
    cols, rows = seed_rows("rfq_inbox", f"""
        SELECT rfq_id, received_at, flight_id, flight_date, origin_iata, destination_iata,
               forwarder_name, commodity_code, requested_weight_kg, requested_pieces,
               ready_date, special_handling, temp_controlled, status,
               customer_max_rate_gbp_per_kg, notes
        FROM {UC_CATALOG}.cargo_bronze.rfq_inbox
    """)
    idx = {c: i for i, c in enumerate(cols)}

    def b(v):  # bronze booleans come back as 'true'/'false' strings
        return None if v is None else (str(v).lower() == "true")

    cur.execute("TRUNCATE rfq_inbox;")
    execute_values(cur, """
        INSERT INTO rfq_inbox (rfq_id, received_at, flight_id, flight_date, origin_iata,
            destination_iata, forwarder_name, commodity_code, requested_weight_kg,
            requested_pieces, ready_date, special_handling, temp_controlled, status,
            customer_max_rate_gbp_per_kg, notes) VALUES %s
        """,
        [[r[idx["rfq_id"]], r[idx["received_at"]], r[idx["flight_id"]], r[idx["flight_date"]],
          r[idx["origin_iata"]], r[idx["destination_iata"]], r[idx["forwarder_name"]],
          r[idx["commodity_code"]], r[idx["requested_weight_kg"]], r[idx["requested_pieces"]],
          r[idx["ready_date"]], b(r[idx["special_handling"]]), b(r[idx["temp_controlled"]]),
          r[idx["status"]], r[idx["customer_max_rate_gbp_per_kg"]], r[idx["notes"]]]
         for r in rows])
    print(f"   rfq_inbox: {len(rows)} rows")

    cur.execute("SELECT count(*) FROM rfq_inbox;"); n_rfq = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM commodities;"); n_com = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM quotes;"); n_q = cur.fetchone()[0]
    print(f"\n✓ done — rfq_inbox={n_rfq}, commodities={n_com}, quotes={n_q}")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
