# Databricks notebook source
# MAGIC %md
# MAGIC # Virgin Atlantic Cargo Yield — Lakeflow Declarative Pipeline
# MAGIC
# MAGIC Bronze → Silver → Gold transforms for the Cargo Yield Agent demo.
# MAGIC
# MAGIC **Sources:** `${catalog}.cargo_bronze.*`
# MAGIC **Sinks:** `${catalog}.cargo_silver.*`, `${catalog}.cargo_gold.*`
# MAGIC
# MAGIC The pipeline is configured with default schema = `cargo_silver`. Gold tables use
# MAGIC 2-part names (e.g. `cargo_gold.lane_monthly_summary`) to publish across schemas.
# MAGIC
# MAGIC Set the `catalog` configuration on the pipeline (Settings → Advanced → Configuration).

# COMMAND ----------

import dlt
from pyspark.sql import functions as F

# Lakeflow / DLT exposes pipeline configuration via spark.conf.
CATALOG = spark.conf.get("catalog")
BRONZE = f"{CATALOG}.cargo_bronze"


# =============================================================================
# SILVER LAYER (default schema: cargo_silver)
# =============================================================================

@dlt.table(
    name="awb_enriched",
    comment="AWBs joined with forwarder, commodity, and flight metadata. Used by the agent's yield_calc step.",
    table_properties={"quality": "silver"},
)
@dlt.expect_or_drop("valid_weight", "chargeable_weight_kg > 0")
@dlt.expect_or_drop("valid_rate", "rate_gbp_per_kg > 0")
@dlt.expect("known_forwarder", "forwarder_name IS NOT NULL")
@dlt.expect("known_commodity", "commodity_code IS NOT NULL")
def awb_enriched():
    awb = spark.read.table(f"{BRONZE}.awb_raw")
    fwd = spark.read.table(f"{BRONZE}.forwarders")
    cmd = spark.read.table(f"{BRONZE}.commodities")
    sched = spark.read.table(f"{BRONZE}.flight_schedule")
    return (
        awb.alias("a")
        .join(fwd.alias("f"), F.col("a.forwarder_name") == F.col("f.forwarder_name"), "left")
        .join(cmd.alias("c"), F.col("a.commodity_code") == F.col("c.commodity_code"), "left")
        .join(sched.alias("s"), F.col("a.flight_id") == F.col("s.flight_id"), "left")
        .select(
            F.col("a.awb_number"),
            F.col("a.flight_id"),
            F.col("a.flight_date"),
            F.col("a.booking_date"),
            F.datediff(F.col("a.flight_date"), F.col("a.booking_date")).alias("lead_time_days"),
            F.col("a.origin_iata"),
            F.col("a.destination_iata"),
            F.concat_ws("-", F.col("a.origin_iata"), F.col("a.destination_iata")).alias("lane"),
            F.col("a.forwarder_name"),
            F.col("f.account_tier"),
            F.col("f.negotiation_strength"),
            F.col("a.commodity_code"),
            F.col("c.commodity_name"),
            F.col("c.handling_tier"),
            F.col("c.rate_multiplier"),
            F.col("c.temp_controlled"),
            F.col("a.chargeable_weight_kg"),
            F.col("a.volume_m3"),
            F.col("a.pieces"),
            F.col("a.rate_gbp_per_kg"),
            F.col("a.revenue_gbp"),
            F.col("a.special_handling"),
            F.col("a.status"),
            F.col("s.cargo_capacity_kg").alias("flight_capacity_kg"),
            F.col("s.aircraft_type"),
            F.year("a.flight_date").alias("flight_year"),
            F.month("a.flight_date").alias("flight_month"),
            F.date_trunc("month", "a.flight_date").alias("flight_month_start"),
            F.date_trunc("week", "a.flight_date").alias("flight_week_start"),
        )
    )


@dlt.table(
    name="flight_utilization",
    comment="Per-flight capacity utilization. Agent's capacity_check step reads this.",
    table_properties={"quality": "silver"},
)
@dlt.expect("load_factor_in_range", "load_factor >= 0 AND load_factor <= 1.5")
def flight_utilization():
    sched = spark.read.table(f"{BRONZE}.flight_schedule").filter(F.col("status") == "operated")
    awb = spark.read.table(f"{BRONZE}.awb_raw")
    agg = (
        awb.groupBy("flight_id")
        .agg(
            F.sum("chargeable_weight_kg").alias("booked_kg"),
            F.sum("revenue_gbp").alias("total_revenue_gbp"),
            F.count("awb_number").alias("awb_count"),
        )
    )
    return (
        sched.alias("s")
        .join(agg.alias("a"), "flight_id", "left")
        .select(
            F.col("s.flight_id"),
            F.col("s.flight_date"),
            F.col("s.origin_iata"),
            F.col("s.destination_iata"),
            F.concat_ws("-", F.col("s.origin_iata"), F.col("s.destination_iata")).alias("lane"),
            F.col("s.aircraft_type"),
            F.col("s.cargo_capacity_kg"),
            F.coalesce(F.col("a.booked_kg"), F.lit(0)).alias("booked_kg"),
            (F.coalesce(F.col("a.booked_kg"), F.lit(0)) / F.col("s.cargo_capacity_kg")).alias("load_factor"),
            F.coalesce(F.col("a.total_revenue_gbp"), F.lit(0)).alias("total_revenue_gbp"),
            (F.coalesce(F.col("a.total_revenue_gbp"), F.lit(0)) /
             F.when(F.col("a.booked_kg").isNull() | (F.col("a.booked_kg") == 0), F.lit(None))
              .otherwise(F.col("a.booked_kg"))).alias("avg_yield_gbp_per_kg"),
            F.coalesce(F.col("a.awb_count"), F.lit(0)).alias("awb_count"),
        )
    )


