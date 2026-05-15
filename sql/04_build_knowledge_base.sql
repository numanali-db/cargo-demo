-- =============================================================================
-- Knowledge Base for Cargo Yield Agent RAG
-- =============================================================================

CREATE OR REPLACE TABLE ${CATALOG}.cargo_ai.knowledge_base (
  doc_id STRING NOT NULL,
  category STRING,
  title STRING,
  content STRING,
  source STRING,
  tags ARRAY<STRING>,
  last_updated TIMESTAMP
)
TBLPROPERTIES (delta.enableChangeDataFeed = true);

INSERT INTO ${CATALOG}.cargo_ai.knowledge_base VALUES
('KB-001', 'iata_rules', 'IATA Cargo Rate Construction Basics',
'Air cargo rates are constructed using General Cargo Rates (GCR), Specific Commodity Rates (SCR), and Class Rates. The chargeable weight is the higher of gross weight or volumetric weight (6000 cm3 per kg standard divisor). Minimum charges apply at MIN level. Rates published in TACT (The Air Cargo Tariff) form the industry reference. Virgin Atlantic publishes its own rates per lane per commodity, with negotiated discounts for tier-1 forwarders.',
'IATA TACT, internal Virgin Atlantic cargo manual', ARRAY('rate_construction','iata','tact'),
CURRENT_TIMESTAMP()),

('KB-002', 'special_cargo', 'Pharmaceutical (PHC) Handling Requirements',
'Pharmaceutical cargo (PHC) requires temperature-controlled handling between 2-8C (CRT) or 15-25C (CRT+) or controlled at frozen levels. CEIV Pharma certification required at origin and destination stations. Cool dollies, thermal blankets, or active containers (Envirotainer, va-Q-tec, CSafe) must be available. Premium of 30-50% over general cargo justified by cool chain integrity. Delays >2 hours on tarmac require active container intervention. Virgin Atlantic LHR, ATL, JFK, BOS, LAX stations are CEIV Pharma certified.',
'Virgin Atlantic Cool Chain Handbook, IATA CEIV', ARRAY('pharma','cool_chain','phc'),
CURRENT_TIMESTAMP()),

('KB-003', 'special_cargo', 'Perishables (PER) Handling Requirements',
'Perishables include flowers, fruit, vegetables, fresh fish, meat, and live seafood. Temperature requirements vary: tropical fruit 13-15C, leafy vegetables 0-4C, cut flowers 2-5C. Maximum tarmac exposure 60 minutes. Cool dolly priority. Premium 25-35% over general cargo. Ethylene-sensitive cargo (flowers) must not be co-loaded with ethylene producers (apples, bananas). Lead time 24 hours notice required for cool dolly allocation. LHR-DEL, LHR-BOM, LHR-LOS lanes have high perishables demand.',
'Virgin Atlantic Perishables SOP', ARRAY('perishables','per','flowers','fresh'),
CURRENT_TIMESTAMP()),

('KB-004', 'special_cargo', 'Live Animals (AVI) Handling',
'Live Animal Regulations (LAR) compliance mandatory. Species-specific container requirements (IATA CR for cats/dogs, Container Requirements for horses, etc.). Temperature, ventilation, and feeding restrictions. Brachycephalic breeds restricted on some flights. Equine cargo (horses) requires specialist handling and dedicated stalls. Falcons accepted in cabin or cargo subject to embargo lists. Premium 60-80% over GCR. AVI cannot transit without proper veterinary documentation.',
'IATA LAR, Virgin Atlantic AVI Manual', ARRAY('avi','animals','horses','falcons'),
CURRENT_TIMESTAMP()),

('KB-005', 'special_cargo', 'Valuables (VAL) Handling',
'Valuables include cash, bullion, banknotes, securities, jewelry, precious stones. Vault storage required at origin and destination. Armored ground transport. Maximum value per AWB typically capped at limits per Warsaw/Montreal Convention unless special declared value applies. Premium 80-100% over GCR. Strict chain of custody documentation. Virgin Atlantic offers VAL service on LHR-JFK, LHR-LAX, LHR-HKG, LHR-DXB (interline) lanes. Brink''s and Loomis are common forwarder partners.',
'Virgin Atlantic VAL SOP', ARRAY('val','valuables','bullion','jewelry'),
CURRENT_TIMESTAMP()),

