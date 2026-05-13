-- =============================================================================
-- SILVER LAYER - Cleaned and enriched
-- =============================================================================

CREATE OR REPLACE TABLE serverless_nal_catalog.cargo_silver.awb_enriched AS
SELECT
  a.awb_number,
  a.flight_id,
  a.flight_date,
  a.booking_date,
  DATEDIFF(a.flight_date, a.booking_date) AS lead_time_days,
  a.origin_iata,
  a.destination_iata,
  CONCAT(a.origin_iata, '-', a.destination_iata) AS lane,
  a.forwarder_name,
  f.account_tier,
  f.negotiation_strength,
  a.commodity_code,
  c.commodity_name,
  c.handling_tier,
  c.rate_multiplier,
  c.temp_controlled,
  a.chargeable_weight_kg,
  a.volume_m3,
  a.pieces,
  a.rate_gbp_per_kg,
  a.revenue_gbp,
  a.special_handling,
  a.status,
  s.cargo_capacity_kg AS flight_capacity_kg,
  s.aircraft_type,
  YEAR(a.flight_date) AS flight_year,
  MONTH(a.flight_date) AS flight_month,
  DATE_TRUNC('month', a.flight_date) AS flight_month_start,
  DATE_TRUNC('week', a.flight_date) AS flight_week_start
FROM serverless_nal_catalog.cargo_bronze.awb_raw a
LEFT JOIN serverless_nal_catalog.cargo_bronze.forwarders f ON a.forwarder_name = f.forwarder_name
LEFT JOIN serverless_nal_catalog.cargo_bronze.commodities c ON a.commodity_code = c.commodity_code
LEFT JOIN serverless_nal_catalog.cargo_bronze.flight_schedule s ON a.flight_id = s.flight_id;

CREATE OR REPLACE TABLE serverless_nal_catalog.cargo_silver.flight_utilization AS
SELECT
  s.flight_id,
  s.flight_date,
  s.origin_iata,
  s.destination_iata,
  CONCAT(s.origin_iata, '-', s.destination_iata) AS lane,
  s.aircraft_type,
  s.cargo_capacity_kg,
  COALESCE(SUM(a.chargeable_weight_kg), 0) AS booked_kg,
  COALESCE(SUM(a.chargeable_weight_kg) / NULLIF(s.cargo_capacity_kg, 0), 0) AS load_factor,
  COALESCE(SUM(a.revenue_gbp), 0) AS total_revenue_gbp,
  COALESCE(SUM(a.revenue_gbp) / NULLIF(SUM(a.chargeable_weight_kg), 0), 0) AS avg_yield_gbp_per_kg,
  COUNT(a.awb_number) AS awb_count
FROM serverless_nal_catalog.cargo_bronze.flight_schedule s
LEFT JOIN serverless_nal_catalog.cargo_bronze.awb_raw a ON s.flight_id = a.flight_id
WHERE s.status = 'operated'
GROUP BY ALL;

-- =============================================================================
-- GOLD LAYER - Business-ready analytics
-- =============================================================================

-- Monthly revenue by lane
CREATE OR REPLACE TABLE serverless_nal_catalog.cargo_gold.lane_monthly_summary AS
SELECT
  flight_month_start AS month,
  lane,
  origin_iata,
  destination_iata,
  SUM(chargeable_weight_kg) AS tonnage_kg,
  ROUND(SUM(chargeable_weight_kg)/1000.0, 1) AS tonnage_tonnes,
  SUM(revenue_gbp) AS revenue_gbp,
  ROUND(AVG(rate_gbp_per_kg), 3) AS avg_yield_gbp_per_kg,
  COUNT(DISTINCT awb_number) AS awb_count,
  COUNT(DISTINCT forwarder_name) AS forwarder_count
FROM serverless_nal_catalog.cargo_silver.awb_enriched
GROUP BY ALL
ORDER BY month, revenue_gbp DESC;

-- Forwarder performance
CREATE OR REPLACE TABLE serverless_nal_catalog.cargo_gold.forwarder_performance AS
SELECT
  forwarder_name,
  account_tier,
  COUNT(DISTINCT awb_number) AS awb_count,
  SUM(chargeable_weight_kg) AS total_kg,
  SUM(revenue_gbp) AS total_revenue_gbp,
  ROUND(AVG(rate_gbp_per_kg), 3) AS avg_yield_gbp_per_kg,
  COUNT(DISTINCT lane) AS lanes_used,
  COUNT(DISTINCT commodity_code) AS commodities_shipped,
  ROUND(SUM(CASE WHEN handling_tier IN ('Premium', 'Specialist') THEN revenue_gbp ELSE 0 END) / SUM(revenue_gbp) * 100, 1) AS premium_revenue_share_pct
FROM serverless_nal_catalog.cargo_silver.awb_enriched
GROUP BY ALL
ORDER BY total_revenue_gbp DESC;

-- Commodity mix
CREATE OR REPLACE TABLE serverless_nal_catalog.cargo_gold.commodity_mix AS
SELECT
  commodity_code,
  commodity_name,
  handling_tier,
  SUM(chargeable_weight_kg) AS total_kg,
  SUM(revenue_gbp) AS revenue_gbp,
  ROUND(AVG(rate_gbp_per_kg), 3) AS avg_yield_gbp_per_kg,
  COUNT(DISTINCT awb_number) AS awb_count
FROM serverless_nal_catalog.cargo_silver.awb_enriched
GROUP BY ALL
ORDER BY revenue_gbp DESC;

-- Load factor trends (key metric)
CREATE OR REPLACE TABLE serverless_nal_catalog.cargo_gold.load_factor_trends AS
SELECT
  DATE_TRUNC('month', flight_date) AS month,
  lane,
  COUNT(*) AS flight_count,
  AVG(load_factor) AS avg_load_factor,
  SUM(booked_kg) AS total_booked_kg,
  SUM(cargo_capacity_kg) AS total_capacity_kg,
  SUM(total_revenue_gbp) AS total_revenue_gbp,
  ROUND(AVG(avg_yield_gbp_per_kg), 3) AS avg_yield_gbp_per_kg
FROM serverless_nal_catalog.cargo_silver.flight_utilization
GROUP BY ALL
ORDER BY month, total_revenue_gbp DESC;

-- Competitor benchmark (for the agent + dashboards)
CREATE OR REPLACE TABLE serverless_nal_catalog.cargo_gold.competitor_benchmark AS
WITH our_rates AS (
  SELECT
    lane,
    origin_iata,
    destination_iata,
    commodity_code,
    AVG(rate_gbp_per_kg) AS our_avg_rate
  FROM serverless_nal_catalog.cargo_silver.awb_enriched
  WHERE flight_date >= DATE_ADD(CURRENT_DATE(), -90)
  GROUP BY ALL
)
SELECT
  CONCAT(c.origin_iata, '-', c.destination_iata) AS lane,
  c.origin_iata,
  c.destination_iata,
  c.commodity_code,
  c.competitor,
  c.rate_gbp_per_kg AS competitor_rate,
  o.our_avg_rate,
  ROUND((c.rate_gbp_per_kg - o.our_avg_rate) / NULLIF(o.our_avg_rate, 0) * 100, 1) AS gap_pct
FROM serverless_nal_catalog.cargo_bronze.competitor_rates c
LEFT JOIN our_rates o ON c.origin_iata = o.origin_iata AND c.destination_iata = o.destination_iata AND c.commodity_code = o.commodity_code;

SELECT 'Silver + gold tables built' AS status
