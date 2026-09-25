"""Lakebase Postgres access for the operational (OLTP) layer — RFQ inbox + quotes.

Auth chain (auto-resolved by the SDK's WorkspaceClient):
  - Databricks Apps runtime: DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET → OAuth M2M.
    The app connects to Postgres as its own service principal (Postgres role == client id).
  - Local dev: ~/.databrickscfg profile; connects as the current human user.

A short-lived Postgres OAuth token is minted via POST /api/2.0/postgres/credentials
(the same call as `databricks postgres generate-database-credential`) and cached
with a ~50-min TTL (tokens expire after 1h). Connections are opened per call — cheap
enough for a demo and robust against scale-to-zero drops and token rotation.
"""
import os
import time
import threading
import datetime
from decimal import Decimal
from typing import Any
from databricks.sdk import WorkspaceClient
import psycopg2
from psycopg2.extras import RealDictCursor

PGHOST = os.environ["PGHOST"]
PGDATABASE = os.environ.get("PGDATABASE", "cargo")
# Full resource path of the Lakebase endpoint, e.g.
# projects/cargo-yield/branches/production/endpoints/primary
LAKEBASE_ENDPOINT = os.environ["LAKEBASE_ENDPOINT"]

_w: WorkspaceClient | None = None
_token: str | None = None
_token_exp: float = 0.0
_lock = threading.Lock()


def _client() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w


def _pg_user(w: WorkspaceClient) -> str:
    # In the Apps runtime this is the app SP's client id — the Postgres role we granted.
    cid = os.environ.get("DATABRICKS_CLIENT_ID")
    if cid:
        return cid
    return w.current_user.me().user_name  # local dev: the human user


def _token_now(w: WorkspaceClient) -> str:
    global _token, _token_exp
    with _lock:
        if _token and time.time() < _token_exp:
            return _token
        resp = w.api_client.do(
            "POST", "/api/2.0/postgres/credentials",
            body={"endpoint": LAKEBASE_ENDPOINT},
        )
        _token = resp["token"]
        _token_exp = time.time() + 50 * 60  # refresh well before the 1h expiry
        return _token


def _connect(retries: int = 2):
    """Open a connection, retrying transient errors (e.g. SSL EOF on scale-to-zero
    cold starts, where the autoscaling endpoint is spinning back up)."""
    w = _client()
    last = None
    for attempt in range(retries + 1):
        try:
            return psycopg2.connect(
                host=PGHOST, port=5432, dbname=PGDATABASE,
                user=_pg_user(w), password=_token_now(w), sslmode="require",
                connect_timeout=15,
            )
        except psycopg2.OperationalError as e:
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))  # 2s, 4s — enough for a cold endpoint
    raise last


def _coerce(v: Any) -> Any:
    """Make values JSON-friendly, matching the old warehouse output shape."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return v


def query(sql: str, params: tuple | list | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as JSON-friendly dicts."""
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [{k: _coerce(v) for k, v in row.items()} for row in rows]
    finally:
        conn.close()


def execute(sql: str, params: tuple | list | None = None, returning: bool = False):
    """Run INSERT/UPDATE/DELETE. If returning=True, return the first row's values."""
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            out = None
            if returning:
                row = cur.fetchone()
                out = {k: _coerce(v) for k, v in row.items()} if row else None
        conn.commit()
        return out
    finally:
        conn.close()
