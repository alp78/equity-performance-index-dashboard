from os import getenv
import logging
import pandas as pd
import duckdb
import asyncio
import json
import pytz
import requests
import uvicorn
import yfinance as yf
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from urllib.parse import unquote
from contextlib import asynccontextmanager
from datetime import datetime
import pandas_market_calendars as mcal

# --- CONFIGURATION ---
PROJECT_ID = getenv("PROJECT_ID")

# --- IN-MEMORY DATABASE (CACHE LAYER) ---
# We use DuckDB instead of querying BigQuery for every request.
# Why? BigQuery has latency (2-3s) and costs money per query.
# DuckDB lives in RAM, offers sub-millisecond response times, and supports
# complex Window Functions (for Moving Averages) natively.
local_db = duckdb.connect(database=':memory:', read_only=False)

# --- REAL-TIME STATE CACHE ---
# Stores the last known price for every symbol (BTC, NVDA, etc.).
# Used to "Hydrate" new WebSocket clients immediately so they don't see "Waiting...".
LATEST_MARKET_DATA = {}

# --- WEBSOCKET CONNECTION MANAGER ---
# Handles the lifecycle of real-time clients.
class ConnectionManager:
    def __init__(self):
        # A list to keep track of all currently connected browser sessions
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"WS Manager: New connection. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # Pushes a message to ALL connected clients (Pub/Sub pattern)
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass # If a client disconnects mid-send, ignore it

manager = ConnectionManager()

# --- DATA INGESTION (ETL) ---
# This function pulls historical data from BigQuery into DuckDB on startup.
async def refresh_duckdb_cache():
    print("Syncing DuckDB cache (Phase 1: Latest Snapshot)...")
    bq_client = bigquery.Client(project=PROJECT_ID)
    
    try:
        # Step 1: Fetch ONLY the most recent day for all tickers.
        # This is a tiny query that finishes in ~1 second.
        latest_query = f"""
            SELECT symbol, name, trade_date, 
                   close_price as close, high_price as high, 
                   low_price as low, volume
            FROM `{PROJECT_ID}.stock_exchange.stock_prices`
            WHERE trade_date = (SELECT MAX(trade_date) FROM `{PROJECT_ID}.stock_exchange.stock_prices`)
        """
        latest_df = bq_client.query(latest_query).to_dataframe()
        latest_df['trade_date'] = pd.to_datetime(latest_df['trade_date'])

        # Load this into a temporary table so the Sidebar can work immediately
        local_db.execute("CREATE OR REPLACE TABLE prices AS SELECT * FROM latest_df")
        local_db.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON prices (symbol)")
        print(f"Sidebar Ready: Loaded {len(latest_df)} tickers.")

        # Step 2: Now fetch the FULL history in the background (Phase 2)
        print("Syncing DuckDB cache (Phase 2: Full History)...")
        history_query = f"""
            SELECT symbol, name, CAST(trade_date AS DATE) as trade_date,
                   CAST(close_price AS FLOAT64) as close, CAST(high_price AS FLOAT64) as high,
                   CAST(low_price AS FLOAT64) as low, CAST(volume AS INT) as volume
            FROM `{PROJECT_ID}.stock_exchange.stock_prices`
        """
        full_df = bq_client.query(history_query).to_dataframe()
        full_df['trade_date'] = pd.to_datetime(full_df['trade_date'])

        # Atomically swap the tiny table for the full table
        local_db.register('temp_full', full_df)
        local_db.execute("CREATE OR REPLACE TABLE prices AS SELECT * FROM temp_full")
        local_db.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON prices (symbol)")
        
        print(f"Full Cache Synced: Loaded {len(full_df)} rows.")
    except Exception as e:
        print(f"CRITICAL: Cache Sync Failed: {e}")

# --- REAL-TIME DATA HELPERS ---
# We split these out so we can call them immediately on startup (Bootstrap)
# and then repeatedly in the loop.

