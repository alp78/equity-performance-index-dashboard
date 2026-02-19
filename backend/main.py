"""
Exchange Dashboard — Backend API
=================================
FastAPI server that powers the real-time stock market dashboard.

Architecture:
  BigQuery (warehouse) → DuckDB (in-memory cache) → REST API / WebSocket → Frontend

Key design decisions:
  - DuckDB acts as a local read-replica of BigQuery, giving sub-millisecond query times
  - WebSocket pushes live prices to all connected browsers (pub/sub pattern)
  - Crypto prices update every 5s (Binance), stock prices every 60s (Yahoo Finance)
  - Supports multiple market indices (S&P 500, EURO STOXX 50) via a single codebase
"""

from os import getenv
import pandas as pd
import duckdb
import asyncio
import requests
import json
import uvicorn
import yfinance as yf
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from urllib.parse import unquote
from contextlib import asynccontextmanager


# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ID = getenv("PROJECT_ID")

# Each market index maps to its own BigQuery table.
# To add a new index, just add an entry here — the rest of the code adapts automatically.
MARKET_INDICES = {
    "sp500":   {"table_id": f"{PROJECT_ID}.stock_exchange.stock_prices" if PROJECT_ID else None},
    "stoxx50": {"table_id": f"{PROJECT_ID}.stock_exchange.stoxx50_prices" if PROJECT_ID else None},
}

# Yahoo Finance symbols → frontend display symbols.
# The frontend expects "FXCM:XAU/USD", but Yahoo calls it "GC=F".
DISPLAY_SYMBOL_MAP = {
    "GC=F":      "FXCM:XAU/USD",
    "EURUSD=X":  "FXCM:EUR/USD",
}


# ============================================================================
# IN-MEMORY CACHE LAYER
# ============================================================================

# DuckDB: sub-millisecond SQL queries on data loaded from BigQuery at startup.
local_db = duckdb.connect(database=':memory:', read_only=False)

# Stores the last known price per symbol so new WebSocket clients get data immediately.
LATEST_MARKET_DATA = {}

# API response cache with a short TTL to avoid redundant DuckDB queries.
API_CACHE = {}
CACHE_TTL = 5  # seconds

