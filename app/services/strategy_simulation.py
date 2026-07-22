import hashlib
import json
import math
import uuid
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    DailyPriceBar,
    StrategyCandidate,
    StrategyDefinition,
    StrategyRun,
    StrategySimulationEquityPoint,
    StrategySimulationRun,
    StrategySimulationTrade,
)
from app.services.strategy_replay import replay_configuration
from app.services.strategy_config import (
    load_simulation_configuration,
    validate_simulation_configuration,
    with_overrides,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
_DEFAULT_SIMULATION = load_simulation_configuration()


@dataclass(frozen=True)
class SimulationParameters:
    starting_capital: Decimal = Decimal(str(_DEFAULT_SIMULATION["starting_capital"]))
    risk_per_trade_pct: Decimal = Decimal(
        str(_DEFAULT_SIMULATION["risk_per_trade_pct"])
    )
    max_total_risk_pct: Decimal = Decimal(
        str(_DEFAULT_SIMULATION["max_total_risk_pct"])
    )
    max_open_positions: int = int(_DEFAULT_SIMULATION["max_open_positions"])
    slippage_pct: Decimal = Decimal(str(_DEFAULT_SIMULATION["slippage_pct"]))
    order_lifetime_sessions: int = int(
        _DEFAULT_SIMULATION["order_lifetime_sessions"]
    )
    max_holding_sessions: int = int(_DEFAULT_SIMULATION["max_holding_sessions"])
    scenario_name: str = str(_DEFAULT_SIMULATION.get("scenario_name", "default"))
    execution_rules: dict[str, Any] = field(
        default_factory=lambda: dict(_DEFAULT_SIMULATION["execution_rules"])
    )

    @classmethod
    def from_configuration(
        cls,
        path: str | None = None,
        **overrides: Any,
    ) -> "SimulationParameters":
        values = with_overrides(load_simulation_configuration(path), overrides)
        return cls(
            starting_capital=Decimal(str(values["starting_capital"])),
            risk_per_trade_pct=Decimal(str(values["risk_per_trade_pct"])),
            max_total_risk_pct=Decimal(str(values["max_total_risk_pct"])),
            max_open_positions=int(values["max_open_positions"]),
            slippage_pct=Decimal(str(values["slippage_pct"])),
            order_lifetime_sessions=int(values["order_lifetime_sessions"]),
            max_holding_sessions=int(values["max_holding_sessions"]),
            scenario_name=str(values.get("scenario_name", "unnamed")),
            execution_rules=dict(values["execution_rules"]),
        )

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        **overrides: Any,
    ) -> "SimulationParameters":
        values = with_overrides(
            validate_simulation_configuration(payload), overrides
        )
        return cls(
            starting_capital=Decimal(str(values["starting_capital"])),
            risk_per_trade_pct=Decimal(str(values["risk_per_trade_pct"])),
            max_total_risk_pct=Decimal(str(values["max_total_risk_pct"])),
            max_open_positions=int(values["max_open_positions"]),
            slippage_pct=Decimal(str(values["slippage_pct"])),
            order_lifetime_sessions=int(values["order_lifetime_sessions"]),
            max_holding_sessions=int(values["max_holding_sessions"]),
            scenario_name=str(values.get("scenario_name", "unnamed")),
            execution_rules=dict(values["execution_rules"]),
        )

    def validate(self) -> None:
        if self.starting_capital <= 0:
            raise ValueError("starting capital must be positive")
        if not ZERO < self.risk_per_trade_pct <= HUNDRED:
            raise ValueError("risk per trade must be greater than 0 and at most 100")
        if not ZERO < self.max_total_risk_pct <= HUNDRED:
            raise ValueError(
                "maximum total risk must be greater than 0 and at most 100"
            )
        if self.max_total_risk_pct < self.risk_per_trade_pct:
            raise ValueError("maximum total risk cannot be below per-trade risk")
        if self.max_open_positions < 1:
            raise ValueError("maximum open positions must be at least 1")
        if not ZERO <= self.slippage_pct <= Decimal("10"):
            raise ValueError("slippage must be between 0 and 10 percent")
        if self.order_lifetime_sessions < 1:
            raise ValueError("order lifetime must be at least 1 session")
        if self.max_holding_sessions < 1:
            raise ValueError("maximum holding period must be at least 1 session")

    def payload(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "starting_capital": str(self.starting_capital),
            "risk_per_trade_pct": str(self.risk_per_trade_pct),
            "max_total_risk_pct": str(self.max_total_risk_pct),
            "max_open_positions": self.max_open_positions,
            "slippage_pct_each_side": str(self.slippage_pct),
            "order_lifetime_sessions": self.order_lifetime_sessions,
            "max_holding_sessions": self.max_holding_sessions,
            "execution_rules": self.execution_rules,
        }


