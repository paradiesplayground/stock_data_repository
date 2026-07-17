import hashlib
import json
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from dateutil.parser import isoparse
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Filing, FinancialFact, Security
from app.providers.sec import SecClient
from app.services.runs import RunTracker

logger = logging.getLogger(__name__)

# Canonical reported facts needed by downstream analytics. These are stored as
# source facts; this service does not calculate growth, liquidity, or scores.
DEFAULT_FACT_CONCEPTS = {
    "AccountsPayableCurrent",
    "Assets",
    "AssetsCurrent",
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CommonStockSharesOutstanding",
    "CostOfRevenue",
    "GrossProfit",
    "Liabilities",
    "LiabilitiesCurrent",
    "LongTermDebt",
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "NetCashProvidedByUsedInOperatingActivities",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "ShortTermInvestments",
    "StockholdersEquity",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "EntityCommonStockSharesOutstanding",
}

FINANCIAL_FORMS = ("10-K", "10-Q", "20-F", "40-F", "6-K", "8-K")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = isoparse(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _fact_id(cik: str, taxonomy: str, concept: str, unit: str, fact: dict[str, Any]) -> str:
    identity = "|".join(
        str(value or "")
        for value in (
            cik,
            taxonomy,
            concept,
            unit,
            fact.get("accn"),
            fact.get("start"),
            fact.get("end"),
            fact.get("frame"),
            fact.get("val"),
        )
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _known_ciks(session: Session) -> set[str]:
    return {row[0] for row in session.execute(select(Security.cik).where(Security.cik.is_not(None))).all()}


def _iter_company_fact_rows(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    cik = str(payload.get("cik", "")).zfill(10)
    for taxonomy, concepts in payload.get("facts", {}).items():
        if taxonomy not in {"us-gaap", "ifrs-full", "dei"}:
            continue
        for concept, metadata in concepts.items():
            if concept not in DEFAULT_FACT_CONCEPTS:
                continue
            for unit, facts in metadata.get("units", {}).items():
                for fact in facts:
                    period_end = _parse_date(fact.get("end"))
                    try:
                        value = Decimal(str(fact.get("val")))
                    except (InvalidOperation, TypeError, ValueError):
                        continue
                    if period_end is None:
                        continue
                    yield {
                        "fact_id": _fact_id(cik, taxonomy, concept, unit, fact),
                        "cik": cik,
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "label": metadata.get("label"),
                        "description": metadata.get("description"),
                        "unit": unit,
                        "value": value,
                        "period_start": _parse_date(fact.get("start")),
                        "period_end": period_end,
                        "filed_date": _parse_date(fact.get("filed")),
                        "form": fact.get("form"),
                        "fiscal_year": fact.get("fy"),
                        "fiscal_period": fact.get("fp"),
                        "frame": fact.get("frame"),
                        "accession_number": fact.get("accn"),
                        "source": "sec-edgar",
                    }


def _upsert_fact_rows(session: Session, rows: list[dict[str, Any]]) -> int:
    rows = list({str(row["fact_id"]): row for row in rows}.values())
    written = 0
    for start in range(0, len(rows), 1000):
        batch = rows[start : start + 1000]
        if not batch:
            continue
        statement = insert(FinancialFact).values(batch)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[FinancialFact.fact_id],
            set_={
                "value": excluded.value,
                "filed_date": excluded.filed_date,
                "form": excluded.form,
                "fiscal_year": excluded.fiscal_year,
                "fiscal_period": excluded.fiscal_period,
                "frame": excluded.frame,
                "label": excluded.label,
                "description": excluded.description,
            },
        )
        session.execute(statement)
        session.commit()
        written += len(batch)
    return written


def sync_companyfacts(session: Session, settings: Settings) -> tuple[int, int]:
    tracker = RunTracker(session, "sec_companyfacts", "sec-edgar")
    archive = settings.raw_dir / "sec" / "companyfacts.zip"
    seen = written = 0
    try:
        with SecClient(settings) as client:
            client.download_companyfacts(archive)
        allowed_ciks = _known_ciks(session)
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                if not member.filename.endswith(".json"):
                    continue
                cik = Path(member.filename).stem.removeprefix("CIK").zfill(10)
                if cik not in allowed_ciks:
                    continue
                with source.open(member) as handle:
                    payload = json.load(handle)
                rows = list(_iter_company_fact_rows(payload))
                seen += len(rows)
                written += _upsert_fact_rows(session, rows)
        if not settings.sec_keep_archives:
            archive.unlink(missing_ok=True)
        tracker.succeed(seen, written, {"companies_matched": len(allowed_ciks)})
        return seen, written
    except Exception as error:
        tracker.fail(error, seen, written)
        raise


def _recent_filing_rows(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    cik = str(payload.get("cik", "")).zfill(10)
    recent = payload.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    for index, accession in enumerate(accessions):
        def value(key: str) -> Any:
            values = recent.get(key, [])
            return values[index] if index < len(values) else None

        filed_date = _parse_date(value("filingDate"))
        form = value("form")
        if not accession or not filed_date or not form:
            continue
        primary_document = value("primaryDocument")
        accession_path = str(accession).replace("-", "")
        source_url = None
        if primary_document:
            source_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_path}/{primary_document}"
            )
        yield {
            "accession_number": accession,
            "cik": cik,
            "form": form,
            "filed_date": filed_date,
            "report_date": _parse_date(value("reportDate")),
            "accepted_at": _parse_datetime(value("acceptanceDateTime")),
            "primary_document": primary_document,
            "primary_doc_description": value("primaryDocDescription"),
            "file_number": value("fileNumber"),
            "film_number": value("filmNumber"),
            "items": value("items"),
            "size_bytes": value("size"),
            "is_xbrl": bool(value("isXBRL")) if value("isXBRL") is not None else None,
            "is_inline_xbrl": bool(value("isInlineXBRL")) if value("isInlineXBRL") is not None else None,
            "source_url": source_url,
        }


def _update_security_metadata(session: Session, cik: str, payload: dict[str, Any]) -> None:
    session.execute(
        update(Security)
        .where(Security.cik == cik)
        .values(
            name=payload.get("name"),
            sic_code=str(payload.get("sic")) if payload.get("sic") else None,
            sic_description=payload.get("sicDescription"),
            fiscal_year_end=payload.get("fiscalYearEnd"),
            state_of_incorporation=payload.get("stateOfIncorporation"),
        )
    )


def _upsert_filing_rows(session: Session, rows: list[dict[str, Any]]) -> int:
    rows = list({str(row["accession_number"]): row for row in rows}.values())
    written = 0
    for start in range(0, len(rows), 1000):
        batch = rows[start : start + 1000]
        if not batch:
            continue
        statement = insert(Filing).values(batch)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[Filing.accession_number],
            set_={
                "form": excluded.form,
                "filed_date": excluded.filed_date,
                "report_date": excluded.report_date,
                "accepted_at": excluded.accepted_at,
                "primary_document": excluded.primary_document,
                "primary_doc_description": excluded.primary_doc_description,
                # ColumnCollection.items is a mapping method, so this column
                # must use key access rather than attribute access.
                "items": excluded["items"],
                "source_url": excluded.source_url,
            },
        )
        session.execute(statement)
        session.commit()
        written += len(batch)
    return written


