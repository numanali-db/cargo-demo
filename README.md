# Cargo Yield Agent

End-to-end Databricks demo: a multi-step AI agent that prices air cargo RFQs in
real time using historical yield, ML-predicted base rates, competitor intel, and
handling rules from a vector-indexed knowledge base. Modeled on Virgin Atlantic
Cargo's footprint (~200K tonnes / £236M revenue / 26K flights per year).

## What is it?

When a freight forwarder needs to ship a pallet of pharma from London to Delhi,
they fire an RFQ (request for quote) to every airline that flies the lane. The
airline's cargo team has minutes to come back with a rate that wins the booking
without leaving money on the table. Today, a yield analyst answers each RFQ
manually — pulling up flight load factors in one system, historical rates in
another, the latest competitor scrape in a third, and the IATA handling rules
in a PDF — then makes a judgement call.

The Cargo Yield Agent automates that decision. For every inbound RFQ it runs a
five-step pipeline:

1. **Capacity check** — how full is the target flight, and how does this booking move the load factor?
2. **Yield calculation** — an ML model predicts the base rate for this lane × commodity × forwarder × season, with the last 90 days of actuals as a sanity bound.
3. **Competitive check** — compares the recommended rate against scraped competitor pricing on the same lane and commodity.
4. **Rules retrieval** — pulls the relevant IATA / airline handling rules (pharma cool chain, AOG SLAs, dangerous-goods limits) from a vector-indexed knowledge base.
5. **Quote drafting** — Claude synthesises a quote with a quantitative rationale the analyst can sign off on or override.

Every run produces an MLflow trace so the analyst can see exactly why the agent
recommended £3.42/kg and not £3.10 — capacity, history, competitors, rules.

## Why airlines should care

Cargo is high-margin, high-volatility, and famously underautomated. A 1–2% yield
lift on the back of a £200M cargo book is £2–4M of pure margin per year, with no
capex and no new aircraft. The levers the agent pulls — capacity-aware pricing,
faster RFQ turnaround, consistent competitive positioning — directly attack the
same problems every cargo team has:

- **Speed** — AOG and pharma RFQs need a confirmed quote in under 30 minutes. Analysts on Mondays at 0800 can't realistically clear an inbox of 80 RFQs that fast. The agent answers in seconds and routes only the strategic / unusual ones to a human.
- **Yield discipline** — manual quoting drifts. Two analysts on the same desk will price the same LHR–JFK pharma booking differently. The agent enforces the same logic every time and makes the deviation auditable.
- **Capacity utilisation** — when a flight is 82% full and trending to a sellout, the agent automatically pushes the capacity premium; when it's 50% full with 5 days to depart, it discounts tactically to fill the belly. Most airlines do this by gut.
- **Competitive responsiveness** — when IAG Cargo cuts £0.30 on LHR–BOM pharma overnight, the agent sees it the next morning and adjusts. No one has to remember to check.
- **Premium cargo focus** — the highest-yield commodities (pharma, perishables, AOG, valuables) have the strictest handling rules. Surfacing those rules at quote time prevents the most expensive class of mistake: accepting cargo the aircraft can't actually carry.

For Databricks-side stakeholders: this demo is a worked example of pulling
**Lakeflow, Unity Catalog, Model Serving, Vector Search, Foundation Model API,
Genie, and Databricks Apps** into a single agentic workflow on real airline-shaped
data — useful for any conversation with a logistics, airline, or distribution
customer where pricing, capacity, or quoting is on the table.

## What's in this repo

- **Synthetic data generator** — 1M+ AWBs, flight schedules, forwarders, competitor rates
- **Lakeflow Declarative Pipeline** — bronze → silver → gold with DQ expectations
- **ML model** — yield prediction (sklearn → MLflow → Model Serving)
- **Knowledge base** — IATA handling rules indexed in Vector Search
- **Agent** — MLflow PyFunc deployed to Model Serving, calling SQL, ML, VS, and an LLM
- **Databricks App** — FastAPI + static UI showing RFQ inbox, agent quotes, and Genie chat

## Prerequisites

