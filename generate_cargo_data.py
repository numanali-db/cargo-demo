"""
Virgin Atlantic Cargo Yield Agent - Synthetic Data Generator

Generates realistic cargo data calibrated to VAA's actual footprint:
- 199K tonnes/year (FY2024 baseline)
- 26,380 flights/year (FY2025)
- £236M cargo revenue (FY2024)
- Key lanes: LHR-JFK, LHR-DEL, MAN-MCO, LHR-LAX, LHR-ATL, etc.

Tables generated (written as parquet, uploaded to UC volumes for ingestion):
  bronze_awb_raw         - Raw Air Waybills (the primary fact)
  bronze_flight_schedule - Daily flight schedule with cargo capacity
  bronze_forwarders      - Freight forwarder accounts
  bronze_commodities     - IATA commodity codes + handling tiers
  bronze_competitor_rates - Scraped competitor pricing intel
  bronze_rfq_inbox       - Live RFQs awaiting quotes (for the agent UI)
"""

import polars as pl
import random
import datetime as dt
from pathlib import Path

random.seed(20260511)
OUT_DIR = Path("/tmp/cargo_demo/data")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# -- Reference data -----------------------------------------------------------

LANES = [
    # (origin, destination, weekly_freq, avg_cargo_capacity_kg, base_rate_per_kg_gbp)
    ("LHR", "JFK", 21, 18000, 2.85),
    ("LHR", "LAX", 14, 16000, 3.10),
    ("LHR", "ATL", 14, 15000, 2.70),
    ("LHR", "BOS", 14, 14000, 2.65),
    ("LHR", "MCO", 7,  16000, 2.45),
    ("LHR", "MIA", 7,  15000, 2.60),
    ("LHR", "DEL", 14, 22000, 3.40),  # India lanes - high yield
    ("LHR", "BOM", 14, 21000, 3.50),
    ("LHR", "BLR", 7,  18000, 3.55),
    ("LHR", "JNB", 7,  20000, 3.20),
    ("LHR", "LOS", 7,  19000, 3.80),  # Lagos - premium
    ("LHR", "TLV", 7,  14000, 3.10),
    ("LHR", "HKG", 7,  18000, 3.90),  # Asia premium
    ("LHR", "DXB", 0,  0,     0),     # No direct, but interlined
    ("MAN", "JFK", 7,  12000, 2.60),
    ("MAN", "MCO", 7,  13000, 2.30),
    ("MAN", "ATL", 7,  11000, 2.55),
    ("EDI", "JFK", 3,  9000,  2.55),
]

COMMODITIES = [
    # (code, name, handling_tier, rate_multiplier, weight_share_pct)
    ("GEN", "General Cargo",          "Standard",  1.00, 45),
    ("PHC", "Pharmaceutical",         "Premium",   1.45, 8),
    ("PER", "Perishables",            "Premium",   1.30, 12),
    ("AVI", "Live Animals",           "Premium",   1.80, 2),
    ("VAL", "Valuables",              "Premium",   1.95, 1),
    ("DGR", "Dangerous Goods",        "Premium",   1.40, 4),
    ("HUM", "Human Remains",          "Specialist", 1.55, 1),
    ("AOG", "Aircraft on Ground",     "Specialist", 2.10, 1),
    ("MAL", "Mail",                   "Standard",  0.85, 6),
    ("EXP", "Express",                "Premium",   1.35, 10),
    ("AUT", "Automotive Parts",       "Standard",  1.05, 5),
    ("HEA", "Healthcare/Medical Dev", "Premium",   1.40, 5),
]
COMMODITY_WEIGHTS = [c[4] for c in COMMODITIES]

