"""
Virgin Atlantic Cargo Yield Agent — Databricks App backend

Endpoints:
  GET  /api/health
  GET  /api/rfqs                          — Open RFQ inbox
  GET  /api/rfq/{rfq_id}                  — Single RFQ detail
  POST /api/rfq/{rfq_id}/analyze          — Run the Cargo Yield Agent
  GET  /api/analytics/summary             — Top-line KPIs
  GET  /api/analytics/lanes               — Lane-level breakdown
  GET  /api/analytics/forwarders          — Forwarder performance
  POST /api/quote/submit                  — Submit an approved quote
  POST /api/genie/ask                     — Ask the cargo Genie space a question (returns answer + SQL + table)
  GET  /api/genie/conversation/{c}/msg/{m} — Poll for Genie response
"""
import os
import json
import time
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from databricks.sdk import WorkspaceClient

from .db import query, CATALOG

GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
AGENT_ENDPOINT = os.environ["AGENT_ENDPOINT"]

app = FastAPI(title="Virgin Atlantic Cargo Yield Agent")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "cargo-yield-agent"}


@app.get("/api/rfqs")
def list_rfqs(status: str = "pending_quote", limit: int = 50):
    rows = query(f"""
      SELECT r.rfq_id, r.received_at, r.flight_id, r.flight_date,
             r.origin_iata, r.destination_iata,
             CONCAT(r.origin_iata,'-',r.destination_iata) AS lane,
             r.forwarder_name, r.commodity_code, c.commodity_name,
             c.handling_tier,
             r.requested_weight_kg, r.requested_pieces,
             r.ready_date, r.special_handling, r.temp_controlled,
             r.status, r.notes
      FROM {CATALOG}.cargo_bronze.rfq_inbox r
      LEFT JOIN {CATALOG}.cargo_bronze.commodities c ON r.commodity_code = c.commodity_code
      WHERE r.status = '{status}'
      ORDER BY r.received_at DESC
      LIMIT {limit}
    """)
    return {"count": len(rows), "rfqs": rows}


@app.get("/api/rfq/{rfq_id}")
def get_rfq(rfq_id: str):
    rows = query(f"""
      SELECT r.*, c.commodity_name, c.handling_tier
      FROM {CATALOG}.cargo_bronze.rfq_inbox r
      LEFT JOIN {CATALOG}.cargo_bronze.commodities c ON r.commodity_code = c.commodity_code
      WHERE r.rfq_id = '{rfq_id}'
    """)
    if not rows:
        raise HTTPException(404, f"RFQ {rfq_id} not found")
    return rows[0]


@app.post("/api/rfq/{rfq_id}/analyze")
def analyze_rfq(rfq_id: str):
    """Invoke the deployed cargo-yield-agent serving endpoint."""
    rows = query(f"""
      SELECT * FROM {CATALOG}.cargo_bronze.rfq_inbox WHERE rfq_id = '{rfq_id}'
    """)
    if not rows:
        raise HTTPException(404, f"RFQ {rfq_id} not found")
    rfq = rows[0]

    # Invoke the agent serving endpoint
    w = WorkspaceClient()
    api = w.api_client
    resp = api.do(
        "POST",
        f"/serving-endpoints/{AGENT_ENDPOINT}/invocations",
        body={"dataframe_records": [{"rfq": json.dumps(rfq)}]},
    )
    # Response shape: { "predictions": [{"trace": "<json-string>"} | "<json-string>"] }
    preds = resp.get("predictions") or []
    if not preds:
        raise HTTPException(502, f"Agent endpoint returned no prediction: {resp}")
    first = preds[0]
    if isinstance(first, dict):
        trace_json = first.get("trace") or first.get("0") or next(iter(first.values()))
    else:
        trace_json = first
    return json.loads(trace_json) if isinstance(trace_json, str) else trace_json


