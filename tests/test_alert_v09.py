import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.alert_v09_engine import EngineConfig, Research, evaluate, load_configuration
from app.services.blocker_codes import blocker_code, blocker_codes
from app.services.daily_stock_alert_shadow import _sessions_counter, email_sections, render_markdown
from app.strategy_decisions import normalize_candidate_decision
from tests.v09_golden_replay import FIXTURE, replay, section_map

CONFIGURATION = load_configuration()
CONFIG = EngineConfig.from_configuration(CONFIGURATION)
DAY = date(2026, 10, 1)
SESSIONS = [DAY + timedelta(days=offset) for offset in range(-120, 120)]
BETWEEN = _sessions_counter(SESSIONS)


def _item(**overrides):
    item = {
        "ticker": "TEST",
        "company": "Test Co",
        "close": "100",
        "price_change_12w_pct": "-25",
        "revenue_ttm_yoy_pct": "60",
        "latest_quarter_revenue_yoy_pct": "55",
        "avg_dollar_volume_20d": "80000000",
        "approximate_market_cap": "5000000000",
        "prior_high_20d": "102",
        "prior_low_20d": "92",
        "prior_atr_14": "3",
        "relative_return_20d_vs_qqq_pct": "4",
        "share_count_yoy_pct": "2",
        "cash_runway_months": None,
        "free_cash_flow_ttm": "1000000",
        "sic_code": "3674",
    }
    item.update(overrides)
    return item


def _evaluate(item, state=None, *, as_of=DAY, market=True, research=Research(DAY, ())):
    return evaluate(item, state, as_of_date=as_of, listing_ok=True, market_gate_passed=market,
                    research=research, sessions_between=BETWEEN, config=CONFIG)


def _valid(candidate):
    normalize_candidate_decision(candidate, contract_version="0.9", contract_required=True)
    return candidate


def test_close_when_within_band_below_prior_session_trigger() -> None:
    candidate, state = _evaluate(_item())

    assert _valid(candidate)["buyability_status"] == "ALMOST_READY"
    assert candidate["payload"]["section"] == "close"
    assert Decimal(candidate["trigger_price"]) == Decimal("102") * Decimal("1.001")
    # Stop = max(prior 20-day low, trigger - 2 x prior ATR) = max(92, 102.102 - 6).
    assert Decimal(candidate["invalidation_price"]) == Decimal("96.102")
    assert state["anchor_trigger"] == candidate["trigger_price"]


def test_buy_when_close_crosses_prior_session_high() -> None:
    candidate, _ = _evaluate(_item(close="104"))

    assert _valid(candidate)["buyability_status"] == "BUY_NOW"
    assert candidate["technical_state"] == "confirmed"
    assert candidate["remaining_gate_count"] == 0


def test_buy_window_closes_after_three_sessions_above_trigger() -> None:
    state = None
    statuses = []
    for offset in range(5):
        candidate, state = _evaluate(_item(close="104"), state, as_of=DAY + timedelta(days=offset))
        statuses.append(candidate["buyability_status"])

    assert statuses == ["BUY_NOW", "BUY_NOW", "BUY_NOW", "RADAR", "RADAR"]
    assert candidate["technical_state"] == "extended"


@pytest.mark.parametrize(
    ("overrides", "market", "research", "reason"),
    [
        ({"relative_return_20d_vs_qqq_pct": "-1"}, True, Research(DAY, ()), "beat QQQ"),
        ({}, False, Research(DAY, ()), "Market gate"),
        ({}, True, None, "research"),
        ({}, True, Research(DAY - timedelta(days=40), ()), "research"),
    ],
)
def test_failing_gate_keeps_a_close_setup_on_recovering(overrides, market, research, reason) -> None:
    candidate, _ = _evaluate(_item(**overrides), market=market, research=research)

    assert _valid(candidate)["buyability_status"] == "RADAR"
    assert any(reason in condition for condition in candidate["buy_conditions"])


