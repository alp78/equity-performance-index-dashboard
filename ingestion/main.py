"""
Cloud Function — Daily Price Sync (Multi-Index)
================================================
Resilient sync with per-symbol gap detection.

On every run:
  1. For each index, check which symbols are missing data per-symbol
  2. Download and load ONLY the missing data
  3. Always checks ALL indices for gaps (catches previous failures)
  4. Per-symbol retry: individual ticker failures don't block others

POST body:
  { "index": "sp500" }       → sync only S&P 500
  { "index": "all" }         → sync all indices that need data
  { "index": "all_force" }   → same as "all"
"""

import functions_framework
from google.cloud import bigquery, storage
from os import getenv
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import json
import time
import requests
import logging
from io import StringIO

logger = logging.getLogger("sync")
logger.setLevel(logging.INFO)

PROJECT_ID  = getenv("PROJECT_ID")
DATASET_ID  = getenv("DATASET_ID")
BUCKET_NAME = getenv("BUCKET_NAME")

INDICES = {
    "stoxx50": {
        "table_id": getenv("STOXX50_TABLE_ID", "stoxx50_prices"),
        "sync_after_utc": 17,
    },
    "sp500": {
        "table_id": getenv("SP500_TABLE_ID", "sp500_prices"),
        "sync_after_utc": 21,
    },
    "ftse100": {
        "table_id": getenv("FTSE100_TABLE_ID", "ftse100_prices"),
        "sync_after_utc": 17,
    },
    "nikkei225": {
        "table_id": getenv("NIKKEI225_TABLE_ID", "nikkei225_prices"),
        "sync_after_utc": 7,
    },
    "csi300": {
        "table_id": getenv("CSI300_TABLE_ID", "csi300_prices"),
        "sync_after_utc": 8,
    },
    "nifty50": {
        "table_id": getenv("NIFTY50_TABLE_ID", "nifty50_prices"),
        "sync_after_utc": 11,
    },
}

MAX_RETRIES = 3
RETRY_DELAY_SEC = 30

EXCHANGE_SUFFIXES = {
    '.DE', '.PA', '.AS', '.BR', '.MI', '.MC', '.HE', '.VI',
    '.L',  '.SW', '.ST', '.CO', '.OL',
    '.T',  '.HK', '.SI', '.AX', '.NZ', '.SA', '.MX',
    '.TO', '.V',  '.NS', '.BO', '.SS', '.SZ',
}


def to_yf_ticker(db_ticker):
    for suffix in EXCHANGE_SUFFIXES:
        if db_ticker.upper().endswith(suffix.upper()):
            return db_ticker
    return db_ticker.replace('.', '-')

def get_full_table_ref(short_table_id):
    return f"{PROJECT_ID}.{DATASET_ID}.{short_table_id}"

def is_weekend(d):
    return d.weekday() >= 5

