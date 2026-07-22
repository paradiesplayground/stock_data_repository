# Stock Data Repository

An authoritative data repository for U.S. equity research. It ingests:

- Massive reference data and adjusted daily OHLCV bars
- SEC EDGAR bulk company facts and filing metadata
- Versioned deterministic fields derived from those stored sources
- Source freshness and ingestion audit records
- Optional append-only observations produced by downstream strategy skills
- Optional versioned mechanical replays and portfolio simulations

The source and derived-data layers deliberately do **not** score, rank, recommend, size, or trade
stocks. Caller-supplied filters and the downstream swing-trading skill own live interpretation.
The separately versioned `strategy_tracking` layer can now reproduce a documented mechanical
subset of that rubric and simulate its trade plans. Missing qualitative evidence remains missing
instead of being creatively hallucinated into existence. Massive and SEC source records remain
read-only to clients.

## Architecture

The Compose project runs one application image in four roles:

- `migrate`: applies database migrations and exits
- `api`: FastAPI repository on host port `8787`; source reads plus authenticated strategy writes
- `worker`: scheduled Massive and SEC ingestion plus deterministic feature calculation
- `mcp`: Streamable HTTP MCP service on host port `8788`; source reads plus optional strategy writes
- `tunnel`: outbound-only OpenAI Secure MCP Tunnel client

PostgreSQL stores normalized data. Original SEC ZIP archives are retained under `/data/raw/sec` by default.

## Stored data

| Dataset | Source | Stored fields |
|---|---|---|
| Security reference | Massive + SEC | Ticker, exchange, type, CIK, FIGI, SIC/industry, active status |
| Daily prices | Massive | Adjusted OHLC, volume, VWAP, transactions, source timestamp |
| Financial facts | SEC EDGAR | Taxonomy, concept, unit, value, reporting period, form, filing date, accession |
| Filing metadata | SEC EDGAR | Form, dates, document, items, XBRL flags, canonical SEC URL |
| Reference history | Massive + SEC | Distinct observed ticker, listing, identifier, and SIC metadata snapshots |
| Price revisions | Massive | Distinct values observed when an existing ticker/date bar is refreshed |
| Daily derived fields | Massive + SEC | Versioned growth, price movement, liquidity, technical, balance-sheet, cash-flow, share, and market-cap fields |
| Strategy tracking | Downstream callers | Versioned strategy definitions, as-run/replay candidates, evidence, and outcome observations |
| Strategy simulations | Internal backtest engine | Immutable scenario parameters, signal ledger, fills, exits, P&L, and daily equity curve |
| Freshness | Internal audit | Job, status, timestamps, counts, source request details, failures |

SEC financial values remain stored unchanged as reported source facts. The derived table separately
applies conservative, versioned normalization rules and preserves the selected revenue concept,
latest source dates, and quality flags. Raw facts remain available for audit.

## Prerequisites

1. A Massive Stocks API key. Massive currently includes the grouped daily market endpoint on all Stocks plans. Basic is end-of-day; Starter is 15-minute delayed.
2. A descriptive SEC User-Agent containing a monitored email address.
3. An existing Unraid Docker network named `paradiesplayground`.

## Unraid installation

Copy this directory to:

```text
/mnt/user/appdata/stock-data-repository/compose
```

Ensure the application data directory is writable by Unraid's `nobody:users` account:

```bash
mkdir -p /mnt/user/appdata/stock-data-repository/data
chown -R 99:100 /mnt/user/appdata/stock-data-repository/data
```

Then create the environment file:

```bash
cd /mnt/user/appdata/stock-data-repository/compose
cp .env.example .env
```

Edit `.env` and set at minimum:

```dotenv
COMPOSE_PROJECT_NAME=stock_data_repo
POSTGRES_PASSWORD=a-long-random-password
DATABASE_URL=postgresql+psycopg://stockdata:THE_SAME_PASSWORD@postgres:5432/stockdata
MASSIVE_API_KEY=your-key-from-massive
SEC_USER_AGENT=StockDataRepository your-real-email@example.com
API_BEARER_TOKEN=another-long-random-token
OPENAI_TUNNEL_ID=tunnel_your_id
OPENAI_TUNNEL_API_KEY=your-runtime-api-key
MCP_ENABLE_STRATEGY_WRITES=true
```

