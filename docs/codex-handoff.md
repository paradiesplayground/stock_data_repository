# Codex handoff — Stock Data Repository — through September 18, 2026

## 2026-09-25 — Durable daily-alert production state machine

- **Implemented:** Extended `DailyAlertPreparation` with explicit overall stage/status, last error, and independent finalization, validation, canonical-run, website, and email completion states. The status endpoint now provides a compact operational view and next action without reconstructing state from MCP history.
- **Implemented:** Recording the final required deep-research checkpoint asks the server to advance the stored preparation. The worker also advances every incomplete preparation at startup and every five minutes, so a container restart, validation failure, persistence failure, or delivery failure resumes from the durable boundary.
- **Implemented:** Production now records only the stored validated `final_payload`; MCP production payload submission is rejected. Canonical persistence is read-back checked before independent website-only and email-only requests. Delivery requests carry a stable destination-specific idempotency key, and acknowledged website/email receipts are stored separately.
- **Implemented:** Normal scheduled ChatGPT workflow now ends after `record_daily_stock_alert_research`; finalization and the administrative recovery endpoint remain server-owned/manual recovery tools. Explicit same-date revisions remain limited to `create_daily_stock_alert_revision`.
- **Regression coverage:** Added transition-retry tests for canonical persistence, website publication, and email failure. A retry preserves one canonical run and skips already-complete destinations. Guarded contract testing caught and corrected only test expectations for already-persisted delivery stages before the final deployment retry.
- **Verified locally:** Python compilation and Git whitespace checks pass. The checked-in virtualenv references a removed interpreter and the bundled runtime has no pytest/Ruff. No preparation, production run, publication, or email was executed.
- **Deployment recovery:** The guarded contract image later passed 216 tests plus Ruff, but the migration stopped cleanly before service recreation because its revision identifier exceeded the existing 32-character Alembic version column. The migration was rolled back transactionally; a shortened revision identifier is being deployed next.
- **Compatibility recovery:** The first state-machine worker startup exposed 10 legacy preparations that had completed under the old combined-delivery model but had no destination receipts. The worker was stopped before further retries; a follow-up migration marks only those legacy/default-or-failed delivery rows complete, avoiding historical publication or email replay. The receiving website now supports destination-specific delivery.
- **Deployed / verified:** The guarded Unraid contract suite passed 216 tests plus Ruff. Functional commit `f70cf0e` applied the compatibility migration; all 10 legacy preparations are now `complete` and the worker is running without replaying them. During the first startup before the worker was stopped, two legacy preparations (September 17 and September 24) reached SMTP acceptance once; their receipts are persisted and no incomplete preparation remains. API and MCP health are OK, and the tunnel is healthy with an initialized MCP session after its normal tunnel-only restart.

## 2026-09-24 — Canonical Daily Stock Alert presentation

- **Implemented:** Replaced generic report prose with a structured candidate presentation model shared by the canonical Markdown delivered to both the website and email. `BUY_NOW`, `ALMOST_READY`, and `RADAR` detailed candidates appear under **Watch first**; `NOT_ELIGIBLE` detailed candidates appear separately under **Excluded — worth reviewing**.
- **Implemented:** Each rendered detailed ticker now shows its effective decision and factual classification reason, positive factors, every current screen/technical/market/qualitative blocker with its clearing condition, the post-eligibility technical trigger, and invalidation level. User-facing prices, percentages, and R multiples are formatted.
- **Implemented:** Mechanical screen explanations use the configured thresholds. The ALAB regression fixture for preparation `242477b3-2eca-4653-9f12-6354747f8ee9` confirms that a `-16.3394%` 12-week change explicitly fails the required `<= -20.0%` threshold and cannot be cured by crossing the technical trigger alone. Fresh research no longer renders the stale generic qualitative-confirmation condition.
- **Verified locally:** Ruff and Git whitespace checks pass. Focused pytest could not run because the checked-in Windows virtualenv references a removed Python interpreter; no alert was finalized, published, emailed, or deployed in this session.

## 2026-09-24 — Historical decline-screen experiment framework

