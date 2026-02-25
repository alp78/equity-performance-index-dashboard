# Backend — FastAPI + DuckDB

Real-time market data API server powering the Global Exchange Monitor dashboard.

## Stack

- **FastAPI** — async REST endpoints + WebSocket broadcasts
- **DuckDB** — in-memory OLAP engine hydrated from BigQuery
- **BigQuery** — source of truth for all historical price data
- **httpx** — persistent connection pools for external APIs (Binance, Finnhub, FRED, Frankfurter)


## Key Endpoints

| Endpoint | Description |
|---|---|
| `GET /summary?index={idx}` | Stock list with latest prices, change %, sector |
| `GET /data?symbol={sym}&index={idx}` | OHLCV time series for charting |
| `GET /rankings?period={p}&index={idx}` | Top/bottom movers by return |
| `GET /most-active?period={p}&index={idx}` | Top 3 stocks by avg daily volume |
| `GET /technicals/{sym}?period={p}` | RSI, MACD, Bollinger, ATR, Beta |
| `GET /sector-comparison/*` | Sector/industry analytics |
| `GET /index-prices/*` | Index-level price series and stats |
| `GET /correlation?period={p}` | Cross-index correlation matrix |
| `GET /news` | Aggregated financial news feed |
| `GET /macro/*` | FX rates, risk summary, economic calendar |
| `WS /ws` | Real-time BTC + macro instrument ticks |
| `GET /health` | Health check |

## Architecture

- **Two-phase startup**: Priority indices (STOXX 50, S&P 500) load first, then remaining indices in parallel
- **RWLock**: Concurrent reads, exclusive writes on DuckDB
- **Circuit breakers**: Per-provider (Binance, Finnhub, FRED, Frankfurter) with configurable thresholds
- **Cache**: In-process LRU dict with per-endpoint TTLs, singleflight to prevent stampedes, SWR headers
- **SQL templates**: All queries in `sql/` directory (46 files), loaded via LRU-cached reader

## Project Structure

```
backend/
├── main.py            # FastAPI application (endpoints, startup, feeds)
├── index_config.py    # Index configuration loader (from config/indices.json)
├── requirements.txt   # Pinned Python dependencies
├── Dockerfile         # Cloud Run container definition
├── config/
│   └── indices.json   # Index metadata (tickers, exchanges, colors)
└── sql/               # 46 SQL template files
    ├── summary.sql
    ├── rankings_period.sql
    ├── rankings_custom.sql
    ├── technicals_*.sql
    ├── sector_*.sql
    └── ...
```
