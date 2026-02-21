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
from pathlib import Path
import pandas as pd
import duckdb
import asyncio
import requests
import json
import uvicorn
import yfinance as yf
import time
import threading
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from urllib.parse import unquote
from contextlib import asynccontextmanager


# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ID = getenv("PROJECT_ID")

MARKET_INDICES = {
    "stoxx50":   {"table_id": f"{PROJECT_ID}.stock_exchange.stoxx50_prices" if PROJECT_ID else None},
    "sp500":     {"table_id": f"{PROJECT_ID}.stock_exchange.sp500_prices" if PROJECT_ID else None},
    "ftse100":   {"table_id": f"{PROJECT_ID}.stock_exchange.ftse100_prices" if PROJECT_ID else None},
    "nikkei225": {"table_id": f"{PROJECT_ID}.stock_exchange.nikkei225_prices" if PROJECT_ID else None},
    "csi300":    {"table_id": f"{PROJECT_ID}.stock_exchange.csi300_prices" if PROJECT_ID else None},
    "nifty50":   {"table_id": f"{PROJECT_ID}.stock_exchange.nifty50_prices" if PROJECT_ID else None},
}

DISPLAY_SYMBOL_MAP = {
    "GC=F":     "FXCM:XAU/USD",
    "EURUSD=X": "FXCM:EUR/USD",
}

INDEX_PRICES_TABLE = f"{PROJECT_ID}.stock_exchange.index_prices" if PROJECT_ID else None
INDEX_PRICES_LOADED = False

INDEX_KEY_TO_TICKER = {
    "sp500":     "^GSPC",
    "stoxx50":   "^STOXX50E",
    "ftse100":   "^FTSE",
    "nikkei225": "^N225",
    "csi300":    "000300.SS",
    "nifty50":   "^NSEI",
}
INDEX_TICKER_TO_KEY = {v: k for k, v in INDEX_KEY_TO_TICKER.items()}


# ============================================================================
# SQL LOADER
# ============================================================================

_SQL_DIR = Path(__file__).parent / "sql"
_SQL_CACHE: dict[str, str] = {}


def sql(filename: str) -> str:
    """Load and cache a SQL template from the sql/ directory."""
    if filename not in _SQL_CACHE:
        _SQL_CACHE[filename] = (_SQL_DIR / filename).read_text()
    return _SQL_CACHE[filename]


# ============================================================================
# CACHE LAYER
# ============================================================================

local_db = duckdb.connect(database=':memory:', read_only=False)
db_lock = threading.Lock()

INDEX_LOAD_STATUS: dict = {}
SECTOR_SERIES_STATUS: dict = {}
INDUSTRY_SERIES_STATUS: dict = {}
LATEST_MARKET_DATA: dict = {}
STARTUP_TIME: float = 0.0
STARTUP_DONE_TIME: float = 0.0

API_CACHE: dict = {}
CACHE_TTL = 1800


def get_cached_response(cache_key):
    if cache_key in API_CACHE:
        data, timestamp = API_CACHE[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return data
    return None


def set_cached_response(cache_key, data):
    API_CACHE[cache_key] = (data, time.time())


def invalidate_index_cache(index_key):
    keys_to_remove = [k for k in API_CACHE if index_key in k]
    for k in keys_to_remove:
        del API_CACHE[k]


# ============================================================================
# DATA QUALITY — Clean sector time series helper
# ============================================================================

def build_clean_sector_sql(table, sector_clause, industry_clause="", date_clause=""):
    """
    Produce clean sector % change time series via DuckDB.

    Normalizes each stock individually first (per-stock % change from its own base),
    forward-fills onto a unified timeline, then averages % changes across stocks.

    Prevents spikes from: different trading calendars, IPOs/delistings mid-period,
    and different price scales across stocks.
    """
    return (
        sql("clean_sector_series.sql")
        .replace("{table}", table)
        .replace("{sector_clause}", sector_clause)
        .replace("{industry_clause}", industry_clause)
        .replace("{date_clause}", date_clause)
    )


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

_bq_client = None


def get_bq_client():
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client


def _load_index_from_bq(index_key):
    """Load a single index from BigQuery into DuckDB. Thread-safe."""
    config = MARKET_INDICES.get(index_key)
    if not config or not config.get("table_id") or not PROJECT_ID:
        return 0

    table_name = f"prices_{index_key}"
    latest_table = f"latest_{index_key}"
    t0 = time.time()

    try:
        bq_client = get_bq_client()
        query = sql("bq_load_index.sql").format(table_id=config["table_id"])
        df = bq_client.query(query).to_dataframe()
        t_bq = time.time()
        print(f"  [{index_key}] BQ fetch: {t_bq - t0:.1f}s ({len(df)} raw rows)")

        if df.empty:
            return 0

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["market_index"] = index_key

        with db_lock:
            local_db.execute(f"DROP TABLE IF EXISTS {table_name}")
            local_db.register(f"temp_{index_key}", df)
            local_db.execute(
                sql("duckdb_create_index_table.sql")
                .replace("{table_name}", table_name)
                .replace("{index_key}", index_key)
            )
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{index_key} ON {table_name} (symbol, trade_date)"
            )
            local_db.execute(f"DROP TABLE IF EXISTS {latest_table}")
            local_db.execute(
                sql("duckdb_create_latest_table.sql")
                .replace("{latest_table}", latest_table)
                .replace("{table_name}", table_name)
            )
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

    unions = " UNION ALL ".join([f"SELECT * FROM prices_{k}" for k in loaded_indices])
    local_db.execute("DROP VIEW IF EXISTS prices")
    local_db.execute(f"CREATE VIEW prices AS {unions}")

    latest_unions = " UNION ALL ".join([f"SELECT * FROM latest_{k}" for k in loaded_indices])
    local_db.execute("DROP VIEW IF EXISTS latest_prices")
    local_db.execute(f"CREATE VIEW latest_prices AS {latest_unions}")


def _precompute_sector_series(index_key):
    """Pre-compute normalized % change time series for ALL sectors in an index.
    Stores result in sector_series_{index_key} DuckDB table for instant lookups."""
    table_name = f"prices_{index_key}"
    series_table = f"sector_series_{index_key}"

    SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": True}
    t0 = time.time()

    try:
        precompute_sql = sql("precompute_all_sector_series.sql").replace("{table}", table_name)

        with db_lock:
            local_db.execute(f"DROP TABLE IF EXISTS {series_table}")
            local_db.execute(f"CREATE TABLE {series_table} AS {precompute_sql}")
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{series_table}_sector ON {series_table} (sector)"
            )

        with db_lock:
            row_count = local_db.execute(f"SELECT COUNT(*) FROM {series_table}").fetchone()[0]
            sector_count = local_db.execute(
                f"SELECT COUNT(DISTINCT sector) FROM {series_table}"
            ).fetchone()[0]

        SECTOR_SERIES_STATUS[index_key] = {"ready": True, "computing": False}
        print(f"  [{index_key}] Sector series precomputed: {sector_count} sectors, "
              f"{row_count} rows in {time.time() - t0:.1f}s")

    except Exception as e:
        SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
        print(f"  [{index_key}] Sector series precompute error: {e}")