# =============================================================================
# GOLD LAYER (cargo_gold schema, published via 2-part name)
# =============================================================================

@dlt.table(
    name="cargo_gold.lane_monthly_summary",
    comment="Monthly revenue and tonnage by lane. Backs the analytics tab.",
    table_properties={"quality": "gold"},
)
def lane_monthly_summary():
    return (
        dlt.read("awb_enriched")
        .groupBy("flight_month_start", "lane", "origin_iata", "destination_iata")
        .agg(
            F.sum("chargeable_weight_kg").alias("tonnage_kg"),
            F.round(F.sum("chargeable_weight_kg") / 1000.0, 1).alias("tonnage_tonnes"),
            F.sum("revenue_gbp").alias("revenue_gbp"),
            F.round(F.avg("rate_gbp_per_kg"), 3).alias("avg_yield_gbp_per_kg"),
            F.countDistinct("awb_number").alias("awb_count"),
            F.countDistinct("forwarder_name").alias("forwarder_count"),
        )
        .withColumnRenamed("flight_month_start", "month")
    )


@dlt.table(
    name="cargo_gold.forwarder_performance",
    comment="Forwarder revenue + premium share. Used by the Forwarders tab.",
    table_properties={"quality": "gold"},
)
def forwarder_performance():
    base = dlt.read("awb_enriched")
    premium = (
        F.when(F.col("handling_tier").isin("Premium", "Specialist"), F.col("revenue_gbp"))
         .otherwise(F.lit(0))
    )
    return (
        base.groupBy("forwarder_name", "account_tier")
        .agg(
            F.countDistinct("awb_number").alias("awb_count"),
            F.sum("chargeable_weight_kg").alias("total_kg"),
            F.sum("revenue_gbp").alias("total_revenue_gbp"),
            F.round(F.avg("rate_gbp_per_kg"), 3).alias("avg_yield_gbp_per_kg"),
            F.countDistinct("lane").alias("lanes_used"),
            F.countDistinct("commodity_code").alias("commodities_shipped"),
            F.round(F.sum(premium) / F.sum("revenue_gbp") * 100, 1).alias("premium_revenue_share_pct"),
        )
    )


@dlt.table(
    name="cargo_gold.commodity_mix",
    comment="Commodity mix and yield. Highlights premium cargo contribution.",
    table_properties={"quality": "gold"},
)
def commodity_mix():
    return (
        dlt.read("awb_enriched")
        .groupBy("commodity_code", "commodity_name", "handling_tier")
        .agg(
            F.sum("chargeable_weight_kg").alias("total_kg"),
            F.sum("revenue_gbp").alias("revenue_gbp"),
            F.round(F.avg("rate_gbp_per_kg"), 3).alias("avg_yield_gbp_per_kg"),
            F.countDistinct("awb_number").alias("awb_count"),
        )
    )


@dlt.table(
    name="cargo_gold.load_factor_trends",
    comment="Monthly load factor by lane. Critical operational metric.",
    table_properties={"quality": "gold"},
)
@dlt.expect("non_null_lane", "lane IS NOT NULL")
def load_factor_trends():
    return (
        dlt.read("flight_utilization")
        .withColumn("month", F.date_trunc("month", "flight_date"))
        .groupBy("month", "lane")
        .agg(
            F.count("flight_id").alias("flight_count"),
            F.avg("load_factor").alias("avg_load_factor"),
            F.sum("booked_kg").alias("total_booked_kg"),
            F.sum("cargo_capacity_kg").alias("total_capacity_kg"),
            F.sum("total_revenue_gbp").alias("total_revenue_gbp"),
            F.round(F.avg("avg_yield_gbp_per_kg"), 3).alias("avg_yield_gbp_per_kg"),
        )
    )


@dlt.table(
    name="cargo_gold.competitor_benchmark",
    comment="Competitor rates vs. our average rate by lane+commodity. Feeds the agent's competitive_check step.",
    table_properties={"quality": "gold"},
)
def competitor_benchmark():
    our_rates = (
        dlt.read("awb_enriched")
        .filter(F.col("flight_date") >= F.date_add(F.current_date(), -90))
        .groupBy("lane", "origin_iata", "destination_iata", "commodity_code")
        .agg(F.avg("rate_gbp_per_kg").alias("our_avg_rate"))
    )
    comp = spark.read.table(f"{BRONZE}.competitor_rates")
    return (
        comp.alias("c")
        .join(
            our_rates.alias("o"),
            (F.col("c.origin_iata") == F.col("o.origin_iata"))
            & (F.col("c.destination_iata") == F.col("o.destination_iata"))
            & (F.col("c.commodity_code") == F.col("o.commodity_code")),
            "left",
        )
        .select(
            F.concat_ws("-", F.col("c.origin_iata"), F.col("c.destination_iata")).alias("lane"),
            F.col("c.origin_iata"),
            F.col("c.destination_iata"),
            F.col("c.commodity_code"),
            F.col("c.competitor"),
            F.col("c.rate_gbp_per_kg").alias("competitor_rate"),
            F.col("o.our_avg_rate"),
            F.round(
                (F.col("c.rate_gbp_per_kg") - F.col("o.our_avg_rate"))
                / F.when(F.col("o.our_avg_rate") == 0, None).otherwise(F.col("o.our_avg_rate"))
                * 100, 1,
            ).alias("gap_pct"),
        )
    )