def test_recovering_out_of_the_screen_keeps_eligibility() -> None:
    _, state = _evaluate(_item())
    candidate, _ = _evaluate(_item(price_change_12w_pct="-5", close="101"), state, as_of=DAY + timedelta(days=1))

    assert candidate["screen_bucket"] == "tracked_recovery"
    assert _valid(candidate)["buyability_status"] == "ALMOST_READY"


def test_not_screened_and_not_tracked_is_ignored() -> None:
    assert _evaluate(_item(price_change_12w_pct="-5")) == (None, None)


def test_broken_setup_clears_the_anchor_and_never_shows_a_stop_above_price() -> None:
    _, state = _evaluate(_item())
    candidate, state = _evaluate(_item(close="88", prior_high_20d="103", prior_low_20d="88"), state,
                                 as_of=DAY + timedelta(days=1))

    assert candidate["technical_state"] == "broken"
    assert _valid(candidate)["buyability_status"] == "RADAR"
    assert state["anchor_trigger"] is None


def test_risk_flag_blocks_then_clears_when_it_no_longer_applies() -> None:
    blocked, state = _evaluate(_item(share_count_yoy_pct="20"))
    cleared, _ = _evaluate(_item(share_count_yoy_pct="3"), state, as_of=DAY + timedelta(days=1))

    assert _valid(blocked)["buyability_status"] == "NOT_ELIGIBLE"
    assert blocked["screen_bucket"] == "blocked"
    assert "share_count_growth_at_or_above_limit" in blocked["blocker_codes"]
    assert _valid(cleared)["buyability_status"] == "ALMOST_READY"


def test_research_blocker_code_vetoes_and_expires_with_the_research() -> None:
    blocked, state = _evaluate(_item(), research=Research(DAY, ("dilution_material",)))
    later = DAY + timedelta(days=31)
    stale, _ = _evaluate(_item(), state, as_of=later, research=Research(DAY, ("dilution_material",)))

    assert blocked["buyability_status"] == "NOT_ELIGIBLE"
    assert stale["buyability_status"] == "RADAR"
    assert stale["payload"]["needs_research"] is True


def test_exit_after_failing_base_gates_and_reentry_restarts_tracking() -> None:
    _, state = _evaluate(_item())
    exited, state = _evaluate(_item(revenue_ttm_yoy_pct="10"), state, as_of=DAY + timedelta(days=1))
    back, state = _evaluate(_item(), state, as_of=DAY + timedelta(days=2))

    assert _valid(exited)["screen_bucket"] == "exited"
    assert back["buyability_status"] == "ALMOST_READY"
    assert state["first_screen_date"] == (DAY + timedelta(days=2)).isoformat()


def test_exit_after_too_long_outside_the_screen() -> None:
    _, state = _evaluate(_item())
    later = DAY + timedelta(days=CONFIG.max_sessions_outside_screen + 1)
    exited, state = _evaluate(_item(price_change_12w_pct="-5"), state, as_of=later)

    assert exited["screen_bucket"] == "exited"
    assert state["exited"] is True


def test_exit_without_progress_toward_the_trigger() -> None:
    _, state = _evaluate(_item())
    later = DAY + timedelta(days=CONFIG.no_progress_sessions + 1)
    far = _item(price_change_12w_pct="-5", close="80", prior_high_20d="100", prior_low_20d="79")
    exited, _ = _evaluate(far, state, as_of=later)

    assert exited["screen_bucket"] == "exited"
    assert "no progress" in exited["payload"]["exit_reason"]


def test_blocker_codes_map_saved_free_text() -> None:
    assert blocker_code("material_share_dilution") == "dilution_material"
    assert blocker_code("large_atm_and_convertible_dilution_exposure") == "dilution_overhang_atm_convert"
    assert blocker_code("negative_free_cash_flow") == "cash_burn_runway"
    assert blocker_code("material china customer and receivables concentration") == "customer_concentration"
    assert blocker_code("very_high_debt_and_financing_dependence") == "debt_refinancing"
    assert blocker_code("bitcoin_collateral_and_price_exposure") == "other_fundamental"
    assert blocker_codes(["negative_relative_strength", "market_regime_block", "weak_chart_structure"]) == []
    assert blocker_code("going_concern") == "going_concern"