- **Implemented:** Updated the `fallen-growth-swing-v1.2.0` historical profile to request feature calculation version `1.5.0`, matching the available simulation snapshots. Production alert configuration was not changed.
- **Implemented:** Added configurable 12-week decline modes (`price_change`, `drawdown_from_high`, `either`, and `both`) with price-change and drawdown thresholds for deterministic replay/backtesting only. Decline-only exclusions are retained as rejected counterfactual opportunities without becoming simulated signals.
- **Implemented:** Added a fixed six-scenario comparison runner plus forward 5/10/20/30-session rejected-opportunity outcomes: trigger hits, MFE/MAE in R, 2R/3R-before-stop, and later mechanical eligibility. Its returned report includes candidate-days and the requested portfolio metrics while holding all non-decline settings constant.
- **Verified and deployed:** The guarded Unraid suite passed 211 tests plus Ruff after compatibility repairs. Functional commit `d9dec20` is deployed; API health returned OK after the normal container-startup race. No production strategy, alert, or publication was changed.
- **Execution boundary:** The no-signal repair was deployed and the guarded suite then passed 212 tests plus Ruff. The price-change `<= -20%` baseline completed for 2026-01-20 through 2026-07-20 (4 signals, 1 fill/closed trade, -3.01% return); the remaining comparison batch could not be verified because the Unraid SSH endpoint became unreachable during execution. The runner is idempotent and preserves the completed immutable record; no production strategy change occurred.

## 2026-09-23 — Deterministic report enrichment

