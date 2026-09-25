# Databricks notebook source
# MAGIC %md
# MAGIC # Cargo Yield Agent — MLflow PyFunc Registration & Deployment
# MAGIC
# MAGIC Wraps the 5-step Cargo Yield Agent as a `mlflow.pyfunc.PythonModel`, registers it in Unity Catalog
# MAGIC as `<catalog>.cargo_ai.yield_agent`, then deploys it as a Model Serving endpoint.
# MAGIC
# MAGIC The agent calls:
# MAGIC - SQL warehouse (Statement Execution API) for capacity, historical yield, competitor data
# MAGIC - The deployed `cargo-yield-model` Model Serving endpoint for ML-predicted base rates
# MAGIC - The `cargo_ai.knowledge_base_index` Vector Search index for handling rules
# MAGIC - The `databricks-claude-sonnet-4-6` Foundation Model endpoint for rationale drafting
# MAGIC
# MAGIC MLflow Tracing is enabled — every agent run produces a trace with per-step latency.

# COMMAND ----------

import os, json, time
import mlflow
import mlflow.pyfunc
import pandas as pd
from mlflow.models.signature import infer_signature

# Try the new resources module; fall back to plain dicts if older mlflow
try:
    from mlflow.models.resources import (
        DatabricksServingEndpoint,
        DatabricksSQLWarehouse,
        DatabricksVectorSearchIndex,
        DatabricksTable,
    )
    HAS_RESOURCES = True
except ImportError:
    HAS_RESOURCES = False
    print("mlflow.models.resources not available — will skip declarative resources block")

mlflow.set_registry_uri("databricks-uc")

dbutils.widgets.text("catalog", "", "Unity Catalog name")
dbutils.widgets.text("warehouse_id", "", "SQL warehouse ID")
dbutils.widgets.text("vs_endpoint", "", "Vector Search endpoint")
dbutils.widgets.text("llm_endpoint", "databricks-claude-sonnet-4-6", "Foundation Model endpoint")
dbutils.widgets.text("yield_model_endpoint", "cargo-yield-model", "Yield model serving endpoint")
dbutils.widgets.text("agent_endpoint", "cargo-yield-agent", "Agent serving endpoint")

CATALOG = dbutils.widgets.get("catalog")
WAREHOUSE_ID = dbutils.widgets.get("warehouse_id")
VS_ENDPOINT = dbutils.widgets.get("vs_endpoint")
LLM_ENDPOINT = dbutils.widgets.get("llm_endpoint")
YIELD_MODEL_ENDPOINT = dbutils.widgets.get("yield_model_endpoint")
AGENT_ENDPOINT = dbutils.widgets.get("agent_endpoint")

assert CATALOG and WAREHOUSE_ID and VS_ENDPOINT, \
    "Set widgets: catalog, warehouse_id, vs_endpoint"

AGENT_NAME = f"{CATALOG}.cargo_ai.yield_agent"
VS_INDEX = f"{CATALOG}.cargo_ai.knowledge_base_index"

# COMMAND ----------

