# Cargo Yield Agent

End-to-end Databricks demo: a multi-step AI agent that prices air cargo RFQs in
real time using historical yield, ML-predicted base rates, competitor intel, and
handling rules from a vector-indexed knowledge base. Modeled on Virgin Atlantic
Cargo's footprint (~200K tonnes / £236M revenue / 26K flights per year).

The demo covers:

- **Synthetic data generator** — 1.1M+ AWBs, flight schedules, forwarders, competitor rates
- **Lakeflow Declarative Pipeline** — bronze → silver → gold with DQ expectations
- **ML model** — yield prediction (sklearn → MLflow → Model Serving)
- **Knowledge base** — IATA handling rules indexed in Vector Search
- **Agent** — MLflow PyFunc deployed to Model Serving, calling SQL, ML, VS, and an LLM
- **Databricks App** — FastAPI + static UI showing RFQ inbox, agent quotes, and Genie chat

## Prerequisites

- Databricks workspace with **Unity Catalog**, **Serverless SQL**, **Model Serving**, **Vector Search**, and **Databricks Apps** enabled
- Permission to create catalogs (or an existing catalog you own)
- A **Foundation Model API** endpoint for Claude (or another chat LLM — see config below)
- Python 3.10+ locally with `polars` and the Databricks CLI configured

```bash
pip install polars databricks-cli
databricks auth login --host https://<your-workspace>.cloud.databricks.com
```

## Configuration — what to change

The repo is wired to one specific workspace. Before running anything, find/replace
these values across the repo. The fastest way is a search-and-replace in your editor.

| Placeholder in repo | What it is | Where it appears |
|---|---|---|
| `serverless_nal_catalog` | Unity Catalog name | `sql/*.sql`, `cargo_demo_pipeline/*.py`, `cargo_demo_app/app.yaml` |
| `410652ea4402d5bf` | SQL warehouse ID | `cargo_demo_pipeline/build_agent.py`, `cargo_demo_app/app.yaml` |
| `nalvs` | Vector Search endpoint name | `cargo_demo_pipeline/sync_vector_index.py`, `build_agent.py`, `app.yaml` |
| `databricks-claude-sonnet-4-6` | Foundation Model endpoint | `build_agent.py`, `app.yaml` |
| `01f14df56bd21782be011a99ee0c80d2` | Genie space ID (optional) | `cargo_demo_app/app.yaml` |
| `d409264b-78aa-4a9e-963a-c56781fdbf5f` | App service principal ID | `sql/05_grant_app_perms.sql` |

To find your app's service principal ID, deploy the app once (step 7) then run
`databricks apps get cargo-yield-agent` and copy `service_principal_client_id`.

## Setup

### 1. Generate synthetic data

```bash
python generate_cargo_data.py
```

Writes 6 parquet files to `/tmp/cargo_demo/data/`. Takes 1–2 minutes.

### 2. Create the catalog + schemas

In a Databricks SQL editor or the Databricks CLI, run:

```bash
databricks sql query --warehouse-id <YOUR_WAREHOUSE_ID> --file sql/01_setup_catalog.sql
```

This creates the catalog (default: `cargo_demo` — note this differs from the
runtime catalog `serverless_nal_catalog` used by the pipeline; align both to
your chosen catalog name).

### 3. Upload parquet to a UC volume and create bronze tables

Create a volume in your chosen catalog/schema, then upload the generated files:

```bash
databricks fs mkdir dbfs:/Volumes/<catalog>/cargo_bronze/landing
databricks fs cp -r /tmp/cargo_demo/data dbfs:/Volumes/<catalog>/cargo_bronze/landing/
```

Then in a SQL editor, create the bronze tables from the parquet files:

```sql
CREATE OR REPLACE TABLE <catalog>.cargo_bronze.awb_raw
  AS SELECT * FROM read_files('/Volumes/<catalog>/cargo_bronze/landing/data/bronze_awb_raw.parquet');
-- repeat for: bronze_flight_schedule, bronze_forwarders, bronze_commodities,
--             bronze_competitor_rates, bronze_rfq_inbox
```