def get_cached_response(cache_key):
    """Return cached data if it exists and hasn't expired."""
    if cache_key in API_CACHE:
        data, timestamp = API_CACHE[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return data
    return None

def set_cached_response(cache_key, data):
    """Store a response in the cache with the current timestamp."""
    API_CACHE[cache_key] = (data, time.time())


# ============================================================================
# WEBSOCKET CONNECTION MANAGER
# ============================================================================

class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages to all clients."""

    def __init__(self):
        self.active_connections = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        """Push a message to every connected browser tab."""
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass  # Client disconnected mid-send — safe to ignore

manager = ConnectionManager()


# ============================================================================
# DATA INGESTION — BigQuery → DuckDB
# ============================================================================

async def refresh_duckdb_cache():
    """
    Pull all historical price data from BigQuery into DuckDB.
    
    Runs once at startup and again whenever the Cloud Function webhook
    signals that new daily data has been loaded into BigQuery.
    """
    print(f"Connecting to BigQuery (Project: {PROJECT_ID})...")
    if not PROJECT_ID:
        return

    try:
        bq_client = bigquery.Client(project=PROJECT_ID)
        all_dfs = []

        for index_key, config in MARKET_INDICES.items():
            try:
                # Deduplicate rows in BigQuery (keep highest volume per symbol+date)
                query = f"""
                    WITH RankedData AS (
                        SELECT symbol, name,
                            CAST(trade_date AS DATE) as trade_date,
                            CAST(close_price AS FLOAT64) as close,
                            CAST(high_price AS FLOAT64) as high,
                            CAST(low_price AS FLOAT64) as low,
                            CAST(volume AS INT) as volume,
                            ROW_NUMBER() OVER (
                                PARTITION BY symbol, trade_date ORDER BY volume DESC
                            ) as rn
                        FROM `{config['table_id']}`
                    )
                    SELECT symbol, name, trade_date, close, high, low, volume
                    FROM RankedData WHERE rn = 1
                """
                df = bq_client.query(query).to_dataframe()
                if not df.empty:
                    df['trade_date'] = pd.to_datetime(df['trade_date'])
                    df['market_index'] = index_key
                    all_dfs.append(df)
                    print(f"   Loaded {len(df)} rows for {index_key}")
            except Exception as e:
                print(f"   Error {index_key}: {e}")

        if all_dfs:
            full_df = pd.concat(all_dfs, ignore_index=True)

            # Rebuild the main prices table
            local_db.execute("DROP TABLE IF EXISTS prices")
            local_db.register('temp_full', full_df)
            local_db.execute("CREATE TABLE prices AS SELECT * FROM temp_full")
            local_db.execute(
                "CREATE INDEX IF NOT EXISTS idx_main ON prices (market_index, symbol, trade_date)"
            )

            # Pre-compute a "latest snapshot" table used by the sidebar.
            # Includes yesterday's close (prev_price) for daily change calculation.
            local_db.execute("DROP TABLE IF EXISTS latest_prices")
            local_db.execute("""
                CREATE TABLE latest_prices AS
                SELECT symbol, name, market_index, trade_date, close, high, low, volume,
                    LAG(close) OVER (
                        PARTITION BY symbol, market_index ORDER BY trade_date
                    ) as prev_price
                FROM prices
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol, market_index ORDER BY trade_date DESC
                ) = 1
            """)

            # Invalidate all cached API responses since underlying data changed
            API_CACHE.clear()
            print(f"CACHE READY: {len(full_df)} rows.")

    except Exception as e:
        print(f"BigQuery Error: {e}")


# ============================================================================
# REAL-TIME DATA FEEDS
# ============================================================================

async def fetch_crypto_data():
    """
    Fetch live BTC price from Binance.
    
    Uses the daily kline (candle) endpoint to get the UTC 00:00 open price,
    which matches how aggregators like Yahoo and CoinGecko calculate daily change.
    """
    try:
        # Current spot price
        ticker_r = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=2
        )
        if ticker_r.status_code != 200:
            return
        current_price = float(ticker_r.json()['price'])

        # Today's daily candle — kline[1] is the UTC midnight open price
        kline_r = requests.get(
            "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1",
            timeout=2,
        )
        if kline_r.status_code != 200:
            return

        day_open = float(kline_r.json()[0][1])

        # Daily change vs UTC midnight open
        diff = current_price - day_open
        pct = (diff / day_open) * 100 if day_open != 0 else 0

        payload = {
            "symbol": "BINANCE:BTCUSDT",
            "price": round(current_price, 2),
            "diff": round(diff, 2),
            "pct": round(pct, 2),
        }

        LATEST_MARKET_DATA["BINANCE:BTCUSDT"] = payload
        if manager.active_connections:
            await manager.broadcast(json.dumps(payload))

    except Exception as e:
        print(f"Crypto Fetch Error: {e}")


async def fetch_stock_data():
    """
    Fetch stock, forex, and gold prices from Yahoo Finance.
    
    Covers three asset classes:
      - US equities (NVDA, AAPL, MSFT, AMZN)
      - European equities (ASML, SAP, LVMH — via their local exchange tickers)
      - Macro indicators (Gold, EUR/USD)
    """
    targets = [
        "NVDA", "AAPL", "MSFT", "AMZN",         # US equities
        "GC=F", "EURUSD=X",                       # Gold & Forex
        "ASML.AS", "SAP.DE", "MC.PA",             # EU equities (Amsterdam, Frankfurt, Paris)
    ]

    # European Yahoo tickers → clean display names for the frontend
    eu_display_map = {
        "ASML.AS": "ASML",
        "SAP.DE":  "SAP",
        "MC.PA":   "LVMH",
    }

    try:
        data = yf.download(
            targets, period="5d", interval="1d",
            progress=False, group_by='ticker', threads=True, prepost=True,
        )
        if data.empty:
            return

        is_multi = isinstance(data.columns, pd.MultiIndex)

        for symbol in targets:
            try:
                df = data[symbol] if is_multi else data
                if not is_multi and len(targets) == 1:
                    df = data

                # If the latest row is empty (market closed), fall back to last valid day
                if pd.isna(df['Close'].iloc[-1]):
                    df = df.dropna(subset=['Close'])
                if len(df) < 2:
                    continue

                current = float(df['Close'].iloc[-1])
                prev = float(df['Close'].iloc[-2])
                if pd.isna(current) or current == 0:
                    continue

                diff = current - prev
                pct = ((current - prev) / prev) * 100 if prev != 0 else 0

                # Resolve the display symbol for the frontend
                if symbol in DISPLAY_SYMBOL_MAP:
                    display_symbol = DISPLAY_SYMBOL_MAP[symbol]
                elif symbol in eu_display_map:
                    display_symbol = eu_display_map[symbol]
                else:
                    display_symbol = symbol

                # EUR/USD needs more decimal precision than equities
                is_fx = symbol == "EURUSD=X"

                payload = {
                    "symbol": display_symbol,
                    "price": round(current, 6 if is_fx else 2),
                    "diff":  round(diff,    6 if is_fx else 2),
                    "pct":   round(pct,     4 if is_fx else 2),
                    "live":  True,
                }

                LATEST_MARKET_DATA[display_symbol] = payload
                if manager.active_connections:
                    await manager.broadcast(json.dumps(payload))

            except Exception as e:
                print(f"Error processing {symbol}: {e}")
                continue

    except Exception as e:
        print(f"Stock Fetch Global Error: {e}")


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

async def market_data_feeder():
    """
    Background loop that polls external APIs on two schedules:
      - Crypto:  every 5 seconds  (Binance is fast and free)
      - Stocks:  every 60 seconds (Yahoo rate-limits aggressive polling)
    
    Waits for the DuckDB cache to be ready before starting.
    """
    # Wait until historical data is loaded
    while True:
        try:
            if local_db.execute("SELECT COUNT(*) FROM prices").fetchone()[0] > 0:
                break
        except:
            pass
        await asyncio.sleep(1)

    print("Real-time Feeder: Running initial bootstrap...")
    await fetch_crypto_data()
    await fetch_stock_data()

    last_crypto = asyncio.get_event_loop().time()
    last_stock = asyncio.get_event_loop().time()
    CRYPTO_INTERVAL = 5
    STOCK_INTERVAL = 60

    print("Real-time Feeder: Dual-speed loop active.")
    while True:
        now = asyncio.get_event_loop().time()
        if now - last_crypto > CRYPTO_INTERVAL:
            await fetch_crypto_data()
            last_crypto = now
        if now - last_stock > STOCK_INTERVAL:
            await fetch_stock_data()
            last_stock = now
        await asyncio.sleep(1)


async def background_startup():
    """Orchestrates the two startup tasks: cache load + real-time feeder."""
    await refresh_duckdb_cache()
    asyncio.create_task(market_data_feeder())


# ============================================================================
# APP LIFECYCLE
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs on server start/stop.
    Startup tasks are fire-and-forget so the API accepts requests immediately.
    """
    asyncio.create_task(background_startup())
    yield
    local_db.close()