CARGO_AGENT_CODE = '''
"""Cargo Yield Agent — multi-step pricing pipeline as an MLflow pyfunc."""
import os, json, time
from typing import Any
import mlflow
import mlflow.pyfunc
import pandas as pd
from databricks.sdk import WorkspaceClient

# These five constants are baked in at build time by build_agent.py
CATALOG = "__CATALOG__"
WAREHOUSE_ID = "__WAREHOUSE_ID__"
YIELD_MODEL_ENDPOINT = "__YIELD_MODEL_ENDPOINT__"
LLM_ENDPOINT = "__LLM_ENDPOINT__"
VS_ENDPOINT = "__VS_ENDPOINT__"
VS_INDEX = f"{CATALOG}.cargo_ai.knowledge_base_index"


class CargoYieldAgent(mlflow.pyfunc.PythonModel):
    """Five-step cargo yield agent.

    Input schema: a pandas DataFrame with a single column `rfq` of JSON strings (or dicts).
    Output schema: a pandas DataFrame with a single column `trace` of JSON strings.
    """

    def load_context(self, context):
        self.w = WorkspaceClient()

    @mlflow.trace(span_type="CHAIN", name="agent.run")
    def predict(self, context, model_input):
        # Accept dict, DataFrame, or list
        if isinstance(model_input, pd.DataFrame):
            inputs = [json.loads(r) if isinstance(r, str) else r for r in model_input["rfq"].tolist()]
        elif isinstance(model_input, dict):
            inputs = [model_input]
        else:
            inputs = list(model_input)

        traces = [self._run_one(rfq) for rfq in inputs]
        return pd.DataFrame({"trace": [json.dumps(t) for t in traces]})

    def _run_one(self, rfq: dict) -> dict:
        steps = []
        def step(name, fn):
            t0 = time.time()
            try:
                out = fn()
                status = "done"
            except Exception as e:
                out = {"error": str(e)[:500]}
                status = "failed"
            ms = int((time.time() - t0) * 1000)
            steps.append({"name": name, "status": status, "output": out, "duration_ms": ms})
            return out

        cap = step("capacity_check", lambda: self._capacity_check(rfq))
        yc = step("yield_calc", lambda: self._yield_calc(rfq, cap))
        comp = step("competitive_check", lambda: self._competitive_check(rfq, yc))
        rules = step("rules_retrieval", lambda: self._rules_retrieval(rfq))
        draft = step("quote_drafting", lambda: self._quote_drafting(rfq, cap, yc, comp, rules))

        return {
            "rfq_id": rfq.get("rfq_id"),
            "steps": steps,
            "final_quote": {
                "rate_gbp_per_kg": yc.get("recommended_rate_gbp_per_kg"),
                "weight_kg": float(rfq.get("requested_weight_kg", 0)),
                "revenue_gbp": yc.get("expected_revenue_gbp"),
                "valid_until_hours": 24,
                "competitive_position": comp.get("our_position"),
                "load_factor_impact": f"{cap.get('current_load_factor', 0):.0%} -> {cap.get('projected_load_factor_post', 0):.0%}",
            },
            "rationale": draft.get("rationale", ""),
        }

    # ---------- Step implementations ----------

    def _sql(self, statement: str) -> list[dict]:
        api = self.w.api_client
        body = {"warehouse_id": WAREHOUSE_ID, "statement": statement, "wait_timeout": "50s", "disposition": "INLINE"}
        result = api.do("POST", "/api/2.0/sql/statements/", body=body)
        sid = result.get("statement_id")
        while result.get("status", {}).get("state") in ("PENDING", "RUNNING"):
            time.sleep(0.5)
            result = api.do("GET", f"/api/2.0/sql/statements/{sid}")
        if result.get("status", {}).get("state") != "SUCCEEDED":
            err = result.get("status", {}).get("error", {}).get("message", "?")
            raise RuntimeError(f"SQL: {err[:300]}")
        schema = result.get("manifest", {}).get("schema", {}).get("columns", [])
        cols = [c["name"] for c in schema]
        rows = result.get("result", {}).get("data_array", []) or []
        return [dict(zip(cols, r)) for r in rows]

    @mlflow.trace(span_type="TOOL", name="capacity_check")
    def _capacity_check(self, rfq):
        flight_id = rfq["flight_id"]
        rows = self._sql(f"""
          SELECT flight_id, flight_date, lane, aircraft_type,
                 cargo_capacity_kg, booked_kg, load_factor
          FROM {CATALOG}.cargo_silver.flight_utilization
          WHERE flight_id = '{flight_id}'
        """)
        if not rows:
            rows = self._sql(f"""
              SELECT flight_id, flight_date, CONCAT(origin_iata,'-',destination_iata) AS lane,
                     aircraft_type, cargo_capacity_kg,
                     0.0 AS booked_kg, 0.0 AS load_factor
              FROM {CATALOG}.cargo_bronze.flight_schedule
              WHERE flight_id = '{flight_id}'
            """)
        if not rows:
            return {"error": "Flight not found", "available_kg": 0}
        r = rows[0]
        booked = float(r.get("booked_kg") or 0)
        capacity = float(r.get("cargo_capacity_kg") or 0)
        requested = float(rfq.get("requested_weight_kg") or 0)
        available = capacity - booked
        utilization_post = (booked + requested) / capacity if capacity else 0
        return {
            "flight_id": r["flight_id"], "lane": r["lane"], "aircraft": r["aircraft_type"],
            "capacity_kg": capacity, "booked_kg": booked, "available_kg": available,
            "current_load_factor": booked / capacity if capacity else 0,
            "projected_load_factor_post": utilization_post,
            "requested_kg": requested,
            "fits": requested <= available,
            "tightness": "tight" if utilization_post > 0.85 else "balanced" if utilization_post > 0.65 else "soft",
        }

    @mlflow.trace(span_type="TOOL", name="yield_calc")
    def _yield_calc(self, rfq, capacity):
        # Call the deployed yield model endpoint for the ML-predicted base rate
        origin = rfq["origin_iata"]; dest = rfq["destination_iata"]
        commodity = rfq["commodity_code"]; fwd = rfq["forwarder_name"]
        weight = float(rfq["requested_weight_kg"])

        # Look up features that the model needs
        rows = self._sql(f"""
          SELECT account_tier, negotiation_strength, handling_tier, rate_multiplier,
                 aircraft_type, lead_time_days
          FROM (
            SELECT f.account_tier, f.negotiation_strength, c.handling_tier, c.rate_multiplier
            FROM {CATALOG}.cargo_bronze.forwarders f
            CROSS JOIN {CATALOG}.cargo_bronze.commodities c
            WHERE f.forwarder_name = '{fwd}' AND c.commodity_code = '{commodity}'
          ) fc
          CROSS JOIN (
            SELECT aircraft_type FROM {CATALOG}.cargo_bronze.flight_schedule WHERE flight_id = '{rfq["flight_id"]}' LIMIT 1
          ) ac
          CROSS JOIN (SELECT GREATEST(0, DATEDIFF(DATE('{rfq["flight_date"]}'), CURRENT_DATE())) AS lead_time_days) lt
        """)
        feat = rows[0] if rows else {}

        lane = f"{origin}-{dest}"
        import math
        features = {
            "lane": lane,
            "commodity_code": commodity,
            "account_tier": feat.get("account_tier", "Bronze"),
            "handling_tier": feat.get("handling_tier", "Standard"),
            "aircraft_type": feat.get("aircraft_type", "A330-300"),
            "log_weight": math.log1p(weight),
            "lead_time_days": int(feat.get("lead_time_days") or 5),
            "rate_multiplier": float(feat.get("rate_multiplier") or 1.0),
            "negotiation_strength": float(feat.get("negotiation_strength") or 0.6),
            "flight_load_factor": float(capacity.get("current_load_factor") or 0.7),
        }

        try:
            api = self.w.api_client
            ml_pred = api.do(
                "POST",
                f"/serving-endpoints/{YIELD_MODEL_ENDPOINT}/invocations",
                body={"dataframe_records": [features]},
            )
            ml_rate = ml_pred.get("predictions", [None])[0]
        except Exception as e:
            ml_rate = None

        # Fallback: historical mean if model endpoint unavailable
        hist = self._sql(f"""
          SELECT AVG(rate_gbp_per_kg) AS avg_rate, STDDEV(rate_gbp_per_kg) AS rate_std,
                 COUNT(*) AS sample_size,
                 AVG(CASE WHEN forwarder_name = '{fwd}' THEN rate_gbp_per_kg END) AS forwarder_avg
          FROM {CATALOG}.cargo_silver.awb_enriched
          WHERE origin_iata = '{origin}' AND destination_iata = '{dest}'
            AND commodity_code = '{commodity}'
            AND flight_date >= DATE_ADD(CURRENT_DATE(), -90)
        """)
        h = hist[0] if hist else {}
        hist_rate = float(h.get("avg_rate") or 2.50)
        std = float(h.get("rate_std") or 0.20)

        base_rate = float(ml_rate) if ml_rate is not None else hist_rate

        tightness = capacity.get("tightness", "balanced")
        cap_premium = 12 if tightness == "tight" else (-5 if tightness == "soft" else 3)
        recommended = base_rate * (1 + cap_premium / 100)
        recommended = max(base_rate - 1.5 * std, min(recommended, base_rate + 2 * std))

        return {
            "ml_predicted_rate": round(float(ml_rate), 3) if ml_rate is not None else None,
            "ml_source": YIELD_MODEL_ENDPOINT if ml_rate is not None else "fallback_historical",
            "historical_base_rate": round(hist_rate, 3),
            "rate_std": round(std, 3),
            "forwarder_historical_rate": round(float(h.get("forwarder_avg")), 3) if h.get("forwarder_avg") else None,
            "capacity_premium_pct": cap_premium,
            "recommended_rate_gbp_per_kg": round(recommended, 3),
            "expected_revenue_gbp": round(recommended * weight, 2),
            "sample_size": int(h.get("sample_size") or 0),
        }

    @mlflow.trace(span_type="TOOL", name="competitive_check")
    def _competitive_check(self, rfq, yc):
        rows = self._sql(f"""
          SELECT competitor, competitor_rate AS rate, gap_pct
          FROM {CATALOG}.cargo_gold.competitor_benchmark
          WHERE origin_iata = '{rfq["origin_iata"]}'
            AND destination_iata = '{rfq["destination_iata"]}'
            AND commodity_code = '{rfq["commodity_code"]}'
          ORDER BY competitor_rate DESC
        """)
        if not rows:
            return {"competitors_observed": 0, "competitor_rates": [], "our_position": "unknown"}
        rates = [float(r["rate"]) for r in rows]
        rec = yc.get("recommended_rate_gbp_per_kg", 0)
        median = sorted(rates)[len(rates)//2]
        if rec < min(rates): pos = "below_market"
        elif rec > max(rates): pos = "above_market"
        elif rec < median: pos = "competitive"
        else: pos = "premium"
        return {
            "competitors_observed": len(rows),
            "median_competitor_rate": round(median, 3),
            "min_competitor_rate": round(min(rates), 3),
            "max_competitor_rate": round(max(rates), 3),
            "our_position": pos,
            "competitor_rates": [{"competitor": r["competitor"], "rate": float(r["rate"])} for r in rows[:5]],
        }

    @mlflow.trace(span_type="RETRIEVER", name="rules_retrieval")
    def _rules_retrieval(self, rfq):
        from databricks.vector_search.client import VectorSearchClient
        try:
            vsc = VectorSearchClient(disable_notice=True)
            idx = vsc.get_index(endpoint_name=VS_ENDPOINT, index_name=VS_INDEX)
            q = f"{rfq['commodity_code']} {rfq['origin_iata']}-{rfq['destination_iata']} handling rate pricing"
            results = idx.similarity_search(
                query_text=q, columns=["doc_id", "title", "content", "category"], num_results=4,
            )
            docs = []
            for row in results.get("result", {}).get("data_array", []):
                docs.append({"doc_id": row[0], "title": row[1], "content": row[2], "category": row[3]})
            return {"retrieved": len(docs), "documents": docs}
        except Exception as e:
            return {"retrieved": 0, "documents": [], "error": str(e)[:300]}

    @mlflow.trace(span_type="LLM", name="quote_drafting")
    def _quote_drafting(self, rfq, cap, yc, comp, rules):
        rules_text = "\\n\\n".join([f"[{d['title']}]\\n{d['content'][:400]}" for d in rules.get("documents", [])[:3]])
        system = ("You are Virgin Atlantic\\'s senior cargo yield analyst. Recommend a quote for an inbound RFQ "
                  "based on capacity, historical yield, ML model prediction, competitive position, and handling rules. "
                  "Be concise, factual, quantitative. Always conclude with: (1) a recommended rate, (2) the rationale "
                  "in 3-5 bullets referencing the data, (3) risks. Do not invent numbers. Quote only in GBP.")
        user = (
            f"## RFQ\\n"
            f"Forwarder: {rfq['forwarder_name']}\\nLane: {rfq['origin_iata']}-{rfq['destination_iata']}\\n"
            f"Flight: {rfq['flight_id']} on {rfq['flight_date']}\\nCommodity: {rfq['commodity_code']}\\n"
            f"Weight: {rfq['requested_weight_kg']} kg\\n"
            f"Special handling: {rfq.get('special_handling')}\\n"
            f"Customer notes: {rfq.get('notes') or 'None'}\\n\\n"
            f"## Capacity Analysis\\n"
            f"- Aircraft: {cap.get('aircraft')}\\n"
            f"- Capacity: {cap.get('capacity_kg', 0):,.0f} kg; Booked: {cap.get('booked_kg', 0):,.0f}; Available: {cap.get('available_kg', 0):,.0f}\\n"
            f"- LF: {cap.get('current_load_factor', 0):.0%} -> {cap.get('projected_load_factor_post', 0):.0%}\\n"
            f"- Tightness: {cap.get('tightness')}\\n"
            f"- Fits: {cap.get('fits')}\\n\\n"
            f"## Yield (ML model + history)\\n"
            f"- ML-predicted rate: £{yc.get('ml_predicted_rate')}/kg ({yc.get('ml_source')})\\n"
            f"- 90-day historical mean: £{yc.get('historical_base_rate')}/kg (std £{yc.get('rate_std')})\\n"
            f"- Forwarder historical avg: £{yc.get('forwarder_historical_rate')}/kg\\n"
            f"- Sample size: {yc.get('sample_size')} AWBs\\n"
            f"- Capacity premium applied: {yc.get('capacity_premium_pct')}%\\n"
            f"- Recommended rate: £{yc.get('recommended_rate_gbp_per_kg')}/kg\\n"
            f"- Expected revenue: £{yc.get('expected_revenue_gbp'):,.0f}\\n\\n"
            f"## Competitive\\n"
            f"- Competitors observed: {comp.get('competitors_observed')}\\n"
            f"- Median competitor rate: £{comp.get('median_competitor_rate')}/kg\\n"
            f"- Range: £{comp.get('min_competitor_rate')} - £{comp.get('max_competitor_rate')}\\n"
            f"- Our position: {comp.get('our_position')}\\n\\n"
            f"## Rules\\n{rules_text}\\n\\nDraft the recommendation."
        )
        api = self.w.api_client
        resp = api.do("POST", f"/serving-endpoints/{LLM_ENDPOINT}/invocations",
                      body={"messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}],
                            "max_tokens": 900, "temperature": 0.2})
        choice = resp.get("choices", [{}])[0]
        usage = resp.get("usage", {})
        return {
            "model": LLM_ENDPOINT,
            "rationale": choice.get("message", {}).get("content", ""),
            "tokens": {"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
        }


# Set the model so MLflow knows what to log
mlflow.models.set_model(CargoYieldAgent())
'''

