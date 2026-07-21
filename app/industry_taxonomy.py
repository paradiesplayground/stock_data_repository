import re
from typing import Any

TAXONOMY_VERSION = "sic-1987-readable-v1"
TAXONOMY_SOURCE_URL = "https://www.osha.gov/data/sic-manual"

# The complete 1987 SIC division/major-group hierarchy published by OSHA.
# Prefixes are evaluated against a four-digit, zero-padded SIC code.
DIVISIONS: tuple[tuple[str, str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "division:agriculture_forestry_fishing",
        "A",
        "Agriculture, Forestry, and Fishing",
        (
            (
                "01",
                "industry:agricultural_production_crops",
                "Agricultural Production — Crops",
            ),
            (
                "02",
                "industry:agricultural_production_livestock",
                "Agricultural Production — Livestock and Animal Specialties",
            ),
            ("07", "industry:agricultural_services", "Agricultural Services"),
            ("08", "industry:forestry", "Forestry"),
            (
                "09",
                "industry:fishing_hunting_trapping",
                "Fishing, Hunting, and Trapping",
            ),
        ),
    ),
    (
        "division:mining",
        "B",
        "Mining",
        (
            ("10", "industry:metal_mining", "Metal Mining"),
            ("12", "industry:coal_mining", "Coal Mining"),
            ("13", "industry:oil_gas_extraction", "Oil and Gas Extraction"),
            (
                "14",
                "industry:nonmetallic_minerals",
                "Nonmetallic Minerals, Except Fuels",
            ),
        ),
    ),
    (
        "division:construction",
        "C",
        "Construction",
        (
            (
                "15",
                "industry:building_construction",
                "Building Construction and General Contractors",
            ),
            (
                "16",
                "industry:heavy_construction",
                "Heavy Construction Other Than Buildings",
            ),
            (
                "17",
                "industry:special_trade_contractors",
                "Construction Special Trade Contractors",
            ),
        ),
    ),
    (
        "division:manufacturing",
        "D",
        "Manufacturing",
        (
            ("20", "industry:food_products", "Food and Kindred Products"),
            ("21", "industry:tobacco_products", "Tobacco Products"),
            ("22", "industry:textile_mill_products", "Textile Mill Products"),
            ("23", "industry:apparel_products", "Apparel and Other Textile Products"),
            ("24", "industry:lumber_wood_products", "Lumber and Wood Products"),
            ("25", "industry:furniture_fixtures", "Furniture and Fixtures"),
            ("26", "industry:paper_products", "Paper and Allied Products"),
            (
                "27",
                "industry:printing_publishing",
                "Printing, Publishing, and Allied Industries",
            ),
            (
                "28",
                "industry:chemicals_allied_products",
                "Chemicals and Allied Products",
            ),
            (
                "29",
                "industry:petroleum_refining",
                "Petroleum Refining and Related Industries",
            ),
            (
                "30",
                "industry:rubber_plastics",
                "Rubber and Miscellaneous Plastics Products",
            ),
            ("31", "industry:leather_products", "Leather and Leather Products"),
            (
                "32",
                "industry:stone_clay_glass_concrete",
                "Stone, Clay, Glass, and Concrete Products",
            ),
            ("33", "industry:primary_metals", "Primary Metal Industries"),
            ("34", "industry:fabricated_metals", "Fabricated Metal Products"),
            (
                "35",
                "industry:machinery_computer_equipment",
                "Machinery and Computer Equipment",
            ),
            (
                "36",
                "industry:electronic_electrical_equipment",
                "Electronic and Electrical Equipment and Components",
            ),
            ("37", "industry:transportation_equipment", "Transportation Equipment"),
            (
                "38",
                "industry:instruments_medical_optical_goods",
                "Measuring, Medical, Optical, and Photographic Instruments",
            ),
            (
                "39",
                "industry:miscellaneous_manufacturing",
                "Miscellaneous Manufacturing Industries",
            ),
        ),
    ),
    (
        "division:transportation_communications_utilities",
        "E",
        "Transportation, Communications, and Utilities",
        (
            ("40", "industry:railroad_transportation", "Railroad Transportation"),
            (
                "41",
                "industry:passenger_ground_transportation",
                "Local and Interurban Passenger Transportation",
            ),
            (
                "42",
                "industry:motor_freight_warehousing",
                "Motor Freight Transportation and Warehousing",
            ),
            ("43", "industry:postal_service", "United States Postal Service"),
            ("44", "industry:water_transportation", "Water Transportation"),
            ("45", "industry:air_transportation", "Transportation by Air"),
            ("46", "industry:pipelines", "Pipelines, Except Natural Gas"),
            ("47", "industry:transportation_services", "Transportation Services"),
            ("48", "industry:communications", "Communications"),
            (
                "49",
                "industry:electric_gas_sanitary_services",
                "Electric, Gas, and Sanitary Services",
            ),
        ),
    ),
    (
        "division:wholesale_trade",
        "F",
        "Wholesale Trade",
        (
            (
                "50",
                "industry:wholesale_durable_goods",
                "Wholesale Trade — Durable Goods",
            ),
            (
                "51",
                "industry:wholesale_nondurable_goods",
                "Wholesale Trade — Nondurable Goods",
            ),
        ),
    ),
    (
        "division:retail_trade",
        "G",
        "Retail Trade",
        (
            (
                "52",
                "industry:building_materials_hardware",
                "Building Materials and Hardware Dealers",
            ),
            ("53", "industry:general_merchandise_stores", "General Merchandise Stores"),
            ("54", "industry:food_stores", "Food Stores"),
            (
                "55",
                "industry:auto_dealers_gas_stations",
                "Automotive Dealers and Gasoline Service Stations",
            ),
            ("56", "industry:apparel_accessory_stores", "Apparel and Accessory Stores"),
            (
                "57",
                "industry:home_furnishings_stores",
                "Home Furniture, Furnishings, and Equipment Stores",
            ),
            ("58", "industry:eating_drinking_places", "Eating and Drinking Places"),
            ("59", "industry:miscellaneous_retail", "Miscellaneous Retail"),
        ),
    ),
    (
        "division:finance_insurance_real_estate",
        "H",
        "Finance, Insurance, and Real Estate",
        (
            ("60", "industry:depository_institutions", "Depository Institutions"),
            (
                "61",
                "industry:nondepository_credit",
                "Nondepository Credit Institutions",
            ),
            (
                "62",
                "industry:securities_brokers_exchanges",
                "Securities and Commodity Brokers, Dealers, and Exchanges",
            ),
            ("63", "industry:insurance_carriers", "Insurance Carriers"),
            (
                "64",
                "industry:insurance_agents_brokers",
                "Insurance Agents, Brokers, and Services",
            ),
            ("65", "industry:real_estate", "Real Estate"),
            (
                "67",
                "industry:holding_investment_offices",
                "Holding and Other Investment Offices",
            ),
        ),
    ),
    (
        "division:services",
        "I",
        "Services",
        (
            ("70", "industry:lodging", "Hotels and Other Lodging Places"),
            ("72", "industry:personal_services", "Personal Services"),
            ("73", "industry:business_services", "Business Services"),
            (
                "75",
                "industry:automotive_repair_parking",
                "Automotive Repair, Services, and Parking",
            ),
            ("76", "industry:miscellaneous_repair", "Miscellaneous Repair Services"),
            ("78", "industry:motion_pictures", "Motion Pictures"),
            (
                "79",
                "industry:amusement_recreation",
                "Amusement and Recreation Services",
            ),
            ("80", "industry:health_services", "Health Services"),
            ("81", "industry:legal_services", "Legal Services"),
            ("82", "industry:educational_services", "Educational Services"),
            ("83", "industry:social_services", "Social Services"),
            (
                "84",
                "industry:museums_gardens_zoos",
                "Museums, Art Galleries, Botanical Gardens, and Zoos",
            ),
            ("86", "industry:membership_organizations", "Membership Organizations"),
            (
                "87",
                "industry:engineering_research_management",
                "Engineering, Research, and Management Services",
            ),
            ("88", "industry:private_households", "Private Households"),
            ("89", "industry:miscellaneous_services", "Miscellaneous Services"),
        ),
    ),
    (
        "division:public_administration",
        "J",
        "Public Administration and Unclassified",
        (
            (
                "91",
                "industry:general_government",
                "Executive, Legislative, and General Government",
            ),
            (
                "92",
                "industry:justice_public_safety",
                "Justice, Public Order, and Safety",
            ),
            (
                "93",
                "industry:public_finance",
                "Public Finance, Taxation, and Monetary Policy",
            ),
            (
                "94",
                "industry:human_resource_programs",
                "Administration of Human Resource Programs",
            ),
            (
                "95",
                "industry:environmental_housing_programs",
                "Environmental Quality and Housing Programs",
            ),
            ("96", "industry:economic_programs", "Administration of Economic Programs"),
            (
                "97",
                "industry:national_security_international_affairs",
                "National Security and International Affairs",
            ),
            ("99", "industry:nonclassifiable", "Nonclassifiable Establishments"),
        ),
    ),
)

