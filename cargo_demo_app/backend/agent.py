"""
Cargo Yield Agent — multi-step agent for dynamic AWB pricing.

Pipeline:
  1. CapacityCheck       — Query flight capacity + committed load (Delta gold)
  2. YieldCalc           — Historical yield model + capacity-driven adjustment
  3. CompetitiveCheck    — Compare against scraped competitor rates
  4. RulesRetrieval      — Vector Search over IATA + VAA handling rules
  5. QuoteDrafting       — FMAPI (Claude Sonnet 4.6) drafts quote + rationale

Each step adds to a structured `AgentTrace` so the UI can show explainability.
"""
import os
import json
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import Any

from . import db

LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "databricks-claude-sonnet-4-6")
VS_INDEX = os.environ.get("VS_INDEX", "serverless_nal_catalog.cargo_ai.knowledge_base_index")
CATALOG = os.environ.get("CATALOG", "serverless_nal_catalog")


@dataclass
class AgentStep:
    name: str
    status: str = "pending"        # pending | running | done | failed
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


@dataclass
class AgentTrace:
    rfq_id: str
    steps: list[AgentStep] = field(default_factory=list)
    final_quote: dict[str, Any] | None = None
    rationale: str = ""


def _llm_chat(messages: list[dict], max_tokens: int = 900, temperature: float = 0.2) -> dict:
    """Call a Databricks serving endpoint via the SDK (auto-handles OAuth M2M in Apps)."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    api = w.api_client
    return api.do(
        "POST",
        f"/serving-endpoints/{LLM_ENDPOINT}/invocations",
        body={
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
    )


def step_capacity_check(rfq: dict) -> dict:
    """Pull flight capacity, committed load, current load factor."""
    flight_id = rfq["flight_id"]
    rows = db.query(f"""
      SELECT
        flight_id, flight_date, lane, aircraft_type,
        cargo_capacity_kg, booked_kg, load_factor, total_revenue_gbp, avg_yield_gbp_per_kg
      FROM {CATALOG}.cargo_silver.flight_utilization
      WHERE flight_id = '{flight_id}'
    """)
    if not rows:
        # Future flight - get capacity from schedule
        rows = db.query(f"""
          SELECT flight_id, flight_date, CONCAT(origin_iata,'-',destination_iata) AS lane,
                 aircraft_type, cargo_capacity_kg,
                 0.0 AS booked_kg, 0.0 AS load_factor, 0.0 AS total_revenue_gbp,
                 0.0 AS avg_yield_gbp_per_kg
          FROM {CATALOG}.cargo_bronze.flight_schedule
          WHERE flight_id = '{flight_id}'
        """)
    if not rows:
        return {"error": "Flight not found", "available_kg": 0}
    row = rows[0]
    booked = float(row.get("booked_kg") or 0)
    capacity = float(row.get("cargo_capacity_kg") or 0)
    requested = float(rfq.get("requested_weight_kg") or 0)
    available = capacity - booked
    fits = requested <= available
    utilization_post = (booked + requested) / capacity if capacity else 0
    return {
        "flight_id": row["flight_id"],
        "lane": row["lane"],
        "aircraft": row["aircraft_type"],
        "capacity_kg": capacity,
        "booked_kg": booked,
        "available_kg": available,
        "current_load_factor": booked / capacity if capacity else 0,
        "projected_load_factor_post": utilization_post,
        "requested_kg": requested,
        "fits": fits,
        "tightness": "tight" if utilization_post > 0.85 else "balanced" if utilization_post > 0.65 else "soft",
    }


def step_yield_calc(rfq: dict, capacity: dict) -> dict:
    """Compute expected yield based on historical lane/commodity rates + capacity premium."""
    origin = rfq["origin_iata"]
    dest = rfq["destination_iata"]
    commodity = rfq["commodity_code"]
    weight = float(rfq["requested_weight_kg"])

    # Pull historical avg yield for this lane+commodity (last 90 days)
    rows = db.query(f"""
      SELECT
        AVG(rate_gbp_per_kg) AS avg_rate,
        STDDEV(rate_gbp_per_kg) AS rate_std,
        AVG(rate_multiplier) AS comm_mult,
        COUNT(*) AS sample_size,
        AVG(CASE WHEN forwarder_name = '{rfq["forwarder_name"]}' THEN rate_gbp_per_kg END) AS forwarder_avg_rate
      FROM {CATALOG}.cargo_silver.awb_enriched
      WHERE origin_iata = '{origin}'
        AND destination_iata = '{dest}'
        AND commodity_code = '{commodity}'
        AND flight_date >= DATE_ADD(CURRENT_DATE(), -90)
    """)
    if not rows or rows[0]["avg_rate"] is None:
        # Fallback: any commodity on this lane
        rows = db.query(f"""
          SELECT AVG(rate_gbp_per_kg) AS avg_rate, STDDEV(rate_gbp_per_kg) AS rate_std,
                 1.0 AS comm_mult, COUNT(*) AS sample_size, NULL AS forwarder_avg_rate
          FROM {CATALOG}.cargo_silver.awb_enriched
          WHERE origin_iata = '{origin}' AND destination_iata = '{dest}'
            AND flight_date >= DATE_ADD(CURRENT_DATE(), -90)
        """)
    r = rows[0]
    base_rate = float(r.get("avg_rate") or 2.50)
    std = float(r.get("rate_std") or 0.20)
    forwarder_avg = r.get("forwarder_avg_rate")

    # Capacity-driven adjustment
    tightness = capacity.get("tightness", "balanced")
    if tightness == "tight":
        capacity_premium_pct = 12
    elif tightness == "soft":
        capacity_premium_pct = -5
    else:
        capacity_premium_pct = 3

    recommended_rate = base_rate * (1 + capacity_premium_pct / 100)
    # Floor/ceiling based on observed range
    recommended_rate = max(base_rate - 1.5 * std, min(recommended_rate, base_rate + 2 * std))

    return {
        "base_rate_gbp_per_kg": round(base_rate, 3),
        "rate_std": round(std, 3),
        "forwarder_historical_rate": round(forwarder_avg, 3) if forwarder_avg else None,
        "capacity_premium_pct": capacity_premium_pct,
        "recommended_rate_gbp_per_kg": round(recommended_rate, 3),
        "expected_revenue_gbp": round(recommended_rate * weight, 2),
        "sample_size": int(r.get("sample_size") or 0),
    }


def step_competitive_check(rfq: dict, yield_calc: dict) -> dict:
    origin = rfq["origin_iata"]
    dest = rfq["destination_iata"]
    commodity = rfq["commodity_code"]
    rows = db.query(f"""
      SELECT competitor, competitor_rate AS rate_gbp_per_kg, gap_pct
      FROM {CATALOG}.cargo_gold.competitor_benchmark
      WHERE origin_iata = '{origin}' AND destination_iata = '{dest}'
        AND commodity_code = '{commodity}'
      ORDER BY competitor_rate DESC
    """)
    if not rows:
        return {"competitors_observed": 0, "competitor_rates": [], "position": "unknown"}
    rec = yield_calc["recommended_rate_gbp_per_kg"]
    comp_rates = [float(r["rate_gbp_per_kg"]) for r in rows]
    median_comp = sorted(comp_rates)[len(comp_rates)//2]
    if rec < min(comp_rates):
        position = "below_market"
    elif rec > max(comp_rates):
        position = "above_market"
    elif rec < median_comp:
        position = "competitive"
    else:
        position = "premium"
    return {
        "competitors_observed": len(rows),
        "median_competitor_rate": round(median_comp, 3),
        "min_competitor_rate": round(min(comp_rates), 3),
        "max_competitor_rate": round(max(comp_rates), 3),
        "our_position": position,
        "competitor_rates": [{"competitor": r["competitor"], "rate": float(r["rate_gbp_per_kg"])} for r in rows[:5]],
    }


def step_rules_retrieval(rfq: dict) -> dict:
    """Retrieve handling rules from Vector Search."""
    try:
        from databricks.vector_search.client import VectorSearchClient
        vsc = VectorSearchClient(disable_notice=True)
        idx = vsc.get_index(endpoint_name=os.environ.get("VS_ENDPOINT", "nalvs"),
                            index_name=VS_INDEX)
        query_text = f"{rfq['commodity_code']} {rfq['origin_iata']}-{rfq['destination_iata']} handling rate pricing"
        results = idx.similarity_search(
            query_text=query_text,
            columns=["doc_id", "title", "content", "category"],
            num_results=4
        )
        docs = []
        for row in results.get("result", {}).get("data_array", []):
            docs.append({"doc_id": row[0], "title": row[1], "content": row[2], "category": row[3]})
        return {"retrieved": len(docs), "documents": docs}
    except Exception as e:
        # Fallback: simple keyword search
        rows = db.query(f"""
          SELECT doc_id, title, content, category
          FROM {CATALOG}.cargo_ai.knowledge_base
          WHERE category IN ('special_cargo', 'pricing_strategy', 'lanes')
          LIMIT 4
        """)
        return {"retrieved": len(rows), "documents": rows, "fallback": str(e)[:200]}


def step_quote_drafting(rfq: dict, capacity: dict, yield_calc: dict, competitive: dict, rules: dict) -> dict:
    """Use FMAPI to draft the quote rationale."""
    rules_text = "\n\n".join([f"[{d['title']}]\n{d['content'][:400]}" for d in rules.get("documents", [])[:3]])
    system = """You are Virgin Atlantic's senior cargo yield analyst. Your job is to recommend a quote for an inbound RFQ based on capacity, historical yield, competitive position, and handling rules. Be concise, factual, and quantitative. Always conclude with: (1) a recommended rate, (2) the rationale in 3-5 bullets referencing the data, (3) risks. Do not invent numbers. Quote only in GBP."""

    user = f"""## RFQ
Forwarder: {rfq['forwarder_name']}
Lane: {rfq['origin_iata']}-{rfq['destination_iata']}
Flight: {rfq['flight_id']} on {rfq['flight_date']}
Commodity: {rfq['commodity_code']}
Weight: {rfq['requested_weight_kg']} kg
Special handling: {rfq.get('special_handling')}, Temp controlled: {rfq.get('temp_controlled')}
Customer notes: {rfq.get('notes') or 'None'}

## Capacity Analysis
- Aircraft: {capacity.get('aircraft')}
- Capacity: {capacity.get('capacity_kg'):,.0f} kg
- Already booked: {capacity.get('booked_kg'):,.0f} kg ({capacity.get('current_load_factor'):.0%})
- Available: {capacity.get('available_kg'):,.0f} kg
- Projected load factor if accepted: {capacity.get('projected_load_factor_post'):.0%}
- Tightness: {capacity.get('tightness')}
- Fits in available capacity: {capacity.get('fits')}

## Historical Yield (last 90 days, same lane+commodity)
- Base rate: £{yield_calc.get('base_rate_gbp_per_kg')}/kg (std £{yield_calc.get('rate_std')})
- Forwarder historical avg: {f"£{yield_calc.get('forwarder_historical_rate')}/kg" if yield_calc.get('forwarder_historical_rate') else 'no prior history'}
- Sample size: {yield_calc.get('sample_size')} AWBs
- Capacity premium applied: {yield_calc.get('capacity_premium_pct')}%
- Model-recommended rate: £{yield_calc.get('recommended_rate_gbp_per_kg')}/kg
- Expected revenue: £{yield_calc.get('expected_revenue_gbp'):,.0f}

## Competitive Benchmark
- Competitors observed: {competitive.get('competitors_observed')}
- Median competitor rate: £{competitive.get('median_competitor_rate')}/kg
- Range: £{competitive.get('min_competitor_rate')} - £{competitive.get('max_competitor_rate')}
- Our position at recommended rate: {competitive.get('our_position')}

## Relevant Rules and Context
{rules_text}

Now draft the quote recommendation."""

    resp = _llm_chat([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], max_tokens=900, temperature=0.2)
    choice = resp.get("choices", [{}])[0]
    usage = resp.get("usage", {})
    return {
        "model": LLM_ENDPOINT,
        "rationale": choice.get("message", {}).get("content", ""),
        "tokens": {"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
    }


def run_agent(rfq: dict) -> dict:
    """Execute the full cargo yield agent pipeline."""
    import time
    trace = AgentTrace(rfq_id=rfq["rfq_id"])

    def time_step(name, fn):
        t0 = time.time()
        step = AgentStep(name=name, status="running")
        trace.steps.append(step)
        try:
            result = fn()
            step.output = result
            step.status = "done"
        except Exception as e:
            step.status = "failed"
            step.output = {"error": str(e)[:500]}
        step.duration_ms = int((time.time() - t0) * 1000)
        return step.output

    cap = time_step("capacity_check", lambda: step_capacity_check(rfq))
    yc = time_step("yield_calc", lambda: step_yield_calc(rfq, cap))
    comp = time_step("competitive_check", lambda: step_competitive_check(rfq, yc))
    rules = time_step("rules_retrieval", lambda: step_rules_retrieval(rfq))
    draft = time_step("quote_drafting", lambda: step_quote_drafting(rfq, cap, yc, comp, rules))

    trace.final_quote = {
        "rate_gbp_per_kg": yc.get("recommended_rate_gbp_per_kg"),
        "weight_kg": float(rfq["requested_weight_kg"]),
        "revenue_gbp": yc.get("expected_revenue_gbp"),
        "valid_until_hours": 24,
        "competitive_position": comp.get("our_position"),
        "load_factor_impact": f"{cap.get('current_load_factor', 0):.0%} -> {cap.get('projected_load_factor_post', 0):.0%}",
    }
    trace.rationale = draft.get("rationale", "")
    return asdict(trace)