Do not paste API keys into chat, commit them, or bake them into the image.

Start the project:

```bash
docker compose up -d --build
```

Keep `COMPOSE_PROJECT_NAME=stock_data_repo` unchanged on upgrades. It matches the existing stack
and prevents fixed container names such as `stock-data-postgres` from colliding with a second
Compose project.

The `migrate` service owns the single application-image build. The `api`, `worker`, and `mcp`
services reuse `stock-data-repository:local` and never try to pull it from a registry. Do not add
`build: .` back to those three services; doing so makes Compose rebuild the same image multiple
times and produces several orphaned images during an update.

Use **Update & Rebuild** after pulling application-code changes. The stable local tag prevents a
new version-tagged image from accumulating on every release. Docker may still retain one replaced
untagged image after a genuine rebuild; it is safe to remove after the updated containers are
healthy.

Check status:

```bash
docker compose ps
curl http://localhost:8787/health
curl http://localhost:8787/ready
```

## First ingestion

Populate ticker/CIK reference data first:

```bash
docker compose exec worker python -m app.cli sync-reference
```

Backfill approximately 400 calendar days of daily prices. This supplies enough history for 12-week movement and 52-week-high calculations downstream:

```bash
docker compose exec worker python -m app.cli backfill-market
```

Download and normalize SEC company facts and recent filing history:

```bash
docker compose exec worker python -m app.cli sync-sec
```

Calculate the first derived-feature snapshot after the three source loads finish:

```bash
docker compose exec worker python -m app.cli sync-features
```

SEC bulk archives are large. The first SEC import and first price backfill can take a while; subsequent scheduled updates are incremental database upserts.

## Schedule

Defaults use `America/Chicago`:

| Job | Default | Reason |
|---|---:|---|
| Massive reference | 2:30 AM weekdays | Refresh ticker and CIK mappings |
| SEC incremental data | 4:30 AM Tuesday-Saturday | Resume from the last completed SEC index |
| Massive daily bars | 4:30 PM weekdays | Request the current session's EOD bars after the close |
| Derived daily fields | 5:00 PM weekdays | Runs after the market job and bounded publication retries |

Cron expressions and the market-date target are configurable in `.env`. The default
`MASSIVE_MARKET_LAG_DAYS=0` targets the current weekday, retries a temporarily unavailable EOD
response, and rejects an unexpectedly partial result. Set it to `1` to use the prior-session
morning workflow instead. Historical backfills remain conservative and never request the current
session by default.

The feature job refuses to publish a fresh snapshot unless the expected market date exists and
contains at least `MASSIVE_MIN_DAILY_RESULTS` rows. `/v1/freshness` and `get_data_freshness`
report `expected_market_date`, `market_is_current`, `features_are_current`, and
`ready_for_screening`. Schedule downstream screening only after `ready_for_screening=true`; with
the defaults, 5:30 PM Central is a reasonable starting time. U.S. market holidays are not inferred
from weekdays, so a holiday will remain not ready instead of incorrectly blessing older data.

The SEC daily-index job intentionally processes completed index dates through the prior calendar
day. Same-day market bars therefore do not imply that SEC filings submitted that same afternoon
have already appeared in the repository.

For a conservative next-morning schedule, use:

```dotenv
MASSIVE_MARKET_LAG_DAYS=1
MARKET_SYNC_CRON=20 2 * * 2-6
FEATURE_SYNC_CRON=30 5 * * 2-6
```

For a plan with reliable 15-minute delayed data, you can move the same-day jobs earlier (for
example, 3:25 PM and 3:50 PM Central) while keeping downstream screening after the feature job.

## API

