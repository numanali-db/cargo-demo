# Northwind Air Cargo — Cargo Yield Agent
### Turning every RFQ into a capacity-aware, margin-optimal quote in seconds

*Business review deck. Audience: VP Cargo Revenue (executive sponsor) + Head of Yield / Cargo Data Lead (domain owner). Figures are synthetic, calibrated to public air-cargo benchmarks.*

---

## 1. The business problem

Northwind Air Cargo runs a **~£250M cargo book** across ~26,000 long-haul flights a year. Revenue is made or lost one **RFQ (request-for-quote)** at a time:

- A freight forwarder fires the same lane RFQ to every airline and books the first good rate back.
- A **human yield analyst** answers each one by hand — pulling flight load factors from one system, historical rates from another, competitor intel from a third, and IATA handling rules from a PDF — then makes a judgement call.
- **AOG and pharma RFQs need a confirmed rate in under 30 minutes.** On a Monday-morning inbox of 80 RFQs, that's impossible.

**The result:** slow responses lose bookings, and manual quoting drifts — two analysts price the same LHR–JFK pharma booking differently, leaving margin on the table.

---

## 2. What it costs today (the buyer's KPIs)

| KPI | Status quo | Why it hurts |
|---|---|---|
| **RFQ turnaround** | 15–40 min/RFQ, manual | AOG/pharma bookings lost to faster carriers |
| **Yield consistency** | ±10–15% analyst variance on identical lanes | Margin leakage on every quote |
| **Capacity utilisation** | Priced "by gut" | Belly flown empty *or* premium given away |
| **Analyst capacity** | ~80 RFQs/day ceiling | Can't scale with demand peaks |

A **1–2% yield lift** on a £250M book is **£2.5–5M of pure margin per year** — no new aircraft, no capex.

---

## 3. The solution — Cargo Yield Agent

An AI agent that answers every inbound RFQ in **~1.4 seconds** with a capacity-aware, auditable quote, and routes only the strategic/unusual ones to a human.

For each RFQ it runs a **5-step reasoning pipeline**:
1. **Capacity check** — how full is the target flight; how does this booking move the load factor?
2. **Yield calc** — an ML model predicts the base rate (lane × commodity × forwarder × season), bounded by 90-day actuals.
3. **Competitive check** — compares the recommended rate to scraped competitor pricing on the same lane/commodity.
4. **Rules retrieval** — pulls the relevant IATA/airline handling rules (pharma cool-chain, AOG SLAs, DG limits) from a vector-indexed knowledge base.
5. **Quote drafting** — an LLM synthesises a quote with a **quantitative rationale the analyst signs off or overrides**.

Every run emits an MLflow trace: *why £3.23/kg and not £3.10* — capacity, history, competitors, rules.

---

## 4. How it's built — one integrated Databricks journey

```
Raw ops data ─▶ Lakeflow ─▶ Unity Catalog ─▶  ┌─ ML yield model ─┐
(synthetic)     (ingest+DQ)  (govern)          ├─ GenAI agent ────┤─▶ Databricks App
                                    │           └─ Genie (NL Q&A) ─┘   (analyst cockpit)
                                    └─▶ Lakebase (Postgres) ── live RFQ inbox + quotes (OLTP)
```

- **Lakeflow** ingests raw AWBs / flights / forwarders / competitor rates with data-quality expectations.
- **Unity Catalog** governs bronze→silver→gold and the ML/agent artifacts.
- **Lakebase** serves the operational RFQ inbox and captures submitted quotes (real-time OLTP).
- **ML + GenAI** make it intelligent; **Genie** makes the book queryable in plain English; the **App** is the analyst's cockpit.

One platform, one governance model, one copy of the data — not six stitched-together tools.

---

## 5. Business outcomes

| Outcome | Before | With the agent |
|---|---|---|
| RFQ response time | 15–40 min | **~1.4 sec** |
| Quotes an analyst can clear | ~80/day | **Agent clears the inbox; humans handle exceptions** |
| Yield consistency | ±10–15% | **Deterministic, auditable, every time** |
| Capacity discipline | by gut | **Auto-premium on tight flights, tactical discount on soft ones** |

**Headline value: £2.5–5M/yr incremental margin** from a 1–2% yield lift, plus faster RFQ win-rate on time-critical (AOG/pharma) cargo.

*(Live demo snapshot: £21.8M cargo revenue in the last 30 days of data, 77.4% avg load factor, £3.645/kg blended yield — the agent recommends against these in real time.)*

---

## 6. Why it matters to each buyer

- **VP Cargo Revenue (sponsor):** protects and grows margin on the existing book; faster quoting wins time-critical bookings; the business case is a hard number, not "efficiency."
- **Head of Yield / Data Lead (owner):** every quote is explainable and overridable; the model and rules are versioned and governed in Unity Catalog; no new data silo to maintain.

---

## 7. Decisions & trade-offs

- **Agent, not a single model.** A 5-step tool-using agent (capacity→yield→competition→rules→draft) beats one black-box price model because analysts must *see the reasoning* to trust and override it. Trade-off: more moving parts, mitigated by MLflow tracing.
- **Lakebase for the RFQ inbox/quotes; lakehouse for analytics.** OLTP reads/writes (open RFQs, quote capture) belong in Postgres for low-latency serving; heavy aggregation stays on the SQL warehouse. Trade-off: two engines, but each used for what it's best at.
- **ML base rate bounded by actuals.** The model can generalise, but a hard 90-day actuals bound stops it drifting to unrealistic rates on thin lanes. Trade-off: slightly less aggressive on sparse lanes, far safer.
- **Genie for exploration, agent for the transaction.** Analysts explore the book in natural language (Genie); the agent handles the repeatable pricing decision. Right tool per job.

---

## 8. Next steps

1. Connect a live RFQ feed (email/EDI) into the Lakebase inbox.
2. A/B the agent's recommended rate vs. analyst quotes on a single desk for one quarter; measure realised yield lift.
3. Extend the knowledge base to the full IATA TACT ruleset and add a feedback loop from won/lost bookings into the yield model.
