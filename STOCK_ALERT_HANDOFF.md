# Stock Alert Process Handoff

## Clean cutoff

The stable cutoff is repository version `0.4.19`, strategy `0.6`, decision contract `0.7`,
feature calculation `1.5.0`, and screener skill `1.4.0`.

The August 19, 2026 corrected production run is
`cceea193-6617-47a1-8605-a17a1ccc57df`. It is the regression fixture for the failure where the
summary appeared actionable while the detailed evidence said not to trade.

## Completed

- New production runs require decision contract `0.7`.
- Screening bucket, technical state, and final decision are stored separately.
- The repository validates current price, relative strength, relative volume, technical and
  market gates, trigger, entry, stop, sizing, targets, R multiples, planned risk, and potential
  rewards before persistence and again before delivery.
- The website repeats the full runtime validation before saving or emailing.
- Website, email, and guide use the same decision labels, explanations, ordering, and number
  formatters.
- The top layout shows BUY/WAIT/NEAR decisions first, then exactly three WATCH candidates. A run
  with none of the first three is labeled `No Trade Today`.
- Legacy runs are labeled as legacy; renderers do not infer decisions from old stage/action fields.
- Explicit resend preserves the immutable source run, uses a unique resend subject, and records
  SMTP message ID, accepted/rejected recipients, and server response.
- Python and website regression tests use the contract-corrected August 19 fixture.

## Verification

Repository checks:

```bash
PYTHONPATH=. pytest -q
ruff check app tests
```

Expected repository result at this cutoff: `109 passed` and no Ruff errors.

Website checks:

```bash
npm run test:unit
npm run build
```

The focused stock-alert suite contains eight tests covering legacy behavior, no-trade layout,
three-watch selection, malformed payload rejection, the August 19 fixture, and SMTP receipts.

## Deployment

Website:

```bash
cd /mnt/user/appdata/paradies-playground
./scripts/update-unraid.sh
```

Stock repository:

```bash
cd /mnt/user/appdata/stock-data-repository/compose
git pull --ff-only
docker compose -p stock_data_repo build migrate
docker compose -p stock_data_repo up -d --force-recreate migrate api worker mcp
docker compose -p stock_data_repo restart tunnel
```

No market, SEC, or feature backfill is required for `0.4.19`.

## Explicit resend fallback

If an AI client caches the old MCP tool schema, run the resend directly in the MCP container:

```bash
docker exec -i stock-data-mcp python - <<'PY'
from app.config import get_settings
from app.db import SessionLocal
from app.services.stock_alert_delivery import resend_strategy_run_email

with SessionLocal() as session:
    print(resend_strategy_run_email(
        session,
        get_settings(),
        "cceea193-6617-47a1-8605-a17a1ccc57df",
    ))
PY
```

Treat `email_delivery: smtp_accepted` as proof that the SMTP server accepted the message, not proof
that it reached the inbox.

## Remaining work

1. Move the structural portion of decision contract `0.7` into one generated/shared schema package
   so Python and TypeScript do not maintain mirrored field definitions by hand.
2. Add a formal content-revision record linking a corrected rendering to its immutable source run;
   `corrected_from_run_id` currently exists only as run metadata.
3. Add a real deployment pipeline for ParadiesWeb that runs the full unit suite and production build
   before activation. GitHub currently reports no CI checks for these commits.
4. Add downstream mailbox verification only if inbox arrival—not merely SMTP acceptance—must become
   machine-verifiable.

These are the remaining architectural improvements. They do not change the current screening
strategy or require historical data to be recalculated.