@dataclass(frozen=True)
class Bar:
    market_date: date
    ticker: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class Signal:
    source_run_id: str
    ticker: str
    signal_date: date
    score: Decimal
    trigger: Decimal
    maximum_entry: Decimal
    stop: Decimal
    target_one: Decimal
    target_two: Decimal
    risk_multiplier: Decimal


@dataclass
class Trade:
    signal: Signal
    status: str = "pending"
    order_expiration_date: date | None = None
    entry_date: date | None = None
    exit_date: date | None = None
    initial_shares: int | None = None
    remaining_shares: int | None = None
    entry_price: Decimal | None = None
    current_stop: Decimal | None = None
    planned_risk: Decimal | None = None
    entry_session_index: int | None = None
    target_one_hit: bool = False
    proceeds: Decimal = ZERO
    realized_cost_basis: Decimal = ZERO
    fills: list[dict[str, Any]] = field(default_factory=list)
    exit_reason: str | None = None
    holding_sessions: int | None = None
    regime_blocked_sessions: int = 0

    @property
    def realized_pnl(self) -> Decimal:
        return self.proceeds - self.realized_cost_basis

    @property
    def realized_r(self) -> Decimal | None:
        if not self.planned_risk:
            return None
        return self.realized_pnl / self.planned_risk


@dataclass(frozen=True)
class EquityPoint:
    market_date: date
    cash: Decimal
    equity: Decimal
    drawdown_pct: Decimal
    open_positions: int
    planned_open_risk: Decimal


@dataclass
class SimulationResult:
    trades: list[Trade]
    equity_points: list[EquityPoint]
    summary: dict[str, Any]


def _slipped(price: Decimal, slippage_pct: Decimal, *, buy: bool) -> Decimal:
    rate = slippage_pct / HUNDRED
    return price * (ONE + rate if buy else ONE - rate)


def _sell(
    trade: Trade,
    shares: int,
    price: Decimal,
    market_date: date,
    reason: str,
    cash: Decimal,
) -> Decimal:
    if not trade.initial_shares or not trade.entry_price or not trade.remaining_shares:
        raise RuntimeError("cannot sell an unfilled trade")
    shares = min(shares, trade.remaining_shares)
    proceeds = price * shares
    trade.proceeds += proceeds
    trade.realized_cost_basis += trade.entry_price * shares
    trade.remaining_shares -= shares
    trade.fills.append(
        {
            "side": "sell",
            "date": market_date.isoformat(),
            "shares": shares,
            "price": str(price),
            "reason": reason,
        }
    )
    if trade.remaining_shares == 0:
        trade.status = "closed"
        trade.exit_date = market_date
        trade.exit_reason = reason
    return cash + proceeds


def _open_risk(trade: Trade) -> Decimal:
    if (
        trade.status != "open"
        or not trade.remaining_shares
        or trade.entry_price is None
        or trade.current_stop is None
    ):
        return ZERO
    return max(ZERO, trade.entry_price - trade.current_stop) * trade.remaining_shares


def _equity(
    cash: Decimal,
    positions: list[Trade],
    bars: dict[str, Bar],
) -> Decimal:
    value = cash
    for trade in positions:
        if trade.status != "open" or not trade.remaining_shares:
            continue
        bar = bars.get(trade.signal.ticker)
        mark = bar.close if bar else trade.entry_price
        if mark is not None:
            value += mark * trade.remaining_shares
    return value


