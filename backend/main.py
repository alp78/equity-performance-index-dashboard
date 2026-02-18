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
import time
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
# BigQuery has latency (2-3s) and costs per query.
# DuckDB lives in RAM, offers sub-millisecond response times, and supports
# complex Window Functions (for Moving Averages) natively.
local_db = duckdb.connect(database=':memory:', read_only=False)

# --- REAL-TIME STATE CACHE ---
# Stores the last known price for every symbol (BTC, NVDA, etc.).
# Used to "Hydrate" new WebSocket clients immediately so they don't see "Waiting...".
LATEST_MARKET_DATA = {}

# --- API RESPONSE CACHE ---
# NEW: In-memory cache for API endpoints with TTL (Time To Live)
# Structure: { endpoint_key: (data, timestamp) }
# This prevents redundant DuckDB queries when multiple components request the same data
API_CACHE = {}
CACHE_TTL = 1800 # 30 min

def get_cached_response(cache_key):
    """Retrieve cached response if still valid, otherwise return None"""
    if cache_key in API_CACHE:
        data, timestamp = API_CACHE[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return data
    return None

def set_cached_response(cache_key, data):
    """Store response in cache with current timestamp"""
    API_CACHE[cache_key] = (data, time.time())

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
    start_total = time.time()
    print("Syncing DuckDB cache (Phase 1: Latest Snapshot)...")
    bq_client = bigquery.Client(project=PROJECT_ID)
    
    try:
        # Step 1: Fetch ONLY the most recent day for all tickers.
        # This is a tiny query that finishes in ~1 second.
        # DEFENSIVE: Use ROW_NUMBER to deduplicate any potential duplicates in BigQuery
        start_phase1 = time.time()
        latest_query = f"""
            WITH RankedData AS (
                SELECT 
                    symbol, name, trade_date, 
                    close_price as close, high_price as high, 
                    low_price as low, volume,
                    ROW_NUMBER() OVER (PARTITION BY symbol, trade_date ORDER BY volume DESC) as rn
                FROM `{PROJECT_ID}.stock_exchange.stock_prices`
                WHERE trade_date = (SELECT MAX(trade_date) FROM `{PROJECT_ID}.stock_exchange.stock_prices`)
            )
            SELECT symbol, name, trade_date, close, high, low, volume
            FROM RankedData
            WHERE rn = 1
        """
        latest_df = bq_client.query(latest_query).to_dataframe()
        latest_df['trade_date'] = pd.to_datetime(latest_df['trade_date'])
        print(f"Phase 1 BigQuery fetch took {time.time() - start_phase1:.2f}s")

        # Load this into a temporary table so the Sidebar can work immediately
        start_load = time.time()
        local_db.execute("CREATE OR REPLACE TABLE prices AS SELECT * FROM latest_df")
        
        # OPTIMIZATION: Create composite index for faster lookups
        local_db.execute("CREATE INDEX IF NOT EXISTS idx_symbol_date ON prices (symbol, trade_date)")
        print(f"⏱Phase 1 DuckDB load + index took {time.time() - start_load:.2f}s")
        
        print(f"Sidebar Ready: Loaded {len(latest_df)} tickers in {time.time() - start_total:.2f}s")

        # Step 2: Now fetch the FULL history in the background (Phase 2)
        print("Syncing DuckDB cache (Phase 2: Full History)...")
        start_phase2 = time.time()
        
        # DEFENSIVE: Deduplicate in BigQuery before loading into DuckDB
        # This ensures clean data for all subsequent queries
        history_query = f"""
            WITH RankedData AS (
                SELECT 
                    symbol, name, CAST(trade_date AS DATE) as trade_date,
                    CAST(close_price AS FLOAT64) as close, 
                    CAST(high_price AS FLOAT64) as high,
                    CAST(low_price AS FLOAT64) as low, 
                    CAST(volume AS INT) as volume,
                    ROW_NUMBER() OVER (PARTITION BY symbol, trade_date ORDER BY volume DESC) as rn
                FROM `{PROJECT_ID}.stock_exchange.stock_prices`
            )
            SELECT symbol, name, trade_date, close, high, low, volume
            FROM RankedData
            WHERE rn = 1
        """
        full_df = bq_client.query(history_query).to_dataframe()
        full_df['trade_date'] = pd.to_datetime(full_df['trade_date'])
        print(f"⏱Phase 2 BigQuery fetch took {time.time() - start_phase2:.2f}s")

        # Atomically swap the tiny table for the full table
        start_swap = time.time()
        local_db.register('temp_full', full_df)
        local_db.execute("CREATE OR REPLACE TABLE prices AS SELECT * FROM temp_full")
        
        # OPTIMIZATION: Create composite index for faster lookups
        local_db.execute("CREATE INDEX IF NOT EXISTS idx_symbol_date ON prices (symbol, trade_date)")
        print(f"⏱Phase 2 DuckDB swap + index took {time.time() - start_swap:.2f}s")
        
        # Clear API cache when data refreshes
        API_CACHE.clear()
        
        print(f"Full Cache Synced: Loaded {len(full_df)} rows in {time.time() - start_total:.2f}s total")
    except Exception as e:
        print(f"CRITICAL: Cache Sync Failed after {time.time() - start_total:.2f}s: {e}")

# --- REAL-TIME DATA HELPERS ---
# We split these out so we can call them immediately on startup (Bootstrap)
# and then repeatedly in the loop.

async def fetch_crypto_data():
    """Fetches BTC data based on Daily Open (00:00 UTC) to match aggregators."""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=2)
        if r.status_code == 200:
            data = r.json()
            
            # 1. Get current price
            current_price = float(data['lastPrice'])
            
            # 2. Get the 'openPrice' from the 24h ticker.
            # Binance's 24h ticker openPrice represents the start of the 24h window.
            day_open = float(data['openPrice']) 
            
            # 3. Calculate metrics based on that open price for parity
            diff = current_price - day_open
            pct = (diff / day_open) * 100 if day_open != 0 else 0
            
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
    """Fetches Stock data (Yahoo) - Fixed for pre-market and reference errors."""
    try:
        # Initialize variable to prevent "referenced before assignment" errors
        is_nyse_open = False 
        
        try:
            nyse = mcal.get_calendar('NYSE')
            now_utc = datetime.now(pytz.utc)
            schedule = nyse.schedule(start_date=now_utc.date(), end_date=now_utc.date())
            if not schedule.empty:
                is_nyse_open = nyse.open_at_time(schedule, now_utc)
        except Exception as cal_err:
            print(f"Calendar check failed: {cal_err}")

        targets = ["GC=F", "EURUSD=X", "NVDA", "AAPL", "MSFT"]
        # Use prepost=True to fetch indicative prices during closed hours
        data = yf.download(targets, period="5d", interval="1d", progress=False, group_by='ticker', threads=True, prepost=True)
        
        if data.empty:
            return

        is_multi = isinstance(data.columns, pd.MultiIndex)
        
        for symbol in targets:
            try:
                display_symbol = symbol
                if symbol == "GC=F": display_symbol = "FXCM:XAU/USD"
                if symbol == "EURUSD=X": display_symbol = "FXCM:EUR/USD"

                df = data[symbol] if is_multi else data
                
                # If the current row is empty (market closed), drop it and use the last valid day
                if pd.isna(df['Close'].iloc[-1]):
                    df = df.dropna(subset=['Close'])
                
                if df.empty: continue
                
                latest = df.iloc[-1]
                # prev_close: compare vs yesterday's close
                prev_close = float(df['Close'].iloc[-2]) if len(df) >= 2 else float(latest['Open'])
                current_price = float(latest['Close'])

                if pd.isna(current_price) or current_price == 0: continue

                diff = current_price - prev_close
                pct = (diff / prev_close) * 100 if prev_close != 0 else 0
                
                is_equity = symbol in ["NVDA", "AAPL", "MSFT"]
                
                payload = {
                    "symbol": display_symbol,
                    "price": current_price, 
                    "diff": diff,
                    "pct": pct,
                    # Stocks follow the calendar; Forex/Gold are 24/5
                    "live": True if not is_equity or is_nyse_open else False
                }
                
                LATEST_MARKET_DATA[display_symbol] = payload
                if manager.active_connections:
                    await manager.broadcast(json.dumps(payload))
            except Exception as e: 
                print(f"Error processing {symbol}: {e}")
                continue
    except Exception as e:
        print(f"Stock Fetch Global Error: {e}")

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
    print("Server Booting...")
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

