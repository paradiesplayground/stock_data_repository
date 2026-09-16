# Codex handoff — Stock Data Repository — through September 15, 2026

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

- The August 25 scheduled attempt initially stopped safely because prices were current through August 24 while derived features were still current only through August 21. The feature job completed successfully at 5:30:46 AM, after the alert had already checked freshness.
- The user confirmed the scheduled-task timing/retry fix is complete, but the next unattended run still needs observation to prove the race is eliminated.
- A canonical August 24 alert was later present and its email was repeatedly accepted by SMTP during presentation testing. End-to-end mailbox verification was not run during this session.
- The tunnel startup race has not been permanently fixed.

## August 25 update

- Diagnosed the scheduled-alert failure as a timing race, not an ingestion or decision-workflow failure. The alert and derived-feature job were both checking/starting around 5:30 AM.
- Confirmed live freshness after feature completion: expected market date, market prices, and derived features all resolved to August 24; `ready_for_screening` became `true` with no freshness issues.
- Added deterministic company-name propagation. Preparation now records a ticker-to-company mapping and final validation places the authoritative name in each candidate payload without relying on the website to guess.
- Added focused tests for company-name propagation. Local Python compilation and Ruff passed; the broken Windows virtual environment prevented local pytest because it points to a removed Python installation.
- The company-name change was pushed as `a8fa11a`, deployed on Unraid, and the user confirmed the linked website experience works.

## Recommended next steps

1. **Prove the scheduling fix on the next unattended run.** Record the alert start time and confirm `expected_market_date`, `latest_trade_date`, and `latest_feature_date` are identical before preparation. Require `ready_for_screening=true`, then verify exactly one canonical run ID and payload hash, a populated `summary.daily_changes`, website publication status, SMTP acceptance, and mailbox verification. If freshness is initially false, confirm the task retries during the same morning rather than waiting until the next day.
2. **Run the authoritative backend regression suite after every stock-repository deployment.** From `/mnt/user/appdata/stock-data-repository/compose`, run `docker compose -p stock_data_repo run --rm contract-test sh -c "python -m pytest -q && ruff check app tests"`. Do not treat the local Windows virtual environment as authoritative until it is rebuilt against an installed Python runtime.
3. **Verify company names on the next newly created alert.** Inspect the canonical run and confirm every expected candidate with an available reference name contains `payload.company_name`; then confirm ParadiesWeb displays that name under the ticker. Missing reference names must remain explicitly unavailable rather than inferred externally.
4. **Fix tunnel recovery permanently.** Add dependency-aware readiness or retry/backoff so the tunnel reconnects after MCP becomes healthy. Test by recreating the complete stack once and confirm the tunnel reaches healthy without a manual restart.
5. **Make evidence confidence explicit upstream.** Add a structured status enum (`current_verified`, `reused_rechecked`, `incomplete`, `conflicting`, `manual_review`), a verification timestamp, and source/date completeness fields to the alert payload. Validate them before persistence so ParadiesWeb can display stored confidence instead of inferring it from prose.

## Relevant commits

- `a73e521` — Add daily stock alert changes summary
- `78a76e7` — Reduce daily alert comparison noise
- `7e558b3` — Require structured daily blocker changes
- `a8fa11a` — Persist alert candidate company names

## August 27 update

- **Implemented:** Added root-level `AGENTS.md` instructions requiring every substantive work session to update this handoff under the correct `America/Chicago` date, commit it with the related repository changes, and push it to the default branch.
- **Decision:** Handoff entries must distinguish implemented, discussed, deployed, and verified work; remain concise and public-safe; and preserve genuinely incomplete work or explicitly discussed backlog without adding generic verification tasks.
- **Status:** The repository instruction and handoff update were implemented and verified by file inspection and Git whitespace checks. Deployment is not applicable because these are documentation-only changes; alert processing, persistence, publication, and email delivery are unchanged.
- **Relevant prior commit:** `3c7594c` — Accept integral JSON values for `remaining_gate_count` and add clearer candidate-specific validation errors.

## August 28 update