- **Implemented:** Added a server-owned 0–100 setup score for every prepared candidate, including deterministic-only names. The score uses saved growth, trend, liquidity, drawdown, trigger proximity, observed risk/reward, and available qualitative confidence; it does not change actionability.
- **Implemented:** Added a bounded top-five `report_focus_queue`, ticker-specific factual Why/Next text, and structural target/R-multiple reporting based only on saved technical levels. Missing structure is shown explicitly rather than manufacturing 1R/2R targets.
- **Implemented:** Daily changes now emit at most eight material New, Improved, Deteriorated, or Removed items, including meaningful score and trigger-distance changes with rounded values.
- **Verified locally:** Python files compile and deterministic enrichment was exercised directly. Focused pytest could not run because the checked-in Windows virtualenv points to a removed Python interpreter; no production alert, publication, or email was run.
- **Verified and deployed:** The guarded Unraid updater passed 201 tests and Ruff, then deployed functional commit `5d82392`. API and MCP are healthy; the API health endpoint returned OK. The tunnel startup race was resolved by restarting only the tunnel after MCP became healthy.
- **Verified and deployed:** Functional commit `fa38b1b` passed the guarded Unraid contract suite (202 tests plus Ruff) and is deployed. The existing September 22 revision `35448c39-57dd-49aa-b5c5-8ba9bb8a3653` finalized and validated successfully with no new preparation or research; production remains not run and no alert was published or emailed.

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
- **Implemented:** Dry validation now queries any existing strategy definition and rejects configuration or skill-fingerprint collisions before production persistence, without creating any rows or triggering external delivery.
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
- **Safety decision:** The skill requires exact expected-ticker coverage, preservation of `run_template`, validation before production, and passing the exact `validated_run_payload` unchanged. It preserves strategy and decision-contract version 0.8 and explicitly keeps `verify_mailbox=false`, because mailbox receipt cannot be verified reliably.
- **Verified locally:** The skill front matter, required v1.6.0 scope and handoff invariants, scheduled-reference link, and Git whitespace were checked. This documentation/skill change did not run production, publishing, or email.
- **Implemented:** Refined the v1.6.0 skill around the deployed durable preparation API: status-driven resume, immediate per-ticker checkpoints, server-owned finalization/validation, and the distinct finalization-response versus persisted-status fields.
- **Safety decision:** Scheduled orchestration remains thin. It must not recreate canonical payloads, repeat checkpointed research, manually call the legacy validation/production route, enable mailbox verification, or reopen a historical date merely to test resumption.
- **Current status:** This guidance change intentionally does not rerun the September 15 alert. Deployment is required so the scheduled task can use the durable checkpoint MCP operations.
- **Deployment blocker:** The Unraid host was reachable by name but its SSH deployment service was unavailable from this workstation, so the guarded update script could not be invoked. No September 15 alert was rerun and no persistence, publication, or email action occurred.
- **Implemented:** Kept the scheduler reference intentionally declarative and moved the complete durable-resume decision table into the repository-tracked skill. The skill now explicitly stops on a persisted invalid finalization rather than retrying or bypassing it, and resumes only from durable status plus saved per-ticker checkpoints.
- **Verified / deployment status:** The skill resume-contract smoke test and Git whitespace check passed. The local Python environment remains unavailable for the repository suite, and the authoritative deployment-side contract build did not begin because the Unraid SSH endpoint again refused the connection. Commit `994db96` is pushed to `main`; no alert was run or changed.
- **Implemented:** Fixed durable-finalizer gate counting so generated v0.8 candidates count a positive distance to trigger alongside failed technical and market-regime gates. This matches the validator rather than weakening it, allowing a saved completed-research preparation to resume finalization without rebuilding its research.
- **Regression coverage:** Added the AAOI-shaped case with failed technical and market-regime gates plus a positive trigger distance. Focused workflow/decision tests passed (40 tests), changed-file Ruff passed, and the full suite passed (178 tests). The saved preparation and research records were not read, modified, or deleted; deployment remains pending.
- **Deployment status:** Commit `16b517b` was pushed to `main`, but the guarded Unraid update script could not start because the configured SSH endpoint refused the connection. No production finalization, retry, persistence, publication, or email action occurred. Once deployed, the existing preparation `cf77fb68-68c2-4d7d-9862-b66e5902ca6f` can be resumed through its saved checkpoint without redoing research.
- **Implemented:** Enforced the server-owned market-regime BLOCK veto after applying any saved per-ticker decision overlay. BLOCK candidates are now forced to `NOT_ELIGIBLE` with a `rejected` screen bucket, while a legitimate `dropped` bucket is preserved; non-BLOCK candidates retain their saved decision unchanged.
- **Regression coverage:** Added explicit cases for BLOCK plus an actionable saved decision, BLOCK plus a dropped candidate, and a non-BLOCK saved actionable decision. This is a finalizer-only contract correction; preparation snapshots, saved research, qualitative evidence, scoring, targets, and risk/reward fields are untouched.
- **Current status:** The implementation and regression tests are pushed to `main` in commits `2e5a0b9` and `2bca16b`. They have not been deployed to Unraid from this session, the existing preparation checkpoint was not modified, and no production persistence, publication, or email action occurred.
- **Problem diagnosed:** The next finalization failure was caused by `strategy.skill_version` changing from 1.5.3 to 1.6.0 while the actual v0.8 trading thresholds, market-regime rules, scoring model, and decision contract remained unchanged. The saved preparation correctly retained the 1.6.0 workflow metadata, but immutable strategy-definition validation treated that operational metadata as a strategy change.
- **Implemented:** The v0.8 validation boundary now compares the submitted and registered configurations with `strategy.skill_version` removed. When that is the only difference, it aligns the submitted metadata to the registered definition before immutable-definition validation. Any actual strategy-configuration difference remains untouched and continues to require a new `strategy_version`.
- **Regression coverage / status:** Added tests for the 1.5.3→1.6.0 metadata transition, numeric-equivalent configuration values, and a genuine threshold change that must still fail. Commits `91f3dff` and `cfcbaae` are pushed to `main`; the saved preparation/research checkpoint was not modified and no production persistence, publication, or email action occurred.
- **Implemented:** Refined the BLOCK behavior so market regime controls actionability without erasing stock-specific setup quality. The finalizer now stores intrinsic setup status, screen bucket, remaining gates, and reason in candidate payload metadata while keeping the effective v0.8 status `NOT_ELIGIBLE` under BLOCK for downstream safety and contract compatibility.
- **Reporting decision:** The closest-setups report now ranks detailed names by intrinsic setup quality and explicitly renders cases such as `BUY_NOW setup (MARKET BLOCKED)`. Summary metadata separately reports effective candidate counts, intrinsic setup counts, and the number of market-blocked candidates.
- **Timing/scope decision:** Research queues, the six-name BLOCK deep-research budget, preparation snapshots, and qualitative evidence requirements are unchanged. This is a finalizer/reporting overlay only, so preserving blocked setups does not create additional qualitative research work.
- **Regression coverage:** Added tests for a blocked actionable setup retaining its intrinsic classification, hard-screen/dropped candidates remaining intrinsically non-eligible, unchanged non-BLOCK behavior, adjusted effective gate counts, and market-blocked setup/report summary rendering. Implementation commits are `8a1d07c` and `022f6aa`.
- **Verified / current status:** PR #12 passed pytest, Ruff, and the production Docker test stage, then merged to `main` as `8d90bd0`. The change is not deployed to Unraid. No production finalization, persistence, publication, website update, or email action was performed by this change.
- **Problem diagnosed:** Completed same-date preparations were permanently reused because the durable preparation key and eventual production idempotency key had no revision dimension. Calling preparation again after a production-complete run therefore returned the completed checkpoint and could not produce a genuinely new canonical run.
- **Implemented:** Added explicit numbered preparation revisions. Normal `prepare_daily_stock_alert` resumes the latest revision; the new side-effectful `create_daily_stock_alert_revision` tool is reserved for an explicit user-requested new run and creates a new preparation ID plus a distinct `:revision:N` production idempotency key without deleting or resetting history.
- **Safety decision:** Revisions are never created by scheduler retries, interrupted chats, validation failures, or ordinary resumes. The screener skill now requires explicit user intent before using the revision tool and then follows the same durable research/finalization/production state machine from the new preparation.
- **Regression coverage:** Added focused persistence tests for latest-revision reuse, explicit revision creation, revision-number advancement, distinct production identity, required audit reason, and request-context reset.
- **Verified / current status:** PR #13 passed pytest, Ruff, and the production Docker test stage, then merged to `main` as `004c269`. The revision mechanism is not deployed to Unraid. No production alert revision, persistence, publication, website update, or email action was performed by this repository change.
- **Problem diagnosed:** Finalization could fail after all deep research was durably complete because checkpointed research evidence accepted free-form `evidence_type` labels, while canonical strategy validation requires a lowercase identifier of at most 64 characters using only letters, numbers, dots, dashes, or underscores.
- **Implemented:** Research checkpointing now canonicalizes `evidence_type`, and finalization applies the same normalization again to both saved fresh research and carry-forward evidence. When normalization changes a label, the original value is retained under `details.original_evidence_type`; existing details are preserved.
- **Resume safety:** The finalizer-side normalization deliberately repairs already-saved evidence at payload assembly time without modifying or deleting checkpoint rows. Preparation `15ddf18f-e5ce-4205-bb48-b8d28d683376` therefore remains resumable directly at finalization after deployment; its five completed research names do not need to be researched again.
- **Regression coverage / current status:** Added tests for invalid characters, the 64-character cap, checkpoint-time normalization, and finalization of preexisting saved invalid evidence. PR #14 passed pytest, Ruff, and the production Docker test stage. No production finalization, persistence, publication, website update, or email action was performed by this fix.
- **Deployed / verified:** Deployed current `main` through the guarded Unraid update process. The deployment-side contract image built successfully; API and MCP became healthy and their health endpoints responded successfully. The tunnel encountered the known early-MCP startup race, then became healthy with an initialized MCP session after a single restart once MCP was ready. No saved preparation was retried and no production persistence, publication, or email action occurred.

