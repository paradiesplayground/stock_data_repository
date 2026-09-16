# Scheduled production

Run the daily alert only after successful `prepare_daily_stock_alert` and fresh qualitative research limited to its `deep_research_queue`.

1. Preserve the returned `run_template` and complete every ticker in `expected_candidate_tickers` exactly once. Use carry-forward evidence as supplied and complete deterministic-only and dropped names without independently expanding fresh research.
2. Call `validate_daily_stock_alert` with the completed template. Do not call production if it fails.
3. Send the exact `validated_run_payload` from validation, unchanged, to `run_daily_stock_alert` with `verify_mailbox=false`.

The production call owns persistence, read-back hash verification, publication, and SMTP acceptance. Do not enable mailbox verification: the recipient mailbox cannot be independently verified. The scheduled workflow must not substitute an older payload or retry by editing the validated payload; resolve validation or production failures from a new preparation cycle when appropriate.
