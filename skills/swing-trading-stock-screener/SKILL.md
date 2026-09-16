---
name: swing-trading-stock-screener
description: Finalize the repository's daily dynamic swing-buy alert after MCP preparation, using its authoritative research scopes and v0.8 validation and production handoff.
---

# Swing Trading Stock Screener

Use this skill for the repository's production `dynamic_swing_buy_alerts` daily workflow. The MCP preparation result is the source of truth for research scope. This is a workflow/skill version `1.6.0`; keep `strategy_version` and `decision_contract_version` at `0.8`.

## Prepare and research

Call `prepare_daily_stock_alert` for the current expected market date before doing qualitative work. Preserve every field in its returned `run_template`; it defines the strategy identity, idempotency, filters, canonical scope, and preparation metadata.

Treat the returned scopes as authoritative:

- Perform fresh qualitative research only for tickers in `deep_research_queue`. Do not independently add a ticker to fresh research.
- Use the reusable evidence and its stated evidence status for `carry_forward_queue`; do not represent it as newly researched.
- Finalize `deterministic_only_queue` without fresh qualitative research. A name without sufficiently current qualitative evidence may be `RADAR` or `NOT_ELIGIBLE`, but must not be `BUY_NOW` or `ALMOST_READY`.
- Reassess `dropped_candidate_reviews` from the supplied deterministic context. A drop from the raw pool alone is not a reason to expand fresh research.

Classify every ticker in `run_template.summary.preparation_scope.expected_candidate_tickers` exactly once. Do not omit a deferred, deterministic-only, or dropped ticker, and do not add a ticker outside that expected scope. Complete the canonical candidates, evidence, summary, and `report_markdown` while following the supplied report scope.

## Validate and finalize

Call `validate_daily_stock_alert` before any production action. If validation succeeds, pass the returned `validated_run_payload` unchanged as `run_payload` to `run_daily_stock_alert`; do not reconstruct, normalize, or edit it between calls.

Keep `verify_mailbox=false`. SMTP acceptance remains required by the production workflow, but mailbox receipt verification is intentionally disabled because it cannot be verified reliably.

For the scheduled production sequence and its stop conditions, read [references/scheduled-production.md](references/scheduled-production.md).