@app.get("/api/analytics/summary")
def analytics_summary():
    """Top-line KPIs."""
    rows = query(f"""
      WITH last_30 AS (
        SELECT SUM(revenue_gbp) AS rev, SUM(chargeable_weight_kg) AS kg,
               COUNT(*) AS awbs, AVG(rate_gbp_per_kg) AS avg_yield
        FROM {CATALOG}.cargo_silver.awb_enriched
        WHERE flight_date >= DATE_ADD(CURRENT_DATE(), -30)
      ),
      prior_30 AS (
        SELECT SUM(revenue_gbp) AS rev, SUM(chargeable_weight_kg) AS kg
        FROM {CATALOG}.cargo_silver.awb_enriched
        WHERE flight_date >= DATE_ADD(CURRENT_DATE(), -60)
          AND flight_date <  DATE_ADD(CURRENT_DATE(), -30)
      ),
      lf AS (
        SELECT AVG(load_factor) AS avg_lf
        FROM {CATALOG}.cargo_silver.flight_utilization
        WHERE flight_date >= DATE_ADD(CURRENT_DATE(), -30)
      )
      SELECT
        l.rev   AS rev_30d,
        l.kg    AS kg_30d,
        l.awbs  AS awbs_30d,
        l.avg_yield AS avg_yield_30d,
        p.rev   AS rev_prior_30d,
        (l.rev - p.rev) / NULLIF(p.rev,0) AS rev_growth_pct,
        lf.avg_lf AS avg_load_factor
      FROM last_30 l CROSS JOIN prior_30 p CROSS JOIN lf
    """)
    return rows[0] if rows else {}


@app.get("/api/analytics/lanes")
def analytics_lanes():
    rows = query(f"""
      SELECT lane, origin_iata, destination_iata,
             SUM(tonnage_tonnes) AS tonnage_tonnes,
             SUM(revenue_gbp)    AS revenue_gbp,
             AVG(avg_yield_gbp_per_kg) AS avg_yield
      FROM {CATALOG}.cargo_gold.lane_monthly_summary
      WHERE month >= DATE_ADD(CURRENT_DATE(), -180)
      GROUP BY ALL
      ORDER BY revenue_gbp DESC
      LIMIT 20
    """)
    return {"lanes": rows}


@app.get("/api/analytics/forwarders")
def analytics_forwarders():
    rows = query(f"""
      SELECT forwarder_name, account_tier, awb_count, total_kg,
             total_revenue_gbp, avg_yield_gbp_per_kg,
             premium_revenue_share_pct
      FROM {CATALOG}.cargo_gold.forwarder_performance
      ORDER BY total_revenue_gbp DESC
    """)
    return {"forwarders": rows}


@app.get("/api/analytics/monthly")
def analytics_monthly():
    rows = query(f"""
      SELECT DATE_TRUNC('month', flight_date) AS month,
             SUM(revenue_gbp) AS revenue_gbp,
             SUM(chargeable_weight_kg)/1000.0 AS tonnage,
             AVG(rate_gbp_per_kg) AS avg_yield
      FROM {CATALOG}.cargo_silver.awb_enriched
      WHERE flight_date >= DATE_ADD(CURRENT_DATE(), -365)
      GROUP BY ALL
      ORDER BY month
    """)
    return {"monthly": rows}


class QuoteSubmission(BaseModel):
    rfq_id: str
    rate_gbp_per_kg: float
    weight_kg: float
    user_email: str
    notes: str = ""


@app.post("/api/quote/submit")
def submit_quote(q: QuoteSubmission):
    # In a real app this would write to Lakebase / Salesforce. For demo: log and return.
    return {
        "quote_id": f"Q-{q.rfq_id}",
        "rfq_id": q.rfq_id,
        "rate_gbp_per_kg": q.rate_gbp_per_kg,
        "revenue_gbp": round(q.rate_gbp_per_kg * q.weight_kg, 2),
        "submitted_by": q.user_email,
        "status": "fired_to_forwarder",
        "message": "Quote fired to forwarder (demo - no real submission)",
    }


