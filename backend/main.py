"""
Exchange Dashboard — Backend API
=================================
Architecture:
  BigQuery (warehouse) → DuckDB (per-index lazy cache) → REST API / WebSocket → Frontend

Cache strategy:
  - Per-index lazy loading: each index loads into its own DuckDB table on first access
  - Non-blocking refresh: webhook triggers background reload; stale data served during refresh
  - API response cache with 5s TTL for hot-path queries
  - Scales to 50+ indices: only the accessed index is loaded into memory

Designed for scale:
  - Adding a new index = one line in MARKET_INDICES
  - Memory proportional to active indices, not total indices
  - Cache hydration never blocks API responses
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
import threading
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
# To add a new index, add one line here. Everything else adapts.
MARKET_INDICES = {
    "stoxx50":   {"table_id": f"{PROJECT_ID}.stock_exchange.stoxx50_prices" if PROJECT_ID else None},
    "sp500":     {"table_id": f"{PROJECT_ID}.stock_exchange.sp500_prices" if PROJECT_ID else None},
    "ftse100":   {"table_id": f"{PROJECT_ID}.stock_exchange.ftse100_prices" if PROJECT_ID else None},
    "nikkei225": {"table_id": f"{PROJECT_ID}.stock_exchange.nikkei225_prices" if PROJECT_ID else None},
    "csi300":    {"table_id": f"{PROJECT_ID}.stock_exchange.csi300_prices" if PROJECT_ID else None},
    "nifty50":   {"table_id": f"{PROJECT_ID}.stock_exchange.nifty50_prices" if PROJECT_ID else None},
}

DISPLAY_SYMBOL_MAP = {
    "GC=F":      "FXCM:XAU/USD",
    "EURUSD=X":  "FXCM:EUR/USD",
}

# ============================================================================
# CACHE LAYER — Per-index lazy loading with non-blocking refresh
# ============================================================================

local_db = duckdb.connect(database=':memory:', read_only=False)
db_lock = threading.Lock()  # Protects DuckDB during table rebuilds

# Track which indices are loaded and their load status
INDEX_LOAD_STATUS = {}  # key → {"loaded": bool, "loading": bool, "row_count": int}
LATEST_MARKET_DATA = {}

# API response cache
API_CACHE = {}
CACHE_TTL = 5

def get_cached_response(cache_key):
    if cache_key in API_CACHE:
        data, timestamp = API_CACHE[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return data
    return None

def set_cached_response(cache_key, data):
    API_CACHE[cache_key] = (data, time.time())

def invalidate_index_cache(index_key):
    """Remove all cached API responses for a specific index."""
    keys_to_remove = [k for k in API_CACHE if index_key in k]
    for k in keys_to_remove:
        del API_CACHE[k]


# ============================================================================
# WEBSOCKET CONNECTION MANAGER
# ============================================================================

class ConnectionManager:
    def __init__(self):
        self.active_connections = []

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
            except:
                pass

manager = ConnectionManager()


# ============================================================================
# DATA INGESTION — Per-index lazy loading from BigQuery → DuckDB
# ============================================================================

# Shared BigQuery client (reuse = faster connection pooling)
_bq_client = None

def get_bq_client():
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client


def _load_index_from_bq(index_key):
    """
    Load a single index from BigQuery into DuckDB. Thread-safe.
    Optimized: simple SELECT from BQ (no window function), dedup done in DuckDB.
    """
    config = MARKET_INDICES.get(index_key)
    if not config or not config.get("table_id") or not PROJECT_ID:
        return 0

    table_name = f"prices_{index_key}"
    latest_table = f"latest_{index_key}"
    t0 = time.time()

    try:
        bq_client = get_bq_client()

        # Simple flat SELECT — no window functions in BigQuery = much faster
        query = f"""
            SELECT symbol, name,
                CAST(trade_date AS DATE) as trade_date,
                CAST(open_price AS FLOAT64) as open,
                CAST(close_price AS FLOAT64) as close,
                CAST(high_price AS FLOAT64) as high,
                CAST(low_price AS FLOAT64) as low,
                CAST(volume AS INT64) as volume
            FROM `{config['table_id']}`
        """
        df = bq_client.query(query).to_dataframe()
        t_bq = time.time()
        print(f"  [{index_key}] BQ fetch: {t_bq - t0:.1f}s ({len(df)} raw rows)")

        if df.empty:
            return 0

        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df['market_index'] = index_key

        with db_lock:
            local_db.execute(f"DROP TABLE IF EXISTS {table_name}")
            local_db.register(f'temp_{index_key}', df)

            # Dedup in DuckDB (sub-second vs seconds in BigQuery)
            local_db.execute(f"""
                CREATE TABLE {table_name} AS
                SELECT symbol, name, trade_date, open, close, high, low, volume, market_index
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date ORDER BY volume DESC
                    ) as rn FROM temp_{index_key}
                ) WHERE rn = 1
            """)
            local_db.execute(f"CREATE INDEX IF NOT EXISTS idx_{index_key} ON {table_name} (symbol, trade_date)")

            local_db.execute(f"DROP TABLE IF EXISTS {latest_table}")
            local_db.execute(f"""
                CREATE TABLE {latest_table} AS
                SELECT symbol, name, market_index, trade_date, open, close, high, low, volume,
                    LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_price
                FROM {table_name}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
            """)

            _rebuild_unified_view()

        row_count = local_db.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        t_done = time.time()
        print(f"  [{index_key}] DuckDB: {t_done - t_bq:.1f}s. Total: {t_done - t0:.1f}s ({row_count} rows)")
        return row_count

    except Exception as e:
        print(f"  [{index_key}] Load error: {e}")
        return 0


def _rebuild_unified_view():
    """Rebuild the unified 'prices' and 'latest_prices' views from all loaded index tables."""
    loaded_indices = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not loaded_indices:
        return

    # Build UNION ALL of all loaded price tables
    unions = " UNION ALL ".join([f"SELECT * FROM prices_{k}" for k in loaded_indices])
    local_db.execute("DROP VIEW IF EXISTS prices")
    local_db.execute(f"CREATE VIEW prices AS {unions}")

    latest_unions = " UNION ALL ".join([f"SELECT * FROM latest_{k}" for k in loaded_indices])
    local_db.execute("DROP VIEW IF EXISTS latest_prices")
    local_db.execute(f"CREATE VIEW latest_prices AS {latest_unions}")


def ensure_index_loaded(index_key):
    """
    Check if an index is loaded. If not, trigger background loading and return False.
    NEVER blocks the request handler — returns immediately.
    """
    status = INDEX_LOAD_STATUS.get(index_key)

    if status and status.get("loaded"):
        return True

    if status and status.get("loading"):
        return False  # Loading in progress, caller should return loading state

    # Trigger background load
    INDEX_LOAD_STATUS[index_key] = {"loaded": False, "loading": True, "row_count": 0}

    def _bg_load():
        row_count = _load_index_from_bq(index_key)
        INDEX_LOAD_STATUS[index_key] = {
            "loaded": row_count > 0,
            "loading": False,
            "row_count": row_count,
        }

    import concurrent.futures
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    _executor.submit(_bg_load)

    return False


async def refresh_single_index(index_key):
    """Background refresh of a single index. Non-blocking."""
    print(f"Refreshing index: {index_key}")
    invalidate_index_cache(index_key)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _load_index_from_bq(index_key))
    INDEX_LOAD_STATUS[index_key] = {
        "loaded": True, "loading": False,
        "row_count": INDEX_LOAD_STATUS.get(index_key, {}).get("row_count", 0),
    }
    invalidate_index_cache(index_key)


# ============================================================================
# REAL-TIME DATA FEEDS
# ============================================================================

async def fetch_crypto_data():
    try:
        ticker_r = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=2
        )
        if ticker_r.status_code != 200:
            return
        current_price = float(ticker_r.json()['price'])

        kline_r = requests.get(
            "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1", timeout=2
        )
        open_price = float(kline_r.json()[0][1]) if kline_r.status_code == 200 else current_price
        diff = current_price - open_price
        pct = (diff / open_price) * 100 if open_price != 0 else 0

        payload = {
            "symbol": "BINANCE:BTCUSDT",
            "price": round(current_price, 2),
            "diff": round(diff, 2),
            "pct": round(pct, 2),
            "live": True,
        }
        LATEST_MARKET_DATA["BINANCE:BTCUSDT"] = payload
        if manager.active_connections:
            await manager.broadcast(json.dumps(payload))
    except:
        pass


async def fetch_stock_data():
    """
    Fetch live stock prices in small batches to avoid blocking.
    Batch 1 (priority): Global macro + current active leaders
    Batch 2+: Remaining leaders from other indices
    """
    GLOBAL_MACRO = ["GC=F", "EURUSD=X"]

    # All leader symbols grouped by index
    INDEX_LEADERS = {
        "sp500":     ["NVDA", "AAPL", "MSFT", "AMZN"],
        "stoxx50":   ["ASML.AS", "SAP.DE", "MC.PA"],
        "ftse100":   ["SHEL.L", "AZN.L", "HSBA.L"],
        "nikkei225": ["7203.T", "6758.T", "9984.T"],
        "csi300":    ["600519.SS", "000858.SZ", "601318.SS"],
        "nifty50":   ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
    }

    DISPLAY_MAP = {
        "GC=F": "FXCM:XAU/USD", "EURUSD=X": "FXCM:EUR/USD",
        "ASML.AS": "ASML", "SAP.DE": "SAP", "MC.PA": "LVMH",
    }

    # Build batches: global macro first, then index leaders in small groups
    batches = [GLOBAL_MACRO]
    for symbols in INDEX_LEADERS.values():
        batches.append(symbols)

    for batch in batches:
        try:
            data = yf.download(
                batch, period="2d", interval="1d",
                progress=False, group_by='ticker', threads=True
            )
            if data is None or data.empty:
                continue

            is_multi = isinstance(data.columns, pd.MultiIndex)

            for symbol in batch:
                try:
                    df = data[symbol] if is_multi and len(batch) > 1 else data
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
                    display_symbol = DISPLAY_MAP.get(symbol, symbol)
                    is_fx = symbol == "EURUSD=X"

                    payload = {
                        "symbol": display_symbol,
                        "price": round(current, 6 if is_fx else 2),
                        "diff":  round(diff,    6 if is_fx else 2),
                        "pct":   round(pct,     4 if is_fx else 2),
                    }

                    LATEST_MARKET_DATA[display_symbol] = payload
                    if manager.active_connections:
                        await manager.broadcast(json.dumps(payload))

                except Exception:
                    continue

        except Exception as e:
            print(f"Stock Batch Error: {e}")
            continue

        # Yield control between batches so other async tasks can run
        await asyncio.sleep(0.5)


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

async def market_data_feeder():
    """Polls live prices. Started after priority indices are loaded."""
    print("Real-time Feeder Active")
    crypto_counter = 0
    while True:
        await fetch_crypto_data()
        crypto_counter += 1
        if crypto_counter >= 6:  # Stocks every ~60s (6 × 10s)
            await fetch_stock_data()
            crypto_counter = 0
        await asyncio.sleep(10)


async def preload_all_indices():
    """
    Preload indices at startup.
    Phase 1: stoxx50 + sp500 sequentially (Cloud Run has limited resources).
    Phase 2: Start feeder immediately.
    Phase 3: Remaining indices load one-by-one in background.
    """
    # Phase 1: Priority indices — sequential to avoid BigQuery concurrency issues
    for idx in ["stoxx50", "sp500"]:
        try:
            await refresh_single_index(idx)
        except Exception as e:
            print(f"Phase 1 error loading {idx}: {e}")
    print("Phase 1 preload complete (stoxx50, sp500)")

    # Phase 2: Start feeder — stoxx50/sp500 leaders available immediately
    asyncio.create_task(market_data_feeder())

    # Phase 3: Remaining indices — sequential to avoid memory pressure on Cloud Run
    remaining = [k for k in MARKET_INDICES if k not in ("stoxx50", "sp500")]
    for idx in remaining:
        try:
            await refresh_single_index(idx)
            print(f"  Background preload: {idx} ready")
        except Exception as e:
            print(f"  Background preload error {idx}: {e}")
        await asyncio.sleep(1)  # Breathing room between BigQuery calls
    print(f"All indices preloaded")


async def background_startup():
    await preload_all_indices()


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(background_startup())
    yield
    local_db.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# WEBSOCKET
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        for payload in list(LATEST_MARKET_DATA.values()):
            await websocket.send_text(json.dumps(payload))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ============================================================================
# REST API
# ============================================================================

@app.get("/health")
async def health():
    loaded = {k: v for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")}
    total_rows = sum(v.get("row_count", 0) for v in loaded.values())
    return {
        "status": "ok" if loaded else "warming_up",
        "indices_loaded": len(loaded),
        "total_rows": total_rows,
        "indices": {k: v for k, v in INDEX_LOAD_STATUS.items()},
    }


@app.post("/api/admin/refresh")
async def webhook_refresh():
    """
    Webhook from Cloud Function. Only refreshes indices that are already loaded.
    For targeted refresh, use /api/admin/refresh/{index_key} instead.
    """
    loaded = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not loaded:
        return {"status": "skipped", "message": "No indices loaded yet"}
    # Refresh loaded indices sequentially to avoid overwhelming BigQuery
    for idx in loaded:
        asyncio.create_task(refresh_single_index(idx))
        await asyncio.sleep(0.5)
    return {"status": "accepted", "message": f"Refreshing {len(loaded)} loaded indices"}


@app.post("/api/admin/refresh/{index_key}")
async def webhook_refresh_index(index_key: str):
    """Refresh a single index (called by per-market cloud function)."""
    if index_key not in MARKET_INDICES:
        return {"status": "error", "message": f"Unknown index: {index_key}"}
    asyncio.create_task(refresh_single_index(index_key))
    return {"status": "accepted", "message": f"Refreshing {index_key}"}


@app.get("/summary")
async def get_summary(index: str = "sp500"):
    if index not in MARKET_INDICES:
        index = "sp500"

    # Non-blocking: trigger lazy load if needed, return empty if not ready
    if not ensure_index_loaded(index):
        return []  # Frontend will retry — sidebar shows loading spinner

    cache_key = f"summary_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        with db_lock:
            res = local_db.execute(f"""
                SELECT symbol, name,
                    CAST(close AS FLOAT) as last_price,
                    CAST(high AS FLOAT) as high,
                    CAST(low AS FLOAT) as low,
                    CAST(volume AS BIGINT) as volume,
                    trade_date,
                    CAST(((close - prev_price) / NULLIF(prev_price, 0)) * 100 AS FLOAT) as daily_change_pct
                FROM latest_{index}
                WHERE market_index = '{index}'
                ORDER BY symbol
            """).df().fillna(0).to_dict(orient='records')
        set_cached_response(cache_key, res)
        return res
    except Exception as e:
        print(f"Summary Error ({index}): {e}")
        return []


@app.get("/data/{symbol:path}")
async def get_data(symbol: str, period: str = "1y"):
    symbol = unquote(symbol)
    cache_key = f"data_{symbol}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}

    # Find which index table has this symbol
    table = _find_symbol_table(symbol)
    if not table:
        # Cache the empty result to prevent request floods
        set_cached_response(cache_key, [])
        return []

    try:
        with db_lock:
            if period.lower() == "max":
                df = local_db.execute(f"""
                    SELECT strftime(trade_date, '%Y-%m-%d') as time, open, close, high, low, volume,
                        AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                        AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                    FROM {table} WHERE symbol = ? ORDER BY trade_date ASC
                """, [symbol]).df()
            else:
                days = intervals.get(period.lower(), 365)
                df = local_db.execute(f"""
                    SELECT strftime(trade_date, '%Y-%m-%d') as time, open, close, high, low, volume,
                        AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) as ma30,
                        AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 89 PRECEDING AND CURRENT ROW) as ma90
                    FROM {table} WHERE symbol = ?
                        AND trade_date >= (SELECT MAX(trade_date) FROM {table} WHERE symbol = ?) - INTERVAL {days} DAY
                    ORDER BY trade_date ASC
                """, [symbol, symbol]).df()

        result = df.fillna(0).to_dict(orient='records')
        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        print(f"Data Error: {e}")
        return []


# Symbol → index reverse lookup cache (built lazily as indices load)
SYMBOL_INDEX_MAP = {}  # "7203.T" → "nikkei225"

# Known symbol suffixes → index mapping for lazy-load triggering
SUFFIX_TO_INDEX = {
    '.T': 'nikkei225',
    '.SS': 'csi300', '.SZ': 'csi300',
    '.NS': 'nifty50', '.BO': 'nifty50',
    '.L': 'ftse100',
    '.DE': 'stoxx50', '.PA': 'stoxx50', '.AS': 'stoxx50', '.BR': 'stoxx50',
    '.MI': 'stoxx50', '.MC': 'stoxx50', '.HE': 'stoxx50',
}


def _guess_index_for_symbol(symbol):
    """Guess which index a symbol belongs to based on its exchange suffix."""
    upper = symbol.upper()
    for suffix, index_key in SUFFIX_TO_INDEX.items():
        if upper.endswith(suffix.upper()):
            return index_key
    # No suffix = likely US stock (S&P 500)
    if '.' not in symbol:
        return 'sp500'
    return None


def _find_symbol_table(symbol):
    """Find which per-index table contains a given symbol. Triggers lazy load if needed."""
    # Check reverse lookup cache first
    if symbol in SYMBOL_INDEX_MAP:
        idx = SYMBOL_INDEX_MAP[symbol]
        status = INDEX_LOAD_STATUS.get(idx)
        if status and status.get("loaded"):
            return f"prices_{idx}"

    # Search loaded indices
    for index_key, status in INDEX_LOAD_STATUS.items():
        if not status.get("loaded"):
            continue
        try:
            with db_lock:
                count = local_db.execute(
                    f"SELECT COUNT(*) FROM prices_{index_key} WHERE symbol = ?", [symbol]
                ).fetchone()[0]
            if count > 0:
                SYMBOL_INDEX_MAP[symbol] = index_key
                return f"prices_{index_key}"
        except:
            continue

    # Not found in any loaded index — try to guess and lazy-load
    guessed_index = _guess_index_for_symbol(symbol)
    if guessed_index and guessed_index in MARKET_INDICES:
        status = INDEX_LOAD_STATUS.get(guessed_index)
        if not status or not status.get("loaded"):
            print(f"  Lazy-loading {guessed_index} for symbol {symbol}")
            if ensure_index_loaded(guessed_index):
                # Check again after loading
                try:
                    with db_lock:
                        count = local_db.execute(
                            f"SELECT COUNT(*) FROM prices_{guessed_index} WHERE symbol = ?", [symbol]
                        ).fetchone()[0]
                    if count > 0:
                        SYMBOL_INDEX_MAP[symbol] = guessed_index
                        return f"prices_{guessed_index}"
                except:
                    pass

    return None


@app.get("/rankings")
async def get_rankings(period: str = "1y", index: str = "sp500"):
    if index not in MARKET_INDICES:
        index = "sp500"

    if not ensure_index_loaded(index):
        return {"selected": {"top": [], "bottom": []}}
    table = f"prices_{index}"

    cache_key = f"rankings_{period}_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    intervals = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}

    try:
        with db_lock:
            if period.lower() == "max":
                df = local_db.execute(f"""
                    WITH Ranked AS (
                        SELECT symbol,
                            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
                                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as start_price,
                            LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
                                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as end_price,
                            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
                        FROM {table} WHERE market_index = '{index}'
                    )
                    SELECT symbol, ((end_price - start_price) / NULLIF(start_price, 0)) * 100 as value
                    FROM Ranked WHERE rn = 1 ORDER BY value DESC
                """).df()
            else:
                days = intervals.get(period.lower(), 365)
                df = local_db.execute(f"""
                    WITH Filtered AS (
                        SELECT symbol, close, trade_date FROM {table}
                        WHERE market_index = '{index}'
                          AND trade_date >= (SELECT MAX(trade_date) FROM {table} WHERE market_index = '{index}') - INTERVAL {days} DAY
                    ),
                    Ranked AS (
                        SELECT symbol,
                            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
                            LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
                                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
                            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
                        FROM Filtered
                    )
                    SELECT symbol, ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
                    FROM Ranked WHERE rn = 1 ORDER BY value DESC
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