# Request logging middleware for performance monitoring
@app.middleware("http")
async def log_requests(request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    
    # Log slow requests
    if duration > 1.0:
        print(f"SLOW REQUEST: {request.method} {request.url.path} took {duration:.2f}s")
    
    return response

# --- ENDPOINTS ---

# --- HEALTH CHECK ENDPOINT ---
# Allows frontend to check if backend is ready before making data requests
@app.get("/health")
async def health_check():
    """Health check endpoint - returns database readiness status"""
    try:
        result = local_db.execute("SELECT COUNT(*) FROM prices").fetchone()
        if result and result[0] > 0:
            return {
                "status": "healthy",
                "cache_rows": result[0],
                "cache_keys": len(API_CACHE)
            }
        else:
            return {"status": "warming_up", "cache_rows": 0}, 503
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}, 503

# --- METADATA ENDPOINT ---
# Provides near-instant name lookup for the UI header.
@app.get("/metadata/{symbol:path}")
async def get_asset_metadata(symbol: str):
    symbol = unquote(symbol)
    cache_key = f"metadata_{symbol}"
   
    # Check cache
    cached = get_cached_response(cache_key)
    if cached:
        return cached
   
    try:
        # OPTIMIZATION: Use indexed lookup
        res = local_db.execute(
            "SELECT name FROM prices WHERE symbol = ? LIMIT 1",
            [symbol]
        ).fetchone()
        result = {
            "symbol": symbol,
            "name": res[0] if res else symbol
        }
        set_cached_response(cache_key, result)
        return result
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
    cache_key = "summary"
   
    # Check cache
    cached = get_cached_response(cache_key)
    if cached:
        return cached
   
    # OPTIMIZED + DEFENSIVE SQL:
    # Data is already deduplicated at load time, so queries can be simple and fast
    query = """
        SELECT
            symbol,
            name,
            CAST(close AS FLOAT) as last_price,
            CAST(high AS FLOAT) as high,
            CAST(low AS FLOAT) as low,
            CAST(volume AS BIGINT) as volume,
            trade_date,
            CAST(((close - prev_price) / NULLIF(prev_price, 0)) * 100 AS FLOAT) as daily_change_pct
        FROM (
            SELECT
                symbol,
                name,
                close,
                high,
                low,
                volume,
                trade_date,
                LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_price,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
            FROM prices
        ) t
        WHERE rn = 1
    """
    try:
        df = local_db.execute(query).df()
        result = df.fillna(0).to_dict(orient='records')
        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        print(f"Summary SQL Error: {e}")
        return []