def _process_position(
    trade: Trade,
    bar: Bar,
    market_date: date,
    session_index: int,
    parameters: SimulationParameters,
    cash: Decimal,
    *,
    allow_gap_stop: bool = True,
) -> Decimal:
    if (
        trade.status != "open"
        or trade.current_stop is None
        or trade.entry_price is None
        or trade.remaining_shares is None
        or trade.entry_session_index is None
    ):
        return cash
    trade.holding_sessions = session_index - trade.entry_session_index + 1

    # Daily bars do not reveal intraday path. Stops therefore win every
    # same-bar ambiguity, including on the fill date.
    if allow_gap_stop and bar.open <= trade.current_stop:
        return _sell(
            trade,
            trade.remaining_shares,
            _slipped(bar.open, parameters.slippage_pct, buy=False),
            market_date,
            "gap_stop",
            cash,
        )
    if bar.low <= trade.current_stop:
        return _sell(
            trade,
            trade.remaining_shares,
            _slipped(trade.current_stop, parameters.slippage_pct, buy=False),
            market_date,
            "stop",
            cash,
        )

    if not trade.target_one_hit and bar.high >= trade.signal.target_one:
        target_shares = max(1, trade.initial_shares // 2)
        cash = _sell(
            trade,
            target_shares,
            _slipped(trade.signal.target_one, parameters.slippage_pct, buy=False),
            market_date,
            "target_one",
            cash,
        )
        trade.target_one_hit = True
        trade.current_stop = trade.entry_price
    if trade.status == "open" and bar.high >= trade.signal.target_two:
        cash = _sell(
            trade,
            trade.remaining_shares,
            _slipped(trade.signal.target_two, parameters.slippage_pct, buy=False),
            market_date,
            "target_two",
            cash,
        )
    if (
        trade.status == "open"
        and trade.holding_sessions >= parameters.max_holding_sessions
    ):
        cash = _sell(
            trade,
            trade.remaining_shares,
            _slipped(bar.close, parameters.slippage_pct, buy=False),
            market_date,
            "time_exit",
            cash,
        )
    return cash


def simulate_signals(
    sessions: list[date],
    bars_by_date: dict[date, dict[str, Bar]],
    signals_by_date: dict[date, list[Signal]],
    parameters: SimulationParameters,
    entry_allowed_by_date: dict[date, bool] | None = None,
) -> SimulationResult:
    parameters.validate()
    if not sessions:
        raise ValueError("simulation requires at least one market session")
    session_index = {market_date: index for index, market_date in enumerate(sessions)}
    cash = parameters.starting_capital
    peak_equity = cash
    pending: list[Trade] = []
    positions: list[Trade] = []
    trades: list[Trade] = []
    equity_points: list[EquityPoint] = []
    maximum_concurrent_risk = ZERO

    for index, market_date in enumerate(sessions):
        bars = bars_by_date.get(market_date, {})

        for trade in list(positions):
            bar = bars.get(trade.signal.ticker)
            if bar:
                cash = _process_position(
                    trade, bar, market_date, index, parameters, cash
                )
            if trade.status != "open":
                positions.remove(trade)

        for trade in sorted(
            list(pending), key=lambda item: (-item.signal.score, item.signal.ticker)
        ):
            expiration_index = session_index.get(trade.order_expiration_date, index)
            if index > expiration_index:
                trade.status = "expired"
                trade.exit_reason = "trigger_not_reached"
                pending.remove(trade)
                continue
            bar = bars.get(trade.signal.ticker)
            if not bar:
                continue
            if bar.open > trade.signal.maximum_entry:
                trade.status = "gap_rejected"
                trade.exit_reason = "opened_above_maximum_entry"
                pending.remove(trade)
                continue
            base_fill = None
            if bar.open >= trade.signal.trigger:
                base_fill = bar.open
            elif bar.high >= trade.signal.trigger:
                base_fill = trade.signal.trigger
            if base_fill is None:
                continue
            if entry_allowed_by_date is not None and not entry_allowed_by_date.get(
                market_date, False
            ):
                trade.regime_blocked_sessions += 1
                continue
            if len(positions) >= parameters.max_open_positions:
                trade.status = "position_limit"
                trade.exit_reason = "maximum_open_positions"
                pending.remove(trade)
                continue

            fill = _slipped(base_fill, parameters.slippage_pct, buy=True)
            risk_per_share = fill - trade.signal.stop
            if risk_per_share <= 0:
                trade.status = "invalid_plan"
                trade.exit_reason = "stop_not_below_fill"
                pending.remove(trade)
                continue
            current_equity = _equity(cash, positions, bars)
            per_trade_budget = (
                current_equity
                * parameters.risk_per_trade_pct
                / HUNDRED
                * trade.signal.risk_multiplier
            )
            total_risk_limit = current_equity * parameters.max_total_risk_pct / HUNDRED
            current_risk = sum((_open_risk(item) for item in positions), ZERO)
            risk_budget = min(per_trade_budget, total_risk_limit - current_risk)
            shares_by_risk = math.floor(risk_budget / risk_per_share)
            shares_by_cash = math.floor(cash / fill)
            shares = min(shares_by_risk, shares_by_cash)
            if shares < 1:
                trade.status = "insufficient_capacity"
                trade.exit_reason = "cash_or_risk_budget"
                pending.remove(trade)
                continue

            cost = fill * shares
            cash -= cost
            trade.status = "open"
            trade.entry_date = market_date
            trade.entry_session_index = index
            trade.initial_shares = shares
            trade.remaining_shares = shares
            trade.entry_price = fill
            trade.current_stop = trade.signal.stop
            trade.planned_risk = risk_per_share * shares
            trade.fills.append(
                {
                    "side": "buy",
                    "date": market_date.isoformat(),
                    "shares": shares,
                    "price": str(fill),
                    "reason": "triggered",
                }
            )
            pending.remove(trade)
            positions.append(trade)

            # Conservatively resolve a fill-day daily bar with the same
            # stop-first convention used for every other session.
            cash = _process_position(
                trade,
                bar,
                market_date,
                index,
                parameters,
                cash,
                allow_gap_stop=False,
            )
            if trade.status != "open":
                positions.remove(trade)

        active_tickers = {
            item.signal.ticker
            for item in pending + positions
            if item.status in {"pending", "open"}
        }
        for signal in sorted(
            signals_by_date.get(market_date, []),
            key=lambda item: (-item.score, item.ticker),
        ):
            trade = Trade(signal=signal)
            trades.append(trade)
            if signal.ticker in active_tickers:
                trade.status = "duplicate_signal"
                trade.exit_reason = "ticker_already_pending_or_open"
                continue
            first_order_index = index + 1
            if first_order_index >= len(sessions):
                trade.status = "end_of_data"
                trade.exit_reason = "no_next_market_session"
                continue
            expiration_index = min(
                len(sessions) - 1,
                index + parameters.order_lifetime_sessions,
            )
            trade.order_expiration_date = sessions[expiration_index]
            pending.append(trade)
            active_tickers.add(signal.ticker)

        mark_to_market = _equity(cash, positions, bars)
        peak_equity = max(peak_equity, mark_to_market)
        drawdown = (
            (mark_to_market - peak_equity) / peak_equity * HUNDRED
            if peak_equity
            else ZERO
        )
        planned_open_risk = sum((_open_risk(item) for item in positions), ZERO)
        maximum_concurrent_risk = max(maximum_concurrent_risk, planned_open_risk)
        equity_points.append(
            EquityPoint(
                market_date=market_date,
                cash=cash,
                equity=mark_to_market,
                drawdown_pct=drawdown,
                open_positions=len(positions),
                planned_open_risk=planned_open_risk,
            )
        )

    for trade in pending:
        trade.status = "end_of_data"
        trade.exit_reason = "order_window_incomplete"
    for trade in positions:
        trade.status = "open"
        if trade.entry_session_index is not None:
            trade.holding_sessions = len(sessions) - trade.entry_session_index

    closed = [item for item in trades if item.status == "closed"]
    winners = [item for item in closed if item.realized_pnl > 0]
    losers = [item for item in closed if item.realized_pnl < 0]
    gross_profit = sum((item.realized_pnl for item in winners), ZERO)
    gross_loss = abs(sum((item.realized_pnl for item in losers), ZERO))
    final_equity = equity_points[-1].equity
    total_return = (final_equity / parameters.starting_capital - ONE) * HUNDRED
    r_values = [item.realized_r for item in closed if item.realized_r is not None]
    gap_losses = [
        item.realized_pnl
        for item in closed
        if item.exit_reason == "gap_stop" and item.realized_pnl < 0
    ]
    summary = {
        "starting_capital": str(parameters.starting_capital),
        "final_equity": str(final_equity),
        "total_return_pct": str(total_return),
        "maximum_drawdown_pct": str(
            min((item.drawdown_pct for item in equity_points), default=ZERO)
        ),
        "signals": len(trades),
        "filled_trades": sum(item.entry_date is not None for item in trades),
        "closed_trades": len(closed),
        "open_trades": sum(item.status == "open" for item in trades),
        "expired_or_unfilled": sum(item.entry_date is None for item in trades),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate_pct": str(Decimal(len(winners)) / len(closed) * HUNDRED)
        if closed
        else None,
        "profit_factor": str(gross_profit / gross_loss) if gross_loss else None,
        "expectancy_r": str(sum(r_values, ZERO) / len(r_values)) if r_values else None,
        "average_winner": str(gross_profit / len(winners)) if winners else None,
        "average_loser": str(-gross_loss / len(losers)) if losers else None,
        "average_holding_sessions": str(
            Decimal(sum(item.holding_sessions or 0 for item in closed)) / len(closed)
        )
        if closed
        else None,
        "worst_gap_loss": str(min(gap_losses)) if gap_losses else None,
        "maximum_concurrent_planned_risk": str(maximum_concurrent_risk),
        "market_regime_blocked_fill_attempts": sum(
            item.regime_blocked_sessions for item in trades
        ),
        "market_regime_affected_orders": sum(
            item.regime_blocked_sessions > 0 for item in trades
        ),
        "insufficient_sample": len(closed) < 30,
        "status_counts": {
            status: sum(item.status == status for item in trades)
            for status in sorted({item.status for item in trades})
        },
    }
    return SimulationResult(trades=trades, equity_points=equity_points, summary=summary)


def _load_signals(
    session: Session,
    start_date: date,
    end_date: date,
    strategy_configuration: dict[str, Any],
) -> tuple[list[Signal], list[str], StrategyDefinition]:
    metadata = strategy_configuration["strategy"]
    rows = session.execute(
        select(StrategyRun, StrategyDefinition, StrategyCandidate)
        .join(
            StrategyDefinition,
            StrategyDefinition.id == StrategyRun.strategy_definition_id,
        )
        .join(StrategyCandidate, StrategyCandidate.run_id == StrategyRun.run_id)
        .where(
            StrategyDefinition.strategy_key == metadata["key"],
            StrategyDefinition.version == metadata["version"],
            StrategyRun.run_type == "backtest",
            StrategyRun.as_of_date.between(start_date, end_date),
            StrategyRun.feature_calculation_version
            == metadata["feature_calculation_version"],
            StrategyCandidate.action == "actionable",
        )
        .order_by(
            StrategyRun.as_of_date,
            StrategyCandidate.score.desc(),
            StrategyCandidate.ticker,
        )
    ).all()
    if not rows:
        raise RuntimeError(
            "No actionable deterministic replay signals were found; run "
            "replay-strategy for the requested dates first"
        )
    definition = rows[0][1]
    signals: list[Signal] = []
    run_hashes: dict[str, str] = {}
    for run, _definition, candidate in rows:
        plan = candidate.trade_plan or {}
        required = (
            "entry_trigger",
            "maximum_entry_price",
            "initial_stop",
            "target_one",
            "target_two",
            "risk_multiplier",
        )
        if any(plan.get(key) is None for key in required):
            continue
        signals.append(
            Signal(
                source_run_id=run.run_id,
                ticker=candidate.ticker,
                signal_date=run.as_of_date,
                score=Decimal(candidate.score or 0),
                trigger=Decimal(str(plan["entry_trigger"])),
                maximum_entry=Decimal(str(plan["maximum_entry_price"])),
                stop=Decimal(str(plan["initial_stop"])),
                target_one=Decimal(str(plan["target_one"])),
                target_two=Decimal(str(plan["target_two"])),
                risk_multiplier=Decimal(str(plan["risk_multiplier"])),
            )
        )
        run_hashes[run.run_id] = run.payload_hash
    return signals, [run_hashes[key] for key in sorted(run_hashes)], definition


def _load_bars(
    session: Session,
    start_date: date,
    end_date: date,
    tickers: set[str],
    benchmark_tickers: set[str] | None = None,
) -> tuple[list[date], dict[date, dict[str, Bar]]]:
    benchmark_tickers = benchmark_tickers or {"QQQ"}
    sessions = session.scalars(
        select(DailyPriceBar.trade_date)
        .where(
            DailyPriceBar.ticker == "QQQ",
            DailyPriceBar.trade_date.between(start_date, end_date),
        )
        .distinct()
        .order_by(DailyPriceBar.trade_date)
    ).all()
    rows = session.scalars(
        select(DailyPriceBar)
        .where(
            DailyPriceBar.ticker.in_(tickers | {"QQQ"} | benchmark_tickers),
            DailyPriceBar.trade_date.between(start_date, end_date),
        )
        .order_by(DailyPriceBar.trade_date, DailyPriceBar.ticker)
    ).all()
    for benchmark_ticker in benchmark_tickers:
        benchmark_history = session.scalars(
            select(DailyPriceBar)
            .where(
                DailyPriceBar.ticker == benchmark_ticker,
                DailyPriceBar.trade_date < start_date,
            )
            .order_by(DailyPriceBar.trade_date.desc())
            .limit(260)
        ).all()
        rows.extend(reversed(benchmark_history))
    bars: dict[date, dict[str, Bar]] = {}
    for row in rows:
        bars.setdefault(row.trade_date, {})[row.ticker] = Bar(
            market_date=row.trade_date,
            ticker=row.ticker,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
        )
    return sessions, bars


def _market_regime_permissions(
    sessions: list[date],
    bars: dict[date, dict[str, Bar]],
    configuration: dict[str, Any],
) -> tuple[dict[date, bool] | None, dict[str, Any]]:
    regime = configuration.get("market_regime")
    if not regime or not regime["enabled"]:
        return None, {"enabled": False}

    benchmarks = [str(regime["benchmark_ticker"]).upper()]
    benchmarks.extend(
        str(ticker).upper()
        for ticker in regime.get("additional_benchmark_tickers", [])
    )
    benchmarks = list(dict.fromkeys(benchmarks))
    combination = regime.get("benchmark_combination", "all")
    window = int(regime["moving_average_sessions"])
    histories = {}
    for benchmark in benchmarks:
        benchmark_dates = sorted(
            market_date
            for market_date, daily_bars in bars.items()
            if benchmark in daily_bars
        )
        histories[benchmark] = (
            benchmark_dates,
            [bars[market_date][benchmark].close for market_date in benchmark_dates],
        )
    permissions: dict[date, bool] = {}
    insufficient = 0
    for entry_date in sessions:
        # Orders fill during the current session, so only information available
        # at the previous close may authorize a new entry.
        benchmark_permissions = []
        session_has_insufficient_history = False
        for benchmark in benchmarks:
            benchmark_dates, closes = histories[benchmark]
            prior_index = bisect_left(benchmark_dates, entry_date) - 1
            if prior_index + 1 < window:
                benchmark_permissions.append(False)
                session_has_insufficient_history = True
                continue
            current_window = closes[prior_index - window + 1 : prior_index + 1]
            current_average = sum(current_window, ZERO) / window
            allowed = True
            if regime["require_close_above_moving_average"]:
                allowed = closes[prior_index] > current_average
            if regime["require_moving_average_rising"]:
                if prior_index < window:
                    allowed = False
                    session_has_insufficient_history = True
                else:
                    previous_window = closes[prior_index - window : prior_index]
                    previous_average = sum(previous_window, ZERO) / window
                    allowed = allowed and current_average > previous_average
            benchmark_permissions.append(allowed)
        if session_has_insufficient_history:
            insufficient += 1
        permissions[entry_date] = (
            all(benchmark_permissions)
            if combination == "all"
            else any(benchmark_permissions)
        )

    return permissions, {
        **regime,
        "benchmark_ticker": benchmarks[0],
        "benchmark_tickers": benchmarks,
        "benchmark_combination": combination,
        "decision_basis": "previous_session_close",
        "entry_sessions_allowed": sum(permissions.values()),
        "entry_sessions_blocked": sum(not item for item in permissions.values()),
        "insufficient_history_sessions": insufficient,
    }


def _weighted_exit_price(trade: Trade) -> Decimal | None:
    sells = [fill for fill in trade.fills if fill["side"] == "sell"]
    shares = sum(fill["shares"] for fill in sells)
    if not shares:
        return None
    return (
        sum((Decimal(fill["price"]) * fill["shares"] for fill in sells), ZERO) / shares
    )


def _benchmark_return(
    sessions: list[date], bars: dict[date, dict[str, Bar]]
) -> Decimal | None:
    if not sessions:
        return None
    first = bars.get(sessions[0], {}).get("QQQ")
    last = bars.get(sessions[-1], {}).get("QQQ")
    if not first or not last or not first.close:
        return None
    return (last.close / first.close - ONE) * HUNDRED


def run_simulation(
    session: Session,
    start_date: date,
    end_date: date,
    parameters: SimulationParameters,
    strategy_configuration_path: str | None = None,
    strategy_configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if start_date > end_date:
        raise ValueError("Simulation start date must be on or before end date")
    parameters.validate()
    strategy_configuration = (
        replay_configuration(strategy_configuration_path)
        if strategy_configuration is None
        else replay_configuration(
            strategy_configuration_path, strategy_configuration
        )
    )
    metadata = strategy_configuration["strategy"]
    signals, run_hashes, definition = _load_signals(
        session, start_date, end_date, strategy_configuration
    )
    source_runs_hash = hashlib.sha256(
        json.dumps(run_hashes, separators=(",", ":")).encode()
    ).hexdigest()
    scenario_payload = {
        "strategy_key": metadata["key"],
        "strategy_version": metadata["version"],
        "replay_model": metadata["replay_model"],
        "feature_version": metadata["feature_calculation_version"],
        "strategy_configuration_fingerprint": strategy_configuration[
            "configuration_fingerprint"
        ],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "source_runs_hash": source_runs_hash,
        "parameters": parameters.payload(),
    }
    scenario_hash = hashlib.sha256(
        json.dumps(scenario_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    scenario_key = f"strategy-simulation:{scenario_hash}"
    existing = session.scalar(
        select(StrategySimulationRun).where(
            StrategySimulationRun.scenario_key == scenario_key
        )
    )
    if existing is not None:
        return {
            "simulation_id": existing.simulation_id,
            "recorded": False,
            "idempotent_replay": True,
            "scenario_key": scenario_key,
            "summary": existing.summary,
        }

    regime = strategy_configuration.get("market_regime") or {}
    benchmark_tickers = {
        str(regime.get("benchmark_ticker", "QQQ")).upper(),
        *(
            str(ticker).upper()
            for ticker in regime.get("additional_benchmark_tickers", [])
        ),
    }
    sessions, bars = _load_bars(
        session,
        start_date,
        end_date,
        {signal.ticker for signal in signals},
        benchmark_tickers,
    )
    entry_permissions, regime_summary = _market_regime_permissions(
        sessions, bars, strategy_configuration
    )
    signals_by_date: dict[date, list[Signal]] = {}
    for signal in signals:
        signals_by_date.setdefault(signal.signal_date, []).append(signal)
    result = simulate_signals(
        sessions,
        bars,
        signals_by_date,
        parameters,
        entry_permissions,
    )
    benchmark = _benchmark_return(sessions, bars)
    result.summary.update(
        {
            "strategy_key": metadata["key"],
            "strategy_version": metadata["version"],
            "replay_model": metadata["replay_model"],
            "feature_calculation_version": metadata[
                "feature_calculation_version"
            ],
            "strategy_configuration_fingerprint": strategy_configuration[
                "configuration_fingerprint"
            ],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "market_sessions": len(sessions),
            "qqq_return_pct": str(benchmark) if benchmark is not None else None,
            "parameters": parameters.payload(),
            "source_runs_hash": source_runs_hash,
            "market_regime": regime_summary,
        }
    )

    simulation_id = str(uuid.uuid4())
    session.add(
        StrategySimulationRun(
            simulation_id=simulation_id,
            strategy_definition_id=definition.id,
            scenario_key=scenario_key,
            start_date=start_date,
            end_date=end_date,
            feature_calculation_version=metadata["feature_calculation_version"],
            source_runs_hash=source_runs_hash,
            parameters=parameters.payload(),
            status="completed",
            summary=result.summary,
        )
    )
    # The child models refer to the parent by scalar simulation_id rather than
    # ORM relationships, so SQLAlchemy cannot infer the required INSERT order.
    # Persist the parent within this transaction before adding dependent rows.
    session.flush()
    for trade in result.trades:
        session.add(
            StrategySimulationTrade(
                simulation_id=simulation_id,
                source_run_id=trade.signal.source_run_id,
                ticker=trade.signal.ticker,
                signal_date=trade.signal.signal_date,
                order_expiration_date=trade.order_expiration_date,
                entry_date=trade.entry_date,
                exit_date=trade.exit_date,
                status=trade.status,
                initial_shares=trade.initial_shares,
                remaining_shares=trade.remaining_shares,
                entry_price=trade.entry_price,
                initial_stop_price=trade.signal.stop,
                target_one_price=trade.signal.target_one,
                target_two_price=trade.signal.target_two,
                exit_price=_weighted_exit_price(trade),
                planned_risk=trade.planned_risk,
                net_pnl=trade.realized_pnl if trade.entry_date else None,
                realized_r=trade.realized_r,
                holding_sessions=trade.holding_sessions,
                exit_reason=trade.exit_reason,
                details={
                    "score": str(trade.signal.score),
                    "trigger": str(trade.signal.trigger),
                    "maximum_entry": str(trade.signal.maximum_entry),
                    "risk_multiplier": str(trade.signal.risk_multiplier),
                    "target_one_hit": trade.target_one_hit,
                    "regime_blocked_sessions": trade.regime_blocked_sessions,
                    "fills": trade.fills,
                },
            )
        )
    for point in result.equity_points:
        session.add(
            StrategySimulationEquityPoint(
                simulation_id=simulation_id,
                market_date=point.market_date,
                cash=point.cash,
                equity=point.equity,
                drawdown_pct=point.drawdown_pct,
                open_positions=point.open_positions,
                planned_open_risk=point.planned_open_risk,
            )
        )
    session.commit()
    return {
        "simulation_id": simulation_id,
        "recorded": True,
        "idempotent_replay": False,
        "scenario_key": scenario_key,
        "summary": result.summary,
    }


def list_simulations(session: Session, limit: int = 20) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    rows = session.scalars(
        select(StrategySimulationRun)
        .order_by(StrategySimulationRun.generated_at_utc.desc())
        .limit(limit)
    ).all()
    return {
        "count": len(rows),
        "items": [
            {
                "simulation_id": row.simulation_id,
                "start_date": row.start_date.isoformat(),
                "end_date": row.end_date.isoformat(),
                "feature_calculation_version": row.feature_calculation_version,
                "parameters": row.parameters,
                "status": row.status,
                "summary": row.summary,
                "generated_at_utc": row.generated_at_utc.isoformat(),
            }
            for row in rows
        ],
    }


def get_simulation(
    session: Session,
    simulation_id: str,
    *,
    include_equity: bool = True,
    trade_limit: int = 5000,
) -> dict[str, Any]:
    if trade_limit < 1 or trade_limit > 5000:
        raise ValueError("trade limit must be between 1 and 5000")
    run = session.get(StrategySimulationRun, simulation_id)
    if run is None:
        return {"simulation_id": simulation_id, "found": False}
    trades = session.scalars(
        select(StrategySimulationTrade)
        .where(StrategySimulationTrade.simulation_id == simulation_id)
        .order_by(
            StrategySimulationTrade.signal_date,
            StrategySimulationTrade.ticker,
        )
        .limit(trade_limit)
    ).all()
    trade_count = session.scalar(
        select(func.count(StrategySimulationTrade.id)).where(
            StrategySimulationTrade.simulation_id == simulation_id
        )
    )
    equity = (
        session.scalars(
            select(StrategySimulationEquityPoint)
            .where(StrategySimulationEquityPoint.simulation_id == simulation_id)
            .order_by(StrategySimulationEquityPoint.market_date)
        ).all()
        if include_equity
        else []
    )
    return {
        "simulation_id": simulation_id,
        "found": True,
        "scenario_key": run.scenario_key,
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "feature_calculation_version": run.feature_calculation_version,
        "source_runs_hash": run.source_runs_hash,
        "parameters": run.parameters,
        "status": run.status,
        "summary": run.summary,
        "trade_count": int(trade_count or 0),
        "trade_limit": trade_limit,
        "trades_truncated": int(trade_count or 0) > trade_limit,
        "trades": [
            {
                "ticker": item.ticker,
                "source_run_id": item.source_run_id,
                "signal_date": item.signal_date.isoformat(),
                "order_expiration_date": item.order_expiration_date.isoformat()
                if item.order_expiration_date
                else None,
                "entry_date": item.entry_date.isoformat() if item.entry_date else None,
                "exit_date": item.exit_date.isoformat() if item.exit_date else None,
                "status": item.status,
                "initial_shares": item.initial_shares,
                "remaining_shares": item.remaining_shares,
                "entry_price": str(item.entry_price) if item.entry_price else None,
                "initial_stop_price": str(item.initial_stop_price)
                if item.initial_stop_price
                else None,
                "target_one_price": str(item.target_one_price)
                if item.target_one_price
                else None,
                "target_two_price": str(item.target_two_price)
                if item.target_two_price
                else None,
                "exit_price": str(item.exit_price) if item.exit_price else None,
                "planned_risk": str(item.planned_risk) if item.planned_risk else None,
                "net_pnl": str(item.net_pnl) if item.net_pnl is not None else None,
                "realized_r": str(item.realized_r)
                if item.realized_r is not None
                else None,
                "holding_sessions": item.holding_sessions,
                "exit_reason": item.exit_reason,
                "details": item.details,
            }
            for item in trades
        ],
        "equity_curve": [
            {
                "market_date": item.market_date.isoformat(),
                "cash": str(item.cash),
                "equity": str(item.equity),
                "drawdown_pct": str(item.drawdown_pct),
                "open_positions": item.open_positions,
                "planned_open_risk": str(item.planned_open_risk),
            }
            for item in equity
        ],
        "equity_curve_included": include_equity,
    }
