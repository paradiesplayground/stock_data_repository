from decimal import Decimal

from app.services.sec_ingestion import (
    _iter_company_fact_rows,
    _parse_master_index,
    _recent_filing_rows,
    _upsert_filing_rows,
)


def test_company_fact_parser_keeps_source_context() -> None:
    payload = {
        "cik": 1234,
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "description": "Reported revenue",
                    "units": {
                        "USD": [
                            {
                                "val": 125000000,
                                "start": "2025-01-01",
                                "end": "2025-03-31",
                                "filed": "2025-05-01",
                                "form": "10-Q",
                                "fy": 2025,
                                "fp": "Q1",
                                "accn": "0000001234-25-000001",
                                "frame": "CY2025Q1",
                            }
                        ]
                    },
                }
            }
        },
    }

    rows = list(_iter_company_fact_rows(payload))

    assert len(rows) == 1
    assert rows[0]["cik"] == "0000001234"
    assert rows[0]["value"] == Decimal("125000000")
    assert rows[0]["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert rows[0]["form"] == "10-Q"
    assert len(rows[0]["fact_id"]) == 64


def test_submission_parser_builds_canonical_sec_url() -> None:
    payload = {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000001"],
                "filingDate": ["2025-01-31"],
                "reportDate": ["2024-12-31"],
                "acceptanceDateTime": ["2025-01-31T16:30:00.000Z"],
                "form": ["10-Q"],
                "primaryDocument": ["aapl-20241231.htm"],
                "primaryDocDescription": ["Quarterly report"],
                "fileNumber": ["001-36743"],
                "filmNumber": ["25555555"],
                "items": [""],
                "size": [1000],
                "isXBRL": [1],
                "isInlineXBRL": [1],
            }
        },
    }

    rows = list(_recent_filing_rows(payload))

    assert len(rows) == 1
    assert rows[0]["cik"] == "0000320193"
    assert rows[0]["form"] == "10-Q"
    assert rows[0]["source_url"].endswith(
        "/Archives/edgar/data/320193/000032019325000001/aapl-20241231.htm"
    )


def test_daily_master_index_collects_changed_ciks_and_forms() -> None:
    text = """Description: Daily Index\nCIK|Company Name|Form Type|Date Filed|Filename
320193|Apple Inc.|10-Q|2026-07-16|edgar/data/320193/example.txt
320193|Apple Inc.|8-K|2026-07-16|edgar/data/320193/example2.txt
1652044|Alphabet Inc.|4|2026-07-16|edgar/data/1652044/example.txt
"""

    changed = _parse_master_index(text)

    assert changed["0000320193"] == {"10-Q", "8-K"}
    assert changed["0001652044"] == {"4"}


def test_filing_upsert_treats_items_as_a_column() -> None:
    class RecordingSession:
        statement = None

        def execute(self, statement):
            self.statement = statement

        def commit(self):
            pass

    session = RecordingSession()
    _upsert_filing_rows(
        session,
        [
            {
                "accession_number": "0000320193-25-000001",
                "cik": "0000320193",
                "form": "8-K",
                "filed_date": "2025-01-31",
                "items": "2.02",
            }
        ],
    )

    sql = str(session.statement)
    assert "items = excluded.items" in sql
