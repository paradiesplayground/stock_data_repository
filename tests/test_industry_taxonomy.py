import pytest

from app.industry_taxonomy import (
    classify_sic,
    industry_hierarchy,
    resolve_industry_groups,
)


def test_hierarchy_contains_every_sic_division_and_major_group() -> None:
    hierarchy = industry_hierarchy()

    assert hierarchy["taxonomy_version"] == "sic-1987-readable-v1"
    assert hierarchy["division_count"] == 10
    assert hierarchy["major_group_count"] == 83
    assert sum(len(division["children"]) for division in hierarchy["divisions"]) == 83


def test_readable_healthcare_group_resolves_to_auditable_prefixes() -> None:
    groups, prefixes = resolve_industry_groups(["Healthcare"])

    assert [group["key"] for group in groups] == ["curated:healthcare"]
    assert prefixes == [
        "80",
        "283",
        "384",
        "385",
        "5047",
        "5122",
        "6324",
        "7352",
        "8731",
    ]


def test_division_and_major_group_labels_are_accepted() -> None:
    groups, prefixes = resolve_industry_groups(
        ["Manufacturing", "Oil and Gas Extraction"]
    )

    assert [group["key"] for group in groups] == [
        "division:manufacturing",
        "industry:oil_gas_extraction",
    ]
    assert "20" in prefixes
    assert "39" in prefixes
    assert "13" in prefixes


def test_overlapping_readable_label_resolves_when_rule_is_identical() -> None:
    groups, prefixes = resolve_industry_groups(["Health Services"])

    assert groups[0]["key"] == "curated:health_services"
    assert prefixes == ["80"]


def test_sic_classification_relates_code_to_readable_hierarchy() -> None:
    result = classify_sic("2834")

    assert result is not None
    assert result["division"]["label"] == "Manufacturing"
    assert result["major_group"]["label"] == "Chemicals and Allied Products"
    assert result["curated_groups"][0]["label"] == "Pharmaceuticals and Biotechnology"


def test_unknown_industry_group_is_rejected() -> None:
    with pytest.raises(ValueError, match="get_industry_hierarchy"):
        resolve_industry_groups(["made up sector"])