def _precompute_industry_series(index_key):
    """Pre-compute normalized % change time series for ALL industries in an index.
    Stores result in industry_series_{index_key} DuckDB table for instant lookups."""
    table_name = f"prices_{index_key}"
    series_table = f"industry_series_{index_key}"

    INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": True}
    t0 = time.time()

    try:
        precompute_sql = sql("precompute_all_industry_series.sql").replace("{table}", table_name)

        with db_lock:
            local_db.execute(f"DROP TABLE IF EXISTS {series_table}")
            local_db.execute(f"CREATE TABLE {series_table} AS {precompute_sql}")
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{series_table}_sector ON {series_table} (sector)"
            )
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{series_table}_si ON {series_table} (sector, industry)"
            )

        with db_lock:
            row_count = local_db.execute(f"SELECT COUNT(*) FROM {series_table}").fetchone()[0]
            industry_count = local_db.execute(
                f"SELECT COUNT(DISTINCT industry) FROM {series_table}"
            ).fetchone()[0]

        INDUSTRY_SERIES_STATUS[index_key] = {"ready": True, "computing": False}
        print(f"  [{index_key}] Industry series precomputed: {industry_count} industries, "
              f"{row_count} rows in {time.time() - t0:.1f}s")

    except Exception as e:
        INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
        print(f"  [{index_key}] Industry series precompute error: {e}")


def _prewarm_sector_caches(index_key):
    """Pre-warm API_CACHE for /sector-comparison/table so Heatmap/Rankings load instantly."""
    table = f"prices_{index_key}"
    t0 = time.time()

    try:
        for period_label in ["max", "1y"]:
            cache_key = f"sector_table_{index_key}_{period_label}"
            with db_lock:
                df = _sector_returns_df(table, False, period_label, "", "")
            if df.empty:
                continue

            all_data = {}
            for _, row in df.iterrows():
                sector = row["sector"]
                all_data[sector] = {
                    "return_pct": round(float(row["return_pct"]), 2),
                    "stock_count": int(row["stock_count"]),
                }

            result = []
            for sector, vals in all_data.items():
                result.append({
                    "sector": sector,
                    "avg_return_pct": vals["return_pct"],
                    "indices": {index_key: vals},
                })
            result.sort(key=lambda x: x["avg_return_pct"], reverse=True)
            set_cached_response(cache_key, result)

        print(f"  [{index_key}] Sector caches pre-warmed in {time.time() - t0:.1f}s")
    except Exception as e:
        print(f"  [{index_key}] Sector cache pre-warm error: {e}")


def _load_index_prices_from_bq():
    """Load index_prices table from BigQuery into DuckDB."""
    global INDEX_PRICES_LOADED
    if not INDEX_PRICES_TABLE or not PROJECT_ID:
        return 0

    t0 = time.time()
    try:
        bq_client = get_bq_client()
        query = sql("bq_load_index_prices.sql").format(table_id=INDEX_PRICES_TABLE)
        df = bq_client.query(query).to_dataframe()
        t_bq = time.time()
        print(f"  [index_prices] BQ fetch: {t_bq - t0:.1f}s ({len(df)} raw rows)")

        if df.empty:
            return 0

        df["trade_date"] = pd.to_datetime(df["trade_date"])

        with db_lock:
            local_db.execute("DROP TABLE IF EXISTS index_prices")
            local_db.register("temp_index_prices", df)
            local_db.execute(sql("duckdb_create_index_prices_table.sql"))
            local_db.execute(
                "CREATE INDEX IF NOT EXISTS idx_index_prices ON index_prices (symbol, trade_date)"
            )
            local_db.execute("DROP TABLE IF EXISTS latest_index_prices")
            local_db.execute(sql("duckdb_create_latest_index_prices.sql"))

        row_count = local_db.execute("SELECT COUNT(*) FROM index_prices").fetchone()[0]
        max_dates = local_db.execute("""
            SELECT symbol, MAX(trade_date) as max_date, MIN(trade_date) as min_date, COUNT(*) as cnt
            FROM index_prices GROUP BY symbol ORDER BY symbol
        """).fetchall()
        t_done = time.time()
        print(f"  [index_prices] DuckDB: {t_done - t_bq:.1f}s. Total: {t_done - t0:.1f}s ({row_count} rows)")
        for sym, max_d, min_d, cnt in max_dates:
            print(f"    {sym}: {min_d} → {max_d} ({cnt} rows)")
        INDEX_PRICES_LOADED = True
        return row_count

    except Exception as e:
        print(f"  [index_prices] Load error: {e}")
        return 0


def ensure_index_loaded(index_key):
    """Trigger background load if not loaded. Never blocks. Returns True if ready."""
    status = INDEX_LOAD_STATUS.get(index_key)
    if status and status.get("loaded"):
        return True
    if status and status.get("loading"):
        return False

    INDEX_LOAD_STATUS[index_key] = {"loaded": False, "loading": True, "row_count": 0}

    def _bg_load():
        row_count = _load_index_from_bq(index_key)
        INDEX_LOAD_STATUS[index_key] = {
            "loaded": row_count > 0,
            "loading": False,
            "row_count": row_count,
        }
        if row_count > 0:
            _precompute_sector_series(index_key)
            _precompute_industry_series(index_key)
            _prewarm_sector_caches(index_key)

    import concurrent.futures
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    _executor.submit(_bg_load)
    return False


async def refresh_single_index(index_key):
    """Background refresh of a single index. Non-blocking."""
    print(f"Refreshing index: {index_key}")
    invalidate_index_cache(index_key)
    SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
    INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
    INDEX_LOAD_STATUS[index_key] = {"loaded": False, "loading": True, "row_count": 0}
    loop = asyncio.get_event_loop()
    row_count = await loop.run_in_executor(None, lambda: _load_index_from_bq(index_key))
    INDEX_LOAD_STATUS[index_key] = {
        "loaded": row_count > 0, "loading": False, "row_count": row_count,
    }
    if row_count > 0:
        await loop.run_in_executor(None, lambda: _precompute_sector_series(index_key))
        await loop.run_in_executor(None, lambda: _precompute_industry_series(index_key))
        await loop.run_in_executor(None, lambda: _prewarm_sector_caches(index_key))
    invalidate_index_cache(index_key)


# ============================================================================
# HELPERS
# ============================================================================

INTERVALS = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}


