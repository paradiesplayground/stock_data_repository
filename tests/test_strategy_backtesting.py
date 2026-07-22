import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.strategy_simulation as strategy_simulation
from app.services.strategy_config import with_nested_overrides
from app.services.strategy_replay import replay_configuration, score_feature
from app.services.strategy_scenarios import resolve_strategy_scenario
from app.services.strategy_simulation import (
    Bar,
    EquityPoint,
    Signal,
    SimulationParameters,
    SimulationResult,
    run_simulation,
    simulate_signals,
)

D = Decimal


def _feature(**overrides):
    values = {
        "ticker": "TEST",
        "reference_primary_exchange": "XNAS",
        "reference_security_type": "CS",
        "reference_active": True,
        "reference_sic_code": "3571",
        "close": D("9.90"),
        "approximate_market_cap": D("1500000000"),
        "revenue_ttm_yoy_pct": D("100"),
        "latest_quarter_revenue_yoy_pct": D("100"),
        "price_change_12w_pct": D("-30"),
        "drawdown_52w_pct": D("-40"),
        "avg_dollar_volume_20d": D("120000000"),
        "cash_runway_months": D("30"),
        "share_count_yoy_pct": D("5"),
        "total_debt": D("10"),
        "cash_and_short_term_investments": D("100"),
        "free_cash_flow_ttm": D("1"),
        "ema_10": D("9.50"),
        "ema_20": D("9.25"),
        "low_20d": D("8"),
        "low_60d": D("7"),
        "high_20d": D("10"),
        "distance_to_20d_high_pct": D("-1"),
        "atr_14": D("1"),
        "rsi_14": D("60"),
        "relative_return_20d_vs_qqq_pct": D("5"),
        "relative_volume_20d": D("2"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _signal() -> Signal:
    return Signal(
        source_run_id="run-1",
        ticker="TEST",
        signal_date=date(2026, 1, 20),
        score=D("70"),
        trigger=D("10"),
        maximum_entry=D("12"),
        stop=D("9"),
        target_one=D("12"),
        target_two=D("13"),
        risk_multiplier=D("1"),
    )


def _bars(*rows):
    output = {}
    for market_date, open_, high, low, close in rows:
        output.setdefault(market_date, {})["TEST"] = Bar(
            market_date=market_date,
            ticker="TEST",
            open=D(open_),
            high=D(high),
            low=D(low),
            close=D(close),
        )
    return output


def test_mechanical_replay_scores_only_known_point_in_time_fields() -> None:
    candidate = score_feature(
        _feature(), constructive_volume=True, excluded_sic_prefixes=["28", "80"]
    )

    assert candidate is not None
    assert candidate["score"] == 74
    assert candidate["action"] == "actionable"
    assert candidate["trade_plan"]["risk_multiplier"] == "1"
    assert candidate["payload"]["qualitative_review_performed"] is False


def test_unknown_sic_is_never_actionable() -> None:
    candidate = score_feature(
        _feature(reference_sic_code=None),
        constructive_volume=True,
        excluded_sic_prefixes=["28", "80"],
    )

    assert candidate["stage"] == "incomplete"
    assert candidate["action"] == "keep-watching"
    assert "unknown_sic_exclusion_status" in candidate["reasons"]


def test_positive_free_cash_flow_does_not_require_cash_runway_value() -> None:
    candidate = score_feature(
        _feature(cash_runway_months=None),
        constructive_volume=True,
        excluded_sic_prefixes=["28", "80"],
    )

    assert candidate["action"] == "actionable"
    assert candidate["metrics"]["positive_free_cash_flow"] is True
    assert "missing_cash_runway" not in candidate["reasons"]


def test_healthcare_candidate_is_excluded() -> None:
    assert (
        score_feature(
            _feature(reference_sic_code="2834"),
            constructive_volume=True,
            excluded_sic_prefixes=["28", "80"],
        )
        is None
    )


def test_strategy_threshold_changes_are_loaded_from_config(tmp_path) -> None:
    configuration = replay_configuration()
    configuration.pop("configuration_fingerprint")
    configuration["scoring"]["actionable"]["minimum_total_score"] = 80
    configuration["universe"]["exclude_industry_groups"] = ["Healthcare"]
    configuration["universe"].pop("excluded_sic_prefixes")
    configuration["universe"].pop("industry_taxonomy_version")
    path = tmp_path / "strict.json"
    path.write_text(json.dumps(configuration), encoding="utf-8")

    loaded = replay_configuration(str(path))
    candidate = score_feature(
        _feature(),
        constructive_volume=True,
        excluded_sic_prefixes=loaded["universe"]["excluded_sic_prefixes"],
        configuration=loaded,
    )

    assert candidate["score"] == 74
    assert candidate["action"] == "keep-watching"


def test_scenario_resolves_nested_config_and_simulation_overrides() -> None:
    resolved = resolve_strategy_scenario(
        "fallen-growth-swing-v1.1.0.json",
        "1.1.2",
        {
            "hard_thresholds": {
                "minimum_ttm_revenue_growth_pct": "20",
                "maximum_price_change_12w_pct": "-10",
            },
            "scoring": {"actionable": {"minimum_total_score": 50}},
        },
        {"risk_per_trade_pct": "2", "max_open_positions": 4},
    )

    strategy = resolved["strategy_configuration"]
    simulation = resolved["simulation_configuration"]
    assert strategy["strategy"]["version"] == "1.1.2"
    assert strategy["hard_thresholds"]["minimum_ttm_revenue_growth_pct"] == "20"
    assert strategy["scoring"]["actionable"]["minimum_total_score"] == 50
    assert simulation["risk_per_trade_pct"] == "2"
    assert simulation["max_open_positions"] == 4


def test_scenario_overrides_reject_unknown_settings() -> None:
    with pytest.raises(ValueError, match="hard_thresholds.typo_threshold"):
        with_nested_overrides(
            replay_configuration(),
            {"hard_thresholds": {"typo_threshold": "20"}},
        )


def test_variable_account_and_risk_change_position_size() -> None:
    sessions = [date(2026, 1, 20), date(2026, 1, 21)]
    bars = _bars((sessions[1], "10", "10.50", "9.50", "10.20"))
    signals = {sessions[0]: [_signal()]}

    small = simulate_signals(
        sessions,
        bars,
        signals,
        SimulationParameters(
            starting_capital=D("10000"),
            risk_per_trade_pct=D("1"),
            max_total_risk_pct=D("3"),
            slippage_pct=D("0"),
        ),
    )
    large = simulate_signals(
        sessions,
        bars,
        signals,
        SimulationParameters(
            starting_capital=D("25000"),
            risk_per_trade_pct=D("3"),
            max_total_risk_pct=D("6"),
            slippage_pct=D("0"),
        ),
    )

    assert small.trades[0].initial_shares == 100
    assert large.trades[0].initial_shares == 750


def test_simulation_parameters_load_profile_with_cli_style_override(tmp_path) -> None:
    profile = {
        "schema_version": 1,
        "scenario_name": "conservative",
        "starting_capital": "20000",
        "risk_per_trade_pct": "1",
        "max_total_risk_pct": "3",
        "max_open_positions": 3,
        "slippage_pct": "0.05",
        "order_lifetime_sessions": 5,
        "max_holding_sessions": 20,
        "execution_rules": {"same_bar_assumption": "stop_before_targets"},
    }
    path = tmp_path / "simulation.json"
    path.write_text(json.dumps(profile), encoding="utf-8")

    parameters = SimulationParameters.from_configuration(
        str(path), risk_per_trade_pct=D("2")
    )

    assert parameters.starting_capital == D("20000")
    assert parameters.risk_per_trade_pct == D("2")
    assert parameters.order_lifetime_sessions == 5
    assert parameters.payload()["scenario_name"] == "conservative"


def test_stop_wins_when_stop_and_targets_share_fill_day_bar() -> None:
    sessions = [date(2026, 1, 20), date(2026, 1, 21)]
    result = simulate_signals(
        sessions,
        _bars((sessions[1], "10", "14", "8.50", "13")),
        {sessions[0]: [_signal()]},
        SimulationParameters(slippage_pct=D("0")),
    )

    trade = result.trades[0]
    assert trade.status == "closed"
    assert trade.exit_reason == "stop"
    assert trade.realized_pnl < 0


def test_gap_above_maximum_entry_is_rejected() -> None:
    sessions = [date(2026, 1, 20), date(2026, 1, 21)]
    result = simulate_signals(
        sessions,
        _bars((sessions[1], "12.01", "13", "12", "12.50")),
        {sessions[0]: [_signal()]},
        SimulationParameters(slippage_pct=D("0")),
    )

    assert result.trades[0].status == "gap_rejected"
    assert result.trades[0].entry_date is None


def test_target_one_sells_half_and_moves_stop_to_entry() -> None:
    sessions = [
        date(2026, 1, 20),
        date(2026, 1, 21),
        date(2026, 1, 22),
    ]
    result = simulate_signals(
        sessions,
        _bars(
            (sessions[1], "10", "12.50", "9.50", "12"),
            (sessions[2], "10.50", "11", "9.90", "10"),
        ),
        {sessions[0]: [_signal()]},
        SimulationParameters(slippage_pct=D("0")),
    )

    trade = result.trades[0]
    assert trade.status == "closed"
    assert trade.target_one_hit is True
    assert trade.exit_reason == "stop"
    assert [fill["reason"] for fill in trade.fills] == [
        "triggered",
        "target_one",
        "stop",
    ]
    assert trade.realized_pnl > 0


def test_invalid_risk_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be below"):
        SimulationParameters(
            risk_per_trade_pct=D("3"), max_total_risk_pct=D("2")
        ).validate()


def test_simulation_parent_is_flushed_before_dependent_rows(monkeypatch) -> None:
    events = []

    class RecordingSession:
        def scalar(self, _statement):
            return None

        def add(self, row):
            events.append(("add", type(row).__name__))

        def flush(self):
            events.append(("flush", None))

        def commit(self):
            events.append(("commit", None))

    market_date = date(2026, 1, 20)
    configuration = replay_configuration()
    monkeypatch.setattr(
        strategy_simulation,
        "replay_configuration",
        lambda _path=None: configuration,
    )
    monkeypatch.setattr(
        strategy_simulation,
        "_load_signals",
        lambda *_args: ([], [], SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(
        strategy_simulation,
        "_load_bars",
        lambda *_args: ([market_date], {}),
    )
    monkeypatch.setattr(
        strategy_simulation,
        "simulate_signals",
        lambda *_args: SimulationResult(
            trades=[],
            equity_points=[
                EquityPoint(
                    market_date=market_date,
                    cash=D("10000"),
                    equity=D("10000"),
                    drawdown_pct=D("0"),
                    open_positions=0,
                    planned_open_risk=D("0"),
                )
            ],
            summary={},
        ),
    )

    run_simulation(
        RecordingSession(),
        market_date,
        market_date,
        SimulationParameters(),
    )

    assert events == [
        ("add", "StrategySimulationRun"),
        ("flush", None),
        ("add", "StrategySimulationEquityPoint"),
        ("commit", None),
    ]
