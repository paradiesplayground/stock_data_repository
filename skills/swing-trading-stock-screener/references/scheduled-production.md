# Scheduled production

Keep the scheduler thin: use the skill for state transitions and stop conditions. Run only for the current expected market date; never reopen a historical alert to resume it.

1. Prepare, retain the returned `preparation_id`, then read its status.
2. Checkpoint each outstanding deep-research ticker immediately after its review.
3. Let server-side finalization assemble and validate the canonical payload after research is complete.
4. Run production only when the persisted validation status is `valid` and production has not completed.

Do not reconstruct payloads, rerun finished research, or enable mailbox verification. The production operation owns persistence, read-back verification, publication, and SMTP acceptance; always keep `verify_mailbox=false`.