# Inject build-time values into the agent source, then write to a temp file MLflow can serialize.
agent_source = (
    CARGO_AGENT_CODE
    .replace("__CATALOG__", CATALOG)
    .replace("__WAREHOUSE_ID__", WAREHOUSE_ID)
    .replace("__YIELD_MODEL_ENDPOINT__", YIELD_MODEL_ENDPOINT)
    .replace("__LLM_ENDPOINT__", LLM_ENDPOINT)
    .replace("__VS_ENDPOINT__", VS_ENDPOINT)
)
with open("/tmp/cargo_agent_model.py", "w") as f:
    f.write(agent_source)

print("Agent source written.")

# COMMAND ----------

# Enable MLflow autologging + tracing for this run
mlflow.autolog()

# Example input/output signature
example_rfq = {
    "rfq_id": "RFQ-TEST",
    "flight_id": "VS100001",
    "flight_date": "2026-05-15",
    "origin_iata": "LHR",
    "destination_iata": "JFK",
    "forwarder_name": "DSV Air & Sea",
    "commodity_code": "PHC",
    "requested_weight_kg": 1500,
    "requested_pieces": 6,
    "special_handling": True,
    "notes": "Pharma cold chain",
}
input_example = pd.DataFrame({"rfq": [json.dumps(example_rfq)]})
output_example = pd.DataFrame({"trace": [json.dumps({"rfq_id": "RFQ-TEST", "steps": [], "final_quote": {}, "rationale": ""})]})
signature = infer_signature(input_example, output_example)