def last_trading_day(d):
    """Most recent weekday on or before d."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ============================================================================
# STEP 1 — PER-SYMBOL GAP DETECTION (including interior gaps)
# ============================================================================

def get_trading_days(start_date, end_date):
    """Generate all weekdays between start and end (inclusive)."""
    days = []
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days

def get_db_state_detailed(bq_client, table_ref, today):
    """
    Returns per-symbol metadata + per-symbol set of missing trading days.
    Checks the last 10 trading days for interior gaps.
    """
    # Get metadata
    meta_query = f"""
        SELECT 
            symbol,
            MAX(name) as name,
            MAX(sector) as sector,
            MAX(industry) as industry,
            MAX(trade_date) as last_date
        FROM `{table_ref}`
        GROUP BY symbol
    """
    meta_rows = list(bq_client.query(meta_query).result())

    name_map, sector_map, industry_map = {}, {}, {}
    symbol_last_dates = {}
    global_last_date = datetime(2023, 1, 1).date()

    for row in meta_rows:
        sym = row.symbol
        name_map[sym] = row.name or sym
        if row.sector and row.sector not in ('N/A', '0', ''):
            sector_map[sym] = row.sector
        if row.industry and row.industry not in ('N/A', '0', ''):
            industry_map[sym] = row.industry
        if row.last_date:
            d = row.last_date.date() if hasattr(row.last_date, 'date') else row.last_date
            symbol_last_dates[sym] = d
            if d > global_last_date:
                global_last_date = d

    tickers = list(name_map.keys())
    if not tickers:
        return tickers, global_last_date, name_map, sector_map, industry_map, {}, set()

    # Check last 10 trading days for interior gaps
    lookback_start = last_trading_day(today) - timedelta(days=16)  # ~10 trading days with buffer
    gap_query = f"""
        SELECT symbol, CAST(trade_date AS DATE) as td
        FROM `{table_ref}`
        WHERE trade_date >= '{lookback_start.strftime('%Y-%m-%d')}'
    """
    gap_rows = list(bq_client.query(gap_query).result())

    # Build set of (symbol, date) pairs that exist
    existing = set()
    for row in gap_rows:
        d = row.td.date() if hasattr(row.td, 'date') else row.td
        existing.add((row.symbol, d))

    # Expected trading days in the lookback window
    expected_days = get_trading_days(lookback_start, last_trading_day(today))

    # For each symbol, find which expected days are missing
    # Use the majority: if >80% of symbols have data for a day, it's a real trading day
    day_coverage = {}
    for d in expected_days:
        count = sum(1 for s in tickers if (s, d) in existing)
        day_coverage[d] = count

    # A day is a valid trading day if at least 50% of symbols have data
    threshold = len(tickers) * 0.5
    valid_trading_days = [d for d, c in day_coverage.items() if c >= threshold]

    # Find per-symbol missing dates (only for valid trading days)
    symbol_missing_dates = {}
    all_gap_dates = set()
    for sym in tickers:
        missing = [d for d in valid_trading_days if (sym, d) not in existing]
        if missing:
            symbol_missing_dates[sym] = missing
            all_gap_dates.update(missing)

    if symbol_missing_dates:
        total_gaps = sum(len(v) for v in symbol_missing_dates.values())
        logger.info(f"  Gap scan: {len(symbol_missing_dates)} symbols with {total_gaps} missing date-slots")

    return tickers, global_last_date, name_map, sector_map, industry_map, symbol_last_dates, symbol_missing_dates


# ============================================================================
# STEP 2 — DOWNLOAD & TRANSFORM
# ============================================================================

def download_and_transform(tickers, name_map, sector_map, industry_map,
                           start_date, end_date, symbol_last_dates):
    if not tickers:
        return []

    yf_map = {t: to_yf_ticker(t) for t in tickers}
    yf_tickers = list(yf_map.values())

    # Fetch missing sector/industry
    missing_meta = [t for t in tickers if t not in sector_map or t not in industry_map]
    if missing_meta:
        logger.info(f"  Fetching metadata for {len(missing_meta)} tickers...")
        for ticker in missing_meta[:50]:
            try:
                info = yf.Ticker(yf_map[ticker]).info
                s, i = info.get('sector', 'N/A'), info.get('industry', 'N/A')
                if s and s != 'N/A': sector_map[ticker] = s
                if i and i != 'N/A': industry_map[ticker] = i
            except:
                pass
            time.sleep(0.1)

    start_str = start_date.strftime('%Y-%m-%d')
    end_str = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')

    # Try bulk download first
    logger.info(f"  Bulk download: {len(yf_tickers)} tickers ({start_date} → {end_date})...")
    data = _try_download(yf_tickers, start_str, end_str)

    # Check how many tickers actually returned data
    bulk_symbols = set()
    if data is not None and not data.empty:
        is_multi = isinstance(data.columns, pd.MultiIndex)
        if is_multi:
            bulk_symbols = set(data.columns.get_level_values(0).unique())

    # If bulk got less than 30% of tickers, fall back to batch download
    if len(bulk_symbols) < len(yf_tickers) * 0.3 and len(yf_tickers) > 10:
        logger.warning(f"  Bulk got {len(bulk_symbols)}/{len(yf_tickers)} tickers. Falling back to batches...")
        data = _batch_download(yf_tickers, start_str, end_str, batch_size=30)

    if data is None or data.empty:
        return []

    return _extract_records(data, tickers, yf_map, name_map, sector_map, industry_map, symbol_last_dates)


def _try_download(yf_tickers, start_str, end_str):
    """Single bulk download attempt with retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = yf.download(
                yf_tickers, start=start_str, end=end_str,
                group_by='ticker', auto_adjust=False, threads=True,
            )
            if data is not None and not data.empty:
                logger.info(f"  Download OK (attempt {attempt}): {data.shape}")
                return data
        except Exception as e:
            logger.warning(f"  Download failed (attempt {attempt}): {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    return None


def _batch_download(yf_tickers, start_str, end_str, batch_size=30):
    """Download in small batches, merge results. For markets where bulk fails (e.g. China)."""
    all_frames = []
    for i in range(0, len(yf_tickers), batch_size):
        batch = yf_tickers[i:i + batch_size]
        logger.info(f"  Batch {i // batch_size + 1}: {len(batch)} tickers...")
        try:
            data = yf.download(
                batch, start=start_str, end=end_str,
                group_by='ticker', auto_adjust=False, threads=True,
            )
            if data is not None and not data.empty:
                all_frames.append(data)
                logger.info(f"    Got {data.shape}")
        except Exception as e:
            logger.warning(f"    Batch failed: {e}")
        time.sleep(2)  # Be gentle between batches

    if not all_frames:
        return None

    # Merge: concat along columns (each batch has same date index, different ticker columns)
    try:
        merged = pd.concat(all_frames, axis=1)
        # Remove duplicate columns if any
        merged = merged.loc[:, ~merged.columns.duplicated()]
        logger.info(f"  Merged {len(all_frames)} batches: {merged.shape}")
        return merged
    except Exception as e:
        logger.warning(f"  Merge failed: {e}")
        return all_frames[0] if all_frames else None


def _extract_records(data, tickers, yf_map, name_map, sector_map, industry_map, symbol_last_dates):
    """Extract records from downloaded data."""
    records = []
    failed_tickers = []
    is_multi = isinstance(data.columns, pd.MultiIndex)
    actual_dates = data.index.strftime('%Y-%m-%d').unique()

    for date_str in actual_dates:
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        for original_ticker in tickers:
            sym_last = symbol_last_dates.get(original_ticker)
            if sym_last and current_date <= sym_last:
                continue

            try:
                yf_ticker = yf_map[original_ticker]
                if is_multi:
                    if yf_ticker not in data.columns.get_level_values(0):
                        if original_ticker not in failed_tickers:
                            failed_tickers.append(original_ticker)
                        continue
                    ticker_data = data[yf_ticker].loc[date_str]
                else:
                    ticker_data = data.loc[date_str]

                close_val = ticker_data.get('Close')
                if close_val is None or pd.isna(close_val):
                    continue

                raw_open = ticker_data.get('Open')
                open_p = float(raw_open) if pd.notna(raw_open) else float(close_val)

                records.append({
                    "symbol":      original_ticker,
                    "name":        name_map.get(original_ticker, original_ticker),
                    "trade_date":  date_str,
                    "open_price":  open_p,
                    "close_price": float(close_val),
                    "high_price":  float(ticker_data['High']),
                    "low_price":   float(ticker_data['Low']),
                    "volume":      int(ticker_data['Volume']),
                    "sector":      sector_map.get(original_ticker, 'N/A'),
                    "industry":    industry_map.get(original_ticker, 'N/A'),
                })
            except Exception as e:
                if original_ticker not in failed_tickers:
                    failed_tickers.append(original_ticker)
                continue

    if failed_tickers:
        logger.warning(f"  {len(failed_tickers)} tickers had no data: {failed_tickers[:15]}")

    updated_symbols = set(r["symbol"] for r in records)
    logger.info(f"  Result: {len(records)} records for {len(updated_symbols)} symbols")
    return records


# ============================================================================
# STEP 3 — LOAD TO GCS & BIGQUERY
# ============================================================================

def load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, gcs_prefix, today):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob_path = f"{gcs_prefix}/{today.strftime('%Y/%m/%d')}/prices.json"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(json.dumps(records), content_type='application/json')

    ndjson = "\n".join([json.dumps(r) for r in records])
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    bq_client.load_table_from_file(StringIO(ndjson), table_ref, job_config=job_config).result()
    logger.info(f"  BigQuery: {len(records)} records → {table_ref}")


