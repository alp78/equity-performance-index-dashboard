# =============================================================================
#  Global Market Dashboard — FastAPI Backend
# =============================================================================
#  Single-process API server that powers a multi-index stock market dashboard.
#
#  Data pipeline:
#    BigQuery (source of truth)
#      → DuckDB in-memory (fast OLAP queries, precomputed tables)
#      → REST JSON responses  (SWR-cached, per-endpoint TTLs)
#      → WebSocket broadcasts (live BTC + macro instrument prices)
#
#  Architecture highlights:
#    • _RWLock serialises DuckDB reads/writes; a single-thread executor
#      offloads blocking reads from the async event loop.
#    • Two-tier cache: in-process dict (API_CACHE) with LRU eviction +
#      per-endpoint TTL overrides.  Singleflight prevents cache stampedes.
#    • Circuit breakers protect Binance, Finnhub, FRED, and Frankfurter
#      calls from cascading failures.
#    • Two-phase startup: priority indices (stoxx50, sp500) load first,
#      then remaining indices + index_prices + sector precomputes.
#
#  Key sections (search for ═══ dividers):
#    1. Configuration          — index/table mappings, env vars
#    2. SQL Loader & Utilities — LRU-cached .sql file reader
#    3. External API Clients   — persistent httpx pools + circuit breakers
#    4. DuckDB Engine          — in-memory DB, RWLock, thread pool
#    5. API Cache Layer        — TTL, SWR, singleflight, BQ semaphore
#    6. WebSocket Manager      — async set-based connection tracking
#    7. Data Ingestion         — BQ load, precompute, prewarm, refresh
#    8. Query Helpers          — reusable SQL dispatchers
#    9. Real-Time Feeds        — Binance BTC, yfinance macro instruments
#   10. Startup Orchestration  — two-phase preload, news prefetch
#   11. App Initialisation     — FastAPI lifespan, CORS, middleware
#   12. REST API Endpoints     — /summary, /data, /rankings, /sector-*,
#                                 /index-prices/*, /news, /macro/*,
#                                 /correlation, /technicals, /health
#   13. Server Entry Point     — uvicorn launcher
# =============================================================================

from os import getenv
from pathlib import Path
import pandas as pd
import duckdb
import asyncio
import httpx
import json
import math
import uvicorn
import yfinance as yf
import time
import threading
from datetime import datetime, timezone, timedelta
import os
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from google.cloud import bigquery
from urllib.parse import unquote
from contextlib import asynccontextmanager, contextmanager
from dotenv import load_dotenv
import logging
import re as _re

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("exchange-api")

load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
#  1. CONFIGURATION
#     Loaded from config/indices.json via index_config.py.
# ═══════════════════════════════════════════════════════════════════════════════

from index_config import (
    MARKET_INDICES,
    INDEX_KEY_TO_TICKER,
    INDEX_TICKER_TO_KEY,
    SUFFIX_TO_INDEX as _CFG_SUFFIX_TO_INDEX,
    NEWS_INDEX_TICKERS as _CFG_NEWS_INDEX_TICKERS,
    INDEX_FLAG_MAP as _CFG_INDEX_FLAG_MAP,
    KEYWORD_INDEX_MAP as _CFG_KEYWORD_INDEX_MAP,
    PHASE1_INDICES,
    CORRELATION_ORDER,
    ALL_LEADER_SYMBOLS,
    LEADER_DISPLAY_MAP,
    INDEX_CONFIG_PUBLIC,
    PROJECT_ID,
)

FINNHUB_API_KEY = getenv("FINNHUB_API_KEY")
FRED_API_KEY = getenv("FRED_API_KEY")

DISPLAY_SYMBOL_MAP = {
    "GC=F":      "FXCM:XAU/USD",
    "EURUSD=X":  "FXCM:EUR/USD",
    "^MOVE":     "MOVE",
    "KRBN":      "EU CARBON",
}

INDEX_PRICES_TABLE = f"{PROJECT_ID}.stock_exchange.index_prices" if PROJECT_ID else None
INDEX_PRICES_LOADED = False


# ═══════════════════════════════════════════════════════════════════════════════
#  2. SQL LOADER & UTILITIES
#     LRU-cached reader for .sql template files; date formatting helper.
# ═══════════════════════════════════════════════════════════════════════════════

_SQL_DIR = Path(__file__).parent / "sql"
from functools import lru_cache


@lru_cache(maxsize=50)
def sql(filename: str) -> str:
    """Load and cache a SQL template from the sql/ directory (bounded LRU)."""
    return (_SQL_DIR / filename).read_text()


def _ts(v) -> str:
    """Format a date/timestamp value as YYYY-MM-DD string for chart serialization."""
    return str(v)[:10]


_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")

def _valid_date(d: str) -> bool:
    """Return True if d matches YYYY-MM-DD format."""
    return bool(d and _DATE_RE.fullmatch(d))


# ═══════════════════════════════════════════════════════════════════════════════
#  3. EXTERNAL API CLIENTS
#     Persistent httpx connection pools (Binance, Finnhub, FRED, Frankfurter)
#     with per-provider circuit breakers to fail fast on outages.
# ═══════════════════════════════════════════════════════════════════════════════

_http_binance = httpx.AsyncClient(
    base_url="https://api.binance.com",
    timeout=httpx.Timeout(5.0, connect=2.0),
    limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
)
_http_finnhub = httpx.AsyncClient(
    base_url="https://finnhub.io",
    timeout=httpx.Timeout(10.0, connect=3.0),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)
_http_fred = httpx.AsyncClient(
    base_url="https://api.stlouisfed.org",
    timeout=httpx.Timeout(12.0, connect=3.0),
    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
)
_http_frankfurter = httpx.AsyncClient(
    base_url="https://api.frankfurter.dev",
    timeout=httpx.Timeout(10.0, connect=3.0),
    limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
)


class _CircuitBreaker:
    """Fail fast when an external API is consistently failing."""
    def __init__(self, failure_threshold=3, recovery_timeout=60):
        self.failures = 0
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = 0.0

    @property
    def is_open(self):
        if self.failures >= self.threshold:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.failures = 0
                return False
            return True
        return False

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()

    def record_success(self):
        self.failures = 0


_cb_binance = _CircuitBreaker()
_cb_finnhub = _CircuitBreaker()
_cb_fred = _CircuitBreaker()
_cb_frankfurter = _CircuitBreaker()


# ═══════════════════════════════════════════════════════════════════════════════
#  4. DUCKDB ENGINE
#     In-memory analytical database, read-write lock for concurrent access,
#     single-thread executor to offload blocking reads from the async loop.
# ═══════════════════════════════════════════════════════════════════════════════

local_db = duckdb.connect(database=':memory:', read_only=False)
# Limit DuckDB internal threads to avoid excessive CPU on Cloud Run.
local_db.execute("SET threads = 2")


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

# Thread pool for offloading blocking DuckDB reads from the async event loop.
# max_workers=1 because DuckDB's Python API is not thread-safe for concurrent
# access on the same connection — serializing on a background thread still frees
# the event loop for WebSocket broadcasts and other async work.
# Note: db_rwlock serializes reads/writes, but DuckDB cursors on a single
# connection are still not safe for concurrent access, so keep workers=1.
from concurrent.futures import ThreadPoolExecutor as _TPE
_db_executor = _TPE(max_workers=1, thread_name_prefix="duckdb")


async def db_read(fn):
    """Run a blocking DuckDB read in the thread pool, freeing the event loop."""
    return await asyncio.get_event_loop().run_in_executor(_db_executor, fn)


INDEX_LOAD_STATUS: dict = {}
SECTOR_SERIES_STATUS: dict = {}
INDUSTRY_SERIES_STATUS: dict = {}
STOCK_RETURNS_STATUS: dict = {}
PREWARM_STATUS: dict = {}
INDEX_PRICES_ROW_COUNT: int = 0
LATEST_MARKET_DATA: dict = {}
_last_eu_vol: float | None = None
STARTUP_TIME: float = 0.0
STARTUP_DONE_TIME: float = 0.0
NEWS_PRELOAD_STATUS: dict = {}  # {"loading": bool, "ready": bool, "count": int}

# ═══════════════════════════════════════════════════════════════════════════════
#  5. API CACHE LAYER
#     In-process dict with per-endpoint TTLs, LRU eviction, stale-while-
#     revalidate support, and singleflight to prevent cache stampedes.
# ═══════════════════════════════════════════════════════════════════════════════

API_CACHE: dict = {}
_cache_lock = threading.Lock()
API_CACHE_MAX = 500          # LRU cap to prevent unbounded memory growth
ALL_SERIES_CACHE: dict = {}
ALL_SERIES_CACHE_MAX_MB = 500  # soft cap for series cache memory
CACHE_TTL = 1800             # default TTL (30 min)

# ─── Per-endpoint TTL overrides (seconds) ───
CACHE_TTLS = {
    "news":        300,   # 5 min — articles go stale quickly
    "macro_fx":    300,   # 5 min — FX rates are volatile
    "macro_cal":   900,   # 15 min — economic calendar
    "macro_rates": 3600,  # 1 hour — bonds/commodities stable
}

# ─── Cache hit/miss metrics ───
_CACHE_STATS = {"hits": 0, "misses": 0, "stale_hits": 0, "evictions": 0}


def _effective_ttl(cache_key):
    """Return the TTL for a cache key based on prefix match."""
    for prefix, ttl in CACHE_TTLS.items():
        if cache_key.startswith(prefix):
            return ttl
    return CACHE_TTL


def get_cached_response(cache_key):
    """Return cached data if fresh. Stale data returned by get_stale_response()."""
    with _cache_lock:
        if cache_key in API_CACHE:
            data, timestamp = API_CACHE[cache_key]
            if time.time() - timestamp < _effective_ttl(cache_key):
                _CACHE_STATS["hits"] += 1
                return data
    _CACHE_STATS["misses"] += 1
    return None


def get_stale_response(cache_key):
    """Return stale cached data (expired but still in cache) for SWR pattern."""
    with _cache_lock:
        if cache_key in API_CACHE:
            data, _ = API_CACHE[cache_key]
            _CACHE_STATS["stale_hits"] += 1
            return data
    return None


def set_cached_response(cache_key, data):
    with _cache_lock:
        API_CACHE[cache_key] = (data, time.time())
        # LRU eviction: if cache exceeds max, remove oldest entries
        if len(API_CACHE) > API_CACHE_MAX:
            sorted_keys = sorted(API_CACHE, key=lambda k: API_CACHE[k][1])
            evict_count = len(sorted_keys) // 5
            for k in sorted_keys[:evict_count]:
                del API_CACHE[k]
            _CACHE_STATS["evictions"] += evict_count


def invalidate_index_cache(index_key):
    """Invalidate all caches for an index, including series cache."""
    with _cache_lock:
        keys_to_remove = [k for k in API_CACHE if f"{index_key}_" in k or k.startswith(f"sector_{index_key}")]
        for k in keys_to_remove:
            del API_CACHE[k]
    ALL_SERIES_CACHE.pop(index_key, None)


# ─── Singleflight: prevent cache stampede ───
# If N requests for the same cache key arrive while one is computing,
# only the first one computes; the rest await the same result.
_singleflight_locks: dict[str, asyncio.Lock] = {}
_singleflight_meta_lock = asyncio.Lock()


async def singleflight(cache_key: str, compute_fn):
    """Run compute_fn once per cache_key; concurrent callers share the result."""
    # Fast path: already cached
    cached = get_cached_response(cache_key)
    if cached is not None:
        return cached

    # Get or create a per-key lock
    async with _singleflight_meta_lock:
        if cache_key not in _singleflight_locks:
            _singleflight_locks[cache_key] = asyncio.Lock()
        key_lock = _singleflight_locks[cache_key]

    async with key_lock:
        # Re-check cache (another caller may have populated it while we waited)
        cached = get_cached_response(cache_key)
        if cached is not None:
            return cached
        result = await compute_fn()
        set_cached_response(cache_key, result)
        return result


# ─── BQ concurrency semaphore ───
# Limits concurrent BigQuery API calls to avoid overwhelming the API quota.
_bq_semaphore = asyncio.Semaphore(3)


# ─── Sector series SQL builder ───

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


# ═══════════════════════════════════════════════════════════════════════════════
#  6. WEBSOCKET CONNECTION MANAGER
#     Async-safe set of active WebSocket connections with snapshot-before-
#     broadcast pattern to avoid holding the lock during I/O.
# ═══════════════════════════════════════════════════════════════════════════════