Health endpoints are public. If `API_BEARER_TOKEN` is configured, all `/v1` routes require it:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8787/v1/freshness
```

Available routes:

```text
GET /health
GET /ready
GET /v1/freshness
GET /v1/industry-hierarchy
GET /v1/features
GET /v1/securities
GET /v1/securities/{ticker}
GET /v1/securities/{ticker}/features
GET /v1/securities/{ticker}/history
GET /v1/securities/{ticker}/prices
GET /v1/securities/{ticker}/price-revisions
GET /v1/securities/{ticker}/facts
GET /v1/securities/{ticker}/filings
GET /v1/strategy-runs
GET /v1/strategy-runs/{run_id}
GET /v1/strategy-simulations
GET /v1/strategy-simulations/{simulation_id}
POST /v1/strategy-runs
POST /v1/strategy-runs/{run_id}/outcomes
```

The two POST routes require a configured `API_BEARER_TOKEN`. They append downstream observations
only; no API route can modify Massive or SEC source data.

Interactive OpenAPI documentation is available at `http://UNRAID-IP:8787/docs`.

## MCP service

The MCP endpoint is:

```text
http://UNRAID-IP:8788/mcp
```

It uses stateless Streamable HTTP and exposes:

```text
search_securities
lookup_security
get_price_history
get_financial_facts
get_filings
get_data_freshness
get_industry_hierarchy
get_security_history
get_price_revisions
get_security_features
query_security_features
list_strategy_runs
get_strategy_run
list_strategy_simulations
get_strategy_simulation
list_strategy_profiles
get_strategy_profile
```

Set `MCP_ENABLE_STRATEGY_WRITES=true` to additionally expose:

```text
record_strategy_run
record_strategy_outcomes
preview_strategy_scenario
run_strategy_scenario
```

The write tools are disabled by default. They can append complete, versioned strategy runs and
later outcome observations to the isolated `strategy_tracking` schema. They cannot update source
facts, prices, filings, reference data, or derived fields. After changing this setting, recreate
the MCP container and refresh the ChatGPT app's tool discovery.

`run_strategy_scenario` accepts a bundled base profile, a new immutable strategy version, nested
strategy overrides, nested portfolio overrides, and a date range. It validates the resolved
configuration, replays the strategy, runs the portfolio simulation, and returns both summaries in
one call. Unknown override keys are rejected. No JSON file needs to be created or copied into a
container.

`query_security_features` applies caller-provided thresholds and sorting to deterministic fields.
Its preferred `exclude_industry_groups` argument accepts readable labels or stable keys returned by
`get_industry_hierarchy`, such as `Healthcare`, `Manufacturing`, or `Oil and Gas Extraction`.
The hierarchy includes all 10 SIC divisions and all 83 SIC major groups, plus curated
cross-division healthcare choices. See [INDUSTRY_HIERARCHY.md](INDUSTRY_HIERARCHY.md) for the
complete list. Responses echo the resolved labels and underlying SIC prefixes.

Its `exclude_sic_prefixes` argument accepts up to 50 SEC SIC prefixes, allowing a downstream skill
to exclude any requested industries without changing the stored universe. For example,
`["283", "384", "385", "80"]` covers broad healthcare groups, while additional four-digit
codes can refine the policy. The response echoes the applied prefixes. Rows with unknown SIC codes
are retained and explicitly identified so callers can classify them rather than silently losing
them. `exclude_healthcare` remains available for older clients as a compatibility shortcut.
Snapshot-wide queries include only rows whose `price_date` matches the selected feature date;
ticker-specific lookup still exposes older rows and their quality flags for diagnosis. The tool
does not contain a built-in strategy, score, ranking, position size, or recommendation. Keep it on
a private network;
do not port-forward `8788` to the public internet. ChatGPT cannot connect directly to a private
LAN endpoint, so use OpenAI Secure MCP Tunnel when connecting this on-premises service to a
supported ChatGPT product.

### OpenAI Secure MCP Tunnel

The `tunnel` service builds the pinned official OpenAI `tunnel-client` release and connects it to
the MCP service over the private Compose network at `http://mcp:8001/mcp`. It opens an outbound
HTTPS connection to OpenAI; no router port forwarding or Cloudflare Tunnel is required.

Create both values before starting the Compose project:

1. Create or inspect the tunnel in [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels).
2. Create a separate runtime API key in [OpenAI Platform API keys](https://platform.openai.com/settings/organization/api-keys).
3. Put the values in `.env` as `OPENAI_TUNNEL_ID` and `OPENAI_TUNNEL_API_KEY`.

Do not use an OpenAI admin key for the long-running tunnel service. The runtime key's principal
needs **Tunnels Read + Use** permission. Keep both values out of Git and chat.

After deployment, check the containers and tunnel logs:

```bash
docker compose ps
docker compose logs --tail=100 tunnel
```

The tunnel's local health/admin UI is bound to `127.0.0.1:8789` on Unraid by default. To view it
from a trusted LAN browser, set `TUNNEL_HEALTH_BIND` to the Unraid LAN IP in `.env`, redeploy, and
open `http://UNRAID-IP:8789/ui`. Do not forward port `8789` to the internet.

When the tunnel reports ready, open ChatGPT **Settings -> Apps**, create a developer-mode app,
choose **Tunnel** as the connection type, and select this tunnel. Refresh tool discovery after a
v0.4.x deployment so ChatGPT can see the tools listed above.

## Manual jobs

```bash
python -m app.cli sync-reference
python -m app.cli sync-reference --include-inactive
python -m app.cli sync-market
python -m app.cli sync-market --date 2026-07-17
python -m app.cli backfill-market --start 2025-06-01 --end 2026-07-17
python -m app.cli sync-features
python -m app.cli sync-features --date 2026-07-17
python -m app.cli backfill-features --start 2026-01-20 --end 2026-07-20 --resume
python -m app.cli replay-strategy --start 2026-01-20 --end 2026-07-20 --resume
python -m app.cli replay-strategy --start 2026-01-20 --end 2026-07-20 --strategy-config config/strategies/my-scenario.json
python -m app.cli simulate-strategy --start 2026-01-20 --end 2026-07-20 --starting-capital 10000 --risk-per-trade-pct 3
python -m app.cli simulate-strategy --start 2026-01-20 --end 2026-07-20 --simulation-config config/simulations/default.json
python -m app.cli list-simulations
python -m app.cli get-simulation --simulation-id UUID
python -m app.cli validate-features --ticker AAPL --ticker NVDA
python -m app.cli sync-companyfacts
python -m app.cli sync-submissions
python -m app.cli sync-sec
python -m app.cli sync-sec-incremental
```

Without `--date`, `sync-market` safely catches up every missing weekday through the configured
market target. Use `--date` only when deliberately reloading or troubleshooting one session.
An explicit market date also applies the minimum-row completeness check, but it does not wait
through the scheduled job's same-day publication retry window.

`sync-sec` is the initial bulk bootstrap and is not scheduled nightly. The worker uses
`sync-sec-incremental`, resuming from a durable daily-index checkpoint and refreshing only
companies in new index files. It also reprocesses the two most recently completed indexes as a
safety overlap. `SEC_INCREMENTAL_LOOKBACK_DAYS` is used only for the first run when no checkpoint
or successful prior incremental run exists. A manual bulk run can be used occasionally for
reconciliation.

Each job writes a row to `ingestion_runs`, including failures. The `/v1/freshness` endpoint exposes the latest state so downstream tools can treat stale or missing data as unverified.

`validate-features` is read-only. It selects each ticker's latest stored snapshot on or before the
optional `--date`, recomputes the deterministic fields from local Massive bars and SEC facts, and
reports field-level mismatches. This validates storage-to-formula reproducibility; it is not a
substitute for occasional comparison with independent public sources.

After upgrading from v0.3.1 to v0.3.2, run `sync-companyfacts` once so the newly supported
marketable-security and short-term-debt concepts are loaded from the SEC bulk archive, then run
`sync-features`. No database migration is required for this release. Normal nightly SEC processing
remains incremental afterward.

Upgrading from v0.3.2 to v0.3.3 requires no migration or source-data reload. Rebuild the stack and
refresh the ChatGPT app's tool discovery to expose the hierarchy tool and readable exclusion input.

Upgrading from v0.3.3 to v0.4.0 applies migration `0004_history_strategy`. The migration preserves
existing rows, seeds one reference-history snapshot per current security, and makes derived rows
unique by ticker, date, and calculation version. Re-run `sync-features` after deployment to publish
the new `1.2.0` fields. Price revisions accumulate on future refreshes; existing price bars remain
the historical baseline and do not need to be re-downloaded. Enable strategy writes only after the
migration succeeds.

Upgrading from v0.4.0 to v0.4.1 requires no migration. Run `sync-reference --include-inactive`
once to enrich currently inactive symbols, then run `sync-submissions` so newly linked CIKs receive
their retained SEC metadata. Historical feature calculation version `1.3.0` uses exact-session
price bars as universe membership and no longer excludes a historical symbol because it is inactive
today. Use `backfill-features --start ... --end ... --resume` for an idempotent range backfill.

Upgrading from v0.4.1 to v0.4.2 applies migration `0005_strategy_backtesting`. It adds only tables
under `strategy_tracking`; source prices, SEC facts, and v1.3 feature snapshots are unchanged. Run
`replay-strategy` once for the desired feature range, then run any number of independently stored
`simulate-strategy` scenarios. A scenario key hashes the source replay runs and every execution
parameter, so repeating the same scenario is idempotent while changing capital or risk creates a
separate result.

Upgrading from v0.4.2 to v0.4.3 requires no migration or data reload. Rebuild the worker image to
apply the simulation persistence-ordering fix, then rerun any simulation that previously failed;
the failed transaction was rolled back and requires no cleanup.

All replay thresholds, scoring bands, risk tiers, entry/stop/target multiples, and constructive-
volume rules live in `config/strategies/*.json`. Portfolio capital, risk, slippage, order lifetime,
position limits, and holding period live in `config/simulations/*.json`. Python implements the
generic evaluator and execution engine; it does not contain the default scenario values. CLI flags
can temporarily override simulation-profile values without editing the profile.

Alert presentation policy also lives in the versioned strategy profile. The default `1.1.1`
profile requires each displayed ticker's signed, two-decimal, close-to-close daily move, using the
immediately preceding trading session and `N/A` when no comparable prior close exists. The skill
calculates and renders this field; the database remains limited to neutral source data and stored
run results.

Create a new strategy JSON file and change `strategy.version` whenever a filter, score, or trade-
plan rule changes. The resolved configuration and its fingerprint are stored with every replay, so
two rule sets cannot silently share an immutable strategy version. These files affect only replay
and simulation records under `strategy_tracking`; they never modify Massive price bars, SEC facts,
reference history, or versioned feature snapshots.

## Derived-field rules and limitations

Each row in `security_daily_features` is keyed by ticker, `as_of_date`, and
`calculation_version`. The current calculation version is `1.3.0`, so old feature snapshots remain
available if formulas change.

- Twelve-week change compares the latest close with the last available close on or before 84
  calendar days earlier.
- Fifty-two-week drawdown compares the latest close with the maximum adjusted high during the
  preceding 365 calendar days.
- Average dollar volume is the 20-session mean of each session's adjusted close multiplied by that
  session's adjusted volume.
- EMA and RSI use adjusted closing prices. Relative volume compares the current session with the
  preceding 20-session average.
- The 20-day return, 12-week-high drawdown, 20/60-session ranges, ATR(14), overnight gap, and
  20-session relative return versus QQQ are calculated solely from stored adjusted daily bars.
- TTM flow values use the latest annual value plus current year-to-date value minus comparable
  prior-year year-to-date value when all three are available.
- Latest-quarter growth uses comparable 65-to-120-day reported periods approximately one year
  apart.
- Approximate market capitalization is latest adjusted close multiplied by the latest SEC-reported
  common shares outstanding. It is an estimate, not a real-time provider market-cap field; shared
  CIKs with multiple tickers are explicitly flagged because entity-level shares may not be
  class-specific.
- Free cash flow is operating cash flow less reported capital expenditures. Cash runway is produced
  only when free cash flow is negative and the necessary cash facts are available.
- Cash and short-term investments prefer reported current investment aggregates and otherwise sum
  aligned current marketable debt and equity securities. Total debt includes aligned long-term
  current/noncurrent portions plus standalone commercial paper when no short-term aggregate already
  contains it.
- A missing value stays null. `quality_flags` explains partial history, annual-only values, stale
  periods, missing identifiers, and unavailable comparisons.

## Point-in-time and replay contract

- `security_reference_history` retains distinct metadata states as they are observed. Its
  migration-bootstrap row represents the state known at upgrade time, not the original historical
  publication time.
- `daily_price_bar_revisions` retains distinct old and new values seen during a refresh. The main
  price table remains the current canonical value.
- SEC facts expose `available_at_utc` from the associated filing acceptance timestamp when known,
  preventing a replay from using a fact before it was public.
- Derived rows store the calculation version, source cutoff, source manifest, and the listing/SIC
  metadata used at calculation time. Historical feature filters therefore do not depend on today's
  active status or classification. Version `1.3.0` requires an exact-date price bar for universe
  membership and resolves reference snapshots observed by that date when available. If the
  repository did not yet exist on the historical date, later best-known static metadata is used and
  `reference_metadata_imputed` is recorded. A replay should request the same calculation version
  when exact comparability matters. Metadata copied into pre-v0.4.0 feature rows represents
  upgrade-time state.
- Strategy definitions are immutable by key/version. Use a new strategy version whenever filters,
  formulas, interpretation, or the skill changes materially.
- `as_run` records what the alert actually emitted. `replay` and `backtest` are separate run types;
  they never overwrite an original run. Candidates may include pass/watch/drop stages, reasons,
  metrics, trade-plan JSON, and linked evidence. Outcome observations are appended later.

The derived layer does not currently determine going-concern language, catalysts, earnings dates,
bid/ask spreads, public float, short interest, or whether growth is organic. Those remain separate
research steps for candidates returned by caller-supplied filters.

## Deterministic replay and simulation

Strategy version `fallen-growth-swing:1.1.0` is a mechanical point-in-time replay, not a claim that
historical qualitative research was performed. It applies the stored listing, healthcare, price,
market-cap, revenue-growth, decline, liquidity, cash-runway, and technical fields. Unknown SIC or
missing cash-runway/trade-plan data is retained as incomplete and never made actionable. Catalyst,
customer concentration, organic-growth, going-concern text, spread, and public-float points remain
zero or unavailable. This conservative limitation is stored in the immutable strategy definition.

The checked-in default profile places a buy-stop 0.1% above the rolling 20-session high, uses the
higher of the 20-session low or two ATR below the trigger as the initial stop, and set targets at
2R and 3R. The simulator uses a three-session order window by default, rejects a gap more than 5%
above the trigger, sizes against current mark-to-market equity and available cash, sells half at
2R, moves the remainder's stop to entry, and exits the remainder at 3R or after 15 sessions. Stops
win same-daily-bar ambiguity; gaps through a stop fill at the open; slippage is adverse on each
side. Open positions at the test end are marked to market and are not counted as closed winners.

Use `--strategy-config` to select a versioned replay profile and `--simulation-config` to select a
portfolio scenario. `--starting-capital`, `--risk-per-trade-pct`, `--max-total-risk-pct`,
`--max-open-positions`, `--slippage-pct`, `--order-lifetime-sessions`, and
`--max-holding-sessions` are scenario variables. Market-cap risk multipliers from the strategy
rubric still reduce the requested per-trade risk for smaller companies.

From an MCP-enabled skill, call `preview_strategy_scenario` to inspect the exact resolved
configuration and fingerprints, then call `run_strategy_scenario` with the same inputs. Use a new
`strategy_version` whenever strategy rules change. Portfolio-only changes may reuse the strategy
version because they are independently fingerprinted in the simulation scenario.

Scenarios may optionally add a `market_regime` policy. When enabled, new fills can require the
benchmark's prior-session close to be above its configurable moving average and can additionally
require that average to be rising. Existing positions continue through their normal stops, targets,
and time exits while entry permission is off. The prior close is used deliberately so a backtest
cannot authorize an entry with information from the same session's future close.

Upgrading from v0.4.4 to v0.4.5 requires no migration or data reload. Rebuild the worker image to
pick up the corrected `backfill-features` CLI dispatch. Existing raw, derived, replay, and
simulation records are preserved.

## Development and tests

```bash
python -m pip install -r requirements-dev.txt
ruff check app tests
pytest
```