# ============================================================================
# SYNC LOGIC
# ============================================================================

def sync_index(bq_client, storage_client, index_key, config, today):
    table_ref = get_full_table_ref(config["table_id"])
    logger.info(f"\n[{index_key.upper()}] Checking {table_ref}...")

    try:
        tickers, global_last, name_map, sector_map, industry_map, symbol_last_dates, symbol_missing_dates = \
            get_db_state_detailed(bq_client, table_ref, today)
        logger.info(f"  {len(tickers)} tickers, global last: {global_last}")
    except Exception as e:
        logger.critical(f"  DB error for {index_key}: {e}")
        return 0, f"Error: {e}"

    if not tickers:
        return 0, "No tickers"

    expected = last_trading_day(today)

    # Trailing gaps: symbols whose MAX(trade_date) < expected
    trailing_behind = {s: d for s, d in symbol_last_dates.items() if d < expected}

    # Combine: any symbol with interior gaps OR trailing gaps needs data
    all_gap_symbols = set(symbol_missing_dates.keys()) | set(trailing_behind.keys())

    if not all_gap_symbols:
        logger.info(f"  All {len(tickers)} symbols up to date ({expected}), no interior gaps")
        return 0, "Up to date"

    # Find the earliest date we need to fetch from
    earliest_needed = expected  # default
    if trailing_behind:
        earliest_trailing = min(trailing_behind.values())
        earliest_needed = min(earliest_needed, earliest_trailing + timedelta(days=1))
    if symbol_missing_dates:
        all_missing = [d for dates in symbol_missing_dates.values() for d in dates]
        if all_missing:
            earliest_needed = min(earliest_needed, min(all_missing))

    logger.info(f"  {len(all_gap_symbols)} symbols need data. "
                f"Trailing: {len(trailing_behind)}, Interior gaps: {len(symbol_missing_dates)}. "
                f"Fetching from {earliest_needed}")

    # Build a combined "already have" map for download_and_transform
    # For interior gap filling, we can't use symbol_last_dates (it would skip gap dates)
    # Instead, pass an empty dict so ALL dates get processed, then deduplicate via BQ WRITE_APPEND
    # Actually better: build per-symbol earliest-gap date
    symbol_effective_start = {}
    for sym in tickers:
        missing = symbol_missing_dates.get(sym, [])
        trailing = symbol_last_dates.get(sym, datetime(2023, 1, 1).date())
        if missing:
            # Start from earliest missing date - 1 day (so it's included)
            sym_earliest = min(missing) - timedelta(days=1)
            symbol_effective_start[sym] = sym_earliest
        elif sym in trailing_behind:
            symbol_effective_start[sym] = trailing
        else:
            # Up to date — set to today so everything gets skipped
            symbol_effective_start[sym] = expected

    records = download_and_transform(
        tickers, name_map, sector_map, industry_map,
        earliest_needed, today, symbol_effective_start
    )

    if not records:
        sample = list(all_gap_symbols)[:10]
        logger.critical(f"  No data for {index_key}. Gap symbols: {sample}")
        return 0, f"No data ({len(all_gap_symbols)} gaps)"

    try:
        load_to_gcs_and_bq(bq_client, storage_client, records, table_ref, index_key, today)

        updated = set(r["symbol"] for r in records)
        still_missing = all_gap_symbols - updated
        if still_missing:
            logger.warning(f"  Still missing: {list(still_missing)[:10]}")

        return len(records), f"{len(records)} records ({len(updated)} symbols)"
    except Exception as e:
        logger.critical(f"  Load failed: {e}")
        return 0, f"Load error: {e}"