FORWARDERS = [
    # (name, account_tier, volume_share, negotiation_strength, base_discount_pct)
    ("DSV Air & Sea",            "Platinum", 0.18, 0.95, 12),
    ("Kuehne+Nagel",             "Platinum", 0.16, 0.93, 11),
    ("DHL Global Forwarding",    "Platinum", 0.14, 0.92, 10),
    ("Expeditors International", "Gold",     0.10, 0.85, 8),
    ("CEVA Logistics",           "Gold",     0.07, 0.82, 7),
    ("Bollore Logistics",        "Gold",     0.05, 0.78, 6),
    ("Hellmann Worldwide",       "Gold",     0.04, 0.76, 6),
    ("Yusen Logistics",          "Silver",   0.04, 0.70, 4),
    ("Geodis",                   "Silver",   0.04, 0.70, 4),
    ("Nippon Express",           "Silver",   0.03, 0.68, 3),
    ("Agility Logistics",        "Silver",   0.03, 0.65, 3),
    ("Crane Worldwide",          "Bronze",   0.02, 0.55, 0),
    ("Rhenus Logistics",         "Bronze",   0.02, 0.55, 0),
    ("MSC Air Cargo",            "Bronze",   0.02, 0.55, 0),
    ("Other / Direct",           "Bronze",   0.06, 0.50, 0),
]
FORWARDER_NAMES = [f[0] for f in FORWARDERS]
FORWARDER_WEIGHTS = [f[2] for f in FORWARDERS]

COMPETITORS = ["British Airways IAG Cargo", "Lufthansa Cargo", "Air France-KLM Cargo",
               "Emirates SkyCargo", "Delta Cargo", "United Cargo", "American Airlines Cargo"]

# -- Forwarders table ---------------------------------------------------------
df_forwarders = pl.DataFrame({
    "forwarder_id": [f"FWD{i:04d}" for i in range(1, len(FORWARDERS)+1)],
    "forwarder_name": [f[0] for f in FORWARDERS],
    "account_tier": [f[1] for f in FORWARDERS],
    "annual_volume_share": [f[2] for f in FORWARDERS],
    "negotiation_strength": [f[3] for f in FORWARDERS],
    "base_discount_pct": [f[4] for f in FORWARDERS],
    "credit_terms_days": [30 if f[1] in ("Platinum", "Gold") else 14 for f in FORWARDERS],
    "primary_contact_email": [f"cargo@{f[0].split()[0].lower().replace(',','').replace('+','-')}.com" for f in FORWARDERS],
})
df_forwarders.write_parquet(OUT_DIR / "bronze_forwarders.parquet")
print(f"  forwarders: {df_forwarders.shape}")

# -- Commodities table --------------------------------------------------------
df_commodities = pl.DataFrame({
    "commodity_code": [c[0] for c in COMMODITIES],
    "commodity_name": [c[1] for c in COMMODITIES],
    "handling_tier": [c[2] for c in COMMODITIES],
    "rate_multiplier": [c[3] for c in COMMODITIES],
    "weight_share_pct": [c[4] for c in COMMODITIES],
    "requires_certification": [c[0] in ("PHC", "AVI", "DGR", "HUM") for c in COMMODITIES],
    "temp_controlled": [c[0] in ("PHC", "PER", "HEA") for c in COMMODITIES],
})
df_commodities.write_parquet(OUT_DIR / "bronze_commodities.parquet")
print(f"  commodities: {df_commodities.shape}")

# -- Flight schedule ----------------------------------------------------------
# 12 months back to 1 month forward from today
START = dt.date(2025, 5, 1)
END = dt.date(2026, 6, 30)
flights = []
flight_id = 100000
for day in range((END - START).days + 1):
    current = START + dt.timedelta(days=day)
    dow = current.weekday()  # 0=Mon, 6=Sun
    for origin, dest, weekly_freq, avg_cap, _ in LANES:
        if weekly_freq == 0:
            continue
        # Probability this lane operates on this day of week
        if random.random() > weekly_freq / 7:
            continue
        flight_id += 1
        cap_kg = int(avg_cap * random.uniform(0.85, 1.15))
        # Seasonal: peak Q4, dip Q1
        seasonal = 1.0 + 0.15 * (1 if current.month in [10,11,12] else (-0.1 if current.month in [1,2] else 0))
        cap_kg = int(cap_kg * seasonal)
        flights.append({
            "flight_id": f"VS{flight_id}",
            "flight_date": current,
            "origin_iata": origin,
            "destination_iata": dest,
            "aircraft_type": random.choice(["B789", "A350-1000", "A330-300", "A330-900"]),
            "scheduled_departure_utc": dt.datetime.combine(current, dt.time(random.randint(9,22), random.choice([0,15,30,45]))),
            "cargo_capacity_kg": cap_kg,
            "cargo_volume_m3": int(cap_kg / 165),  # avg density
            "status": random.choices(["scheduled","operated","cancelled"], weights=[5,93,2])[0] if current <= dt.date.today() else "scheduled",
        })