- Databricks workspace with **Unity Catalog**, **Serverless SQL**, **Model Serving**, **Vector Search**, and **Databricks Apps** enabled
- Permission to create catalogs
- A **Foundation Model API** endpoint for Claude (or another chat LLM)
- An existing **Vector Search endpoint** (create one if needed)
- Python 3.10+ locally with the Databricks CLI configured
- `envsubst` available locally (ships with `gettext` — on macOS: `brew install gettext`)

```bash
pip install polars
databricks auth login --host https://<your-workspace>.cloud.databricks.com
```

## Configure once: `.env`

All workspace-specific values are read from a single `.env` file at the repo root.

```bash
cp .env.example .env
# edit .env and fill in: CATALOG, WAREHOUSE_ID, VS_ENDPOINT, LLM_ENDPOINT, ...
./scripts/render.sh
```

`scripts/render.sh` reads `.env` and:

- Generates `build/sql/*.sql` (gitignored) — SQL files with placeholders substituted
- Substitutes placeholders in `cargo_demo_app/app.yaml` **in place** (the file is tracked because Databricks Apps requires a real `app.yaml` in the source tree)

`.env` is gitignored. After deploying via CLI, revert your local `app.yaml`
edits with `git checkout cargo_demo_app/app.yaml` so workspace values don't
leak into commits.

The Python notebooks in `cargo_demo_pipeline/` use `dbutils.widgets` instead of
envsubst — pass the same values as job parameters or set them interactively in the
notebook UI.

## Deployment steps

### 1. Generate synthetic data

```bash
python generate_cargo_data.py
```

Writes 6 parquet files to `/tmp/cargo_demo/data/`. Takes 1–2 minutes.

### 2. Create the catalog + schemas

```bash
databricks sql query --warehouse-id "$WAREHOUSE_ID" --file build/sql/01_setup_catalog.sql
```

This creates the catalog from `.env` and its five schemas (`cargo_bronze`,
`cargo_silver`, `cargo_gold`, `cargo_ai`, `cargo_ops`).

### 3. Upload parquet to a UC volume and create bronze tables

```bash
databricks fs mkdir "dbfs:/Volumes/$CATALOG/cargo_bronze/landing"
databricks fs cp -r /tmp/cargo_demo/data "dbfs:/Volumes/$CATALOG/cargo_bronze/landing/"
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
databricks sql query --warehouse-id "$WAREHOUSE_ID" --file build/sql/03_build_silver_gold.sql
```

Or create a Lakeflow Declarative Pipeline pointing at
`cargo_demo_pipeline/cargo_yield_pipeline.py` with default target schema
`cargo_silver` — recommended, since it brings DQ expectations.

The pipeline reads its catalog name from a Spark conf — set
`catalog=<your-catalog>` in **Settings → Advanced → Configuration**.

### 5. Train the yield model

Run `cargo_demo_pipeline/train_yield_model.py` as a notebook or job. At the top,
fill in the `catalog` widget. It logs a sklearn regressor to MLflow, registers it
as `<catalog>.cargo_ai.yield_model`, and deploys a Model Serving endpoint named
from `YIELD_MODEL_ENDPOINT` in `.env`.

### 6. Build the knowledge base + vector index

```bash
databricks sql query --warehouse-id "$WAREHOUSE_ID" --file build/sql/04_build_knowledge_base.sql
```

Then run `cargo_demo_pipeline/sync_vector_index.py` (set widgets `catalog` and
`vs_endpoint`) to create `<catalog>.cargo_ai.knowledge_base_index` on your
Vector Search endpoint.

### 7. Build and deploy the agent

Run `cargo_demo_pipeline/build_agent.py` as a notebook. Set widgets: `catalog`,
`warehouse_id`, `vs_endpoint`, `llm_endpoint`, `yield_model_endpoint`. The notebook:

1. Defines the agent as an `mlflow.pyfunc.PythonModel`
2. Bakes the widget values into the agent source code at build time
3. Logs with declarative resources (warehouse, VS index, model serving, FM endpoint)
4. Registers as `<catalog>.cargo_ai.yield_agent` and deploys to a Model Serving endpoint

### 8. Deploy the Databricks App

Two deploy flows are supported. Pick one.

**Option A — CLI deploy (recommended for local development)**

`scripts/render.sh` substitutes the placeholders in `cargo_demo_app/app.yaml`
with values from `.env`, then you deploy:

