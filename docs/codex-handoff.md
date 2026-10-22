# Codex handoff — Stock Data Repository — through September 18, 2026

## 2026-10-22 — v0.9 shadow alert deployed

- **Verified:** The first scheduled feature job writing both 1.5.0 and 1.6.0 took 2½ minutes, unchanged. The live v0.8 alert for Oct 21 completed and was delivered at 5:43 AM CT. The PostgreSQL container restarted at 3:08 AM CT for an unknown reason (not OOM, restart count 0); services were healthy afterwards.
- **Deployed / verified (`3b7a5a5`):**
  - The guarded contract suite passed (287 tests, Ruff). Services were recreated after 7:30 AM CT, so the new shadow job could not fire before the bootstrap. Health is OK after the usual tunnel-only restart.
  - The bootstrap replayed v0.9 tracking from Jul 1 to Oct 21 and recorded 16 shadow runs (Sep 30 – Oct 21).
  - The scorecard sync covered 52 runs, live and shadow, and wrote 1,580 new observations.
- **Shadow vs v0.8 (Sep 30 – Oct 21):**
  - v0.9 tracked 45–49 names a day; v0.8 showed 3–6 RADAR names.
  - v0.9 called Buy for NVT (Oct 1, 2, 5), CRDO (Oct 2, 6, 7, 9), and STRL (Oct 7). It called Close for CRDO, FLY, SNDK, STRL, NVT, and KLIC on several days. v0.8 had no Buy or Close calls in that period.
  - Nothing was labelled Buy or Close on market-blocked days.
  - About 23 names were Blocked, mostly by the 15% share-count rule.
  - The would-be research queue added APH, SANM, TLN, MU, CLS, TER, and VIAV.
  - Shadow reports are 750–1,450 characters, against 70–100k for v0.8.
- **Remaining:**
  - Daily shadow runs at 7:30 AM CT.
  - Phase 3: the "Buy / Close / why" presentation and an unlisted shadow page with a weekly comparison email.
  - At cutover: wire v0.9 into the live preparation and research queue.
  - In parallel: the B4 (exit) and B7 (market gate) backtests.

## 2026-10-21 — Claude takes over the Daily Stock Alert client

- **Implemented:** The assistant side of the Daily Stock Alert is moving from the ChatGPT scheduled task to a Claude Code cloud routine. The prompt lives in `docs/claude-daily-stock-alert-routine.md`, with a `MODE: CHECK` (read-only) / `MODE: LIVE` switch. It keeps the existing contract: freshness gate, idempotent no-op when a canonical run is delivered, resume-not-restart preparation, research only the server's deep-research queue, server-owned finalization and delivery.
- **Integration decision:** Claude cloud routines can't reach the LAN-only stock MCP, and the server has no auth, so it stays private. The website now exposes an OAuth-protected `/stock/mcp` gateway that forwards to it and allowlists only read tools and the daily-alert workflow. Delivery, revision, manual payload, outcome, and scenario tools remain LAN-only. No stock code or config changed, and the OpenAI tunnel keeps serving ChatGPT during the transition.
- **Schedule:** Tue–Sat 11:45 UTC, which is 6:45 AM CDT until daylight saving ends and then 5:45 AM CST. Until Nov 1 the routine runs after the ChatGPT task, so it should find each run already delivered and no-op.
- **Verified:** A read-only check through the gateway read freshness, the latest canonical run and its preparation, and fetched SEC EDGAR. That needed the cloud environment's network access opened; the first check could search but not fetch pages.
- **Status:** The routine is switched to LIVE. Until daylight saving ends it runs an hour after the ChatGPT task, so it should no-op on a delivered run, or finish a preparation ChatGPT left incomplete.
- **Remaining:** Confirm a no-op morning, run one Claude-first morning, then pause the ChatGPT task.

### Alert strategy review and v0.9 Phase 1

- **Reviewed (read-only):** In 35 v0.8 alert days the alert produced no BUY_NOW and two ALMOST_READY decisions. The causes are structural:
  - the trigger includes the alert session's own high, so a fresh setup can't be at or above it;
  - ALMOST_READY depends on an assistant-written decision object, and most research checkpoints carry none;
  - names that recover out of the decline screen leave the alert after one "dropped" day, even though lifecycle tracking keeps them;
  - anchored triggers never reset after a breakdown.

  RADAR names did beat QQQ over 10 sessions, so the screening ranks; the breakout plan lost money in a sector selloff that the SPY gate did not catch.