('KB-006', 'special_cargo', 'Dangerous Goods (DGR) Handling',
'Dangerous Goods Regulations (DGR) compliance mandatory. Class 1 (Explosives), Class 2 (Gases), Class 3 (Flammable Liquids), Class 4 (Flammable Solids), Class 5 (Oxidizers), Class 6 (Toxics), Class 7 (Radioactive), Class 8 (Corrosives), Class 9 (Misc). Some classes embargoed entirely (e.g., Class 1.1, Class 7 above limits). Lithium batteries (UN3480, UN3481) subject to specific quantity limits and packaging. Premium 30-50% over GCR. Acceptance subject to DGR check at origin.',
'IATA DGR, Virgin Atlantic DG Acceptance', ARRAY('dgr','dangerous_goods','lithium'),
CURRENT_TIMESTAMP()),

('KB-007', 'pricing_strategy', 'Forwarder Discount Tiers',
'Virgin Atlantic forwarder tiers: Platinum (DSV, K+N, DHL Global) receive 10-12% off published rates, dedicated capacity allocations on key lanes, 30-day credit terms, and named cargo account manager. Gold tier (Expeditors, CEVA, Bollore) receive 6-8% off, named contact, 30-day terms. Silver (Yusen, Geodis, Nippon Express) receive 3-4% off, 14-day terms. Bronze and direct shippers pay published rates. Annual contract negotiation in Q4 for following calendar year.',
'Virgin Atlantic Commercial Cargo Manual 2026', ARRAY('forwarder','discount','tier','contract'),
CURRENT_TIMESTAMP()),

('KB-008', 'pricing_strategy', 'Yield Management Principles',
'Cargo yield management aims to maximize revenue per available ton kilometer (RATK). Key levers: dynamic rate adjustment based on remaining capacity, booking acceptance to favor higher-yield commodities when capacity constrained, overbooking calibrated to historical no-show rates (~5-8%), priority allocation for premium cargo. Last 72 hours before flight: premium for AOG, pharma, time-sensitive. First 7-21 days before flight: standard rates for general cargo to fill base load. Competitor benchmark refresh weekly.',
'Virgin Atlantic Yield Strategy 2026', ARRAY('yield','pricing','capacity','overbooking'),
CURRENT_TIMESTAMP()),

('KB-009', 'pricing_strategy', 'Capacity Premium and Discount Logic',
'When flight cargo capacity utilization exceeds 80%, apply capacity premium of 10-25% above base rate. When utilization is below 50% and within 7 days of flight, apply tactical discount of 5-15% to fill capacity. Premium cargo (PHC, AVI, VAL, AOG) is never discounted regardless of load factor. Charter cargo for specific events (Formula 1, pharma launches, AOG recovery) priced separately, typically 2-3x published rates.',
'Virgin Atlantic Rate Card 2026', ARRAY('capacity','premium','discount','tactical'),
CURRENT_TIMESTAMP()),

('KB-010', 'competitive', 'Key Competitor Positioning',
'British Airways IAG Cargo: Same UK origin, similar lane structure. Strong on US transatlantic. Aggressive on pharma. Lufthansa Cargo: Frankfurt hub, broader European feed, premium pharma. Air France-KLM Cargo: AMS and CDG hubs, strong Asia connectivity. Emirates SkyCargo: DXB hub, dominant on India and Africa. Delta and United: US carriers, transatlantic competition only on direct lanes. Position Virgin Atlantic on: UK-India lanes (#1 share), UK-US pharma corridor, Caribbean leisure cargo, and Lagos premium freight.',
'Virgin Atlantic Commercial Intel Brief Q1 2026', ARRAY('competitor','iag','lufthansa','emirates'),
CURRENT_TIMESTAMP()),

