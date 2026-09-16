---
name: swing-trading-stock-screener
description: Finalize the repository's daily dynamic swing-buy alert after MCP preparation, using its authoritative research scopes and v0.8 validation and production handoff.
---

# Swing Trading Stock Screener

Use this skill for the repository's production `dynamic_swing_buy_alerts` daily workflow. The MCP preparation result is the source of truth for research scope. This is a workflow/skill version `1.6.0`; keep `strategy_version` and `decision_contract_version` at `0.8`.

## Prepare and research

Call `prepare_daily_stock_alert` for the current expected market date before doing qualitative work. It returns a durable `preparation_id`; a repeated call reuses the same deterministic checkpoint rather than losing prior work.

Treat the returned scopes as authoritative:

- Perform fresh qualitative research only for tickers in `deep_research_queue`. Do not independently add a ticker to fresh research.
- Use the reusable evidence and its stated evidence status for `carry_forward_queue`; do not represent it as newly researched.
- Finalize `deterministic_only_queue` without fresh qualitative research. A name without sufficiently current qualitative evidence may be `RADAR` or `NOT_ELIGIBLE`, but must not be `BUY_NOW` or `ALMOST_READY`.
- Reassess `dropped_candidate_reviews` from the supplied deterministic context. A drop from the raw pool alone is not a reason to expand fresh research.

Call `get_daily_stock_alert_preparation_status` and research only its outstanding deep-research tickers. Immediately call `record_daily_stock_alert_research` after each completed ticker, including its evidence, required-dimension completion, qualitative blockers/flags, and any per-ticker decision detail. Never hold completed research only in chat context.

Do not construct a canonical candidate array, summary, or report in ChatGPT. Call `finalize_daily_stock_alert_preparation`; the repository loads the durable snapshot, carry-forward evidence, and saved research, constructs every expected v0.8 candidate exactly once, generates the report, and validates the exact resulting payload.

## Validate and finalize

`finalize_daily_stock_alert_preparation` calls the existing mandatory validation path. Only after it reports validation success may `run_finalized_daily_stock_alert_preparation` be called. Do not use the old manual-payload production route for scheduled alerts.

Keep `verify_mailbox=false`. SMTP acceptance remains required by the production workflow, but mailbox receipt verification is intentionally disabled because it cannot be verified reliably.

For the scheduled production sequence and its stop conditions, read [references/scheduled-production.md](references/scheduled-production.md).
