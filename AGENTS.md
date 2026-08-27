# Repository agent instructions

Before finishing any substantive work session, update `docs/codex-handoff.md`.

Use the current calendar date in `America/Chicago`. Create the file if it does not exist. Add or update one clearly dated section for today; do not record today’s work under yesterday’s date.

Document only meaningful repository work:

- What was completed or changed and why it matters
- Important technical or product decisions
- Problems encountered and how they were resolved
- Current implementation and deployment status
- Work that remains incomplete or blocked
- Future enhancements, backlog items, design ideas, and technical debt explicitly discussed
- Relevant commit hashes and concise descriptions

Distinguish clearly between implemented, discussed, deployed, and verified work. Do not present plans as completed.

For future work, prioritize actual enhancements and backlog details. Do not add generic items such as testing the latest release, smoke testing, deployment verification, or monitoring unless they genuinely remain incomplete, failed, or blocked.

Exclude credentials, tokens, private URLs, exact private network details, personal information, lengthy commands, and raw logs.

Keep the handoff concise, factual, and suitable as source material for a public daily journal. If multiple sessions occur on the same date, update the existing dated section instead of creating duplicates.

If no substantive repository work occurred, do not add an empty section or commit.

After updating the handoff, commit it with the completed repository changes and push it to the current default branch so external automation can read it from GitHub.