async def fetch_crypto_data():
    """Fetches Crypto data (Binance) - Fast & Cheap."""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=2)
        if r.status_code == 200:
            data = r.json()
            current_price = float(data['lastPrice'])
            prev_close = float(data['prevClosePrice'])
            
            diff = current_price - prev_close
            pct = float(data['priceChangePercent'])
            
            payload = {
                "symbol": "BINANCE:BTCUSDT",
                "price": round(current_price, 2),
                "diff": round(diff, 2),
                "pct": round(pct, 2)
            }
            
            LATEST_MARKET_DATA["BINANCE:BTCUSDT"] = payload
            if manager.active_connections:
                await manager.broadcast(json.dumps(payload))
    except Exception as e:
        print(f"Crypto Fetch Error: {e}")

async def fetch_stock_data():
    """Fetches Stock data (Yahoo) - Now with Holiday/Holiday awareness."""
    try:
        # 1. Market Status Check
        nyse = mcal.get_calendar('NYSE')
        now_utc = datetime.now(pytz.utc)
        schedule = nyse.schedule(start_date=now_utc.date(), end_date=now_utc.date())
        
        # Strictly False if holiday/weekend; otherwise check current time
        is_nyse_open = False if schedule.empty else nyse.open_at_time(schedule, now_utc)

        targets = ["GC=F", "EURUSD=X", "NVDA", "AAPL", "MSFT"]
        data = yf.download(targets, period="5d", interval="1d", progress=False, group_by='ticker', threads=True)
        
        is_multi = isinstance(data.columns, pd.MultiIndex)
        
        for symbol in targets:
            try:
                display_symbol = symbol
                if symbol == "GC=F": display_symbol = "FXCM:XAU/USD"
                if symbol == "EURUSD=X": display_symbol = "FXCM:EUR/USD"

                if is_multi:
                    if symbol not in data.columns.levels[0]: continue
                    df = data[symbol].dropna(subset=['Close'])
                else:
                    df = data.dropna(subset=['Close'])

                if df.empty: continue
                
                latest = df.iloc[-1]
                prev_close = float(df['Close'].iloc[-2]) if len(df) >= 2 else float(latest['Open'])
                current_price = float(latest['Close'])

                if pd.isna(current_price) or current_price == 0: continue

                # FIX: Keep diff as a raw float for maximum precision
                diff = current_price - prev_close
                pct = (diff / prev_close) * 100 if prev_close != 0 else 0
                
                is_equity = symbol in ["NVDA", "AAPL", "MSFT"]
                
                payload = {
                    "symbol": display_symbol,
                    "price": current_price, 
                    "diff": diff,
                    "pct": pct,
                    "live": True if not is_equity or is_nyse_open else False
                }
                
                LATEST_MARKET_DATA[display_symbol] = payload
                if manager.active_connections:
                    await manager.broadcast(json.dumps(payload))
            except Exception: continue
    except Exception as e:
        print(f"Stock Fetch Error: {e}")


# --- REAL-TIME DATA FEEDER ---
# Background task that manages the polling schedule.
async def market_data_feeder():
    # 1. Wait for DuckDB to be ready
    while True:
        try:
            res = local_db.execute("SELECT COUNT(*) FROM prices").fetchone()
            if res and res[0] > 0: break
        except: pass
        await asyncio.sleep(0.5)

    print("WebSocket Feeder: Database ready. Running initial bootstrap poll...")
    
    # 2. BOOTSTRAP: Fetch data IMMEDIATELY (Don't wait for loop)
    # This ensures LATEST_MARKET_DATA is full before the first user even connects.
    await fetch_crypto_data()
    await fetch_stock_data()
    
    last_crypto_update = asyncio.get_event_loop().time()
    last_stock_update = asyncio.get_event_loop().time()
    
    # Polling Intervals (Seconds)
    CRYPTO_INTERVAL = 5
    STOCK_INTERVAL = 60

    print("WebSocket Feeder: Bootstrap complete. Starting Dual-Speed Loop...")

    while True:
        now = asyncio.get_event_loop().time()
        
        # Fast Loop (Crypto)
        if now - last_crypto_update > CRYPTO_INTERVAL:
            await fetch_crypto_data()
            last_crypto_update = now
        
        # Slow Loop (Stocks)
        if now - last_stock_update > STOCK_INTERVAL:
            await fetch_stock_data()
            last_stock_update = now
        
        # Sleep 1s to prevent high CPU usage, but keep loop responsive
        await asyncio.sleep(1)