# ============================================================================
# ENTRY POINT
# ============================================================================

@functions_framework.http
def sync_stocks(request):
    """
    Every run checks ALL targeted indices for per-symbol data gaps.
    No market-hours gating — gaps from any previous failure are caught.
    """
    bq_client = bigquery.Client()
    storage_client = storage.Client()
    now_utc = datetime.utcnow()
    today = now_utc.date()

    if is_weekend(today):
        return f"Weekend — skipping. UTC: {now_utc.isoformat()}", 200

    target_index = "all"
    try:
        body = request.get_json(silent=True) or {}
        target_index = body.get("index", "all").lower()
    except:
        pass

    if target_index in INDICES:
        indices_to_sync = {target_index: INDICES[target_index]}
    else:
        indices_to_sync = INDICES

    logger.info(f"--- Sync (target: {target_index}, UTC: {now_utc.isoformat()}) ---")

    total_records = 0
    results = {}
    synced_indices = []

    for index_key, config in indices_to_sync.items():
        count, status = sync_index(bq_client, storage_client, index_key, config, today)
        total_records += count
        results[index_key] = status
        if count > 0:
            synced_indices.append(index_key)

    # Notify backend
    if synced_indices:
        api_url = getenv("BACKEND_API_URL")
        if api_url:
            for idx in synced_indices:
                try:
                    requests.post(f"{api_url}/api/admin/refresh/{idx}", timeout=(3.05, 5))
                    logger.info(f"  Webhook → {idx}")
                except requests.exceptions.ReadTimeout:
                    pass
                except Exception as e:
                    logger.warning(f"  Webhook error {idx}: {e}")

    summary = f"Done (target: {target_index}). {total_records} records. {json.dumps(results)}"
    logger.info(f"\n--- {summary} ---")
    return summary, 200