app = FastAPI(lifespan=lifespan)

# Allow the frontend (any origin) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# ENDPOINTS — WebSocket
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time price stream.
    On connect, immediately sends the latest known prices (hydration),
    then keeps the connection open for live updates via broadcast.
    """
    await manager.connect(websocket)
    try:
        # Send current snapshot so new clients don't see "waiting..."
        for payload in list(LATEST_MARKET_DATA.values()):
            await websocket.send_text(json.dumps(payload))
        # Keep connection alive (messages from client are ignored)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ============================================================================
# ENDPOINTS — REST API
# ============================================================================

@app.get("/health")
async def health():
    """Health check — returns row count so monitors can verify data is loaded."""
    try:
        return {
            "status": "ok",
            "rows": local_db.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
        }
    except:
        return {"status": "warming_up"}


@app.post("/api/admin/refresh")
async def webhook_refresh():
    """
    Webhook called by the Cloud Function after new daily data is synced to BigQuery.
    Triggers a background reload of the DuckDB cache without blocking the response.
    """
    asyncio.create_task(refresh_duckdb_cache())
    return {"status": "accepted", "message": "Refresh task started"}


@app.get("/summary")
async def get_summary(index: str = "sp500"):
    """
    Returns the latest snapshot for all tickers in a given index.
    Used by the sidebar to show price, daily change, high/low, and volume.
    """
    if index not in MARKET_INDICES:
        index = "sp500"
    cache_key = f"summary_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        res = local_db.execute(f"""
            SELECT symbol, name,
                CAST(close AS FLOAT) as last_price,
                CAST(high AS FLOAT) as high,
                CAST(low AS FLOAT) as low,
                CAST(volume AS BIGINT) as volume,
                trade_date,
                CAST(((close - prev_price) / NULLIF(prev_price, 0)) * 100 AS FLOAT) as daily_change_pct
            FROM latest_prices
            WHERE market_index = '{index}'
            ORDER BY symbol
        """).df().fillna(0).to_dict(orient='records')
        set_cached_response(cache_key, res)
        return res
    except:
        return []


@app.get("/data/{symbol:path}")
async def get_data(symbol: str, period: str = "1y"):
    """
    Returns historical price data for a single ticker with moving averages.
    
    The frontend fetches period=max and filters client-side for instant period switching.
    Supports: 1w, 1mo, 3mo, 6mo, 1y, 5y, max.
    """
    symbol = unquote(symbol)
    cache_key = f"data_{symbol}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    intervals = {
        "1w": 7, "1mo": 30, "3mo": 90,
        "6mo": 180, "1y": 365, "5y": 1825,
    }

    try:
        if period.lower() == "max":
            df = local_db.execute("""
                SELECT strftime(trade_date, '%Y-%m-%d') as time, close, volume,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM prices
                WHERE symbol = ?
                ORDER BY trade_date ASC
            """, [symbol]).df()
        else:
            days = intervals.get(period.lower(), 365)
            df = local_db.execute(f"""
                SELECT strftime(trade_date, '%Y-%m-%d') as time, close, volume,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM prices
                WHERE symbol = ?
                  AND trade_date >= (
                      SELECT MAX(trade_date) FROM prices WHERE symbol = ?
                  ) - INTERVAL {days} DAY
                ORDER BY trade_date ASC
            """, [symbol, symbol]).df()

        result = df.fillna(0).to_dict(orient='records')
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Data Error: {e}")
        return []


@app.get("/rankings")
async def get_rankings(period: str = "1y", index: str = "sp500"):
    """
    Returns top 3 and bottom 3 performers for a given index and time period.
    Used by the RankingPanel component to display performance bar charts.
    """
    if index not in MARKET_INDICES:
        index = "sp500"
    cache_key = f"rankings_{period}_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    intervals = {
        "1w": 7, "1mo": 30, "3mo": 90,
        "6mo": 180, "1y": 365, "5y": 1825,
    }

    try:
        if period.lower() == "max":
            df = local_db.execute(f"""
                WITH Ranked AS (
                    SELECT symbol,
                        FIRST_VALUE(close) OVER (
                            PARTITION BY symbol ORDER BY trade_date ASC
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        ) as start_price,
                        LAST_VALUE(close) OVER (
                            PARTITION BY symbol ORDER BY trade_date ASC
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        ) as end_price,
                        ROW_NUMBER() OVER (
                            PARTITION BY symbol ORDER BY trade_date DESC
                        ) as rn
                    FROM prices
                    WHERE market_index = '{index}'
                )
                SELECT symbol,
                    ((end_price - start_price) / NULLIF(start_price, 0)) * 100 as value
                FROM Ranked
                WHERE rn = 1
                ORDER BY value DESC
            """).df()
        else:
            days = intervals.get(period.lower(), 365)
            df = local_db.execute(f"""
                WITH Filtered AS (
                    SELECT symbol, close, trade_date
                    FROM prices
                    WHERE market_index = '{index}'
                      AND trade_date >= (
                          SELECT MAX(trade_date) FROM prices
                          WHERE market_index = '{index}'
                      ) - INTERVAL {days} DAY
                ),
                Ranked AS (
                    SELECT symbol,
                        FIRST_VALUE(close) OVER (
                            PARTITION BY symbol ORDER BY trade_date ASC
                        ) as first_val,
                        LAST_VALUE(close) OVER (
                            PARTITION BY symbol ORDER BY trade_date ASC
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                        ) as last_val,
                        ROW_NUMBER() OVER (
                            PARTITION BY symbol ORDER BY trade_date DESC
                        ) as rn
                    FROM Filtered
                )
                SELECT symbol,
                    ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
                FROM Ranked
                WHERE rn = 1
                ORDER BY value DESC
            """).df()

        result = {
            "selected": {
                "top":    df.head(3).to_dict('records'),
                "bottom": df.tail(3).sort_values('value').to_dict('records'),
            }
        }
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Ranking Error: {e}")
        return {"selected": {"top": [], "bottom": []}}


@app.get("/metadata/{symbol:path}")
async def metadata(symbol: str):
    """Quick lookup for a ticker's company name. Used by the chart header."""
    symbol = unquote(symbol)
    try:
        res = local_db.execute(
            "SELECT name FROM prices WHERE symbol = ? LIMIT 1", [symbol]
        ).fetchone()
        return {"symbol": symbol, "name": res[0] if res else symbol}
    except:
        return {"symbol": symbol, "name": symbol}


