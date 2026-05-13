# Databricks notebook source
# MAGIC %md
# MAGIC # Sync Cargo Knowledge Base Vector Index
# MAGIC
# MAGIC Task in the cargo_yield_orchestration job. Triggers a sync on the cargo handling-rules
# MAGIC vector index after any upstream changes to the knowledge_base table.

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient

INDEX_NAME = "serverless_nal_catalog.cargo_ai.knowledge_base_index"
ENDPOINT_NAME = "nalvs"

vsc = VectorSearchClient(disable_notice=True)
idx = vsc.get_index(endpoint_name=ENDPOINT_NAME, index_name=INDEX_NAME)

try:
    idx.sync()
    print(f"Triggered sync on {INDEX_NAME}")
except Exception as e:
    print(f"Sync request: {e}")

# Report status
desc = idx.describe()
print(f"State: {desc.get('status', {}).get('detailed_state', 'unknown')}")
print(f"Indexed rows: {desc.get('status', {}).get('indexed_row_count', 'unknown')}")