- **Problem found:** The prior-year TTM revenue was anchored to the alert date minus 365 days instead of the current TTM's period end. At each calendar quarter end, before the next 10-Q, YoY growth was understated: 45 names failed the 40/40 growth gate overnight on Sep 30, and the same happened on Jun 30.
- **Decision (owner-approved):** Build v0.9 in shadow mode first:
  - recovering names stay tracked with exit rules;
  - the server derives Buy / Close labels, and research can only veto, with typed blockers;
  - the trigger uses the prior session's 20-day high;
  - broken setups reset;
  - risk-flag blocks re-check daily;
  - a capped "Buy / Close / Recovering / Blocked" email;
  - v0.8 stays live and unchanged until cutover.

  Exit-rule and market-gate backtests run alongside.
- **Implemented (Phase 1, not deployed):**
  - Feature calculation 1.6.0 corrects the TTM YoY anchor and adds prior-session 20-day high, low, and ATR (migration `0018`). 1.5.0 is still written every day, legacy first, and the v0.8 preparation is pinned to 1.5.0, so the live alert's inputs are unchanged. `backfill-features` accepts `--versions`.
  - A live outcome scorecard writes forward 5/10/20-session returns against QQQ and the alert's own trade path (fill, 1R, 2R or stop) for every as-run and shadow candidate after the daily feature job. It also adds a `sync-alert-outcomes` backfill command, an `alert-scorecard` CLI, an API endpoint, and a read-only MCP tool. The website gateway allowlist does not include the new tool yet.
- **Verified locally:** 266 tests and Ruff pass. The trade-path and forward-window logic, run on exported production data, reproduces the review's figures.
- **Deployed / verified (`14fb1bd`):**
  - The guarded contract suite passed (266 tests, Ruff), and migration `0018` applied.
  - API, MCP, worker, and database are healthy with no OOM kills or restarts; the tunnel needed its usual tunnel-only restart.
  - Feature 1.6.0 was backfilled for 99 sessions (2026-06-01 to 2026-10-20). On Sep 30, 258 names pass the 40/40 growth gate under 1.6.0 versus 219 under 1.5.0. APH and MXL hold their growth across the quarter end.
  - The scorecard sync recorded 35 v0.8 runs, and a repeat sync was a no-op.
  - First v0.8 scorecard by unique fills:

    | Setup status | 10-day vs QQQ | Unique fills | Reached 2R | Expectancy |
    |---|---|---|---|---|
    | RADAR | +2.6% | 28 | 5 | −0.47R |
    | NOT_ELIGIBLE | −1.1% | 45 | 2 | −0.89R |
- **Remaining:**
  - Confirm the next 5:30 AM feature job, which now writes both versions, still finishes before the alert, and that the v0.8 alert is unchanged.
- **Implemented (Phase 2, not deployed): the v0.9 engine in shadow mode.**
  - **Config and contract.** New config `dynamic-swing-buy-alerts-v0.9.json` and decision contract v0.9:
    - the same four statuses;
    - buckets `screen` / `tracked_recovery` / `blocked` / `exited` / `incomplete`;
    - technical state `broken`;
    - typed `blocker_codes`.

    v0.9 can be recorded only as `run_type=shadow`; `as_run` still requires v0.8, and v0.9 is not in the delivery allowlist.
  - **Engine.** A pure engine (`alert_v09_engine`) assigns every label on the server:
    - the trigger is built from the prior-session 20-day high (Buy is reachable on a breakout session);
    - names stay tracked after they recover out of the screen;
    - exit rules: base gates fail, more than 60 sessions outside the screen, or no progress in 30 sessions;
    - broken setups reset, and no actionable label ever shows a stop above the price;
    - risk flags and typed research blockers give a daily re-checked Blocked state.

    Saved free-text blockers map to typed codes (`blocker_codes`).
  - **Shadow job.** `daily_stock_alert_shadow` runs at 7:30 AM CT, Tue–Sat. It reuses the live alert's saved research read-only and keeps its own lifecycle rows under `dynamic_swing_buy_alerts@0.9`. It records one shadow run per date under strategy key `dynamic_swing_buy_alerts_shadow`, with capped email sections, a compact report, the would-be research queue, and a comparison with v0.8. It never publishes or emails.
  - **Commands.** `bootstrap-alert-shadow` replays tracking point-in-time and resumes safely; `run-alert-shadow` runs one date. The outcome scorecard now also covers shadow runs.
