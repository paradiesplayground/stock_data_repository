"""Forward outcomes for setups excluded only by a replay decline screen."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyPriceBar, SecurityDailyFeature
from app.services.strategy_replay import score_feature


ZERO = Decimal("0")
HUNDRED = Decimal("100")
HORIZONS = (5, 10, 20, 30)


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _mean(values: list[Decimal]) -> str | None:
    return str(sum(values, ZERO) / len(values)) if values else None


def _outcome_before_stop(
    bars: list[DailyPriceBar], *, stop: Decimal, target: Decimal
) -> bool | None:
    """Use the simulator's conservative same-bar ordering: stop wins ties."""
    for bar in bars:
        stopped = bar.low is not None and bar.low <= stop
        reached = bar.high is not None and bar.high >= target
        if stopped:
            return False
        if reached:
            return True
    return None


def _outcome(
    bars: list[DailyPriceBar], candidate: dict[str, Any], horizon: int
) -> dict[str, Any]:
    plan = candidate.get("trade_plan") or {}
    trigger = _decimal(plan.get("entry_trigger"))
    stop = _decimal(plan.get("initial_stop"))
    if trigger is None or stop is None or trigger <= stop:
        return {"sessions_available": len(bars), "trade_plan_available": False}
    risk = trigger - stop
    observed = bars[:horizon]
    highs = [bar.high for bar in observed if bar.high is not None]
    lows = [bar.low for bar in observed if bar.low is not None]
    target_2r = trigger + risk * Decimal("2")
    target_3r = trigger + risk * Decimal("3")
    return {
        "sessions_available": len(observed),
        "trade_plan_available": True,
        "trigger_hit": any(high >= trigger for high in highs),
        "mfe_r": str((max(highs) - trigger) / risk) if highs else None,
        "mae_r": str((min(lows) - trigger) / risk) if lows else None,
        "two_r_before_stop": _outcome_before_stop(
            observed, stop=stop, target=target_2r
        ),
        "three_r_before_stop": _outcome_before_stop(
            observed, stop=stop, target=target_3r
        ),
    }


def _future_sessions(session: Session, signal_date: date, limit: int) -> list[date]:
    return list(
        session.scalars(
            select(DailyPriceBar.trade_date)
            .where(
                DailyPriceBar.ticker == "QQQ",
                DailyPriceBar.trade_date > signal_date,
            )
            .distinct()
            .order_by(DailyPriceBar.trade_date)
            .limit(limit)
        )
    )


def _forward_bars(
    session: Session, ticker: str, sessions: list[date]
) -> list[DailyPriceBar]:
    if not sessions:
        return []
    return list(
        session.scalars(
            select(DailyPriceBar)
            .where(
                DailyPriceBar.ticker == ticker,
                DailyPriceBar.trade_date.in_(sessions),
            )
            .order_by(DailyPriceBar.trade_date)
        )
    )


def rejected_opportunity_analysis(
    session: Session,
    start_date: date,
    end_date: date,
    *,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Measure forward counterfactuals for names rejected only by decline mode.

    This reads point-in-time features and forward OHLC bars; it neither changes
    source data nor promotes a rejected candidate into the simulated portfolio.
    """
    feature_version = configuration["strategy"]["feature_calculation_version"]
    features = session.scalars(
        select(SecurityDailyFeature)
        .where(
            SecurityDailyFeature.as_of_date.between(start_date, end_date),
            SecurityDailyFeature.price_date == SecurityDailyFeature.as_of_date,
            SecurityDailyFeature.calculation_version == feature_version,
        )
        .order_by(SecurityDailyFeature.as_of_date, SecurityDailyFeature.ticker)
    ).all()
    opportunities: list[dict[str, Any]] = []
    for feature in features:
        candidate = score_feature(
            feature,
            constructive_volume=False,
            excluded_sic_prefixes=configuration["universe"]["excluded_sic_prefixes"],
            configuration=configuration,
        )
        if not candidate or not candidate["payload"].get("rejected_opportunity"):
            continue
        # A decline-only opportunity can carry no unrelated rejection reason.
        if candidate["reasons"] != ["decline_screen_failed"]:
            continue
        future_sessions = _future_sessions(session, feature.as_of_date, max(HORIZONS))
        bars = _forward_bars(session, feature.ticker, future_sessions)
        forward = {
            str(horizon): _outcome(bars, candidate, horizon) for horizon in HORIZONS
        }
        later_eligibility = {}
        for horizon in HORIZONS:
            if len(future_sessions) < horizon:
                later_eligibility[str(horizon)] = None
                continue
            later_feature = session.scalar(
                select(SecurityDailyFeature).where(
                    SecurityDailyFeature.ticker == feature.ticker,
                    SecurityDailyFeature.as_of_date == future_sessions[horizon - 1],
                    SecurityDailyFeature.price_date == future_sessions[horizon - 1],
                    SecurityDailyFeature.calculation_version == feature_version,
                )
            )
            later_candidate = (
                score_feature(
                    later_feature,
                    constructive_volume=False,
                    excluded_sic_prefixes=configuration["universe"]["excluded_sic_prefixes"],
                    configuration=configuration,
                )
                if later_feature is not None
                else None
            )
            later_eligibility[str(horizon)] = bool(
                later_candidate and later_candidate["action"] == "actionable"
            )
        opportunities.append({
            "ticker": feature.ticker,
            "signal_date": feature.as_of_date.isoformat(),
            "decline_screen": candidate["payload"]["decline_screen"],
            "forward": forward,
            "subsequent_eligibility": later_eligibility,
        })

    horizon_summary = {}
    for horizon in HORIZONS:
        outcomes = [item["forward"][str(horizon)] for item in opportunities]
        planned = [item for item in outcomes if item.get("trade_plan_available")]
        hits = [item for item in planned if item.get("trigger_hit")]
        two_r = [item for item in planned if item.get("two_r_before_stop") is True]
        three_r = [item for item in planned if item.get("three_r_before_stop") is True]
        eligible = [
            item for item in opportunities
            if item["subsequent_eligibility"][str(horizon)] is True
        ]
        horizon_summary[str(horizon)] = {
            "opportunity_count": len(opportunities),
            "trade_plan_count": len(planned),
            "trigger_hits": len(hits),
            "trigger_hit_rate_pct": str(Decimal(len(hits)) / len(planned) * HUNDRED) if planned else None,
            "mean_mfe_r": _mean([_decimal(item["mfe_r"]) for item in planned if item.get("mfe_r") is not None]),
            "mean_mae_r": _mean([_decimal(item["mae_r"]) for item in planned if item.get("mae_r") is not None]),
            "two_r_before_stop": len(two_r),
            "three_r_before_stop": len(three_r),
            "subsequently_eligible": len(eligible),
        }
    return {
        "feature_calculation_version": feature_version,
        "decline_filter": {
            key: configuration["hard_thresholds"].get(key)
            for key in (
                "maximum_price_change_12w_pct",
                "maximum_drawdown_12w_high_pct",
                "decline_filter_mode",
            )
        },
        "candidate_days": len(opportunities),
        "forward_outcomes": horizon_summary,
        "opportunities": opportunities,
    }
