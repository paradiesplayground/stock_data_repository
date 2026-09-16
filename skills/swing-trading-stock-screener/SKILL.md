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

After preparation, call `get_daily_stock_alert_preparation_status` and follow its durable state. This is the complete resume decision point; a scheduler must not duplicate it:

- If `production_status` is `completed`, report the idempotent result and stop.
- If `finalization_complete` is true and `validation_status` is `valid`, call `run_finalized_daily_stock_alert_preparation`; do not re-finalize or rebuild the payload.
- If `finalization_complete` is true and `validation_status` is not `valid`, stop and report the persisted validation state. Do not retry finalization or bypass it with a manual payload.
- If `outstanding_deep_research_tickers` is non-empty, research only those tickers. Immediately call `record_daily_stock_alert_research` after each completed ticker. Supply its evidence, mark every required dimension complete, and include blockers, flags, and any per-ticker decision detail. Never hold completed research only in chat context.
- If no deep research is outstanding and finalization is incomplete, call `finalize_daily_stock_alert_preparation`, then use its `validated.status` result or a fresh status read to choose the next state above.

Checkpoint only per-ticker research. Do not use `candidate_decision` to construct a canonical candidate array, summary, or report. A resume must reuse the same `preparation_id` and saved deterministic snapshot; never restart work merely because an earlier execution ended.

Do not construct a canonical candidate array, summary, or report in ChatGPT. Call `finalize_daily_stock_alert_preparation`; the repository loads the durable snapshot, carry-forward evidence, and saved research, constructs every expected v0.8 candidate exactly once, generates the report, and validates the exact resulting payload.

## Validate and finalize

`finalize_daily_stock_alert_preparation` server-assembles the canonical payload and calls the existing mandatory validation path. Its response reports the result in `validated.status`; the status operation exposes the persisted result as `validation_status`. Only after either is `valid` may `run_finalized_daily_stock_alert_preparation` be called. Do not call `validate_daily_stock_alert` or use the old manual-payload production route for scheduled alerts.

Keep `verify_mailbox=false`. SMTP acceptance remains required by the production workflow, but mailbox receipt verification is intentionally disabled because it cannot be verified reliably.

Never rerun a historical alert merely to demonstrate resumption. Use only the current expected market date, and report the preparation ID plus the exact stopped stage if a current run cannot continue.

For the scheduler's intentionally narrow invocation contract, read [references/scheduled-production.md](references/scheduled-production.md).
