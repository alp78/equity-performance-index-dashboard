# Equity Performance Index Dashboard

A high-performance, cloud-native financial dashboard built on Google Cloud Platform. Combines a SvelteKit frontend, FastAPI backend, and BigQuery data warehouse to deliver real-time S&P 500 market data, technical indicators, and global macro feeds through WebSocket and REST APIs.

---

## Live Dashboard

![Live Dashboard](docs/Dashboard.jpg)

The interface is composed of six panels: a searchable S&P 500 sidebar with live daily price, high/low, and volume; an interactive price chart with MA30/MA90 overlays and a volume histogram; a period selector (1W through MAX) that filters client-side with no additional network requests; US equities and global macro panels fed by a persistent WebSocket connection; and a period performance panel showing the top and bottom three S&P 500 movers.

---

## System Architecture

![System Diagram](docs/Diagram.jpg)

---

## Core Components

### 1. Data Ingestion and Persistence

| Component | Role |
|---|---|
| Cloud Function `sync_stocks` | Triggered daily by Cloud Scheduler. Performs an incremental EOD pull from Yahoo Finance — idempotent, fetches only dates not yet in BigQuery |
| GCS Bucket | Receives each daily batch as NDJSON before any database write, serving as an immutable bronze-layer audit trail |
| BigQuery `stock_prices` | System of record for all historical OHLCV data. Append-only with read-time deduplication via `ROW_NUMBER() OVER (PARTITION BY symbol, trade_date)` |

### 2. Event-Driven Cache Orchestration

On successful ingestion, the Cloud Function fires an async webhook to the FastAPI backend at `/api/admin/refresh`. The backend responds immediately and runs a two-phase background refresh: first loading only the latest trade date into DuckDB (~1 second, makes the sidebar immediately available), then replacing the full historical dataset atomically. The API response cache is cleared on completion.

### 3. Serving Layer — Cloud Run API

A FastAPI application on Cloud Run with dedicated CPU allocation, serving REST endpoints and WebSocket connections backed by a two-layer cache:

| Layer | Technology | TTL | Purpose |
|---|---|---|---|
| Response Cache | Python dict `API_CACHE` | 30 minutes | Eliminates redundant DuckDB queries |
| Data Cache | DuckDB In-Memory | Until webhook | Reduces query latency from 2–5s to under 30ms |

All analytical queries use DuckDB window functions (`LAG`, `FIRST_VALUE`, `LAST_VALUE`, `AVG OVER ROWS`) in single-pass operations. A composite index on `(symbol, trade_date)` supports sub-millisecond lookups.

**REST Endpoints**

| Endpoint | Description |
|---|---|
| `GET /summary` | All tickers with daily change, high, low, volume |
| `GET /data/{symbol}?period=` | Historical OHLCV with MA30 and MA90 |
| `GET /rankings?period=` | Top and bottom three period performers |
| `GET /metadata/{symbol}` | Company name for chart header |
| `POST /api/admin/refresh` | Webhook receiver — triggers cache reload |

### 4. Real-Time Feed — WebSocket Broadcaster

A persistent async background task polls two sources at different cadences and broadcasts updates to all connected clients. New connections receive an immediate snapshot of the last-known state before entering the live stream.

| Source | Assets | Interval |
|---|---|---|
| Binance REST API | BTC/USDT | Every 5 seconds |
| Yahoo Finance | NVDA, AAPL, MSFT, XAU/USD, EUR/USD | Every 60 seconds |

NYSE market hours are checked via `pandas_market_calendars` on each poll cycle, driving the LIVE/CLOSED indicator in the UI.

### 5. Frontend — SvelteKit on Firebase

The SvelteKit application is deployed on Firebase Hosting with global CDN edge distribution, automatic SSL, and Brotli/Gzip compression. SSR is disabled — the dashboard is fully client-rendered.

A centralized store architecture (`stores.js`) ensures data is fetched once and shared across components. Summary data loads on mount and is never reloaded. Period changes affect only the RankingPanel. Chart data is fetched at `period=max` on symbol load and filtered client-side for all period views. The RankingPanel prefetches all period variants on mount for instant switching.

---

## Project Structure

```text
EXCHANGE_GCP/
├── backend/
│   └── main.py                  # FastAPI, DuckDB, WebSocket, caching
├── ingestion/
│   └── main.py                  # Cloud Function: Yahoo Finance → GCS → BigQuery → Webhook
├── frontend/
│   ├── src/
│   │   ├── routes/
│   │   │   ├── +page.svelte     # Root layout and state orchestration
│   │   │   └── +page.js         # SSR and prerendering disabled
│   │   └── lib/
│   │       ├── stores.js        # Centralized reactive state
│   │       └── components/
│   │           ├── Sidebar.svelte
│   │           ├── Chart.svelte
│   │           ├── LiveIndicators.svelte
│   │           └── RankingPanel.svelte
│   └── firebase.json
└── docs/
    ├── Dashboard.jpg
    └── Diagram.jpg
```

---

## Technical Specifications

| Metric | Value |
|---|---|
| Initial page load | 1–2 seconds |
| Chart and rankings period switch | Instant — client-side, no network call |
| Symbol switch | ~200ms (cache hit) / ~1s (cache miss) |
| Crypto refresh | Every 5 seconds |
| Equity refresh | Every 60 seconds |
| DuckDB query latency | 10–30ms vs 2–5s raw BigQuery |
| API cache hit rate | ~95% |

**Decoupled architecture.** Ingestion, storage, and serving layers operate and recover independently. A failure in any one layer does not cascade to the others.

**Hybrid data model.** Accurate EOD historical data from BigQuery is combined with live intraday price action from WebSocket feeds, giving the chart both depth and recency.

**Compiler-first frontend.** Svelte eliminates the Virtual DOM, surgically updating only the DOM nodes that change when WebSocket ticks arrive — well suited for a high-frequency, multi-panel price dashboard.
