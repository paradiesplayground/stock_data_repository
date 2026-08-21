# Stock Alert Process Handoff

## Clean cutoff

The stable cutoff is repository version `0.4.20`, strategy `0.6`, decision contract `0.8`,
feature calculation `1.5.0`, and screener skill `1.4.0`.

The August 19, 2026 corrected production run is
`cceea193-6617-47a1-8605-a17a1ccc57df`. It is the regression fixture for the failure where the
summary appeared actionable while the detailed evidence said not to trade.

## Completed

- New production runs require decision contract `0.8`; contract `0.7` remains readable and
  deliverable for historical runs.
- REST and MCP persistence never publish implicitly. Publication is an explicit operation.
- `run_daily_stock_alert` completes freshness checking, persistence, canonical read-back,
  validation, publication, email delivery, and optional mailbox verification in one resumable
  server-side call for a prepared production alert.
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
- The canonical JSON schema is owned by this repository and vendored by the website with an
  automated drift check.
- Rendering revisions are append-only records linked to the immutable source run and are displayed
  on the signal page.
- CI and Docker deployment builds run the contract regressions before activation.
- Optional TLS IMAP verification records whether an accepted SMTP message appears in the configured
  destination mailbox.

## Verification

Repository checks:

```bash
python -m pytest -q
ruff check app tests
```

Expected repository result at this cutoff: the full test suite passes with no Ruff errors.

Website checks:

```bash
npm run test:unit
npm run build
```

The focused stock-alert suite contains nine tests covering legacy behavior, no-trade layout,
three-watch selection, malformed payload rejection, the August 19 fixture, SMTP receipts, and
mailbox-verification parsing.

## Deployment

Website:

```bash
cd /mnt/user/appdata/paradies-playground
./scripts/update-unraid.sh
```

Stock repository:

```bash
cd /mnt/user/appdata/stock-data-repository/compose
./scripts/update-unraid.sh
```

No market, SEC, or feature backfill is required for `0.4.20`.

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

## Architectural cutoff

The durable prepared-alert workflow is complete. Remaining architecture work is to construct the
production decision payload entirely server-side from an as-of date and to store ChatGPT commentary
as append-only enrichment without making it part of deterministic strategy logic. Operationally,
run one production alert through publication, SMTP acceptance, and mailbox verification. No
historical market, SEC, or feature data needs recalculation.