def test_email_sections_apply_caps_and_collapse_unchanged_names() -> None:
    candidates = []
    for index in range(7):
        candidate, _ = _evaluate(_item(ticker=f"T{index}", close=str(100 + index * Decimal("0.1"))))
        candidate["payload"]["unchanged"] = index == 6
        candidates.append(candidate)
    sections = email_sections(candidates, CONFIGURATION)

    assert len(sections["close"]["shown"]) == CONFIGURATION["email"]["max_close"]
    assert sections["close"]["more_on_website"] == 1
    assert sections["close"]["unchanged"] == ["T6"]
    markdown = render_markdown(DAY, True, candidates, sections)
    assert "SHADOW" in markdown
    assert len(markdown) < CONFIGURATION["email"]["target_max_chars"]


def test_golden_replay_matches_expected_sections_and_contract() -> None:
    output = replay()
    expected = json.loads((FIXTURE / "expected_sections.json").read_text(encoding="utf-8"))

    assert section_map(output, date(2026, 9, 28)) == expected
    for candidates in output.values():
        for candidate in candidates:
            _valid(candidate)


def test_golden_replay_keeps_recovering_names_and_honours_the_market_gate() -> None:
    output = replay()
    window = {market_date: {item["ticker"]: item for item in candidates}
              for market_date, candidates in output.items() if market_date >= date(2026, 9, 30)}

    for tickers in window.values():
        # Recovering names that v0.8 dropped from its emails stay visible every day.
        assert {"CRDO", "KLIC", "APH", "ALAB", "MXL"} <= set(tickers)
    for market_date in (date(2026, 9, 30), date(2026, 10, 16), date(2026, 10, 19)):
        assert not [item for item in window[market_date].values()
                    if item["buyability_status"] in {"BUY_NOW", "ALMOST_READY"}]
    actionable = [item for tickers in window.values() for item in tickers.values()
                  if item["buyability_status"] in {"BUY_NOW", "ALMOST_READY"}]
    assert actionable
    assert all(Decimal(item["invalidation_price"]) < Decimal(item["current_price"]) for item in actionable)
    final = window[date(2026, 10, 20)]
    assert final["APP"]["buyability_status"] == "RADAR"
    assert final["STRL"]["buyability_status"] == "RADAR"


class _NoDefinitionSession:
    def scalar(self, _statement):
        return None


def test_shadow_run_payload_validates_as_contract_v09_and_as_run_still_requires_v08() -> None:
    from app.services.daily_stock_alert_shadow import CONFIGURATION as SHADOW_CONFIGURATION
    from app.services.strategy_tracking import record_strategy_run

    candidates = replay()[date(2026, 10, 2)]
    arguments = dict(
        strategy_key="dynamic_swing_buy_alerts_shadow", strategy_version="0.9", as_of_date="2026-10-02",
        idempotency_key="test", configuration=SHADOW_CONFIGURATION, filters={}, candidates=candidates,
        decision_contract_version="0.9", publish=False, validate_only=True,
    )
    result = record_strategy_run(_NoDefinitionSession(), run_type="shadow", **arguments)

    assert result["status"] == "valid"
    assert result["candidate_count"] == len(candidates)
    with pytest.raises(ValueError, match="as_run alerts require decision_contract_version=0.8"):
        record_strategy_run(_NoDefinitionSession(), run_type="as_run", **arguments)


def test_v09_is_not_deliverable_yet() -> None:
    from app.decision_contract import SUPPORTED_DECISION_CONTRACT_VERSIONS

    assert "0.9" not in SUPPORTED_DECISION_CONTRACT_VERSIONS