def sync_submissions(session: Session, settings: Settings) -> tuple[int, int]:
    tracker = RunTracker(session, "sec_submissions", "sec-edgar")
    archive = settings.raw_dir / "sec" / "submissions.zip"
    seen = written = 0
    try:
        with SecClient(settings) as client:
            client.download_submissions(archive)
        allowed_ciks = _known_ciks(session)
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                name = Path(member.filename).name
                if not name.startswith("CIK") or not name.endswith(".json"):
                    continue
                cik = Path(name).stem.removeprefix("CIK").zfill(10)
                if cik not in allowed_ciks:
                    continue
                with source.open(member) as handle:
                    payload = json.load(handle)
                    rows = list(_recent_filing_rows(payload))
                _update_security_metadata(session, cik, payload)
                seen += len(rows)
                written += _upsert_filing_rows(session, rows)
        if not settings.sec_keep_archives:
            archive.unlink(missing_ok=True)
        tracker.succeed(seen, written, {"companies_matched": len(allowed_ciks)})
        return seen, written
    except Exception as error:
        tracker.fail(error, seen, written)
        raise


def sync_sec_all(session: Session, settings: Settings) -> dict[str, tuple[int, int]]:
    return {
        "companyfacts": sync_companyfacts(session, settings),
        "submissions": sync_submissions(session, settings),
    }


def _parse_master_index(text: str) -> dict[str, set[str]]:
    changed: dict[str, set[str]] = {}
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip().isdigit():
            continue
        cik = parts[0].strip().zfill(10)
        form = parts[2].strip()
        changed.setdefault(cik, set()).add(form)
    return changed


def sync_sec_incremental(session: Session, settings: Settings) -> tuple[int, int]:
    """Refresh only SEC filers appearing in recent daily master indexes."""
    tracker = RunTracker(session, "sec_incremental", "sec-edgar")
    seen = written = 0
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=settings.sec_incremental_lookback_days - 1)
    allowed_ciks = _known_ciks(session)
    changed: dict[str, set[str]] = {}
    try:
        with SecClient(settings) as client:
            current = start_date
            while current <= end_date:
                index_text = client.get_daily_master_index(current)
                if index_text:
                    for cik, forms in _parse_master_index(index_text).items():
                        if cik in allowed_ciks:
                            changed.setdefault(cik, set()).update(forms)
                current += timedelta(days=1)

            for position, (cik, forms) in enumerate(sorted(changed.items()), start=1):
                submissions = client.get_submissions(cik)
                if submissions:
                    _update_security_metadata(session, cik, submissions)
                    filing_rows = list(_recent_filing_rows(submissions))
                    seen += len(filing_rows)
                    written += _upsert_filing_rows(session, filing_rows)

                if any(form.startswith(FINANCIAL_FORMS) for form in forms):
                    companyfacts = client.get_companyfacts(cik)
                    if companyfacts:
                        fact_rows = list(_iter_company_fact_rows(companyfacts))
                        seen += len(fact_rows)
                        written += _upsert_fact_rows(session, fact_rows)

                if position % 100 == 0:
                    logger.info("SEC incremental processed %s/%s changed CIKs", position, len(changed))

        tracker.succeed(
            seen,
            written,
            {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "changed_ciks": len(changed),
            },
        )
        return seen, written
    except Exception as error:
        tracker.fail(error, seen, written)
        raise