def _sector_returns_df(table, use_custom, period, start, end):
    """Run the right sector_returns SQL variant and return a DataFrame."""
    if use_custom:
        return local_db.execute(
            sql("sector_returns_custom.sql")
            .replace("{table}", table)
            .replace("{start}", start)
            .replace("{end}", end)
        ).df()
    elif period.lower() == "max":
        return local_db.execute(
            sql("sector_returns_max.sql").replace("{table}", table)
        ).df()
    else:
        days = INTERVALS.get(period.lower(), 365)
        return local_db.execute(
            sql("sector_returns_period.sql")
            .replace("{table}", table)
            .replace("{days}", str(days))
        ).df()


def _top_items_df(union, item_col, use_custom, period, start, end):
    """Run the right top_sectors or top_industries SQL variant."""
    base = f"top_{item_col}s"
    if use_custom:
        return local_db.execute(
            sql(f"{base}_custom.sql")
            .replace("{union}", union)
            .replace("{start}", start)
            .replace("{end}", end)
        ).df()
    elif period.lower() == "max":
        return local_db.execute(
            sql(f"{base}_max.sql").replace("{union}", union)
        ).df()
    else:
        days = INTERVALS.get(period.lower(), 365)
        return local_db.execute(
            sql(f"{base}_period.sql")
            .replace("{union}", union)
            .replace("{days}", str(days))
        ).df()


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
        current_price = float(ticker_r.json()["price"])

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
    """Fetch live stock prices in small batches to avoid blocking."""
    GLOBAL_MACRO = ["GC=F", "EURUSD=X"]
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

    batches = [GLOBAL_MACRO] + list(INDEX_LEADERS.values())

    for batch in batches:
        try:
            data = yf.download(
                batch, period="2d", interval="1d",
                progress=False, group_by="ticker", threads=True
            )
            if data is None or data.empty:
                continue

            is_multi = isinstance(data.columns, pd.MultiIndex)

            for symbol in batch:
                try:
                    df = data[symbol] if is_multi and len(batch) > 1 else data
                    if pd.isna(df["Close"].iloc[-1]):
                        df = df.dropna(subset=["Close"])
                    if len(df) < 2:
                        continue

                    current = float(df["Close"].iloc[-1])
                    prev = float(df["Close"].iloc[-2])
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

        await asyncio.sleep(0.5)


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

async def self_keepalive():
    """Self-ping every 4 minutes to prevent Cloud Run from recycling the container."""
    await asyncio.sleep(60)
    port = int(getenv("PORT", "8080"))
    print(f"SELF_KEEPALIVE: Pinging localhost:{port}/health every 4 min")
    while True:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            await reader.read(512)
            writer.close()
        except Exception:
            pass
        await asyncio.sleep(4 * 60)


async def market_data_feeder():
    print("Real-time Feeder Active")
    crypto_counter = 0
    while True:
        await fetch_crypto_data()
        crypto_counter += 1
        if crypto_counter >= 6:
            await fetch_stock_data()
            crypto_counter = 0
        await asyncio.sleep(10)


async def preload_all_indices():
    for idx in ["stoxx50", "sp500"]:
        try:
            await refresh_single_index(idx)
        except Exception as e:
            print(f"Phase 1 error loading {idx}: {e}")
    print("Phase 1 preload complete (stoxx50, sp500)")

    asyncio.create_task(market_data_feeder())
    asyncio.create_task(self_keepalive())

    remaining = [k for k in MARKET_INDICES if k not in ("stoxx50", "sp500")]
    for idx in remaining:
        try:
            await refresh_single_index(idx)
            print(f"  Background preload: {idx} ready")
        except Exception as e:
            print(f"  Background preload error {idx}: {e}")
        await asyncio.sleep(1)

    try:
        loop = asyncio.get_event_loop()
        row_count = await loop.run_in_executor(None, _load_index_prices_from_bq)
        print(f"  Index prices loaded: {row_count} rows")
    except Exception as e:
        print(f"  Index prices load error: {e}")

    print("All indices preloaded")


async def background_startup():
    global STARTUP_TIME, STARTUP_DONE_TIME
    STARTUP_TIME = time.time()
    await preload_all_indices()
    STARTUP_DONE_TIME = time.time()


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
# REST API — Admin
# ============================================================================