('KB-011', 'lanes', 'LHR-JFK Lane Characteristics',
'LHR-JFK is Virgin Atlantic''s flagship cargo lane, operating 3x daily. Average cargo capacity 18,000 kg per flight. Strong demand for transatlantic finance documents, e-commerce, pharma. Competition: BA IAG Cargo (4x daily), AA Cargo (2x daily), Delta Cargo (3x daily JFK-LHR/LGW). Average yield £2.85/kg general cargo, £4.10/kg pharma, £5.50/kg express. Peak demand: Mon-Wed eastbound, Thu-Fri westbound. Load factor target 80%.',
'Virgin Atlantic LHR-JFK Brief', ARRAY('lhr-jfk','lane','transatlantic'),
CURRENT_TIMESTAMP()),

('KB-012', 'lanes', 'LHR-DEL / LHR-BOM India Lanes',
'India lanes (LHR-DEL, LHR-BOM, LHR-BLR) are Virgin Atlantic''s highest-yield routes. Strong demand for: pharma (India is global generic pharma hub), perishables (flowers, vegetables), garments, IT equipment. Average yield £3.40-3.55/kg. Capacity 22,000 kg per B789 flight. Competition: Emirates via DXB (still 30% lower yield due to longer routing), Air India direct, IndiGo (limited cargo). Red Sea disruption 2024 increased VAA share by 18%. Mumbai/Delhi pharma corridor: 35% of LHR-India cargo revenue.',
'Virgin Atlantic India Cargo Strategy', ARRAY('india','del','bom','blr','pharma'),
CURRENT_TIMESTAMP()),

('KB-013', 'regulations', 'UK Customs and Trade Documents',
'Post-Brexit, UK cargo to/from EU requires customs declarations (CDS for UK, NCTS for transit). Common documents: Commercial Invoice, Packing List, Bill of Lading or Air Waybill, Certificate of Origin, EUR1 Movement Certificate (where preferences apply). Specialist documents for pharma (CITES if applicable), valuables (special declared value form), animals (CITES, veterinary). Customs clearance times: HMRC 4-6 hours typical, can be 24 hours for complex shipments. AEO (Authorised Economic Operator) status enables faster clearance.',
'HMRC Cargo Manual, Virgin Atlantic Customs Guide', ARRAY('customs','brexit','hmrc','aeo'),
CURRENT_TIMESTAMP()),

('KB-014', 'operations', 'AOG (Aircraft on Ground) Recovery',
'Aircraft on Ground (AOG) shipments are top-priority cargo to recover an aircraft stranded due to part failure. Premium 100-200% over GCR. Bookings handled by AOG desk 24/7. Common parts: engine components, avionics, landing gear, hydraulics. Critical SLA: confirmed quote within 30 minutes, uplifted within 4 hours of acceptance. Forwarders: Bollore Logistics, Kuehne+Nagel, AOG Services. Often routes via interline if direct capacity unavailable. Revenue contribution: small volume, high yield, brand value.',
'Virgin Atlantic AOG Procedure', ARRAY('aog','aircraft','urgent','24x7'),
CURRENT_TIMESTAMP()),

('KB-015', 'operations', 'No-Show and Offload Management',
'Cargo no-show rate averages 6-8% across the network. Higher for general cargo (10-12%) than premium (2-3%). Overbooking strategy: book 5-8% above flight capacity, scaled by lane volatility. Offloads occur when actual cargo arriving exceeds capacity. Priority sequence for offloads: AOG > Pharma > Perishables > Valuables > Express > General. Compensation for offloads: refund of freight + rebooking on next available flight. Repeated offloads on same forwarder trigger commercial review.',
'Virgin Atlantic Capacity Management SOP', ARRAY('no_show','offload','overbooking','capacity'),
CURRENT_TIMESTAMP());

SELECT COUNT(*) AS knowledge_base_size FROM ${CATALOG}.cargo_ai.knowledge_base
