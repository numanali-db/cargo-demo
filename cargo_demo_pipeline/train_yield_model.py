# Databricks notebook source
# MAGIC %md
# MAGIC # Cargo Yield Model — Training & Registration
# MAGIC
# MAGIC Trains a gradient-boosted yield model on `cargo_silver.awb_enriched` and registers it in Unity Catalog
# MAGIC as `serverless_nal_catalog.cargo_ai.yield_model`. Logged via MLflow for full traceability.

# COMMAND ----------

import mlflow
import mlflow.sklearn
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_percentage_error, mean_absolute_error, r2_score
from mlflow.models.signature import infer_signature

mlflow.set_registry_uri("databricks-uc")

CATALOG = "serverless_nal_catalog"
MODEL_NAME = f"{CATALOG}.cargo_ai.yield_model"

# COMMAND ----------

# Load training data from the DLT-managed silver layer
df = spark.read.table(f"{CATALOG}.cargo_silver.awb_enriched").toPandas()
print(f"Training rows: {len(df):,}")
print(f"Lanes: {df['lane'].nunique()}, Commodities: {df['commodity_code'].nunique()}, Forwarders: {df['forwarder_name'].nunique()}")

# COMMAND ----------

# Feature engineering
df["log_weight"] = np.log1p(df["chargeable_weight_kg"])
# Flight-level capacity utilisation feature
util = (
    spark.read.table(f"{CATALOG}.cargo_silver.flight_utilization")
    .selectExpr("flight_id", "load_factor AS flight_load_factor")
    .toPandas()
)
df = df.merge(util, on="flight_id", how="left")
df["flight_load_factor"] = df["flight_load_factor"].fillna(0.7)

features_cat = ["lane", "commodity_code", "account_tier", "handling_tier", "aircraft_type"]
features_num = ["log_weight", "lead_time_days", "rate_multiplier", "negotiation_strength", "flight_load_factor"]
target = "rate_gbp_per_kg"

# Drop rows with nulls in features/target
df = df.dropna(subset=features_cat + features_num + [target])
X = df[features_cat + features_num]
y = df[target]
print(f"After NA drop: {len(df):,} rows")

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

# COMMAND ----------

# Pipeline: OHE on categorical + scaler on numeric + GBM
preproc = ColumnTransformer([
    ("cat", OneHotEncoder(handle_unknown="ignore"), features_cat),
    ("num", StandardScaler(), features_num),
])
model = Pipeline([
    ("preproc", preproc),
    ("gbm", GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.08, random_state=42, subsample=0.85
    )),
])

# COMMAND ----------

with mlflow.start_run(run_name="cargo_yield_gbm") as run:
    model.fit(X_train, y_train)

    pred = model.predict(X_test)
    mape = mean_absolute_percentage_error(y_test, pred)
    mae = mean_absolute_error(y_test, pred)
    r2 = r2_score(y_test, pred)

    mlflow.log_metrics({"mape": mape, "mae": mae, "r2": r2})
    mlflow.log_params({
        "n_estimators": 200, "max_depth": 4, "learning_rate": 0.08,
        "train_rows": len(X_train), "test_rows": len(X_test),
        "features_cat": ",".join(features_cat),
        "features_num": ",".join(features_num),
    })

    print(f"\n=== Model performance ===")
    print(f"  MAPE: {mape:.1%}")
    print(f"  MAE:  £{mae:.3f}/kg")
    print(f"  R²:   {r2:.3f}")

    sig = infer_signature(X_train.head(100), model.predict(X_train.head(100)))

    info = mlflow.sklearn.log_model(
        sk_model=model,
        artifact_path="model",
        signature=sig,
        registered_model_name=MODEL_NAME,
        input_example=X_train.head(3),
    )
    print(f"\nLogged: {info.model_uri}")
    print(f"Registered: {MODEL_NAME}")

# COMMAND ----------

# Find the version that was just registered (MLflow 3 doesn't expose it on ModelInfo)
from mlflow.tracking import MlflowClient

client = MlflowClient()
versions = client.search_model_versions(f"name='{MODEL_NAME}'")
versions = sorted(versions, key=lambda v: int(v.version), reverse=True)
new_version = versions[0].version
print(f"Latest registered version: {new_version}")

# Set the new version as the production alias
client.set_registered_model_alias(
    name=MODEL_NAME,
    alias="production",
    version=new_version,
)
print(f"Set alias 'production' → version {new_version}")
print(f"MODEL_VERSION={new_version}")