# --- STARTUP SYNC HELPER ---
# This runs the heavy data loading in the background
async def background_startup():
    # 1. Sync the historical cache
    await refresh_duckdb_cache()
    # 2. Start the real-time feeder task
    asyncio.create_task(market_data_feeder())

# --- APP LIFESPAN ---
# Controls what happens when the server Starts and Stops.
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Server Booting...")
    # Trigger everything in the background. 
    # We do NOT 'await' this, so the API starts responding to the frontend immediately.
    asyncio.create_task(background_startup())
    
    yield # App runs here
    
    # Cleanup on Shutdown
    local_db.close()

app = FastAPI(lifespan=lifespan)

# CORS Setup (Allows Frontend to talk to Backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ENDPOINTS ---

# --- METADATA ENDPOINT ---
# Provides near-instant name lookup for the UI header.
@app.get("/metadata/{symbol:path}")
async def get_asset_metadata(symbol: str):
    symbol = unquote(symbol)
    try:
        # This simple query is much faster than the full /summary endpoint
        res = local_db.execute("SELECT name FROM prices WHERE symbol = ? LIMIT 1", [symbol]).fetchone()
        return {
            "symbol": symbol,
            "name": res[0] if res else symbol
        }
    except Exception:
        return {"symbol": symbol, "name": symbol}

# 1. ADMIN REFRESH
# Can be called by a Cloud Scheduler or manually to force-reload BigQuery data
@app.post("/api/admin/refresh")
async def webhook_refresh():
    asyncio.create_task(refresh_duckdb_cache())
    return {"status": "accepted", "message": "Refresh task offloaded"}

# 2. DASHBOARD SUMMARY
# Returns the main list of assets with their daily % change.
@app.get("/summary")
async def get_summary():
    # SQL LOGIC:
    # 1. UniquePrices: Deduplicates rows (takes MAX if duplicates exist).
    # 2. Latest: Uses LAG() window function to compare Today vs Yesterday.
    #    (This avoids performing self-joins, which are slow).
    query = """
        WITH UniquePrices AS (
            SELECT 
                symbol,
                MAX(name) as name, 
                MAX(close) as close, 
                MAX(high) as high, 
                MAX(low) as low, 
                MAX(volume) as volume, 
                trade_date
            FROM prices
            GROUP BY symbol, trade_date
        ),
        Latest AS (
            SELECT
                symbol,
                name,
                close as last_price,
                high,
                low,
                volume,
                trade_date,
                LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_price,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
            FROM UniquePrices
        )
        SELECT
            symbol,
            name,
            CAST(last_price AS FLOAT) as last_price,
            CAST(high AS FLOAT) as high,
            CAST(low AS FLOAT) as low,
            CAST(volume AS BIGINT) as volume,
            trade_date,
            CAST(((last_price - prev_price) / NULLIF(prev_price, 0)) * 100 AS FLOAT) as daily_change_pct
        FROM Latest
        WHERE rn = 1
    """
    try:
        df = local_db.execute(query).df()
        return df.fillna(0).to_dict(orient='records')
    except Exception as e:
        print(f"Summary SQL Error: {e}")
        return []

