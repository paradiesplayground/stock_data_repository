---
name: swing-trading-stock-screener
description: Finalize the repository's daily dynamic swing-buy alert after MCP preparation, using its authoritative research scopes and v0.8 validation and production handoff.
---

# Swing Trading Stock Screener

Use this skill for the repository's production `dynamic_swing_buy_alerts` daily workflow. The MCP preparation result is the source of truth for research scope. This is a workflow/skill version `1.6.0`; keep `strategy_version` and `decision_contract_version` at `0.8`.

## Prepare and research

Call `prepare_daily_stock_alert` for the current expected market date before doing qualitative work. It returns a durable `preparation_id`; a repeated call reuses the latest preparation revision rather than losing prior work.

Treat the returned scopes as authoritative:

- Perform fresh qualitative research only for tickers in `deep_research_queue`. Do not independently add a ticker to fresh research.
- Use the reusable evidence and its stated evidence status for `carry_forward_queue`; do not represent it as newly researched.
- Finalize `deterministic_only_queue` without fresh qualitative research. A name without sufficiently current qualitative evidence may be `RADAR` or `NOT_ELIGIBLE`, but must not be `BUY_NOW` or `ALMOST_READY`.
- Reassess `dropped_candidate_reviews` from the supplied deterministic context. A drop from the raw pool alone is not a reason to expand fresh research.

After preparation, call `get_daily_stock_alert_preparation_status` only to inspect operational progress. The server worker owns all transitions after research; a scheduler must not duplicate them:

- If `production_status` is `completed`, report the idempotent result and stop.
- Use the explicit `stage`, per-transition statuses, `last_error`, and `next_action` to report an incomplete or failed production pipeline. Do not call a production tool to “help” it along.
- If `outstanding_deep_research_tickers` is non-empty, research only those tickers. Immediately call `record_daily_stock_alert_research` after each completed ticker. Supply its evidence, mark every required dimension complete, and include blockers, flags, and any per-ticker decision detail. Never hold completed research only in chat context.
- Once the final required checkpoint is recorded, stop. The server finalizes, validates, persists the stored canonical payload, publishes it, and sends email asynchronously.

Checkpoint only per-ticker research. Do not use `candidate_decision` to construct a canonical candidate array, summary, or report. A resume must reuse the latest saved `preparation_id`; never restart work merely because an earlier execution ended.

### Explicit same-date reruns

A completed preparation remains immutable. If, and only if, the user explicitly asks for a genuinely new run for the current expected market date after an earlier production run already completed, call `create_daily_stock_alert_revision` with a concise reason that reflects the user's request. This creates a fresh deterministic preparation revision with a new `preparation_id` and a distinct production `idempotency_key`.

Do not call `create_daily_stock_alert_revision` for ordinary retries, interrupted chats, validation failures, scheduler resumes, or because the same preparation was returned again. Those cases must resume the existing preparation. A scheduled unattended run must never invent a revision merely because production for that date already exists.

After creating a revision, use the returned revision's `preparation_id` and follow the normal status/research/finalization/production state machine. Do not reuse research checkpoints from the completed preparation unless the new preparation itself places that evidence in `carry_forward_queue`.

Do not construct a canonical candidate array, summary, or report in ChatGPT. The repository loads the durable snapshot, carry-forward evidence, and saved research, constructs every expected v0.8 candidate exactly once, generates the report, and validates the exact resulting payload.

## Validate and finalize

`finalize_daily_stock_alert_preparation` and `run_finalized_daily_stock_alert_preparation` remain administrative/manual recovery endpoints. They are not part of the scheduled ChatGPT workflow. Do not call `validate_daily_stock_alert` or use the old manual-payload production route for scheduled alerts.

Keep `verify_mailbox=false`. SMTP acceptance remains required by the production workflow, but mailbox receipt verification is intentionally disabled because it cannot be verified reliably.

Never rerun a historical alert merely to demonstrate resumption. Use only the current expected market date, and report the preparation ID plus the exact stopped stage if a current run cannot continue.

For the scheduler's intentionally narrow invocation contract, read [references/scheduled-production.md](references/scheduled-production.md).