- **Problem diagnosed:** The August 27 alert passed dry validation but production persistence rejected it because `dynamic_swing_buy_alerts:0.7` already referred to a different immutable configuration. No August 27 run was persisted, published, emailed, or mailbox-verified.
- **Implemented:** Advanced the hybrid production workflow to repository-owned strategy v0.8 with an identity-bearing versioned configuration. The prior v0.7 profile remains preserved for audit history.
- **Implemented:** Dry validation now queries any existing strategy definition and rejects configuration or skill-fingerprint collisions before production persistence, without creating rows or triggering external delivery.
- **Decision:** Prior alert scope and daily-change comparisons continue across strategy-version boundaries so the corrective version advance does not reset dropped-candidate review or the daily comparison.
- **Verified locally:** The full backend suite passed with 160 tests, Ruff passed, Python compilation passed, and Git whitespace checks passed.
- **Current status:** The fix is implemented and locally verified but not yet deployed. The August 27 alert must be prepared and validated again under v0.8; the earlier v0.7 validation hash is intentionally obsolete. Production persistence, website publication, SMTP acceptance, and mailbox verification remain incomplete for that alert.
- **Commit description:** Fix production strategy-version validation and advance the hybrid alert to v0.8.

## August 31 update

- **Problem diagnosed:** The unattended August 28 alert was blocked before repository execution because `prepare_daily_stock_alert` and `validate_daily_stock_alert` were published without MCP safety annotations. MCP clients therefore treated these side-effect-free tools as potentially destructive, which requires an approval that a scheduled task cannot provide. Freshness, the database, and preparation logic were healthy; an interactive preparation call returned the expected v0.8 template for all 40 current and dropped tickers.
- **Implemented:** Marked both preparation and validation as read-only, non-destructive, idempotent, and closed-world in the MCP manifest. Added regression assertions against the registered tool metadata so the safety contract cannot silently regress.
- **Verified:** Focused local Ruff and Git whitespace checks pass. GitHub CI passed the full Python test suite, Ruff, and the production Docker test stage. The existing Windows virtual environment still points to a removed Python installation, so pytest was not run through that environment.
- **Deployment update:** The user reported the MCP safety-annotation deployment working. Commit `15f6d9b` remains the implementation commit for that issue.
- **Second problem diagnosed:** The August 28 payload passed validation but production compared JSON numeric representations byte-for-byte when enforcing the immutable strategy definition. Equivalent values such as integer `5` and floating `5.0` could change representation across the MCP validation-response handoff and receive different hashes even though the configuration was semantically unchanged.
- **Implemented:** Added one shared, JSON-semantic configuration fingerprint for both dry validation and production definition checks. It preserves object and array structure, string and Boolean types, and exact numeric values while treating equivalent JSON number representations equally. The full run payload hash remains unchanged. Preparation now reports the canonical configuration fingerprint for traceability.
- **Regression coverage:** Added a canonical v0.8 prepare → validate → unchanged production handoff test, strict changed-configuration rejection in both validation and production, numeric-representation fingerprint coverage, and preparation fingerprint coverage. Screening, scoring, classifications, decision semantics, email, and website rendering were not changed.
- **Verified:** Commit `ede442e` passed 164 tests and Ruff in both the standard GitHub CI run and the production Docker test stage. Focused local Ruff and Git whitespace checks also pass; local pytest remains unavailable because the Windows virtual environment references a removed Python installation.
- **Current status:** The configuration-fingerprint fix is pushed to `main` but is not yet deployed. No August 28 production run was persisted, published, or emailed during this work.

## September 15 update

- **Problem diagnosed:** A production alert passed validation but returned a different payload hash after the stateless MCP handoff. The earlier repair normalized numeric representations only for the immutable strategy configuration; the complete v0.8 run hash still distinguished equivalent JSON numbers such as `5` and `5.0` outside that configuration.
- **Implemented:** v0.8 run hashing now normalizes equivalent JSON number representations throughout the complete canonical payload before hashing. Legacy contract hashes remain unchanged, and genuine changes to numeric values, structure, strings, or Booleans still produce a different identity.
- **Regression coverage:** The canonical prepare → validate → production test now simulates every integer being re-encoded as a float during transport and requires production to preserve the validation hash. The full local suite passed with 164 tests; focused Ruff and Git whitespace checks passed.
- **Production status:** The reported run was already recorded, read back, published, and accepted by SMTP before the mismatch was observed. It was not resent or republished. This repair is committed locally but is not deployed or verified on Unraid yet.
- **Commit description:** Preserve v0.8 validation hashes across JSON transport.

## September 16 update