```bash
./scripts/render.sh   # substitutes ${CATALOG}, ${WAREHOUSE_ID}, ... in app.yaml
databricks apps create "$APP_NAME"
databricks apps deploy "$APP_NAME" --source-code-path "$(pwd)/cargo_demo_app"
git checkout cargo_demo_app/app.yaml   # revert local substitutions
```

**Option B — Deploy from GitHub source**

The committed `cargo_demo_app/app.yaml` contains `${VAR}` placeholders. When
Databricks Apps pulls from GitHub, those literal strings become the default env
values — you need to override them in the workspace UI:

1. Create the app pointing at your GitHub repo, with source path `cargo_demo_app/`.
2. Once created, open the app → **Settings → Environment Variables**.
3. Set each variable (`CATALOG`, `DATABRICKS_WAREHOUSE_ID`, `LLM_ENDPOINT`,
   `VS_ENDPOINT`, `VS_INDEX`, `GENIE_SPACE_ID`, `AGENT_ENDPOINT`) to your
   workspace values. Workspace UI values override `app.yaml`.
4. Restart the app.

### 9. Grant the app permission to read your tables

After the app is deployed, get its service principal client ID:

```bash
databricks apps get "$APP_NAME" --output json | jq -r '.service_principal_client_id'
```

Paste that value into `APP_SP_ID` in `.env`, re-render, and apply:

```bash
./scripts/render.sh
databricks sql query --warehouse-id "$WAREHOUSE_ID" --file build/sql/05_grant_app_perms.sql
```

Restart the app and open its URL from `databricks apps list`.

## Repo layout

```
cargo-yield-agent/
├── .env.example                  # Template - copy to .env and fill in
├── scripts/render.sh             # Renders SQL + app.yaml from .env
├── generate_cargo_data.py        # Synthetic data generator (local-only, no params)
├── sql/                          # SQL templates with ${VAR} placeholders
│   ├── 01_setup_catalog.sql
│   ├── 02_setup_schemas.sql
│   ├── 03_build_silver_gold.sql
│   ├── 04_build_knowledge_base.sql
│   └── 05_grant_app_perms.sql
├── cargo_demo_pipeline/          # Databricks notebooks (use dbutils.widgets)
│   ├── cargo_yield_pipeline.py   # Reads `catalog` from spark.conf (DLT)
│   ├── train_yield_model.py
│   ├── sync_vector_index.py
│   ├── build_agent.py
│   └── run_quality_checks.py
└── cargo_demo_app/
    ├── app.yaml                  # Committed with ${VAR} placeholders; CLI render fills them in
    ├── requirements.txt
    ├── backend/                  # All values from env vars (set in app.yaml)
    └── static/
```

## Verifying the setup

After step 7, hit the agent endpoint with a test RFQ:

```bash
databricks serving-endpoints query "$AGENT_ENDPOINT" --json '{
  "dataframe_records": [{
    "rfq": "{\"rfq_id\":\"RFQ-TEST-001\",\"flight_id\":\"VS100001\",\"flight_date\":\"2026-06-15\",\"origin_iata\":\"LHR\",\"destination_iata\":\"JFK\",\"forwarder_name\":\"DSV Air & Sea\",\"commodity_code\":\"PHC\",\"requested_weight_kg\":2000}"
  }]
}'
```

Expected response: a per-step trace with capacity check, ML yield prediction,
competitive position, retrieved handling rules, and an LLM-drafted rationale.

## Troubleshooting

- **`envsubst: command not found`** — install `gettext`: `brew install gettext` (macOS) or `apt install gettext` (Linux).
- **`ERROR: missing required values in .env`** — render.sh tells you which keys are blank. Fill them in and re-run.
- **`Table or view not found` during pipeline run** — bronze tables aren't built yet. Re-check step 3.
- **Agent endpoint 403 on VS or warehouse** — re-deploy from `build_agent.py`; declarative resources auto-grant the endpoint's service principal.
- **App can't read tables** — step 9 wasn't run with the right service principal ID. Run `databricks apps get "$APP_NAME"` to confirm and re-render.
- **No Genie chat in the app** — optional; set `GENIE_SPACE_ID` in `.env` and re-render. Leave blank to disable.