@app.get("/rankings/custom")
async def get_custom_rankings(start: str, end: str, index: str = "sp500"):
    """
    Returns top 3 and bottom 3 performers between two arbitrary dates.
    Used by the RankingPanel when the user selects a custom date range on the chart.
    
    Query params:
      start — start date (YYYY-MM-DD)
      end   — end date (YYYY-MM-DD)
      index — market index key
    """
    if index not in MARKET_INDICES:
        index = "sp500"
    cache_key = f"rankings_custom_{start}_{end}_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        df = local_db.execute(f"""
            WITH Filtered AS (
                SELECT symbol, close, trade_date
                FROM prices
                WHERE market_index = '{index}'
                  AND trade_date >= '{start}'
                  AND trade_date <= '{end}'
            ),
            Ranked AS (
                SELECT symbol,
                    FIRST_VALUE(close) OVER (
                        PARTITION BY symbol ORDER BY trade_date ASC
                    ) as first_val,
                    LAST_VALUE(close) OVER (
                        PARTITION BY symbol ORDER BY trade_date ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                    ) as last_val,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol ORDER BY trade_date DESC
                    ) as rn
                FROM Filtered
            )
            SELECT symbol,
                ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
            FROM Ranked
            WHERE rn = 1
            ORDER BY value DESC
        """).df()

        result = {
            "selected": {
                "top":    df.head(3).to_dict('records'),
                "bottom": df.tail(3).sort_values('value').to_dict('records'),
            }
        }
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Custom Ranking Error: {e}")
        return {"selected": {"top": [], "bottom": []}}


@app.get("/rankings/custom")
async def get_rankings_custom(start: str, end: str, index: str = "sp500"):
    """
    Returns top 3 and bottom 3 performers between two arbitrary dates.
    Used by the brush selection feature on the chart.
    
    Example: /rankings/custom?start=2024-03-01&end=2024-06-15&index=sp500
    """
    if index not in MARKET_INDICES:
        index = "sp500"
    cache_key = f"rankings_custom_{start}_{end}_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        df = local_db.execute(f"""
            WITH Filtered AS (
                SELECT symbol, close, trade_date
                FROM prices
                WHERE market_index = '{index}'
                  AND trade_date >= '{start}'
                  AND trade_date <= '{end}'
            ),
            Ranked AS (
                SELECT symbol,
                    FIRST_VALUE(close) OVER (
                        PARTITION BY symbol ORDER BY trade_date ASC
                    ) as first_val,
                    LAST_VALUE(close) OVER (
                        PARTITION BY symbol ORDER BY trade_date ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                    ) as last_val,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol ORDER BY trade_date DESC
                    ) as rn
                FROM Filtered
            )
            SELECT symbol,
                ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
            FROM Ranked
            WHERE rn = 1
            ORDER BY value DESC
        """).df()

        result = {
            "selected": {
                "top":    df.head(3).to_dict('records'),
                "bottom": df.tail(3).sort_values('value').to_dict('records'),
            },
            "range": {"start": start, "end": end},
        }
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Custom Ranking Error: {e}")
        return {"selected": {"top": [], "bottom": []}, "range": {"start": start, "end": end}}


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    # Locally defaults to port 8000; Cloud Run injects PORT=8080 via environment
    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)