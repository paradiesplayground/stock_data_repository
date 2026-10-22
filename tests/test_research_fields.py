from datetime import date

import pytest

from app.services.research_fields import earnings_bucket, normalize_research_fields, research_summary


def test_research_fields_normalize_and_default_to_unknown() -> None:
    fields = normalize_research_fields({"next_earnings_date": "2026-11-05", "guidance_direction": "Raised"})

    assert fields["next_earnings_date"] == "2026-11-05"
    assert fields["guidance_direction"] == "raised"
    assert fields["catalyst_quality"] == "unknown"
    assert normalize_research_fields(None) is None


@pytest.mark.parametrize(
    "value",
    [{"guidance_direction": "great"}, {"next_earnings_date": "soon"}, {"surprise": "x"}, ["not", "a", "dict"]],
)
def test_research_fields_reject_untyped_values(value) -> None:
    with pytest.raises(ValueError):
        normalize_research_fields(value)


def test_earnings_bucket_and_summary() -> None:
    fields = normalize_research_fields({"next_earnings_date": "2026-10-06", "gross_margin_trend": "expanding",
                                        "insider_activity": "net_selling"})
    day = date(2026, 10, 1)

    assert earnings_bucket(fields, day) == "within_7_days"
    assert earnings_bucket(fields, date(2026, 9, 1)) == "over_30_days"
    assert earnings_bucket(fields, date(2026, 10, 9)) == "unknown"
    assert earnings_bucket(None, day) == "unknown"
    assert research_summary(fields, day) == "earnings 2026-10-06 (in 5 days); margins expanding; insiders net selling"


def test_research_fields_are_recorded_but_never_change_a_label() -> None:
    from tests.test_alert_v09 import DAY, _evaluate, _item
    from app.services.alert_v09_engine import Research
    from app.services.alert_v09_presentation import card

    fields = normalize_research_fields({"next_earnings_date": "2026-10-03", "guidance_direction": "cut"})
    with_fields, _ = _evaluate(_item(), research=Research(DAY, (), fields))
    without, _ = _evaluate(_item(), research=Research(DAY, ()))

    assert with_fields["buyability_status"] == without["buyability_status"] == "ALMOST_READY"
    assert with_fields["payload"]["research_fields"] == fields
    assert "guidance cut" in card(with_fields)["research"]


def test_research_checkpoint_normalizes_fields_before_storage(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services import daily_stock_alert_workflow as workflow

    added = []

    class Session:
        def scalar(self, _statement):
            return None

        def add(self, row):
            added.append(row)

        def commit(self):
            pass

    preparation = SimpleNamespace(snapshot={"deep_research_queue": [
        {"ticker": "MSFT", "qualitative_evidence_required": ["going_concern_and_auditor_language"]}]})
    monkeypatch.setattr(workflow, "_preparation", lambda *_args: preparation)
    workflow.record_daily_stock_alert_research(
        Session(), preparation_id="p", ticker="msft", evidence=[],
        required_dimensions={"going_concern_and_auditor_language": True},
        research_fields={"catalyst_quality": "Strong"},
    )

    assert added[0].research_fields["catalyst_quality"] == "strong"
    assert added[0].research_fields["guidance_direction"] == "unknown"