# 3. CHART DATA
# Returns historical candles + Moving Averages.
# Supports ?period=1y filtering.
@app.get("/data/{symbol:path}")
async def get_data(symbol: str, period: str = "1y"):
    symbol = unquote(symbol)
    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}
    days_to_subtract = intervals.get(period.lower(), 365)
    
    try:
        # Find the last available date for this stock
        latest_res = local_db.execute("SELECT MAX(trade_date) FROM prices WHERE symbol = ?", [symbol]).fetchone()
        if not latest_res or not latest_res[0]: return []
        anchor_date = latest_res[0]

        # SQL LOGIC:
        # 1. Deduplicate (GROUP BY).
        # 2. Calculate Moving Averages (MA30, MA90) using Window Functions.
        if period.lower() == "max":
            query = """
                WITH DailyUnique AS (
                    SELECT symbol, trade_date, MAX(close) as close, MAX(volume) as volume
                    FROM prices 
                    WHERE symbol = ? 
                    GROUP BY symbol, trade_date
                )
                SELECT strftime(trade_date, '%Y-%m-%d') as time, close, volume,
                AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM DailyUnique ORDER BY trade_date ASC
            """
            df = local_db.execute(query, [symbol]).df()
        else:
            query = f"""
                WITH DailyUnique AS (
                    SELECT symbol, trade_date, MAX(close) as close, MAX(volume) as volume
                    FROM prices
                    WHERE symbol = ?
                    GROUP BY symbol, trade_date
                )
                SELECT strftime(trade_date, '%Y-%m-%d') as time, close, volume,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM DailyUnique
                WHERE trade_date >= CAST(? AS DATE) - INTERVAL {days_to_subtract} DAY
                ORDER BY trade_date ASC
            """
            df = local_db.execute(query, [symbol, anchor_date]).df()
        return df.replace({float('nan'): 0}).to_dict(orient='records')
    except Exception as e:
        logging.error(f"DuckDB Error: {e}")
        return []

# 4. RANKINGS (TOP/BOTTOM PERFORMERS)
# Calculates performance over a specific period (e.g., 1 Year)
@app.get("/rankings")
async def get_rankings(period: str = "1y"):
    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}
    
    # SQL LOGIC:
    # Uses FIRST_VALUE() twice (once ASC, once DESC) to find the Start and End price
    # of the period in a single pass, without joining the table to itself.
    # This is O(n) complexity (fast).
    if period.lower() == "max":
        query = """
            WITH UniquePrices AS (
                SELECT symbol, trade_date, MAX(close) as close 
                FROM prices 
                GROUP BY symbol, trade_date
            ),
            Ranked AS (
                SELECT symbol, close, trade_date,
                        FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date ASC) as start_price,
                        FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date DESC) as end_price,
                        ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
                FROM UniquePrices
            )
            SELECT symbol, ((end_price - start_price) / NULLIF(start_price, 0)) * 100 as value
            FROM Ranked WHERE rn = 1 ORDER BY value DESC
        """
    else:
        days = intervals.get(period.lower(), 365)
        query = f"""
            WITH LatestDate AS (SELECT MAX(trade_date) as max_d FROM prices),
            UniquePrices AS (
                 SELECT symbol, trade_date, MAX(close) as close
                 FROM prices
                 GROUP BY symbol, trade_date
            ),
            Filtered AS (
                SELECT symbol, close, trade_date
                FROM UniquePrices, LatestDate
                WHERE trade_date >= LatestDate.max_d - INTERVAL {days} DAY
            ),
            Bounds AS (
                SELECT symbol,
                        FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
                        FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date DESC) as last_val,
                        ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
                FROM Filtered
            )
            SELECT symbol, ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
            FROM Bounds WHERE rn = 1 ORDER BY value DESC
        """
    try:
        df = local_db.execute(query).df()
        return {
            "selected": {
                "top": df.head(3).to_dict(orient='records'),
                "bottom": df.tail(3).sort_values(by='value').to_dict(orient='records')
            }
        }
    except Exception as e:
        print(f"Ranking Error: {e}")
        return {"selected": {"top": [], "bottom": []}}

# 5. WEBSOCKET ENDPOINT
# Handles real-time client connections.
@app.websocket("/ws/market")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # IMMEDIATE HYDRATION:
        # As soon as a user connects, we dump the last known market state (LATEST_MARKET_DATA)
        # to them. This prevents the UI from showing "Waiting..." for up to 60 seconds.
        if LATEST_MARKET_DATA:
            for payload in LATEST_MARKET_DATA.values():
                 await websocket.send_text(json.dumps(payload))
        
        # Keep connection open until client disconnects
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    port = int(getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)