@app.get("/health")
async def health():
    loaded = {k: v for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")}
    total_indices = len(MARKET_INDICES)

    # Build step-by-step progress for each index
    steps = []
    step_num = 0
    for idx in MARKET_INDICES:
        step_num += 1
        status = INDEX_LOAD_STATUS.get(idx, {})
        if status.get("loaded"):
            rows = status.get("row_count", 0)
            steps.append(f"{step_num}/{total_indices * 3 + 1}: stocks for {idx} ({rows:,} rows) [ok]")
        elif status.get("loading"):
            steps.append(f"{step_num}/{total_indices * 3 + 1}: stocks for {idx} ... loading")
        else:
            steps.append(f"{step_num}/{total_indices * 3 + 1}: stocks for {idx} - pending")

        step_num += 1
        sec_status = SECTOR_SERIES_STATUS.get(idx, {})
        if sec_status.get("ready"):
            try:
                with db_lock:
                    cnt = local_db.execute(f"SELECT COUNT(*) FROM sector_series_{idx}").fetchone()[0]
                steps.append(f"{step_num}/{total_indices * 3 + 1}: sector series for {idx} ({cnt:,} rows) [ok]")
            except Exception:
                steps.append(f"{step_num}/{total_indices * 3 + 1}: sector series for {idx} [ok]")
        elif sec_status.get("computing"):
            steps.append(f"{step_num}/{total_indices * 3 + 1}: sector series for {idx} ... computing")
        else:
            steps.append(f"{step_num}/{total_indices * 3 + 1}: sector series for {idx} - pending")

        step_num += 1
        ind_status = INDUSTRY_SERIES_STATUS.get(idx, {})
        if ind_status.get("ready"):
            try:
                with db_lock:
                    cnt = local_db.execute(f"SELECT COUNT(*) FROM industry_series_{idx}").fetchone()[0]
                steps.append(f"{step_num}/{total_indices * 3 + 1}: industry series for {idx} ({cnt:,} rows) [ok]")
            except Exception:
                steps.append(f"{step_num}/{total_indices * 3 + 1}: industry series for {idx} [ok]")
        elif ind_status.get("computing"):
            steps.append(f"{step_num}/{total_indices * 3 + 1}: industry series for {idx} ... computing")
        else:
            steps.append(f"{step_num}/{total_indices * 3 + 1}: industry series for {idx} - pending")

    # Final step: index_prices
    total_steps = total_indices * 3 + 1
    if INDEX_PRICES_LOADED:
        try:
            with db_lock:
                cnt = local_db.execute("SELECT COUNT(*) FROM index_prices").fetchone()[0]
            steps.append(f"{total_steps}/{total_steps}: index prices ({cnt:,} rows) [ok]")
        except Exception:
            steps.append(f"{total_steps}/{total_steps}: index prices [ok]")
    else:
        steps.append(f"{total_steps}/{total_steps}: index prices - pending")

    completed = sum(1 for s in steps if "[ok]" in s)
    all_done = completed == total_steps

    # Count total rows and memory across all loaded tables
    total_rows = 0
    total_bytes = 0
    try:
        with db_lock:
            tables = [r[0] for r in local_db.execute("SHOW TABLES").fetchall()]
            for tbl in tables:
                rc = local_db.execute(f"SELECT COUNT(*) FROM \"{tbl}\"").fetchone()[0]
                total_rows += rc
                # estimated_size returns bytes for in-memory tables
                try:
                    sz = local_db.execute(f"SELECT estimated_size FROM duckdb_tables() WHERE table_name = '{tbl}'").fetchone()
                    if sz:
                        total_bytes += sz[0]
                except Exception:
                    pass
    except Exception:
        pass

    def fmt_bytes(b):
        if b >= 1_073_741_824:
            return f"{b / 1_073_741_824:.1f}Gb"
        if b >= 1_048_576:
            return f"{b / 1_048_576:.0f}Mb"
        if b >= 1024:
            return f"{b / 1024:.0f}Kb"
        return f"{b}b"

    def fmt_rows(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.0f}k"
        return str(n)

    def fmt_time(s):
        if s >= 60:
            m = int(s // 60)
            sec = s % 60
            return f"{m}m{sec:.0f}s" if sec >= 1 else f"{m}m"
        return f"{s:.0f}s"

    result = {
        "status": "ready" if all_done else "warming_up",
        "progress": f"{completed}/{total_steps}",
        "indices_loaded": len(loaded),
        "total_rows": fmt_rows(total_rows),
        "total_memory": fmt_bytes(total_bytes) if total_bytes else "n/a",
        "steps": steps,
    }

    if all_done and STARTUP_DONE_TIME and STARTUP_TIME:
        result["total_time"] = fmt_time(STARTUP_DONE_TIME - STARTUP_TIME)
        cet = timezone(timedelta(hours=1))
        result["loaded_at"] = datetime.fromtimestamp(STARTUP_DONE_TIME, tz=cet).strftime("%d %b %Y %H:%M:%S CET")
    elif STARTUP_TIME:
        result["elapsed"] = fmt_time(time.time() - STARTUP_TIME)

    return result


@app.get("/market-data")
async def get_market_data():
    return LATEST_MARKET_DATA


@app.post("/api/admin/refresh")
async def webhook_refresh():
    loaded = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not loaded:
        return {"status": "skipped", "message": "No indices loaded yet"}
    for idx in loaded:
        asyncio.create_task(refresh_single_index(idx))
        await asyncio.sleep(0.5)
    return {"status": "accepted", "message": f"Refreshing {len(loaded)} loaded indices"}


@app.post("/api/admin/refresh/{index_key}")
async def webhook_refresh_index(index_key: str):
    if index_key == "index_prices":
        loop = asyncio.get_event_loop()
        asyncio.create_task(loop.run_in_executor(None, _load_index_prices_from_bq))
        return {"status": "accepted", "message": "Refreshing index_prices"}
    if index_key not in MARKET_INDICES:
        return {"status": "error", "message": f"Unknown index: {index_key}"}
    asyncio.create_task(refresh_single_index(index_key))
    return {"status": "accepted", "message": f"Refreshing {index_key}"}


# ============================================================================
# REST API — Index overview
# ============================================================================

@app.get("/index-prices/debug")
async def get_index_prices_debug():
    if not INDEX_PRICES_LOADED:
        return {"loaded": False}
    try:
        with db_lock:
            rows = local_db.execute("""
                SELECT symbol,
                    MIN(trade_date)::VARCHAR as min_date,
                    MAX(trade_date)::VARCHAR as max_date,
                    COUNT(*) as row_count
                FROM index_prices GROUP BY symbol ORDER BY symbol
            """).fetchall()
        return {
            "loaded": True,
            "symbols": [
                {"symbol": r[0], "min_date": r[1], "max_date": r[2], "rows": r[3]}
                for r in rows
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/index-prices/summary")
async def get_index_prices_summary():
    if not INDEX_PRICES_LOADED:
        return []

    cache_key = "index_prices_summary"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        with db_lock:
            res = local_db.execute(sql("index_prices_summary.sql")).df().fillna(0).to_dict(orient="records")
        set_cached_response(cache_key, res)
        return res
    except Exception as e:
        print(f"Index prices summary error: {e}")
        return []


@app.get("/index-prices/data")
async def get_index_prices_data(symbols: str = "", period: str = "1y"):
    """Multi-symbol index price comparison using unified timeline + forward-fill."""
    if not INDEX_PRICES_LOADED:
        return {"series": []}

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return {"series": []}

    cache_key = f"index_prices_data_{','.join(sorted(symbol_list))}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        placeholders = ",".join(["?" for _ in symbol_list])
        date_clause = ""
        if period.lower() != "max":
            days = INTERVALS.get(period.lower(), 365)
            date_clause = f"AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'"

        query = (
            sql("index_prices_data.sql")
            .replace("{placeholders}", placeholders)
            .replace("{date_clause}", date_clause)
        )

        with db_lock:
            df = local_db.execute(query, symbol_list).df()

        if df.empty:
            result = {"series": []}
            set_cached_response(cache_key, result)
            return result

        series = []
        for sym in symbol_list:
            sym_df = df[df["symbol"] == sym]
            if sym_df.empty:
                continue
            points = [
                {
                    "time": str(row["time"]),
                    "close": float(row["close"]),
                    "pct": float(row["pct"]),
                    "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                }
                for _, row in sym_df.iterrows()
            ]
            series.append({
                "symbol": sym,
                "indexKey": INDEX_TICKER_TO_KEY.get(sym, sym),
                "points": points,
            })

        result = {"series": series}
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Index prices data error: {e}")
        return {"series": []}


@app.get("/index-prices/stats")
async def get_index_prices_stats(period: str = "1y", start: str = "", end: str = ""):
    if not INDEX_PRICES_LOADED:
        return []

    use_custom = bool(start and end)
    cache_key = f"index_stats_{start}_{end}" if use_custom else f"index_stats_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        with db_lock:
            symbols = [s[0] for s in local_db.execute(
                "SELECT DISTINCT symbol FROM index_prices"
            ).fetchall()]

            results = []
            for sym in symbols:
                latest = local_db.execute("""
                    SELECT close, trade_date, name, currency, exchange
                    FROM index_prices WHERE symbol = ?
                    ORDER BY trade_date DESC LIMIT 1
                """, [sym]).fetchone()
                if not latest:
                    continue

                current_price = float(latest[0])
                latest_date, name, currency, exchange = latest[1], latest[2], latest[3], latest[4] or ""

                prev = local_db.execute("""
                    SELECT close FROM index_prices
                    WHERE symbol = ? AND trade_date < ?
                    ORDER BY trade_date DESC LIMIT 1
                """, [sym, latest_date]).fetchone()
                daily_change = float(((current_price - prev[0]) / prev[0]) * 100) if prev and prev[0] else 0

                # Period return start price
                if use_custom:
                    period_start = local_db.execute("""
                        SELECT close FROM index_prices WHERE symbol = ? AND trade_date >= ?
                        ORDER BY trade_date ASC LIMIT 1
                    """, [sym, start]).fetchone()
                elif period.lower() == "max":
                    period_start = local_db.execute("""
                        SELECT close FROM index_prices WHERE symbol = ?
                        ORDER BY trade_date ASC LIMIT 1
                    """, [sym]).fetchone()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    period_start = local_db.execute(
                        sql("index_stats_period_start.sql").replace("{days}", str(days)),
                        [sym, sym]
                    ).fetchone()
                period_return = float(
                    ((current_price - period_start[0]) / period_start[0]) * 100
                ) if period_start and period_start[0] else 0

                # YTD return
                ytd_start = local_db.execute("""
                    SELECT close FROM index_prices
                    WHERE symbol = ? AND trade_date >= DATE_TRUNC('year', CURRENT_DATE)
                    ORDER BY trade_date ASC LIMIT 1
                """, [sym]).fetchone()
                ytd_return = float(
                    ((current_price - ytd_start[0]) / ytd_start[0]) * 100
                ) if ytd_start and ytd_start[0] else 0

                # 52-week high/low
                range_52w = local_db.execute("""
                    SELECT MIN(low) as low_52w, MAX(high) as high_52w FROM index_prices
                    WHERE symbol = ? AND trade_date >= (
                        SELECT MAX(trade_date) - INTERVAL '365 days' FROM index_prices WHERE symbol = ?
                    )
                """, [sym, sym]).fetchone()
                low_52w = float(range_52w[0]) if range_52w and range_52w[0] else current_price
                high_52w = float(range_52w[1]) if range_52w and range_52w[1] else current_price

                # Volatility
                if use_custom:
                    vol_row = local_db.execute(
                        sql("index_stats_volatility_custom.sql")
                        .replace("{start}", start)
                        .replace("{end}", end),
                        [sym]
                    ).fetchone()
                elif period.lower() == "max":
                    vol_row = local_db.execute(
                        sql("index_stats_volatility_max.sql"), [sym]
                    ).fetchone()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    vol_row = local_db.execute(
                        sql("index_stats_volatility_period.sql").replace("{days}", str(days)),
                        [sym, sym]
                    ).fetchone()
                volatility = float(vol_row[0] * 100) if vol_row and vol_row[0] else 0

                results.append({
                    "symbol": sym, "name": name, "currency": currency, "exchange": exchange,
                    "current_price": round(current_price, 2),
                    "daily_change_pct": round(daily_change, 2),
                    "period_return_pct": round(period_return, 2),
                    "ytd_return_pct": round(ytd_return, 2),
                    "high_52w": round(high_52w, 2),
                    "low_52w": round(low_52w, 2),
                    "volatility_pct": round(volatility, 2),
                })

        set_cached_response(cache_key, results)
        return results

    except Exception as e:
        print(f"Index stats error: {e}")
        import traceback; traceback.print_exc()
        return []


@app.get("/index-prices/single/{symbol:path}")
async def get_index_price_single(symbol: str, period: str = "max"):
    if not INDEX_PRICES_LOADED:
        return []

    symbol = unquote(symbol)
    cache_key = f"index_price_single_{symbol}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        with db_lock:
            if period.lower() == "max":
                df = local_db.execute(sql("index_price_single_max.sql"), [symbol]).df()
            else:
                days = INTERVALS.get(period.lower(), 365)
                df = local_db.execute(
                    sql("index_price_single_period.sql").replace("{days}", str(days)),
                    [symbol]
                ).df()

        result = df.fillna(0).to_dict(orient="records")
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Index price single error ({symbol}): {e}")
        return []


# ============================================================================
# REST API — Sector comparison
# ============================================================================

@app.get("/sector-comparison/data")
async def get_sector_comparison_data(sector: str = "Technology", indices: str = "", period: str = "max"):
    """Legacy endpoint — simple AVG(close) normalisation. Kept for backwards compatibility."""
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list or not sector:
        return {"series": [], "sector": sector}

    cache_key = f"sector_compare_{sector}_{','.join(sorted(index_list))}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        series = []
        for idx in index_list:
            if not ensure_index_loaded(idx):
                continue
            table = f"prices_{idx}"

            with db_lock:
                if period.lower() == "max":
                    df = local_db.execute(
                        sql("legacy_sector_avg_max.sql").replace("{table}", table),
                        [sector]
                    ).df()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    df = local_db.execute(
                        sql("legacy_sector_avg_period.sql")
                        .replace("{table}", table)
                        .replace("{days}", str(days)),
                        [sector]
                    ).df()

            if df.empty or len(df) < 2:
                continue

            closes, times = df["close"].values, df["time"].values
            base = closes[0] if closes[0] != 0 else 1
            points = [
                {"time": str(times[i]), "pct": float(((closes[i] - base) / base) * 100), "close": float(closes[i])}
                for i in range(len(closes))
            ]
            series.append({"indexKey": idx, "points": points})

        result = {"series": series, "sector": sector}
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Sector comparison data error: {e}")
        return {"series": [], "sector": sector}


@app.get("/sector-comparison/sectors")
async def get_available_sectors(indices: str = ""):
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list:
        return []

    cache_key = f"available_sectors_{','.join(sorted(index_list))}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        tables = [f"prices_{idx}" for idx in index_list if ensure_index_loaded(idx)]
        if not tables:
            return []

        union = " UNION ALL ".join([
            f"SELECT DISTINCT sector FROM {t} WHERE sector IS NOT NULL AND sector NOT IN ('N/A', '0', '')"
            for t in tables
        ])
        with db_lock:
            df = local_db.execute(f"SELECT DISTINCT sector FROM ({union}) ORDER BY sector ASC").df()

        result = df["sector"].tolist() if not df.empty else []
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Available sectors error: {e}")
        return []


@app.get("/sector-comparison/industries")
async def get_sector_industries(sector: str = "", indices: str = ""):
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list or not sector:
        return []

    cache_key = f"sector_industries_{sector}_{','.join(sorted(index_list))}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        all_industries = {}
        for idx in index_list:
            if not ensure_index_loaded(idx):
                continue
            with db_lock:
                df = local_db.execute(
                    sql("sector_industries.sql").replace("{table}", f"prices_{idx}"),
                    [sector]
                ).df()
            if df.empty:
                continue
            for _, row in df.iterrows():
                ind = row["industry"]
                if ind not in all_industries:
                    all_industries[ind] = {}
                all_industries[ind][idx] = int(row["cnt"])

        result = [{"industry": k, "indices": v, "total": sum(v.values())} for k, v in all_industries.items()]
        result.sort(key=lambda x: x["total"], reverse=True)
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Sector industries error: {e}")
        return []


@app.get("/sector-comparison/data-v2")
async def get_sector_comparison_data_v2(
    sector: str = "Technology",
    indices: str = "",
    industries: str = "",
    mode: str = "cross-index",
    period: str = "max",
):
    """
    Clean sector comparison using per-stock normalisation + unified timeline + forward-fill.
    mode=cross-index: one sector across multiple indices (each index = one line).
    mode=single-index: multiple sectors within one index (each sector = one line).
    """
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        return {"series": [], "mode": mode}

    industry_filter = [i.strip() for i in industries.split(",") if i.strip()] if industries else []
    cache_key = f"sector_v2_{mode}_{sector}_{','.join(sorted(index_list))}_{','.join(sorted(industry_filter))}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    # --- Fast path: serve from pre-computed sector_series tables ---
    use_precomputed = (not industry_filter and period.lower() == "max")
    if use_precomputed:
        try:
            series = []
            all_ready = True

            if mode == "cross-index":
                for idx in index_list:
                    if not SECTOR_SERIES_STATUS.get(idx, {}).get("ready"):
                        all_ready = False
                        break
                    with db_lock:
                        df = local_db.execute(
                            f"SELECT time, pct FROM sector_series_{idx} WHERE sector = ? ORDER BY time",
                            [sector]
                        ).df()
                    if df.empty or len(df) < 2:
                        continue
                    points = [{"time": str(r["time"]), "pct": float(r["pct"])} for _, r in df.iterrows()]
                    series.append({"symbol": idx, "points": points})

            elif mode == "single-index":
                idx = index_list[0]
                if not SECTOR_SERIES_STATUS.get(idx, {}).get("ready"):
                    all_ready = False
                else:
                    for sec in [s.strip() for s in sector.split(",") if s.strip()]:
                        with db_lock:
                            df = local_db.execute(
                                f"SELECT time, pct FROM sector_series_{idx} WHERE sector = ? ORDER BY time",
                                [sec]
                            ).df()
                        if df.empty or len(df) < 2:
                            continue
                        points = [{"time": str(r["time"]), "pct": float(r["pct"])} for _, r in df.iterrows()]
                        series.append({"symbol": sec, "points": points})

            if all_ready and series:
                result = {"series": series, "mode": mode}
                set_cached_response(cache_key, result)
                return result
        except Exception:
            pass  # Fall through to slow path

    # --- Slow path: run clean_sector_series.sql on the fly ---
    def _build_clauses(sec):
        industry_clause = ""
        params = [sec]
        if industry_filter:
            ph = ",".join(["?" for _ in industry_filter])
            industry_clause = f" AND industry IN ({ph})"
            params += industry_filter
        date_clause = ""
        if period.lower() != "max":
            days = INTERVALS.get(period.lower(), 365)
            date_clause = f" AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'"
        return industry_clause, date_clause, params

    try:
        series = []

        if mode == "cross-index":
            for idx in index_list:
                if not ensure_index_loaded(idx):
                    continue
                industry_clause, date_clause, params = _build_clauses(sector)
                q = build_clean_sector_sql(f"prices_{idx}", "sector = ?", industry_clause, date_clause)
                with db_lock:
                    df = local_db.execute(q, params).df()
                if df.empty or len(df) < 2:
                    continue
                points = [{"time": str(r["time"]), "pct": float(r["pct"])} for _, r in df.iterrows()]
                series.append({"symbol": idx, "points": points})

        elif mode == "single-index":
            idx = index_list[0]
            if not ensure_index_loaded(idx):
                return {"series": [], "mode": mode}
            for sec in [s.strip() for s in sector.split(",") if s.strip()]:
                industry_clause, date_clause, params = _build_clauses(sec)
                q = build_clean_sector_sql(f"prices_{idx}", "sector = ?", industry_clause, date_clause)
                with db_lock:
                    df = local_db.execute(q, params).df()
                if df.empty or len(df) < 2:
                    continue
                points = [{"time": str(r["time"]), "pct": float(r["pct"])} for _, r in df.iterrows()]
                series.append({"symbol": sec, "points": points})

        result = {"series": series, "mode": mode}
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Sector comparison v2 error: {e}")
        import traceback; traceback.print_exc()
        return {"series": [], "mode": mode}


@app.get("/sector-comparison/all-series")
async def get_all_sector_series(indices: str = ""):
    """Return ALL pre-computed sector time series for requested indices.
    Used by frontend for instant mode/sector/index switching."""
    if indices and indices.lower() != "all":
        index_list = [i.strip() for i in indices.split(",")
                      if i.strip() and i.strip() in MARKET_INDICES]
    else:
        index_list = [k for k, v in SECTOR_SERIES_STATUS.items() if v.get("ready")]

    if not index_list:
        return {"data": {}, "ready": [], "pending": []}

    result = {}
    ready_indices = []
    pending_indices = []

    for idx in index_list:
        status = SECTOR_SERIES_STATUS.get(idx, {})
        if not status.get("ready"):
            pending_indices.append(idx)
            continue

        series_table = f"sector_series_{idx}"
        try:
            with db_lock:
                df = local_db.execute(
                    f"SELECT sector, time, pct FROM {series_table} ORDER BY sector, time"
                ).df()

            if df.empty:
                continue

            idx_data = {}
            current_sector = None
            points = []
            for _, row in df.iterrows():
                s = row["sector"]
                if s != current_sector:
                    if current_sector is not None:
                        idx_data[current_sector] = points
                    current_sector = s
                    points = []
                points.append({"time": str(row["time"]), "pct": float(row["pct"])})
            if current_sector is not None:
                idx_data[current_sector] = points

            result[idx] = idx_data
            ready_indices.append(idx)
        except Exception as e:
            print(f"  all-series: error reading {idx}: {e}")
            pending_indices.append(idx)

    return {"data": result, "ready": ready_indices, "pending": pending_indices}


@app.get("/sector-comparison/industry-series")
async def get_industry_series(sector: str = "", indices: str = ""):
    """Return pre-computed industry time series for ONE sector across requested indices.
    Used by frontend for instant industry filtering."""
    if not sector:
        return {"data": {}, "ready": [], "pending": []}

    if indices and indices.lower() != "all":
        index_list = [i.strip() for i in indices.split(",")
                      if i.strip() and i.strip() in MARKET_INDICES]
    else:
        index_list = [k for k, v in INDUSTRY_SERIES_STATUS.items() if v.get("ready")]

    if not index_list:
        return {"data": {}, "ready": [], "pending": []}

    result = {}
    ready_indices = []
    pending_indices = []

    for idx in index_list:
        status = INDUSTRY_SERIES_STATUS.get(idx, {})
        if not status.get("ready"):
            pending_indices.append(idx)
            continue

        series_table = f"industry_series_{idx}"
        try:
            with db_lock:
                df = local_db.execute(
                    f"SELECT industry, time, pct, stock_count FROM {series_table} "
                    f"WHERE sector = ? ORDER BY industry, time",
                    [sector]
                ).df()

            if df.empty:
                ready_indices.append(idx)
                result[idx] = {}
                continue

            idx_data = {}
            current_industry = None
            points = []
            for _, row in df.iterrows():
                ind = row["industry"]
                if ind != current_industry:
                    if current_industry is not None:
                        idx_data[current_industry] = points
                    current_industry = ind
                    points = []
                points.append({
                    "time": str(row["time"]),
                    "pct": round(float(row["pct"]), 4),
                    "n": int(row["stock_count"]),
                })
            if current_industry is not None:
                idx_data[current_industry] = points

            result[idx] = idx_data
            ready_indices.append(idx)
        except Exception as e:
            print(f"  industry-series: error reading {idx}/{sector}: {e}")
            pending_indices.append(idx)

    return {"data": result, "ready": ready_indices, "pending": pending_indices}


@app.get("/sector-comparison/histogram")
async def get_sector_histogram(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list:
        return []

    use_custom = bool(start and end)
    cache_key = (
        f"sector_histogram_{','.join(sorted(index_list))}_{start}_{end}"
        if use_custom else
        f"sector_histogram_{','.join(sorted(index_list))}_{period}"
    )
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        sector_returns = {}
        for idx in index_list:
            if not ensure_index_loaded(idx):
                continue
            with db_lock:
                df = _sector_returns_df(f"prices_{idx}", use_custom, period, start, end)
            if df.empty:
                continue
            for _, row in df.iterrows():
                sec = row["sector"]
                sector_returns.setdefault(sec, []).append(float(row["return_pct"]))

        result = [
            {"sector": sec, "return_pct": round(sum(r) / len(r), 2)}
            for sec, r in sector_returns.items()
        ]
        result.sort(key=lambda x: x["return_pct"], reverse=True)
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Sector histogram error: {e}")
        return []


@app.get("/sector-comparison/table")
async def get_sector_comparison_table(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list:
        return []

    use_custom = bool(start and end)
    cache_key = (
        f"sector_table_{','.join(sorted(index_list))}_{start}_{end}"
        if use_custom else
        f"sector_table_{','.join(sorted(index_list))}_{period}"
    )
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        all_data = {}
        for idx in index_list:
            if not ensure_index_loaded(idx):
                continue
            with db_lock:
                df = _sector_returns_df(f"prices_{idx}", use_custom, period, start, end)
            if df.empty:
                continue
            for _, row in df.iterrows():
                sector = row["sector"]
                all_data.setdefault(sector, {})[idx] = {
                    "return_pct": round(float(row["return_pct"]), 2),
                    "stock_count": int(row["stock_count"]),
                }

        result = []
        for sector, per_index in all_data.items():
            returns = [v["return_pct"] for v in per_index.values()]
            result.append({
                "sector": sector,
                "avg_return_pct": round(sum(returns) / len(returns), 2),
                "indices": per_index,
            })
        result.sort(key=lambda x: x["avg_return_pct"], reverse=True)
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Sector comparison table error: {e}")
        import traceback; traceback.print_exc()
        return []


@app.get("/sector-comparison/top-stocks")
async def get_sector_top_stocks(
    sector: str, indices: str = "", period: str = "1y",
    start: str = "", end: str = "", n: int = 5
):
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list or not sector:
        return {"top": [], "bottom": []}

    use_custom = bool(start and end)
    cache_key = (
        f"sector_top_stocks_{sector}_{','.join(sorted(index_list))}_{start}_{end}"
        if use_custom else
        f"sector_top_stocks_{sector}_{','.join(sorted(index_list))}_{period}"
    )
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        all_rows = []
        for idx in index_list:
            if not ensure_index_loaded(idx):
                continue
            table = f"prices_{idx}"

            with db_lock:
                if use_custom:
                    df = local_db.execute(
                        sql("sector_top_stocks_custom.sql")
                        .replace("{table}", table)
                        .replace("{start}", start)
                        .replace("{end}", end),
                        [sector]
                    ).df()
                elif period.lower() == "max":
                    df = local_db.execute(
                        sql("sector_top_stocks_max.sql").replace("{table}", table),
                        [sector]
                    ).df()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    df = local_db.execute(
                        sql("sector_top_stocks_period.sql")
                        .replace("{table}", table)
                        .replace("{days}", str(days)),
                        [sector]
                    ).df()

            if df.empty:
                continue
            for _, row in df.iterrows():
                all_rows.append({
                    "symbol": row["symbol"],
                    "name": row["name"] if row["name"] and row["name"] != "0" else "",
                    "return_pct": round(float(row["return_pct"]), 2),
                    "index_key": idx,
                })

        if not all_rows:
            return {"top": [], "bottom": []}

        all_rows.sort(key=lambda x: x["return_pct"], reverse=True)
        result = {"top": all_rows[:n], "bottom": list(reversed(all_rows[-n:]))}
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Sector top stocks error: {e}")
        import traceback; traceback.print_exc()
        return {"top": [], "bottom": []}


@app.get("/sector-comparison/industry-breakdown")
async def get_industry_breakdown(
    index: str = "", sector: str = "",
    period: str = "1y", start: str = "", end: str = "",
):
    if not index or index not in MARKET_INDICES or not sector:
        return []

    use_custom = bool(start and end)
    cache_key = (
        f"industry_breakdown_{index}_{sector}_{start}_{end}"
        if use_custom else
        f"industry_breakdown_{index}_{sector}_{period}"
    )
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    if not ensure_index_loaded(index):
        return []

    table = f"prices_{index}"

    try:
        with db_lock:
            if use_custom:
                df = local_db.execute(
                    sql("industry_breakdown_custom.sql")
                    .replace("{table}", table)
                    .replace("{start}", start)
                    .replace("{end}", end),
                    [sector]
                ).df()
            elif period.lower() == "max":
                df = local_db.execute(
                    sql("industry_breakdown_max.sql").replace("{table}", table),
                    [sector]
                ).df()
            else:
                days = INTERVALS.get(period.lower(), 365)
                df = local_db.execute(
                    sql("industry_breakdown_period.sql")
                    .replace("{table}", table)
                    .replace("{days}", str(days)),
                    [sector]
                ).df()

        if df.empty:
            return []

        result = [
            {
                "industry": row["industry"],
                "return_pct": round(float(row["return_pct"]), 2),
                "stock_count": int(row["stock_count"]),
            }
            for _, row in df.iterrows()
        ]
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Industry breakdown error: {e}")
        import traceback; traceback.print_exc()
        return []


@app.get("/index-prices/top-sectors")
async def get_top_sectors(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list:
        return {"top": [], "bottom": []}

    use_custom = bool(start and end)
    cache_key = (
        f"top_sectors_{','.join(sorted(index_list))}_{start}_{end}"
        if use_custom else
        f"top_sectors_{','.join(sorted(index_list))}_{period}"
    )
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        tables = [f"prices_{idx}" for idx in index_list if ensure_index_loaded(idx)]
        if not tables:
            return {"top": [], "bottom": []}

        union = " UNION ALL ".join([f"SELECT symbol, sector, close, trade_date FROM {t}" for t in tables])

        with db_lock:
            df = _top_items_df(union, "sector", use_custom, period, start, end)

        if df.empty:
            result = {"top": [], "bottom": []}
        else:
            result = {
                "top": df.head(5).to_dict("records"),
                "bottom": df.tail(5).sort_values("value").to_dict("records"),
            }
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Top sectors error: {e}")
        return {"top": [], "bottom": []}


@app.get("/index-prices/top-industries")
async def get_top_industries(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list:
        return {"top": [], "bottom": []}

    use_custom = bool(start and end)
    cache_key = (
        f"top_industries_{','.join(sorted(index_list))}_{start}_{end}"
        if use_custom else
        f"top_industries_{','.join(sorted(index_list))}_{period}"
    )
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        tables = [f"prices_{idx}" for idx in index_list if ensure_index_loaded(idx)]
        if not tables:
            return {"top": [], "bottom": []}

        union = " UNION ALL ".join([f"SELECT symbol, industry, close, trade_date FROM {t}" for t in tables])

        with db_lock:
            df = _top_items_df(union, "industry", use_custom, period, start, end)

        if df.empty:
            result = {"top": [], "bottom": []}
        else:
            result = {
                "top": df.head(5).to_dict("records"),
                "bottom": df.tail(5).sort_values("value").to_dict("records"),
            }
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Top industries error: {e}")
        return {"top": [], "bottom": []}


# ============================================================================
# REST API — Stock data
# ============================================================================

@app.get("/summary")
async def get_summary(index: str = "sp500"):
    if index not in MARKET_INDICES:
        index = "sp500"
    if not ensure_index_loaded(index):
        return []

    cache_key = f"summary_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        with db_lock:
            res = (
                local_db.execute(
                    sql("summary.sql")
                    .replace("{index}", index)
                )
                .df().fillna(0).to_dict(orient="records")
            )
        set_cached_response(cache_key, res)
        return res
    except Exception as e:
        print(f"Summary Error ({index}): {e}")
        return []


SYMBOL_INDEX_MAP: dict = {}
SUFFIX_TO_INDEX = {
    ".T":  "nikkei225",
    ".SS": "csi300",  ".SZ": "csi300",
    ".NS": "nifty50", ".BO": "nifty50",
    ".L":  "ftse100",
    ".DE": "stoxx50", ".PA": "stoxx50", ".AS": "stoxx50",
    ".BR": "stoxx50", ".MI": "stoxx50", ".MC": "stoxx50", ".HE": "stoxx50",
}


def _guess_index_for_symbol(symbol):
    upper = symbol.upper()
    for suffix, index_key in SUFFIX_TO_INDEX.items():
        if upper.endswith(suffix.upper()):
            return index_key
    return "sp500" if "." not in symbol else None


def _find_symbol_table(symbol):
    if symbol in SYMBOL_INDEX_MAP:
        idx = SYMBOL_INDEX_MAP[symbol]
        if INDEX_LOAD_STATUS.get(idx, {}).get("loaded"):
            return f"prices_{idx}"

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

    guessed_index = _guess_index_for_symbol(symbol)
    if guessed_index and guessed_index in MARKET_INDICES:
        if not INDEX_LOAD_STATUS.get(guessed_index, {}).get("loaded"):
            print(f"  Lazy-loading {guessed_index} for symbol {symbol}")
            if ensure_index_loaded(guessed_index):
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


@app.get("/data/{symbol:path}")
async def get_data(symbol: str, period: str = "1y"):
    symbol = unquote(symbol)
    cache_key = f"data_{symbol}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    table = _find_symbol_table(symbol)
    if not table:
        set_cached_response(cache_key, [])
        return []

    try:
        with db_lock:
            if period.lower() == "max":
                df = local_db.execute(
                    sql("symbol_data_max.sql").replace("{table}", table),
                    [symbol]
                ).df()
            else:
                days = INTERVALS.get(period.lower(), 365)
                df = local_db.execute(
                    sql("symbol_data_period.sql")
                    .replace("{table}", table)
                    .replace("{days}", str(days)),
                    [symbol, symbol]
                ).df()

        result = df.fillna(0).to_dict(orient="records")
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Data Error: {e}")
        return []


@app.get("/rankings")
async def get_rankings(period: str = "1y", index: str = "sp500"):
    if index not in MARKET_INDICES:
        index = "sp500"
    if not ensure_index_loaded(index):
        return {"selected": {"top": [], "bottom": []}}

    cache_key = f"rankings_{period}_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    table = f"prices_{index}"

    try:
        with db_lock:
            if period.lower() == "max":
                df = local_db.execute(
                    sql("rankings_max.sql")
                    .replace("{table}", table)
                    .replace("{index}", index)
                ).df()
            else:
                days = INTERVALS.get(period.lower(), 365)
                df = local_db.execute(
                    sql("rankings_period.sql")
                    .replace("{table}", table)
                    .replace("{index}", index)
                    .replace("{days}", str(days))
                ).df()

        result = {
            "selected": {
                "top":    df.head(3).to_dict("records"),
                "bottom": df.tail(3).sort_values("value").to_dict("records"),
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

    cache_key = f"rankings_custom_{start}_{end}_{index}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    table = f"prices_{index}"

    try:
        with db_lock:
            df = local_db.execute(
                sql("rankings_custom.sql")
                .replace("{table}", table)
                .replace("{index}", index)
                .replace("{start}", start)
                .replace("{end}", end)
            ).df()

        result = {
            "selected": {
                "top":    df.head(3).to_dict("records"),
                "bottom": df.tail(3).sort_values("value").to_dict("records"),
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
            res = local_db.execute(
                f"SELECT name FROM {table} WHERE symbol = ? LIMIT 1", [symbol]
            ).fetchone()
        return {"symbol": symbol, "name": res[0] if res else symbol}
    except:
        return {"symbol": symbol, "name": symbol}


if __name__ == "__main__":
    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