# COMMAND ----------

log_kwargs = dict(
    artifact_path="agent",
    python_model="/tmp/cargo_agent_model.py",
    registered_model_name=AGENT_NAME,
    signature=signature,
    input_example=input_example,
    pip_requirements=[
        "mlflow>=2.16",
        "databricks-sdk",
        "databricks-vectorsearch",
        "pandas",
    ],
)
if HAS_RESOURCES:
    log_kwargs["resources"] = [
        DatabricksServingEndpoint(endpoint_name=YIELD_MODEL_ENDPOINT),
        DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT),
        DatabricksSQLWarehouse(warehouse_id=WAREHOUSE_ID),
        DatabricksVectorSearchIndex(index_name=VS_INDEX),
        DatabricksTable(table_name=f"{CATALOG}.cargo_silver.awb_enriched"),
        DatabricksTable(table_name=f"{CATALOG}.cargo_silver.flight_utilization"),
        DatabricksTable(table_name=f"{CATALOG}.cargo_gold.competitor_benchmark"),
        DatabricksTable(table_name=f"{CATALOG}.cargo_bronze.flight_schedule"),
        DatabricksTable(table_name=f"{CATALOG}.cargo_bronze.forwarders"),
        DatabricksTable(table_name=f"{CATALOG}.cargo_bronze.commodities"),
    ]