class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)

    @property
    def active_connections(self):
        """Backward-compat: return snapshot list for read-only checks."""
        return list(self._connections)

    async def broadcast(self, message: str):
        async with self._lock:
            if not self._connections:
                return
            snapshot = list(self._connections)
        # Send to all clients concurrently; remove any that fail
        results = await asyncio.gather(
            *[conn.send_text(message) for conn in snapshot],
            return_exceptions=True
        )
        dead = [snapshot[i] for i, r in enumerate(results) if isinstance(r, Exception)]
        if dead:
            async with self._lock:
                for conn in dead:
                    self._connections.discard(conn)


manager = ConnectionManager()


# ═══════════════════════════════════════════════════════════════════════════════
#  7. DATA INGESTION PIPELINE
#     BigQuery → DuckDB loading, per-index precomputation (sector series,
#     industry series, stock returns), cache prewarm, and index refresh logic.
# ═══════════════════════════════════════════════════════════════════════════════

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
        logger.info(f"[{index_key}] BQ fetch: {t_bq - t0:.1f}s ({len(df)} raw rows)")

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
        logger.info(f"[{index_key}] DuckDB: {t_done - t_bq:.1f}s. Total: {t_done - t0:.1f}s ({row_count} rows)")
        return row_count

    except Exception as e:
        logger.error(f"[{index_key}] Load error: {e}")
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
                        {"time": _ts(t), "pct": float(p)}
                        for t, p in zip(group["time"].values, group["pct"].values)
                    ]
                ALL_SERIES_CACHE[index_key] = idx_data

        SECTOR_SERIES_STATUS[index_key] = {"ready": True, "computing": False, "row_count": row_count}
        logger.info(f"[{index_key}] Sector series precomputed: {sector_count} sectors, "
                    f"{row_count} rows in {time.time() - t0:.1f}s")

    except Exception as e:
        SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
        logger.error(f"[{index_key}] Sector series precompute error: {e}")


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
        logger.info(f"[{index_key}] Industry series precomputed: {industry_count} industries, "
                    f"{row_count} rows in {time.time() - t0:.1f}s")

    except Exception as e:
        INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
        logger.error(f"[{index_key}] Industry series precompute error: {e}")


def _precompute_stock_returns(index_key):
    """Precompute per-stock returns for all sectors × standard periods into stock_returns_{index}."""
    table = f"prices_{index_key}"
    result_table = f"stock_returns_{index_key}"
    STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": True}
    t0 = time.time()

    PERIODS = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}

    try:
        all_dfs = []
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
            total_rows = sum(len(d) for d in all_dfs)
            STOCK_RETURNS_STATUS[index_key] = {"ready": True, "computing": False, "rows": total_rows}
            logger.info(f"[{index_key}] Stock returns precomputed: {total_rows} rows in {time.time() - t0:.1f}s")
        else:
            STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": False, "rows": 0}
            logger.info(f"[{index_key}] Stock returns: no data returned from prices table")

    except Exception as e:
        STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": False}
        logger.error(f"[{index_key}] Stock returns precompute error: {e}")


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
        logger.info(f"[{index_key}] Sector caches pre-warmed ({periods_warmed} periods) in {time.time() - t0:.1f}s")
    except Exception as e:
        PREWARM_STATUS[index_key] = {"ready": False, "computing": False}
        logger.error(f"[{index_key}] Sector cache pre-warm error: {e}")


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
        logger.info(f"[index_prices] BQ fetch: {t_bq - t0:.1f}s ({len(df)} raw rows)")

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
        logger.info(f"[index_prices] DuckDB: {t_done - t_bq:.1f}s. Total: {t_done - t0:.1f}s ({row_count} rows)")
        for sym, max_d, min_d, cnt in max_dates:
            logger.info(f"  {sym}: {min_d} -> {max_d} ({cnt} rows)")
        INDEX_PRICES_LOADED = True
        global INDEX_PRICES_ROW_COUNT
        INDEX_PRICES_ROW_COUNT = row_count
        return row_count

    except Exception as e:
        logger.error(f"[index_prices] Load error: {e}")
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
    logger.info(f"Refreshing index: {index_key}")
    invalidate_index_cache(index_key)
    ALL_SERIES_CACHE.pop(index_key, None)
    SECTOR_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
    INDUSTRY_SERIES_STATUS[index_key] = {"ready": False, "computing": False}
    STOCK_RETURNS_STATUS[index_key] = {"ready": False, "computing": False}
    PREWARM_STATUS[index_key] = {"ready": False, "computing": False}
    INDEX_LOAD_STATUS[index_key] = {"loaded": False, "loading": True, "row_count": 0}
    loop = asyncio.get_event_loop()
    async with _bq_semaphore:
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


# ═══════════════════════════════════════════════════════════════════════════════
#  8. QUERY HELPERS
#     Reusable SQL dispatchers that pick period/max/custom SQL variants
#     and execute them against the correct per-index DuckDB table.
# ═══════════════════════════════════════════════════════════════════════════════

INTERVALS = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}


def _sector_returns_df(table, use_custom, period, start, end, industries=None):
    """Execute the appropriate sector returns SQL variant and return a DataFrame."""
    extra = ""
    if industries:
        escaped = ",".join(f"'{i.replace(chr(39), chr(39)+chr(39))}'" for i in industries)
        extra = f"\n      AND industry IN ({escaped})"

    if use_custom:
        sql_text = (sql("sector_returns_custom.sql")
            .replace("{table}", table)
            .replace("{start}", start)
            .replace("{end}", end))
    elif period.lower() == "max":
        sql_text = sql("sector_returns_max.sql").replace("{table}", table)
    else:
        days = INTERVALS.get(period.lower(), 365)
        sql_text = (sql("sector_returns_period.sql")
            .replace("{table}", table)
            .replace("{days}", str(days)))

    if extra:
        sql_text = sql_text.replace(
            "AND sector NOT IN ('N/A', '0', '')",
            f"AND sector NOT IN ('N/A', '0', ''){extra}")
    return local_db.execute(sql_text).df()


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