- **Implemented:** Redesigned daily-alert preparation under skill/workflow version 1.6.0 without changing strategy version 0.8 or the decision contract. The complete current-plus-dropped canonical ticker scope remains mandatory, while preparation now separates bounded fresh deep research, carry-forward evidence, deterministic-only handling, and dropped-candidate reviews.
- **Implemented:** Fresh qualitative research is capped at 12 names in a normal regime and 6 in a SPY BLOCK regime. New names, newer filings, prior BUY_NOW/ALMOST_READY conclusions, stale evidence, and material actionable setup changes are prioritized; deterministic risk flags, missing evidence for clearly non-actionable names, and raw-pool drops alone no longer trigger deep research.
- **Safety decision:** A candidate lacking current qualitative confirmation may remain RADAR or NOT_ELIGIBLE, but final validation rejects BUY_NOW or ALMOST_READY unless it records fresh research or explicitly reusable current evidence. The preparation result also supplies a 20-name detailed-report scope with a compact remainder summary.
- **Verified locally:** Added focused regression coverage for queue causes, budgets, dropped names, canonical coverage, report scope, and actionable-evidence safety. The full Python suite passed (171 tests) and Python compilation passed using the bundled runtime.
- **Current status:** No production run, publication, email, or mailbox action was performed. A live read-only preparation attempt could not reach the local default database hostname from this checkout, so current-market queue counts and tickers remain unverified until the deployed environment runs preparation.
- **Implemented:** Added durable daily-alert preparation checkpoints and per-ticker deep-research records. Preparation now returns a stable ID and reuses its deterministic snapshot; completed evidence, source metadata, dimension completion, blockers, flags, and per-ticker decision detail survive an interrupted chat execution.
- **Implemented:** Added server-side finalization that loads the checkpoint, resolves carry-forward evidence, assembles all canonical v0.8 candidates (including dropped and deterministic-only names), generates the report, and invokes the existing validation path before enabling explicit production. New status and MCP operations expose outstanding research, finalization, validation, and production state.
- **Verified locally:** Added interruption/resume, repeated preparation, 48-ticker assembly, deterministic-only safety, validation-failure, and idempotent-production-path coverage. The complete suite passed with 177 tests; Ruff and Git whitespace checks passed.
- **Deployment status:** Not deployed. No September 15 alert was rerun, persisted, published, emailed, or otherwise modified.
- **Implemented:** Added the repository-tracked `swing-trading-stock-screener` skill and scheduled-production reference for workflow version 1.6.0. They make MCP preparation scopes authoritative: only `deep_research_queue` receives fresh qualitative work; carry-forward evidence is reused as supplied; deterministic-only and dropped names remain canonical without scope expansion.
- **Safety decision:** The skill requires exact expected-ticker coverage, preservation of `run_template`, validation before production, and passing the exact `validated_run_payload` unchanged. It preserves strategy and decision-contract version 0.8 and explicitly keeps `verify_mailbox=false`, because mailbox receipt cannot be verified.
- **Verified locally:** The skill front matter, required v1.6.0 scope and handoff invariants, scheduled-reference link, and Git whitespace were checked. This documentation/skill change did not run production, publishing, or email.
- **Implemented:** Refined the v1.6.0 skill around the deployed durable preparation API: status-driven resume, immediate per-ticker checkpoints, server-owned finalization/validation, and the distinct finalization-response versus persisted-status fields.
- **Safety decision:** Scheduled orchestration remains thin. It must not recreate canonical payloads, repeat checkpointed research, manually call the legacy validation/production route, enable mailbox verification, or reopen a historical date merely to test resumption.
- **Current status:** This guidance change intentionally does not rerun the September 15 alert. Deployment is required so the scheduled task can use the durable checkpoint MCP operations.
- **Deployment blocker:** The Unraid host was reachable by name but its SSH deployment service was unavailable from this workstation, so the guarded update script could not be invoked. No September 15 alert was rerun and no persistence, publication, or email action occurred.
- **Implemented:** Kept the scheduler reference intentionally declarative and moved the complete durable-resume decision table into the repository-tracked skill. The skill now explicitly stops on a persisted invalid finalization rather than retrying or bypassing it, and resumes only from durable status plus saved per-ticker checkpoints.
- **Verified / deployment status:** The skill resume-contract smoke test and Git whitespace check passed. The local Python environment remains unavailable for the repository suite, and the authoritative deployment-side contract build did not begin because the Unraid SSH endpoint again refused the connection. Commit `994db96` is pushed to `main`; no alert was run or changed.