## September 18 update

- **Implemented:** Centralized daily-alert finalizer candidate rules in `daily_stock_alert_candidate_contract.py`. Finalization and prepared-payload validation now share one normalized effective state for eligibility, market-regime vetoes, bucket mapping, represented remaining-gate counts, and deep-research checkpoint freshness.
- **Implemented:** A durable deep-research checkpoint is stamped only when its saved update time is later than the preparation timestamp. The finalized payload preserves the server-owned checkpoint and intrinsic setup metadata; an actionable setup under a BLOCK regime remains effectively `NOT_ELIGIBLE`.
- **Regression coverage:** Added a resumed-production-shaped fixture that finalizes a saved BUY_NOW research decision under a market BLOCK and asserts the canonical effective candidate, gate count, setup metadata, and freshness checkpoint.
- **Verified:** The guarded Unraid contract image passed 194 tests and Ruff. An initial test failure preserved an existing freshness-error wording contract and stopped deployment before migration or service recreation; the narrow wording repair was then committed as `1cfef99` and `826f0df`.
- **Deployed / verified:** Unraid fast-forwarded to `826f0df`, rebuilt the runtime and contract images, completed migration, and recreated API, worker, and MCP. The API health endpoint returned `ok`; MCP was healthy and its `/mcp` endpoint responded. The tunnel hit its known early-MCP startup race, then became healthy with an initialized MCP session after one tunnel-only restart. No preparation, production alert, persistence, publication, or email action was performed.
- **Implemented:** Extended the canonical finalizer contract so any `NOT_ELIGIBLE` setup with a non-terminal saved bucket is emitted as effective `rejected`, while its original setup bucket remains in payload metadata. This prevents the v0.8 validator failure encountered by saved KRMN research without changing the durable preparation or research rows.
- **Regression coverage / deployed:** Added the KRMN-shaped resumed-research fixture (`NOT_ELIGIBLE` plus saved `qualified` bucket). Commit `cf39f09` passed the guarded Unraid contract image with 195 tests and Ruff, then deployed successfully. API and MCP became healthy; the tunnel again required one tunnel-only restart after its known early-MCP race and then initialized its MCP session. The saved preparation `ae72625f-b34b-4458-a69e-b29c79d10522` was not resumed, modified, persisted, published, or emailed during this repair.
- **Implemented / deployed:** Canonical `ALMOST_READY` construction now derives its remaining-gate count from represented gates. It emits `ALMOST_READY` only with exactly one gate and a structured 0–5% trigger/invalidation plan; otherwise it safely normalizes to `RADAR`. The MXL-shaped stale-count fixture is covered. Commit `55ae230` passed the guarded Unraid suite (196 tests and Ruff), deployed successfully, and API, MCP, and the tunnel were healthy after the standard one tunnel-only restart. The saved preparation was not resumed, modified, persisted, published, or emailed.
