# Databricks notebook source
# MAGIC %md
# MAGIC # Cargo Gold Quality Checks
# MAGIC
# MAGIC Post-pipeline assertions. Fails the job if data looks off so the agent doesn't quote on bad data.

# COMMAND ----------

CATALOG = "serverless_nal_catalog"

checks = [
    ("awb_count > 0",
     f"SELECT COUNT(*) AS n FROM {CATALOG}.cargo_silver.awb_enriched"),
    ("gold lane_monthly_summary not empty",
     f"SELECT COUNT(*) AS n FROM {CATALOG}.cargo_gold.lane_monthly_summary"),
    ("at least 10 distinct forwarders",
     f"SELECT COUNT(*) AS n FROM {CATALOG}.cargo_gold.forwarder_performance"),
    ("at least 1 month of recent revenue",
     f"SELECT COUNT(*) AS n FROM {CATALOG}.cargo_gold.lane_monthly_summary WHERE month >= date_add(current_date(), -90)"),
    ("competitor benchmark populated",
     f"SELECT COUNT(*) AS n FROM {CATALOG}.cargo_gold.competitor_benchmark WHERE our_avg_rate IS NOT NULL"),
]

failed = []
for name, sql in checks:
    n = spark.sql(sql).collect()[0]["n"]
    status = "✓" if n > 0 else "✗"
    print(f"{status} {name}: {n}")
    if n == 0:
        failed.append(name)

if failed:
    raise Exception(f"Quality checks failed: {failed}")

print("\nAll quality checks passed.")
