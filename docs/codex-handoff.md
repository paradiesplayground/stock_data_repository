# Codex handoff — August 24, 2026

## What was accomplished

- Added a deterministic **What changed since yesterday?** summary to the stock-alert workflow. It reports candidate additions/removals, classification and trigger-distance changes, stop breaches, blocker changes, new evidence, and names needing attention.
- Added shared expandable ticker details throughout the Paradies Playground stock-signal tables.
- Added a price-structure view showing stored price levels, including current price, available moving averages, official trigger, structural stop, and official or clearly labeled speculative targets.
- Added an override calculator for hypothetical position sizing using a preferred entry and maximum dollar risk.
- Added evidence-confidence labels to explain whether ticker evidence is current, rechecked, incomplete, conflicting, or requires manual review.
- Built, deployed, and live-tested the stock repository and Paradies Playground changes on Unraid.

## Major code and configuration changes

- `stock_data_repository` now compares each finalized alert with the prior canonical alert and stores the result under `summary.daily_changes`. The comparison is also inserted into `report_markdown` without changing the underlying decision contract.
- `ParadiesWeb` now uses one shared ticker-detail component across candidate and exclusion tables.
- The website gained reusable calculation modules for override sizing and evidence-confidence classification, with focused unit tests.
- No secrets, credentials, network exposure, or production scheduling configuration were changed.

## Important decisions made

- Official classifications and trade decisions remain repository-owned. Website visualizations and override calculations are informational and never promote or reclassify a ticker.
- Speculative 1R/2R levels are explicitly labeled as hypothetical risk-planning values.
- Missing historical metrics are shown as unavailable instead of being reconstructed. For example, older alerts without stored EMA values display a partial price structure.
- Evidence-confidence precedence is: conflicting disclosure, incomplete data, explicitly reused/rechecked evidence, fully sourced and dated evidence, then manual review.
- The proposed Attention list was intentionally deferred at the user's request.

## Bugs and problems discovered

- The stock-data tunnel can start before the MCP service is ready, remain unhealthy after a connection-refused error, and require a tunnel restart after MCP becomes healthy.
- Older stored alerts often lack complete EMA, filing-link, or evidence-date data, limiting the detail and confidence labels available for those runs.
- ParadiesWeb still has unrelated pre-existing full-lint findings in the mailbox and Markdown test files; focused lint for all work completed today passed.

## What worked and what did not

- Stock repository contract verification passed with 146 tests and Ruff on Unraid.
- ParadiesWeb finished with 35 passing unit tests, focused ESLint checks, TypeScript validation, and a successful production build.
- Live browser checks confirmed ticker details, price structure, override calculations, and evidence-confidence labels on the deployed site.
- Restarting the stock-data tunnel after MCP became healthy fixed the tunnel health problem. The original startup ordering did not recover automatically.

## Anything still incomplete

- The first normal scheduled alert run using the new daily-change data has not yet been observed end to end. Manual validation succeeded, but scheduled publication, email delivery, and mailbox verification should still be confirmed on the next real run.
- The Attention list remains deferred.
- Evidence confidence is conservatively derived from stored fields. Future payloads would benefit from an explicit evidence-confidence status and structured verification metadata.
- The tunnel startup race has been identified but not permanently fixed in Compose or the tunnel client.

## Recommended next steps

1. Observe the next scheduled stock alert and verify the canonical run, daily-change summary, website publication, SMTP acceptance, and mailbox receipt.
2. Fix the tunnel startup race with dependency-aware readiness or automatic retry behavior.
3. Add explicit evidence-confidence and verification metadata to the stock-alert payload so the website does not need to infer it.
4. Revisit the Attention list only when it becomes a priority.
5. Consider simplifying the daily email to the decision, key changes, three closest setups, stop breaches, and a link to the full analysis.

## Relevant commits

- `stock_data_repository`: `a73e521`, `78a76e7`, `7e558b3`
- `ParadiesWeb`: `e38ec55`, `908c1c1`, `4f56308`, `6f02ae9`, `b42391a`