# =============================================================================
# Genie integration
# =============================================================================

class GenieAsk(BaseModel):
    content: str
    conversation_id: str | None = None


@app.post("/api/genie/ask")
def genie_ask(req: GenieAsk):
    """Submit a question to the cargo Genie space. Returns conversation + message IDs to poll."""
    w = WorkspaceClient()
    api = w.api_client
    if req.conversation_id:
        # Continue an existing conversation
        result = api.do(
            "POST",
            f"/api/2.0/genie/spaces/{GENIE_SPACE_ID}/conversations/{req.conversation_id}/messages",
            body={"content": req.content},
        )
        return {
            "conversation_id": req.conversation_id,
            "message_id": result.get("message_id") or result.get("id"),
        }
    else:
        # Start a new conversation
        result = api.do(
            "POST",
            f"/api/2.0/genie/spaces/{GENIE_SPACE_ID}/start-conversation",
            body={"content": req.content},
        )
        return {
            "conversation_id": result.get("conversation_id") or result.get("conversation", {}).get("id"),
            "message_id": result.get("message_id") or result.get("message", {}).get("id"),
        }


@app.get("/api/genie/conversation/{conversation_id}/message/{message_id}")
def genie_get_message(conversation_id: str, message_id: str):
    """Poll for a Genie message — returns content, SQL, and query result when ready."""
    w = WorkspaceClient()
    api = w.api_client

    msg = api.do(
        "GET",
        f"/api/2.0/genie/spaces/{GENIE_SPACE_ID}/conversations/{conversation_id}/messages/{message_id}",
    )

    status = msg.get("status", "PENDING")
    out = {
        "status": status,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "content": msg.get("content"),
        "attachments": [],
        "error": None,
    }

    if status == "FAILED":
        out["error"] = msg.get("error", {}).get("error", "Genie failed to answer")
        return out

    if status != "COMPLETED":
        return out

    # Pull attachments (text answer + SQL + query result)
    for att in msg.get("attachments", []) or []:
        a_out: dict = {"attachment_id": att.get("attachment_id")}
        if att.get("text"):
            a_out["type"] = "text"
            a_out["content"] = att["text"].get("content")
        elif att.get("query"):
            q = att["query"]
            a_out["type"] = "query"
            a_out["description"] = q.get("description")
            a_out["title"] = q.get("title")
            a_out["sql"] = q.get("query")
            a_out["statement_id"] = q.get("statement_id")
            # Try fetching the query result inline
            try:
                qr = api.do(
                    "GET",
                    f"/api/2.0/genie/spaces/{GENIE_SPACE_ID}/conversations/{conversation_id}/messages/{message_id}/query-result/{att.get('attachment_id')}",
                )
                statement = qr.get("statement_response", {}) or qr.get("statement", {})
                result = statement.get("result", {})
                manifest = statement.get("manifest", {})
                cols = [c["name"] for c in (manifest.get("schema", {}) or {}).get("columns", [])]
                rows = result.get("data_array", []) or []
                a_out["columns"] = cols
                a_out["rows"] = rows[:200]
                a_out["row_count"] = len(rows)
            except Exception as e:
                a_out["query_error"] = str(e)[:300]
        out["attachments"].append(a_out)

    return out


@app.get("/api/genie/sample-questions")
def genie_sample_questions():
    return {
        "questions": [
            "What was our cargo revenue by month in the last 12 months?",
            "Top 5 forwarders by revenue and their premium cargo share",
            "Where are we positioned vs competitors on LHR-JFK pharma?",
            "Which lanes have soft load factor and high yield potential?",
            "Show our monthly yield trend by handling tier",
            "Which forwarders had the largest premium revenue growth this quarter?",
        ]
    }


# Serve the static frontend
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(STATIC_DIR):
    @app.get("/")
    def root():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
