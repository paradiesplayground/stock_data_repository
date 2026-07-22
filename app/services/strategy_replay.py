import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.industry_taxonomy import TAXONOMY_VERSION, resolve_industry_groups
from app.models import DailyPriceBar, SecurityDailyFeature, StrategyRun
from app.services.strategy_config import (
    configuration_hash,
    load_strategy_configuration,
    validate_strategy_configuration,
)
from app.services.strategy_tracking import record_strategy_run

_DEFAULT_CONFIGURATION = load_strategy_configuration()
STRATEGY_KEY = _DEFAULT_CONFIGURATION["strategy"]["key"]
STRATEGY_VERSION = _DEFAULT_CONFIGURATION["strategy"]["version"]
FEATURE_VERSION = _DEFAULT_CONFIGURATION["strategy"]["feature_calculation_version"]
REPLAY_MODEL = _DEFAULT_CONFIGURATION["strategy"]["replay_model"]


def replay_configuration(
    path: str | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if path is not None and configuration is not None:
        raise ValueError("provide a strategy configuration path or payload, not both")
    configuration = (
        validate_strategy_configuration(configuration)
        if configuration is not None
        else load_strategy_configuration(path)
    )
    requested_groups = configuration["universe"]["exclude_industry_groups"]
    resolved, prefixes = resolve_industry_groups(requested_groups)
    configuration["universe"]["exclude_industry_groups"] = [
        item["key"] for item in resolved
    ]
    configuration["universe"]["excluded_sic_prefixes"] = prefixes
    configuration["universe"]["industry_taxonomy_version"] = TAXONOMY_VERSION
    configuration["configuration_fingerprint"] = configuration_hash(configuration)
    return configuration


def _json_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _sic_excluded(sic_code: str | None, prefixes: list[str]) -> bool | None:
    if not sic_code or not str(sic_code).strip().isdigit():
        return None
    normalized = str(sic_code).strip().zfill(4)
    return any(normalized.startswith(prefix) for prefix in prefixes)


def _band_points(
    value: Decimal | None,
    bands: list[dict[str, Any]],
    threshold_key: str,
) -> int:
    if value is None:
        return 0
    ordered = sorted(
        bands, key=lambda item: Decimal(str(item[threshold_key])), reverse=True
    )
    for band in ordered:
        if value >= Decimal(str(band[threshold_key])):
            return int(band["points"])
    return 0


def _risk_tier(market_cap: Decimal, configuration: dict[str, Any]) -> dict[str, Any]:
    tiers = sorted(
        configuration["risk_tiers"],
        key=lambda item: Decimal(str(item["minimum_market_cap"])),
        reverse=True,
    )
    for tier in tiers:
        if market_cap >= Decimal(str(tier["minimum_market_cap"])):
            return tier
    raise ValueError("risk_tiers must cover the configured minimum market cap")


def score_feature(
    feature: SecurityDailyFeature,
    *,
    constructive_volume: bool,
    excluded_sic_prefixes: list[str],
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a reproducible replay candidate from stored point-in-time fields."""
    configuration = configuration or replay_configuration()
    universe = configuration["universe"]
    thresholds = configuration["hard_thresholds"]
    scoring = configuration["scoring"]
    required_values = (
        feature.close,
        feature.approximate_market_cap,
        feature.revenue_ttm_yoy_pct,
        feature.latest_quarter_revenue_yoy_pct,
        feature.price_change_12w_pct,
        feature.avg_dollar_volume_20d,
    )
    if any(value is None for value in required_values):
        return None
    if (
        feature.reference_primary_exchange not in set(universe["exchanges"])
        or feature.reference_security_type not in set(universe["security_types"])
        or (universe["require_active"] and feature.reference_active is not True)
        or feature.close < Decimal(str(universe["minimum_price"]))
        or feature.approximate_market_cap
        < Decimal(str(universe["minimum_market_cap"]))
        or feature.revenue_ttm_yoy_pct
        < Decimal(str(thresholds["minimum_ttm_revenue_growth_pct"]))
        or feature.latest_quarter_revenue_yoy_pct
        < Decimal(str(thresholds["minimum_quarter_revenue_growth_pct"]))
        or feature.price_change_12w_pct
        > Decimal(str(thresholds["maximum_price_change_12w_pct"]))
        or feature.avg_dollar_volume_20d
        < Decimal(str(thresholds["minimum_avg_dollar_volume_20d"]))
    ):
        return None

    excluded = _sic_excluded(feature.reference_sic_code, excluded_sic_prefixes)
    if excluded is True:
        return None

    reasons: list[str] = []
    warnings: list[str] = []
    revenue_points = _band_points(
        feature.revenue_ttm_yoy_pct, scoring["growth_bands"], "minimum_pct"
    ) + _band_points(
        feature.latest_quarter_revenue_yoy_pct,
        scoring["growth_bands"],
        "minimum_pct",
    )

    price_setup = scoring["price_setup"]
    price_points = int(price_setup["base_points"])
    if (
        feature.drawdown_52w_pct is not None
        and Decimal(str(price_setup["drawdown_52w_min_pct"]))
        <= feature.drawdown_52w_pct
        <= Decimal(str(price_setup["drawdown_52w_max_pct"]))
    ):
        price_points += int(price_setup["drawdown_points"])
    if feature.ema_10 is not None and feature.close >= feature.ema_10:
        price_points += int(price_setup["above_ema_10_points"])
    if feature.ema_20 is not None and feature.close >= feature.ema_20:
        price_points += int(price_setup["above_ema_20_points"])

    liquidity_points = _band_points(
        feature.avg_dollar_volume_20d,
        scoring["liquidity_bands"],
        "minimum_dollars",
    )
    durability = scoring["financial_durability"]
    financial_points = 0
    self_funding = (
        feature.free_cash_flow_ttm is not None and feature.free_cash_flow_ttm >= 0
    )
    if self_funding:
        # Runway is intentionally null for non-burning companies in the
        # derived-data contract; positive FCF satisfies both runway buckets.
        financial_points += int(durability["self_funding_runway_points"])
    elif (
        feature.cash_runway_months is not None
        and feature.cash_runway_months
        >= Decimal(str(durability["minimum_runway_months"]))
    ):
        financial_points += int(durability["minimum_runway_points"])
        if feature.cash_runway_months >= Decimal(
            str(durability["strong_runway_months"])
        ):
            financial_points += int(durability["strong_runway_points"])
    if (
        feature.share_count_yoy_pct is not None
        and feature.share_count_yoy_pct
        < Decimal(str(durability["maximum_share_growth_pct"]))
    ):
        financial_points += int(durability["share_growth_points"])
    if (
        feature.total_debt is not None
        and feature.cash_and_short_term_investments is not None
        and feature.total_debt <= feature.cash_and_short_term_investments
    ):
        financial_points += int(durability["debt_covered_by_cash_points"])
    if feature.free_cash_flow_ttm is not None and feature.free_cash_flow_ttm >= 0:
        financial_points += int(durability["positive_free_cash_flow_points"])

    technical = scoring["technical_confirmation"]
    technical_points = 0
    if (
        feature.low_20d is not None
        and feature.low_60d is not None
        and feature.low_20d > feature.low_60d
    ):
        technical_points += int(technical["higher_low_points"])
    if (feature.ema_10 is not None and feature.close >= feature.ema_10) or (
        feature.ema_20 is not None and feature.close >= feature.ema_20
    ):
        technical_points += int(technical["above_ema_points"])
    if (
        feature.distance_to_20d_high_pct is not None
        and feature.distance_to_20d_high_pct
        >= Decimal(str(technical["minimum_distance_to_20d_high_pct"]))
    ):
        technical_points += int(technical["near_high_points"])
    if constructive_volume:
        technical_points += int(technical["constructive_volume_points"])
    if (
        feature.relative_return_20d_vs_qqq_pct is not None
        and feature.relative_return_20d_vs_qqq_pct > 0
    ):
        technical_points += int(technical["positive_relative_return_points"])

    score_components = {
        "revenue_growth": revenue_points,
        "revenue_quality": 0,
        "price_setup": price_points,
        "liquidity": liquidity_points,
        "financial_durability": financial_points,
        "catalyst": 0,
        "technical_confirmation": technical_points,
    }
    score = sum(score_components.values())

    incomplete = False
    if excluded is None:
        incomplete = True
        warnings.append("unknown_sic_exclusion_status")
    if feature.cash_runway_months is None and not self_funding:
        incomplete = True
        warnings.append("missing_cash_runway")
    elif (
        feature.cash_runway_months is not None
        and feature.cash_runway_months
        < Decimal(str(thresholds["minimum_cash_runway_months"]))
    ):
        reasons.append("cash_runway_below_configured_minimum")
    if feature.high_20d is None or feature.low_20d is None or feature.atr_14 is None:
        incomplete = True
        warnings.append("missing_trade_plan_price_fields")

    rejected = bool(reasons)
    actionable_rules = scoring["actionable"]
    require_relative_strength = actionable_rules.get(
        "require_positive_relative_return_20d_vs_qqq", False
    )
    require_constructive_volume = actionable_rules.get(
        "require_constructive_volume", False
    )
    relative_strength_passes = (
        feature.relative_return_20d_vs_qqq_pct is not None
        and feature.relative_return_20d_vs_qqq_pct > 0
    )
    if require_relative_strength and not relative_strength_passes:
        warnings.append("relative_return_20d_vs_qqq_not_positive")
    if require_constructive_volume and not constructive_volume:
        warnings.append("constructive_volume_below_configured_minimum")
    actionable = (
        not incomplete
        and not rejected
        and (not require_relative_strength or relative_strength_passes)
        and (not require_constructive_volume or constructive_volume)
        and score >= int(actionable_rules["minimum_total_score"])
        and technical_points >= int(actionable_rules["minimum_technical_points"])
        and (
            feature.rsi_14 is None
            or feature.rsi_14 < Decimal(str(actionable_rules["maximum_rsi_14"]))
        )
    )
    stage = "rejected" if rejected else "incomplete" if incomplete else "qualified"
    action = "remove" if rejected else "actionable" if actionable else "keep-watching"

    trade_plan = None
    if not incomplete and not rejected:
        entry_model = configuration["entry_model"]
        trigger = feature.high_20d * (
            Decimal("1")
            + Decimal(str(entry_model["trigger_above_high_pct"])) / Decimal("100")
        )
        atr_stop = trigger - (
            feature.atr_14 * Decimal(str(entry_model["atr_stop_multiple"]))
        )
        stop = max(feature.low_20d, atr_stop)
        if stop >= trigger:
            stop = trigger - (
                feature.atr_14
                * Decimal(str(entry_model["fallback_atr_stop_multiple"]))
            )
        risk_per_share = trigger - stop
        if risk_per_share > 0:
            trade_plan = {
                "entry_order": "buy-stop",
                "entry_trigger": str(trigger),
                "maximum_entry_price": str(
                    trigger
                    * (
                        Decimal("1")
                        + Decimal(
                            str(entry_model["maximum_gap_above_trigger_pct"])
                        )
                        / Decimal("100")
                    )
                ),
                "initial_stop": str(stop),
                "target_one": str(
                    trigger
                    + risk_per_share * Decimal(str(entry_model["target_one_r"]))
                ),
                "target_two": str(
                    trigger
                    + risk_per_share * Decimal(str(entry_model["target_two_r"]))
                ),
                "risk_tier": _risk_tier(
                    feature.approximate_market_cap, configuration
                )["tier"],
                "risk_multiplier": str(
                    _risk_tier(feature.approximate_market_cap, configuration)[
                        "multiplier"
                    ]
                ),
            }
        else:
            stage = "incomplete"
            action = "keep-watching"
            warnings.append("invalid_trade_plan_risk")

    metrics = {
        "close": _json_decimal(feature.close),
        "market_cap": _json_decimal(feature.approximate_market_cap),
        "ttm_revenue_growth_pct": _json_decimal(feature.revenue_ttm_yoy_pct),
        "quarter_revenue_growth_pct": _json_decimal(
            feature.latest_quarter_revenue_yoy_pct
        ),
        "price_change_12w_pct": _json_decimal(feature.price_change_12w_pct),
        "drawdown_52w_pct": _json_decimal(feature.drawdown_52w_pct),
        "avg_dollar_volume_20d": _json_decimal(feature.avg_dollar_volume_20d),
        "cash_runway_months": _json_decimal(feature.cash_runway_months),
        "positive_free_cash_flow": self_funding,
        "rsi_14": _json_decimal(feature.rsi_14),
        "relative_volume_20d": _json_decimal(feature.relative_volume_20d),
    }
    if "require_positive_relative_return_20d_vs_qqq" in actionable_rules:
        metrics["relative_return_20d_vs_qqq_pct"] = _json_decimal(
            feature.relative_return_20d_vs_qqq_pct
        )

    return {
        "ticker": feature.ticker,
        "stage": stage,
        "action": action,
        "score": score,
        "score_components": score_components,
        "metrics": metrics,
        "reasons": reasons + warnings,
        "trade_plan": trade_plan,
        "payload": {
            "in_raw_pool": True,
            "in_qualified_watchlist": stage == "qualified",
            "mechanical_replay": True,
            "qualitative_review_performed": False,
        },
    }


def _constructive_volume_tickers(
    session: Session,
    as_of_date: date,
    tickers: set[str],
    configuration: dict[str, Any],
) -> set[str]:
    if not tickers:
        return set()
    latest_rows = session.execute(
        select(DailyPriceBar.ticker, DailyPriceBar.close, DailyPriceBar.volume).where(
            DailyPriceBar.trade_date == as_of_date,
            DailyPriceBar.ticker.in_(tickers),
        )
    ).all()
    prior_date = session.scalar(
        select(func.max(DailyPriceBar.trade_date)).where(
            DailyPriceBar.ticker == "QQQ", DailyPriceBar.trade_date < as_of_date
        )
    )
    if prior_date is None:
        return set()
    prior_close = dict(
        session.execute(
            select(DailyPriceBar.ticker, DailyPriceBar.close).where(
                DailyPriceBar.trade_date == prior_date,
                DailyPriceBar.ticker.in_(tickers),
            )
        ).all()
    )
    features = dict(
        session.execute(
            select(
                SecurityDailyFeature.ticker,
                SecurityDailyFeature.avg_volume_20d,
            ).where(
                SecurityDailyFeature.as_of_date == as_of_date,
                SecurityDailyFeature.calculation_version
                == configuration["strategy"]["feature_calculation_version"],
                SecurityDailyFeature.ticker.in_(tickers),
            )
        ).all()
    )
    return {
        ticker
        for ticker, close, volume in latest_rows
        if ticker in prior_close
        and features.get(ticker)
        and close > prior_close[ticker]
        and volume
        >= features[ticker]
        * Decimal(
            str(
                configuration["scoring"]["technical_confirmation"][
                    "constructive_volume_multiplier"
                ]
            )
        )
    }


def replay_strategy_date(
    session: Session,
    as_of_date: date,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configuration = configuration or replay_configuration()
    metadata = configuration["strategy"]
    strategy_key = metadata["key"]
    strategy_version = metadata["version"]
    feature_version = metadata["feature_calculation_version"]
    replay_model = metadata["replay_model"]
    fingerprint = configuration["configuration_fingerprint"]
    excluded_prefixes = configuration["universe"]["excluded_sic_prefixes"]
    features = session.scalars(
        select(SecurityDailyFeature)
        .where(
            SecurityDailyFeature.as_of_date == as_of_date,
            SecurityDailyFeature.calculation_version == feature_version,
            SecurityDailyFeature.price_date == as_of_date,
        )
        .order_by(SecurityDailyFeature.ticker)
    ).all()
    if not features:
        raise RuntimeError(
            f"No {feature_version} feature snapshot exists for {as_of_date}"
        )
    constructive = _constructive_volume_tickers(
        session,
        as_of_date,
        {item.ticker for item in features},
        configuration,
    )
    candidates = [
        candidate
        for feature in features
        if (
            candidate := score_feature(
                feature,
                constructive_volume=feature.ticker in constructive,
                excluded_sic_prefixes=excluded_prefixes,
                configuration=configuration,
            )
        )
        is not None
    ]
    candidates.sort(key=lambda item: (-item["score"], item["ticker"]))
    source_cutoff = max(
        (
            item.source_data_cutoff_utc
            for item in features
            if item.source_data_cutoff_utc is not None
        ),
        default=None,
    )
    summary = {
        "model": replay_model,
        "configuration_fingerprint": fingerprint,
        "feature_rows_reviewed": len(features),
        "raw_candidate_count": len(candidates),
        "qualified_count": sum(item["stage"] == "qualified" for item in candidates),
        "incomplete_count": sum(item["stage"] == "incomplete" for item in candidates),
        "rejected_count": sum(item["stage"] == "rejected" for item in candidates),
        "actionable_count": sum(item["action"] == "actionable" for item in candidates),
        "qualitative_review_performed": False,
    }
    result = record_strategy_run(
        session,
        strategy_key=strategy_key,
        strategy_version=strategy_version,
        strategy_name=metadata["name"],
        as_of_date=as_of_date.isoformat(),
        run_type="backtest",
        idempotency_key=(
            f"{strategy_key}:{strategy_version}:{as_of_date}:"
            f"{feature_version}:{replay_model}:{fingerprint[:16]}"
        ),
        configuration=configuration,
        filters={
            "exclude_industry_groups": configuration["universe"][
                "exclude_industry_groups"
            ]
        },
        candidates=candidates,
        summary=summary,
        feature_calculation_version=feature_version,
        data_cutoff_at_utc=source_cutoff.isoformat() if source_cutoff else None,
        notes=(
            "Mechanical point-in-time replay. Historical qualitative catalyst, "
            "going-concern, customer concentration, spread, and float reviews are "
            "not inferred."
        ),
    )
    return {**result, "as_of_date": as_of_date.isoformat(), **summary}


def replay_strategy_range(
    session: Session,
    start_date: date,
    end_date: date,
    *,
    resume: bool = False,
    configuration_path: str | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if start_date > end_date:
        raise ValueError("Replay start date must be on or before end date")
    configuration = replay_configuration(configuration_path, configuration)
    metadata = configuration["strategy"]
    strategy_key = metadata["key"]
    strategy_version = metadata["version"]
    feature_version = metadata["feature_calculation_version"]
    replay_model = metadata["replay_model"]
    fingerprint = configuration["configuration_fingerprint"]
    sessions = session.scalars(
        select(DailyPriceBar.trade_date)
        .where(
            DailyPriceBar.ticker == "QQQ",
            DailyPriceBar.trade_date.between(start_date, end_date),
        )
        .distinct()
        .order_by(DailyPriceBar.trade_date)
    ).all()
    completed: list[str] = []
    skipped: list[str] = []
    actionable = raw_candidates = 0
    for market_date in sessions:
        key = (
            f"{strategy_key}:{strategy_version}:{market_date}:"
            f"{feature_version}:{replay_model}:{fingerprint[:16]}"
        )
        if resume and session.scalar(
            select(StrategyRun.run_id).where(StrategyRun.idempotency_key == key)
        ):
            skipped.append(market_date.isoformat())
            continue
        result = replay_strategy_date(session, market_date, configuration)
        completed.append(market_date.isoformat())
        actionable += result["actionable_count"]
        raw_candidates += result["raw_candidate_count"]
    source_runs = session.scalars(
        select(StrategyRun.payload_hash)
        .where(
            StrategyRun.run_type == "backtest",
            StrategyRun.as_of_date.between(start_date, end_date),
            StrategyRun.feature_calculation_version == feature_version,
            StrategyRun.idempotency_key.like(
                f"{strategy_key}:{strategy_version}:%:{feature_version}:"
                f"{replay_model}:{fingerprint[:16]}"
            ),
        )
        .order_by(StrategyRun.as_of_date)
    ).all()
    source_runs_hash = hashlib.sha256(
        json.dumps(source_runs, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "replay_model": replay_model,
        "feature_calculation_version": feature_version,
        "configuration_fingerprint": fingerprint,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "market_sessions": len(sessions),
        "completed_sessions": len(completed),
        "skipped_sessions": len(skipped),
        "raw_candidates_in_completed_sessions": raw_candidates,
        "actionable_signals_in_completed_sessions": actionable,
        "source_runs_hash": source_runs_hash,
        "completed_dates": completed,
        "skipped_dates": skipped,
    }