CURATED_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "key": "curated:healthcare",
        "label": "Healthcare",
        "description": "Cross-division healthcare aggregate used by the screener default.",
        "children": (
            "curated:pharma_biotech",
            "curated:medical_devices",
            "curated:health_services",
            "curated:medical_distribution",
            "curated:health_plans",
            "curated:medical_equipment_rental",
            "curated:life_sciences_research",
        ),
    },
    {
        "key": "curated:pharma_biotech",
        "label": "Pharmaceuticals and Biotechnology",
        "description": "Medicinal chemicals, pharmaceuticals, diagnostics, and biological products.",
        "sic_prefixes": ("283",),
    },
    {
        "key": "curated:medical_devices",
        "label": "Medical Devices and Supplies",
        "description": "Surgical, orthopedic, dental, imaging, electromedical, and ophthalmic products.",
        "sic_prefixes": ("384", "385"),
    },
    {
        "key": "curated:health_services",
        "label": "Health Services",
        "description": "Physicians, hospitals, nursing facilities, laboratories, and allied health services.",
        "sic_prefixes": ("80",),
    },
    {
        "key": "curated:medical_distribution",
        "label": "Medical and Drug Distribution",
        "description": "Medical equipment and pharmaceutical wholesalers.",
        "sic_prefixes": ("5047", "5122"),
    },
    {
        "key": "curated:health_plans",
        "label": "Hospital and Medical Service Plans",
        "description": "Hospital and medical service plan operators.",
        "sic_prefixes": ("6324",),
    },
    {
        "key": "curated:medical_equipment_rental",
        "label": "Medical Equipment Rental",
        "description": "Medical equipment rental and leasing.",
        "sic_prefixes": ("7352",),
    },
    {
        "key": "curated:life_sciences_research",
        "label": "Life Sciences Research",
        "description": "Commercial physical and biological research; this rule can include non-healthcare research.",
        "sic_prefixes": ("8731",),
    },
)


