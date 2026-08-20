# v0.7 Decision-Status Contract

This contract removes the old ambiguity where a candidate could look actionable in the summary while the detail text effectively said not to buy it.

## Statuses

| Status | Meaning |
|---|---|
| `BUY_SETUP` | Tradeable now. All entry-quality gates pass. |
| `CONFIRMED_WAIT_FOR_ENTRY` | Technical confirmation exists, but the current entry is not acceptable. Wait rather than chase. |
| `NEAR_TRIGGER` | The setup is close to its technical trigger, but confirmation is not complete. |
| `WATCH` | Valid developing candidate, but not close enough to an entry trigger to treat as imminent. |
| `RESEARCH` | Potential setup cannot be classified confidently until specified evidence or data is verified. |
| `AVOID` | A known risk/reward or quality problem makes the current setup unattractive. |
| `INVALIDATED` | The prior setup/thesis broke its stated invalidation condition and is no longer eligible without a new thesis. |

## Required explanation

Every candidate using the v0.7 decision contract must include:

- `screen_bucket` — screening membership only: `qualified`, `speculative`, `cooldown`, `rejected`, `dropped`, or `incomplete`
- `technical_state` — chart state only: `no_setup`, `developing`, `near_trigger`, `confirmed`, `extended`, `invalidated`, or `unknown`
- `decision_status`
- `status_reason` — plain-language explanation of exactly why the candidate is in that status now
- `next_condition` — the specific condition that would change the decision or the next thing to wait for

The stored status is the authoritative decision label. `screen_bucket`, `technical_state`, and `decision_status` describe separate concerns and must not be derived from one another. Older `stage` and `action` fields remain readable for historical compatibility; renderers must not interpret them as permission to trade.

New production `as_run` records must declare `decision_contract_version: "0.7"`. The repository rejects the entire run before persistence if any candidate omits a required contract field. It validates the same contract again before website delivery. Replays and backtests may omit the version only when preserving a historical payload.

## BUY_SETUP hard gates

`BUY_SETUP` is rejected at write time unless all of the following are explicitly true:

- current price is at or above the trigger and inside the 5% entry zone
- 20-session relative strength versus QQQ is positive
- constructive relative volume meets the trade plan's required-volume threshold
- T1 reward/risk is at least `1.00R`
- T2 reward/risk is at least `1.75R`
- current price is no more than `5.00%` above the trigger
- technical-confirmation gate passes
- market-regime gate passes

The persisted fields are:

- `current_entry`
- `pct_above_trigger`
- `t1_r`
- `t2_r`
- `technical_gate_passed`
- `market_regime_gate_passed`

Every v0.7 candidate must also carry `metrics.close`,
`metrics.relative_return_20d_vs_qqq_pct`, `metrics.relative_volume_20d`, and explicit technical and
market gate results. Candidates with a trade plan must provide entry, trigger, stop, two targets,
two R multiples, whole shares, planned risk, two potential rewards, and required volume. The
repository recalculates trigger distance, risk, R multiples, and potential rewards before saving.

If technical confirmation exists but one of the entry-quality gates fails, the candidate belongs in `CONFIRMED_WAIT_FOR_ENTRY`, not `BUY_SETUP`.

## Output ordering

Candidate output from `get_strategy_run` and the website-delivery payload is ordered by decision status first, then score descending within a status:

1. `BUY_SETUP`
2. `CONFIRMED_WAIT_FOR_ENTRY`
3. `NEAR_TRIGGER`
4. `WATCH`
5. `RESEARCH`
6. `AVOID`
7. `INVALIDATED`
8. legacy candidates without a v0.7 decision status

The downstream report renderer should put **Decision Summary / Best Setups** first and detailed analysis below it. Each top setup should show its status, `status_reason`, next condition, entry/trigger extension, T1/T2 R, and gate results so the headline and the detail cannot point in opposite directions.

## Compatibility

Existing historical runs are not rewritten. A candidate with no decision-status fields remains a legacy record and sorts after v0.7 candidates. Downstream renderers must label that data as legacy rather than guessing a decision from `stage` or `action`. New production callers send `decision_contract_version: "0.7"` at run level and the required contract fields as top-level keys inside every candidate passed to `record_strategy_run`.
