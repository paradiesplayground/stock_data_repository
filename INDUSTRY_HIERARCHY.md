# Readable Industry Hierarchy

Taxonomy version: `sic-1987-readable-v1`

This is the complete hierarchy accepted by `exclude_industry_groups`: all 10 SIC divisions and
all 83 SIC major groups from the OSHA 1987 SIC manual, plus curated cross-division groups. Pass a
displayed key or its unambiguous label. Direct `exclude_sic_prefixes` remain available for advanced
overrides.

Source: [OSHA SIC Manual](https://www.osha.gov/data/sic-manual)

## Curated cross-division groups

- `curated:healthcare` — Healthcare
  - `curated:pharma_biotech` — Pharmaceuticals and Biotechnology (`283*`)
  - `curated:medical_devices` — Medical Devices and Supplies (`384*`, `385*`)
  - `curated:health_services` — Health Services (`80*`)
  - `curated:medical_distribution` — Medical and Drug Distribution (`5047`, `5122`)
  - `curated:health_plans` — Hospital and Medical Service Plans (`6324`)
  - `curated:medical_equipment_rental` — Medical Equipment Rental (`7352`)
  - `curated:life_sciences_research` — Life Sciences Research (`8731`; can include non-healthcare research)

## Division A — Agriculture, Forestry, and Fishing

- `division:agriculture_forestry_fishing` — entire division
- `industry:agricultural_production_crops` — 01 Agricultural Production — Crops
- `industry:agricultural_production_livestock` — 02 Agricultural Production — Livestock and Animal Specialties
- `industry:agricultural_services` — 07 Agricultural Services
- `industry:forestry` — 08 Forestry
- `industry:fishing_hunting_trapping` — 09 Fishing, Hunting, and Trapping

## Division B — Mining

- `division:mining` — entire division
- `industry:metal_mining` — 10 Metal Mining
- `industry:coal_mining` — 12 Coal Mining
- `industry:oil_gas_extraction` — 13 Oil and Gas Extraction
- `industry:nonmetallic_minerals` — 14 Nonmetallic Minerals, Except Fuels

## Division C — Construction

- `division:construction` — entire division
- `industry:building_construction` — 15 Building Construction and General Contractors
- `industry:heavy_construction` — 16 Heavy Construction Other Than Buildings
- `industry:special_trade_contractors` — 17 Construction Special Trade Contractors

## Division D — Manufacturing

- `division:manufacturing` — entire division
- `industry:food_products` — 20 Food and Kindred Products
- `industry:tobacco_products` — 21 Tobacco Products
- `industry:textile_mill_products` — 22 Textile Mill Products
- `industry:apparel_products` — 23 Apparel and Other Textile Products
- `industry:lumber_wood_products` — 24 Lumber and Wood Products
- `industry:furniture_fixtures` — 25 Furniture and Fixtures
- `industry:paper_products` — 26 Paper and Allied Products
- `industry:printing_publishing` — 27 Printing, Publishing, and Allied Industries
- `industry:chemicals_allied_products` — 28 Chemicals and Allied Products
- `industry:petroleum_refining` — 29 Petroleum Refining and Related Industries
- `industry:rubber_plastics` — 30 Rubber and Miscellaneous Plastics Products
- `industry:leather_products` — 31 Leather and Leather Products
- `industry:stone_clay_glass_concrete` — 32 Stone, Clay, Glass, and Concrete Products
- `industry:primary_metals` — 33 Primary Metal Industries
- `industry:fabricated_metals` — 34 Fabricated Metal Products
- `industry:machinery_computer_equipment` — 35 Machinery and Computer Equipment
- `industry:electronic_electrical_equipment` — 36 Electronic and Electrical Equipment and Components
- `industry:transportation_equipment` — 37 Transportation Equipment
- `industry:instruments_medical_optical_goods` — 38 Measuring, Medical, Optical, and Photographic Instruments
- `industry:miscellaneous_manufacturing` — 39 Miscellaneous Manufacturing Industries

## Division E — Transportation, Communications, and Utilities

- `division:transportation_communications_utilities` — entire division
- `industry:railroad_transportation` — 40 Railroad Transportation
- `industry:passenger_ground_transportation` — 41 Local and Interurban Passenger Transportation
- `industry:motor_freight_warehousing` — 42 Motor Freight Transportation and Warehousing
- `industry:postal_service` — 43 United States Postal Service
- `industry:water_transportation` — 44 Water Transportation
- `industry:air_transportation` — 45 Transportation by Air
- `industry:pipelines` — 46 Pipelines, Except Natural Gas
- `industry:transportation_services` — 47 Transportation Services
- `industry:communications` — 48 Communications
- `industry:electric_gas_sanitary_services` — 49 Electric, Gas, and Sanitary Services

## Division F — Wholesale Trade

- `division:wholesale_trade` — entire division
- `industry:wholesale_durable_goods` — 50 Wholesale Trade — Durable Goods
- `industry:wholesale_nondurable_goods` — 51 Wholesale Trade — Nondurable Goods

## Division G — Retail Trade

- `division:retail_trade` — entire division
- `industry:building_materials_hardware` — 52 Building Materials and Hardware Dealers
- `industry:general_merchandise_stores` — 53 General Merchandise Stores
- `industry:food_stores` — 54 Food Stores
- `industry:auto_dealers_gas_stations` — 55 Automotive Dealers and Gasoline Service Stations
- `industry:apparel_accessory_stores` — 56 Apparel and Accessory Stores
- `industry:home_furnishings_stores` — 57 Home Furniture, Furnishings, and Equipment Stores
- `industry:eating_drinking_places` — 58 Eating and Drinking Places
- `industry:miscellaneous_retail` — 59 Miscellaneous Retail

## Division H — Finance, Insurance, and Real Estate

- `division:finance_insurance_real_estate` — entire division
- `industry:depository_institutions` — 60 Depository Institutions
- `industry:nondepository_credit` — 61 Nondepository Credit Institutions
- `industry:securities_brokers_exchanges` — 62 Securities and Commodity Brokers, Dealers, and Exchanges
- `industry:insurance_carriers` — 63 Insurance Carriers
- `industry:insurance_agents_brokers` — 64 Insurance Agents, Brokers, and Services
- `industry:real_estate` — 65 Real Estate
- `industry:holding_investment_offices` — 67 Holding and Other Investment Offices

## Division I — Services

- `division:services` — entire division
- `industry:lodging` — 70 Hotels and Other Lodging Places
- `industry:personal_services` — 72 Personal Services
- `industry:business_services` — 73 Business Services
- `industry:automotive_repair_parking` — 75 Automotive Repair, Services, and Parking
- `industry:miscellaneous_repair` — 76 Miscellaneous Repair Services
- `industry:motion_pictures` — 78 Motion Pictures
- `industry:amusement_recreation` — 79 Amusement and Recreation Services
- `industry:health_services` — 80 Health Services
- `industry:legal_services` — 81 Legal Services
- `industry:educational_services` — 82 Educational Services
- `industry:social_services` — 83 Social Services
- `industry:museums_gardens_zoos` — 84 Museums, Art Galleries, Botanical Gardens, and Zoos
- `industry:membership_organizations` — 86 Membership Organizations
- `industry:engineering_research_management` — 87 Engineering, Research, and Management Services
- `industry:private_households` — 88 Private Households
- `industry:miscellaneous_services` — 89 Miscellaneous Services

## Division J — Public Administration and Unclassified

- `division:public_administration` — entire division
- `industry:general_government` — 91 Executive, Legislative, and General Government
- `industry:justice_public_safety` — 92 Justice, Public Order, and Safety
- `industry:public_finance` — 93 Public Finance, Taxation, and Monetary Policy
- `industry:human_resource_programs` — 94 Administration of Human Resource Programs
- `industry:environmental_housing_programs` — 95 Environmental Quality and Housing Programs
- `industry:economic_programs` — 96 Administration of Economic Programs
- `industry:national_security_international_affairs` — 97 National Security and International Affairs
- `industry:nonclassifiable` — 99 Nonclassifiable Establishments

## Examples

```text
exclude_industry_groups=["Healthcare"]
exclude_industry_groups=["Pharmaceuticals and Biotechnology", "Medical Devices and Supplies"]
exclude_industry_groups=["Manufacturing", "Oil and Gas Extraction"]
```