def _alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _catalog_entries() -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for division_key, division_code, division_label, major_groups in DIVISIONS:
        entries[division_key] = {
            "key": division_key,
            "label": division_label,
            "level": "division",
            "sic_code": division_code,
            "sic_prefixes": tuple(group[0] for group in major_groups),
        }
        for prefix, key, label in major_groups:
            entries[key] = {
                "key": key,
                "label": label,
                "level": "major_group",
                "sic_code": prefix,
                "sic_prefixes": (prefix,),
                "parent": division_key,
            }
    curated = {group["key"]: dict(group) for group in CURATED_GROUPS}
    for group in curated.values():
        group["level"] = "curated_group"
    for group in curated.values():
        if "children" in group:
            group["sic_prefixes"] = tuple(
                prefix
                for child_key in group["children"]
                for prefix in curated[child_key]["sic_prefixes"]
            )
    entries.update(curated)
    return entries


CATALOG_ENTRIES = _catalog_entries()


def _lookup_aliases() -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for key, entry in CATALOG_ENTRIES.items():
        for value in (key, key.split(":", 1)[-1], entry["label"]):
            candidates.setdefault(_alias(value), set()).add(key)
    aliases: dict[str, str] = {}
    for alias, keys in candidates.items():
        if len(keys) == 1:
            aliases[alias] = next(iter(keys))
            continue
        prefix_sets = {tuple(CATALOG_ENTRIES[key]["sic_prefixes"]) for key in keys}
        if len(prefix_sets) == 1:
            aliases[alias] = sorted(
                keys, key=lambda key: (not key.startswith("curated:"), key)
            )[0]
    return aliases