- **Verified locally:**
  - 287 tests and Ruff pass.
  - The golden replay drives the shadow job's own per-day function with production 1.6.0 features for 19 tickers (Jul 1 – Oct 20). It checks that:
    - recovering names stay visible every day;
    - nothing is labelled Buy or Close on market-blocked days;
    - Buy and Close calls appear;
    - stops sit below the price;
    - APP and STRL are no longer led by stale triggers;
    - every candidate validates against contract v0.9.
  - The shadow job's database path has not run against PostgreSQL yet; the first bootstrap at deploy is its first real run.
- **Remaining (Phase 2 deploy gate, needs the owner's go-ahead):**
  - deploy;
  - `bootstrap-alert-shadow --start 2026-07-01 --end <latest market date> --record-from 2026-09-30`;
  - review the shadow runs against v0.8.

  The live v0.9 preparation wiring (research queue order, finalization through the engine) and the A8 presentation are Phase 3 / cutover work.

## 2026-10-14 — Decision-first Daily Stock Alert

- **Companion presentation correction:** Website/email renderer 0.8.4 (`24cdd56`) now summarizes saved evidence into at most four strengths, four typed concerns, three exact requirements, and a direct conclusion. STRL narrative risks are surfaced even when its saved fundamental-risk array is empty. Only the first realistic target appears in the primary card; potential R/R uses the official trigger and planned post-entry stop, with one-decimal percentages/R multiples. Full research and secondary targets stay in collapsed website details; email links to the full research.
- **Verified / deployment boundary:** All 95 companion tests and its production build passed; live STRL card, service health, and image cleanup were verified. This correction changes shared server-side rendering only. Backend strategy/actionability, canonical ranking, stored evidence, and historical records are unchanged; no stock image was rebuilt and no research or alert delivery was rerun.

- **Implemented:** Server stamps normalized checkpoint tickers on saved, candidate, and canonical evidence, including older checkpoint records at finalization. Current evidence and its qualitative blockers/flags survive carry-forward without crossing ticker boundaries.
- **Implemented:** Bounded research now prioritizes actionable history and the closest viable setups before new distant hard-blocked names. Existing daily budgets, strategy thresholds, market veto, evidence requirements, validation, and production idempotency are preserved.
- **Implemented:** Canonical investor summaries separate positive fundamentals, fundamental risks with evidence and clearance conditions, technical requirements, screen failures, data gaps, catalysts, research freshness, and the proposed entry/stop/target plan. Actual research conclusions replace evidence-type boilerplate. Potential R/R uses the official proposed trigger and planned post-entry stop.
- **Decision:** Backend owns ordered `primary_setups` and `next_watchlist` ticker groups. Markdown and companion website/email consume that order; useful exclusions remain in the audit material. Website renderer 0.8.3 leads with decisions and setup cards, with detailed audit tables collapsed.
- **Verified locally:** All 252 Python tests and Ruff pass locally and in the production contract image; regression coverage includes checkpoint-to-canonical STRL evidence reuse, ticker isolation, closest-setup queue priority, categorized summaries, and unchanged actionability/idempotency checks. Companion website tests and production build pass.
- **Deployed / verified:** Functional backend `ae1a788` is pushed to `main` and deployed through the guarded updater. API health/readiness passed; API, MCP, database, and tunnel are healthy and the worker is running. The known startup connection race was resolved with one tunnel-only restart; MCP session initialization was confirmed. Companion renderer `7cf3c22` deployed successfully with 89 tests and the production build passing, and a live saved signal page shows closest setups/watchlist before collapsed audit details.
- **Boundary:** No saved alert was manually resumed, republished, or emailed. The new categorized summaries are produced for future finalizations; historical records were not rewritten.

## 2026-09-30 — Phase 3 decline-rule comparison

- **Executed:** Ran the bounded, resumable six-scenario historical comparison on the deployed stack for 2024-08-05 through 2026-08-05 using feature calculation version 1.5.0. Coverage was confirmed from 2024-08-05 through 2026-09-29 across 540 stored sessions before the run.
- **Results:** All six scenarios completed. `price_change <= -15%` was the strongest simulated result in this window: 9 closed trades, 14.86% total return, 0.396R expectancy, 44.4% win rate, 2.27 profit factor, and -4.04% maximum drawdown. `price_change <= -10%` produced 14 closed trades but lower 0.192R expectancy and -9.74% maximum drawdown. Drawdown-based and either-condition variants each produced 15 closed trades, 0.118R expectancy, 8.35% total return, and -7.33% maximum drawdown.
- **Boundary:** This is historical/replay evidence only, with non-decline parameters held to the base profile. Production strategy configuration, daily alerts, publication, and email were not changed or run. Results remain exploratory because the sample contains 9–15 closed trades per scenario.
- **Verified / deployed:** The comparison used the already deployed safeguarded implementation; live API health/readiness passed and the feature snapshot was current before execution. No code deployment was needed for this run. Phase 3 controlled comparison is complete; any production decline-threshold change would require a separate explicit decision and validation.

## 2026-09-30 — Phase 2 observation snapshot integrity correction

- **Corrected:** Lifecycle transition observations are now appended after the lifecycle row calculates its resulting active trigger, rolling trigger, and trigger distance. Observations preserve the prior state separately and use `payload.rolling_trigger` for the rolling reference.
- **Regression coverage:** Added new-trigger and anchored-trigger transition cases, including active/rolling trigger separation. The stale-timeout observation and canonical run-read coverage remain intact. The complete local suite passes 245 tests and Ruff passes.
- **Verified / deployed:** Functional commit `21c937c` was deployed through the guarded Unraid updater. The contract image passed 245 tests and Ruff; API health/readiness and MCP health returned successfully; PostgreSQL, API, worker, MCP, and the tunnel are healthy after the standard tunnel-only startup-race recovery. No production alert, publication, or email was run.

## 2026-09-30 — Phase 2 lifecycle transition observations

- **Implemented:** Added append-only `strategy_candidate_lifecycle_observations` storage. Every actual lifecycle state transition records the ticker, prior/new state, event, outcome status, transition metrics, source canonical run, and observation timestamp. Stale-timeout archival records the same durable observation path.
- **Implemented:** Canonical strategy-run reads now expose lifecycle observations separately from downstream performance outcomes. Historical alert payloads remain immutable.
- **Regression coverage:** Added transition-persistence coverage and retained the stale-timeout boundary coverage. The complete local suite passes 244 tests and Ruff passes.
- **Verified / deployed:** Functional commit `d786e25` and migration `0017_lifecycle_observations` were deployed through the guarded Unraid updater. The contract image passed 244 tests and Ruff; migration completed; API health/readiness and MCP health returned successfully; PostgreSQL, API, worker, MCP, and the tunnel are healthy after the standard tunnel-only startup-race recovery. Phase 2 lifecycle work is complete. Phase 3 controlled decline-rule comparison remains separate.

## 2026-09-30 — Phase 2 stale-timeout bootstrap correction

- **Implemented:** Stale lifecycle archival now falls back from a null `last_discovery_screen_date` to `first_discovered_date`. This covers rollout-era dropped/reassessed candidates without changing historical alert payloads or exempting them from timeout enforcement.
- **Regression coverage:** The null-anchor case remains active at 29 stored sessions and archives at 30 with `STALE_TIMEOUT`, archive date/reason, and cleared `active_trigger`. The complete local suite passes 243 tests and Ruff passes.
- **Verified / deployed:** Functional commit `252bb4b` was deployed through the guarded Unraid updater. The contract image passed 243 tests and Ruff; API health/readiness and MCP health returned successfully; PostgreSQL, API, worker, MCP, and the tunnel are healthy after the standard tunnel-only startup-race recovery. No production alert, publication, or email was run.

## 2026-09-30 — Phase 2 stale lifecycle timeout

- **Implemented:** Candidate lifecycle persistence now archives a tracked candidate as `INVALIDATED` after it has been absent from the discovery universe for a configurable number of stored market sessions. The initial default is 30 sessions, configured with `CANDIDATE_LIFECYCLE_STALE_TIMEOUT_TRADING_SESSIONS`.
- **Implemented:** Timeout archival records `STALE_TIMEOUT`, the archival date, an explicit reason, and clears the anchored trigger. Existing invalidation remains terminal, and observed candidates are not changed by the timeout pass.
- **Regression coverage:** Added timeout-boundary tests; the complete local suite passes 242 tests and Ruff passes. No production alert, publication, email, or lifecycle state was changed.
- **Verified / deployed:** Functional commit `e209cb8` was deployed through the guarded Unraid updater. The contract image passed 242 tests and Ruff; migrations completed; API health/readiness and MCP health returned successfully; PostgreSQL, API, worker, MCP, and the tunnel are healthy after the standard tunnel-only startup-race recovery. No production alert, publication, or email was run. The next Phase 2 slice remains durable per-transition outcome observations.

## 2026-09-29 — Derived-feature recovery and memory containment

- **Problem diagnosed:** Sep. 28 market bars were current, but the feature snapshot remained Sep. 25 because the scheduled feature process was cleanly interrupted 15 seconds after it began. Its run row remained `running`, so the replacement scheduler had no same-day catch-up. A manual recovery then exposed the underlying capacity failure: loading the complete price and SEC-fact universe at once exceeded the worker's 3 GiB cgroup limit and the kernel OOM-killed the calculation process.
- **Implemented:** A clean scheduler shutdown now marks active ingestion runs failed with an explicit retry-safe interruption marker. On startup, the worker immediately retries an interrupted or stale derived-feature run; ordinary calculation failures are not retried by that path.
- **Implemented:** Feature calculation now loads price history and financial facts in 100-security batches, upserting each completed batch. It preserves the existing calculation version, source inputs, feature-universe contract, and idempotent `(ticker, as_of_date, calculation_version)` writes while avoiding full-universe ORM materialization.
- **Verified / deployed:** Commits `15bc5fb` and `f59c0b5` were deployed to Unraid. The authoritative container suite passed **223 tests** and Ruff. The recovery ran successfully for Sep. 28 with **5,161** feature rows, matching the feature universe; market data had 12,570 source rows. API health returned OK; API, MCP, PostgreSQL, and the worker are healthy, and the tunnel became healthy after its normal post-recreation restart. No daily alert was prepared, finalized, published, or emailed.
- **Current status:** The former orphaned runs are retained as failed with an explicit interruption reason; the latest derived-feature run is succeeded for Sep. 28, so the market and feature freshness gate can resolve ready for that expected date. The duplicate idempotent recovery execution during deployment wrote the same keys and both completed successfully.

## 2026-09-28 — Historical scenario host-memory containment

- **Implemented:** Capped the active stock stack at approximately 12.25 GB: PostgreSQL 2 GB, API 1 GB, worker 3 GB, MCP 6 GB, and tunnel 256 MB (with a temporary 1 GB migration cap). This replaces the previous two 8 GB limits plus unbounded database/API processes and preserves host headroom for Unraid and other services.
- **Implemented:** Historical scenario and decline-comparison requests are now single-flight within MCP, and the service layer rejects invalid or more-than-two-year date windows. The previously requested August 2024–August 2026 comparison remains supported.
- **Implemented:** The guarded Unraid updater now reports each stock container's enforced memory limit, OOM-kill state, and restart count after recreation.
- **Pending verification:** Run the guarded Unraid contract suite and deploy this change before starting the historical comparison. No scenario, production alert, publication, or email has been invoked in this session.

## 2026-09-25 — Durable daily-alert production state machine

- **Implemented (presentation):** Canonical v0.8 candidate presentation now persists renderer-ready Why, Next, score summary, score/components, observed targets/R multiples, positive factors, blockers, and research summary. Markdown consumes those fields directly; legacy decision text remains compatibility-only.
- **Implemented (presentation):** Daily-change renderers consume the canonical formatted `meaningful_changes` text, keep analytical decimals raw, round displayed trigger distances to one decimal place, and suppress a classification downgrade caused solely by missing fresh qualitative research when the underlying setup is unchanged.
- **Regression coverage / deployed:** Added factual KLIC-style presentation, high-R/low-score explanation, Markdown canonical-field, rounded trigger-distance, and research-scope-only downgrade tests. The guarded Unraid suite passed 218 tests plus Ruff; functional commits `fc34068` and `1a17c75` are deployed and the API health endpoint is OK. No preparation, publication, or email was invoked.
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

## September 30 update

- **Implemented:** Added the repository-owned daily-change event taxonomy and priority ordering. Recovery exits now emit `LEFT_DISCOVERY_DUE_TO_RECOVERY` when the 12-week decline improves above the discovery threshold while growth, liquidity, and deterministic risk remain acceptable. Repeated setup-invalidation breaches are suppressed, and setup wording no longer implies an open-position stop.
- **Implemented:** Completed the Phase 1 reporting layer. The stock repository now publishes consolidated per-ticker report sections for `Needs attention today`, `Recovery Watch`, and `New / materially changed`, with event-priority ordering, recovery-watch sorting, and an eight-name attention cap. The Markdown renderer no longer repeats separate Why/Next/blocker prose for these sections.
- **Implemented:** ParadiesWeb email and v0.8 signal-page rendering now consume the server-owned sections. The shared section renderer displays KLIC-style recovery events with current price, trigger, distance, relative strength, what changed, and what matters next; lower-priority repeated event rows are consolidated by ticker.
- **Regression coverage:** Stock tests pass locally and in the production contract image (231 tests plus Ruff). The KLIC fixture now uses actual canonical `in_raw_pool`, `screen_bucket`, presentation blocker, and status transitions; no synthetic deterministic-failure fields are injected. Added the independent dilution-risk negative case and corrected recovery next-step wording to reference the current trigger and remaining entry gates. ParadiesWeb unit tests pass (84), lint has three pre-existing warnings and no errors, and the production build passes.
- **Deployed / verified:** Stock commit `1957370` was deployed through the guarded Unraid updater. Migrations completed, API health/readiness returned successfully, and API, MCP, worker, PostgreSQL, and the tunnel are healthy; the tunnel initialized its MCP session after the known one-time startup race and tunnel-only restart. ParadiesWeb current `main` (`22d31d3`, functional renderer `8081198`) remains deployed and healthy. No alert preparation, persistence, publication, or email action occurred.
- **Implemented:** Phase 2 lifecycle foundation is now deployed. Added durable per-ticker lifecycle rows with FALLEN, RECOVERING, NEAR_TRIGGER, ACTIONABLE, and INVALIDATED states; discovery dates, watch duration, recovery/relative-strength history, archive fields, and material transition markers are retained independently of daily discovery membership.
- **Implemented:** Added anchored-trigger behavior. The rolling trigger remains recalculated for analysis, while a near-trigger tracked candidate receives a fixed active trigger that remains stable across later rolling-high changes and trigger crossings without confirmation. Active-trigger data is exposed in canonical candidate payload lifecycle metadata.
- **Regression coverage / deployment:** Added lifecycle transition and anchored-trigger tests, including fixed-trigger behavior after rolling-trigger movement and trigger crossing without confirmation. The guarded Unraid contract image passed 236 tests and Ruff; migration `0016_candidate_lifecycle` completed, API/MCP readiness passed, and the tunnel is healthy with an initialized MCP session. Functional commit `7d26e0a` is deployed.
- **Corrected / deployed:** Canonical finalization now preserves `active_trigger`, recalculates trigger distance and technical/remaining gates from that anchored value, and carries lifecycle metadata into the final candidate. Lifecycle persistence is performed once per canonical run, and `INVALIDATED` remains terminal unless an explicit reset path is added. Regression coverage now passes 238 tests plus Ruff. Commit `ff019c2` is deployed; API/MCP readiness and the healthy initialized tunnel were verified.
- **Corrected:** Remaining-gate normalization now removes stale rolling-trigger technical/distance gates after active-trigger application while retaining independent gates, including market and qualitative requirements. Lifecycle persistence is isolated to one canonical-run helper and has an explicit exactly-once regression.
- **Verified:** The complete local suite passes (240 tests) and Ruff passes. Unraid deployment is pending for this change.
- **Still remaining:** A dedicated web lifecycle table/expandable research detail view, configurable stale-timeout enforcement (initial 30 trading sessions), durable per-transition outcome observations, and the Phase 3 controlled decline-rule comparison have not been implemented. No alert preparation, canonical production persistence, publication, or email run was invoked. The pre-existing Unraid `ffg` file was left untouched.
