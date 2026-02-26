# Global Exchange Monitor

Real-time financial dashboard for tracking and comparing global stock market indices. Built on Google Cloud Platform with SvelteKit, FastAPI, BigQuery, and DuckDB. Index coverage is fully configurable via a single JSON manifest — any equity index with publicly available EOD pricing can be added.

## Dashboard Modes

- **Index Benchmarks** — Normalized % change comparison across all configured indices, with local currency and USD-adjusted views. Includes a performance stats table, Pearson correlation heatmap, aggregated news feed, and economic calendar.
- **Sector Analysis** — Cross-index sector comparison (how does "Financials" perform across markets?) or single-index sector breakdown. Industry-level drill-down with donut charts, per-sector top/bottom stock rankings, and sector-weighted return overlays.
- **Stock Browser** — Individual stock OHLCV candlestick chart with moving-average overlays. Technical indicator panels (RSI, MACD, Bollinger Bands, ATR, Beta). Top movers by return and most-active stocks by volume surge.
- **Macro Context** — Risk dashboard (VIX, MOVE, credit spreads, yield curve, USD index), live BTC/Gold/EUR-USD tickers via WebSocket, FX cross-rates, central bank interest rates, and economic calendar.

## Architecture

![System Architecture](docs/Diagram.jpg)

### Data Pipeline

1. **Ingestion** — A Cloud Function runs daily via Cloud Scheduler, fetching end-of-day prices from Yahoo Finance with exchange-calendar-aware gap detection. Data is archived as NDJSON in Cloud Storage and appended to BigQuery.
2. **Cache Hydration** — On ingestion completion, a webhook triggers the backend to pull fresh data from BigQuery into an in-memory DuckDB instance. Two-phase startup loads priority indices first, then remaining indices in parallel.
3. **Serving** — FastAPI on Cloud Run exposes 40+ REST endpoints with per-endpoint TTL caching, singleflight deduplication, and stale-while-revalidate headers. A WebSocket feed broadcasts live crypto and macro instrument ticks.
4. **Presentation** — SvelteKit SPA on Firebase Hosting consumes REST snapshots and WebSocket streams. Svelte 5 runes enable surgical DOM updates for real-time price movements.

### Key Technical Decisions

- **DuckDB in-memory** — Sub-millisecond OLAP queries without per-request BigQuery costs. An RWLock serializes concurrent reads during write operations.
- **Circuit breakers** — Per-provider fault isolation (Binance, Finnhub, FRED, Frankfurter) with configurable failure thresholds and automatic recovery.
- **Precomputed sector series** — Forward-filled sector and industry time series are materialized in DuckDB on startup, enabling instant mode switching and sector selection.
- **Hybrid data model** — Accurate EOD historical data from BigQuery combined with live intraday ticks from external WebSocket feeds.
- **USD adjustment** — Historical ECB daily FX rates convert all index prices and returns to a common USD basis for cross-market comparison.

## Project Structure

```
global-exchange-monitor/
├── backend/                  # FastAPI + DuckDB serving layer
│   ├── main.py               # API server — endpoints, startup, WebSocket feeds
│   ├── index_config.py       # Index configuration loader
│   ├── sql/                  # SQL template files for all queries
│   └── config/               # indices.json (synced copy from root)
├── ingestion/                # Cloud Function — daily EOD price sync
│   └── main.py               # Gap detection, Yahoo Finance download, BigQuery load
├── frontend/                 # SvelteKit 5 + Tailwind v4 dashboard
│   ├── src/lib/
│   │   ├── stores.js         # Svelte stores, persistence, global state
│   │   ├── data-loaders.js   # Data fetching with SWR caching
│   │   ├── cache.js          # In-memory + localStorage cache layer
│   │   ├── format.js         # Shared number/date formatters
│   │   ├── index-registry.js # Index metadata derived from indices.json
│   │   ├── theme.js          # Color tokens, sector palette, theme utilities
│   │   ├── styles/           # Design tokens, dark/light themes, responsive breakpoints
│   │   └── components/
│   │       ├── ui/           # Primitive UI components (Card, Badge, Stat, Tooltip, etc.)
│   │       ├── sidebar/      # Navigation + mode-specific sidebar panels
│   │       ├── shared/       # Cross-mode components (PriceChart, LiveTicker)
│   │       ├── stock/        # Stock browsing panels
│   │       ├── sector/       # Sector analysis panels
│   │       └── macro/        # Macro/overview panels
│   └── src/routes/           # Single-page dashboard orchestrator
├── config/
│   └── indices.json          # Single source of truth for all index metadata
└── docs/                     # Architecture diagram
```

### Backend API

The backend serves structured market data through REST endpoints and real-time data via WebSocket:

| Category | Endpoints |
|---|---|
| Stock data | `/summary`, `/data`, `/rankings`, `/most-active`, `/technicals/{symbol}` |
| Index data | `/index-prices/data`, `/index-prices/stats`, `/index-prices/single/{symbol}` |
| Sectors | `/sector-comparison/table`, `/sector-comparison/sectors`, `/sector-comparison/industries` |
| Macro | `/correlation`, `/news`, `/macro/calendar`, `/macro/risk-summary`, `/macro/fx`, `/macro/rates` |
| Real-time | `WS /ws` — live crypto and macro instrument ticks |
| System | `/health`, `/api/admin/refresh/{index}` |

### Frontend Design System

- **Fonts**: Geist + Geist Mono (CDN-loaded), Inter fallback
- **Themes**: Dark (primary) and light, toggled via `data-theme` attribute
- **Tokens**: CSS custom properties for spacing, radius, shadows, chart palette, numeric font sizes
- **Responsive**: 7 breakpoints from 375px to 1920px+, container queries for component-level adaptation
- **Charts**: TradingView lightweight-charts for time series, ECharts for pie/donut visualizations

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | SvelteKit 2, Svelte 5, Tailwind CSS v4, lightweight-charts, ECharts |
| Backend | FastAPI, DuckDB, pandas, httpx, WebSockets |
| Data | BigQuery, Cloud Storage, Yahoo Finance, Binance, Finnhub, FRED, Frankfurter |
| Infrastructure | Cloud Run, Cloud Functions, Cloud Scheduler, Firebase Hosting |
