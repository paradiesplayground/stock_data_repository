# Scheduled production

Run the daily alert only after freshness is current. Never rerun, publish, or email an earlier alert merely to resume its research checkpoint.

1. Check for a completed production run for the expected market date. If complete, report it and stop.
2. Call `prepare_daily_stock_alert`; retain its durable `preparation_id`. A repeated call must reuse the existing checkpoint.
3. Call `get_daily_stock_alert_preparation_status`. Research only outstanding deep-research tickers.
4. After every ticker, immediately call `record_daily_stock_alert_research`. An interruption after that call is safe; do not repeat the completed ticker on resume.
5. Call `finalize_daily_stock_alert_preparation`. It server-assembles the full canonical payload, including carry-forward, deterministic-only, and dropped candidates, generates the report, and runs mandatory validation.
6. Only when finalization reports `validation_status=valid`, call `run_finalized_daily_stock_alert_preparation` once. It keeps `verify_mailbox=false` and uses the existing production idempotency path.

The production call owns persistence, read-back hash verification, publication, and SMTP acceptance. Do not enable mailbox verification: the recipient mailbox cannot be independently verified. The scheduled workflow must not substitute an older payload or retry by editing a validated payload.
