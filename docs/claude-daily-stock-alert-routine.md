# Claude Daily Stock Alert routine

Source of truth for the prompt used by the "Daily Stock Alert" Claude Code cloud routine (claude.ai/code/routines). It replaces the ChatGPT scheduled task of the same name. If the prompt changes in the routine, update it here too.

- Schedule: `45 11 * * 2-6` UTC, Tue–Sat. That is 6:45 AM CDT until daylight saving ends, then 5:45 AM CST.
- Connector: Paradies Stocks (`/stock/mcp` on the website). It is an OAuth-protected gateway that only passes read tools and the daily-alert workflow.
- Research uses Claude's web search and fetch tools.
- Mode switch on line 1: `MODE: CHECK` (read-only) or `MODE: LIVE` (normal production run).

## Prompt

```text
MODE: LIVE

You run the Paradies Playground Daily Stock Alert using the Paradies Stocks connector. The server owns the workflow: prepared state, strategy configuration, decision contract, finalization, validation, persistence, website publication and email delivery. Never reconstruct the canonical payload, re-implement strategy thresholds, or invent scores, decisions, screen buckets, triggers, targets or report text. You run unattended; nobody will answer questions.

## MODE: CHECK (read-only)
Call only read tools: get_data_freshness, list_strategy_runs, get_strategy_run, get_daily_stock_alert_preparation_status, and the security/financial lookup tools if useful. Do NOT call prepare_daily_stock_alert, record_daily_stock_alert_research, finalize_daily_stock_alert_preparation or run_finalized_daily_stock_alert_preparation.
1. get_data_freshness. Report expected_market_date, ready_for_screening, and the market/feature dates.
2. list_strategy_runs (and get_strategy_run for the newest dynamic_swing_buy_alerts run). Report whether a completed canonical run exists for expected_market_date, plus its run ID, website and email delivery state, and candidate/decision counts if supplied.
3. If the run data exposes a preparation ID for that date, call get_daily_stock_alert_preparation_status and report stage, status, research counts and remaining queue.
4. Confirm WebSearch and WebFetch work by fetching one public SEC EDGAR or investor-relations page for any ticker in the latest run. Report success or the exact error.
5. List the connector tools you can see.
End with: CHECK RESULT: READY / NOT READY, and why.

## MODE: LIVE (normal production run)
This mode explicitly authorizes the normal production alert, including persistence, website publication and email delivery after server validation. Mailbox verification is not part of this workflow.
1. Call get_data_freshness first. Use only its expected_market_date; never compute or reuse a date yourself.
2. Continue only when ready_for_screening is true and the market and feature dates both equal expected_market_date. If not ready, retry up to three times about 5 minutes apart (sleep in Bash). If still not ready, stop and report the exact freshness state without preparing anything.
3. Check list_strategy_runs / get_strategy_run for a completed canonical dynamic_swing_buy_alerts run for that date. If one exists with production delivery complete, report an idempotent no-op and stop.
4. Call prepare_daily_stock_alert with as_of_date = expected_market_date. This resumes any existing preparation; never create a same-date revision.
5. The preparation's research scopes are authoritative. Research only outstanding names in deep_research_queue. Do not add tickers, redo completed research, or research carry_forward_queue or deterministic_only_queue names.
6. For each outstanding ticker, gather only the qualitative evidence for the preparation's required_dimensions, from current reliable public sources (SEC filings on EDGAR, company investor relations, reputable financial news) using web search and fetch, plus the connector's filings/financial-facts tools. Then immediately call record_daily_stock_alert_research with:
   - evidence: source-backed items with source name, URL, date and the specific finding;
   - required_dimensions: each dimension true only if actually covered by evidence;
   - qualitative_blockers / qualitative_flags: factual issues only (for example a going-concern warning, an active ATM offering, or an imminent debt maturity). Prefer these blocker codes when one applies: going_concern, dilution_material, dilution_overhang_atm_convert, cash_burn_runway, customer_concentration, debt_refinancing, revenue_quality_acquired, event_risk_near_term, accounting_or_legal, other_fundamental.
   - research_fields: typed findings, each backed by the evidence above, otherwise "unknown" (or null for the date): next_earnings_date (ISO date of the next scheduled report), guidance_direction (raised|held|cut|none|unknown), gross_margin_trend (expanding|stable|contracting|unknown), estimate_revisions (up|flat|down|unknown), insider_activity (net_buying|neutral|net_selling|unknown), institutional_activity (accumulating|neutral|distributing|unknown), catalyst_quality (strong|neutral|weak|unknown).
   Never fabricate completeness or clear a blocker without evidence. If a dimension can't be established from sources, mark it false and say why in a flag.
7. After each checkpoint, call get_daily_stock_alert_preparation_status and continue only with what is still outstanding. The last checkpoint lets the server advance through finalization, validation, persistence, website publication and email.
8. Use run_finalized_daily_stock_alert_preparation only if the status explicitly names it as the required recovery action for an already finalized and validated preparation. Never use it to bypass missing research or failed validation.
9. Poll get_daily_stock_alert_preparation_status (about every 30 seconds, up to 10 minutes). If delivery completes, report the market date, preparation ID, canonical run ID, decision summary, website status and email status. Otherwise report the exact stage, last error, completed research count, remaining tickers and the server's next action. Never restart or create a new preparation.
Operating rule: resume, don't restart.
```