# 3. CHART DATA
# Returns historical candles + Moving Averages.
# Supports ?period=1y filtering.
@app.get("/data/{symbol:path}")
async def get_data(symbol: str, period: str = "1y"):
    symbol = unquote(symbol)
    cache_key = f"data_{symbol}_{period}"
   
    # Check cache
    cached = get_cached_response(cache_key)
    if cached:
        return cached
   
    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}
    days_to_subtract = intervals.get(period.lower(), 365)
   
    try:
        # OPTIMIZED + DEFENSIVE:
        # Data is already deduplicated at load time, so we can query directly
       
        if period.lower() == "max":
            query = """
                SELECT
                    strftime(trade_date, '%Y-%m-%d') as time,
                    close,
                    volume,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM prices
                WHERE symbol = ?
                ORDER BY trade_date ASC
            """
            df = local_db.execute(query, [symbol]).df()
        else:
            query = f"""
                SELECT
                    strftime(trade_date, '%Y-%m-%d') as time,
                    close,
                    volume,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM prices
                WHERE symbol = ?
                  AND trade_date >= (SELECT MAX(trade_date) FROM prices WHERE symbol = ?) - INTERVAL {days_to_subtract} DAY
                ORDER BY trade_date ASC
            """
            df = local_db.execute(query, [symbol, symbol]).df()
       
        result = df.replace({float('nan'): 0}).to_dict(orient='records')
        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        logging.error(f"DuckDB Error: {e}")
        return []

# 4. RANKINGS (TOP/BOTTOM PERFORMERS)
# Calculates performance over a specific period (e.g., 1 Year)
@app.get("/rankings")
async def get_rankings(period: str = "1y"):
    cache_key = f"rankings_{period}"
   
    # Check cache
    cached = get_cached_response(cache_key)
    if cached:
        return cached
   
    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}
   
    if period.lower() == "max":
        query = """
            WITH Ranked AS (
                SELECT
                    symbol,
                    FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as start_price,
                    LAST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as end_price,
                    ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
                FROM prices
            )
            SELECT
                symbol,
                ((end_price - start_price) / NULLIF(start_price, 0)) * 100 as value
            FROM Ranked
            WHERE rn = 1
            ORDER BY value DESC
        """
    else:
        days = intervals.get(period.lower(), 365)
        query = f"""
            WITH Filtered AS (
                SELECT
                    symbol,
                    close,
                    trade_date
                FROM prices
                WHERE trade_date >= (SELECT MAX(trade_date) FROM prices) - INTERVAL {days} DAY
            ),
            Ranked AS (
                SELECT
                    symbol,
                    FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as first_val,
                    LAST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
                    ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
                FROM Filtered
            )
            SELECT
                symbol,
                ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
            FROM Ranked
            WHERE rn = 1
            ORDER BY value DESC
        """
    try:
        df = local_db.execute(query).df()
        result = {
            "selected": {
                "top": df.head(3).to_dict(orient='records'),
                "bottom": df.tail(3).sort_values(by='value').to_dict(orient='records')
            }
        }
        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        print(f"Ranking Error: {e}")
        return {"selected": {"top": [], "bottom": []}}

# 5. WEBSOCKET ENDPOINT
# Handles real-time client connections.
@app.websocket("/ws/market")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Copy to avoid mutation issues during broadcast
        current_data = list(LATEST_MARKET_DATA.values())
        if current_data:
            for payload in current_data:
                 await websocket.send_text(json.dumps(payload))
       
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    port = int(getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)