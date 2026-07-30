import hashlib

from app.services.sec_ingestion import _archive_sha256, _iter_company_fact_rows


def _payload(label: str, concept: str = "CustomCashCapex") -> dict:
    return {
        "cik": 1030894,
        "facts": {
            "cls": {
                concept: {
                    "label": label,
                    "description": "Registrant-defined cash capital expenditure fact.",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "filed": "2026-02-27",
                                "form": "10-K",
                                "fy": 2025,
                                "fp": "FY",
                                "accn": "0001030894-26-000011",
                                "val": 201200000,
                            }
                        ]
                    },
                }
            }
        },
    }


def test_custom_cash_capex_label_is_retained_with_original_taxonomy() -> None:
    rows = list(
        _iter_company_fact_rows(
            _payload(
                "Purchase of property, plant and equipment, net of sales proceeds"
            )
        )
    )

    assert len(rows) == 1
    assert rows[0]["taxonomy"] == "cls"
    assert rows[0]["concept"] == "CustomCashCapex"
    assert rows[0]["value"] == 201200000


def test_all_numeric_custom_facts_are_retained() -> None:
    rows = list(_iter_company_fact_rows(_payload("Capital expenditures incurred")))

    assert len(rows) == 1
    assert rows[0]["taxonomy"] == "cls"
    assert rows[0]["concept"] == "CustomCashCapex"
    assert rows[0]["label"] == "Capital expenditures incurred"


def test_all_numeric_standard_facts_are_retained() -> None:
    payload = _payload("Research and development expense", "ResearchAndDevelopmentExpense")
    payload["facts"]["us-gaap"] = payload["facts"].pop("cls")

    rows = list(_iter_company_fact_rows(payload))

    assert len(rows) == 1
    assert rows[0]["taxonomy"] == "us-gaap"
    assert rows[0]["concept"] == "ResearchAndDevelopmentExpense"


def test_companyfacts_archive_fingerprint_is_stable(tmp_path) -> None:
    archive = tmp_path / "companyfacts.zip"
    archive.write_bytes(b"company-facts-test")

    assert _archive_sha256(archive) == hashlib.sha256(
        b"company-facts-test"
    ).hexdigest()
