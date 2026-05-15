"""Databricks data access via Statement Execution API, using the SDK for auth.

Auth chain (auto-resolved by the SDK):
  - Databricks Apps runtime: DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET → OAuth M2M
  - Local dev: ~/.databrickscfg profile (or DATABRICKS_HOST + DATABRICKS_TOKEN)
"""
import os
import time
import json
from typing import Any
from databricks.sdk import WorkspaceClient

WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"]
CATALOG = os.environ["CATALOG"]

# Single client - the SDK handles token refresh internally
_w: WorkspaceClient | None = None

def _client() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w


def query(statement: str, max_rows: int = 10000) -> list[dict[str, Any]]:
    w = _client()
    api = w.api_client

    # Submit
    body = {
        "warehouse_id": WAREHOUSE_ID,
        "statement": statement,
        "wait_timeout": "50s",
        "disposition": "INLINE",
        "row_limit": max_rows,
    }
    result = api.do("POST", "/api/2.0/sql/statements/", body=body)
    statement_id = result.get("statement_id")

    # Poll
    while result.get("status", {}).get("state") in ("PENDING", "RUNNING"):
        time.sleep(1)
        result = api.do("GET", f"/api/2.0/sql/statements/{statement_id}")

    state = result.get("status", {}).get("state")
    if state != "SUCCEEDED":
        err = result.get("status", {}).get("error", {}).get("message", "unknown error")
        raise RuntimeError(f"SQL failed ({state}): {err[:500]}")

    schema = result.get("manifest", {}).get("schema", {}).get("columns", [])
    cols = [c["name"] for c in schema]
    rows = result.get("result", {}).get("data_array", []) or []
    out = []
    for row in rows:
        d = {}
        for i, col in enumerate(cols):
            v = row[i] if i < len(row) else None
            d[col] = _coerce(v, schema[i].get("type_name", "STRING"))
        out.append(d)
    return out


def _coerce(v, type_name):
    if v is None:
        return None
    if type_name in ("INT", "LONG", "SHORT", "BYTE"):
        try: return int(v)
        except: return v
    if type_name in ("FLOAT", "DOUBLE", "DECIMAL"):
        try: return float(v)
        except: return v
    if type_name == "BOOLEAN":
        return v == "true" or v is True
    return v