# ═══════════════════════════════════════════════════════════════════════════════
#  9. REAL-TIME MARKET FEEDS
#     Binance BTC/USDT (10 s poll) and yfinance macro instruments (30 s poll).
#     Results are stored in LATEST_MARKET_DATA and broadcast via WebSocket.
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_crypto_data():
    """Fetch BTC/USDT price from Binance and broadcast via WebSocket."""
    if _cb_binance.is_open:
        return
    try:
        # Concurrent fetch: ticker + kline in parallel via persistent connection pool
        ticker_r, kline_r = await asyncio.gather(
            _http_binance.get("/api/v3/ticker/price", params={"symbol": "BTCUSDT"}),
            _http_binance.get("/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "1d", "limit": 1}),
        )
        if ticker_r.status_code != 200:
            _cb_binance.record_failure()
            return
        _cb_binance.record_success()
        current_price = float(ticker_r.json()["price"])

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
            await manager.broadcast(json.dumps(_sanitize_floats(payload)))
    except Exception as e:
        logger.debug("Suppressed: %s", e)
        _cb_binance.record_failure()


async def fetch_stock_data():
    """Fetch live prices for global macro instruments and index leader stocks via yfinance."""
    DISPLAY_MAP = {**DISPLAY_SYMBOL_MAP, **LEADER_DISPLAY_MAP}
    _MACRO_INSTRUMENTS = ["GC=F", "EURUSD=X", "^MOVE", "KRBN"]
    ALL_SYMBOLS = _MACRO_INSTRUMENTS + ALL_LEADER_SYMBOLS
    loop = asyncio.get_event_loop()

    try:
        data = await loop.run_in_executor(None, lambda: yf.download(
            ALL_SYMBOLS, period="5d", interval="1d",
            progress=False, group_by="ticker", threads=True
        ))
        if data is None or data.empty:
            logger.info("fetch_stock_data: no data returned")
            return

        is_multi = isinstance(data.columns, pd.MultiIndex)
        payloads = []
        new_data = {}

        for symbol in ALL_SYMBOLS:
            try:
                df = data[symbol] if is_multi else data
                df = df.dropna(subset=["Close"])
                if len(df) < 1:
                    continue

                current = float(df["Close"].iloc[-1])
                if pd.isna(current) or current == 0:
                    continue

                prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else None
                if prev is not None and (pd.isna(prev) or prev == 0):
                    prev = None

                diff = (current - prev) if prev else 0
                pct = ((current - prev) / prev) * 100 if prev else 0
                display_symbol = DISPLAY_MAP.get(symbol, symbol)
                is_fx = symbol == "EURUSD=X"

                payload = {
                    "symbol": display_symbol,
                    "price": round(current, 6 if is_fx else 2),
                    "diff":  round(diff,    6 if is_fx else 2),
                    "pct":   round(pct,     4 if is_fx else 2),
                }
                new_data[display_symbol] = payload
                payloads.append(_sanitize_floats(payload))
            except Exception as e:
                logger.info(f"feed skip {symbol}: {e}")
                continue

        # Atomic merge: copy-on-write to avoid partial state visible to readers
        if new_data:
            merged = {**LATEST_MARKET_DATA, **new_data}
            LATEST_MARKET_DATA.update(merged)

        logger.info(f"Stock feed: {len(payloads)}/{len(ALL_SYMBOLS)} symbols OK")
        if payloads and manager.active_connections:
            await manager.broadcast(json.dumps(payloads))
    except Exception as e:
        logger.error(f"fetch_stock_data error: {e}")


# ─── Background keep-alive & feed scheduler ───

async def self_keepalive():
    """Ping localhost every 4 minutes to prevent Cloud Run container recycling."""
    await asyncio.sleep(60)
    port = int(getenv("PORT", "8080"))
    logger.info(f"SELF_KEEPALIVE: Pinging localhost:{port}/health every 4 min")
    while True:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            await reader.read(512)
            writer.close()
        except Exception as e:
            logger.debug("Suppressed: %s", e)
        await asyncio.sleep(4 * 60)


async def market_data_feeder():
    """Poll crypto every 10s, stocks every 30s (every 3rd cycle)."""
    logger.info("Real-time Feeder Active")
    # Fetch both immediately on startup so clients get data right away
    await asyncio.gather(fetch_crypto_data(), fetch_stock_data())
    logger.info("Initial market data fetched")
    crypto_counter = 0
    while True:
        await fetch_crypto_data()
        crypto_counter += 1
        if crypto_counter >= 3:
            await fetch_stock_data()
            crypto_counter = 0
        await asyncio.sleep(10)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. STARTUP ORCHESTRATION
#     Two-phase preload: priority indices first (stoxx50 + sp500), then
#     remaining indices + index_prices in parallel.  Kicks off market feeder,
#     keep-alive, and news prefetch once the critical path completes.
# ═══════════════════════════════════════════════════════════════════════════════

async def preload_all_indices():
    """Two-phase startup: priority indices first, then remaining indices + index prices."""
    loop = asyncio.get_event_loop()

    # phase 1: priority indices in parallel (from config)
    phase1 = PHASE1_INDICES
    phase1_tasks = [refresh_single_index(idx) for idx in phase1]
    results = await asyncio.gather(*phase1_tasks, return_exceptions=True)
    for idx, res in zip(phase1, results):
        if isinstance(res, Exception):
            logger.error(f"Phase 1 error loading {idx}: {res}")
    logger.info(f"Phase 1 preload complete ({', '.join(phase1)})")

    asyncio.create_task(market_data_feeder())
    asyncio.create_task(self_keepalive())

    # phase 2: remaining indices + index_prices in parallel
    _phase1_set = set(phase1)
    remaining = [k for k in MARKET_INDICES if k not in _phase1_set]

    async def _load_remaining(idx):
        try:
            await refresh_single_index(idx)
            logger.info(f"Background preload: {idx} ready")
        except Exception as e:
            logger.error(f"Background preload error {idx}: {e}")

    async def _load_index_prices():
        try:
            row_count = await loop.run_in_executor(None, _load_index_prices_from_bq)
            logger.info(f"Index prices loaded: {row_count} rows")
        except Exception as e:
            logger.error(f"Index prices load error: {e}")

    phase2_tasks = [_load_remaining(idx) for idx in remaining] + [_load_index_prices()]
    await asyncio.gather(*phase2_tasks)

    with db_rwlock.write():
        _rebuild_unified_view()

    logger.info("All indices preloaded")


async def background_startup():
    global STARTUP_TIME, STARTUP_DONE_TIME
    STARTUP_TIME = time.time()
    await preload_all_indices()
    STARTUP_DONE_TIME = time.time()
    # Preload news feed in background so it's cached before first request
    asyncio.create_task(_preload_news())


async def _preload_news():
    NEWS_PRELOAD_STATUS["loading"] = True
    try:
        result = await get_news()
        count = len(result) if result else 0
        NEWS_PRELOAD_STATUS["ready"] = True
        NEWS_PRELOAD_STATUS["count"] = count
        logger.info(f"News feed preloaded ({count} articles)")
    except Exception as e:
        logger.error(f"News preload error: {e}")
    NEWS_PRELOAD_STATUS["loading"] = False


# ═══════════════════════════════════════════════════════════════════════════════
# 11. APP INITIALISATION
#     FastAPI lifespan (startup triggers background_startup, shutdown closes DB),
#     CORS configuration, and HTTP Cache-Control middleware.
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(background_startup())
    yield
    local_db.close()


app = FastAPI(lifespan=lifespan)
_ALLOWED_ORIGINS = [
    "https://esg-analytics-poc.web.app",
    "https://esg-analytics-poc.firebaseapp.com",
    "http://localhost:5173",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# ─── HTTP Cache-Control middleware ───
_CACHE_CONTROL_MAP = {
    "/macro/rates":    "public, max-age=300, stale-while-revalidate=600",
    "/macro/fx":       "public, max-age=60, stale-while-revalidate=300",
    "/macro/calendar": "public, max-age=300, stale-while-revalidate=900",
    "/news":           "public, max-age=60, stale-while-revalidate=300",
    "/health":         "no-cache",
    "/metrics/cache":  "no-cache",
}
_CACHE_CONTROL_DEFAULT = "public, max-age=120, stale-while-revalidate=300"


class _CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/ws"):
            return response
        cc = _CACHE_CONTROL_MAP.get(path, _CACHE_CONTROL_DEFAULT)
        response.headers.setdefault("Cache-Control", cc)
        return response


app.add_middleware(_CacheControlMiddleware)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. REST API & WEBSOCKET ENDPOINTS
#     All HTTP + WS endpoints served by this backend.  Grouped by domain:
#       • WebSocket   — /ws (live price stream)
#       • Admin       — /health, /market-data, /api/admin/refresh
#       • Index       — /index-prices/* (overview, stats, single, debug)
#       • Sectors     — /sector-comparison/* (heatmap, table, series, stocks)
#       • Stocks      — /summary, /data/{symbol}, /rankings, /metadata
#       • News        — /news, /news/latest
#       • Macro       — /macro/rates, /macro/fx, /macro/calendar, /macro/risk
#       • Technicals  — /technicals/{symbol}
#       • Diagnostics — /correlation, /metrics/cache
# ═══════════════════════════════════════════════════════════════════════════════

# ─── WebSocket: live price stream ───

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # send cached market data snapshot as a single batch on connect
        cached = list(LATEST_MARKET_DATA.values())
        if cached:
            await websocket.send_text(json.dumps([_sanitize_floats(p) for p in cached]))

        # keepalive: send ping every 30s to prevent proxy/LB timeouts
        async def _ping_loop():
            try:
                while True:
                    await asyncio.sleep(30)
                    await websocket.send_text('{"type":"ping"}')
            except Exception as e:
                logger.debug("Suppressed: %s", e)

        ping_task = asyncio.create_task(_ping_loop())
        try:
            while True:
                await websocket.receive_text()
        finally:
            ping_task.cancel()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)


# ─── Admin & diagnostics ───

@app.get("/health")
async def health():
    """Return detailed loading progress and readiness status."""
    loaded = {k: v for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")}
    total_indices = len(MARKET_INDICES)
    # each index has 5 steps (load, sector series, industry series, stock returns, prewarm) + 1 for index_prices + 1 for news
    total_steps = total_indices * 5 + 2

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

    if NEWS_PRELOAD_STATUS.get("ready"):
        nc = NEWS_PRELOAD_STATUS.get("count", 0)
        done.append(f"news feed ({nc:,} articles)")
    elif NEWS_PRELOAD_STATUS.get("loading"):
        loading.append("news feed")

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
    except Exception as e:
        logger.debug("Suppressed: %s", e)
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # kB → MB on Linux
        except Exception as e:
            logger.debug("Suppressed: %s", e)

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


def _sanitize_floats(obj):
    """Replace inf/nan with None so json.dumps doesn't choke."""
    if isinstance(obj, float):
        if math.isfinite(obj):
            return obj
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _ffill_outliers(df, threshold=0.5):
    """Replace outlier OHLC rows with forward-filled values.

    Detects two kinds of outliers:
      1. Row-level: close deviates >threshold from previous close → ffill entire row
      2. Bar-level: any of open/high/low deviates >threshold from same-row close
         → clamp that column to close (e.g. high spike while close is normal)
    """
    if df.empty or "close" not in df.columns or len(df) < 2:
        return df
    price_cols = [c for c in ["open", "close", "high", "low"] if c in df.columns]
    fill_cols = price_cols + [c for c in ["volume", "ma30", "ma90"] if c in df.columns]

    # 1) Row-level: close vs previous close
    prev_close = df["close"].shift(1)
    pct_change = ((df["close"] - prev_close) / prev_close).abs()
    row_outlier = pct_change > threshold
    row_outlier.iloc[0] = False
    if row_outlier.any():
        df = df.copy()
        df.loc[row_outlier, fill_cols] = float("nan")
        df[fill_cols] = df[fill_cols].ffill()

    # 2) Bar-level: open/high/low vs close on same row
    copied = False
    for col in ["open", "high", "low"]:
        if col not in df.columns:
            continue
        deviation = ((df[col] - df["close"]) / df["close"]).abs()
        bad = deviation > threshold
        bad.iloc[0] = False
        if bad.any():
            if not copied:
                df = df.copy()
                copied = True
            df.loc[bad, col] = df.loc[bad, "close"]

    return df


@app.get("/market-data")
async def get_market_data():
    return _sanitize_floats(LATEST_MARKET_DATA)


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


# ─── Index overview: summary table, multi-index comparison, per-index chart ───

@app.get("/index-prices/debug")
async def get_index_prices_debug():
    if not INDEX_PRICES_LOADED:
        return {"loaded": False}
    try:
        def _q():
            with db_rwlock.read():
                return local_db.execute("""
                    SELECT symbol,
                        MIN(trade_date)::VARCHAR as min_date,
                        MAX(trade_date)::VARCHAR as max_date,
                        COUNT(*) as row_count
                    FROM index_prices GROUP BY symbol ORDER BY symbol
                """).fetchall()
        rows = await db_read(_q)
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
        def _q():
            with db_rwlock.read():
                return local_db.execute(sql("index_prices_summary.sql")).df().fillna(0).to_dict(orient="records")
        res = await db_read(_q)
        set_cached_response(cache_key, res)
        return res
    except Exception as e:
        logger.error(f"Index prices summary error: {e}")
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

        def _q():
            with db_rwlock.read():
                return local_db.execute(query, symbol_list).df()
        df = await db_read(_q)

        if df.empty:
            result = {"series": []}
            set_cached_response(cache_key, result)
            return result

        series = []
        for sym in symbol_list:
            sym_df = df[df["symbol"] == sym].copy()
            if sym_df.empty:
                continue
            sym_df = _ffill_outliers(sym_df)
            times = sym_df["time"].astype(str).str[:10].values
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
        logger.error(f"Index prices data error: {e}")
        return {"series": []}


@app.get("/index-prices/stats")
async def get_index_prices_stats(period: str = "1y", start: str = "", end: str = ""):
    """Compute per-index stats: daily change, period return, YTD, 52w range, volatility."""
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
    if not INDEX_PRICES_LOADED:
        return []

    use_custom = bool(start and end)
    cache_key = f"index_stats_{start}_{end}" if use_custom else f"index_stats_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        # Build period-dependent SQL fragments for the single CTE query.
        # This replaces ~36 sequential per-symbol queries with 1 query.
        if use_custom:
            period_where = f"trade_date >= '{start}'"
            vol_where = f"trade_date >= '{start}' AND trade_date <= '{end}'"
        elif period.lower() == "max":
            period_where = "TRUE"
            vol_where = "TRUE"
        else:
            days = INTERVALS.get(period.lower(), 365)
            period_where = f"trade_date >= (SELECT MAX(trade_date) - INTERVAL '{days} days' FROM index_prices)"
            vol_where = period_where

        stats_sql = f"""
        WITH base AS (
            SELECT symbol, name, currency, COALESCE(exchange, '') AS exchange,
                trade_date, close, high, low,
                LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_close,
                MAX(trade_date) OVER (PARTITION BY symbol) as sym_max_date
            FROM index_prices
            WHERE close IS NOT NULL AND close > 0
        ),
        agg AS (
            SELECT symbol,
                ARG_MAX(close, trade_date) as current_price,
                MAX(trade_date) as latest_date,
                ARG_MAX(name, trade_date) as name,
                ARG_MAX(currency, trade_date) as currency,
                ARG_MAX(exchange, trade_date) as exchange,
                ARG_MAX(prev_close, trade_date) as prev_close,
                ARG_MIN(close, trade_date) FILTER (WHERE {period_where}) as period_close,
                ARG_MIN(close, trade_date) FILTER (WHERE trade_date >= DATE_TRUNC('year', CURRENT_DATE)) as ytd_close,
                MIN(low) FILTER (WHERE trade_date >= sym_max_date - INTERVAL '365 days') as low_52w,
                MAX(high) FILTER (WHERE trade_date >= sym_max_date - INTERVAL '365 days') as high_52w,
                (STDDEV((close / NULLIF(prev_close, 0) - 1))
                    FILTER (WHERE prev_close IS NOT NULL AND {vol_where})) * SQRT(252) as volatility
            FROM base
            GROUP BY symbol
        )
        SELECT symbol, current_price, latest_date, name, currency, exchange,
            ROUND(CASE WHEN prev_close > 0
                 THEN ((current_price - prev_close) / prev_close * 100)::NUMERIC
                 ELSE 0 END, 2) AS daily_change_pct,
            ROUND(CASE WHEN period_close > 0
                 THEN ((current_price - period_close) / period_close * 100)::NUMERIC
                 ELSE 0 END, 2) AS period_return_pct,
            ROUND(CASE WHEN ytd_close > 0
                 THEN ((current_price - ytd_close) / ytd_close * 100)::NUMERIC
                 ELSE 0 END, 2) AS ytd_return_pct,
            ROUND(COALESCE(high_52w, current_price)::NUMERIC, 2) AS high_52w,
            ROUND(COALESCE(low_52w, current_price)::NUMERIC, 2) AS low_52w,
            ROUND(COALESCE(volatility * 100, 0)::NUMERIC, 2) AS volatility_pct
        FROM agg
        """

        def _q():
            with db_rwlock.read():
                rows = local_db.execute(stats_sql).fetchall()
                cols = ["symbol", "current_price", "latest_date", "name", "currency", "exchange",
                        "daily_change_pct", "period_return_pct", "ytd_return_pct",
                        "high_52w", "low_52w", "volatility_pct"]
                return [
                    {c: (float(row[i]) if isinstance(row[i], (int, float)) else row[i])
                     for i, c in enumerate(cols)}
                    for row in rows
                ]

        results = await db_read(_q)
        set_cached_response(cache_key, results)
        return results

    except Exception as e:
        logger.exception("Index stats error")
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
        def _q():
            with db_rwlock.read():
                if period.lower() == "max":
                    return local_db.execute(sql("index_price_single_max.sql"), [symbol]).df()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    return local_db.execute(
                        sql("index_price_single_period.sql").replace("{days}", str(days)),
                        [symbol]
                    ).df()
        df = await db_read(_q)

        df = _ffill_outliers(df)
        if "time" in df.columns:
            df["time"] = df["time"].astype(str).str[:10]
        result = _sanitize_floats(df.fillna(0).to_dict(orient="records"))
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Index price single error ({symbol}): {e}")
        return []


# --- REST API: SECTOR COMPARISON ---

# ─── Sector comparison: heatmap, table, series, top stocks, industry drill-down ───

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

            def _q(_table=table, _sector=sector):
                with db_rwlock.read():
                    if period.lower() == "max":
                        return local_db.execute(
                            sql("legacy_sector_avg_max.sql").replace("{table}", _table),
                            [_sector]
                        ).df()
                    else:
                        days = INTERVALS.get(period.lower(), 365)
                        return local_db.execute(
                            sql("legacy_sector_avg_period.sql")
                            .replace("{table}", _table)
                            .replace("{days}", str(days)),
                            [_sector]
                        ).df()
            df = await db_read(_q)

            if df.empty or len(df) < 2:
                continue

            closes, times = df["close"].values, df["time"].values
            base = closes[0] if closes[0] != 0 else 1
            points = [
                {"time": _ts(times[i]), "pct": float(((closes[i] - base) / base) * 100), "close": float(closes[i])}
                for i in range(len(closes))
            ]
            series.append({"indexKey": idx, "points": points})

        result = {"series": series, "sector": sector}
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Sector comparison data error: {e}")
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
        def _q():
            with db_rwlock.read():
                return local_db.execute(f"SELECT DISTINCT sector FROM ({union}) ORDER BY sector ASC").df()
        df = await db_read(_q)

        result = df["sector"].tolist() if not df.empty else []
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Available sectors error: {e}")
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
            def _q(_idx=idx):
                with db_rwlock.read():
                    return local_db.execute(
                        sql("sector_industries.sql").replace("{table}", f"prices_{_idx}"),
                        [sector]
                    ).df()
            df = await db_read(_q)
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
        logger.error(f"Sector industries error: {e}")
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
            def _q(_idx=idx):
                with db_rwlock.read():
                    return local_db.execute(
                        f"SELECT sector, industry, COUNT(DISTINCT symbol) as cnt FROM prices_{_idx}"
                        f" WHERE sector IS NOT NULL AND sector NOT IN ('N/A','0','')"
                        f" AND industry IS NOT NULL AND industry NOT IN ('N/A','0','')"
                        f" GROUP BY sector, industry"
                    ).df()
            df = await db_read(_q)
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
        logger.exception("All sector industries error")
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
                    def _q(_idx=idx, _sector=sector):
                        with db_rwlock.read():
                            return local_db.execute(
                                f"SELECT time, pct FROM sector_series_{_idx} WHERE sector = ? ORDER BY time",
                                [_sector]
                            ).df()
                    df = await db_read(_q)
                    if df.empty or len(df) < 2:
                        continue
                    points = [{"time": _ts(t), "pct": float(p)} for t, p in zip(df["time"].values, df["pct"].values)]
                    series.append({"symbol": idx, "points": points})

            elif mode == "single-index":
                idx = index_list[0]
                if not SECTOR_SERIES_STATUS.get(idx, {}).get("ready"):
                    all_ready = False
                else:
                    for sec in [s.strip() for s in sector.split(",") if s.strip()]:
                        def _q(_idx=idx, _sec=sec):
                            with db_rwlock.read():
                                return local_db.execute(
                                    f"SELECT time, pct FROM sector_series_{_idx} WHERE sector = ? ORDER BY time",
                                    [_sec]
                                ).df()
                        df = await db_read(_q)
                        if df.empty or len(df) < 2:
                            continue
                        points = [{"time": _ts(t), "pct": float(p)} for t, p in zip(df["time"].values, df["pct"].values)]
                        series.append({"symbol": sec, "points": points})

            if all_ready and series:
                result = {"series": series, "mode": mode}
                set_cached_response(cache_key, result)
                return result
        except Exception as e:
            logger.debug("Suppressed: %s", e)  # fall through to slow path

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
                def _q(_q=q, _p=params):
                    with db_rwlock.read():
                        return local_db.execute(_q, _p).df()
                df = await db_read(_q)
                if df.empty or len(df) < 2:
                    continue
                points = [{"time": _ts(r["time"]), "pct": float(r["pct"])} for _, r in df.iterrows()]
                series.append({"symbol": idx, "points": points})

        elif mode == "single-index":
            idx = index_list[0]
            if not ensure_index_loaded(idx):
                return {"series": [], "mode": mode}
            for sec in [s.strip() for s in sector.split(",") if s.strip()]:
                industry_clause, date_clause, params = _build_clauses(sec)
                q = build_clean_sector_sql(f"prices_{idx}", "sector = ?", industry_clause, date_clause)
                def _q2(_q=q, _p=params):
                    with db_rwlock.read():
                        return local_db.execute(_q, _p).df()
                df = await db_read(_q2)
                if df.empty or len(df) < 2:
                    continue
                points = [{"time": _ts(r["time"]), "pct": float(r["pct"])} for _, r in df.iterrows()]
                series.append({"symbol": sec, "points": points})

        result = {"series": series, "mode": mode}
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        logger.exception("Sector comparison v2 error")
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
            def _q(_t=series_table):
                with db_rwlock.read():
                    return local_db.execute(
                        f"SELECT sector, time, pct FROM {_t} ORDER BY sector, time"
                    ).df()
            df = await db_read(_q)

            if df.empty:
                continue

            idx_data = {}
            for sector, group in df.groupby("sector", sort=False):
                idx_data[sector] = [
                    {"time": _ts(t), "pct": float(p)}
                    for t, p in zip(group["time"].values, group["pct"].values)
                ]

            ALL_SERIES_CACHE[idx] = idx_data
            result[idx] = idx_data
            ready_indices.append(idx)
        except Exception as e:
            logger.error(f"all-series: error reading {idx}: {e}")
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
            def _q(_t=series_table, _s=sector):
                with db_rwlock.read():
                    return local_db.execute(
                        f"SELECT industry, time, pct, stock_count FROM {_t} "
                        f"WHERE sector = ? ORDER BY industry, time",
                        [_s]
                    ).df()
            df = await db_read(_q)

            if df.empty:
                ready_indices.append(idx)
                result[idx] = {}
                continue

            idx_data = {}
            for industry, group in df.groupby("industry", sort=False):
                idx_data[industry] = [
                    {"time": _ts(t), "pct": round(float(p), 4), "n": int(n)}
                    for t, p, n in zip(group["time"].values, group["pct"].values, group["stock_count"].values)
                ]

            result[idx] = idx_data
            ready_indices.append(idx)
        except Exception as e:
            logger.error(f"industry-series: error reading {idx}/{sector}: {e}")
            pending_indices.append(idx)

    return {"data": result, "ready": ready_indices, "pending": pending_indices}


@app.get("/sector-comparison/histogram")
async def get_sector_histogram(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    """Return average return per sector across indices for histogram display."""
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
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
            def _q(_idx=idx):
                with db_rwlock.read():
                    return _sector_returns_df(f"prices_{_idx}", use_custom, period, start, end)
            df = await db_read(_q)
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
        logger.error(f"Sector histogram error: {e}")
        return []


@app.get("/sector-comparison/table")
async def get_sector_comparison_table(indices: str = "", period: str = "1y", start: str = "", end: str = "", industries: str = ""):
    """Return sector returns broken down by index for heatmap/rankings table."""
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
    index_list = [i.strip() for i in indices.split(",") if i.strip() and i.strip() in MARKET_INDICES]
    if not index_list:
        index_list = [k for k, v in INDEX_LOAD_STATUS.items() if v.get("loaded")]
    if not index_list:
        return []

    industry_list = [i.strip() for i in industries.split(",") if i.strip()] if industries else None

    use_custom = bool(start and end)
    cache_key = (
        f"sector_table_{','.join(sorted(index_list))}_{start}_{end}"
        if use_custom else
        f"sector_table_{','.join(sorted(index_list))}_{period}"
    )
    if industry_list:
        cache_key += f"_ind_{'|'.join(sorted(industry_list))}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        all_data = {}
        for idx in index_list:
            if not ensure_index_loaded(idx):
                continue
            def _q(_idx=idx):
                with db_rwlock.read():
                    return _sector_returns_df(f"prices_{_idx}", use_custom, period, start, end, industries=industry_list)
            df = await db_read(_q)
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
        logger.exception("Sector comparison table error")
        return []


@app.get("/sector-comparison/top-stocks")
async def get_sector_top_stocks(
    sector: str, indices: str = "", period: str = "1y",
    start: str = "", end: str = "", n: int = 5
):
    """Return top N and bottom N performing stocks within a sector across indices."""
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
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

            def _q(_table=table, _sector=sector):
                with db_rwlock.read():
                    if use_custom:
                        return local_db.execute(
                            sql("sector_top_stocks_custom.sql")
                            .replace("{table}", _table)
                            .replace("{start}", start)
                            .replace("{end}", end),
                            [_sector]
                        ).df()
                    elif period.lower() == "max":
                        return local_db.execute(
                            sql("sector_top_stocks_max.sql").replace("{table}", _table),
                            [_sector]
                        ).df()
                    else:
                        days = INTERVALS.get(period.lower(), 365)
                        return local_db.execute(
                            sql("sector_top_stocks_period.sql")
                            .replace("{table}", _table)
                            .replace("{days}", str(days)),
                            [_sector]
                        ).df()
            df = await db_read(_q)

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
        logger.exception("Sector top stocks error")
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
            def _q(_rt=result_table, _p=period.lower()):
                with db_rwlock.read():
                    tables = [r[0] for r in local_db.execute("SHOW TABLES").fetchall()]
                    if _rt not in tables:
                        return None
                    return local_db.execute(
                        f"SELECT symbol, name, industry, sector, return_pct FROM {_rt} WHERE period = ?",
                        [_p]
                    ).df()
            df = await db_read(_q)
            if df is None:
                pending.append(idx)
                continue

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
            logger.error(f"Error reading stock returns for {idx}: {e}")
            pending.append(idx)

    return {"data": data, "ready": ready, "pending": pending}


@app.get("/sector-comparison/industry-breakdown")
async def get_industry_breakdown(
    index: str = "", sector: str = "",
    period: str = "1y", start: str = "", end: str = "",
):
    """Return per-industry return breakdown within one sector of one index."""
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
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
        def _q():
            with db_rwlock.read():
                if use_custom:
                    return local_db.execute(
                        sql("industry_breakdown_custom.sql")
                        .replace("{table}", table)
                        .replace("{start}", start)
                        .replace("{end}", end),
                        [sector]
                    ).df()
                elif period.lower() == "max":
                    return local_db.execute(
                        sql("industry_breakdown_max.sql").replace("{table}", table),
                        [sector]
                    ).df()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    return local_db.execute(
                        sql("industry_breakdown_period.sql")
                        .replace("{table}", table)
                        .replace("{days}", str(days)),
                        [sector]
                    ).df()
        df = await db_read(_q)

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
        logger.exception("Industry breakdown error")
        return []


@app.get("/sector-comparison/industry-turnover")
async def get_industry_turnover(
    index: str = "", sector: str = "",
    period: str = "1y", start: str = "", end: str = "",
):
    """Return total turnover (close * volume) per industry within one sector of one index."""
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
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
        def _q():
            with db_rwlock.read():
                if use_custom:
                    return local_db.execute(
                        sql("industry_turnover_custom.sql")
                        .replace("{table}", table)
                        .replace("{start}", start)
                        .replace("{end}", end),
                        [sector]
                    ).df()
                elif period.lower() == "max":
                    return local_db.execute(
                        sql("industry_turnover_max.sql").replace("{table}", table),
                        [sector]
                    ).df()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    return local_db.execute(
                        sql("industry_turnover_period.sql")
                        .replace("{table}", table)
                        .replace("{days}", str(days)),
                        [sector]
                    ).df()
        df = await db_read(_q)

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
        logger.exception("Industry turnover error")
        return []


# ─── Global rankings: top/bottom sectors & industries across all indices ───

@app.get("/index-prices/top-sectors")
async def get_top_sectors(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
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

        def _q():
            with db_rwlock.read():
                return _top_items_df(union, "sector", use_custom, period, start, end)
        df = await db_read(_q)

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
        logger.error(f"Top sectors error: {e}")
        return {"top": [], "bottom": []}


@app.get("/index-prices/top-industries")
async def get_top_industries(indices: str = "", period: str = "1y", start: str = "", end: str = ""):
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
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

        def _q():
            with db_rwlock.read():
                return _top_items_df(union, "industry", use_custom, period, start, end)
        df = await db_read(_q)

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
        logger.error(f"Top industries error: {e}")
        return {"top": [], "bottom": []}


# ─── Stock data: sidebar list, OHLCV chart, rankings, metadata ───

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
        def _q():
            with db_rwlock.read():
                return _sanitize_floats(
                    local_db.execute(
                        sql("summary.sql")
                        .replace("{index}", index)
                    )
                    .df().fillna(0).to_dict(orient="records")
                )
        res = await db_read(_q)
        set_cached_response(cache_key, res)
        return res
    except Exception as e:
        logger.error(f"Summary Error ({index}): {e}")
        return []


# symbol-to-index lookup cache and suffix heuristics for lazy loading
SYMBOL_INDEX_MAP: dict = {}
# SUFFIX_TO_INDEX imported from index_config.py (via _CFG_SUFFIX_TO_INDEX)
SUFFIX_TO_INDEX = _CFG_SUFFIX_TO_INDEX


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
        except Exception as e:
            logger.debug("Suppressed: %s", e)
            continue

    # trigger lazy load for the guessed index
    guessed_index = _guess_index_for_symbol(symbol)
    if guessed_index and guessed_index in MARKET_INDICES:
        if not INDEX_LOAD_STATUS.get(guessed_index, {}).get("loaded"):
            logger.info(f"Lazy-loading {guessed_index} for symbol {symbol}")
            if ensure_index_loaded(guessed_index):
                try:
                    with db_rwlock.read():
                        count = local_db.execute(
                            f"SELECT COUNT(*) FROM prices_{guessed_index} WHERE symbol = ?", [symbol]
                        ).fetchone()[0]
                    if count > 0:
                        SYMBOL_INDEX_MAP[symbol] = guessed_index
                        return f"prices_{guessed_index}"
                except Exception as e:
                    logger.debug("Suppressed: %s", e)
    return None


@app.get("/data/{symbol:path}")
async def get_data(symbol: str, period: str = "1y"):
    symbol = unquote(symbol)
    cache_key = f"data_{symbol}_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    table = await db_read(lambda: _find_symbol_table(symbol))
    if not table:
        set_cached_response(cache_key, [])
        return []

    try:
        def _q():
            with db_rwlock.read():
                if period.lower() == "max":
                    return local_db.execute(
                        sql("symbol_data_max.sql").replace("{table}", table),
                        [symbol]
                    ).df()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    return local_db.execute(
                        sql("symbol_data_period.sql")
                        .replace("{table}", table)
                        .replace("{days}", str(days)),
                        [symbol, symbol]
                    ).df()
        df = await db_read(_q)

        df = _ffill_outliers(df)
        if "time" in df.columns:
            df["time"] = df["time"].astype(str).str[:10]
        result = _sanitize_floats(df.fillna(0).to_dict(orient="records"))
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Data Error: {e}")
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
        def _q():
            with db_rwlock.read():
                if period.lower() == "max":
                    return local_db.execute(
                        sql("rankings_max.sql")
                        .replace("{table}", table)
                        .replace("{index}", index)
                    ).df()
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    return local_db.execute(
                        sql("rankings_period.sql")
                        .replace("{table}", table)
                        .replace("{index}", index)
                        .replace("{days}", str(days))
                    ).df()
        df = await db_read(_q)

        result = _sanitize_floats({
            "selected": {
                "top":    df.head(3).to_dict("records"),
                "bottom": df.tail(3).sort_values("value").to_dict("records"),
            }
        })
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Ranking Error: {e}")
        return {"selected": {"top": [], "bottom": []}}


@app.get("/rankings/custom")
async def get_custom_rankings(start: str, end: str, index: str = "sp500"):
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
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
        def _q():
            with db_rwlock.read():
                return local_db.execute(
                    sql("rankings_custom.sql")
                    .replace("{table}", table)
                    .replace("{index}", index)
                    .replace("{start}", start)
                    .replace("{end}", end)
                ).df()
        df = await db_read(_q)

        result = {
            "selected": {
                "top":    df.head(3).to_dict("records"),
                "bottom": df.tail(3).sort_values("value").to_dict("records"),
            }
        }
        set_cached_response(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Custom Ranking Error: {e}")
        return {"selected": {"top": [], "bottom": []}}


@app.get("/most-active")
async def get_most_active(period: str = "1y", index: str = "sp500",
                          start: str = None, end: str = None):
    """Top 3 stocks by average daily volume for the selected period."""
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
    if index not in MARKET_INDICES:
        index = "sp500"
    if not ensure_index_loaded(index):
        return []

    period_key = f"{start}_{end}" if start else period
    cache_key = f"most_active_{index}_{period_key}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    table = f"prices_{index}"
    try:
        def _q():
            with db_rwlock.read():
                if start and end:
                    date_filter = f"AND p.trade_date >= '{start}' AND p.trade_date <= '{end}'"
                elif period.lower() == "max":
                    date_filter = ""
                else:
                    days = INTERVALS.get(period.lower(), 365)
                    date_filter = (
                        f"AND p.trade_date >= "
                        f"(SELECT MAX(trade_date) FROM {table} WHERE market_index = '{index}') "
                        f"- INTERVAL {days} DAY"
                    )
                return local_db.execute(f"""
                    WITH vol AS (
                        SELECT p.symbol,
                               AVG(p.volume)  as avg_volume,
                               SUM(CAST(p.volume AS DOUBLE) * p.close) as turnover,
                               COUNT(*)       as trading_days,
                               ((ARG_MAX(p.close, p.trade_date) - ARG_MIN(p.close, p.trade_date))
                                / NULLIF(ARG_MIN(p.close, p.trade_date), 0)) * 100 as period_return
                        FROM {table} p
                        WHERE p.market_index = '{index}'
                          AND p.close > 0
                          {date_filter}
                        GROUP BY p.symbol
                    )
                    SELECT v.symbol, l.name, l.sector,
                           CAST(l.close AS FLOAT) as last_price,
                           CAST(((l.close - l.prev_price) / NULLIF(l.prev_price, 0)) * 100 AS FLOAT) as daily_change_pct,
                           CAST(v.avg_volume AS BIGINT) as avg_volume,
                           CAST(v.turnover AS DOUBLE) as turnover,
                           v.trading_days,
                           ROUND(CAST(v.period_return AS FLOAT), 2) as period_return
                    FROM vol v
                    JOIN latest_{index} l ON l.symbol = v.symbol AND l.market_index = '{index}'
                    ORDER BY v.avg_volume DESC
                    LIMIT 3
                """).df()
        df = await db_read(_q)
        result = _sanitize_floats(df.to_dict(orient="records") if not df.empty else [])
        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Most-active error ({index}): {e}")
        return []


@app.get("/metadata/{symbol:path}")
async def metadata(symbol: str):
    symbol = unquote(symbol)
    table = await db_read(lambda: _find_symbol_table(symbol))
    if not table:
        return {"symbol": symbol, "name": symbol}
    try:
        def _q():
            with db_rwlock.read():
                return local_db.execute(
                    f"SELECT name FROM {table} WHERE symbol = ? LIMIT 1", [symbol]
                ).fetchone()
        res = await db_read(_q)
        return {"symbol": symbol, "name": res[0] if res else symbol}
    except Exception as e:
        logger.debug("Suppressed: %s", e)
        return {"symbol": symbol, "name": symbol}


# ─── News feed: Finnhub company + general news, mapped to indices ───

# News config imported from index_config.py
NEWS_INDEX_TICKERS = _CFG_NEWS_INDEX_TICKERS
INDEX_FLAG_MAP = _CFG_INDEX_FLAG_MAP
KEYWORD_INDEX_MAP = _CFG_KEYWORD_INDEX_MAP

# Ticker-to-index mapping for resolving general news 'related' field
NEWS_TICKER_INDEX = {}
for _idx, _tickers in NEWS_INDEX_TICKERS.items():
    for _t in _tickers:
        NEWS_TICKER_INDEX[_t] = _idx

_NEWS_TOPIC_RULES = [
    # (tag, keywords) — first match wins per tag; checked against headline+summary uppercase
    ("Earnings",     ["EARNINGS", "REVENUE", "PROFIT", " EPS ", "QUARTERLY RESULTS", "QUARTERLY REPORT",
                      "FISCAL Q", "BEAT ESTIMATES", "MISSED ESTIMATES", "ANNUAL REPORT", "GUIDANCE",
                      "FORECAST", "OUTLOOK", "SALES GROWTH", "NET INCOME", "OPERATING INCOME",
                      "MARGIN", "BOTTOM LINE", "TOP LINE", "INCOME ROSE", "INCOME FELL",
                      "BEATS EXPECTATIONS", "MISSES EXPECTATIONS", "FINANCIAL RESULTS"]),
    ("Central Bank", ["FEDERAL RESERVE", "FED ", " FOMC", "ECB ", "BANK OF ENGLAND", "BOE ",
                      "BANK OF JAPAN", "BOJ ", "RATE DECISION", "MONETARY POLICY", "INTEREST RATE",
                      "RATE HIKE", "RATE CUT", "HAWKISH", "DOVISH", "TAPER", "POWELL", "LAGARDE",
                      "QUANTITATIVE", "BASIS POINTS", "BOND BUYING", "YIELD CURVE"]),
    ("M&A",          ["MERGER", "ACQUISITION", "TAKEOVER", "BUYOUT", " DEAL ", "ACQUIRES", "ACQUIRED",
                      "STAKE IN", "DOUBLES ITS STAKE", "DUMPS ", "SELLS SHARES", "BUYS SHARES",
                      "STRATEGIC SALE", " SALE OF", "DIVESTITURE", "DIVESTS", "SPINS OFF"]),
    ("IPO",          [" IPO ", "INITIAL PUBLIC OFFERING", "PUBLIC LISTING", "GOES PUBLIC",
                      "DIRECT LISTING", " SPAC "]),
    ("Macro",        [" GDP ", "INFLATION", " CPI ", "UNEMPLOYMENT", " JOBS ", "PAYROLLS",
                      "RETAIL SALES", "PMI ", "CONSUMER CONFIDENCE", "TRADE DEFICIT", "TRADE SURPLUS",
                      "ECONOMIC GROWTH", "RECESSION", "CONSUMER SPENDING", "HOUSING MARKET",
                      "LABOR MARKET", "ECONOMIC DATA", "MANUFACTURING", "CRISIS"]),
    ("Commodities",  [" OIL ", "CRUDE", "BRENT", " WTI ", " GOLD ", "SILVER", "NATURAL GAS",
                      "COMMODITY", "COMMODITIES", "COPPER", "IRON ORE", "LITHIUM", "PLATINUM",
                      "PALLADIUM", "WHEAT", "SOYBEAN"]),
    ("Crypto",       ["BITCOIN", "ETHEREUM", "CRYPTO", "BLOCKCHAIN", " BTC ", " ETH ", "DOGECOIN",
                      "SOLANA", "STABLECOIN", "DEFI ", "WEB3", " XRP", "RIPPLE"]),
    ("Tech",         ["ARTIFICIAL INTELLIGENCE", " AI ", "AI-", "SEMICONDUCTOR", " CHIP ", "CHIPMAKER",
                      "CLOUD COMPUTING", "DATA CENTER", "MACHINE LEARNING", "DEEP LEARNING",
                      "NEURAL", "GPU ", "NVIDIA", "SOFTWARE", "SAAS ", "CYBERSECURITY", "5G ",
                      "CHATGPT", "OPENAI", "ANTHROPIC", "CLAUDE", "GEMINI", "COPILOT",
                      "LARGE LANGUAGE MODEL", " LLM", "GENERATIVE AI", "GEN AI",
                      "ROBOT", "AUTOMATION", "QUANTUM COMPUTING", "TECH STOCK",
                      "DRIVERLESS", "AUTONOMOUS", "SELF-DRIVING",
                      "MICROSOFT", "APPLE", "ALPHABET", "META PLATFORMS", "AMAZON", "SAMSUNG",
                      "TESLA", "GOOGLE", "MICRON"]),
    ("Energy",       ["CLEAN ENERGY", "RENEWABLE", "SOLAR POWER", "SOLAR ENERGY", "WIND POWER",
                      "WIND ENERGY", "NUCLEAR POWER", "NUCLEAR ENERGY", " HYDROGEN",
                      "BATTERY STORAGE", "ENERGY TRANSITION", "GREEN ENERGY", "ENERGY STOCK",
                      "ENERGY PRICE", "ENERGY BILL", "ELECTRIC VEHICLE", " EV "]),
    ("Healthcare",   ["PHARMA", "BIOTECH", " FDA ", "CLINICAL TRIAL", " DRUG ", "VACCINE",
                      "THERAPEUTIC", "MEDICAL DEVICE", "HEALTHCARE", "HEALTH CARE",
                      "NOVARTIS", "PFIZER", "ROCHE", "ABBVIE", "MERCK", "ASTRAZENECA",
                      "MARIJUANA", "CANNABIS"]),
    ("Regulation",   [" SEC ", "REGULATION", "REGULATORY", "ANTITRUST", "COMPLIANCE", "SANCTION",
                      "LAWSUIT", "SUED", "PENALTY", "INVESTIGATION", " FINE ", "FINED",
                      " DEI ", "FALSE ADVERTISING"]),
    ("Geopolitics",  ["TARIFF", "TRADE WAR", "SANCTIONS", "GEOPOLIT", "EMBARGO",
                      "TRADE TENSION", "TRADE DEAL", "EXPORT BAN", "IMPORT DUTY"]),
    ("Markets",      [" ETF ", "S&P 500", "NASDAQ", "DOW JONES", "BULL MARKET", "BEAR MARKET",
                      "CORRECTION", "RALLY", "SELL-OFF", "SELLOFF", "MARKET CRASH", "ALL-TIME HIGH",
                      "WALL STREET", "STOCK MARKET", " BUY ", "VALUATION", "OVERVALUED",
                      "UNDERVALUED", "DIVIDEND", "BUYBACK", "SHARE REPURCHASE",
                      "ANALYST", "UPGRADE", "DOWNGRADE", "PRICE TARGET", "TARGET PRICE",
                      "OUTPERFORM", "UNDERPERFORM", "OVERWEIGHT", "MARKET CAP",
                      "INDEX FUND", "SHARES ROSE", "SHARES FELL", "STOCK ROSE", "STOCK FELL",
                      "SHARES SURGE", "SHARES DROP", "STOCK SURGE", "STOCK DROP",
                      "STOCK SPLIT", "STOCK ZOOMED", "STOCK ASCEND", "FTSE", "NIKKEI",
                      " DAX ", "HANG SENG", "MARKET RETURNS", "STOCK HIGHER",
                      "INSTITUTIONAL INVESTOR", "RETIREMENT PORTFOLIO", "MILLIONAIRE",
                      "INVESTOR", "INVESTING", "PORTFOLIO", "STOCK ", "STOCKS ",
                      "SHARES ", " FUND ", "TRADING", "TRADERS", "HEDGE FUND",
                      "MOST ACTIVE", "LARGE CAP", "SMALL CAP", "MID CAP",
                      "MARKET HIGH", "MARKET LOW", "SECTOR ", "ROTATION",
                      "SKYROCKET", "BOUNCED", "PLUNGED", "SOAR", " TANK",
                      "DRAWDOWN", "HOLDINGS", "NYSE:", "NASDAQ:", "(NYSE",
                      "(NASDAQ", "CONSUMER STAPLE", "CONSUMER DISCRETION"]),
]

def _tag_article(headline: str, summary: str = "") -> list[str]:
    """Derive topic tags from headline + summary using keyword matching."""
    text = (headline + " " + summary).upper()
    tags = []
    for tag, keywords in _NEWS_TOPIC_RULES:
        for kw in keywords:
            if kw in text:
                tags.append(tag)
                break
        if len(tags) >= 2:  # cap at 2 tags per article
            break
    if not tags:
        tags.append("Finance")
    return tags

def _resolve_news_index(article):
    """Map a Finnhub news article to an index key via related tickers or headline keywords."""
    related = article.get('related', '')
    if related:
        for ticker in related.split(','):
            ticker = ticker.strip().upper()
            idx = NEWS_TICKER_INDEX.get(ticker)
            if idx:
                return idx
    headline = (article.get('headline', '') + ' ' + article.get('summary', '')).upper()
    for keywords, idx in KEYWORD_INDEX_MAP:
        for kw in keywords:
            if kw.upper() in headline:
                return idx
    return None

async def _fetch_company_news_async(ticker, index_key, from_date, to_date):
    """Fetch company news from Finnhub for a single ticker (async, connection-pooled)."""
    try:
        resp = await _http_finnhub.get(
            "/api/v1/company-news",
            params={"symbol": ticker, "from": from_date, "to": to_date, "token": FINNHUB_API_KEY},
        )
        if resp.status_code == 200:
            return [(item, index_key) for item in resp.json()]
    except Exception as e:
        logger.debug("Suppressed: %s", e)
    return []

@app.get("/news")
async def get_news():
    cache_key = "news_3m"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    if not FINNHUB_API_KEY or _cb_finnhub.is_open:
        return get_stale_response(cache_key) or []

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        articles = []
        seen = set()

        # 1) Fetch company news for all tickers concurrently (async, no ThreadPoolExecutor)
        company_tasks = []
        for index_key, tickers in NEWS_INDEX_TICKERS.items():
            for ticker in tickers:
                company_tasks.append(_fetch_company_news_async(ticker, index_key, three_months_ago, today))

        # 2) Also fetch general + merger news concurrently
        category_tasks = [
            _http_finnhub.get("/api/v1/news", params={"category": cat, "token": FINNHUB_API_KEY})
            for cat in ("general", "merger")
        ]

        # Fire all requests concurrently — company news + category news
        all_results = await asyncio.gather(*company_tasks, *category_tasks, return_exceptions=True)

        # Process company news results
        company_results = all_results[:len(company_tasks)]
        for result in company_results:
            if isinstance(result, Exception):
                continue
            for item, idx in result:
                headline = item.get("headline", "")
                if not headline or headline in seen:
                    continue
                seen.add(headline)
                summary = item.get("summary", "")
                articles.append({
                    "headline": headline,
                    "source": item.get("source", ""),
                    "datetime": item.get("datetime", 0),
                    "url": item.get("url", ""),
                    "index": idx,
                    "summary": summary,
                    "tags": _tag_article(headline, summary),
                })

        # Process category news results
        category_results = all_results[len(company_tasks):]
        for result in category_results:
            if isinstance(result, Exception):
                continue
            if result.status_code == 200:
                for item in result.json():
                    headline = item.get("headline", "")
                    if not headline or headline in seen:
                        continue
                    seen.add(headline)
                    idx = _resolve_news_index(item)
                    if not idx:
                        continue
                    summary = item.get("summary", "")
                    articles.append({
                        "headline": headline,
                        "source": item.get("source", ""),
                        "datetime": item.get("datetime", 0),
                        "url": item.get("url", ""),
                        "index": idx,
                        "summary": summary,
                        "tags": _tag_article(headline, summary),
                    })

        _cb_finnhub.record_success()

        # Sort all articles by date, no quota filtering
        articles.sort(key=lambda x: x["datetime"], reverse=True)
        set_cached_response(cache_key, articles)
        per_index = {}
        for a in articles:
            per_index.setdefault(a["index"], []).append(a)
        counts = {idx: len(items) for idx, items in per_index.items()}
        logger.info(f"News: {len(articles)} articles - {counts}")
        return articles
    except Exception as e:
        _cb_finnhub.record_failure()
        logger.error(f"News fetch error: {e}")
        stale = get_stale_response(cache_key)
        if stale:
            return stale
        return []


@app.get("/news/latest")
async def get_news_latest(since: int = 0):
    """Return only articles newer than `since` (unix timestamp) from the cached news."""
    cache_key = "news_3m"
    cached = get_cached_response(cache_key)
    if not cached:
        # No cache yet — trigger a full fetch so next call has data
        cached = await get_news()
    if not cached or since == 0:
        return cached or []
    return [a for a in cached if a["datetime"] > since]


# ─── Macro pulse: FRED rates, FX, economic calendar, risk summary ───

FRED_SERIES = {
    "DGS10":             {"name": "US 10Y",      "group": "bonds",       "unit": "%"},
    "IRLTLT01DEM156N":   {"name": "Germany 10Y",  "group": "bonds",       "unit": "%"},
    "IRLTLT01GBM156N":   {"name": "UK 10Y Gilt",  "group": "bonds",       "unit": "%"},
    "IRLTLT01JPM156N":   {"name": "Japan 10Y",    "group": "bonds",       "unit": "%"},
    "DCOILBRENTEU":      {"name": "Brent Crude",  "group": "commodities", "unit": "$/bbl"},
    "GOLDAMGBD228NLBM":  {"name": "Gold",         "group": "commodities", "unit": "$/oz"},
    "DHHNGSP":           {"name": "Natural Gas",  "group": "commodities", "unit": "$/MMBtu"},
    "DFF":               {"name": "Fed Funds",    "group": "rates",       "unit": "%"},
    "T10Y2Y":            {"name": "10Y-2Y Spread","group": "rates",       "unit": "%"},
    "BAMLH0A0HYM2":      {"name": "HY Credit Spread", "group": "rates",  "unit": "%"},
    "T10YIE":            {"name": "10Y Breakeven", "group": "rates",      "unit": "%"},
    "VIXCLS":            {"name": "VIX",          "group": "volatility",  "unit": ""},
    "ECBMRRFR":          {"name": "ECB Refi Rate","group": "rates",       "unit": "%"},
    "IR3TIB01EZM156N":   {"name": "EURIBOR 3M",  "group": "rates",       "unit": "%"},
}


async def _fetch_fred_series(series_id, observations_limit=5):
    """Fetch latest observations for a single FRED series (async, connection-pooled)."""
    try:
        resp = await _http_fred.get(
            "/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": observations_limit,
            },
        )
        if resp.status_code != 200:
            return None
        obs = resp.json().get("observations", [])
        valid = [o for o in obs if o.get("value", ".") != "."]
        if not valid:
            return None
        latest = valid[0]
        prev = valid[1] if len(valid) > 1 else None
        val = float(latest["value"])
        prev_val = float(prev["value"]) if prev else None
        change = (val - prev_val) if prev_val is not None else None
        return {
            "value": round(val, 4),
            "prev": round(prev_val, 4) if prev_val is not None else None,
            "change": round(change, 4) if change is not None else None,
            "date": latest["date"],
        }
    except Exception as e:
        logger.debug("Suppressed: %s", e)
        return None


# ─── Correlation matrix: Pearson correlation between index daily returns ───

@app.get("/correlation")
async def get_correlation(period: str = "1y"):
    """Compute Pearson correlation matrix between the 6 indices."""
    if not INDEX_PRICES_LOADED:
        return {"matrix": [], "labels": []}

    cache_key = f"correlation_{period}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    try:
        date_clause = ""
        if period.lower() != "max":
            days = INTERVALS.get(period.lower(), 365)
            date_clause = f"AND trade_date >= CURRENT_DATE - INTERVAL '{days} days'"

        query = sql("correlation_returns.sql").replace("{date_clause}", date_clause)

        def _q():
            with db_rwlock.read():
                return local_db.execute(query).df()
        df = await db_read(_q)

        if df.empty:
            return {"matrix": [], "labels": []}

        pivot = df.pivot(index="time", columns="symbol", values="ret").dropna()
        corr = pivot.corr()

        ordered_tickers = [
            INDEX_KEY_TO_TICKER[k]
            for k in CORRELATION_ORDER
            if k in INDEX_KEY_TO_TICKER and INDEX_KEY_TO_TICKER[k] in corr.columns
        ]
        corr = corr.loc[ordered_tickers, ordered_tickers]
        labels = [INDEX_TICKER_TO_KEY.get(t, t) for t in ordered_tickers]
        matrix = [
            [round(float(corr.loc[t1, t2]), 4) for t2 in ordered_tickers]
            for t1 in ordered_tickers
        ]

        result = {"matrix": matrix, "labels": labels}
        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Correlation error: {e}")
        return {"matrix": [], "labels": []}


# ─── FX rates via Frankfurter (ECB data, no API key needed) ───

_FX_SYMBOLS = "EUR,GBP,JPY,CNY,INR,CHF,AUD"
_FX_INVERTED = {"EUR", "GBP", "AUD"}  # pairs quoted as X/USD → invert


def _fx_pair_val(raw_rates, ccy):
    """Convert raw base=USD rate to display pair value."""
    v = raw_rates.get(ccy)
    if v is None or v == 0:
        return None
    return round(1 / v, 6) if ccy in _FX_INVERTED else round(v, 4 if ccy not in ("JPY", "INR") else 2)


def _fx_pair_name(ccy):
    return f"{ccy}/USD" if ccy in _FX_INVERTED else f"USD/{ccy}"


@app.get("/macro/fx")
async def get_macro_fx():
    """Fetch FX rates + daily change from Frankfurter API (ECB data, no key needed)."""
    cache_key = "macro_fx"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    if _cb_frankfurter.is_open:
        return get_stale_response(cache_key) or {"rates": {}, "base": "USD"}

    try:
        # Fetch latest and previous business day concurrently via async connection pool
        fx_params = {"base": "USD", "symbols": _FX_SYMBOLS}
        prev_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        latest_r, prev_r = await asyncio.gather(
            _http_frankfurter.get("/v1/latest", params=fx_params),
            _http_frankfurter.get(f"/v1/{prev_date}..{end_date}", params=fx_params),
        )

        latest_data = latest_r.json() if latest_r.status_code == 200 else {}
        prev_data = prev_r.json() if prev_r.status_code == 200 else {}
        _cb_frankfurter.record_success()

        latest_rates = latest_data.get("rates", {})
        # Time-series response: {"rates": {"2026-02-19": {...}, "2026-02-20": {...}}}
        # Pick the earliest date as "previous"
        prev_rates = {}
        ts_rates = prev_data.get("rates", {})
        if isinstance(ts_rates, dict) and ts_rates:
            sorted_dates = sorted(ts_rates.keys())
            if len(sorted_dates) >= 2:
                prev_rates = ts_rates[sorted_dates[-2]]  # second-to-last = previous day
            elif len(sorted_dates) == 1:
                prev_rates = ts_rates[sorted_dates[0]]

        ccys = ["EUR", "GBP", "JPY", "CNY", "INR", "CHF", "AUD"]
        rates_out = {}
        for ccy in ccys:
            val = _fx_pair_val(latest_rates, ccy)
            if val is None:
                continue
            pair = _fx_pair_name(ccy)
            entry = {"value": val}
            prev_val = _fx_pair_val(prev_rates, ccy)
            if prev_val is not None and prev_val != 0:
                diff = round(val - prev_val, 6)
                pct = round((val - prev_val) / prev_val * 100, 3)
                entry["change"] = diff
                entry["changePct"] = pct
            rates_out[pair] = entry

        result = {"base": "USD", "rates": rates_out}
        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Macro FX error: {e}")
        return get_stale_response(cache_key) or {"rates": {}, "base": "USD"}


# ─── Economic calendar: upcoming high-impact FRED releases ───

_HIGH_IMPACT_RELEASES = {
    # (name, country, impact, description, source_url)
    10:  ("CPI", "US", "high", "Consumer Price Index — measures average change in prices paid by urban consumers for a basket of goods and services. Key inflation gauge watched by the Fed.", "https://www.bls.gov/cpi/"),
    9:   ("Retail Sales", "US", "high", "Monthly retail and food services sales. Indicator of consumer spending strength, which drives ~70% of US GDP.", "https://www.census.gov/retail/"),
    11:  ("Employment Cost Index", "US", "high", "Quarterly measure of total compensation costs (wages + benefits). Closely watched by the Fed for wage-driven inflation pressures.", "https://www.bls.gov/eci/"),
    13:  ("Industrial Production", "US", "high", "Output of factories, mines, and utilities. Measures manufacturing sector health and capacity utilization.", "https://www.federalreserve.gov/releases/g17/"),
    46:  ("PCE Price Index", "US", "high", "Personal Consumption Expenditures price index — the Fed's preferred inflation measure. Broader than CPI, includes employer-paid healthcare.", "https://www.bea.gov/data/personal-consumption-expenditures-price-index"),
    50:  ("Nonfarm Payrolls", "US", "high", "Monthly change in employed persons excluding farm workers. The most closely watched employment indicator; major market mover.", "https://www.bls.gov/ces/"),
    53:  ("GDP", "US", "high", "Gross Domestic Product — broadest measure of economic output. Quarterly estimate of total goods and services produced.", "https://www.bea.gov/data/gdp"),
    21:  ("Housing Starts", "US", "medium", "New residential construction projects started. Leading indicator of housing market health and construction activity.", "https://www.census.gov/construction/nrc/"),
    32:  ("ISM Manufacturing", "US", "medium", "Institute for Supply Management manufacturing PMI. Above 50 = expansion, below 50 = contraction. Leading economic indicator.", "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/"),
    33:  ("ISM Services", "US", "medium", "ISM services sector PMI. Services represent ~80% of US economy, making this a key activity gauge.", "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/"),
    14:  ("Consumer Credit", "US", "medium", "Outstanding consumer credit (auto loans, credit cards, student loans). Signals consumer borrowing appetite and financial health.", "https://www.federalreserve.gov/releases/g19/"),
    19:  ("PPI", "US", "medium", "Producer Price Index — measures price changes from the seller's perspective. Leading indicator for consumer inflation (CPI).", "https://www.bls.gov/ppi/"),
    22:  ("Existing Home Sales", "US", "medium", "Monthly sales of previously owned homes. Largest segment of the housing market; sensitive to mortgage rates.", "https://www.nar.realtor/research-and-statistics/housing-statistics/existing-home-sales"),
    47:  ("Durable Goods", "US", "medium", "Orders for manufactured goods expected to last 3+ years (machinery, vehicles, aircraft). Proxy for business investment intentions.", "https://www.census.gov/manufacturing/m3/"),
    83:  ("Treasury Budget", "US", "medium", "Monthly US government budget surplus or deficit. Tracks federal spending vs revenue; impacts Treasury supply outlook.", "https://fiscaldata.treasury.gov/datasets/monthly-treasury-statement/"),
    116: ("ADP Employment", "US", "medium", "ADP private-sector employment estimate, released 2 days before Nonfarm Payrolls. Used as an early read on the labor market.", "https://adpemploymentreport.com/"),
    180: ("Trade Balance", "US", "medium", "Difference between exports and imports of goods and services. Large deficits can pressure the dollar; surplus supports it.", "https://www.bea.gov/data/intl-trade-investment/international-trade-goods-and-services"),
    323: ("JOLTS", "US", "medium", "Job Openings and Labor Turnover Survey. Measures labor demand (openings), hires, and quits. Fed watches quits rate closely.", "https://www.bls.gov/jlt/"),
    175: ("Beige Book", "US", "medium", "Fed's qualitative summary of economic conditions across 12 districts. Published 2 weeks before each FOMC meeting.", "https://www.federalreserve.gov/monetarypolicy/beige-book-default.htm"),
    # International releases tracked by FRED
    192: ("EU CPI", "EU", "high", "Eurozone Harmonised Index of Consumer Prices. Primary inflation measure for ECB monetary policy decisions.", "https://ec.europa.eu/eurostat/databrowser/view/prc_hicp_manr/default/table?lang=en"),
    194: ("EU GDP", "EU", "high", "Eurozone gross domestic product. Quarterly aggregate output for the 20-member euro area.", "https://ec.europa.eu/eurostat/databrowser/view/namq_10_gdp/default/table?lang=en"),
    193: ("EU Unemployment", "EU", "medium", "Eurozone unemployment rate. Structural indicator of labor market slack across the euro area.", "https://ec.europa.eu/eurostat/databrowser/view/une_rt_m/default/table?lang=en"),
    205: ("UK CPI", "GB", "high", "UK Consumer Prices Index. Bank of England's target inflation measure; drives rate decisions.", "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/latest"),
    206: ("UK GDP", "GB", "high", "UK gross domestic product. Quarterly estimate of total economic output for the United Kingdom.", "https://www.ons.gov.uk/economy/grossdomesticproductgdp/bulletins/gdpfirstquarterlyestimateuk/latest"),
    207: ("UK Unemployment", "GB", "medium", "UK unemployment rate from the Labour Force Survey. Key input for Bank of England policy.", "https://www.ons.gov.uk/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms"),
    253: ("Japan CPI", "JP", "high", "Japan Consumer Price Index. Watched for signs of reflation after decades of deflationary pressure.", "https://www.stat.go.jp/english/data/cpi/1581-z.html"),
    254: ("Japan GDP", "JP", "high", "Japan gross domestic product. Quarterly output measure for the world's 4th-largest economy.", "https://www.esri.cao.go.jp/en/sna/data/sokuhou/top_index.html"),
}


@app.get("/macro/calendar")
async def get_macro_calendar():
    """Fetch upcoming high-impact economic releases from FRED (US + international)."""
    cache_key = "macro_calendar"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    if not FRED_API_KEY:
        return []

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        four_weeks = (datetime.now() + timedelta(days=28)).strftime("%Y-%m-%d")

        resp = await _http_fred.get(
            "/fred/releases/dates",
            params={
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "sort_order": "asc",
                "include_release_dates_with_no_data": "true",
                "limit": 400,
                "realtime_start": today,
                "realtime_end": four_weeks,
            },
        )
        if resp.status_code != 200:
            return []

        releases = resp.json().get("release_dates", [])
        result = []
        seen = set()
        for rel in releases:
            rid = rel.get("release_id")
            if rid not in _HIGH_IMPACT_RELEASES:
                continue
            date_str = rel.get("date", "")
            dedup_key = (date_str, rid)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            name, country, impact, desc, source_url = _HIGH_IMPACT_RELEASES[rid]
            result.append({
                "date": date_str + " 08:30",
                "country": country,
                "event": name,
                "impact": impact,
                "actual": None,
                "estimate": None,
                "prev": None,
                "unit": "",
                "description": desc,
                "link": source_url,
            })

        result.sort(key=lambda x: x["date"])
        result = result[:80]

        set_cached_response(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Macro calendar error: {e}")
        return get_stale_response(cache_key) or []


@app.get("/macro/rates")
async def get_macro_rates():
    """Fetch bond yields, commodities, VIX from FRED API + live yfinance instruments."""
    # --- FRED data (cached) ---
    cache_key = "macro_rates"
    cached = get_cached_response(cache_key)
    if cached:
        fred_instruments = list(cached.get("instruments", []))
    elif not FRED_API_KEY:
        fred_instruments = []
    elif _cb_fred.is_open:
        stale = get_stale_response(cache_key)
        fred_instruments = list(stale.get("instruments", [])) if stale else []
    else:
        try:
            sids = list(FRED_SERIES.keys())
            results = await asyncio.gather(*[_fetch_fred_series(sid) for sid in sids], return_exceptions=True)
            fred_instruments = []
            for sid, data in zip(sids, results):
                if isinstance(data, Exception) or data is None:
                    continue
                meta = FRED_SERIES[sid]
                fred_instruments.append({"id": sid, "name": meta["name"], "group": meta["group"], "unit": meta["unit"], **data})
            _cb_fred.record_success()
            set_cached_response(cache_key, {"instruments": fred_instruments})
        except Exception as e:
            _cb_fred.record_failure()
            logger.error(f"Macro rates error: {e}")
            stale = get_stale_response(cache_key)
            fred_instruments = list(stale.get("instruments", [])) if stale else []

    # --- Inject yfinance-sourced instruments (always fresh, not cached) ---
    YFINANCE_MACRO = {
        "MOVE":      {"name": "MOVE Index",    "group": "volatility",  "unit": ""},
        "EU CARBON": {"name": "Carbon (KRBN)", "group": "commodities", "unit": "$/sh"},
    }
    # Strip any previously-injected non-FRED instruments from cached list
    fred_ids = set(FRED_SERIES.keys())
    instruments = [i for i in fred_instruments if i["id"] in fred_ids]

    for display_sym, meta in YFINANCE_MACRO.items():
        md = LATEST_MARKET_DATA.get(display_sym)
        if md:
            instruments.append({
                "id": display_sym, "name": meta["name"], "group": meta["group"],
                "unit": meta["unit"], "value": md.get("price"), "date": None,
                "prev": None, "change": md.get("diff"),
            })

    # Inject EU Vol (computed from EURO STOXX 50 realized vol)
    global _last_eu_vol
    try:
        def _compute_eu_vol_for_rates():
            try:
                with db_rwlock.read():
                    df = local_db.execute("""
                        SELECT close FROM index_prices
                        WHERE symbol = '^STOXX50E' AND close IS NOT NULL AND close > 0
                        ORDER BY trade_date DESC LIMIT 31
                    """).fetchdf()
                if len(df) >= 10:
                    closes = df["close"].astype(float).values[::-1]
                    rets = (closes[1:] / closes[:-1]) - 1
                    return round(float(rets.std() * (252 ** 0.5) * 100), 1)
            except Exception as e:
                logger.debug("Suppressed: %s", e)
            return None
        eu_vol_val = await db_read(_compute_eu_vol_for_rates)
        if eu_vol_val is not None:
            _last_eu_vol = eu_vol_val
    except Exception as e:
        logger.debug("Suppressed: %s", e)
        eu_vol_val = _last_eu_vol
    if eu_vol_val is not None:
        instruments.append({
            "id": "EU_VOL", "name": "EU Vol (STOXX 50)", "group": "volatility",
            "unit": "%", "value": eu_vol_val, "date": None,
            "prev": None, "change": None,
        })

    group_order = {"bonds": 0, "commodities": 1, "rates": 2, "volatility": 3}
    instruments.sort(key=lambda x: (group_order.get(x["group"], 9), x["name"]))
    return {"instruments": instruments}


@app.get("/macro/risk-summary")
async def get_risk_summary():
    """Compute risk dashboard signals from cached macro data."""
    # Re-use the macro/rates data
    rates_data = await get_macro_rates()
    instruments = rates_data.get("instruments", [])
    inst_map = {i["id"]: i for i in instruments}

    # VIX
    vix = inst_map.get("VIXCLS", {})
    vix_val = vix.get("value")
    vix_level = "low" if vix_val and vix_val < 15 else "normal" if vix_val and vix_val < 20 else "elevated" if vix_val and vix_val < 30 else "high" if vix_val else "unknown"

    # Yield curve
    spread = inst_map.get("T10Y2Y", {})
    spread_val = spread.get("value")
    curve_status = "inverted" if spread_val is not None and spread_val < 0 else "flat" if spread_val is not None and spread_val < 0.25 else "normal" if spread_val is not None else "unknown"

    # HY credit spread direction
    hy = inst_map.get("BAMLH0A0HYM2", {})
    hy_val = hy.get("value")
    hy_change = hy.get("change")
    credit_status = "tightening" if hy_change is not None and hy_change < -0.02 else "widening" if hy_change is not None and hy_change > 0.02 else "stable" if hy_change is not None else "unknown"

    # USD strength from FX data
    fx_data = await get_macro_fx()
    fx_rates = fx_data.get("rates", {})
    # Average absolute pct change across major pairs — positive = USD strengthening for USD/X pairs
    usd_changes = []
    for pair, data in fx_rates.items():
        pct = data.get("changePct")
        if pct is not None:
            # For USD/X pairs, positive pct = USD strengthening; for X/USD, negative = USD strengthening
            if pair.startswith("USD/"):
                usd_changes.append(pct)
            else:
                usd_changes.append(-pct)
    avg_usd = sum(usd_changes) / len(usd_changes) if usd_changes else 0
    usd_status = "strengthening" if avg_usd > 0.1 else "weakening" if avg_usd < -0.1 else "stable"

    # EU Vol — already computed by get_macro_rates() above, just read the cached value
    eu_vol_val = _last_eu_vol
    eu_vol_level = "low" if eu_vol_val and eu_vol_val < 12 else "normal" if eu_vol_val and eu_vol_val < 20 else "elevated" if eu_vol_val and eu_vol_val < 30 else "high" if eu_vol_val else "unknown"

    # MOVE Index — bond market volatility from yfinance
    move_md = LATEST_MARKET_DATA.get("MOVE")
    move_val = round(move_md["price"], 1) if move_md and move_md.get("price") else None
    move_level = "low" if move_val and move_val < 80 else "normal" if move_val and move_val < 100 else "elevated" if move_val and move_val < 130 else "high" if move_val else "unknown"

    return {
        "vix": {"value": vix_val, "level": vix_level},
        "eu_vol": {"value": eu_vol_val, "level": eu_vol_level},
        "move": {"value": move_val, "level": move_level},
        "yield_curve": {"value": spread_val, "status": curve_status},
        "credit": {"value": hy_val, "change": hy_change, "status": credit_status},
        "usd": {"avg_change": round(avg_usd, 3), "status": usd_status},
    }


# ─── Technical indicators: RSI, MACD, Bollinger, ATR, Beta ───

_TECH_PARAMS = {
    # period → (rsi_span, macd_fast, macd_slow, macd_signal, bb_window, atr_window)
    "1w":  (5,  3,  10, 4,  7,  5),
    "1mo": (7,  5,  13, 5,  10, 7),
    "3mo": (10, 8,  21, 7,  15, 10),
    "6mo": (12, 10, 22, 8,  18, 12),
    "1y":  (14, 12, 26, 9,  20, 14),
    "2y":  (14, 12, 26, 9,  21, 14),
    "5y":  (14, 12, 26, 10, 22, 14),
    "max": (14, 12, 26, 11, 26, 14),
}


def _compute_technicals(df: pd.DataFrame, market_df: pd.DataFrame = None,
                        period: str = None) -> dict:
    """Compute RSI, MACD, Bollinger %B, ATR, Beta from an OHLCV DataFrame.
    Indicator lookback periods adapt to the requested period so that
    each timeframe produces genuinely different values."""
    n = len(df)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # Period-based lookback parameters — each period gets distinct values
    if period and period in _TECH_PARAMS:
        rsi_span, macd_f, macd_s, macd_sig, bb_win, atr_win = _TECH_PARAMS[period]
    elif n <= 40:
        rsi_span, macd_f, macd_s, macd_sig, bb_win, atr_win = 7, 5, 13, 5, 10, 7
    elif n <= 100:
        rsi_span, macd_f, macd_s, macd_sig, bb_win, atr_win = 10, 8, 21, 7, 15, 10
    elif n <= 200:
        rsi_span, macd_f, macd_s, macd_sig, bb_win, atr_win = 12, 10, 22, 8, 18, 12
    else:
        rsi_span, macd_f, macd_s, macd_sig, bb_win, atr_win = 14, 12, 26, 9, 20, 14

    result = {}
    # Store the parameters used so the frontend can display them
    result["params"] = {
        "rsi": rsi_span, "macd": [macd_f, macd_s, macd_sig],
        "bb": bb_win, "atr": atr_win, "rows": n,
    }

    # --- RSI ---
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).ewm(span=rsi_span, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(span=rsi_span, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_val = round(float(rsi_series.iloc[-1]), 1) if len(rsi_series) > 0 else None
    rsi_status = "overbought" if rsi_val and rsi_val > 70 else "oversold" if rsi_val and rsi_val < 30 else "neutral"
    result["rsi"] = {"value": rsi_val, "status": rsi_status}

    # --- MACD ---
    ema_fast = close.ewm(span=macd_f, adjust=False).mean()
    ema_slow = close.ewm(span=macd_s, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=macd_sig, adjust=False).mean()
    histogram = macd_line - signal_line
    macd_val = round(float(macd_line.iloc[-1]), 2) if len(macd_line) > 0 else None
    signal_val = round(float(signal_line.iloc[-1]), 2) if len(signal_line) > 0 else None
    hist_val = round(float(histogram.iloc[-1]), 2) if len(histogram) > 0 else None
    macd_status = "bullish" if hist_val and hist_val > 0 else "bearish"
    result["macd"] = {"value": macd_val, "signal": signal_val, "histogram": hist_val, "status": macd_status}

    # --- Bollinger Bands ---
    sma = close.rolling(bb_win).mean()
    std = close.rolling(bb_win).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    band_width = upper.iloc[-1] - lower.iloc[-1] if len(upper) > 0 else 1
    pct_b_val = round(float((close.iloc[-1] - lower.iloc[-1]) / band_width), 2) if band_width and band_width > 0 else None
    bb_status = "upper_band" if pct_b_val and pct_b_val > 0.8 else "lower_band" if pct_b_val is not None and pct_b_val < 0.2 else "mid_range"
    result["bollinger"] = {
        "upper": round(float(upper.iloc[-1]), 2) if len(upper) > 0 and pd.notna(upper.iloc[-1]) else None,
        "middle": round(float(sma.iloc[-1]), 2) if len(sma) > 0 and pd.notna(sma.iloc[-1]) else None,
        "lower": round(float(lower.iloc[-1]), 2) if len(lower) > 0 and pd.notna(lower.iloc[-1]) else None,
        "percent_b": pct_b_val,
        "status": bb_status,
    }

    # --- ATR ---
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(atr_win).mean()
    atr_val = round(float(atr.iloc[-1]), 2) if len(atr) > 0 and pd.notna(atr.iloc[-1]) else None
    atr_pct = round(float(atr_val / close.iloc[-1] * 100), 1) if atr_val and close.iloc[-1] > 0 else None
    atr_status = "high" if atr_pct and atr_pct > 3 else "low" if atr_pct is not None and atr_pct < 1.5 else "moderate"
    result["atr"] = {"value": atr_val, "percent": atr_pct, "status": atr_status}

    # --- Beta (vs S&P 500) — computed over whatever data window is provided ---
    if market_df is not None and len(market_df) > 15:
        stock_ret = close.pct_change().dropna()
        mkt_close = market_df["close"].astype(float)
        mkt_ret = mkt_close.pct_change().dropna()
        stock_ret.index = df["trade_date"].iloc[1:].values
        mkt_ret.index = market_df["trade_date"].iloc[1:].values
        aligned = pd.DataFrame({"stock": stock_ret, "market": mkt_ret}).dropna()
        if len(aligned) > 15:
            cov = aligned["stock"].cov(aligned["market"])
            var = aligned["market"].var()
            beta_val = round(float(cov / var), 2) if var > 0 else None
        else:
            beta_val = None
    else:
        beta_val = None
    beta_status = "high" if beta_val and beta_val > 1.3 else "low" if beta_val is not None and beta_val < 0.7 else "moderate"
    result["beta"] = {"value": beta_val, "status": beta_status}

    return result


@app.get("/technicals/{symbol:path}")
async def get_technicals(symbol: str, market_index: str = "sp500",
                         period: str = "1y", start: str = None, end: str = None):
    if start and not _valid_date(start):
        return {"error": "Invalid start date format"}
    if end and not _valid_date(end):
        return {"error": "Invalid end date format"}
    symbol = unquote(symbol)
    period_key = f"{start}_{end}" if start else period
    cache_key = f"technicals_{symbol}_{market_index}_{period_key}"
    cached = get_cached_response(cache_key)
    if cached:
        return cached

    # Resolve symbol table
    table = await db_read(lambda: _find_symbol_table(symbol))
    if not table:
        return {"symbol": symbol, "error": "symbol not found"}

    try:
        def _q():
            with db_rwlock.read():
                return local_db.execute(
                    f"SELECT trade_date, open, close, high, low, volume FROM {table} "
                    f"WHERE symbol = ? AND close IS NOT NULL AND close > 0 "
                    f"ORDER BY trade_date ASC",
                    [symbol]
                ).df()
        df = await db_read(_q)

        if df.empty or len(df) < 30:
            return {"symbol": symbol, "error": "insufficient data"}

        # Determine row count based on period — keys match frontend values
        PERIOD_ROWS = {"1w": 12, "1mo": 30, "3mo": 75, "6mo": 140, "1y": 260, "2y": 510, "5y": 1270, "max": len(df)}
        if start and end:
            df["trade_date"] = df["trade_date"].astype(str)
            df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)].reset_index(drop=True)
        else:
            n = PERIOD_ROWS.get(period, 260)
            df = df.tail(n).reset_index(drop=True)

        if len(df) < 10:
            return {"symbol": symbol, "error": "insufficient data for period"}

        # Fetch S&P 500 for beta — match period window
        market_df = None
        # Fetch S&P 500 for beta — match the SAME date range as stock data
        sp500_ticker = INDEX_KEY_TO_TICKER.get("sp500", "^GSPC")
        if symbol != sp500_ticker:
            try:
                date_min = str(df["trade_date"].iloc[0])[:10]
                date_max = str(df["trade_date"].iloc[-1])[:10]
                def _q2():
                    with db_rwlock.read():
                        return local_db.execute(
                            "SELECT trade_date, close FROM index_prices "
                            "WHERE symbol = ? AND close IS NOT NULL AND close > 0 "
                            "AND CAST(trade_date AS VARCHAR) >= ? "
                            "AND CAST(trade_date AS VARCHAR) <= ? "
                            "ORDER BY trade_date ASC",
                            [sp500_ticker, date_min, date_max]
                        ).df()
                market_df = await db_read(_q2)
            except Exception as e:
                logger.debug("Suppressed: %s", e)
                market_df = None

        technicals = _compute_technicals(df, market_df, period=period)
        date_str = str(df["trade_date"].iloc[-1])[:10] if len(df) > 0 else None

        response = _sanitize_floats({
            "symbol": symbol,
            "date": date_str,
            **technicals,
        })
        set_cached_response(cache_key, response)
        return response

    except Exception as e:
        logger.error(f"Technicals error ({symbol}): {e}")
        return {"symbol": symbol, "error": str(e)}


# ─── Cache diagnostics ───

@app.get("/metrics/cache")
async def get_cache_metrics():
    """Cache diagnostics: hit rates, sizes, per-key TTLs."""
    now = time.time()
    total = _CACHE_STATS["hits"] + _CACHE_STATS["misses"]
    hit_rate = round(_CACHE_STATS["hits"] / total * 100, 1) if total > 0 else 0

    # Per-key freshness snapshot
    entries = []
    for key, (_, ts) in API_CACHE.items():
        ttl = _effective_ttl(key)
        age = round(now - ts, 1)
        entries.append({"key": key, "age_s": age, "ttl_s": ttl, "fresh": age < ttl})

    return {
        "stats": {**_CACHE_STATS, "hit_rate_pct": hit_rate, "total_requests": total},
        "api_cache_size": len(API_CACHE),
        "series_cache_keys": list(ALL_SERIES_CACHE.keys()),
        "entries": entries,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 13. SERVER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
