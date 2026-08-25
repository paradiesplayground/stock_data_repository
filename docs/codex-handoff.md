# Codex handoff — Stock Data Repository — August 24, 2026

## What was accomplished

- Added a deterministic **What changed since yesterday?** summary to each stock alert.
- The comparison reports candidate additions/removals, classification and trigger-distance changes, stop breaches, blocker changes, new evidence, and names needing attention.
- Built, deployed, and manually validated the updated service on Unraid.

## Major code and configuration changes

- The repository now compares each finalized alert with the prior canonical alert.
- Results are stored under `summary.daily_changes` and inserted into `report_markdown` idempotently.
- Comparison noise was reduced and blocker changes must now be structured before they appear in the summary.
- No secrets, credentials, network exposure, or scheduling configuration were changed.

## Important decisions made

- The repository remains the source of truth for classifications, comparisons, persistence, publication, and email delivery.
- Daily changes are derived from canonical stored alerts rather than prompt text or website calculations.
- Existing decision-contract compatibility was preserved.

## Bugs and problems discovered

- The tunnel can start before MCP is ready, receive a connection-refused error, and remain unhealthy instead of recovering automatically.
- Restarting the tunnel after MCP becomes healthy resolves the immediate problem.

## What worked and what did not

- The Unraid contract-test image passed 146 tests and Ruff.
- A manual no-publish validation produced a concise, correct daily-change summary without persisting or emailing anything.
- Automatic tunnel recovery after the startup race did not work.

## Anything still incomplete

- The first normal scheduled run using daily changes has not been observed end to end.
- Scheduled publication, SMTP acceptance, and mailbox verification still need confirmation on the next real run.
- The tunnel startup race has not been permanently fixed.

## Recommended next steps

1. Observe the next scheduled alert and verify the canonical run, daily-change summary, website publication, SMTP acceptance, and mailbox receipt.
2. Add dependency-aware readiness or automatic retry behavior for the tunnel.
3. Consider adding explicit evidence-confidence and verification metadata to future alert payloads.

## Relevant commits

- `a73e521` — Add daily stock alert changes summary
- `78a76e7` — Reduce daily alert comparison noise
- `7e558b3` — Require structured daily blocker changes