@app.get("/rankings/custom")
async def get_custom_rankings(start: str, end: str, index: str = "sp500"):
    if index not in MARKET_INDICES:
        index = "sp500"

    if not ensure_index_loaded(index):
        return {"selected": {"top": [], "bottom": []}}
    table = f"prices_{index}"

    cache_key = f"rankings_custom_{start}_{end}_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        with db_lock:
            df = local_db.execute(f"""
                WITH Filtered AS (
                    SELECT symbol, close, trade_date FROM {table}
                    WHERE market_index = '{index}' AND trade_date >= '{start}' AND trade_date <= '{end}'
                ),
                Ranked AS (
                    SELECT symbol,
                        FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) as first_val,
                        LAST_VALUE(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC
                            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) as last_val,
                        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
                    FROM Filtered
                )
                SELECT symbol, ((last_val - first_val) / NULLIF(first_val, 0)) * 100 as value
                FROM Ranked WHERE rn = 1 ORDER BY value DESC
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


@app.get("/metadata/{symbol:path}")
async def metadata(symbol: str):
    symbol = unquote(symbol)
    table = _find_symbol_table(symbol)
    if not table:
        return {"symbol": symbol, "name": symbol}
    try:
        with db_lock:
            res = local_db.execute(f"SELECT name FROM {table} WHERE symbol = ? LIMIT 1", [symbol]).fetchone()
        return {"symbol": symbol, "name": res[0] if res else symbol}
    except:
        return {"symbol": symbol, "name": symbol}


if __name__ == "__main__":
    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)