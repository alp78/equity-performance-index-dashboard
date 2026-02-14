import os
import logging
import pandas as pd
import duckdb
import asyncio
import json
import random
import requests
import uvicorn
import yfinance as yf
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from urllib.parse import unquote
from contextlib import asynccontextmanager

PROJECT_ID = ""

# Initialize global DuckDB
local_db = duckdb.connect(database=':memory:', read_only=False)

# --- WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

# --- CACHE REFRESH LOGIC ---
async def refresh_duckdb_cache():
    """Logic to pull from BQ and populate DuckDB. Preserves your exact casting."""
    print("Syncing DuckDB cache with BigQuery...")
    bq_client = bigquery.Client(project=PROJECT_ID)
    load_query = f"""
        SELECT
            symbol,
            name,
            CAST(trade_date AS DATE) as trade_date,
            CAST(close_price AS FLOAT64) as close,
            CAST(high_price AS FLOAT64) as high,
            CAST(low_price AS FLOAT64) as low,
            CAST(volume AS INT) as volume
        FROM `{PROJECT_ID}.stock_exchange.stock_prices`
    """
    raw_df = bq_client.query(load_query).to_dataframe()
    raw_df['trade_date'] = pd.to_datetime(raw_df['trade_date'])

    local_db.execute("DROP TABLE IF EXISTS prices")
    local_db.register('temp_df', raw_df)
    local_db.execute("CREATE TABLE prices AS SELECT * FROM temp_df")
    local_db.execute("CREATE INDEX idx_symbol ON prices (symbol)")
    print("Cache Synced.")

# --- EXTERNAL API DATA FETCHERS ---
async def fetch_macro_references():
    refs = {}
    try:
        r = requests.get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1", timeout=5)
        if r.status_code == 200:
            data = r.json()[0]
            open_price = float(data[1])
            last_price = float(data[4])
            refs["BINANCE:BTCUSDT"] = {
                "price": last_price,
                "prev": open_price 
            }
    except Exception:
        pass

    mapping = {"FXCM:XAU/USD": "GC=F", "FXCM:EUR/USD": "EURUSD=X"}
    for label, ticker_symbol in mapping.items():
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                refs[label] = {
                    "price": float(hist['Close'].iloc[-1]),
                    "prev": float(hist['Close'].iloc[-2])
                }
        except Exception: pass
    return refs

async def market_data_feeder():
    while True:
        try:
            res = local_db.execute("SELECT COUNT(*) FROM prices").fetchone()
            if res and res[0] > 0: break
        except: pass
        await asyncio.sleep(1)

    market_state = await fetch_macro_references()
    try:
        ref_query = """
            WITH Daily AS (
                SELECT symbol, close,
                       ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
                FROM prices
            )
            SELECT symbol,
                   MAX(CASE WHEN rn = 1 THEN close END) as latest,
                   MAX(CASE WHEN rn = 2 THEN close END) as previous
            FROM Daily
            WHERE rn <= 2
            GROUP BY symbol
        """
        rows = local_db.execute(ref_query).fetchall()
        for symbol, latest, previous in rows:
            ref_price = previous if previous else (latest * 1.005)
            market_state[symbol] = {
                "price": float(latest),
                "prev": float(ref_price)
            }
        print(f"WebSocket Feeder: Initialized {len(market_state)} symbols.")

    except Exception as e:
        print(f"Feeder initialization error: {e}")

    while True:
        if manager.active_connections:
            for symbol, state in market_state.items():
                change_factor = random.uniform(-0.0001, 0.0001)
                state["price"] *= (1 + change_factor)
                abs_diff = state["price"] - state["prev"]
                total_pct = (abs_diff / state["prev"]) * 100
                payload = {
                    "symbol": symbol,
                    "price": round(state["price"], 2),
                    "diff": round(abs_diff, 2),
                    "pct": round(total_pct, 2)
                }
                await manager.broadcast(json.dumps(payload))
        await asyncio.sleep(1)

# --- LIFESPAN (CACHING + FEEDER) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Warming up GCP In-Memory Cache...")
    feeder_task = None
    try:
        await refresh_duckdb_cache()
        feeder_task = asyncio.create_task(market_data_feeder())
    except Exception as e:
        print(f"Failed to warm cache: {e}")

    yield
    if feeder_task: feeder_task.cancel()
    local_db.close()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ENDPOINTS ---
@app.post("/api/admin/refresh")
async def webhook_refresh():
    """Immediately acknowledges and triggers the cache refresh independently."""
    asyncio.create_task(refresh_duckdb_cache())
    return {"status": "accepted", "message": "Refresh task offloaded"}

@app.get("/summary")
async def get_summary():
    query = """
        WITH Latest AS (
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
            FROM prices
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

@app.get("/data/{symbol:path}")
async def get_data(symbol: str, period: str = "1y"):
    symbol = unquote(symbol)
    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}
    days_to_subtract = intervals.get(period.lower(), 365)
    try:
        latest_res = local_db.execute("SELECT MAX(trade_date) FROM prices WHERE symbol = ?", [symbol]).fetchone()
        if not latest_res or not latest_res[0]: return []
        anchor_date = latest_res[0]

        if period.lower() == "max":
            query = """
                SELECT strftime(trade_date, '%Y-%m-%d') as time, close, volume,
                AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM prices WHERE symbol = ? ORDER BY trade_date ASC
            """
            df = local_db.execute(query, [symbol]).df()
        else:
            query = f"""
                SELECT strftime(trade_date, '%Y-%m-%d') as time, close, volume,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                    AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                FROM prices
                WHERE symbol = ?
                QUALIFY trade_date >= CAST(? AS DATE) - INTERVAL {days_to_subtract} DAY
                ORDER BY trade_date ASC
            """
            df = local_db.execute(query, [symbol, anchor_date]).df()
        return df.replace({float('nan'): 0}).to_dict(orient='records')
    except Exception as e:
        logging.error(f"DuckDB Error: {e}")
        return []

@app.get("/rankings")
async def get_rankings(period: str = "1y"):
    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}
    if period.lower() == "max":
        query = """
            WITH Ranked AS (
                SELECT symbol, close, trade_date,
                       FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date ASC) as start_price,
                       FIRST_VALUE(close) OVER(PARTITION BY symbol ORDER BY trade_date DESC) as end_price,
                       ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
                FROM prices
            )
            SELECT symbol, ((end_price - start_price) / NULLIF(start_price, 0)) * 100 as value
            FROM Ranked WHERE rn = 1 ORDER BY value DESC
        """
    else:
        days = intervals.get(period.lower(), 365)
        query = f"""
            WITH LatestDate AS (SELECT MAX(trade_date) as max_d FROM prices),
            Filtered AS (
                SELECT symbol, close, trade_date
                FROM prices, LatestDate
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
    df = local_db.execute(query).df()
    return {
        "selected": {
            "top": df.head(3).to_dict(orient='records'),
            "bottom": df.tail(3).sort_values(by='value').to_dict(orient='records')
        }
    }

@app.websocket("/ws/market")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)