### 4. Run the Lakeflow pipeline (bronze → silver → gold)

Either run the SQL directly:

```bash
databricks sql query --warehouse-id <YOUR_WAREHOUSE_ID> --file sql/03_build_silver_gold.sql
```

Or create a Lakeflow Declarative Pipeline pointing at
`cargo_demo_pipeline/cargo_yield_pipeline.py` with default target schema
`cargo_silver` — recommended, since it brings DQ expectations.

### 5. Train the yield model

Run `cargo_demo_pipeline/train_yield_model.py` as a notebook or job. It logs a
sklearn regressor to MLflow, registers it as `<catalog>.cargo_ai.yield_model`,
and deploys a Model Serving endpoint named `cargo-yield-model`.

### 6. Build the knowledge base + vector index

```bash
databricks sql query --warehouse-id <YOUR_WAREHOUSE_ID> --file sql/04_build_knowledge_base.sql
```

Then run `cargo_demo_pipeline/sync_vector_index.py` to create
`<catalog>.cargo_ai.knowledge_base_index` on your Vector Search endpoint.

### 7. Build and deploy the agent

Run `cargo_demo_pipeline/build_agent.py` as a notebook. It:

1. Defines the agent as an `mlflow.pyfunc.PythonModel`
2. Logs it with declarative resources (warehouse, VS index, model serving, FM endpoint)
3. Registers it as `<catalog>.cargo_ai.yield_agent`
4. Deploys it to a Model Serving endpoint named `cargo-yield-agent`

### 8. Deploy the Databricks App

```bash
databricks apps create cargo-yield-agent
databricks apps deploy cargo-yield-agent --source-code-path $(pwd)/cargo_demo_app
```

### 9. Grant the app permission to read your tables

Get the app's service principal ID, paste it into `sql/05_grant_app_perms.sql`
(replacing the placeholder UUID), and run:

```bash
databricks sql query --warehouse-id <YOUR_WAREHOUSE_ID> --file sql/05_grant_app_perms.sql
```

Restart the app and open its URL from `databricks apps list`.

## Repo layout

```
cargo-yield-agent/
├── generate_cargo_data.py        # Synthetic data generator
├── sql/                          # Catalog setup + silver/gold/KB SQL
│   ├── 01_setup_catalog.sql
│   ├── 02_setup_schemas.sql
│   ├── 03_build_silver_gold.sql
│   ├── 04_build_knowledge_base.sql
│   └── 05_grant_app_perms.sql
├── cargo_demo_pipeline/          # Notebooks for pipeline, model, KB, agent
│   ├── cargo_yield_pipeline.py
│   ├── train_yield_model.py
│   ├── sync_vector_index.py
│   ├── build_agent.py
│   └── run_quality_checks.py
└── cargo_demo_app/               # FastAPI + static UI Databricks App
    ├── app.yaml
    ├── requirements.txt
    ├── backend/
    └── static/
```

## Verifying the setup

After step 7, hit the agent endpoint with a test RFQ:

```bash
databricks serving-endpoints query cargo-yield-agent --json '{
  "dataframe_records": [{
    "rfq_id": "RFQ-TEST-001",
    "origin_iata": "LHR",
    "destination_iata": "JFK",
    "commodity_code": "PHC",
    "requested_weight_kg": 2000,
    "flight_date": "2026-06-15",
    "forwarder_name": "DSV Air & Sea"
  }]
}'
```

Expected response: a quoted rate, supporting context (capacity, historical yield,
competitor rates, handling rules), and an LLM-drafted rationale.

## Troubleshooting

- **`Table or view not found` during pipeline run** — bronze tables aren't built yet. Re-check step 3.
- **Agent endpoint 403 on VS or warehouse** — re-deploy from `build_agent.py`; declarative resources auto-grant the endpoint's service principal.
- **App can't read tables** — step 9 wasn't run with the right service principal ID. Run `databricks apps get cargo-yield-agent` to confirm.
- **No Genie chat in the app** — optional; either set up a Genie space against the gold schema and update `GENIE_SPACE_ID` in `app.yaml`, or leave the panel disabled.
