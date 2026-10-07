# FlowTrade Radar API

Python 3.12+ FastAPI decision-support service. It serves either a complete verified
Toss snapshot (`mode: live`, `source: toss`) or explicitly synthetic demo data
(`mode: demo`, `source: synthetic`). No market/strategy factor mixes these sources.
The current published environment remains demo because the actual OAuth request
returned **403 `access_denied` / `IP address not allowed`**.

From repository root:

```bash
python -m venv .venv
.venv/bin/pip install -r apps/api/requirements.lock.txt
PYTHONPATH=apps/api .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests -q
PYTHONPATH=apps/api .venv/bin/python apps/api/scripts/verify_toss.py
```

Swagger: `http://localhost:8000/docs`. `/api/health` reports service availability;
`/api/connection` separately reports authentication, schema/sample verification,
data quality, and sanitized blockers. Neither GET performs network authentication.

## Runtime and data mode

`TOSS_DATA_MODE=auto` is the default. The server owns one in-memory OAuth client,
uses the injected `TOSS_CLIENT_ID` and `TOSS_CLIENT_SECRET`, and periodically updates
quotes after verification succeeds. `TOSS_DATA_MODE=demo` prohibits automatic and
explicit-refresh HTTP calls; use this for offline work and isolated browser tests.
`TOSS_DATA_MODE=live` requests live data but still falls back to labeled demo if
observations are incomplete, stale, invalid, or authentication is blocked. Setting
this flag never bypasses validation. The current official pipeline does not need a
user-authored manifest or `TOSS_ACCESS_TOKEN`.

`API_DATA_DIR` defaults to ignored `.runtime/`. SQLite paper trades, strategy/theme
overrides, notification reads, and backtest runs survive server restarts. Normalized
live snapshots are atomically saved there with private permissions, then revalidated
on every read. No credentials, token, or raw OAuth response is written to these
reports or snapshots. Original demo paper rows are preserved and marked `demo` by
an additive schema migration. Live and demo paper positions are evaluated separately.
`CORS_ORIGINS` lists allowed frontend origins separated by commas.

Optional PostgreSQL tables are in `app/db/postgres_schema.sql`; the local service
uses SQLite, not PostgreSQL/Redis. Live ingestion currently uses bounded REST
candidate refresh and cache reuse; WebSocket ingestion is not enabled.

## Official Toss verification and recovery

Public official OpenAPI **1.2.19** and AsyncAPI **1.2.2** are now accessible. The
selected read-only response contract and official document hash are checked into
`config/toss-openapi-contract.json`. The official FAQ states domestic prices and
candles use KRX+NXT integrated data, without a per-trade venue field. Actual quote,
daily OHLC, NXT-supported stock metadata, and nonzero KRW minute candles inside
calendar-defined extended sessions must also pass before live recommendations.
Those observations establish integrated extended-session data, not an individually
identified NXT tick. Ordinary minute candles cannot reconstruct the complete daily
candle because their included trade types differ.

The single actual OAuth attempt used injected server credentials with verified TLS
and documented form encoding. Its error description exactly matched `IP address
not allowed`. No token was issued, no authenticated quote was fetched, and no
actual NXT sample has been verified. No numeric calling IP was supplied in the
retained safe diagnostic fields. Credential rotation is not indicated by this error.

In Toss WTS, use **설정 → Open API → 허용 IP 관리** to register the actual Toss-facing
outbound IP. A stable outbound IP is necessary for a cloud environment whose IP
can change. Generic IP-check services may report a different proxy route, so they
are not proof of the address Toss sees. After that external setting is corrected,
use **시세 연결 상태 새로고침** in the web settings, or call
`POST /api/connection/refresh`. An IP/credential refusal is latched; startup and
background refresh never repeat it unchanged. An explicit refresh clears the latch,
performs bounded discovery, and resumes complete dataset ingestion when possible.

The preferred retry is the running API's refresh endpoint because Toss permits only
one valid token per client: a new token immediately invalidates the previous one.
For command-line verification, stop the API first:

```bash
PYTHONPATH=apps/api .venv/bin/python apps/api/scripts/verify_toss.py --live
# Bounded schema/session discovery only, without full ingestion:
PYTHONPATH=apps/api .venv/bin/python apps/api/scripts/bootstrap_toss.py --probe
```

Without these explicit flags both scripts only show safe saved diagnostics.
`TOSS_DATA_MODE=demo` also suppresses their probe calls. TLS checking stays enabled,
redirects are rejected, retries are bounded and follow rate-limit headers, and no
order/account/holding endpoint is exposed.

## Analysis and persistence

The frontend consumes `/api/market`, `/api/stocks`, `/api/stocks/{symbol}`,
`/api/themes`, `/api/strategy`, `/api/backtest`, `/api/paper`, and `/api/notifications`.
Market envelopes include mode/source/asOf/venueLabel, stale/degraded status, and
quality information. Missing live indices remain missing; synthetic index cards are
never used in live mode. Live stock investor/program flows are **shares**, with
separate observation dates and venue labels: registered foreign/institution flows
include KRX+NXT; program flow is KRX only. Historical significant-candle turnover
uses a labeled close-times-volume estimate when an exact candle amount is absent.

The candidate stage joins exact current prices with documented trading-amount
rankings and stock metadata. It analyzes only observed mapped candidates; theme
coverage shows missing members, and breadth uses the configured member count.
Live snapshots require enough daily and minute history, complete required US
watchlist returns, recent quotes, and calendar-relative daily freshness. A score of
A or better still requires theme agreement, liquidity, reward/risk, and a stop from
confirmed box/significant-candle structure within the configured risk limit.

Strategy changes use `PUT /api/strategy` (full object or nested patch); defaults
remain in `config/strategy.yml`, while overrides go to `.runtime/`. Theme creation,
editing, and deletion use `POST /api/themes`, `PUT /api/themes/{id}`, and
`DELETE /api/themes/{id}` and immediately recalculate the analysis.

Paper buying uses `POST /api/paper/buy` with `{symbol,quantity}`; selling uses
`POST /api/paper/{id}/sell`. Fills carry their demo/live data provenance. These are
virtual trades only. Missing or differently sourced quotes cannot close a position.
There is no actual-order endpoint, regardless of `ENABLE_LIVE_TRADING`.

Backtesting remains a separately labeled **synthetic demo**. It replays
non-overlapping daily MA5/pivot signals with next-open fills, fees, slippage,
stop-first ambiguous candles, and gap-open stop fills. MFE/MAE include OHLC extrema
through the exit bar, whose ordering is unknown. Other patterns show zero samples
until minute replay is available. Demo samples use a fixed 2026-10-07 10:24 KST
scenario and a synthetic weekday calendar; their returns are not historical results.