df_flights = pl.DataFrame(flights)
df_flights.write_parquet(OUT_DIR / "bronze_flight_schedule.parquet")
print(f"  flights: {df_flights.shape}")

# -- AWBs ---------------------------------------------------------------------
# Target: ~200K tonnes/year, avg AWB ~500-1500kg, so ~200K AWBs/year
print("  generating AWBs (this may take a moment)...")
awbs = []
awb_serial = 5000000
operated_flights = df_flights.filter(pl.col("status") == "operated").to_dicts()
print(f"    operated flights: {len(operated_flights)}")

# Aim for ~70% cargo load factor on average
for flt in operated_flights:
    target_load_kg = int(flt["cargo_capacity_kg"] * random.uniform(0.55, 0.92))
    booked_kg = 0
    while booked_kg < target_load_kg:
        # Pick commodity (weighted)
        cmd = random.choices(COMMODITIES, weights=COMMODITY_WEIGHTS, k=1)[0]
        # AWB weight distribution: log-normal-ish
        awb_kg = max(50, int(random.lognormvariate(6.0, 0.9)))  # median ~400kg
        if awb_kg > flt["cargo_capacity_kg"] - booked_kg:
            awb_kg = flt["cargo_capacity_kg"] - booked_kg
        if awb_kg < 50:
            break

        # Pick forwarder
        fwd = random.choices(FORWARDERS, weights=FORWARDER_WEIGHTS, k=1)[0]

        # Calculate rate
        base_rate = next(l[4] for l in LANES if l[0] == flt["origin_iata"] and l[1] == flt["destination_iata"])
        comm_mult = cmd[3]
        fwd_discount = fwd[4]
        # Add noise + capacity-driven pricing
        utilization = booked_kg / flt["cargo_capacity_kg"]
        capacity_premium = 1.0 + 0.20 * utilization
        noise = random.uniform(0.92, 1.12)
        rate_gbp_per_kg = round(base_rate * comm_mult * (1 - fwd_discount/100) * capacity_premium * noise, 3)
        revenue_gbp = round(rate_gbp_per_kg * awb_kg, 2)

        # Booking lead time (days before flight)
        lead_days = max(0, int(random.expovariate(1/7)))
        booking_date = flt["flight_date"] - dt.timedelta(days=lead_days)

        awb_serial += 1
        awbs.append({
            "awb_number": f"932-{awb_serial:08d}",
            "flight_id": flt["flight_id"],
            "flight_date": flt["flight_date"],
            "booking_date": booking_date,
            "origin_iata": flt["origin_iata"],
            "destination_iata": flt["destination_iata"],
            "forwarder_name": fwd[0],
            "commodity_code": cmd[0],
            "chargeable_weight_kg": awb_kg,
            "volume_m3": round(awb_kg / random.uniform(150, 200), 2),
            "rate_gbp_per_kg": rate_gbp_per_kg,
            "revenue_gbp": revenue_gbp,
            "special_handling": cmd[2] == "Premium" or cmd[2] == "Specialist",
            "temp_controlled": cmd[0] in ("PHC", "PER", "HEA"),
            "pieces": random.randint(1, max(2, awb_kg // 100)),
            "status": "uplifted",
            "offload_reason": None,
        })
        booked_kg += awb_kg

# Add some recent open RFQs (last 7 days)
recent_flights = [f for f in df_flights.to_dicts() if f["status"] == "scheduled" and f["flight_date"] <= dt.date.today() + dt.timedelta(days=14)][:50]
rfqs = []
for i, flt in enumerate(recent_flights):
    cmd = random.choices(COMMODITIES, weights=COMMODITY_WEIGHTS, k=1)[0]
    fwd = random.choices(FORWARDERS[:8], weights=[f[2] for f in FORWARDERS[:8]], k=1)[0]
    awb_kg = random.choice([500, 1000, 1500, 2000, 3000, 5000, 8000, 12000])
    rfqs.append({
        "rfq_id": f"RFQ-2026-{i+1001:05d}",
        "received_at": dt.datetime.now() - dt.timedelta(hours=random.randint(1, 72)),
        "flight_id": flt["flight_id"],
        "flight_date": flt["flight_date"],
        "origin_iata": flt["origin_iata"],
        "destination_iata": flt["destination_iata"],
        "forwarder_name": fwd[0],
        "commodity_code": cmd[0],
        "requested_weight_kg": awb_kg,
        "requested_pieces": random.randint(1, max(2, awb_kg // 200)),
        "ready_date": flt["flight_date"] - dt.timedelta(days=random.randint(0,2)),
        "special_handling": cmd[2] in ("Premium", "Specialist"),
        "temp_controlled": cmd[0] in ("PHC", "PER", "HEA"),
        "status": "pending_quote",
        "customer_max_rate_gbp_per_kg": None,
        "notes": random.choice([
            None, None, None,
            "Need confirmation by EOD",
            "Customer prefers VS direct over interline",
            "Repeat shipper, monthly volume",
            "Bid against IAG Cargo - they quoted £2.95/kg",
            "Pharma cold chain - 2-8C required",
        ]),
    })

df_awbs = pl.DataFrame(awbs)
df_awbs.write_parquet(OUT_DIR / "bronze_awb_raw.parquet")
print(f"  AWBs: {df_awbs.shape} ({df_awbs['chargeable_weight_kg'].sum()/1000:,.0f} tonnes, £{df_awbs['revenue_gbp'].sum()/1e6:.1f}M)")

df_rfqs = pl.DataFrame(rfqs)
df_rfqs.write_parquet(OUT_DIR / "bronze_rfq_inbox.parquet")
print(f"  RFQs: {df_rfqs.shape}")

# -- Competitor rates ---------------------------------------------------------
# Synthetic scrape from competitor APIs / market intel
comp_rates = []
for lane in LANES:
    if lane[2] == 0:
        continue
    for comp in COMPETITORS:
        for cmd in random.sample(COMMODITIES, k=6):
            base_rate = lane[4]
            # Competitors are within +/- 15% of VAA base rate
            comp_rate = round(base_rate * cmd[3] * random.uniform(0.88, 1.12), 3)
            comp_rates.append({
                "scrape_timestamp": dt.datetime.now() - dt.timedelta(hours=random.randint(1, 48)),
                "competitor": comp,
                "origin_iata": lane[0],
                "destination_iata": lane[1],
                "commodity_code": cmd[0],
                "rate_gbp_per_kg": comp_rate,
                "min_weight_kg": random.choice([100, 250, 500, 1000]),
                "source": random.choice(["api","webscrape","industry_intel","forwarder_intel"]),
            })
df_comp = pl.DataFrame(comp_rates)
df_comp.write_parquet(OUT_DIR / "bronze_competitor_rates.parquet")
print(f"  competitor_rates: {df_comp.shape}")

print(f"\nAll files written to: {OUT_DIR}")
print("\nSummary:")
print(f"  Total cargo revenue T12M (synth): £{df_awbs['revenue_gbp'].sum()/1e6:,.1f}M")
print(f"  Total tonnage: {df_awbs['chargeable_weight_kg'].sum()/1000:,.0f}t")
print(f"  Open RFQs in inbox: {len(df_rfqs)}")
print(f"  Active forwarders: {len(df_forwarders)}")