with mlflow.start_run(run_name="cargo_yield_agent_v1"):
    info = mlflow.pyfunc.log_model(**log_kwargs)
    print(f"Registered: {info.model_uri}")

# Lookup version
from mlflow.tracking import MlflowClient
client = MlflowClient()
versions = client.search_model_versions(f"name='{AGENT_NAME}'")
versions = sorted(versions, key=lambda v: int(v.version), reverse=True)
agent_version = versions[0].version
print(f"Latest agent version: {agent_version}")

# COMMAND ----------

# Set production alias
client.set_registered_model_alias(name=AGENT_NAME, alias="production", version=agent_version)
print(f"Alias 'production' -> version {agent_version}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Deploy to Model Serving
# MAGIC
# MAGIC Create or update the serving endpoint pointing at the new agent version.

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import (
    EndpointCoreConfigInput,
    ServedEntityInput,
)

w = WorkspaceClient()

served_entity = ServedEntityInput(
    entity_name=AGENT_NAME,
    entity_version=str(agent_version),
    workload_size="Small",
    scale_to_zero_enabled=True,
)

try:
    w.serving_endpoints.get(AGENT_ENDPOINT)
    exists = True
except Exception:
    exists = False

if exists:
    print(f"Updating endpoint {AGENT_ENDPOINT} -> version {agent_version} (waiting)...")
    w.serving_endpoints.update_config_and_wait(
        name=AGENT_ENDPOINT,
        served_entities=[served_entity],
    )
else:
    print(f"Creating endpoint {AGENT_ENDPOINT} -> version {agent_version} (waiting)...")
    w.serving_endpoints.create_and_wait(
        name=AGENT_ENDPOINT,
        config=EndpointCoreConfigInput(served_entities=[served_entity]),
    )

print(f"Endpoint {AGENT_ENDPOINT} ready.")

# COMMAND ----------

print(f"\nAGENT_VERSION={agent_version}")
print(f"AGENT_URI={info.model_uri}")
print(f"AGENT_NAME={AGENT_NAME}")
print(f"AGENT_ENDPOINT={AGENT_ENDPOINT}")
