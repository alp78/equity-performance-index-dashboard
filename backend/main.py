# FastAPI backend for stock market dashboard.
# Loads BigQuery data into DuckDB for fast querying; serves REST + WebSocket APIs.

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
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from urllib.parse import unquote
from contextlib import asynccontextmanager, contextmanager


# --- CONFIGURATION ---

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


# --- SQL LOADER ---

_SQL_DIR = Path(__file__).parent / "sql"
_SQL_CACHE: dict[str, str] = {}


def sql(filename: str) -> str:
    """Load and cache a SQL template from the sql/ directory."""
    if filename not in _SQL_CACHE:
        _SQL_CACHE[filename] = (_SQL_DIR / filename).read_text()
    return _SQL_CACHE[filename]


# --- CACHE LAYER ---

local_db = duckdb.connect(database=':memory:', read_only=False)


class _RWLock:
    """Read-write lock: concurrent reads, exclusive writes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._readers_lock = threading.Lock()
        self._readers = 0

    @contextmanager
    def read(self):
        with self._readers_lock:
            self._readers += 1
            if self._readers == 1:
                self._lock.acquire()
        try:
            yield
        finally:
            with self._readers_lock:
                self._readers -= 1
                if self._readers == 0:
                    self._lock.release()

    @contextmanager
    def write(self):
        self._lock.acquire()
        try:
            yield
        finally:
            self._lock.release()


db_rwlock = _RWLock()

INDEX_LOAD_STATUS: dict = {}
SECTOR_SERIES_STATUS: dict = {}
INDUSTRY_SERIES_STATUS: dict = {}
STOCK_RETURNS_STATUS: dict = {}
PREWARM_STATUS: dict = {}
INDEX_PRICES_ROW_COUNT: int = 0
LATEST_MARKET_DATA: dict = {}
STARTUP_TIME: float = 0.0
STARTUP_DONE_TIME: float = 0.0

API_CACHE: dict = {}
ALL_SERIES_CACHE: dict = {}
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


# --- SECTOR SERIES BUILDER ---

def build_clean_sector_sql(table, sector_clause, industry_clause="", date_clause=""):
    """Build normalized per-stock % change SQL with forward-fill on a unified timeline.
    Prevents spikes from calendar mismatches, IPOs/delistings, and differing price scales."""
    return (
        sql("clean_sector_series.sql")
        .replace("{table}", table)
        .replace("{sector_clause}", sector_clause)
        .replace("{industry_clause}", industry_clause)
        .replace("{date_clause}", date_clause)
    )


# --- WEBSOCKET CONNECTION MANAGER ---

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


# --- DATA INGESTION ---

_bq_client = None


def get_bq_client():
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client


def _load_index_from_bq(index_key):
    """Fetch one index from BigQuery, create per-index DuckDB table + latest snapshot."""
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

        with db_rwlock.write():
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
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{index_key}_sector ON {table_name} (sector)"
            )
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{index_key}_si ON {table_name} (sector, industry)"
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
    """Recreate the 'prices' and 'latest_prices' views as a union of all loaded index tables."""
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
    """Build normalized % change time series for all sectors, stored as sector_series_{index}."""
    table_name = f"prices_{index_key}"
    series_table = f"sector_series_{index_key}"

    SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": True}
    t0 = time.time()

    try:
        precompute_sql = sql("precompute_all_sector_series.sql").replace("{table}", table_name)

        with db_rwlock.write():
            local_db.execute(f"DROP TABLE IF EXISTS {series_table}")
            local_db.execute(f"CREATE TABLE {series_table} AS {precompute_sql}")
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{series_table}_sector ON {series_table} (sector)"
            )
            row_count = local_db.execute(f"SELECT COUNT(*) FROM {series_table}").fetchone()[0]
            sector_count = local_db.execute(
                f"SELECT COUNT(DISTINCT sector) FROM {series_table}"
            ).fetchone()[0]

            # pre-populate ALL_SERIES_CACHE so /all-series serves instantly
            df_cache = local_db.execute(
                f"SELECT sector, time, pct FROM {series_table} ORDER BY sector, time"
            ).df()
            if not df_cache.empty:
                idx_data = {}
                for sector, group in df_cache.groupby("sector", sort=False):
                    idx_data[sector] = [
                        {"time": str(t), "pct": float(p)}
                        for t, p in zip(group["time"].values, group["pct"].values)
                    ]
                ALL_SERIES_CACHE[index_key] = idx_data

        SECTOR_SERIES_STATUS[index_key] = {"ready": True, "computing": False, "row_count": row_count}
        print(f"  [{index_key}] Sector series precomputed: {sector_count} sectors, "
              f"{row_count} rows in {time.time() - t0:.1f}s")

    except Exception as e:
        SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
        print(f"  [{index_key}] Sector series precompute error: {e}")


def _precompute_industry_series(index_key):
    """Build normalized % change time series for all industries, stored as industry_series_{index}."""
    table_name = f"prices_{index_key}"
    series_table = f"industry_series_{index_key}"

    INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": True}
    t0 = time.time()

    try:
        precompute_sql = sql("precompute_all_industry_series.sql").replace("{table}", table_name)

        with db_rwlock.write():
            local_db.execute(f"DROP TABLE IF EXISTS {series_table}")
            local_db.execute(f"CREATE TABLE {series_table} AS {precompute_sql}")
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{series_table}_sector ON {series_table} (sector)"
            )
            local_db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{series_table}_si ON {series_table} (sector, industry)"
            )
            row_count = local_db.execute(f"SELECT COUNT(*) FROM {series_table}").fetchone()[0]
            industry_count = local_db.execute(
                f"SELECT COUNT(DISTINCT industry) FROM {series_table}"
            ).fetchone()[0]

        INDUSTRY_SERIES_STATUS[index_key] = {"ready": True, "computing": False, "row_count": row_count}
        print(f"  [{index_key}] Industry series precomputed: {industry_count} industries, "
              f"{row_count} rows in {time.time() - t0:.1f}s")

    except Exception as e:
        INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
        print(f"  [{index_key}] Industry series precompute error: {e}")


def _precompute_stock_returns(index_key):
    """Precompute per-stock returns for all sectors × standard periods into stock_returns_{index}."""
    table = f"prices_{index_key}"
    result_table = f"stock_returns_{index_key}"
    STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": True}
    t0 = time.time()

    PERIODS = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}

    try:
        all_dfs = []
        # Use write lock for entire block — DuckDB is not safe for concurrent
        # reads from multiple threads on the same connection.
        with db_rwlock.write():
            for period_name, days in PERIODS.items():
                df = local_db.execute(
                    sql("precompute_stock_returns.sql")
                    .replace("{table}", table)
                    .replace("{days}", str(days))
                ).df()
                if not df.empty:
                    df["period"] = period_name
                    all_dfs.append(df)

            df = local_db.execute(
                sql("precompute_stock_returns_max.sql").replace("{table}", table)
            ).df()
            if not df.empty:
                df["period"] = "max"
                all_dfs.append(df)

            if all_dfs:
                import pandas as pd
                combined = pd.concat(all_dfs, ignore_index=True)
                local_db.execute(f"DROP TABLE IF EXISTS {result_table}")
                local_db.execute(f"CREATE TABLE {result_table} AS SELECT * FROM combined")
                local_db.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{result_table}_sp ON {result_table} (sector, period)"
                )

        if all_dfs:
            total_rows = sum(len(df) for df in all_dfs)
            STOCK_RETURNS_STATUS[index_key] = {"ready": True, "computing": False, "rows": total_rows}
            print(f"  [{index_key}] Stock returns precomputed: {total_rows} rows in {time.time() - t0:.1f}s")
        else:
            # no data returned — mark NOT ready so endpoint doesn't try to query non-existent table
            STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": False, "rows": 0}
            print(f"  [{index_key}] Stock returns: no data returned from prices table")

    except Exception as e:
        STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": False}
        print(f"  [{index_key}] Stock returns precompute error: {e}")


def _prewarm_sector_caches(index_key):
    """Populate API_CACHE for sector table endpoint so heatmap/rankings load instantly."""
    table = f"prices_{index_key}"
    PREWARM_STATUS[index_key] = {"ready": False, "computing": True}
    t0 = time.time()
    periods_warmed = 0

    try:
        for period_label in ["max", "5y", "1y", "6mo", "3mo", "1mo", "1w"]:
            cache_key = f"sector_table_{index_key}_{period_label}"
            with db_rwlock.write():
                df = _sector_returns_df(table, False, period_label, "", "")
            if df.empty:
                continue

            all_data = {}
            for rec in df.to_dict("records"):
                all_data[rec["sector"]] = {
                    "return_pct": round(float(rec["return_pct"]), 2),
                    "stock_count": int(rec["stock_count"]),
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
            periods_warmed += 1

        PREWARM_STATUS[index_key] = {"ready": True, "computing": False, "periods": periods_warmed}
        print(f"  [{index_key}] Sector caches pre-warmed ({periods_warmed} periods) in {time.time() - t0:.1f}s")
    except Exception as e:
        PREWARM_STATUS[index_key] = {"ready": False, "computing": False}
        print(f"  [{index_key}] Sector cache pre-warm error: {e}")


def _load_index_prices_from_bq():
    """Load the index-level price history (e.g. ^GSPC, ^STOXX50E) from BigQuery into DuckDB."""
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

        with db_rwlock.write():
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
        global INDEX_PRICES_ROW_COUNT
        INDEX_PRICES_ROW_COUNT = row_count
        return row_count

    except Exception as e:
        print(f"  [index_prices] Load error: {e}")
        return 0


def ensure_index_loaded(index_key):
    """Trigger background load if not cached yet. Returns True only if data is ready."""
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
            _precompute_stock_returns(index_key)
            _prewarm_sector_caches(index_key)

    import concurrent.futures
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    _executor.submit(_bg_load)
    return False


async def refresh_single_index(index_key):
    """Reload one index from BigQuery, recompute series, and invalidate caches."""
    print(f"Refreshing index: {index_key}")
    invalidate_index_cache(index_key)
    ALL_SERIES_CACHE.pop(index_key, None)
    SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
    INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
    STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": False}
    PREWARM_STATUS[index_key] = {"ready": False, "computing": False}
    INDEX_LOAD_STATUS[index_key] = {"loaded": False, "loading": True, "row_count": 0}
    loop = asyncio.get_event_loop()
    row_count = await loop.run_in_executor(None, lambda: _load_index_from_bq(index_key))
    INDEX_LOAD_STATUS[index_key] = {
        "loaded": row_count > 0, "loading": False, "row_count": row_count,
    }
    if row_count > 0:
        await loop.run_in_executor(None, lambda: _precompute_sector_series(index_key))
        await loop.run_in_executor(None, lambda: _precompute_industry_series(index_key))
        await loop.run_in_executor(None, lambda: _precompute_stock_returns(index_key))
        await loop.run_in_executor(None, lambda: _prewarm_sector_caches(index_key))
    invalidate_index_cache(index_key)


# --- HELPERS ---

INTERVALS = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}


def _sector_returns_df(table, use_custom, period, start, end):
    """Execute the appropriate sector returns SQL variant and return a DataFrame."""
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
    """Execute the appropriate top sectors/industries SQL variant."""
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


# --- REAL-TIME DATA FEEDS ---

async def fetch_crypto_data():
    """Fetch BTC/USDT price from Binance and broadcast via WebSocket."""
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
    """Fetch live prices for global macro instruments and index leader stocks via yfinance."""
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


# --- BACKGROUND TASKS ---

async def self_keepalive():
    """Ping localhost every 4 minutes to prevent Cloud Run container recycling."""
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
    """Poll crypto every 10s, stocks every 60s (every 6th cycle)."""
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
    """Two-phase startup: priority indices first, then remaining indices + index prices."""
    loop = asyncio.get_event_loop()

    # phase 1: stoxx50 + sp500 in parallel (highest priority)
    phase1 = ["stoxx50", "sp500"]
    phase1_tasks = [refresh_single_index(idx) for idx in phase1]
    results = await asyncio.gather(*phase1_tasks, return_exceptions=True)
    for idx, res in zip(phase1, results):
        if isinstance(res, Exception):
            print(f"Phase 1 error loading {idx}: {res}")
    print("Phase 1 preload complete (stoxx50, sp500)")

    asyncio.create_task(market_data_feeder())
    asyncio.create_task(self_keepalive())

    # phase 2: remaining indices + index_prices in parallel
    remaining = [k for k in MARKET_INDICES if k not in ("stoxx50", "sp500")]

    async def _load_remaining(idx):
        try:
            await refresh_single_index(idx)
            print(f"  Background preload: {idx} ready")
        except Exception as e:
            print(f"  Background preload error {idx}: {e}")

    async def _load_index_prices():
        try:
            row_count = await loop.run_in_executor(None, _load_index_prices_from_bq)
            print(f"  Index prices loaded: {row_count} rows")
        except Exception as e:
            print(f"  Index prices load error: {e}")

    phase2_tasks = [_load_remaining(idx) for idx in remaining] + [_load_index_prices()]
    await asyncio.gather(*phase2_tasks)

    with db_rwlock.write():
        _rebuild_unified_view()

    print("All indices preloaded")


async def background_startup():
    global STARTUP_TIME, STARTUP_DONE_TIME
    STARTUP_TIME = time.time()
    await preload_all_indices()
    STARTUP_DONE_TIME = time.time()


# --- APP INITIALIZATION ---

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


# --- WEBSOCKET ENDPOINT ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # send cached market data snapshot on connect
        for payload in list(LATEST_MARKET_DATA.values()):
            await websocket.send_text(json.dumps(payload))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# --- REST API: ADMIN ---

@app.get("/health")
async def health():
    """Return detailed loading progress and readiness status."""
    loaded = {k: v for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")}
    total_indices = len(MARKET_INDICES)
    # each index has 5 steps (load, sector series, industry series, stock returns, prewarm) + 1 for index_prices
    total_steps = total_indices * 5 + 1

    done = []
    loading = []
    total_rows = 0
    for idx in MARKET_INDICES:
        status = INDEX_LOAD_STATUS.get(idx, {})
        if status.get("loaded"):
            rc = status.get("row_count", 0)
            done.append(f"stocks for {idx} ({rc:,} rows)")
            total_rows += rc
        elif status.get("loading"):
            loading.append(f"stocks for {idx}")

        sec_status = SECTOR_SERIES_STATUS.get(idx, {})
        if sec_status.get("ready"):
            rc = sec_status.get("row_count", 0)
            done.append(f"sector series for {idx} ({rc:,} rows)")
            total_rows += rc
        elif sec_status.get("computing"):
            loading.append(f"sector series for {idx}")

        ind_status = INDUSTRY_SERIES_STATUS.get(idx, {})
        if ind_status.get("ready"):
            rc = ind_status.get("row_count", 0)
            done.append(f"industry series for {idx} ({rc:,} rows)")
            total_rows += rc
        elif ind_status.get("computing"):
            loading.append(f"industry series for {idx}")

        ret_status = STOCK_RETURNS_STATUS.get(idx, {})
        if ret_status.get("ready"):
            rc = ret_status.get("rows", 0)
            done.append(f"stock returns for {idx} ({rc:,} rows)")
        elif ret_status.get("computing"):
            loading.append(f"stock returns for {idx}")

        pw_status = PREWARM_STATUS.get(idx, {})
        if pw_status.get("ready"):
            pc = pw_status.get("periods", 0)
            done.append(f"cache prewarm for {idx} ({pc} periods)")
        elif pw_status.get("computing"):
            loading.append(f"cache prewarm for {idx}")

    if INDEX_PRICES_LOADED:
        done.append(f"index prices ({INDEX_PRICES_ROW_COUNT:,} rows)")
        total_rows += INDEX_PRICES_ROW_COUNT
    elif any(v.get("loaded") for v in INDEX_LOAD_STATUS.values()):
        loading.append("index prices")

    completed = len(done)
    all_done = completed == total_steps

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

    # process memory (RSS) — works on Linux/Cloud Run via /proc, fallback for other OS
    mem_mb = None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    mem_mb = int(line.split()[1]) / 1024  # kB → MB
                    break
    except Exception:
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # kB → MB on Linux
        except Exception:
            pass

    result = {
        "status": "ready" if all_done else "warming_up",
        "progress": f"{completed}/{total_steps}",
        "indices_loaded": len(loaded),
        "total_rows": fmt_rows(total_rows),
        "memory": f"{mem_mb:.0f} MB" if mem_mb else "n/a",
        "done": done,
    }

    if loading:
        result["loading"] = loading

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
    """Trigger background refresh for all currently loaded indices."""
    loaded = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not loaded:
        return {"status": "skipped", "message": "No indices loaded yet"}
    for idx in loaded:
        asyncio.create_task(refresh_single_index(idx))
        await asyncio.sleep(0.5)
    return {"status": "accepted", "message": f"Refreshing {len(loaded)} loaded indices"}


@app.post("/api/admin/refresh/{index_key}")
async def webhook_refresh_index(index_key: str):
    """Trigger background refresh for a single index or index_prices."""
    if index_key == "index_prices":
        loop = asyncio.get_event_loop()
        asyncio.create_task(loop.run_in_executor(None, _load_index_prices_from_bq))
        return {"status": "accepted", "message": "Refreshing index_prices"}
    if index_key not in MARKET_INDICES:
        return {"status": "error", "message": f"Unknown index: {index_key}"}
    asyncio.create_task(refresh_single_index(index_key))
    return {"status": "accepted", "message": f"Refreshing {index_key}"}


# --- REST API: INDEX OVERVIEW ---

@app.get("/index-prices/debug")
async def get_index_prices_debug():
    if not INDEX_PRICES_LOADED:
        return {"loaded": False}
    try:
        with db_rwlock.read():
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
        with db_rwlock.read():
            res = local_db.execute(sql("index_prices_summary.sql")).df().fillna(0).to_dict(orient="records")
        set_cached_response(cache_key, res)
        return res
    except Exception as e:
        print(f"Index prices summary error: {e}")
        return []


@app.get("/index-prices/data")
async def get_index_prices_data(symbols: str = "", period: str = "1y"):
    """Multi-symbol index price comparison with unified timeline and forward-fill."""
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

        with db_rwlock.read():
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
            times = sym_df["time"].astype(str).values
            closes = sym_df["close"].values
            pcts = sym_df["pct"].values
            vols = sym_df["volume"].fillna(0).astype(int).values
            points = [
                {"time": times[i], "close": float(closes[i]), "pct": float(pcts[i]), "volume": int(vols[i])}
                for i in range(len(times))
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
    """Compute per-index stats: daily change, period return, YTD, 52w range, volatility."""
    if not INDEX_PRICES_LOADED:
        return []

    use_custom = bool(start and end)
    cache_key = f"index_stats_{start}_{end}" if use_custom else f"index_stats_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        with db_rwlock.read():
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

                # period return: find the start price for the requested window
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

                # year-to-date return
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

                # annualized volatility from daily log returns
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
        with db_rwlock.read():
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


# --- REST API: SECTOR COMPARISON ---

@app.get("/sector-comparison/data")
async def get_sector_comparison_data(sector: str = "Technology", indices: str = "", period: str = "max"):
    """Legacy endpoint using simple AVG(close) normalization. Kept for backwards compatibility."""
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

            with db_rwlock.read():
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
        with db_rwlock.read():
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
            with db_rwlock.read():
                df = local_db.execute(
                    sql("sector_industries.sql").replace("{table}", f"prices_{idx}"),
                    [sector]
                ).df()
            if df.empty:
                continue
            for rec in df.to_dict("records"):
                ind = rec["industry"]
                if ind not in all_industries:
                    all_industries[ind] = {}
                all_industries[ind][idx] = int(rec["cnt"])

        result = [{"industry": k, "indices": v, "total": sum(v.values())} for k, v in all_industries.items()]
        result.sort(key=lambda x: x["total"], reverse=True)
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Sector industries error: {e}")
        return []


@app.get("/sector-comparison/all-industries")
async def get_all_sector_industries(indices: str = ""):
    """Return industry breakdown for every sector across given indices in a single call."""
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list:
        return {}

    cache_key = f"all_industries_{','.join(sorted(index_list))}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        result = {}
        for idx in index_list:
            if not ensure_index_loaded(idx):
                continue
            with db_rwlock.read():
                df = local_db.execute(
                    f"SELECT sector, industry, COUNT(DISTINCT symbol) as cnt FROM prices_{idx}"
                    f" WHERE sector IS NOT NULL AND sector NOT IN ('N/A','0','')"
                    f" AND industry IS NOT NULL AND industry NOT IN ('N/A','0','')"
                    f" GROUP BY sector, industry"
                ).df()
            if df.empty:
                continue
            for rec in df.to_dict("records"):
                sec, ind, cnt = rec["sector"], rec["industry"], int(rec["cnt"])
                if sec not in result:
                    result[sec] = {}
                if ind not in result[sec]:
                    result[sec][ind] = {}
                result[sec][ind][idx] = cnt

        # reshape: { sector: [ {industry, indices: {idx: cnt}, total} ] }
        final = {}
        for sec, industries in result.items():
            items = []
            for ind, idx_counts in industries.items():
                items.append({"industry": ind, "indices": idx_counts, "total": sum(idx_counts.values())})
            items.sort(key=lambda x: x["total"], reverse=True)
            final[sec] = items
            # also populate per-sector cache so /industries endpoint is instant too
            for idx_combo_key in [f"sector_industries_{sec}_{','.join(sorted(index_list))}"]:
                set_cached_response(idx_combo_key, items)

        set_cached_response(cache_key, final)
        return final

    except Exception as e:
        print(f"All sector industries error: {e}")
        import traceback; traceback.print_exc()
        return {}


@app.get("/sector-comparison/data-v2")
async def get_sector_comparison_data_v2(
    sector: str = "Technology",
    indices: str = "",
    industries: str = "",
    mode: str = "cross-index",
    period: str = "max",
):
    """Clean sector comparison using per-stock normalization, unified timeline, and forward-fill.
    cross-index mode: one sector across indices. single-index mode: multiple sectors within one index."""
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        return {"series": [], "mode": mode}

    industry_filter = [i.strip() for i in industries.split(",") if i.strip()] if industries else []
    cache_key = f"sector_v2_{mode}_{sector}_{','.join(sorted(index_list))}_{','.join(sorted(industry_filter))}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    # fast path: serve from precomputed tables when no filters applied
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
                    with db_rwlock.read():
                        df = local_db.execute(
                            f"SELECT time, pct FROM sector_series_{idx} WHERE sector = ? ORDER BY time",
                            [sector]
                        ).df()
                    if df.empty or len(df) < 2:
                        continue
                    points = [{"time": str(t), "pct": float(p)} for t, p in zip(df["time"].values, df["pct"].values)]
                    series.append({"symbol": idx, "points": points})

            elif mode == "single-index":
                idx = index_list[0]
                if not SECTOR_SERIES_STATUS.get(idx, {}).get("ready"):
                    all_ready = False
                else:
                    for sec in [s.strip() for s in sector.split(",") if s.strip()]:
                        with db_rwlock.read():
                            df = local_db.execute(
                                f"SELECT time, pct FROM sector_series_{idx} WHERE sector = ? ORDER BY time",
                                [sec]
                            ).df()
                        if df.empty or len(df) < 2:
                            continue
                        points = [{"time": str(t), "pct": float(p)} for t, p in zip(df["time"].values, df["pct"].values)]
                        series.append({"symbol": sec, "points": points})

            if all_ready and series:
                result = {"series": series, "mode": mode}
                set_cached_response(cache_key, result)
                return result
        except Exception:
            pass  # fall through to slow path

    # slow path: compute clean_sector_series on the fly
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
                with db_rwlock.read():
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
                with db_rwlock.read():
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
    """Return all precomputed sector time series for instant frontend switching."""
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

        # serve from pre-built cache (populated at precompute time)
        if idx in ALL_SERIES_CACHE:
            result[idx] = ALL_SERIES_CACHE[idx]
            ready_indices.append(idx)
            continue

        # fallback: query DuckDB (cache miss after eviction/restart)
        series_table = f"sector_series_{idx}"
        try:
            with db_rwlock.read():
                df = local_db.execute(
                    f"SELECT sector, time, pct FROM {series_table} ORDER BY sector, time"
                ).df()

            if df.empty:
                continue

            idx_data = {}
            for sector, group in df.groupby("sector", sort=False):
                idx_data[sector] = [
                    {"time": str(t), "pct": float(p)}
                    for t, p in zip(group["time"].values, group["pct"].values)
                ]

            ALL_SERIES_CACHE[idx] = idx_data
            result[idx] = idx_data
            ready_indices.append(idx)
        except Exception as e:
            print(f"  all-series: error reading {idx}: {e}")
            pending_indices.append(idx)

    return {"data": result, "ready": ready_indices, "pending": pending_indices}


@app.get("/sector-comparison/industry-series")
async def get_industry_series(sector: str = "", indices: str = ""):
    """Return precomputed industry time series for one sector across requested indices."""
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
            with db_rwlock.read():
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
            for industry, group in df.groupby("industry", sort=False):
                idx_data[industry] = [
                    {"time": str(t), "pct": round(float(p), 4), "n": int(n)}
                    for t, p, n in zip(group["time"].values, group["pct"].values, group["stock_count"].values)
                ]

            result[idx] = idx_data
            ready_indices.append(idx)
        except Exception as e:
            print(f"  industry-series: error reading {idx}/{sector}: {e}")
            pending_indices.append(idx)

    return {"data": result, "ready": ready_indices, "pending": pending_indices}


@app.get("/sector-comparison/histogram")
async def get_sector_histogram(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    """Return average return per sector across indices for histogram display."""
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
            with db_rwlock.read():
                df = _sector_returns_df(f"prices_{idx}", use_custom, period, start, end)
            if df.empty:
                continue
            for rec in df.to_dict("records"):
                sector_returns.setdefault(rec["sector"], []).append(float(rec["return_pct"]))

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
    """Return sector returns broken down by index for heatmap/rankings table."""
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
            with db_rwlock.read():
                df = _sector_returns_df(f"prices_{idx}", use_custom, period, start, end)
            if df.empty:
                continue
            for rec in df.to_dict("records"):
                all_data.setdefault(rec["sector"], {})[idx] = {
                    "return_pct": round(float(rec["return_pct"]), 2),
                    "stock_count": int(rec["stock_count"]),
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
    """Return top N and bottom N performing stocks within a sector across indices."""
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

            with db_rwlock.read():
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
            for rec in df.to_dict("records"):
                all_rows.append({
                    "symbol": rec["symbol"],
                    "name": rec["name"] if rec["name"] and rec["name"] != "0" else "",
                    "industry": rec.get("industry", "") or "",
                    "return_pct": round(float(rec["return_pct"]), 2),
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


@app.get("/sector-comparison/all-top-stocks")
async def get_all_top_stocks(period: str = "1y"):
    """Return precomputed stock returns for all indices, all sectors, for a given period."""
    data = {}
    ready = []
    pending = []

    for idx in MARKET_INDICES:
        status = STOCK_RETURNS_STATUS.get(idx, {})
        if not status.get("ready"):
            pending.append(idx)
            continue

        result_table = f"stock_returns_{idx}"
        try:
            with db_rwlock.read():
                # verify table exists before querying
                tables = [r[0] for r in local_db.execute("SHOW TABLES").fetchall()]
                if result_table not in tables:
                    pending.append(idx)
                    continue
                df = local_db.execute(
                    f"SELECT symbol, name, industry, sector, return_pct FROM {result_table} WHERE period = ?",
                    [period.lower()]
                ).df()

            rows = []
            for rec in df.to_dict("records"):
                rows.append({
                    "symbol": rec["symbol"],
                    "name": rec["name"] if rec["name"] and str(rec["name"]) != "0" else "",
                    "industry": rec.get("industry", "") or "",
                    "sector": rec["sector"],
                    "return_pct": round(float(rec["return_pct"]), 2),
                })
            data[idx] = rows
            ready.append(idx)
        except Exception as e:
            print(f"Error reading stock returns for {idx}: {e}")
            pending.append(idx)

    return {"data": data, "ready": ready, "pending": pending}


@app.get("/sector-comparison/industry-breakdown")
async def get_industry_breakdown(
    index: str = "", sector: str = "",
    period: str = "1y", start: str = "", end: str = "",
):
    """Return per-industry return breakdown within one sector of one index."""
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
        with db_rwlock.read():
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
                "industry": rec["industry"],
                "return_pct": round(float(rec["return_pct"]), 2),
                "stock_count": int(rec["stock_count"]),
            }
            for rec in df.to_dict("records")
        ]
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Industry breakdown error: {e}")
        import traceback; traceback.print_exc()
        return []


@app.get("/sector-comparison/industry-turnover")
async def get_industry_turnover(
    index: str = "", sector: str = "",
    period: str = "1y", start: str = "", end: str = "",
):
    """Return total turnover (close * volume) per industry within one sector of one index."""
    if not index or index not in MARKET_INDICES or not sector:
        return []

    use_custom = bool(start and end)
    cache_key = (
        f"industry_turnover_{index}_{sector}_{start}_{end}"
        if use_custom else
        f"industry_turnover_{index}_{sector}_{period}"
    )
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    if not ensure_index_loaded(index):
        return []

    table = f"prices_{index}"

    try:
        with db_rwlock.read():
            if use_custom:
                df = local_db.execute(
                    sql("industry_turnover_custom.sql")
                    .replace("{table}", table)
                    .replace("{start}", start)
                    .replace("{end}", end),
                    [sector]
                ).df()
            elif period.lower() == "max":
                df = local_db.execute(
                    sql("industry_turnover_max.sql").replace("{table}", table),
                    [sector]
                ).df()
            else:
                days = INTERVALS.get(period.lower(), 365)
                df = local_db.execute(
                    sql("industry_turnover_period.sql")
                    .replace("{table}", table)
                    .replace("{days}", str(days)),
                    [sector]
                ).df()

        if df.empty:
            return []

        result = [
            {
                "industry": rec["industry"],
                "turnover": float(rec["turnover"]),
                "stock_count": int(rec["stock_count"]),
            }
            for rec in df.to_dict("records")
        ]
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        print(f"Industry turnover error: {e}")
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

        with db_rwlock.read():
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

        with db_rwlock.read():
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


# --- REST API: STOCK DATA ---

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
        with db_rwlock.read():
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


# symbol-to-index lookup cache and suffix heuristics for lazy loading
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
    """Infer index from ticker suffix (e.g. .T -> nikkei225), default to sp500."""
    upper = symbol.upper()
    for suffix, index_key in SUFFIX_TO_INDEX.items():
        if upper.endswith(suffix.upper()):
            return index_key
    return "sp500" if "." not in symbol else None


def _find_symbol_table(symbol):
    """Search loaded indices for a symbol, lazy-loading the guessed index if needed."""
    if symbol in SYMBOL_INDEX_MAP:
        idx = SYMBOL_INDEX_MAP[symbol]
        if INDEX_LOAD_STATUS.get(idx, {}).get("loaded"):
            return f"prices_{idx}"

    # scan all loaded indices
    for index_key, status in INDEX_LOAD_STATUS.items():
        if not status.get("loaded"):
            continue
        try:
            with db_rwlock.read():
                count = local_db.execute(
                    f"SELECT COUNT(*) FROM prices_{index_key} WHERE symbol = ?", [symbol]
                ).fetchone()[0]
            if count > 0:
                SYMBOL_INDEX_MAP[symbol] = index_key
                return f"prices_{index_key}"
        except:
            continue

    # trigger lazy load for the guessed index
    guessed_index = _guess_index_for_symbol(symbol)
    if guessed_index and guessed_index in MARKET_INDICES:
        if not INDEX_LOAD_STATUS.get(guessed_index, {}).get("loaded"):
            print(f"  Lazy-loading {guessed_index} for symbol {symbol}")
            if ensure_index_loaded(guessed_index):
                try:
                    with db_rwlock.read():
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
        with db_rwlock.read():
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
        with db_rwlock.read():
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
        with db_rwlock.read():
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
        with db_rwlock.read():
            res = local_db.execute(
                f"SELECT name FROM {table} WHERE symbol = ? LIMIT 1", [symbol]
            ).fetchone()
        return {"symbol": symbol, "name": res[0] if res else symbol}
    except:
        return {"symbol": symbol, "name": symbol}


if __name__ == "__main__":
    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
