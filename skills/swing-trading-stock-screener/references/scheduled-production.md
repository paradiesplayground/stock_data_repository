# Scheduled production

Keep the scheduler thin. Its prompt must invoke `swing-trading-stock-screener` only to prepare and record required qualitative research for the current expected market date.

All production implementation—including preparation reuse, checkpoint persistence, status-driven resume, finalization, validation, canonical persistence, publication, email, and retry—belongs to the server worker. The scheduler must not prescribe production MCP calls, reconstruct payloads, or hold a `preparation_id` as its own state.

Never use it to reopen or rerun a historical alert, including September 15, to demonstrate resumption. Keep `verify_mailbox=false`; the production operation owns persistence, read-back verification, publication, and SMTP acceptance.