CATALOG_ALIASES = _lookup_aliases()


def resolve_industry_groups(
    values: list[str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not values:
        return [], []
    if len(values) > 25:
        raise ValueError("at most 25 industry groups may be excluded")
    resolved_keys: list[str] = []
    unknown: list[str] = []
    for value in values:
        raw = str(value).strip()
        key = raw if raw in CATALOG_ENTRIES else CATALOG_ALIASES.get(_alias(raw))
        if key is None:
            unknown.append(raw)
        elif key not in resolved_keys:
            resolved_keys.append(key)
    if unknown:
        raise ValueError(
            "unknown industry group(s): "
            + ", ".join(unknown)
            + "; use get_industry_hierarchy for valid options"
        )
    resolved = [CATALOG_ENTRIES[key] for key in resolved_keys]
    prefixes = sorted(
        {prefix for entry in resolved for prefix in entry["sic_prefixes"]},
        key=lambda prefix: (len(prefix), prefix),
    )
    return resolved, prefixes


def industry_hierarchy() -> dict[str, Any]:
    divisions = []
    for division_key, division_code, division_label, major_groups in DIVISIONS:
        divisions.append(
            {
                "key": division_key,
                "code": division_code,
                "label": division_label,
                "children": [
                    {
                        "key": key,
                        "sic_prefix": prefix,
                        "label": label,
                    }
                    for prefix, key, label in major_groups
                ],
            }
        )
    curated_lookup = {group["key"]: group for group in CURATED_GROUPS}
    curated = []
    for group in CURATED_GROUPS:
        if "children" not in group:
            continue
        curated.append(
            {
                "key": group["key"],
                "label": group["label"],
                "description": group["description"],
                "children": [
                    {
                        "key": child_key,
                        "label": curated_lookup[child_key]["label"],
                        "description": curated_lookup[child_key]["description"],
                        "sic_prefixes": list(curated_lookup[child_key]["sic_prefixes"]),
                    }
                    for child_key in group["children"]
                ],
            }
        )
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "source": "OSHA 1987 Standard Industrial Classification Manual",
        "source_url": TAXONOMY_SOURCE_URL,
        "usage": "pass any displayed key or unambiguous label to exclude_industry_groups",
        "division_count": len(divisions),
        "major_group_count": sum(len(division[3]) for division in DIVISIONS),
        "divisions": divisions,
        "curated_groups": curated,
    }


def classify_sic(sic_code: str | None) -> dict[str, Any] | None:
    if not sic_code or not str(sic_code).strip().isdigit():
        return None
    normalized = str(sic_code).strip().zfill(4)
    major_prefix = normalized[:2]
    major_entry = next(
        (
            entry
            for entry in CATALOG_ENTRIES.values()
            if entry.get("level") == "major_group"
            and entry.get("sic_code") == major_prefix
        ),
        None,
    )
    division_entry = CATALOG_ENTRIES.get(major_entry["parent"]) if major_entry else None
    curated_matches = [
        {
            "key": group["key"],
            "label": group["label"],
            "parent_groups": [{"key": "curated:healthcare", "label": "Healthcare"}],
            "matched_prefixes": [
                prefix
                for prefix in group.get("sic_prefixes", ())
                if normalized.startswith(prefix)
            ],
        }
        for group in CURATED_GROUPS
        if "children" not in group
        and any(
            normalized.startswith(prefix) for prefix in group.get("sic_prefixes", ())
        )
    ]
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "normalized_sic_code": normalized,
        "division": (
            {"key": division_entry["key"], "label": division_entry["label"]}
            if division_entry
            else None
        ),
        "major_group": (
            {"key": major_entry["key"], "label": major_entry["label"]}
            if major_entry
            else None
        ),
        "curated_groups": curated_matches,
    }
